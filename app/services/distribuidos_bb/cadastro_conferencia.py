"""Conferência pós-import: o L1 criou UMA pasta por processo, ou mais?

Motivação (24/08/2026): a planilha 119 tinha 51 processos, o XLSX estava limpo
(51 linhas distintas), e o L1 ficou com **102 pastas** — cada CNJ virou duas
pastas gêmeas, criadas no mesmo segundo. O import reportou sucesso, ninguém foi
avisado, e o estrago só apareceu dias depois pela agenda duplicada (duas pastas
geram duas tarefas). O operador descobriu no olho.

O import do L1 é assíncrono e a resposta dele diz o que foi ENVIADO, não o que
foi criado. Então a única forma de saber é perguntar depois — que é o que este
módulo faz.

Como distinguir duplicata NOSSA de pasta legítima que já existia:

  - só conta pasta criada DEPOIS que a planilha nasceu (`desde`). Pasta de
    meses atrás é pré-existente, não foi este import;
  - agrupa por (CNJ, escritório responsável). O fluxo cadastra de propósito o
    MESMO CNJ em escritórios diferentes quando o processo é de outro cliente
    (`cnjs_liberados`) — isso é correto e não pode virar alarme falso.

Duas pastas do mesmo CNJ, no mesmo escritório, criadas nesta janela: aí sim é
duplicação, e o operador precisa saber na hora.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.distribuidos_bb import NIVEL_ERRO, NIVEL_INFO, SECAO_CADASTRO
from app.services.distribuidos_bb.log_service import registrar_evento

logger = logging.getLogger("distribuidos_bb.conferencia")

ACAO_OK = "Conferência pós-import"
ACAO_DUP = "Pasta duplicada no L1"

# O L1 rejeita $top acima de 30 em /Lawsuits, e cada CNJ vira 1 cláusula OR.
_CHUNK = 8
# Folga pra trás: o relógio do L1 e o nosso não são o mesmo, e a pasta pode ser
# carimbada alguns segundos antes do commit que a criou.
_FOLGA_MIN = 10


def _digitos(v) -> str:
    return "".join(c for c in str(v or "") if c.isdigit())


# O L1 devolve 7 casas de fração de segundo ("...09:37:11.6718563-03:00"), e o
# `fromisoformat` do Python 3.10 (o do container) só aceita 3 ou 6 — ele levanta
# ValueError. O ambiente de desenvolvimento roda 3.13, que aceita, então isso
# passaria nos testes e falharia SÓ em produção. Corta a fração pra 6 casas.
_FRACAO = re.compile(r"(\.\d{6})\d+")


def _parse_data(bruto) -> Optional[datetime]:
    """Data do L1 → datetime aware. None quando não dá pra entender."""
    if not bruto:
        return None
    texto = _FRACAO.sub(r"", str(bruto).replace("Z", "+00:00"))
    try:
        dt = datetime.fromisoformat(texto)
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def conferir_duplicacao(
    db: Session,
    planilha,
    *,
    client: Any = None,
    desde: Optional[datetime] = None,
) -> dict:
    """Pergunta ao L1 quantas pastas existem por (CNJ, escritório) desta planilha.

    Best-effort por decisão: a conferência NÃO pode derrubar um cadastro que já
    deu certo. Falha aqui vira log, não exceção.

    Devolve {conferidos, duplicados, pastas_extras, detalhe}.
    """
    resumo = {"conferidos": 0, "duplicados": 0, "pastas_extras": 0, "detalhe": []}
    try:
        cnjs = [
            r[0] for r in db.execute(
                text(
                    "SELECT DISTINCT cnj FROM bbd_processos "
                    # `trim` e nao `btrim`: btrim so' existe no Postgres e a suite roda em
                    # SQLite — o teste pegou isso na primeira execucao.
                    "WHERE planilha_id = :p AND cnj IS NOT NULL AND trim(cnj) <> ''"
                ),
                {"p": planilha.id},
            )
        ]
        if not cnjs:
            return resumo

        if client is None:
            from app.services.legal_one_client import LegalOneApiClient

            client = LegalOneApiClient()

        corte = (desde or planilha.created_at or datetime.now(timezone.utc))
        if corte.tzinfo is None:
            corte = corte.replace(tzinfo=timezone.utc)
        corte = corte - timedelta(minutes=_FOLGA_MIN)

        # UMA consulta por entidade, por faixa de data — e nao uma busca por
        # CNJ. Com 500 processos o caminho antigo fazia 62 chamadas, batia no
        # 429 do L1 e ABORTAVA no meio (parou em 328 de 500 no tombamento de
        # 26/08/2026), devolvendo um "sem duplicacao" que nao valia nada.
        corte_iso = corte.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        alvo = {_digitos(x) for x in cnjs}
        resumo["conferidos"] = len(alvo)

        # As DUAS entidades: pasta do L1 nasce em /Lawsuits OU /Litigations.
        # MAS elas devolvem o MESMO registro — sem deduplicar por `id`, toda
        # pasta aparece duas vezes e a conferencia acusa duplicata falsa em
        # 100% dos casos. Foi o alarme falso que eu dei no lote de teste.
        pastas: dict = {}
        for endpoint in ("/Lawsuits", "/Litigations"):
            try:
                for it in client._paginated_catalog_loader(endpoint, {
                    "$filter": f"creationDate ge {corte_iso}",
                    "$select": "id,identifierNumber,folder,creationDate,"
                               "responsibleOfficeId",
                    "$top": 30,
                }):
                    if it.get("id") is not None:
                        pastas.setdefault(it["id"], it)
            except Exception as exc:  # noqa: BLE001
                resumo["parcial"] = True
                logger.warning(
                    "Conferencia: %s falhou (%s) — resultado PARCIAL.",
                    endpoint, str(exc)[:120],
                )

        por_chave: dict = {}
        for it in pastas.values():
            dt = _parse_data(it.get("creationDate"))
            if dt is not None and dt < corte:
                continue
            d = _digitos(it.get("identifierNumber"))
            if d not in alvo:
                continue  # pasta de outro fluxo criada na mesma janela
            por_chave.setdefault((d, it.get("responsibleOfficeId")), []).append(it)

        for (cnj, office), lista in por_chave.items():
            if len(lista) < 2:
                continue
            lista.sort(key=lambda x: (x.get("folder") or ""))
            resumo["duplicados"] += 1
            resumo["pastas_extras"] += len(lista) - 1
            resumo["detalhe"].append({
                "cnj": cnj,
                "escritorio": office,
                "manter": lista[0].get("folder"),
                "extras": [
                    {"folder": p.get("folder"), "lawsuit_id": p.get("id")}
                    for p in lista[1:]
                ],
            })
        resumo["com_pasta"] = len(por_chave)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Conferência pós-import da planilha %s falhou (%s) — cadastro segue válido.",
            getattr(planilha, "id", "?"), exc,
        )
        return resumo

    _registrar(db, planilha, resumo)
    return resumo


def _registrar(db: Session, planilha, resumo: dict) -> None:
    """Grava o resultado no log do módulo (é o que o operador lê na tela)."""
    if resumo["duplicados"]:
        amostra = ", ".join(
            f"{d['cnj']} → {d['manter']} + " + "/".join(e["folder"] or "?" for e in d["extras"])
            for d in resumo["detalhe"][:5]
        )
        registrar_evento(
            db, secao=SECAO_CADASTRO, nivel=NIVEL_ERRO, acao=ACAO_DUP,
            mensagem=(
                f"O Legal One ficou com pasta DUPLICADA em {resumo['duplicados']} "
                f"processo(s) desta planilha ({resumo['pastas_extras']} pasta[s] a "
                f"mais que o devido). O import diz o que foi enviado, não o que foi "
                f"criado — por isso a conferência. Remover as pastas extras no L1. "
                f"Exemplos: {amostra}."
            ),
            dados={"planilha_id": planilha.id, **resumo},
        )
        logger.error(
            "Planilha %s: %s processo(s) com pasta duplicada no L1 (%s extras).",
            planilha.id, resumo["duplicados"], resumo["pastas_extras"],
        )
    else:
        registrar_evento(
            db, secao=SECAO_CADASTRO, nivel=NIVEL_INFO, acao=ACAO_OK,
            mensagem=(
                f"Conferência pós-import: {resumo['conferidos']} processo(s) "
                f"checado(s) no Legal One, uma pasta cada. Nenhuma duplicação."
            ),
            dados={"planilha_id": planilha.id, **resumo},
        )
    db.commit()
