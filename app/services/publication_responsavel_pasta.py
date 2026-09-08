"""Responsável NOMINAL da pasta (processo) no Legal One, para a tela de triagem.

Por que existe um módulo só pra isso: o responsável da pasta não é coluna de
`publicacao_registros`. Ele vive no `lawsuit_cache`, dentro do JSON `payload`,
na chave `responsibleUser` — gravada por `prefetch_lawsuit_responsibles_cache`
durante a montagem das propostas de tarefa. Ler daqui é de graça (uma query
local); ler do L1 custa quota da Firm Premium.

Duas escolhas que valem explicação:

1. **Ignoramos o TTL do cache.** `LawsuitCache.is_fresh()` corta em 24h porque
   quem consome é o motor de agendamento, que não pode errar o escritório. Aqui
   o consumo é de LEITURA na tela: responsável de pasta muda raramente (troca de
   equipe), e mostrar o dono de ontem é muito melhor que mostrar um traço. Medido
   em produção (03/09/2026): honrando o TTL a tela mostraria o responsável em 672
   das 1.193 publicações pendentes com pasta (56%); ignorando, em 951 (80%).
   As entradas com mais de 7 dias (12 de 951) voltam marcadas como
   `desatualizado` pra tela poder ressalvar sem esconder.

2. **Não é validação, é contexto.** Só 16% das tarefas agendadas nos últimos 30
   dias foram para o dono da pasta — o template roteia por equipe/especialidade,
   então divergir é o NORMAL, não a exceção. Qualquer alerta de "responsável
   diferente do da pasta" dispararia em 84% dos casos e viraria ruído. O campo
   informa de quem é a pasta; a decisão de para quem vai a tarefa continua do
   template e do operador.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Além disso a tela ressalva a informação (tooltip), sem escondê-la.
_DIAS_ATE_DESATUALIZAR = 7


def _extrair(payload) -> dict | None:
    """Tira o `responsibleUser` do payload do cache, tolerando JSON em texto.

    A coluna é `JSON` (não JSONB): dependendo do driver e de quem gravou, o
    SQLAlchemy devolve dict ou a string crua.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            return None
    if not isinstance(payload, dict):
        return None
    # Mesma ordem de chaves aceita pelo cliente do L1
    # (`_cached_responsible_from_payload`), pra não divergir do agendamento.
    for chave in ("responsibleUser", "responsible_user", "responsible"):
        resp = payload.get(chave)
        if isinstance(resp, dict) and resp.get("id"):
            return resp
    return None


def responsaveis_por_lawsuit(db: Session, lawsuit_ids: list[int]) -> dict[int, dict]:
    """Responsável de cada pasta, em UMA query. Ausente = sem informação.

    Devolve `{lawsuit_id: {id, nome, email, atualizado_em, desatualizado}}`.
    Pasta fora do dicionário significa "ainda não consultado" — que é diferente
    de "sem responsável", e a tela precisa dizer isso ao operador.
    """
    ids = sorted({int(x) for x in lawsuit_ids if x})
    if not ids:
        return {}

    try:
        rows = db.execute(
            text(
                "SELECT lawsuit_id, payload, fetched_at "
                "FROM lawsuit_cache WHERE lawsuit_id = ANY(:ids)"
            ),
            {"ids": ids},
        ).fetchall()
    except Exception:  # noqa: BLE001
        logger.exception("Falha lendo responsável de pasta no lawsuit_cache (ignorado).")
        return {}

    limite = datetime.now(timezone.utc) - timedelta(days=_DIAS_ATE_DESATUALIZAR)
    out: dict[int, dict] = {}
    for lawsuit_id, payload, fetched_at in rows:
        resp = _extrair(payload)
        if not resp:
            continue
        quando = fetched_at
        if quando is not None and quando.tzinfo is None:
            quando = quando.replace(tzinfo=timezone.utc)
        out[int(lawsuit_id)] = {
            "id": resp.get("id"),
            "nome": resp.get("name") or resp.get("nome"),
            "email": resp.get("email"),
            "atualizado_em": quando.isoformat() if quando else None,
            "desatualizado": bool(quando and quando < limite),
        }
    return out


def responsaveis_distintos(db: Session, limite: int = 60) -> list[dict]:
    """Vocabulário do filtro: quem é dono de pasta com publicação PENDENTE.

    Diferente do catálogo de etiquetas (global de propósito), aqui o escopo é a
    fila: listar os ~1.500 advogados do tenant num dropdown de triagem não
    ajudaria ninguém. Ordena por volume — quem tem mais publicação parada na
    fila aparece primeiro, que é a ordem em que o supervisor procura.
    """
    try:
        rows = db.execute(
            text(
                """
                SELECT CAST(CAST(lc.payload AS jsonb)->'responsibleUser'->>'id' AS integer) AS id,
                       max(CAST(lc.payload AS jsonb)->'responsibleUser'->>'name')          AS nome,
                       count(DISTINCT pr.id)                                               AS total
                  FROM publicacao_registros pr
                  JOIN lawsuit_cache lc ON lc.lawsuit_id = pr.linked_lawsuit_id
                 WHERE pr.is_duplicate = false
                   AND pr.status IN ('NOVO', 'CLASSIFICADO', 'ERRO')
                   AND CAST(lc.payload AS jsonb)->'responsibleUser'->>'id' IS NOT NULL
                 GROUP BY 1
                 ORDER BY total DESC, nome ASC
                 LIMIT :limite
                """
            ),
            {"limite": int(limite)},
        ).fetchall()
    except Exception:  # noqa: BLE001
        logger.exception("Falha listando responsáveis de pasta pro filtro (ignorado).")
        return []

    return [
        {"id": r[0], "nome": r[1] or f"Contato {r[0]}", "total": int(r[2])}
        for r in rows
        if r[0]
    ]
