"""
Autorun recorrente do Tratamento Web de publicacoes.

O painel "Tratamento Web" sempre dependeu do operador clicar "Iniciar
execucao". Este worker torna o modulo automatico: um cron do APScheduler
(default 01h/04h/12h/22h America/Sao_Paulo, configuravel via
PUBLICATION_TREATMENT_AUTORUN_CRON) dispara o mesmo start_run do botao —
backfill dos registros elegiveis + runner Playwright em subprocesso —
sem ninguem na tela. O run e server-backed: o subprocess Node sobrevive
ao tick e o progresso continua visivel no painel e nos logs do container.

Protecoes:
- advisory lock no Postgres: com UVICORN_WORKERS>1 cada worker tem seu
  proprio APScheduler in-memory disparando o mesmo cron; so um ganha o
  lock por tick (mesma causa raiz corrigida em
  scheduled_automation_service._execute_automation).
- recover_stale_runs antes de iniciar: um run zumbi (container matou o
  runner no meio de um deploy/OOM) nunca bloqueia os ciclos seguintes
  com "already_running".
- se ja ha execucao ativa legitima ou a fila esta vazia, o tick termina
  sem criar run (start_run ja responde already_running/no_pending_items).
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings

logger = logging.getLogger(__name__)

_LOCK_NAMESPACE = 4243
_LOCK_KEY = 1
_JOB_ID = "publication_treatment_autorun"


def _autorun_tick() -> None:
    from sqlalchemy import text as _sql_text

    from app.db.session import SessionLocal, engine as _engine
    from app.models.publication_treatment import RUN_TRIGGER_AUTOMATION
    from app.services.publication_treatment_service import PublicationTreatmentService

    lock_conn = _engine.connect()
    try:
        got_lock = lock_conn.execute(
            _sql_text("SELECT pg_try_advisory_lock(:k1, :k2)"),
            {"k1": _LOCK_NAMESPACE, "k2": _LOCK_KEY},
        ).scalar()
        if not got_lock:
            logger.info(
                "Tratamento Web autorun: outro worker/container ja esta cuidando deste tick."
            )
            return

        db = SessionLocal()
        try:
            service = PublicationTreatmentService(db)
            recovered = service.recover_stale_runs(reason="autorun")
            if recovered:
                logger.warning(
                    "Tratamento Web autorun: %d run(s) zumbi(s) recuperado(s) antes do ciclo.",
                    recovered,
                )

            result = service.start_run(
                trigger_type=RUN_TRIGGER_AUTOMATION,
                triggered_by_email="autorun@scheduler",
            )
            if result.get("started"):
                run = result.get("run") or {}
                logger.info(
                    "Tratamento Web autorun: run #%s iniciado com %s item(ns) na fila.",
                    run.get("id"),
                    run.get("total_items"),
                )
            elif result.get("reason") == "already_running":
                run = result.get("run") or {}
                logger.info(
                    "Tratamento Web autorun: run #%s ja em andamento — este ciclo nao inicia outro.",
                    run.get("id"),
                )
            else:
                logger.info(
                    "Tratamento Web autorun: fila zerada — nenhum item pendente/falha para tratar."
                )
        finally:
            db.close()
    except Exception:
        logger.exception("Tratamento Web autorun: falha no tick.")
    finally:
        try:
            lock_conn.execute(
                _sql_text("SELECT pg_advisory_unlock(:k1, :k2)"),
                {"k1": _LOCK_NAMESPACE, "k2": _LOCK_KEY},
            )
        except Exception:
            logger.exception("Tratamento Web autorun: falha ao liberar advisory lock.")
        lock_conn.close()


def register_publication_treatment_autorun_job(scheduler: BaseScheduler) -> None:
    """
    Registra o cron do autorun no APScheduler singleton. Idempotente
    (replace_existing) e com coalesce/misfire pra nao disparar N execucoes
    represadas depois de um deploy demorado.
    """
    if not settings.publication_treatment_autorun_enabled:
        logger.info(
            "Tratamento Web autorun desabilitado (PUBLICATION_TREATMENT_AUTORUN_ENABLED=false)."
        )
        return

    cron = settings.publication_treatment_autorun_cron
    try:
        from zoneinfo import ZoneInfo

        br_tz = ZoneInfo("America/Sao_Paulo")
    except Exception:
        br_tz = None

    trigger = (
        CronTrigger.from_crontab(cron, timezone=br_tz)
        if br_tz
        else CronTrigger.from_crontab(cron)
    )
    scheduler.add_job(
        _autorun_tick,
        trigger=trigger,
        id=_JOB_ID,
        name="Tratamento Web de publicacoes (autorun)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    logger.info(
        "Tratamento Web autorun registrado no APScheduler (cron=%s, tz=America/Sao_Paulo).",
        cron,
    )
