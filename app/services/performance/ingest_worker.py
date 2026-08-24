"""Job diário: ingestão do Minha Equipe via download do relatório do L1.

O relatório "Agenda Analytics" é gerado pelo L1 de manhã (~9h, demora porque é
grande). O job roda das **9h às 12h30 BRT, a cada 30 min**: o primeiro tick que
encontrar o relatório DE HOJE ingere; os seguintes pulam (já sincronizou). É o
retry-até-aparecer que o operador pediu. Advisory lock = só um worker do uvicorn.
"""

import logging

from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

JOB_ID = "perf_minha_equipe_ingest_daily"
# Advisory lock dedicado (onerequest usa ...001/002/003).
_LOCK_KEY = 826100004


def _tick() -> None:
    from app.db.session import SessionLocal
    from app.services.onerequest._concurrency import single_worker_lock
    from app.services.performance.report_ingest import baixar_e_ingerir, ja_sincronizou_hoje

    if ja_sincronizou_hoje():
        logger.info("Minha Equipe ingest: já sincronizou hoje — tick ignorado.")
        return

    with single_worker_lock(_LOCK_KEY) as got:
        if not got:
            logger.info("Minha Equipe ingest: outro worker já rodando — pulando.")
            return
        if ja_sincronizou_hoje():  # double-check sob o lock
            return

        db = SessionLocal()
        try:
            res = baixar_e_ingerir(db)
            if res.get("ok"):
                logger.info(
                    "Minha Equipe ingest: OK — %s tarefas do relatório de %s.",
                    res.get("tarefas"), res.get("data"),
                )
            else:
                logger.info("Minha Equipe ingest: ainda não ingeriu — %s.", res.get("motivo"))
        except Exception:
            logger.exception("Minha Equipe ingest: erro inesperado no tick.")
        finally:
            db.close()


def _tick_gerar(cancelar_massa: bool = False) -> None:
    """Madrugada + meio-dia: FORÇA a geração de um relatório fresco no L1 e ingere
    (não espera a geração matinal do L1). Mantém o pool fresco 2x/dia. Na
    madrugada (cancelar_massa=True), na sequência do pool fresco, roda o
    cancelamento em massa de duplicadas dos subtipos da whitelist."""
    from app.db.session import SessionLocal
    from app.services.onerequest._concurrency import single_worker_lock
    from app.services.performance.report_ingest import (
        gerar_e_ingerir,
        limpar_sync_rodando,
        marcar_sync_rodando,
    )

    with single_worker_lock(_LOCK_KEY) as got:
        if not got:
            logger.info("Minha Equipe geração: outro worker já rodando — pulando.")
            return
        db = None
        try:
            # Publica o MESMO estado usado pela atualização manual. Assim uma
            # aba aberta percebe o job das 4h/13h, bloqueia cliques concorrentes
            # e, ao mudar a versão do snapshot, recarrega o Balanceamento.
            marcar_sync_rodando(
                "agendamento automático",
                "gerando relatório no L1",
            )
            db = SessionLocal()
            res = gerar_e_ingerir(db)
            if res.get("ok"):
                logger.info(
                    "Minha Equipe geração sob demanda: OK — %s tarefas (report %s).",
                    res.get("tarefas"), res.get("relatorio"),
                )
                if cancelar_massa:
                    from app.services.performance.cancel_duplicadas import cancelar_em_massa

                    r = cancelar_em_massa(db, dry_run=False, origem="scheduler")
                    logger.info(
                        "Cancelamento em massa pós-geração: %s candidatos -> %s canceladas, %s preservadas, %s falhas.",
                        r.get("total_candidatos"), r.get("cancelled"), r.get("preservadas"), r.get("falhas"),
                    )
            else:
                logger.warning(
                    "Minha Equipe geração sob demanda: falhou — %s (cancelamento em massa pulado).",
                    res.get("motivo"),
                )
        except Exception:
            logger.exception("Minha Equipe geração sob demanda: erro inesperado.")
        finally:
            limpar_sync_rodando()
            if db is not None:
                db.close()


def register_perf_ingest_job(scheduler) -> None:
    # Fallback matinal: baixa a geração que o L1 faz de manhã (9h-12h30, retry).
    scheduler.add_job(
        _tick,
        trigger=CronTrigger(hour="9-12", minute="0,30", timezone="America/Sao_Paulo"),
        id=JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    # Geração SOB DEMANDA (pool fresco): madrugada (4h, + cancelamento em massa
    # de duplicadas) e meio-dia (13h, só refresca o pool).
    scheduler.add_job(
        _tick_gerar,
        args=[True],  # madrugada: gera o pool fresco E cancela duplicadas em massa
        trigger=CronTrigger(hour="4", minute="0", timezone="America/Sao_Paulo"),
        id="perf_minha_equipe_gerar_madrugada",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _tick_gerar,
        args=[False],  # meio-dia: só refresca o pool
        trigger=CronTrigger(hour="13", minute="0", timezone="America/Sao_Paulo"),
        id="perf_minha_equipe_gerar_meiodia",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info(
        "Minha Equipe: jobs registrados — download matinal (9h-12h30) + geração 4h (pool+cancelamento massa) e 13h (pool) BRT."
    )
