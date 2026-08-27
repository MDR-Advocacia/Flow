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


def _mascara(d: str) -> str:
    """Digitos → forma canônica NNNNNNN-DD.AAAA.J.TR.OOOO (o L1 guarda com máscara)."""
    if len(d) != 20:
        return d
    return f"{d[:7]}-{d[7:9]}.{d[9:13]}.{d[13]}.{d[14:16]}.{d[16:20]}"


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

        alvo = sorted({_digitos(x) for x in cnjs})
        resumo["conferidos"] = len(alvo)

        # Busca POR CNJ (identifierNumber), em chunks — e nao por faixa de
        # creationDate. A consulta por data parecia mais barata, mas o
        # /Lawsuits nao devolve @odata.nextLink nesse tipo de filtro e o
        # loader para nos primeiros 30 registros: na madrugada de 27/08 a
        # conferencia "aprovou" 8 lotes seguidos enquanto 298 pastas em dobro
        # nasciam fora da janela visivel. Cega, nao barata.
        #
        # O caminho por CNJ ja' tinha sido tentado e abortava no 429 — o erro
        # de la' era ABORTAR: aqui, chunk que falha marca `parcial` e os
        # OUTROS chunks continuam, entao o resultado nunca finge cobertura.
        #
        # As DUAS entidades (pasta nasce em /Lawsuits OU /Litigations), com
        # dedupe por `id` porque elas devolvem o MESMO registro.
        pastas: dict = {}
        for i in range(0, len(alvo), _CHUNK):
            chunk = alvo[i:i + _CHUNK]
            partes = []
            for d in chunk:
                partes.append(f"identifierNumber eq '{d}'")
                partes.append(f"identifierNumber eq '{_mascara(d)}'")
            filtro = " or ".join(partes)
            for endpoint in ("/Lawsuits", "/Litigations"):
                try:
                    for it in client._paginated_catalog_loader(endpoint, {
                        "$filter": filtro,
                        "$select": "id,identifierNumber,folder,creationDate,"
                                   "responsibleOfficeId",
                        "$top": 30,
                    }):
                        if it.get("id") is not None:
                            pastas.setdefault(it["id"], it)
                except Exception as exc:  # noqa: BLE001
                    resumo["parcial"] = True
                    logger.warning(
                        "Conferencia: chunk %s de %s falhou (%s) — resultado "
                        "PARCIAL.", i, endpoint, str(exc)[:120],
                    )

        # Agrupa por (CNJ, escritorio) SEM filtrar por data: duas pastas do
        # mesmo CNJ no mesmo escritorio sao duplicata seja la' quando a
        # primeira nasceu — filtrar por janela escondia exatamente o caso em
        # que a segunda pasta e' nossa e a primeira e' de minutos antes.
        por_chave: dict = {}
        for it in pastas.values():
            d = _digitos(it.get("identifierNumber"))
            if d not in set(alvo):
                continue  # variante casou com CNJ fora do lote
            por_chave.setdefault((d, it.get("responsibleOfficeId")), []).append(it)

        for (cnj, office), lista in por_chave.items():
            if len(lista) < 2:
                continue
            # A mais ANTIGA fica (e' a que os vinculos apontam); extras saem.
            lista.sort(key=lambda x: (
                _parse_data(x.get("creationDate")) or datetime.max.replace(tzinfo=timezone.utc),
                x.get("folder") or "",
            ))
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
