"""Endpoints do módulo OneRequest.

Por enquanto (Fase 1) só o `intake_router`: ingresso dos dados que o MOTOR RPA
externo empurra. Autenticado por API key no header `X-Onerequest-Api-Key`
(env `ONEREQUEST_INTAKE_API_KEY`), SEM JWT — mesmo padrão do intake de Prazos
Iniciais e do Classificador. Registrado sem `protected_dependencies` em main.py.

A UI do operador (listagem/tratamento/agendar) e o acompanhamento de status no
L1 entram em fases seguintes — ver `docs/onerequest-integracao-plano.md`.
"""

import logging
from datetime import date
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_permission
from app.core.config import settings
from app.core.dependencies import get_api_client, get_db
from app.core.scheduler import get_scheduler
from app.models.legal_one import LegalOneUser
from app.services.legal_one_client import LegalOneApiClient
from app.services.onerequest import suggestions
from app.services.onerequest.intake_service import OnerequestIntakeService
from app.services.onerequest.service import OnerequestService

logger = logging.getLogger(__name__)

intake_router = APIRouter(prefix="/onerequest", tags=["OneRequest (Intake)"])


def _validate_intake_api_key(
    x_onerequest_api_key: Optional[str] = Header(
        default=None, alias="X-Onerequest-Api-Key"
    ),
) -> str:
    """
    Autentica o motor RPA externo por header `X-Onerequest-Api-Key`.

    Aceita múltiplas chaves em `ONEREQUEST_INTAKE_API_KEY` (separadas por
    vírgula) pra rotação sem downtime. Se nenhuma chave estiver configurada,
    o endpoint fica explicitamente bloqueado (503) — evita rota aberta em
    produção por esquecimento de config.
    """
    valid_keys = settings.onerequest_intake_api_keys
    if not valid_keys:
        logger.error("ONEREQUEST_INTAKE_API_KEY não configurada — intake rejeitado.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Endpoint de intake do OneRequest não configurado.",
        )
    if not x_onerequest_api_key or x_onerequest_api_key not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key inválida ou ausente no header X-Onerequest-Api-Key.",
        )
    return x_onerequest_api_key


# ── Schemas ────────────────────────────────────────────────────────────
class IntakeNumerosRequest(BaseModel):
    numeros: List[str] = Field(
        ...,
        description=(
            "Snapshot COMPLETO dos números de DMI abertos no portal do BB "
            "(ex.: '2026/0000000001'). O Flow faz o diff de novos/respondidos."
        ),
    )


class IntakeNumerosResponse(BaseModel):
    recebidos: int
    novos: int
    respondidos: int
    reabertos: int


class PendentesDetalheResponse(BaseModel):
    total: int
    numeros: List[str]


class IntakeDetalheItem(BaseModel):
    numero_solicitacao: str
    titulo: Optional[str] = None
    npj_direcionador: Optional[str] = None
    prazo: Optional[str] = None
    texto_dmi: Optional[str] = None
    numero_processo: Optional[str] = None
    polo: Optional[str] = None


class IntakeDetalhesRequest(BaseModel):
    itens: List[IntakeDetalheItem]


class IntakeDetalhesResponse(BaseModel):
    atualizados: int
    nao_encontrados: List[str]


# ── Endpoints ──────────────────────────────────────────────────────────
@intake_router.post(
    "/intake/numeros",
    response_model=IntakeNumerosResponse,
    summary="Sincroniza o snapshot de números de DMI abertos no portal do BB",
)
def intake_numeros(
    payload: IntakeNumerosRequest,
    db: Session = Depends(get_db),
    _: str = Depends(_validate_intake_api_key),
):
    service = OnerequestIntakeService(db)
    return service.sync_numeros(payload.numeros)


@intake_router.get(
    "/intake/pendentes-detalhe",
    response_model=PendentesDetalheResponse,
    summary="Lista os números abertos que ainda precisam de detalhamento",
)
def intake_pendentes_detalhe(
    db: Session = Depends(get_db),
    _: str = Depends(_validate_intake_api_key),
):
    service = OnerequestIntakeService(db)
    numeros = service.pendentes_detalhe()
    return PendentesDetalheResponse(total=len(numeros), numeros=numeros)


@intake_router.post(
    "/intake/detalhes",
    response_model=IntakeDetalhesResponse,
    summary="Atualiza os detalhes capturados das DMIs (título/NPJ/prazo/processo)",
)
def intake_detalhes(
    payload: IntakeDetalhesRequest,
    db: Session = Depends(get_db),
    _: str = Depends(_validate_intake_api_key),
):
    service = OnerequestIntakeService(db)
    return service.upsert_detalhes(payload.itens)


# ════════════════════════════════════════════════════════════════════════
# Router do OPERADOR (UI de tratamento) — JWT + permissão dedicada.
# Gateado por `onerequest` (default false → entra travado, só admin enxerga;
# admin libera por usuário na tela de Usuários & Permissões).
# Registrado COM protected_dependencies em main.py.
# ════════════════════════════════════════════════════════════════════════
router = APIRouter(prefix="/onerequest", tags=["OneRequest"])

_perm = Depends(require_permission("onerequest"))


class SolicitacaoOut(BaseModel):
    id: int
    numero_solicitacao: str
    titulo: Optional[str] = None
    npj_direcionador: Optional[str] = None
    prazo: Optional[str] = None
    texto_dmi: Optional[str] = None
    numero_processo: Optional[str] = None
    proc_utilizavel: bool = False
    polo: Optional[str] = None
    recebido_em: Optional[str] = None
    status_sistema: str
    status_tratamento: str
    desfecho: Optional[str] = None
    responsavel_user_id: Optional[int] = None
    responsavel_nome: Optional[str] = None
    setor: Optional[str] = None
    data_agendamento: Optional[str] = None
    anotacao: Optional[str] = None
    tem_anotacao: bool = False
    created_task_id: Optional[int] = None
    linked_lawsuit_id: Optional[int] = None
    last_error: Optional[str] = None
    scheduled_by_nome: Optional[str] = None
    scheduled_at: Optional[str] = None
    farol: str
    # Status no L1 (cacheado pelo botão "Atualizar status L1").
    l1_checked_at: Optional[str] = None
    l1_dmi_task_id: Optional[int] = None
    l1_dmi_status_id: Optional[int] = None
    l1_dmi_status_label: Optional[str] = None
    l1_dmi_respondida: bool = False
    l1_dmi_encontrada: bool = False
    l1_pendentes_count: Optional[int] = None
    l1_sem_pendencia: Optional[bool] = None
    l1_task_url: Optional[str] = None
    # Verificação proativa de existência do processo no L1.
    proc_l1_checado_em: Optional[str] = None
    proc_l1_encontrado: Optional[bool] = None
    proc_l1_via: Optional[str] = None


class ListResponse(BaseModel):
    total: int
    kpis: Dict[str, int]
    items: List[SolicitacaoOut]


class OptionsResponse(BaseModel):
    setores: List[str]


class UpdateTratamentoRequest(BaseModel):
    responsavel_user_id: Optional[int] = None
    setor: Optional[str] = None
    data_agendamento: Optional[str] = None
    anotacao: Optional[str] = None
    status_tratamento: Optional[str] = None


class UpdateResponse(BaseModel):
    ok: bool
    id: int
    status_tratamento: str


class AgendarResponse(BaseModel):
    ok: bool
    status_tratamento: str
    created_task_id: Optional[int] = None
    mensagem: str
    # Trava-duplo: quando True, há tarefa PENDENTE pra DMI; o front confirma e reenvia.
    requires_confirmation: bool = False
    tarefa_existente: Optional[Dict] = None


@router.get(
    "/solicitacoes",
    response_model=ListResponse,
    summary="Lista solicitações (DMIs) com farol/KPIs, paginada",
    dependencies=[_perm],
)
def listar_solicitacoes(
    status_sistema: Optional[str] = Query(None),
    status_tratamento: Optional[str] = Query(None),
    responsavel_user_id: Optional[int] = Query(None),
    setor: Optional[str] = Query(None),
    busca: Optional[str] = Query(None),
    farol: Optional[str] = Query(None, description="Filtra por farol: cinza|atrasado|vermelho|amarelo|roxo|verde"),
    sem_responsavel: Optional[bool] = Query(None, description="Apenas DMIs sem responsável (não distribuídas)"),
    sem_anotacao: Optional[bool] = Query(None, description="Apenas DMIs SEM anotação (ex.: atrasadas que ainda precisam de ação)"),
    sem_processo_l1: Optional[bool] = Query(None, description="Apenas DMIs cujo processo NÃO foi encontrado no Legal One"),
    concluidas: Optional[bool] = Query(None, description="Concluídas = BB respondeu (RESPONDIDO) ou operador encerrou sem providência (IGNORADO)"),
    disp_de: Optional[date] = Query(None, description="Disponibilização (recebido_em) a partir desta data (BRT)"),
    disp_ate: Optional[date] = Query(None, description="Disponibilização (recebido_em) até esta data (BRT)"),
    prazo_de: Optional[date] = Query(None, description="Prazo fatal a partir desta data"),
    prazo_ate: Optional[date] = Query(None, description="Prazo fatal até esta data"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    service = OnerequestService(db)
    return service.list_solicitacoes(
        status_sistema=status_sistema or None,
        status_tratamento=status_tratamento,
        responsavel_user_id=responsavel_user_id,
        setor=setor,
        busca=busca,
        farol=farol,
        sem_responsavel=sem_responsavel,
        sem_anotacao=sem_anotacao,
        sem_processo_l1=sem_processo_l1,
        concluidas=concluidas,
        disp_de=disp_de,
        disp_ate=disp_ate,
        prazo_de=prazo_de,
        prazo_ate=prazo_ate,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/solicitacoes/export",
    summary="Exporta as DMIs filtradas em Excel (xlsx), respeitando os mesmos filtros da listagem",
    dependencies=[_perm],
)
def exportar_solicitacoes(
    status_sistema: Optional[str] = Query(None),
    status_tratamento: Optional[str] = Query(None),
    responsavel_user_id: Optional[int] = Query(None),
    setor: Optional[str] = Query(None),
    busca: Optional[str] = Query(None),
    farol: Optional[str] = Query(None),
    sem_responsavel: Optional[bool] = Query(None),
    sem_anotacao: Optional[bool] = Query(None),
    sem_processo_l1: Optional[bool] = Query(None),
    concluidas: Optional[bool] = Query(None),
    disp_de: Optional[date] = Query(None),
    disp_ate: Optional[date] = Query(None),
    prazo_de: Optional[date] = Query(None),
    prazo_ate: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    from datetime import datetime as _dt

    from fastapi import Response

    service = OnerequestService(db)
    xlsx = service.export_xlsx(
        status_sistema=status_sistema or None,
        status_tratamento=status_tratamento,
        responsavel_user_id=responsavel_user_id,
        setor=setor,
        busca=busca,
        farol=farol,
        sem_responsavel=sem_responsavel,
        sem_anotacao=sem_anotacao,
        sem_processo_l1=sem_processo_l1,
        concluidas=concluidas,
        disp_de=disp_de,
        disp_ate=disp_ate,
        prazo_de=prazo_de,
        prazo_ate=prazo_ate,
    )
    fname = f"onerequest-dmis-{_dt.now().strftime('%Y%m%d-%H%M')}.xlsx"
    return Response(
        content=xlsx,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


class DashboardResponse(BaseModel):
    kpis: Dict[str, int]
    farol: Dict[str, int]
    recebimentos: List[Dict]
    agendamentos: List[Dict]
    por_responsavel: List[Dict]
    por_setor: List[Dict]
    periodo_dias: int


@router.get(
    "/dashboard",
    response_model=DashboardResponse,
    summary="Dashboard do OneRequest: KPIs, séries diárias e distribuições (operacional + risco)",
    dependencies=[_perm],
)
def dashboard(
    days: int = Query(30, ge=5, le=180),
    db: Session = Depends(get_db),
):
    return OnerequestService(db).dashboard_data(days=days)


@router.get(
    "/options",
    response_model=OptionsResponse,
    summary="Opções pro formulário (setores disponíveis)",
    dependencies=[_perm],
)
def opcoes():
    return OptionsResponse(setores=OnerequestService.setores_disponiveis())


class EstadoResponse(BaseModel):
    last_ingest_at: Optional[str] = None
    abertas: int
    # Farol GLOBAL das abertas (KPI cards estáticos, independem do filtro).
    kpis: Dict[str, int] = {}


@router.get(
    "/estado",
    response_model=EstadoResponse,
    summary="Estado do módulo: data da última ingestão + total de abertas",
    dependencies=[_perm],
)
def estado(db: Session = Depends(get_db)):
    return OnerequestService(db).estado()


@router.patch(
    "/solicitacoes/{solicitacao_id}",
    response_model=UpdateResponse,
    summary="Atualiza o tratamento de uma solicitação",
    dependencies=[_perm],
)
def atualizar_tratamento(
    solicitacao_id: int,
    payload: UpdateTratamentoRequest,
    db: Session = Depends(get_db),
):
    service = OnerequestService(db)
    row = service.get(solicitacao_id)
    if not row:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada.")
    row = service.update_tratamento(row, payload.model_dump(exclude_unset=True))
    return UpdateResponse(ok=True, id=row.id, status_tratamento=row.status_tratamento)


@router.post(
    "/solicitacoes/{solicitacao_id}/agendar",
    response_model=AgendarResponse,
    summary="Cria a tarefa no Legal One para a solicitação tratada",
    dependencies=[_perm],
)
def agendar_solicitacao(
    solicitacao_id: int,
    confirmar: bool = Query(False, description="Criar mesmo havendo tarefa pendente pra DMI (trava-duplo)"),
    db: Session = Depends(get_db),
    client: LegalOneApiClient = Depends(get_api_client),
    current_user: LegalOneUser = Depends(get_current_user),
):
    service = OnerequestService(db)
    row = service.get(solicitacao_id)
    if not row:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada.")
    return service.agendar(row, client, current_user, confirmar=confirmar)


# ── Sugestão (motor de pré-preenchimento) ──────────────────────────────
class SugestaoResponse(BaseModel):
    setor: Optional[str] = None
    setor_confianca: Optional[str] = None
    responsavel_user_id: Optional[int] = None
    responsavel_nome: Optional[str] = None
    responsavel_confianca: Optional[int] = None
    data_agendamento: Optional[str] = None


@router.get(
    "/solicitacoes/{solicitacao_id}/sugestao",
    response_model=SugestaoResponse,
    summary="Sugestão de setor/responsável/data (motor parametrizado)",
    dependencies=[_perm],
)
def sugestao(solicitacao_id: int, db: Session = Depends(get_db)):
    service = OnerequestService(db)
    row = service.get(solicitacao_id)
    if not row:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada.")
    return suggestions.sugerir(db, titulo=row.titulo, polo=row.polo, prazo=row.prazo)


# ── Tarefas na pasta (Legal One, sob demanda) ──────────────────────────
class L1TaskItem(BaseModel):
    task_id: Optional[int] = None
    description: Optional[str] = None
    status_id: Optional[int] = None
    status_label: Optional[str] = None
    end_date_time: Optional[str] = None
    l1_url: Optional[str] = None


class L1TarefasResponse(BaseModel):
    lawsuit_id: Optional[int] = None
    l1_url: Optional[str] = None
    pendentes: List[L1TaskItem]
    concluidas: List[L1TaskItem]
    resolvido: bool
    check_failed: bool


@router.get(
    "/solicitacoes/{solicitacao_id}/l1-tarefas",
    response_model=L1TarefasResponse,
    summary="Tarefas pendentes/concluídas na pasta do processo no Legal One",
    dependencies=[_perm],
)
def l1_tarefas(
    solicitacao_id: int,
    db: Session = Depends(get_db),
    client: LegalOneApiClient = Depends(get_api_client),
):
    service = OnerequestService(db)
    row = service.get(solicitacao_id)
    if not row:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada.")
    return service.tarefas_na_pasta(row, client)


# ── Verificação proativa: o processo existe no L1? (sob demanda) ────────
class VerificarProcessoResponse(BaseModel):
    encontrado: bool
    via: Optional[str] = None
    lawsuit_id: Optional[int] = None


@router.post(
    "/solicitacoes/{solicitacao_id}/verificar-processo-l1",
    response_model=VerificarProcessoResponse,
    summary="Resolve o processo no L1 (CNJ->NPJ) sem criar tarefa — só sinaliza se a pasta existe",
    dependencies=[_perm],
)
def verificar_processo_l1(
    solicitacao_id: int,
    db: Session = Depends(get_db),
    client: LegalOneApiClient = Depends(get_api_client),
):
    service = OnerequestService(db)
    row = service.get(solicitacao_id)
    if not row:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada.")
    return service.verificar_processo_l1(row, client)


# ── Status no Legal One (sob demanda) ──────────────────────────────────
class StatusL1Response(BaseModel):
    checked_at: Optional[str] = None
    resolvido: bool = False
    lawsuit_id: Optional[int] = None
    l1_url: Optional[str] = None
    # Sinal A: a tarefa da DMI (match por número da solicitação).
    dmi_task_id: Optional[int] = None
    dmi_task_url: Optional[str] = None
    dmi_status_id: Optional[int] = None
    dmi_status_label: Optional[str] = None
    dmi_respondida: bool = False
    dmi_encontrada: bool = False
    # Sinal B: pendências na pasta.
    pendentes_count: Optional[int] = None
    sem_pendencia: Optional[bool] = None


@router.post(
    "/solicitacoes/{solicitacao_id}/status-l1",
    response_model=StatusL1Response,
    summary="Checa no Legal One se a tarefa da DMI foi respondida (Cumprida) e se a pasta tem pendência",
    dependencies=[_perm],
)
def status_l1(
    solicitacao_id: int,
    db: Session = Depends(get_db),
    client: LegalOneApiClient = Depends(get_api_client),
):
    service = OnerequestService(db)
    row = service.get(solicitacao_id)
    if not row:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada.")
    return service.verificar_status_l1(row, client)


# ── Auto-refresh horário do status L1 (DMIs que vencem hoje) ───────────
class L1AutorefreshState(BaseModel):
    enabled: bool
    last_run_at: Optional[str] = None
    last_count: Optional[int] = None
    intervalo: str = "1h"
    alvo: str = "DMIs que vencem hoje"


class L1AutorefreshToggle(BaseModel):
    enabled: bool


def _l1_autorefresh_state() -> "L1AutorefreshState":
    from app.services.app_settings import get_setting
    from app.services.onerequest.l1_autorefresh_worker import (
        SETTING_LAST_COUNT,
        SETTING_LAST_RUN,
        is_enabled,
    )

    lc = get_setting(SETTING_LAST_COUNT)
    try:
        last_count = int(lc) if lc is not None else None
    except (TypeError, ValueError):
        last_count = None
    return L1AutorefreshState(
        enabled=is_enabled(),
        last_run_at=get_setting(SETTING_LAST_RUN),
        last_count=last_count,
    )


@router.get(
    "/l1-autorefresh",
    response_model=L1AutorefreshState,
    summary="Estado da regra de auto-atualização horária do status L1 (vence hoje)",
    dependencies=[_perm],
)
def l1_autorefresh_estado():
    return _l1_autorefresh_state()


@router.post(
    "/l1-autorefresh",
    response_model=L1AutorefreshState,
    summary="Liga/desliga (play/stop) a auto-atualização horária; ao ligar, já dispara uma execução",
    dependencies=[_perm],
)
def l1_autorefresh_toggle(
    payload: L1AutorefreshToggle,
    scheduler=Depends(get_scheduler),
):
    from datetime import datetime

    from app.services.app_settings import set_setting
    from app.services.onerequest.l1_autorefresh_worker import JOB_ID, SETTING_ENABLED

    set_setting(SETTING_ENABLED, "true" if payload.enabled else "false")
    # Ao LIGAR, não espera a próxima hora: adianta a próxima execução pra agora.
    if payload.enabled:
        try:
            job = scheduler.get_job(JOB_ID)
            if job is not None:
                job.modify(next_run_time=datetime.now())
        except Exception:
            logger.warning(
                "OneRequest: não consegui adiantar o job de auto-refresh L1.",
                exc_info=True,
            )
    return _l1_autorefresh_state()


# ── Anotações (log de auditoria) ───────────────────────────────────────
class AnotacaoItem(BaseModel):
    id: int
    texto: str
    autor_nome: Optional[str] = None
    created_at: Optional[str] = None


class AnotacaoCreate(BaseModel):
    texto: str


@router.get(
    "/solicitacoes/{solicitacao_id}/anotacoes",
    response_model=List[AnotacaoItem],
    summary="Histórico de anotações da DMI",
    dependencies=[_perm],
)
def listar_anotacoes(solicitacao_id: int, db: Session = Depends(get_db)):
    return OnerequestService(db).list_anotacoes(solicitacao_id)


@router.post(
    "/solicitacoes/{solicitacao_id}/anotacoes",
    response_model=AnotacaoItem,
    status_code=201,
    summary="Adiciona uma anotação (log de auditoria) à DMI",
    dependencies=[_perm],
)
def criar_anotacao(
    solicitacao_id: int,
    payload: AnotacaoCreate,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(get_current_user),
):
    service = OnerequestService(db)
    if not service.get(solicitacao_id):
        raise HTTPException(status_code=404, detail="Solicitação não encontrada.")
    if not (payload.texto or "").strip():
        raise HTTPException(status_code=400, detail="Anotação vazia.")
    return service.add_anotacao(solicitacao_id, payload.texto, current_user)


# ── Auditoria total (consulta por CNJ ou nº da DMI) ────────────────────
class AuditTarefaL1(BaseModel):
    task_id: Optional[int] = None
    description: Optional[str] = None
    status_id: Optional[int] = None
    status_label: Optional[str] = None
    start_date_time: Optional[str] = None
    end_date_time: Optional[str] = None
    l1_url: Optional[str] = None
    lawsuit_url: Optional[str] = None


class AuditAgendamento(BaseModel):
    agendado: bool = False
    scheduled_by_nome: Optional[str] = None
    scheduled_by_email: Optional[str] = None
    scheduled_at: Optional[str] = None
    responsavel_nome: Optional[str] = None
    setor: Optional[str] = None
    data_agendamento: Optional[str] = None
    prazo_bb: Optional[str] = None
    created_task_id: Optional[int] = None
    status_sistema: Optional[str] = None
    status_tratamento: Optional[str] = None
    last_error: Optional[str] = None


class AuditoriaResponse(BaseModel):
    id: int
    numero_solicitacao: str
    numero_processo: Optional[str] = None
    npj_direcionador: Optional[str] = None
    titulo: Optional[str] = None
    agendamento: AuditAgendamento
    tarefa_l1: Optional[AuditTarefaL1] = None
    anotacoes: List[AnotacaoItem]


@router.get(
    "/solicitacoes/{solicitacao_id}/auditoria",
    response_model=AuditoriaResponse,
    summary="Auditoria total da DMI: quem agendou, o que, pra quem + tarefa viva no L1 + anotações",
    dependencies=[_perm],
)
def auditoria(
    solicitacao_id: int,
    db: Session = Depends(get_db),
    client: LegalOneApiClient = Depends(get_api_client),
):
    service = OnerequestService(db)
    row = service.get(solicitacao_id)
    if not row:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada.")
    return service.auditoria(row, client)


# ── Alertas "vence hoje" (agrupados por responsável, texto pronto) ─────
class AlertaResponsavel(BaseModel):
    responsavel_user_id: Optional[int] = None
    responsavel_nome: str
    responsavel_email: Optional[str] = None
    teams_disponivel: bool = False
    count: int
    mensagem: str


@router.get(
    "/alertas/vence-hoje",
    response_model=List[AlertaResponsavel],
    summary="Mensagens de alerta (uma por responsável) das DMIs que vencem hoje",
    dependencies=[_perm],
)
def alertas_vence_hoje(db: Session = Depends(get_db)):
    return OnerequestService(db).alertas_vence_hoje()


class EnviarTeamsRequest(BaseModel):
    responsavel_user_id: int
    # Token delegado do Graph (MSAL no front), pra mandar a DM no nome da operadora.
    graph_token: str


class EnviarTeamsResponse(BaseModel):
    ok: bool
    mensagem: str


@router.post(
    "/alertas/enviar-teams",
    response_model=EnviarTeamsResponse,
    summary="Envia o alerta 'vence hoje' do responsável via Teams (Microsoft Graph, no nome da operadora)",
    dependencies=[_perm],
)
def enviar_alerta_teams(payload: EnviarTeamsRequest, db: Session = Depends(get_db)):
    return OnerequestService(db).enviar_alerta_teams(
        payload.responsavel_user_id, payload.graph_token
    )
