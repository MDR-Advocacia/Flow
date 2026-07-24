"""Crons do bracket de reagendamento — 07h (manhã) e 19h (noite) BRT.

Cada tick: refresca perf_l1_tarefa gerando um relatório fresco no L1
(gerar_e_ingerir — a MESMA máquina de geração já usada 4h/13h, reaproveitada
conforme decisão do operador) e então captura/detecta:
  - 07h  → capturar_manha  (baseline do dia)
  - 19h  → detectar_noite  (adiamentos do dia)

Advisory lock compartilhado com o ingest do Minha Equipe (não faz sentido gerar
dois relatórios em paralelo). Best-effort: falha não derruba o scheduler.
"""

import datetime as _dt
import logging

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

logger = logging.getLogger(__name__)

_JOB_MANHA = "perf_reagendamento_manha"
_JOB_NOITE = "perf_reagendamento_noite"
_JOB_CATCHUP = "perf_reagendamento_catchup"

try:
    from zoneinfo import ZoneInfo

    _BRT = ZoneInfo("America/Sao_Paulo")
except Exception:  # pragma: no cover
    _BRT = None


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


def _tem_baseline_hoje(db) -> bool:
    """Já existe a foto da manhã de HOJE em perf_prazo_manha?"""
    from sqlalchemy import text

    hoje = (_dt.datetime.now(_BRT) if _BRT else _dt.datetime.now()).date()
    r = db.execute(
        text(
            "SELECT count(*) FROM perf_prazo_manha "
            "WHERE (dia AT TIME ZONE 'America/Sao_Paulo')::date = :d"
        ),
        {"d": hoje},
    ).scalar()
    return bool(r)


def _catchup_manha() -> None:
    """CATCH-UP: se o container subiu DEPOIS das 07h e a foto da manhã de hoje
    não foi tirada (deploy tardio, container fora do ar às 07h), captura agora —
    do snapshot atual, sem gerar relatório novo (mais rápido; melhor uma foto
    tardia que perder o dia inteiro, como aconteceu em 23/07). Só grava se ainda
    NÃO houver baseline (não sobrescreve o do cron nem o de um boot anterior)."""
    from app.db.session import SessionLocal
    from app.services.onerequest._concurrency import single_worker_lock
    from app.services.performance.ingest_worker import _LOCK_KEY
    from app.services.performance.reagendamento_service import capturar_manha

    with single_worker_lock(_LOCK_KEY) as got:
        if not got:
            return
        db = SessionLocal()
        try:
            if _tem_baseline_hoje(db):
                return  # já capturada — não mexe
            capturar_manha(db)
            logger.info("Reagendamento: CATCH-UP da manhã executado (baseline capturado tardiamente).")
        except Exception:  # noqa: BLE001
            logger.exception("Reagendamento: catch-up da manhã falhou.")
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
    # CATCH-UP no boot: se subiu na janela do dia (07h–19h) e a manhã de hoje
    # ainda não foi fotografada, agenda uma captura ~60s após o boot. Evita
    # perder o dia inteiro quando o deploy cai depois das 07h (caso 23/07). O
    # próprio job confere se já há baseline antes de gravar (idempotente entre
    # múltiplos redeploys no mesmo dia).
    try:
        agora = _dt.datetime.now(_BRT) if _BRT else _dt.datetime.now()
        if 7 <= agora.hour < 19:
            scheduler.add_job(
                _catchup_manha,
                trigger=DateTrigger(run_date=agora + _dt.timedelta(seconds=60)),
                id=_JOB_CATCHUP, replace_existing=True, max_instances=1, coalesce=True,
            )
            logger.info("Reagendamento: catch-up da manhã agendado (boot na janela do dia).")
    except Exception:  # noqa: BLE001
        logger.exception("Reagendamento: falha ao agendar o catch-up da manhã.")
    logger.info("Reagendamento: jobs registrados — bracket 07h (manhã) e 19h (noite) BRT.")
