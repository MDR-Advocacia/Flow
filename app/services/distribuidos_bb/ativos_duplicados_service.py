"""Duplicados da ingestão Ativos: listagem rastreável + resolução da pasta no L1.

Um CNJ da planilha Ativos que já existe no Legal One não é recadastrado (vira
uma linha em `bbd_ativos_duplicados`). Aqui o operador:
  - lista os duplicados (por lote, motivo, com/sem pasta resolvida, busca por CNJ);
  - resolve sob demanda o id/folder da pasta no L1 (pra dar o link e, depois,
    permitir o agendamento de tarefa em lote sobre essas pastas).

A resolução da pasta é preguiçosa (não bloqueia a ingestão) e cacheada na linha.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.distribuidos_bb import (
    DUP_MOTIVO_LABEL,
    BbAtivosDuplicado,
)

logger = logging.getLogger("distribuidos_bb.ativos.duplicados")

L1_DETAILS_URL = "https://mdradvocacia.novajus.com.br/processos/Processos/details"


def _dto(d: BbAtivosDuplicado) -> dict:
    return {
        "id": d.id,
        "lote_id": d.lote_id,
        "cnj": d.cnj,
        "cnj_digitos": d.cnj_digitos,
        "motivo": d.motivo,
        "motivo_label": DUP_MOTIVO_LABEL.get(d.motivo, d.motivo),
        "parte": d.parte,
        "l1_lawsuit_id": d.l1_lawsuit_id,
        "l1_folder": d.l1_folder,
        "l1_url": f"{L1_DETAILS_URL}/{d.l1_lawsuit_id}" if d.l1_lawsuit_id else None,
        "criado_em": d.criado_em.isoformat() if d.criado_em else None,
    }


def listar(
    db: Session,
    *,
    lote_id: Optional[int] = None,
    motivo: Optional[str] = None,
    com_pasta: Optional[bool] = None,
    busca: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Lista paginada dos duplicados, do mais recente pro mais antigo."""
    q = db.query(BbAtivosDuplicado)
    if lote_id is not None:
        q = q.filter(BbAtivosDuplicado.lote_id == lote_id)
    if motivo:
        q = q.filter(BbAtivosDuplicado.motivo == motivo)
    if com_pasta is True:
        q = q.filter(BbAtivosDuplicado.l1_lawsuit_id.isnot(None))
    elif com_pasta is False:
        q = q.filter(BbAtivosDuplicado.l1_lawsuit_id.is_(None))
    if busca:
        alvo = "".join(ch for ch in busca if ch.isdigit()) or busca
        like = f"%{alvo}%"
        q = q.filter(or_(
            BbAtivosDuplicado.cnj_digitos.ilike(like),
            BbAtivosDuplicado.cnj.ilike(f"%{busca}%"),
            BbAtivosDuplicado.parte.ilike(f"%{busca}%"),
        ))

    total = q.count()
    rows = (
        q.order_by(BbAtivosDuplicado.id.desc())
        .limit(limit).offset(offset).all()
    )

    # KPIs do recorte inteiro (sem paginação), pros cards da aba.
    base = db.query(BbAtivosDuplicado)
    if lote_id is not None:
        base = base.filter(BbAtivosDuplicado.lote_id == lote_id)
    kpis = {
        "total": base.count(),
        "com_pasta": base.filter(BbAtivosDuplicado.l1_lawsuit_id.isnot(None)).count(),
        "ja_cadastrado": base.filter(BbAtivosDuplicado.motivo == "JA_CADASTRADO").count(),
        "repetido_lote": base.filter(BbAtivosDuplicado.motivo == "REPETIDO_LOTE").count(),
    }
    return {"total": total, "items": [_dto(x) for x in rows], "kpis": kpis}


def resolver_pastas_l1(
    db: Session,
    *,
    ids: Optional[list[int]] = None,
    lote_id: Optional[int] = None,
    limite: int = 200,
) -> dict:
    """Resolve id/folder da pasta no L1 pros duplicados que ainda não têm.

    Casa por CNJ (o CNJ é justamente o motivo do duplicado — a pasta existe).
    Em lote via `search_lawsuits_by_cnj_numbers`. Idempotente: só toca quem está
    sem `l1_lawsuit_id`. Retorna quantos foram resolvidos e quantos não acharam.
    """
    from app.services.legal_one_client import LegalOneApiClient

    q = db.query(BbAtivosDuplicado).filter(BbAtivosDuplicado.l1_lawsuit_id.is_(None))
    if ids:
        q = q.filter(BbAtivosDuplicado.id.in_(ids))
    if lote_id is not None:
        q = q.filter(BbAtivosDuplicado.lote_id == lote_id)
    pendentes = q.limit(limite).all()
    if not pendentes:
        return {"resolvidos": 0, "nao_encontrados": 0, "pendentes": 0}

    por_cnj: dict[str, list[BbAtivosDuplicado]] = {}
    for d in pendentes:
        por_cnj.setdefault(d.cnj, []).append(d)

    client = LegalOneApiClient()
    matches = client.search_lawsuits_by_cnj_numbers(list(por_cnj.keys()))
    # matches é chaveado pelo CNJ normalizado do client; casa por dígitos.
    match_por_digs = {
        "".join(ch for ch in k if ch.isdigit()): v for k, v in matches.items()
    }

    agora = datetime.now(timezone.utc)
    resolvidos = nao_encontrados = 0
    for d in pendentes:
        m = match_por_digs.get(d.cnj_digitos)
        if m and m.get("id"):
            d.l1_lawsuit_id = int(m["id"])
            d.l1_folder = m.get("folder")
            d.l1_resolvido_em = agora
            resolvidos += 1
        else:
            nao_encontrados += 1
    db.commit()

    restantes = (
        db.query(BbAtivosDuplicado)
        .filter(BbAtivosDuplicado.l1_lawsuit_id.is_(None))
        .count()
    )
    logger.info(
        "Ativos duplicados: resolvidos=%s nao_encontrados=%s (restam %s sem pasta).",
        resolvidos, nao_encontrados, restantes,
    )
    return {"resolvidos": resolvidos, "nao_encontrados": nao_encontrados, "pendentes": restantes}
