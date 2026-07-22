"""Crons do bracket de reagendamento — 07h (manhã) e 19h (noite) BRT.

Cada tick: refresca perf_l1_tarefa gerando um relatório fresco no L1
(gerar_e_ingerir — a MESMA máquina de geração já usada 4h/13h, reaproveitada
conforme decisão do operador) e então captura/detecta:
  - 07h  → capturar_manha  (baseline do dia)
  - 19h  → detectar_noite  (adiamentos do dia)

Advisory lock compartilhado com o ingest do Minha Equipe (não faz sentido gerar
dois relatórios em paralelo). Best-effort: falha não derruba o scheduler.
"""

import logging

from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_JOB_MANHA = "perf_reagendamento_manha"
_JOB_NOITE = "perf_reagendamento_noite"


def _tick(momento: str) -> None:
    from app.db.session import SessionLocal
    from app.services.onerequest._concurrency import single_worker_lock
    from app.services.performance.ingest_worker import _LOCK_KEY
    from app.services.performance.reagendamento_service import capturar_manha, detectar_noite
    from app.services.performance.report_ingest import gerar_e_ingerir

    with single_worker_lock(_LOCK_KEY) as got:
        if not got:
            logger.info("Reagendamento (%s): outro worker gerando relatório — pulando.", momento)
            return
        db = SessionLocal()
        try:
            # Refresca o snapshot com um relatório fresco do L1 (a foto do momento).
            res = gerar_e_ingerir(db)
            if not res.get("ok"):
                logger.warning("Reagendamento (%s): geração do relatório falhou — %s.",
                               momento, res.get("motivo"))
                return
            if momento == "manha":
                capturar_manha(db)
            else:
                detectar_noite(db)
        except Exception:  # noqa: BLE001
            logger.exception("Reagendamento (%s): erro inesperado no tick.", momento)
        finally:
            db.close()


def register_reagendamento_jobs(scheduler) -> None:
    scheduler.add_job(
        _tick, args=["manha"],
        trigger=CronTrigger(hour="7", minute="0", timezone="America/Sao_Paulo"),
        id=_JOB_MANHA, replace_existing=True, max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        _tick, args=["noite"],
        trigger=CronTrigger(hour="19", minute="0", timezone="America/Sao_Paulo"),
        id=_JOB_NOITE, replace_existing=True, max_instances=1, coalesce=True,
    )
    logger.info("Reagendamento: jobs registrados — bracket 07h (manhã) e 19h (noite) BRT.")
