"""Preenche o RITO das publicacoes que ja existiam antes do pub016.

Duas passadas, e a ordem importa porque a primeira e' de graca:

  1. TEXTO — regex sobre a publicacao. Sem rede, sem cache, milhares por
     segundo. Resolve ~63% (medicao de 03/09/2026).
  2. DATAJUD — so' para o que sobrou, e so' para a fila PENDENTE, que e' a
     que o operador olha. Uma requisicao por CNJ DISTINTO, cacheada para
     sempre em `processo_rito` (rito de processo nao muda).

Uso:
    python scripts/backfill_rito.py                 # so' a passada de texto
    python scripts/backfill_rito.py --datajud       # texto + DataJud nos pendentes
    python scripts/backfill_rito.py --datajud --limite 500
"""
import argparse
import logging
import sys
import time

sys.path.insert(0, "/app")

from app.db.session import SessionLocal                       # noqa: E402
from app.models.publication_search import PublicationRecord    # noqa: E402
from app.services.publication_rito import (                    # noqa: E402
    FONTE_TEXTO, consultar_datajud_e_cachear, rito_em_cache, rito_pelo_texto,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("backfill_rito")

PENDENTES = ("NOVO", "CLASSIFICADO")


def passada_texto(db, lote: int = 2000) -> int:
    """Todas as publicacoes sem rito, em lotes, direto do texto."""
    total = 0
    while True:
        registros = (
            db.query(PublicationRecord)
            # `rito_fonte` e' o marcador de JA VISITEI, nao o `rito`: quem o
            # texto nao resolve fica com rito NULL, e filtrar por rito faria
            # o mesmo lote voltar para sempre.
            .filter(PublicationRecord.rito_fonte.is_(None))
            .filter(PublicationRecord.description.isnot(None))
            .limit(lote)
            .all()
        )
        if not registros:
            break
        achou = 0
        for rec in registros:
            rito, fonte = rito_pelo_texto(rec.description)
            # Marca TODAS as visitadas, senao o proximo lote traz as mesmas.
            # "nao_resolvido" e' registro de que ja tentamos pelo texto.
            rec.rito = rito
            rec.rito_fonte = fonte or "nao_resolvido"
            if rito:
                achou += 1
        db.commit()
        total += len(registros)
        log.info("texto: %s visitadas (+%s resolvidas) — total %s", len(registros), achou, total)
        if len(registros) < lote:
            break
    return total


def passada_datajud(db, limite: int, pausa: float = 0.4) -> int:
    """So' os PENDENTES sem rito e com CNJ. Uma consulta por CNJ distinto."""
    registros = (
        db.query(PublicationRecord)
        .filter(PublicationRecord.rito.is_(None))
        .filter(PublicationRecord.status.in_(PENDENTES))
        .filter(PublicationRecord.linked_lawsuit_cnj.isnot(None))
        .filter(PublicationRecord.is_duplicate == False)  # noqa: E712
        .limit(limite)
        .all()
    )
    log.info("datajud: %s publicacoes pendentes sem rito", len(registros))
    resolvidas = 0
    for i, rec in enumerate(registros, 1):
        cnj = rec.linked_lawsuit_cnj
        info = rito_em_cache(db, cnj)
        if info is None:
            info = consultar_datajud_e_cachear(db, cnj)
            time.sleep(pausa)   # o DataJud e' publico; nao vale afogar
        if info.get("rito"):
            rec.rito = info["rito"]
            rec.rito_fonte = info.get("fonte") or "datajud"
            resolvidas += 1
        if i % 25 == 0:
            db.commit()
            log.info("datajud: %s/%s (%s resolvidas)", i, len(registros), resolvidas)
    db.commit()
    return resolvidas


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--datajud", action="store_true")
    ap.add_argument("--limite", type=int, default=2000)
    args = ap.parse_args()

    db = SessionLocal()
    try:
        n = passada_texto(db)
        log.info("PASSADA DE TEXTO: %s publicacoes visitadas.", n)
        if args.datajud:
            r = passada_datajud(db, args.limite)
            log.info("PASSADA DATAJUD: %s resolvidas.", r)
    finally:
        db.close()
