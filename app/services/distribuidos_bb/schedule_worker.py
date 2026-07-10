"""Agendamento da coleta Distribuídos BB (APScheduler).

3x por dia (madrugada / meio-dia / noite, horário de Brasília) o robô entra no
portal do BB via OneLog, coleta as notificações do dia, distribui e — ao fim —
gera e arquiva a planilha de migração (histórico em `bbd_planilhas`), pronta pro
operador baixar e subir no Legal One.

Ciência protegida: o run agendado pede ciência (confirmar_ciencia=True), mas ela
só acontece de fato se a trava GLOBAL (settings.distribuidos_bb_confirmar_ciencia)
também estiver ligada. Com ela desligada, a passagem agendada roda em modo seguro
(fecha com NÃO) e mesmo assim gera a planilha.

Roda em thread do BackgroundScheduler, então abre a própria SessionLocal.
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger("distribuidos_bb.agendamento")

JOB_ID_PREFIX = "distribuidos_bb_coleta"
_TZ_BR = "America/Sao_Paulo"
# Lock entre workers do uvicorn (WORKERS=4): só 1 roda a passagem, senão a coleta
# — e a ciência — dispararia 4×. Chaves da casa: onerequest 001-003, ingest 004,
# recursal 005; distribuídos BB usa 006 (coleta) e 007 (monitor cadastro).
_LOCK_KEY = 826100006

# Janela de raspagem das passagens automáticas: 3 dias pra trás até hoje.
_DIAS_PARA_TRAS = 3


def _janela_datas() -> tuple[str, str]:
    hoje = datetime.now(ZoneInfo(_TZ_BR)).date()
    inicio = hoje - timedelta(days=_DIAS_PARA_TRAS)
    return inicio.strftime("%d/%m/%Y"), hoje.strftime("%d/%m/%Y")


def _horarios() -> list[int]:
    from app.core.config import settings

    horas: list[int] = []
    for parte in (settings.distribuidos_bb_agendamento_horarios or "").split(","):
        parte = parte.strip()
        if parte.isdigit() and 0 <= int(parte) <= 23:
            horas.append(int(parte))
    return horas or [3, 12, 20]


def _tick() -> None:
    """Uma passagem agendada: cria o run, coleta e distribui (pool)."""
    from app.db.session import SessionLocal
    from app.services.distribuidos_bb import coleta_service
    from app.services.distribuidos_bb.onelog_client import OneLogClient
    from app.services.onerequest._concurrency import single_worker_lock

    if not OneLogClient().configurado:
        logger.warning(
            "Distribuídos BB agendado: credenciais do OneLog ausentes — passagem pulada."
        )
        return

    # Só UM worker do uvicorn roda a passagem (senão coleta+ciência em 4×).
    with single_worker_lock(_LOCK_KEY) as got:
        if not got:
            logger.info("Distribuídos BB agendado: outro worker já rodando — pulando.")
            return

        data_inicial, data_final = _janela_datas()

        db = SessionLocal()
        try:
            run = coleta_service.criar_run(
                db,
                data_inicial=data_inicial,   # 3 dias pra trás
                data_final=data_final,       # até hoje
                confirmar_ciencia=True,   # gated pela trava global; modo seguro se off
                disparado_por_user_id=None,   # sistema (agendado)
            )
            run_id = run.id
        except Exception:
            logger.exception("Distribuídos BB agendado: falha ao criar o run.")
            db.close()
            return
        finally:
            db.close()

        logger.info(
            "Distribuídos BB agendado: run #%s iniciado (janela %s → %s).",
            run_id, data_inicial, data_final,
        )
        try:
            # Já estamos numa thread do scheduler — roda a coleta síncrona aqui.
            coleta_service.executar_coleta_background(
                run_id,
                data_inicial=data_inicial,
                data_final=data_final,
                coletar_envolvidos=True,
            )
            logger.info("Distribuídos BB agendado: run #%s concluído.", run_id)
        except Exception:
            logger.exception("Distribuídos BB agendado: run #%s falhou.", run_id)


def register_distribuidos_bb_coleta_job(scheduler) -> None:
    """Registra as passagens diárias (uma por horário configurado)."""
    from app.core.config import settings

    if not settings.distribuidos_bb_agendamento_ativo:
        logger.info("Distribuídos BB agendado: desligado (distribuidos_bb_agendamento_ativo=False).")
        return

    horas = _horarios()
    for h in horas:
        scheduler.add_job(
            _tick,
            trigger=CronTrigger(hour=h, minute=0, timezone=_TZ_BR),
            id=f"{JOB_ID_PREFIX}_{h:02d}00",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    logger.info(
        "Distribuídos BB agendado: %s passagem(ns)/dia registrada(s) (horas BRT: %s).",
        len(horas), ", ".join(f"{h:02d}h" for h in horas),
    )
