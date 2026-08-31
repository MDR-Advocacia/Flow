from contextlib import asynccontextmanager
import logging
import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import models as _models
from app.api.v1.endpoints import (
    admin,
    admin_notices,
    auth,
    ajus,
    automations,
    base_processual,
    base_processual_api_keys,
    base_processual_backfill,
    base_processual_bulk,
    base_processual_conversao,
    base_processual_exports,
    base_processual_public,
    capture_health,
    citacoes_bm,
    classifier,
    classificador,
    contatos_legalone,
    balanceador,
    dashboard,
    distribuidos_bb,
    ged_legalone,
    offices,
    encerramentos,
    onenotify_bb,
    onerequest,
    performance,
    prazos_iniciais,
    prazos_iniciais_legacy_tasks,
    prazos_iniciais_scheduling,
    recursal,
    publication_treatment,
    publications,
    publications_performance,
    squads,
    task_templates,
    tasks,
    taxonomy_admin,
    user_feedback,
    users,
    varredura,
    cargos,
    uso,
    analise_risco_intake,
    distribuidos_bb_vinculos_intake,
)
from app.core import auth as auth_security
from app.core.config import settings
from app.core.scheduler import scheduler
from app.services.batch_worker import BatchExecutionWorker

logger = logging.getLogger(__name__)
batch_worker = BatchExecutionWorker()

# ── Eleição do worker que roda o trabalho de fundo ────────────────────────
# Ver o docstring de `_sou_o_lider`. Guardado em módulo pra o lock viver
# enquanto o processo viver (soltar a referência liberaria o flock).
_LOCK_TRABALHO_FUNDO = None
_LOCK_PATH = "/tmp/flow-scheduler.lock"


def _sou_o_lider() -> bool:
    """Este worker é o que deve rodar scheduler e batch worker?

    O uvicorn roda com --workers N e cada worker executa este startup. Sem
    eleição, os N processos sobem N schedulers e cada job agendado dispara N
    vezes (medido: 4x em produção, 13/08/2026). `max_instances=1` não cobre
    isso — ele só age dentro de um mesmo scheduler.

    Lock de arquivo não-bloqueante: quem pega, lidera. Quem não pega, continua
    servindo HTTP normalmente. O flock é liberado pelo SO se o processo morrer,
    então o worker respawnado pelo uvicorn assume sozinho.

    Se o `filelock` não estiver disponível por algum motivo, o comportamento
    volta a ser o de antes (todos lideram) — degradar pro conhecido é melhor
    que ficar SEM trabalho de fundo nenhum.
    """
    global _LOCK_TRABALHO_FUNDO
    try:
        from filelock import FileLock, Timeout
    except Exception:  # noqa: BLE001
        logger.warning(
            "filelock indisponível — este worker vai rodar o trabalho de fundo "
            "sem eleição (comportamento antigo)."
        )
        return True

    try:
        _LOCK_TRABALHO_FUNDO = FileLock(_LOCK_PATH)
        _LOCK_TRABALHO_FUNDO.acquire(timeout=0)
        return True
    except Timeout:
        _LOCK_TRABALHO_FUNDO = None
        return False
    except Exception:  # noqa: BLE001
        logger.exception("Falha na eleição do worker de fundo; assumindo liderança.")
        return True


@asynccontextmanager
async def lifespan(_: FastAPI):
    lider = _sou_o_lider()
    if not lider:
        logger.info(
            "Worker secundário: serve HTTP, sem scheduler nem batch worker "
            "(o trabalho de fundo roda no worker líder)."
        )
        try:
            yield
        finally:
            # O acumulador do relatório de utilização é POR PROCESSO: este
            # worker atende requisição e acumula o seu próprio pedaço. O flush
            # periódico se dispara sozinho dentro do `registrar()` (não depende
            # do scheduler), mas o da PARADA precisa acontecer aqui também —
            # sem isso, todo redeploy perderia o acumulado dos secundários e o
            # relatório subcontaria justamente quem estava trabalhando na hora.
            try:
                from app.services import uso_service

                gravadas = uso_service.descarregar()
                if gravadas:
                    logger.info(
                        "uso: %s linha(s) gravadas no shutdown (worker secundário)",
                        gravadas,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("uso: flush de shutdown falhou (%s)", exc)
        return

    logger.info("Worker LÍDER: iniciando scheduler e trabalho de fundo.")
    batch_worker.start()
    scheduler.start()
    logger.info("APScheduler started")

    # Repovoa o scheduler com as automations persistidas e habilitadas.
    try:
        from app.db.session import SessionLocal
        from app.models.scheduled_automation import ScheduledAutomation
        from app.services.scheduled_automation_service import ScheduledAutomationService

        db = SessionLocal()
        try:
            service = ScheduledAutomationService(db=db, scheduler=scheduler)
            enabled = (
                db.query(ScheduledAutomation)
                .filter(ScheduledAutomation.is_enabled == True)  # noqa: E712
                .all()
            )
            for automation in enabled:
                try:
                    service._register_job(automation)
                except Exception:
                    logger.exception(
                        "Falha ao registrar automation %d no scheduler", automation.id
                    )
            if enabled:
                logger.info("Repovoei %d automation(s) no scheduler.", len(enabled))
        finally:
            db.close()
    except Exception:
        logger.exception("Falha ao repopular automations no startup.")

    try:
        from datetime import datetime, timedelta, timezone

        from app.db.session import SessionLocal
        from app.models.scheduled_automation import ScheduledAutomation, ScheduledAutomationRun

        # Só é órfã a run que está PARADA — não toda run "running".
        #
        # Antes isto derrubava qualquer execução em andamento, e o estrago foi
        # exatamente esse: nas madrugadas de 29, 30 e 31/08/2026 a captura de
        # publicações começou às 01:00, estava na página 58 de 385 e progredindo,
        # um worker do uvicorn subiu (a liderança é por filelock e troca sem o
        # container reiniciar — `restartCount` ficou em 0 os três dias), e este
        # bloco carimbou a run viva como "API reiniciou durante a execução".
        # Três dias sem publicação nenhuma, e a mensagem apontava pra um
        # reinício que nunca houve.
        #
        # O critério agora é heartbeat: run cujo `progress_updated_at` (ou, na
        # falta dele, `started_at`) parou há mais de _ORFA_APOS_MIN de fato
        # morreu com o processo. Quem atualizou progresso agora há pouco está
        # viva em OUTRO worker e não se toca.
        _ORFA_APOS_MIN = int(os.environ.get("AUTOMATION_ORFA_APOS_MIN", "15"))

        db = SessionLocal()
        try:
            corte = datetime.now(timezone.utc) - timedelta(minutes=_ORFA_APOS_MIN)
            candidatas = (
                db.query(ScheduledAutomationRun)
                .filter(ScheduledAutomationRun.status == "running")
                .all()
            )
            orphans, vivas = [], 0
            for run in candidatas:
                batida = run.progress_updated_at or run.started_at
                if batida is not None and batida.tzinfo is None:
                    batida = batida.replace(tzinfo=timezone.utc)
                if batida is not None and batida > corte:
                    vivas += 1          # deu sinal de vida agora: outro worker
                    continue
                orphans.append(run)
            if vivas:
                logger.info(
                    "%d run(s) em andamento com heartbeat recente — preservadas "
                    "(rodando em outro worker).", vivas,
                )
            for run in orphans:
                run.status = "failed"
                run.error_message = (
                    f"Execução sem sinal de vida há mais de {_ORFA_APOS_MIN} min "
                    f"— o processo que a conduzia morreu."
                )
                run.finished_at = datetime.now(timezone.utc)
                run.progress_phase = "orphaned"
                run.progress_message = "Execução interrompida por reinício da API"
                run.progress_updated_at = datetime.now(timezone.utc)
                automation = (
                    db.query(ScheduledAutomation)
                    .filter(ScheduledAutomation.id == run.automation_id)
                    .first()
                )
                if automation:
                    automation.last_status = "failed"
                    automation.last_error = run.error_message
            if orphans:
                logger.warning("Reapei %d run(s) órfãs de automations.", len(orphans))
                db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("Falha ao reapear runs órfãs no startup.")

    # Reapa syncs de escritório órfãs — thread daemon morre no restart sem rodar o finally
    try:
        from datetime import datetime, timezone

        from app.db.session import SessionLocal
        from app.models.office_lawsuit_index import OfficeLawsuitSync

        db = SessionLocal()
        try:
            stuck = (
                db.query(OfficeLawsuitSync)
                .filter(OfficeLawsuitSync.in_progress == True)  # noqa: E712
                .all()
            )
            for state in stuck:
                state.in_progress = False
                state.last_sync_status = "error"
                state.last_sync_error = (
                    "API reiniciou durante a sincronização - thread interrompida."
                )
                state.finished_at = datetime.now(timezone.utc)
            if stuck:
                logger.warning(
                    "Reapei %d sync(s) órfã(s) de escritório (in_progress=True).",
                    len(stuck),
                )
                db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("Falha ao reapear syncs órfãs de escritório no startup.")

    # Reapa buscas de publicações presas em EXECUTANDO — o try/except interno
    # do PublicationSearchService não cobre SIGKILL/OOM (caso visto em prod
    # na Busca #2 em 22/04/2026: status ficou EXECUTANDO por 30+ min com
    # total_new=0 na UI, sem error_message). Também registra job periódico
    # no APScheduler pra cobrir casos sem restart de container.
    try:
        from app.services.publication_search_watchdog import (
            reap_orphaned_searches_on_startup,
            register_publication_search_watchdog_job,
        )

        reap_orphaned_searches_on_startup()
        register_publication_search_watchdog_job(scheduler)
    except Exception:
        logger.exception(
            "Falha ao inicializar watchdog de buscas de publicações no startup."
        )

    # Enriquecimento de publicações com as etiquetas (tags) do processo no L1
    # — caminho web com cache local; chip "Estratégico" e afins na tela de
    # tratamento (uma publicação estratégica já foi perdida sem isso).
    try:
        from app.services.publication_etiquetas import (
            register_publication_etiquetas_job,
        )

        register_publication_etiquetas_job(scheduler)
    except Exception:
        logger.exception("Falha ao registrar job de etiquetas L1 das publicações.")

    # Worker periódico do fluxo "Agendar Prazos Iniciais" — gated pela flag
    # prazos_iniciais_auto_classification_enabled (default off).
    try:
        from app.services.prazos_iniciais.auto_worker import (
            register_auto_classification_job,
        )

        register_auto_classification_job(scheduler)
    except Exception:
        logger.exception(
            "Falha ao registrar worker auto de prazos iniciais no startup."
        )

    try:
        from app.services.prazos_iniciais.legacy_task_queue_worker import (
            register_legacy_task_cancellation_job,
        )

        register_legacy_task_cancellation_job(scheduler)
    except Exception:
        logger.exception(
            "Falha ao registrar worker de cancelamento legado de prazos iniciais no startup."
        )

    # Worker periódico do disparo de Tratamento Web (Onda 3 #6) — gated
    # por prazos_iniciais_dispatch_enabled (default off).
    try:
        from app.services.prazos_iniciais.dispatch_worker import (
            register_dispatch_job,
        )

        register_dispatch_job(scheduler)
    except Exception:
        logger.exception(
            "Falha ao registrar dispatch_worker de prazos iniciais no startup."
        )

    # Publicações — promove lotes cujo cache já aqueceu (pub011). O envio é
    # em duas fases e o lote nasce em AQUECENDO; sem este tick, só o job
    # noturno promoveria, e quem dispara pela tela esperaria a madrugada.
    try:
        from app.services.publication_warm_promote_worker import (
            register_warm_promote_job,
        )

        register_warm_promote_job(scheduler)
    except Exception:
        logger.exception(
            "Falha ao registrar worker de promoção de lotes aquecidos."
        )

    # Worker periodico do Classificador — polling de batches Anthropic.
    try:
        from app.services.classificador.poll_worker import (
            register_classificador_poll_job,
        )

        register_classificador_poll_job(scheduler)
    except Exception:
        logger.exception(
            "Falha ao registrar worker do Classificador no startup."
        )

    # Motor dormente do Classificador — agrupa PDFs do robo em batches.
    try:
        from app.services.classificador.pending_worker import (
            register_classificador_pending_job,
        )

        register_classificador_pending_job(scheduler)
    except Exception:
        logger.exception(
            "Falha ao registrar motor dormente do Classificador no startup."
        )

    # Worker de geracao de relatorios em background (substitui
    # BackgroundTasks do FastAPI que se mostrou instavel).
    try:
        from app.services.classificador.report_worker import (
            register_classificador_report_job,
        )

        register_classificador_report_job(scheduler)
    except Exception:
        logger.exception(
            "Falha ao registrar report_worker do Classificador no startup."
        )

    # Worker de upload do GED LegalOne — CORE do modulo (sobe os arquivos
    # dos lotes pro GED do L1). Default ON (ged_legalone_worker_enabled).
    try:
        from app.services.ged_legalone.upload_worker import (
            register_ged_legalone_job,
        )

        register_ged_legalone_job(scheduler)
    except Exception:
        logger.exception(
            "Falha ao registrar worker do GED LegalOne no startup."
        )

    # Worker de enriquecimento de Contatos LegalOne — acha o contato por
    # CPF/CNPJ e grava telefone/e-mail/endereco. Default ON.
    try:
        from app.services.contatos_legalone.enrich_worker import (
            register_contatos_legalone_job,
        )

        register_contatos_legalone_job(scheduler)
    except Exception:
        logger.exception(
            "Falha ao registrar worker de Contatos LegalOne no startup."
        )

    # Cron diário de cleanup dos PDFs da habilitação (Onda 3).
    # Pega resíduos: intakes já uplodados pro GED mas com pdf_path != None,
    # e também arquivos antigos (retenção) de intakes que travaram fora
    # do fluxo crítico.
    try:
        from app.services.prazos_iniciais.pdf_cleanup_worker import (
            register_pdf_cleanup_job,
        )

        register_pdf_cleanup_job(scheduler)
    except Exception:
        logger.exception(
            "Falha ao registrar worker de cleanup de PDFs de prazos iniciais no startup."
        )

    # Job diário do módulo Citações BM — puxa processos novos do L1
    # (Banco Master/Réu) e varre o DataJud atrás de movimentações/citação.
    try:
        from app.services.citacoes_bm.scan_worker import (
            register_citacoes_bm_scan_job,
        )

        register_citacoes_bm_scan_job(scheduler)
    except Exception:
        logger.exception(
            "Falha ao registrar job diário do Citações BM no startup."
        )

    # Auto-refresh horário do status L1 do OneRequest (DMIs que vencem hoje).
    # A regra liga/desliga via setting (play/stop na UI); o job só faz trabalho
    # quando habilitada (default LIGADO).
    try:
        from app.services.onerequest.l1_autorefresh_worker import (
            register_onerequest_l1_autorefresh_job,
        )

        register_onerequest_l1_autorefresh_job(scheduler)
    except Exception:
        logger.exception(
            "Falha ao registrar job de auto-refresh L1 do OneRequest no startup."
        )

    # Sync read-only do Postgres da FONTE do OneRequest (a RPA grava lá; o Flow
    # lê e espelha pro onr_solicitacoes). Só roda se ONEREQUEST_SOURCE_DB_URL setada.
    try:
        from app.services.onerequest.source_sync_worker import (
            register_onerequest_source_sync_job,
        )

        register_onerequest_source_sync_job(scheduler)
    except Exception:
        logger.exception(
            "Falha ao registrar job de sync da fonte do OneRequest no startup."
        )

    # Verificação PROATIVA de existência do processo no L1 (CNJ->NPJ, sem criar
    # tarefa): sinaliza no painel se a pasta existe antes do agendamento.
    try:
        from app.services.onerequest.proc_l1_check_worker import (
            register_onerequest_proc_l1_check_job,
        )

        register_onerequest_proc_l1_check_job(scheduler)
    except Exception:
        logger.exception(
            "Falha ao registrar job de verificação de processo no L1 do OneRequest no startup."
        )

    # Retenção dos artefatos do Playwright: as capturas de tela dos runners
    # eram 5,21 GB de 5,8 GB em /app/output, sem nenhuma política de descarte,
    # num disco a 83%. Apaga só `artifacts/` de run com mais de 14 dias — o
    # status.json (que a tela lê) e os logs ficam.
    try:
        from app.services.playwright_artifacts_cleanup import (
            register_playwright_artifacts_cleanup_job,
        )

        register_playwright_artifacts_cleanup_job(scheduler)
    except Exception:
        logger.exception("Falha ao registrar a retenção de artefatos do Playwright.")

    # Minha Equipe: ingestão diária via download do relatório "Agenda Analytics"
    # do L1 (9h-12h30 BRT, 30/30min até o relatório do dia aparecer).
    try:
        from app.services.performance.ingest_worker import register_perf_ingest_job

        register_perf_ingest_job(scheduler)
    except Exception:
        logger.exception("Falha ao registrar job de ingestão do Minha Equipe no startup.")

    # Reagendamentos: bracket diário 07h/19h — foto da manhã vs. noite detecta os
    # adiamentos de prazo feitos DURANTE o dia (o "calo" que era invisível).
    try:
        from app.services.performance.reagendamento_worker import register_reagendamento_jobs

        register_reagendamento_jobs(scheduler)
    except Exception:
        logger.exception("Falha ao registrar jobs de reagendamento no startup.")

    # Análise Recursal: worker fire-and-forget — auto-submete os PDFs subidos e
    # auto-aplica os vereditos quando o batch termina (sem depender da tela).
    try:
        from app.services.recursal.worker import register_recursal_worker

        register_recursal_worker(scheduler)
    except Exception:
        logger.exception("Falha ao registrar o auto-worker da Análise Recursal no startup.")

    # Tratamento Web de publicações: autorun recorrente — cron configurável
    # (default 01h/04h/12h/22h BRT) que dispara o runner Playwright pra zerar
    # a fila de pendências sem depender do operador clicar "Iniciar execução".
    try:
        from app.services.publication_treatment_autorun import (
            register_publication_treatment_autorun_job,
        )

        register_publication_treatment_autorun_job(scheduler)
    except Exception:
        logger.exception(
            "Falha ao registrar o autorun do Tratamento Web de publicações no startup."
        )

    # Distribuídos BB (Cadastro de Processo): coleta agendada 3x/dia + planilha.
    try:
        from app.services.distribuidos_bb.schedule_worker import (
            register_distribuidos_bb_coleta_job,
        )

        register_distribuidos_bb_coleta_job(scheduler)
    except Exception:
        logger.exception(
            "Falha ao registrar o agendamento da coleta Distribuídos BB no startup."
        )

    # Distribuídos BB: monitor que confirma o cadastro no L1 (de 2 em 2 min).
    try:
        from app.services.distribuidos_bb.cadastro_monitor_worker import (
            register_distribuidos_bb_monitor_cadastro_job,
        )

        register_distribuidos_bb_monitor_cadastro_job(scheduler)
    except Exception:
        logger.exception(
            "Falha ao registrar o monitor de cadastro L1 Distribuídos BB no startup."
        )

    # Análise de Risco BB Réu: esteira que confere no portal do BB se a análise
    # foi realmente feita quando a tarefa é cumprida no L1 (sessão OneLog).
    try:
        from app.services.analise_risco.portal_verify_worker import (
            register_analise_risco_verify_job,
        )

        register_analise_risco_verify_job(scheduler)
    except Exception:
        logger.exception(
            "Falha ao registrar a esteira de verificação da Análise de Risco no startup."
        )

    # Ativos: a consulta ao DataJud acontece SÓ na ingestão (decisão do operador
    # 2026-07-17: sem worker recorrente depois do cadastro — o que o DataJud não
    # tiver na hora, fica com o dado da planilha e pronto).

    try:
        yield
    finally:
        batch_worker.stop()
        # Descarrega o que o relatório de utilização ainda tinha em memória.
        # Sem isto, todo redeploy perde o acumulado da última janela — e num
        # dia de vários deploys o relatório subcontaria justamente quem estava
        # trabalhando na hora.
        try:
            from app.services import uso_service

            gravadas = uso_service.descarregar()
            if gravadas:
                logger.info("uso: %s linha(s) gravadas no shutdown", gravadas)
        except Exception as exc:  # noqa: BLE001
            logger.warning("uso: flush de shutdown falhou (%s)", exc)
        scheduler.shutdown()
        logger.info("APScheduler stopped")


app = FastAPI(title="OneTask API", version="1.0.0", lifespan=lifespan)

origins = settings.cors_origins
allow_origin_regex = None
allow_credentials = True

if "*" in origins:
    origins = []
    allow_origin_regex = ".*"
    allow_credentials = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=allow_origin_regex,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

protected_dependencies = [Depends(auth_security.get_current_user)]

app.include_router(admin.router, prefix="/api/v1/admin", tags=["Admin"], dependencies=protected_dependencies)
app.include_router(cargos.router, prefix="/api/v1/admin", tags=["Admin"], dependencies=protected_dependencies)
app.include_router(uso.router, prefix="/api/v1/admin", tags=["Admin: Utilização"], dependencies=protected_dependencies)
app.include_router(cargos.me_router, prefix="/api/v1", tags=["User"], dependencies=protected_dependencies)
# admin_notices.router usa o prefixo /api/v1 cru porque algumas rotas
# (active/dismiss) sao acessiveis a qualquer JWT, e outras (CRUD) tem
# guard interno de role=admin. Manter sob /api/v1/admin/notices nao
# requer prefixo extra — o router ja' usa "/admin/notices/...".
app.include_router(admin_notices.router, prefix="/api/v1", tags=["Admin: Avisos"], dependencies=protected_dependencies)
# user_feedback expoe POST /feedback (qualquer JWT) + rotas /admin/feedback
# (guard interno de role=admin). Mesmo padrao de admin_notices —
# protected_dependencies cobre o JWT, o resto e' feito dentro do router.
app.include_router(user_feedback.router, prefix="/api/v1", tags=["Feedback"], dependencies=protected_dependencies)
app.include_router(capture_health.router, prefix="/api/v1/admin", tags=["Admin"], dependencies=protected_dependencies)
app.include_router(taxonomy_admin.router, prefix="/api/v1/admin", tags=["Admin: Taxonomia"], dependencies=protected_dependencies)
app.include_router(admin.me_router, prefix="/api/v1", tags=["User"], dependencies=protected_dependencies)
app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["Dashboard"], dependencies=protected_dependencies)
app.include_router(squads.router, prefix="/api/v1/squads", tags=["Squads"], dependencies=protected_dependencies)
app.include_router(tasks.router, prefix="/api/v1/tasks", tags=["Tasks"], dependencies=protected_dependencies)
# Router de automação externa (OneSid, OneRequest): autenticado por
# header X-Batch-Api-Key, SEM JWT. Separado pra não herdar o
# protected_dependencies do router de operador.
app.include_router(tasks.batch_router, prefix="/api/v1/tasks")
app.include_router(users.router, prefix="/api/v1/users", tags=["Users"], dependencies=protected_dependencies)
app.include_router(offices.router, prefix="/api/v1", tags=["Offices"], dependencies=protected_dependencies)
app.include_router(classifier.router, prefix="/api/v1/classifier", tags=["Classificador"], dependencies=protected_dependencies)
# Classificador (diagnostico de carteira) — modulo paralelo a Prazos Iniciais.
# Ver memory project_classificador.md. Fase 1 = esqueleto com endpoints stub.
app.include_router(classificador.router, prefix="/api/v1", tags=["Classificador - Diagnostico"], dependencies=protected_dependencies)
# Intake publico do Classificador (motor dormente) — auth via X-Classificador-Api-Key.
# Sem JWT. Robo de entrega POSTa aqui, worker dormente agrupa em batches de 50.
app.include_router(classificador.intake_router, prefix="/api/v1")
app.include_router(publications.router, prefix="/api/v1/publications", tags=["Publicações"], dependencies=protected_dependencies)
app.include_router(publication_treatment.router, prefix="/api/v1/publications", tags=["Publicações"], dependencies=protected_dependencies)
# Relatório Crítico de Performance (admin-only; gate dentro do endpoint via require_admin).
app.include_router(publications_performance.router, prefix="/api/v1/publications", tags=["Publicações"], dependencies=protected_dependencies)
# Citações BM — monitoramento de citação via DataJud (CNJ). Seção dentro de
# Tratamento de Publicações. JWT + permissão publications.
app.include_router(citacoes_bm.router, prefix="/api/v1/publications", tags=["Citações BM"], dependencies=protected_dependencies)
# OneNotify BB — notificações do portal do cliente conciliadas com publicações.
app.include_router(onenotify_bb.router, prefix="/api/v1", dependencies=protected_dependencies)
# Intake externo do OneNotify BB: auth via X-Onenotify-Api-Key, SEM JWT.
app.include_router(onenotify_bb.intake_router, prefix="/api/v1")
# Intake externo: autenticado por API key (header X-Intake-Api-Key), SEM JWT.
app.include_router(prazos_iniciais.intake_router, prefix="/api/v1")
# Intake do RPA de Análise de Risco BB Réu (servidor AWS): auth via header
# X-AnaliseRisco-Api-Key, SEM JWT. Entrega a fila e recebe os vereditos do portal.
app.include_router(analise_risco_intake.intake_router, prefix="/api/v1")
# Intake do RPA de Vínculos BB (mesmo servidor AWS, repo RPA_encerramentos
# --vinculos): auth via header X-VinculosBB-Api-Key, SEM JWT. Entrega a fila de
# partes e recebe os vínculos pesquisados no portal do BB.
app.include_router(distribuidos_bb_vinculos_intake.intake_router, prefix="/api/v1")
# Intake do OneRequest (motor RPA externo): auth via header
# X-Onerequest-Api-Key, SEM JWT. Recebe números/detalhes das DMIs do BB.
app.include_router(onerequest.intake_router, prefix="/api/v1")
# Intake do Sistema de Encerramentos: auth via header X-Encerramentos-Api-Key,
# SEM JWT. Encerra o processo no Legal One quando encerrado la.
app.include_router(encerramentos.intake_router, prefix="/api/v1")
# Menu "Encerramentos" (gestao, admin): rastro do que a integracao encerrou no L1.
app.include_router(encerramentos.router, prefix="/api/v1", tags=["Encerramentos"], dependencies=protected_dependencies)
# UI do operador OneRequest (tratamento + agendar): JWT + permissão onerequest.
app.include_router(
    onerequest.router, prefix="/api/v1", tags=["OneRequest"], dependencies=protected_dependencies
)
# Minha Equipe (Performance de Equipes): JWT + admin (checado no router). Monitora
# desempenho dos colaboradores a partir das tarefas do L1 (tabelas perf*).
app.include_router(
    performance.router, prefix="/api/v1", tags=["Performance"], dependencies=protected_dependencies
)
# Balanceador de Agenda (supervisor redistribui tarefas): reusa gate por time do Minha Equipe.
app.include_router(
    balanceador.router, prefix="/api/v1", tags=["Balanceador de Agenda"], dependencies=protected_dependencies
)
# Endpoints internos de prazos iniciais (UI do operador): JWT obrigatório.
app.include_router(prazos_iniciais.router, prefix="/api/v1", dependencies=protected_dependencies)
app.include_router(
    prazos_iniciais_legacy_tasks.router,
    prefix="/api/v1",
    dependencies=protected_dependencies,
)
app.include_router(
    prazos_iniciais_scheduling.router,
    prefix="/api/v1",
    dependencies=protected_dependencies,
)
# Análise Recursal (dentro de Prazos Processuais): JWT + permissão prazos_iniciais.
app.include_router(recursal.router, prefix="/api/v1", dependencies=protected_dependencies)
app.include_router(distribuidos_bb.router, prefix="/api/v1", tags=["Distribuídos BB"], dependencies=protected_dependencies)
app.include_router(task_templates.router, prefix="/api/v1/task-templates", tags=["Templates de Tarefa"], dependencies=protected_dependencies)
app.include_router(ajus.router, prefix="/api/v1", tags=["AJUS"], dependencies=protected_dependencies)
# GED LegalOne — envio em lote de arquivos pro GED (ECM) de processos do L1.
# JWT obrigatorio + permissao schedule_batch (guard interno por endpoint).
app.include_router(ged_legalone.router, prefix="/api/v1", tags=["GED LegalOne"], dependencies=protected_dependencies)
# Contatos LegalOne — enriquece contatos (telefone/e-mail/endereco) por CPF/CNPJ.
# JWT obrigatorio + permissao schedule_batch (guard interno por endpoint).
app.include_router(contatos_legalone.router, prefix="/api/v1", tags=["Contatos LegalOne"], dependencies=protected_dependencies)
app.include_router(automations.router, prefix="/api/v1/automations", tags=["Automações"], dependencies=protected_dependencies)
# Base Processual: upload diario da Listagem de Acoes do L1 + dashboard
# de movimentacao de carteira. JWT obrigatorio + guard interno admin-only.
app.include_router(base_processual.router, prefix="/api/v1", dependencies=protected_dependencies)
# Mesmo prefixo /admin/base-processual mas separado em arquivo proprio pra
# evitar inchaco do base_processual.py (que ja' tem ~1k linhas). Inclui
# /eventos (cross-upload) e /processos/bulk-update.
app.include_router(base_processual_bulk.router, prefix="/api/v1", dependencies=protected_dependencies)
# Backfill historico: POST /uploads/backfill aceita uploaded_at + mode
# (snapshot ou lote_historico) pra popular timeline de uploads passados.
app.include_router(base_processual_backfill.router, prefix="/api/v1", dependencies=protected_dependencies)
# Exports XLSX (Chunk 5): 6 templates de relatorio + historico paginado.
app.include_router(base_processual_exports.router, prefix="/api/v1", dependencies=protected_dependencies)
# API keys admin CRUD (Chunk 6) — JWT obrigatorio + role admin via require_admin.
app.include_router(base_processual_api_keys.router, prefix="/api/v1", dependencies=protected_dependencies)
# Conversao Listagem AJUS -> XLSX de migracao Legal One (POST /conversao-l1).
app.include_router(base_processual_conversao.router, prefix="/api/v1", dependencies=protected_dependencies)
# API publica (Chunk 6) — SEM JWT. Auth via header X-Base-Processual-Key.
# Cuidado: NAO adicionar protected_dependencies aqui — quebraria o uso externo.
app.include_router(base_processual_public.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Autenticacao"])
# Varredura de andamentos (modulo incidental — sem deploy em main).
# Roda local no docker: operador escolhe offices passivos e o RPA
# raspa DetailsAndamentos atras de eventos relevantes (audiencias,
# sentenca, revelia, etc.).
app.include_router(
    varredura.router,
    prefix="/api/v1",
    tags=["Varredura"],
    dependencies=protected_dependencies,
)


@app.get(
    "/api/v1/monitor/legal-one-position-fix/status",
    tags=["Monitor"],
    summary="Acompanhar correcao de posicao do cliente principal (autenticado)",
    dependencies=protected_dependencies,
)
def monitor_legal_one_position_fix_status():
    return tasks.get_legal_one_position_fix_status()


@app.post(
    "/api/v1/monitor/legal-one-position-fix/control",
    tags=["Monitor"],
    summary="Pausar ou retomar a correcao de posicao do cliente principal (autenticado)",
    dependencies=protected_dependencies,
)
def monitor_legal_one_position_fix_control(payload: tasks.LegalOnePositionFixControlRequest):
    return tasks.set_legal_one_position_fix_control(payload.action)


@app.get("/", tags=["Root"])
async def read_root():
    return {"message": "Bem-vindo a API OneTask"}


@app.get("/healthz", tags=["Health"])
async def healthcheck():
    return {
        "status": "ok",
        "batch_worker_enabled": settings.batch_worker_enabled,
    }
