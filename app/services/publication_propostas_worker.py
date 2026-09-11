"""Repassa as propostas de tarefa que ficaram para trás.

POR QUE EXISTE (11/09/2026)
---------------------------
O operador achou as publicações de 09/09 "sem template": das 530 pendentes, só
38 tinham proposta de tarefa. Classificar e montar a proposta são dois passos
— `apply_batch_results` aplica a classificação e só depois chama
`_build_task_proposals` — e o segundo depende da busca do responsável da pasta
no L1 para os templates sem responsável nominal. Quando essa busca cai
INTEIRA, a regra de 08/09 é não gravar proposta manca: o registro fica como
estava. A regra está certa, mas não havia segunda volta — nada tentava de
novo, e 359 publicações com pasta ficaram dois dias na mesa sem proposta até
alguém reclamar.

O marcador é exato: todo registro por onde `_build_task_proposals` passa sai
com `raw_relationships` em dict — com proposta, ou sem (quando nenhum template
casa). CLASSIFICADO com a lista crua do L1 (ou nada) é classificação aplicada
e proposta nunca montada. Este job acha esses e roda o mesmo passo de novo,
calado (decisão do operador: falha que o sistema conserta sozinho não vira
aviso). Se a busca do responsável ainda estiver fora, o registro continua
intocado e volta no próximo ciclo; o Vigia de Filas avisa se passar de 3 h.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, text

logger = logging.getLogger(__name__)

JOB_ID = "publicacoes_propostas_para_tras"
INTERVALO_MIN = 30
# Não disputa com o apply_batch_results que acabou de classificar e ainda vai
# montar as propostas logo em seguida.
FOLGA_MIN = 15
# Por ciclo, os mais novos primeiro (é a mesa de hoje); o resto no próximo.
LOTE = 200


def ids_sem_proposta_montada(
    db,
    agora: Optional[datetime] = None,
    *,
    folga_min: int = FOLGA_MIN,
    desde: Optional[datetime] = None,
    limite: Optional[int] = LOTE,
) -> list[int]:
    """Ids de publicações CLASSIFICADAS cuja proposta nunca foi montada."""
    from app.models.publication_search import RECORD_STATUS_CLASSIFIED
    from app.models.publication_search import PublicationRecord as PR

    agora = agora or datetime.now(timezone.utc)
    filtros = [
        PR.status == RECORD_STATUS_CLASSIFIED,
        PR.category.isnot(None),
        PR.is_duplicate.isnot(True),
        func.coalesce(PR.updated_at, PR.created_at) < agora - timedelta(minutes=folga_min),
    ]
    if desde is not None:
        filtros.append(PR.created_at >= desde)

    if db.get_bind().dialect.name == "postgresql":
        q = (
            db.query(PR.id)
            .filter(*filtros)
            .filter(text(
                "(raw_relationships IS NULL "
                "OR jsonb_typeof(CAST(raw_relationships AS jsonb)) <> 'object')"
            ))
            .order_by(PR.id.desc())
        )
        if limite:
            q = q.limit(limite)
        return [r.id for r in q.all()]

    # SQLite (suíte de testes): sem jsonb_typeof — filtra no Python.
    linhas = db.query(PR.id, PR.raw_relationships).filter(*filtros).order_by(PR.id.desc()).all()
    ids = [r.id for r in linhas if not isinstance(r.raw_relationships, dict)]
    return ids[:limite] if limite else ids


def repassar_propostas(db, agora: Optional[datetime] = None, *, limite: int = LOTE) -> int:
    """Monta a proposta das que ficaram para trás. Devolve quantas passaram."""
    ids = ids_sem_proposta_montada(db, agora, limite=limite)
    if not ids:
        return 0

    from app.models.publication_search import PublicationRecord as PR
    from app.services.publication_search_service import PublicationSearchService

    registros = db.query(PR).filter(PR.id.in_(ids)).all()
    PublicationSearchService(db, None)._build_task_proposals(registros)
    passaram = sum(1 for r in registros if isinstance(r.raw_relationships, dict))
    logger.info(
        "Propostas para trás: %d repassada(s); %d continua(m) sem passar "
        "(busca do responsável da pasta fora?) e volta(m) no próximo ciclo.",
        passaram, len(registros) - passaram,
    )
    return passaram


def _tick() -> None:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        repassar_propostas(db)
    except Exception:  # noqa: BLE001
        logger.exception("Repassada de propostas estourou (ignorado; tenta no próximo ciclo).")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()


def register_propostas_para_tras_job(scheduler) -> None:
    """Job periódico — sem sobreposição. Sem nada para trás, o tick é 1 SELECT."""
    scheduler.add_job(
        _tick,
        trigger="interval",
        minutes=INTERVALO_MIN,
        id=JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Job de propostas de tarefa para trás registrado (%d min).", INTERVALO_MIN)
