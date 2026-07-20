"""Job horário do OneRequest: LÊ o Postgres da FONTE (onde a RPA local escreve)
e espelha pro `onr_solicitacoes` do Flow.

Arquitetura nova: o app da RPA foi desmembrado — a RPA roda no escritório e
grava num Postgres separado (recurso Coolify do OneRequest); o Flow CONSOME de
lá. Este job lê de hora em hora e faz upsert (Flow é dono do tratamento; só os
campos capturados + status_sistema são espelhados).

Além do job horário, `sync_on_demand()` roda o MESMO espelhamento quando um
operador abre a página do OneRequest — com throttle pra várias pessoas abrindo
em sequência não martelarem o Postgres da fonte.

READ-ONLY DE VERDADE: a sessão de leitura é aberta com `readonly=True`, então o
Postgres rejeita qualquer escrita acidental na fonte. Ligado só quando
`ONEREQUEST_SOURCE_DB_URL` está setada (env do Coolify). Roda em thread do
BackgroundScheduler, então abre a própria SessionLocal pro lado do Flow.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

JOB_ID = "onerequest_source_sync_hourly"
# Chave do advisory lock (só um worker do uvicorn sincroniza por vez).
_LOCK_KEY = 826100001

# Carimbo do último sync BEM-SUCEDIDO (do job horário OU on-demand). É o
# throttle do sync disparado ao abrir a página do OneRequest.
LAST_SYNC_OK_KEY = "onerequest_source_last_sync_ok_at"
# Abrir a página só re-sincroniza se o último sync ok for mais velho que isso.
ON_DEMAND_THROTTLE_SECONDS = 180

# Só os campos CAPTURADOS pela RPA (o tratamento vive no Flow e é preservado).
_SOURCE_QUERY = (
    "SELECT numero_solicitacao, titulo, npj_direcionador, prazo, texto_dmi, "
    "numero_processo, polo, recebido_em, status_sistema, "
    # Campos de tratamento (espelhados quando onerequest_sync_espelha_tratamento=True).
    "responsavel, setor, data_agendamento, anotacao FROM solicitacoes"
)


def run_source_sync() -> Optional[dict]:
    """Lê a fonte e espelha pro `onr_solicitacoes`. Retorna o resultado do
    espelhamento, ou None quando não rodou (sem config, outro worker já
    sincronizando, ou falha de leitura/escrita)."""
    import psycopg2
    import psycopg2.extras

    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.services.app_settings import set_setting
    from app.services.onerequest._concurrency import single_worker_lock
    from app.services.onerequest.intake_service import OnerequestIntakeService

    dsn = settings.onerequest_source_db_url
    if not dsn:
        logger.info("OneRequest sync: ONEREQUEST_SOURCE_DB_URL não setada — pulando tick.")
        return None

    # Só UM worker do uvicorn sincroniza por vez (senão inserts concorrentes
    # batem em duplicate key). Os demais workers pulam este tick.
    with single_worker_lock(_LOCK_KEY) as got:
        if not got:
            logger.info("OneRequest sync: outro worker já está sincronizando — pulando.")
            return None

        # 1) LÊ a fonte — sessão READ-ONLY (o Postgres barra qualquer escrita).
        try:
            conn = psycopg2.connect(dsn, connect_timeout=15)
            try:
                conn.set_session(readonly=True, autocommit=True)
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(_SOURCE_QUERY)
                    rows = [dict(r) for r in cur.fetchall()]
            finally:
                conn.close()
        except Exception:
            logger.exception("OneRequest sync: falha ao LER o Postgres da fonte.")
            return None

        logger.info("OneRequest sync: %s linhas lidas da fonte.", len(rows))

        # 2) Espelha pro onr_solicitacoes (preserva tratamento do Flow).
        db = SessionLocal()
        try:
            res = OnerequestIntakeService(db).sync_from_source(rows)
            logger.info("OneRequest sync concluído: %s", res)
        except Exception:
            logger.exception("OneRequest sync: falha ao espelhar pro onr_solicitacoes.")
            return None
        finally:
            db.close()

    # Carimba o sucesso (fora do lock — é só o throttle do on-demand).
    try:
        set_setting(LAST_SYNC_OK_KEY, datetime.now(timezone.utc).isoformat())
    except Exception as e:  # noqa: BLE001
        logger.warning("OneRequest sync: falha ao carimbar last_sync_ok: %s", e)
    return res


def sync_on_demand() -> dict:
    """Sync disparado ao abrir a página do OneRequest.

    Roda o mesmo espelhamento do job horário, mas SÓ se o último sync ok tiver
    mais de ON_DEMAND_THROTTLE_SECONDS — assim N operadores abrindo a página em
    sequência geram no máximo 1 leitura na fonte a cada janela. O advisory lock
    dentro do run_source_sync ainda garante 1 sync por vez entre workers."""
    from app.services.app_settings import get_setting

    last = get_setting(LAST_SYNC_OK_KEY)
    if last:
        try:
            idade = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
        except ValueError:
            idade = None
        if idade is not None and 0 <= idade < ON_DEMAND_THROTTLE_SECONDS:
            return {
                "executado": False,
                "motivo": f"Dados já sincronizados há {int(idade)}s.",
                "resultado": None,
            }

    res = run_source_sync()
    if res is None:
        return {
            "executado": False,
            "motivo": "Sync não rodou (fonte indisponível, sem configuração ou já em andamento).",
            "resultado": None,
        }
    return {"executado": True, "motivo": None, "resultado": res}


def _tick() -> None:
    run_source_sync()


def register_onerequest_source_sync_job(scheduler) -> None:
    """Registra o job de sync (de hora em hora) + uma 1ª execução no boot."""
    from datetime import timedelta

    scheduler.add_job(
        _tick,
        trigger=IntervalTrigger(hours=1),
        id=JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        # 1ª rodada logo após o boot (popula o que a RPA já gravou), depois horária.
        next_run_time=datetime.now() + timedelta(seconds=30),
    )
    logger.info("OneRequest: job de sync da fonte (horário) registrado.")
