"""Tick que promove lotes de classificação parados em AQUECENDO (pub011).

POR QUE PRECISA DE UM WORKER
----------------------------
O envio do lote virou duas fases, como a doc da Batches API prescreve: dispara
um batch de aquecimento, espera ele CONCLUIR, e só então manda o lote real.
Esperar leva minutos — o endpoint HTTP não pode ficar pendurado, então o lote
nasce em AQUECENDO e alguém precisa terminar o serviço depois.

O job noturno já promove no início da sua rodada, mas isso só cobre o caminho
automático. Quem dispara pela tela ficaria esperando até a madrugada seguinte.
Este tick fecha essa lacuna.

Por que o intervalo é curto: AQUECENDO sombreia os registros na coleta
(anti-duplicação), então lote esquecido nesse estado esconde publicação. A
`promover_aquecidos` tem guarda de tempo próprio e manda SEM cache se o
aquecimento não fechar — fila parada é pior que lote caro —, mas o guarda só
age se alguém rodar o tick.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

# 60s: o aquecimento é um batch de ~8 requisições e costuma fechar rápido;
# checar de minuto em minuto custa uma consulta de status e evita segurar o
# lote real à toa.
POLL_INTERVAL_SECONDS = 60


def _tick() -> None:
    """Uma passada. Nunca levanta — não pode derrubar o scheduler."""
    db = SessionLocal()
    try:
        from app.services.publication_batch_classifier import (
            PublicationBatchClassifier,
        )

        resultado = asyncio.run(PublicationBatchClassifier(db).promover_aquecidos())
        if resultado.get("promovidos") or resultado.get("sem_cache"):
            logger.info("publicacoes.aquecimento: %s", resultado)
    except Exception as exc:  # noqa: BLE001
        logger.exception("publicacoes.aquecimento: tick falhou: %s", exc)
    finally:
        db.close()


def register_warm_promote_job(scheduler: BackgroundScheduler) -> None:
    """Registra o tick de promoção no scheduler global."""
    scheduler.add_job(
        _tick,
        trigger=IntervalTrigger(seconds=POLL_INTERVAL_SECONDS),
        id="publicacoes_promover_aquecidos",
        name="Publicações — promove lotes com cache aquecido",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc),
    )
    logger.info(
        "publication_warm_promote_worker: registrado (interval=%ds)",
        POLL_INTERVAL_SECONDS,
    )
