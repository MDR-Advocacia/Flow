"""Esteira de verificação da Análise de Risco no portal BB.

Consome a fila de `arb_analise_risco_tarefa` (verif_status NA_FILA ou ERRO —
tarefas cumpridas no L1 aguardando conferência) e, com a sessão OneLog, consulta
a pendência de análise no portal (ver portal_bb.py). Resultado:

  - pendência FECHADA -> análise feita, verif VERIFICADA, divergente=False
  - pendência ABERTA  -> cumpriu a tarefa SEM fazer a análise: divergente=True
    (o farol vermelho do painel do supervisor)
  - erro/timeout      -> verif ERRO com o motivo; a linha CONTINUA na fila e o
    próximo tick re-tenta (ordenação por portal_verificado_em nullsfirst = fila
    round-robin natural, mesmo padrão do monitor de cadastro do Distribuídos BB)

Roda no BackgroundScheduler (IntervalTrigger) com advisory lock de instância
única. Kill-switch e cadência em settings (analise_risco_verificacao_*).
"""

from __future__ import annotations

import logging

from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

JOB_ID = "analise_risco_portal_verify"
# Registro informal de chaves: 826100001-003 onerequest, ...004 perf ingest,
# ...005 recursal, ...006 coleta BB, ...007 monitor cadastro. Esta é a 008.
_LOCK_KEY = 826100008


def verificar_fila(db, sess, *, base_url=None, limite: int = 50) -> dict:
    """Verifica até `limite` linhas da fila. `sess` = requests.Session já
    autenticada no portal (OneLog). Commit por linha: uma falha no meio não
    desfaz o que já foi verificado."""
    from app.models.analise_risco import (
        AnaliseRiscoTarefa,
        VERIF_ERRO,
        VERIF_NA_FILA,
    )
    from app.services.analise_risco.portal_bb import (
        consultar_pendencia,
        npj_sem_mascara,
        resolver_npj_por_cnj,
    )
    from app.services.analise_risco.service import (
        aplicar_erro_verificacao,
        aplicar_verificacao,
    )

    fila = (
        db.query(AnaliseRiscoTarefa)
        .filter(AnaliseRiscoTarefa.verif_status.in_([VERIF_NA_FILA, VERIF_ERRO]))
        .order_by(AnaliseRiscoTarefa.portal_verificado_em.asc().nullsfirst(), AnaliseRiscoTarefa.id.asc())
        .limit(limite)
        .all()
    )

    ok = erros = divergentes = 0
    for row in fila:
        try:
            numero = npj_sem_mascara(row.npj)
            if not numero and row.cnj:
                # Pasta sem NPJ utilizável: resolve pelo CNJ na pesquisa do PAJ
                # e guarda pra não repetir a resolução no próximo tick.
                numero = resolver_npj_por_cnj(sess, row.cnj, base_url=base_url)
                if numero:
                    row.npj = f"{numero[:4]}/{numero[4:]}-000"
            if not numero:
                raise RuntimeError(
                    f"sem NPJ utilizável (pasta={row.npj!r}, cnj={row.cnj!r})"
                )

            pend = consultar_pendencia(sess, numero, base_url=base_url)
            aplicar_verificacao(
                row,
                pendencia_aberta=pend.pendencia_aberta,
                estado=pend.estado,
                exito=pend.exito,
            )
            ok += 1
            if pend.pendencia_aberta:
                divergentes += 1
        except Exception as e:  # noqa: BLE001 — item falho fica na fila
            aplicar_erro_verificacao(row, str(e))
            erros += 1
            logger.warning(
                "Análise de Risco verify: falha na tarefa L1 %s: %s", row.l1_task_id, e
            )
        db.commit()

    resultado = {"fila": len(fila), "verificadas": ok, "divergentes": divergentes, "erros": erros}
    if fila:
        logger.info("Análise de Risco verify: %s", resultado)
    return resultado


def _tick() -> None:
    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.models.analise_risco import AnaliseRiscoTarefa, VERIF_ERRO, VERIF_NA_FILA
    from app.services.distribuidos_bb.onelog_client import OneLogClient
    from app.services.distribuidos_bb.vinculos_bb import montar_sessao
    from app.services.onerequest._concurrency import single_worker_lock

    if not settings.analise_risco_verificacao_ativa:
        return

    client = OneLogClient()
    if not client.configurado:
        logger.info("Análise de Risco verify: OneLog não configurado — pulando tick.")
        return

    with single_worker_lock(_LOCK_KEY) as got:
        if not got:
            return

        db = SessionLocal()
        try:
            # Fila vazia? Não gasta login do OneLog à toa.
            tem_fila = (
                db.query(AnaliseRiscoTarefa.id)
                .filter(AnaliseRiscoTarefa.verif_status.in_([VERIF_NA_FILA, VERIF_ERRO]))
                .first()
            )
            if not tem_fila:
                return

            try:
                sessao = client.obter_sessao()
            except Exception:
                logger.exception("Análise de Risco verify: falha ao obter sessão OneLog — re-tenta no próximo tick.")
                return

            sess = montar_sessao(sessao.get("cookies", []), sessao.get("user_agent", ""))
            verificar_fila(db, sess, limite=settings.analise_risco_verificacao_lote)
        except Exception:
            logger.exception("Análise de Risco verify: falha no tick.")
        finally:
            db.close()


def register_analise_risco_verify_job(scheduler) -> None:
    from app.core.config import settings

    scheduler.add_job(
        _tick,
        trigger=IntervalTrigger(minutes=settings.analise_risco_verificacao_intervalo_min or 10),
        id=JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Análise de Risco: job de verificação no portal registrado.")
