"""Reconsulta assíncrona do DataJud pros processos do Ativos (APScheduler).

A planilha da Ativos é a fonte primária do cadastro; o DataJud complementa. Como
o processo recém-distribuído pode ainda não estar indexado na base pública (e a
rede ao DataJud às vezes dá timeout), a consulta NÃO trava a ingestão: cada
processo Ativos entra com `datajud_status=pendente` e este worker, de tempos em
tempos, tenta enriquecer os pendentes:

- achou a capa → aplica classe/assunto/órgão/comarca/data + REFINA o polo/escritório
  pela classe real do DataJud, e marca `datajud_status=ok`;
- não achou → mantém `pendente` (carimba `datajud_verificado_em` p/ dar backoff);
  depois de muitas tentativas sem sucesso, marca `sem_capa` e para de insistir.

Roda em thread do BackgroundScheduler → abre a própria SessionLocal.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger("distribuidos_bb.datajud_reconsult")

JOB_ID = "distribuidos_bb_datajud_reconsult"
# Lock entre workers do uvicorn (só 1 bate no DataJud, senão 4× requests).
_LOCK_KEY = 826100008

# Depois de tantas tentativas sem capa, desiste (evita reconsultar eternamente).
_MAX_TENTATIVAS = 12


def reconsultar_pendentes(db, *, limite: int = 100) -> dict:
    """Reconsulta os processos Ativos com DataJud pendente. Devolve contadores."""
    from app.models.distribuidos_bb import (
        CLIENTE_ATIVOS,
        DATAJUD_OK,
        DATAJUD_PENDENTE,
        DATAJUD_SEM_CAPA,
        NIVEL_AVISO,
        POOL_NOVO,
        SECAO_DISTRIBUICAO,
        BbProcesso,
    )
    from app.services.distribuidos_bb.ativos_service import (
        _classe_para_polo,
        _montar_tramitacao,
    )
    from app.services.distribuidos_bb.datajud_ativos import consultar_capa
    from app.services.distribuidos_bb.distribuicao_service import distribuir_processo
    from app.services.distribuidos_bb.log_service import registrar_evento

    pendentes = (
        db.query(BbProcesso)
        .filter(
            BbProcesso.cliente == CLIENTE_ATIVOS,
            BbProcesso.datajud_status == DATAJUD_PENDENTE,
            BbProcesso.cnj.isnot(None),
        )
        .order_by(BbProcesso.datajud_verificado_em.asc().nullsfirst())
        .limit(limite)
        .all()
    )
    if not pendentes:
        return {"verificados": 0, "enriquecidos": 0, "desistidos": 0}

    agora = datetime.now(timezone.utc)
    verificados = enriquecidos = desistidos = 0

    for p in pendentes:
        verificados += 1
        try:
            capa = consultar_capa(p.cnj)
        except Exception:  # noqa: BLE001
            capa = None

        if not capa:
            # Conta tentativas no raw pra desistir depois de _MAX_TENTATIVAS.
            raw = dict(p.raw or {})
            tent = int(raw.get("datajud_tentativas", 0)) + 1
            raw["datajud_tentativas"] = tent
            p.raw = raw
            p.datajud_verificado_em = agora
            if tent >= _MAX_TENTATIVAS:
                p.datajud_status = DATAJUD_SEM_CAPA
                desistidos += 1
            db.commit()
            continue

        # Achou: a capa do DataJud é mais confiável que o TIPO da planilha.
        classe = capa.get("classe") or p.natureza
        p.natureza = classe
        p.acao = capa.get("assunto") or capa.get("classe") or p.acao
        p.situacao = capa.get("assunto") or p.situacao
        if capa.get("data_ajuizamento"):
            p.data_ajuizamento = capa.get("data_ajuizamento")
        p.tramitacao = _montar_tramitacao(
            capa.get("uf"), capa.get("orgao_julgador"), None
        ) or p.tramitacao

        # Polo pela classe REAL do DataJud. ATENÇÃO: o fluxo é sequencial — a
        # ingestão já gerou a planilha e cadastrou no L1 —, então NÃO remanejamos
        # o escritório de quem já foi cadastrado: a pasta existe lá sob o escritório
        # antigo e o banco divergiria em silêncio. Só reroteia quem ainda está no
        # pool; para o resto, AVISA o operador decidir.
        posicao, polo = _classe_para_polo(db, classe)
        if posicao != (p.posicao or ""):
            if p.planilha_status == POOL_NOVO:
                p.polo = polo
                p.posicao = posicao
                distribuir_processo(db, p)  # ainda não foi pro L1: reroteia de verdade
            else:
                registrar_evento(
                    db,
                    secao=SECAO_DISTRIBUICAO,
                    nivel=NIVEL_AVISO,
                    acao="Polo divergente do DataJud",
                    mensagem=(
                        f"O DataJud trouxe a classe '{classe}', que indica {posicao}, "
                        f"mas o processo já foi cadastrado no Legal One como "
                        f"{p.posicao or '—'} ({p.escritorio_path or '—'}). Não remanejei "
                        f"sozinho pra não divergir da pasta — ajuste no L1 se for o caso."
                    ),
                    dados={"classe": classe, "posicao_datajud": posicao, "posicao_atual": p.posicao},
                    processo_id=p.id,
                )

        raw = dict(p.raw or {})
        raw["datajud"] = capa
        p.raw = raw
        p.datajud_status = DATAJUD_OK
        p.datajud_verificado_em = agora
        enriquecidos += 1
        db.commit()

    logger.info(
        "DataJud reconsult: %s verificados, %s enriquecidos, %s desistidos.",
        verificados, enriquecidos, desistidos,
    )
    return {"verificados": verificados, "enriquecidos": enriquecidos, "desistidos": desistidos}


def _tick() -> None:
    from app.db.session import SessionLocal
    from app.services.onerequest._concurrency import single_worker_lock

    with single_worker_lock(_LOCK_KEY) as got:
        if not got:
            return
        db = SessionLocal()
        try:
            reconsultar_pendentes(db)
        except Exception:  # noqa: BLE001
            logger.exception("DataJud reconsult: erro inesperado no tick.")
        finally:
            db.close()


def register_distribuidos_bb_datajud_reconsult_job(scheduler) -> None:
    """Registra a reconsulta recorrente (default 30 min)."""
    from apscheduler.triggers.interval import IntervalTrigger

    from app.core.config import settings

    if not getattr(settings, "distribuidos_bb_datajud_reconsult_ativo", True):
        logger.info("DataJud reconsult: desligado (distribuidos_bb_datajud_reconsult_ativo=False).")
        return

    minutos = max(5, int(getattr(settings, "distribuidos_bb_datajud_reconsult_intervalo_min", 30) or 30))
    scheduler.add_job(
        _tick,
        trigger=IntervalTrigger(minutes=minutos),
        id=JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("DataJud reconsult: registrado (a cada %s min).", minutos)
