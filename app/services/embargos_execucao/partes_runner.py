"""Processo filho da coleta de partes no portal do BB.

    python -m app.services.embargos_execucao.partes_runner --limite 80
    python -m app.services.embargos_execucao.partes_runner --execucao-id 12

Roda fora do uvicorn pelo mesmo motivo da coleta do Distribuídos BB: Chromium
travado dentro de thread não se mata. O worker dispara este processo em sessão
própria, espera com teto e mata o grupo se estourar (worker.py).
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

from app.models.embargos_execucao import EVT_AVISO, SECAO_PARTES, EmbExecucao
from app.services.embargos_execucao import partes_bb, service

logger = logging.getLogger("embargos_execucao.partes_runner")


def executar(db, limite: int, coletar=partes_bb.coletar, execucao_id: Optional[int] = None) -> int:
    fila = service.fila_partes(db, limite, execucao_id=execucao_id)
    if not fila:
        return 0
    por_npj: dict[str, list[EmbExecucao]] = {}
    for exe in fila:
        por_npj.setdefault(exe.npj, []).append(exe)
    try:
        resultados = coletar(list(por_npj))
    except Exception as exc:  # noqa: BLE001
        # A sessão do portal não abriu (OneLog/Xvfb): não é culpa do NPJ, não
        # gasta tentativa — só registra e deixa pra próxima passagem.
        for exes in por_npj.values():
            for exe in exes:
                exe.partes_erro = f"Portal do BB indisponível: {exc}"[:500]
                exe.partes_em = service.agora()
        service.registrar_evento(
            db, SECAO_PARTES,
            f"Portal do BB indisponível — {len(fila)} coleta(s) adiada(s): {exc}",
            nivel=EVT_AVISO,
        )
        db.commit()
        return 2
    for npj, resultado in resultados.items():
        for exe in por_npj.get(npj, []):
            service.aplicar_partes(db, exe, resultado)
        db.commit()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=80)
    ap.add_argument("--execucao-id", type=int, default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    import app.models  # noqa: F401 — registra os mappers
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        return executar(db, args.limite, execucao_id=args.execucao_id)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("Embargos: coleta de partes caiu.")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
