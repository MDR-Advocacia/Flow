from contextlib import asynccontextmanager
import logging

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
    ged_legalone,
    offices,
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
)
from app.core import auth as auth_security
from app.core.config import settings
from app.core.scheduler import scheduler
from app.services.batch_worker import BatchExecutionWorker

logger = logging.getLogger(__name__)
batch_worker = BatchExecutionWorker()


@asynccontextmanager
async def lifespan(_: FastAPI):
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
        from datetime import datetime, timezone

        from app.db.session import SessionLocal
        from app.models.scheduled_automation import ScheduledAutomation, ScheduledAutomationRun

        db = SessionLocal()
        try:
            orphans = (
                db.query(ScheduledAutomationRun)
                .filter(ScheduledAutomationRun.status == "running")
                .all()
            )
            for run in orphans:
                run.status = "failed"
                run.error_message = "API reiniciou durante a execução - polling do batch interrompido."
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

    # Minha Equipe: ingestão diária via download do relatório "Agenda Analytics"
    # do L1 (9h-12h30 BRT, 30/30min até o relatório do dia aparecer).
    try:
        from app.services.performance.ingest_worker import register_perf_ingest_job

        register_perf_ingest_job(scheduler)
    except Exception:
        logger.exception("Falha ao registrar job de ingestão do Minha Equipe no startup.")

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

    try:
        yield
    finally:
        batch_worker.stop()
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
# Intake externo: autenticado por API key (header X-Intake-Api-Key), SEM JWT.
app.include_router(prazos_iniciais.intake_router, prefix="/api/v1")
# Intake do OneRequest (motor RPA externo): auth via header
# X-Onerequest-Api-Key, SEM JWT. Recebe números/detalhes das DMIs do BB.
app.include_router(onerequest.intake_router, prefix="/api/v1")
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
