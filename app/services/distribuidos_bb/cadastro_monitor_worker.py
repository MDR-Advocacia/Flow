"""Monitor de cadastro no Legal One (APScheduler).

A partir do momento em que a planilha é gerada, cada processo fica
`PENDENTE_CADASTRO`. De 2 em 2 minutos este monitor bate na API do Legal One
procurando a pasta por **CNJ + escritório responsável**; quando acha, marca o
processo como `CADASTRADO_L1` (guarda o id e o folder da pasta) e loga.

Ciclo: NOVO → (operador gera planilha) PENDENTE_CADASTRO → (monitor acha) CADASTRADO_L1.

Roda em thread do BackgroundScheduler → abre a própria SessionLocal.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger("distribuidos_bb.monitor_cadastro")

JOB_ID = "distribuidos_bb_monitor_cadastro"
# Lock entre workers do uvicorn (só 1 checa o L1, senão 4× requests/corrida).
_LOCK_KEY = 826100007


def verificar_pendentes(db, *, client=None, limite: int = 300) -> dict:
    """Varre os PENDENTE_CADASTRO com CNJ e confirma no L1 os que já existem.

    Devolve {verificados, confirmados, sem_cnj_ignorados}.
    """
    from app.models.distribuidos_bb import (
        NIVEL_SUCESSO,
        POOL_CADASTRADO_L1,
        POOL_PENDENTE_CADASTRO,
        SECAO_CADASTRO,
        BbProcesso,
    )
    from app.services.distribuidos_bb import cadastro_l1
    from app.services.distribuidos_bb.log_service import registrar_evento

    pendentes = (
        db.query(BbProcesso)
        .filter(BbProcesso.planilha_status == POOL_PENDENTE_CADASTRO)
        .order_by(BbProcesso.l1_verificado_em.asc().nullsfirst())
        .limit(limite)
        .all()
    )
    if not pendentes:
        return {"verificados": 0, "confirmados": 0, "sem_cnj_ignorados": 0}

    if client is None:
        from app.services.legal_one_client import LegalOneApiClient

        client = LegalOneApiClient()

    office_cache: dict[str, int | None] = {}
    verificados = confirmados = sem_id = 0
    agora = datetime.now(timezone.utc)

    for p in pendentes:
        # Sem NENHUM identificador (nem CNJ nem NPJ) não dá pra confirmar.
        if not p.cnj and not p.npj:
            sem_id += 1
            continue
        verificados += 1
        try:
            path = p.escritorio_path or ""
            if path not in office_cache:
                office_cache[path] = cadastro_l1.resolver_office_por_path(client, path)
            office_id = office_cache[path]

            pasta = None
            via = None
            # 1) Por CNJ + escritório (respeita BB vs Ativos no mesmo CNJ).
            if p.cnj:
                res = cadastro_l1.verificar_duplicado(client, p.cnj, office_id)
                achados = res.get("no_mesmo_escritorio") or []
                if achados:
                    pasta, via = achados[0], f"CNJ {p.cnj}"
            # 2) Por NPJ (o import grava o NPJ como TITLE) — resolve os SEM CNJ
            #    e reforça os demais. Prefere mesmo escritório, senão aceita o NPJ
            #    (que é único por processo BB).
            if pasta is None and p.npj:
                por_npj = cadastro_l1.buscar_lawsuit_por_npj(client, p.npj)
                if por_npj:
                    mesmo = [m for m in por_npj if m.get("office") == office_id]
                    pasta = (mesmo or por_npj)[0]
                    via = f"NPJ {p.npj}"

            p.l1_verificado_em = agora
            if pasta:
                p.planilha_status = POOL_CADASTRADO_L1
                p.l1_lawsuit_id = pasta.get("id")
                p.l1_folder = pasta.get("folder")
                p.cadastro_confirmado_em = agora
                confirmados += 1
                registrar_evento(
                    db, secao=SECAO_CADASTRO, nivel=NIVEL_SUCESSO,
                    acao="Cadastro confirmado no L1",
                    mensagem=(
                        f"Cadastro confirmado no Legal One: pasta {pasta.get('folder')} "
                        f"(id {pasta.get('id')}) — casado por {via}."
                    ),
                    dados={"lawsuit_id": pasta.get("id"), "folder": pasta.get("folder"), "via": via},
                    processo_id=p.id, run_id=p.run_id,
                )
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception(
                "Monitor cadastro: falha ao verificar processo %s (CNJ %s / NPJ %s).",
                p.id, p.cnj, p.npj,
            )

    logger.info(
        "Monitor cadastro L1: %s verificado(s), %s confirmado(s), %s sem identificador.",
        verificados, confirmados, sem_id,
    )
    return {"verificados": verificados, "confirmados": confirmados, "sem_cnj_ignorados": sem_id}


def _tick() -> None:
    from app.db.session import SessionLocal
    from app.services.onerequest._concurrency import single_worker_lock

    # Só UM worker do uvicorn bate no L1 (evita 4× requests e corrida na marcação).
    with single_worker_lock(_LOCK_KEY) as got:
        if not got:
            return
        db = SessionLocal()
        try:
            verificar_pendentes(db)
        except Exception:  # noqa: BLE001
            logger.exception("Monitor cadastro L1: erro inesperado no tick.")
        finally:
            db.close()


def register_distribuidos_bb_monitor_cadastro_job(scheduler) -> None:
    """Registra o monitor recorrente (default 2 min)."""
    from apscheduler.triggers.interval import IntervalTrigger

    from app.core.config import settings

    if not settings.distribuidos_bb_monitor_cadastro_ativo:
        logger.info("Monitor cadastro L1: desligado (distribuidos_bb_monitor_cadastro_ativo=False).")
        return

    minutos = max(1, int(settings.distribuidos_bb_monitor_intervalo_min or 2))
    scheduler.add_job(
        _tick,
        trigger=IntervalTrigger(minutes=minutos),
        id=JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Monitor cadastro L1: registrado (a cada %s min).", minutos)
