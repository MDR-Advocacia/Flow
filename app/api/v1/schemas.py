# app/api/v1/schemas.py

from pydantic import BaseModel, ConfigDict, computed_field, Field, field_validator
from typing import List, Optional, Dict, Any, Generic, TypeVar
from datetime import datetime
from app.core.utils import format_cnj

# --- Schemas para a API Externa (Legal One) ---

class Relationship(BaseModel):
    id: int
    type: str

class ResponsibleUser(BaseModel):
    id: int
    name: str

class LegalOneTaskPayload(BaseModel):
    description: str
    case_id: int
    task_type_id: int
    deadline: str  # Formato: "YYYY-MM-DD"
    relationships: List[Relationship]
    responsibles: List[ResponsibleUser]

# --- Schema para o Gatilho de Tarefas (Interno) ---

class TaskTriggerPayload(BaseModel):
    task_type_id: int
    squad_ids: Optional[List[int]] = None
    squad_member_ids: Optional[List[int]] = None
    task_details: Dict[str, Any]

# --- Schema para a Criação de Tarefas a partir da UI ---
# Mantido para compatibilidade com o endpoint existente em tasks.py

class TaskCreationRequest(BaseModel):
    template_id: int
    squad_ids: List[int]
    process_numbers: List[str]
    due_date: str
    priority: str
    custom_fields: Dict[str, str]

# --- Schemas para Office (rep simples; full e' em outro endpoint) ---

class OfficeRef(BaseModel):
    """Referencia de escritorio responsavel exibida nas squads."""
    external_id: int
    name: str
    path: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


# --- Schemas para Usuários e Squads ---

class UserSquadInfo(BaseModel):
    """Schema simples para representar o squad de um usuário."""
    id: int
    name: str
    model_config = ConfigDict(from_attributes=True)


class UserWithSquads(BaseModel):
    """
    Schema para um usuário do Legal One com a lista de squads associados.
    Usado para popular seletores no frontend.
    """
    id: int
    external_id: Optional[int] = None
    name: str

    @computed_field
    def squads(self) -> List[UserSquadInfo]:
        """
        Calcula a lista de squads únicos aos quais o usuário pertence.
        A propriedade 'squad_members' é carregada via joinedload na query da API.
        """
        if not hasattr(self, 'squad_members') or not self.squad_members:
            return []

        squad_dict = {member.squad.id: member.squad for member in self.squad_members if member.squad}
        sorted_squads = sorted(squad_dict.values(), key=lambda s: s.name)

        return [UserSquadInfo.from_orm(s) for s in sorted_squads]

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)


class LegalOneUser(BaseModel):
    """
    Representa um usuário do Legal One, conforme retornado pela nossa API.
    external_id é opcional: usuários provisionados via SSO (Entra) ainda
    não têm vínculo com o Legal One no primeiro acesso.
    """
    id: int
    external_id: Optional[int] = None
    name: str
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class UserMe(BaseModel):
    """Resposta do GET /me. Diferente de LegalOneUser, inclui papel + permissões
    para que o FRONTEND use o banco como fonte de verdade (e não o JWT, que é um
    snapshot de até 24h). Sem isto, liberar/revogar acesso no admin só "pega"
    quando o token expira — e o usuário fica preso na tela de espera nesse meio
    tempo, mesmo já liberado."""
    id: int
    external_id: Optional[int] = None
    name: str
    email: str
    is_active: bool
    role: Optional[str] = None
    can_schedule_batch: bool = False
    can_use_publications: bool = False
    can_use_prazos_iniciais: bool = False
    can_use_onerequest: bool = False
    can_manage_distribuidos_bb: bool = False
    must_change_password: bool = False
    model_config = ConfigDict(from_attributes=True)


class SquadMember(BaseModel):
    """
    Representa a associação de um usuário a um squad, aninhando os detalhes do usuário.
    """
    id: int
    is_leader: bool
    is_assistant: bool = False
    user: LegalOneUser
    model_config = ConfigDict(from_attributes=True)

class Squad(BaseModel):
    """
    Schema de resposta para um Squad, incluindo escritorio responsavel e membros.
    """
    id: int
    name: str
    is_active: bool
    kind: str = "principal"  # 'principal' | 'support'
    office_external_id: Optional[int] = None
    office: Optional[OfficeRef] = None
    members: List[SquadMember] = []
    model_config = ConfigDict(from_attributes=True)


# --- Schemas para Gerenciamento de Squads (Admin) ---

class SquadMemberSchema(BaseModel):
    """Schema para definir um membro ao criar/atualizar um squad."""
    user_id: int
    is_leader: bool = False
    is_assistant: bool = False


class SquadMemberRoleUpdate(BaseModel):
    """Schema pra PATCH em squad_members (toggle leader/assistant).

    Validacao no servico: max 1 leader e 1 assistant por squad. Quando
    o frontend marca outro membro, o servico desmarca o anterior antes.
    """
    is_leader: Optional[bool] = None
    is_assistant: Optional[bool] = None


class SquadMemberAddRequest(BaseModel):
    """Schema pra adicionar um user a uma squad."""
    user_id: int
    is_leader: bool = False
    is_assistant: bool = False


class AssistantResolution(BaseModel):
    """Resposta do GET /squads/assistant-of/{user_external_id}."""
    user_external_id: int
    squad_id: Optional[int] = None
    squad_name: Optional[str] = None
    fallback_reason: Optional[str] = None

class SquadCreateSchema(BaseModel):
    name: str
    office_external_id: int
    kind: str = Field(default="principal", pattern="^(principal|support)$")
    members: List[SquadMemberSchema] = []

class SquadUpdateSchema(BaseModel):
    name: Optional[str] = None
    office_external_id: Optional[int] = None
    kind: Optional[str] = Field(default=None, pattern="^(principal|support)$")
    members: Optional[List[SquadMemberSchema]] = None

# --- Schema para Task Templates ---

class TaskTemplate(BaseModel):
    id: int
    name: str
    description: Optional[str]
    estimated_time: Optional[str]
    fields: List[str]
    model_config = ConfigDict(from_attributes=True)


class ProcessoResponsavel(BaseModel):
    numero_processo: str
    id_responsavel: int
    observacao: Optional[str] = None
    data_agendamento: Optional[str] = None
    setor: Optional[str] = None
    id: Optional[int] = None
    numero_solicitacao: Optional[str] = None
    titulo: Optional[str] = None
    npj_direcionador: Optional[str] = None
    prazo: Optional[str] = None  # <--- CORREÇÃO PRINCIPAL
    texto_dmi: Optional[str] = None
    polo: Optional[str] = None
    recebido_em: Optional[str] = None
    anotacao: Optional[str] = None
    status: Optional[str] = None
    status_sistema: Optional[str] = None

    @field_validator("numero_processo")
    @classmethod
    def format_process_number_to_cnj(cls, v: str) -> str:
        """
        Garante que o número do processo esteja sempre no formato CNJ.
        """
        if v: # Adiciona verificação para não falhar em None
            return format_cnj(v)
        return v
    
    model_config = ConfigDict(extra="allow")


class BatchTaskCreationRequest(BaseModel):
    fonte: str
    processos: List[ProcessoResponsavel]
    file_content: Optional[bytes] = None # <-- ADICIONAR ESTA LINHA
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
# --- Schemas para o Dashboard de Lotes ---

class BatchExecutionItemResponse(BaseModel):
    id: int
    process_number: str
    status: str
    created_task_id: Optional[int] = None
    error_message: Optional[str] = None
    fingerprint: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)

class BatchExecutionResponse(BaseModel):
    id: int
    source: str
    source_filename: Optional[str] = None
    requested_by_email: Optional[str] = None
    status: str
    start_time: datetime
    end_time: Optional[datetime] = None
    total_items: int
    success_count: int
    failure_count: int
    items: List[BatchExecutionItemResponse] = []
    
    model_config = ConfigDict(from_attributes=True)

# --- ADIÇÃO: Schemas para Autenticação ---

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

# Schemas para o endpoint de dados de criação de tarefa
class SubTypeSchema(BaseModel):
    id: int
    name: str

class HierarchicalTaskTypeSchema(BaseModel):
    id: int
    name: str
    sub_types: List[SubTypeSchema] = []

class UserSchema(BaseModel):
    id: int
    name: str
    external_id: Optional[int] = None

class TaskStatusSchema(BaseModel):
    id: int
    name: str

class TaskCreationDataResponse(BaseModel):
    task_types: List[HierarchicalTaskTypeSchema]
    users: List[UserSchema]
    task_statuses: List[TaskStatusSchema]

# Schemas para o endpoint de criação interativa
class InteractiveTaskPayload(BaseModel):
    cnj_number: str
    task_type_id: int
    sub_type_id: int
    responsible_external_id: int
    description: str
    due_date: str
    due_time: Optional[str] = None

class BatchInteractiveCreationRequest(BaseModel):
    tasks: List[InteractiveTaskPayload]
    source_filename: str

class TaskForRuleValidation(BaseModel):
    """
    Representa uma única tarefa simplificada, contendo apenas
    a informação necessária para a validação de regras.
    """
    selected_subtype_id: str = Field(..., alias='selectedSubTypeId') # Recebe o camelCase do frontend

class ValidatePublicationTasksRequest(BaseModel):
    """
    O payload que o frontend enviará, contendo a lista de
    tarefas de uma única publicação para serem validadas.
    """
    tasks: List[TaskForRuleValidation]

T = TypeVar('T')

class PaginatedResponse(BaseModel, Generic[T]):
    """
    Schema genérico para uma resposta paginada.
    """
    total_items: int
    total_pages: int
    page: int
    items_per_page: int
    items: List[T]

    model_config = ConfigDict(from_attributes=True)


# --- Schemas para Base Processual (Chunk 1: uploads + eventos) ---

class BaseProcessualUploadOut(BaseModel):
    """Detalhe de um upload (linha de base_processual_upload)."""
    id: int
    filename: str
    file_sha256: str
    file_bytes: Optional[int] = None
    total_rows_in_file: Optional[int] = None
    summary_novos: int
    summary_removidos: int
    summary_atualizados: int
    summary_inalterados: int
    status: str
    error_message: Optional[str] = None
    eventos_preview_json: Optional[List[Dict[str, Any]]] = None
    dry_run_of_upload_id: Optional[int] = None
    storage_path: Optional[str] = None
    uploaded_by_user_id: Optional[int] = None
    uploaded_at: datetime
    processed_at: Optional[datetime] = None
    committed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class BaseProcessualUploadListResponse(BaseModel):
    """Lista paginada de uploads (formato padrao da casa: {total, items})."""
    total: int
    items: List[BaseProcessualUploadOut]

    model_config = ConfigDict(from_attributes=True)


class BaseProcessualUploadResult(BaseModel):
    """Resposta dos endpoints POST /uploads e POST /uploads/{id}/commit.

    Em dry-run, `eventos_preview` traz uma lista compacta de mudancas
    previstas (cap=200).
    """
    upload_id: int
    status: str
    summary_novos: int = 0
    summary_removidos: int = 0
    summary_atualizados: int = 0
    summary_inalterados: int = 0
    error_message: Optional[str] = None
    is_idempotente: bool = False
    eventos_preview: Optional[List[Dict[str, Any]]] = None

    model_config = ConfigDict(from_attributes=True)


class BaseProcessualEventoOut(BaseModel):
    """Evento atomico (ENTROU/SAIU/ATUALIZADO/ATUALIZADO_MANUAL) de um upload."""
    id: int
    upload_id: int
    processo_id: int
    cod_ajus: str
    tipo_evento: str
    changed_fields: Optional[Dict[str, Any]] = None
    snapshot_before_id: Optional[int] = None
    snapshot_after_id: Optional[int] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BaseProcessualEventoListResponse(BaseModel):
    total: int
    items: List[BaseProcessualEventoOut]

    model_config = ConfigDict(from_attributes=True)


# --- Schemas Dashboard (Chunk 2) ---

class BaseProcessualTopResponsavelItem(BaseModel):
    """Linha do ranking 'Top responsaveis' do dashboard."""
    usuario_responsavel: Optional[str]
    total: int


class BaseProcessualUfItem(BaseModel):
    """Linha da distribuicao UF."""
    uf: Optional[str]
    total: int


class BaseProcessualResumoOut(BaseModel):
    """KPIs do dashboard de Base Processual.

    'Hoje' = data UTC corrente. Operador no Brasil (UTC-3) vai ver o
    'hoje' do servidor; refinamento de timezone fica pra fase 2 se importar.
    """
    total_ativos_na_base: int
    total_removidos_na_base: int
    novos_hoje: int
    saidos_hoje: int
    atualizados_hoje: int
    ultimo_upload_id: Optional[int] = None
    ultimo_upload_em: Optional[datetime] = None
    ultimo_upload_status: Optional[str] = None
    ultimo_upload_filename: Optional[str] = None
    top_responsaveis: List[BaseProcessualTopResponsavelItem] = Field(default_factory=list)
    distribuicao_uf: List[BaseProcessualUfItem] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class BaseProcessualSerieDiariaItem(BaseModel):
    """Um ponto da serie temporal (1 por dia)."""
    data: datetime
    novos: int = 0
    removidos: int = 0
    atualizados: int = 0


class BaseProcessualSerieDiariaResponse(BaseModel):
    """Range temporal de movimentacao do dashboard (default = ultimos 90d)."""
    from_date: datetime
    to_date: datetime
    items: List[BaseProcessualSerieDiariaItem]


class BaseProcessualMovimentacaoItem(BaseModel):
    """Linha de movimentacao do dia (ENTROU / SAIU / ATUALIZADO)."""
    evento_id: int
    cod_ajus: str
    numero_processo_mascarado: Optional[str] = None
    empresa: Optional[str] = None
    uf: Optional[str] = None
    comarca: Optional[str] = None
    usuario_responsavel: Optional[str] = None
    distribuido_em: Optional[datetime] = None
    # Para SAIU: ultimo dia em que esteve ATIVO. Para ENTROU/ATUALIZADO: criacao do evento.
    visto_em: Optional[datetime] = None
    # Para ATUALIZADO: dict {campo: {de, para}}.
    changed_fields: Optional[Dict[str, Any]] = None


class BaseProcessualMovimentacaoDoDiaResponse(BaseModel):
    """Listas detalhadas dos 3 tipos de movimentacao em uma data especifica."""
    data: datetime
    entraram_total: int
    sairam_total: int
    atualizados_total: int
    entraram: List[BaseProcessualMovimentacaoItem]
    sairam: List[BaseProcessualMovimentacaoItem]
    atualizados: List[BaseProcessualMovimentacaoItem]


class BaseProcessualInatividadeOut(BaseModel):
    """Tempo desde o ultimo upload CONCLUIDO + flag de alerta."""
    ultimo_upload_em: Optional[datetime] = None
    horas_desde_ultimo: Optional[float] = None
    alerta: bool = False
    threshold_horas: int = 24


# --- Schemas Processos (Chunk 3) ---

class BaseProcessualProcessoOut(BaseModel):
    """Estado atual do processo na base. Linha de base_processual_processo."""
    id: int
    cod_ajus: str
    numero_processo: Optional[str] = None
    numero_processo_mascarado: Optional[str] = None
    numero_interno: Optional[str] = None
    numero_pasta: Optional[str] = None
    acao_principal: Optional[str] = None
    materia: Optional[str] = None
    risco_prob_perda: Optional[str] = None
    tipo_acao: Optional[str] = None
    polo: Optional[str] = None
    natureza: Optional[str] = None
    numero_vara: Optional[str] = None
    foro: Optional[str] = None
    comarca: Optional[str] = None
    uf: Optional[str] = None
    empresa: str
    grupo_responsavel: Optional[str] = None
    usuario_responsavel: Optional[str] = None
    escritorio_responsavel: Optional[str] = None
    situacao_processo: str
    justica_honorario: Optional[str] = None
    valor_causa: Optional[float] = None
    valor_prev_acordo: Optional[float] = None
    valor_acordo: Optional[float] = None
    valor_discutido: Optional[float] = None
    valor_exito: Optional[float] = None
    valor_condenacao: Optional[float] = None
    valor_contingencia: Optional[float] = None
    ult_andamento: Optional[str] = None
    data_ult_andamento: Optional[datetime] = None
    dias_ult_atualizacao: Optional[int] = None
    distribuido_em: Optional[datetime] = None
    processo_virtual: Optional[bool] = None
    numero_contrato: Optional[str] = None
    usuario_cadastro_acao: Optional[str] = None
    data_cadastro_acao: Optional[datetime] = None
    autores_json: Optional[List[Dict[str, Any]]] = None
    reus_json: Optional[List[Dict[str, Any]]] = None
    presenca_status: str
    first_seen_upload_id: Optional[int] = None
    last_seen_upload_id: Optional[int] = None
    removed_at_upload_id: Optional[int] = None
    current_snapshot_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BaseProcessualProcessoListResponse(BaseModel):
    total: int
    items: List[BaseProcessualProcessoOut]

    model_config = ConfigDict(from_attributes=True)


class BaseProcessualSnapshotOut(BaseModel):
    """Snapshot historico de um processo num upload especifico."""
    id: int
    upload_id: int
    cod_ajus: str
    diff_hash: str
    captured_at: datetime
    payload_normalized: Dict[str, Any]

    model_config = ConfigDict(from_attributes=True)


class BaseProcessualSnapshotListResponse(BaseModel):
    total: int
    items: List[BaseProcessualSnapshotOut]

    model_config = ConfigDict(from_attributes=True)


class BaseProcessualProcessoPatch(BaseModel):
    """Override manual de um processo. Apenas campos editaveis pelo operador.

    Gera evento ATUALIZADO_MANUAL com changed_fields {campo: {de, para}}.
    """
    situacao_processo: Optional[str] = None
    usuario_responsavel: Optional[str] = None
    grupo_responsavel: Optional[str] = None
    escritorio_responsavel: Optional[str] = None
    polo: Optional[str] = None
    materia: Optional[str] = None
    risco_prob_perda: Optional[str] = None
    # razao do override (audit) — nao aplicada como campo, fica no upload.error_message
    motivo: Optional[str] = None


# --- Schemas Bulk Update (Chunk 4) ---

class BaseProcessualBulkUpdateFilters(BaseModel):
    """Filtros usados pra selecionar quais processos receberao o bulk update.

    Mesmos campos do GET /processos — UI envia o filtro corrente e o servidor
    aplica nas mesmas condicoes pra evitar surpresa.
    """
    presenca_status: Optional[str] = None
    cod_ajus_list: Optional[List[str]] = None
    empresa: Optional[str] = None
    uf: Optional[str] = None
    comarca: Optional[str] = None
    situacao_processo: Optional[str] = None
    polo: Optional[str] = None
    materia: Optional[str] = None
    natureza: Optional[str] = None
    tipo_acao: Optional[str] = None
    risco_prob_perda: Optional[str] = None
    usuario_responsavel: Optional[str] = None
    grupo_responsavel: Optional[str] = None
    escritorio_responsavel: Optional[str] = None
    valor_causa_min: Optional[float] = None
    valor_causa_max: Optional[float] = None
    distribuido_de: Optional[datetime] = None
    distribuido_ate: Optional[datetime] = None
    search: Optional[str] = None


class BaseProcessualBulkUpdateSet(BaseModel):
    """Campos editaveis em bulk — mesmo subset do PATCH individual."""
    situacao_processo: Optional[str] = None
    usuario_responsavel: Optional[str] = None
    grupo_responsavel: Optional[str] = None
    escritorio_responsavel: Optional[str] = None
    polo: Optional[str] = None
    materia: Optional[str] = None
    risco_prob_perda: Optional[str] = None


class BaseProcessualBulkUpdatePayload(BaseModel):
    """Body de POST /processos/bulk-update.

    `confirm_count`: opcional, se enviado o servidor valida que o total
    real ainda bate (protege contra race entre preview e commit).
    """
    filter: BaseProcessualBulkUpdateFilters
    set: BaseProcessualBulkUpdateSet
    motivo: Optional[str] = None
    confirm_count: Optional[int] = None


class BaseProcessualBulkUpdateResult(BaseModel):
    """Resposta do bulk update."""
    total_afetados: int
    cods_afetados: List[str]
    upload_id: int  # do upload virtual BULK_UPDATE (audit trail)
    eventos_criados: int


# --- Schemas Exports (Chunk 5) ---

class BaseProcessualExportCreate(BaseModel):
    """Body do POST /exports.

    Templates aceitos (em template):
    - movimentacao_semanal: params = {from_date?, to_date?} (default = last 7d)
    - carteira_responsavel: params = {empresa?}
    - sumicos_periodo: params = {from_date?, to_date?} (default = mes corrente)
    - variacao_valores: params = {threshold_pct?, from_date?} (default 50%, todos)
    - carteira_uf_comarca: params = {empresa?}
    - snapshot_completo: params = {presenca_status?} (default ATIVO_NA_BASE)
    """
    template: str
    params: Optional[Dict[str, Any]] = None


class BaseProcessualExportOut(BaseModel):
    id: int
    template_name: str
    params_json: Optional[Dict[str, Any]] = None
    status: str
    file_path: Optional[str] = None
    file_bytes: Optional[int] = None
    total_rows: Optional[int] = None
    error_message: Optional[str] = None
    requested_by_user_id: Optional[int] = None
    requested_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class BaseProcessualExportListResponse(BaseModel):
    total: int
    items: List[BaseProcessualExportOut]

    model_config = ConfigDict(from_attributes=True)


# --- Schemas API Keys (Chunk 6) ---

class BaseProcessualApiKeyOut(BaseModel):
    """Chave de API — NUNCA inclui plaintext nem hash completo."""
    id: int
    nome: str
    key_prefix: str
    scope: str
    rate_limit_per_min: int
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    created_by_user_id: Optional[int] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BaseProcessualApiKeyListResponse(BaseModel):
    total: int
    items: List[BaseProcessualApiKeyOut]

    model_config = ConfigDict(from_attributes=True)


class BaseProcessualApiKeyCreatePayload(BaseModel):
    """Body do POST /admin/base-processual/api-keys."""
    nome: str
    scope: str  # read_processos | read_valores | read_dashboard | read_all
    rate_limit_per_min: Optional[int] = 60


class BaseProcessualApiKeyCreateResponse(BaseModel):
    """Resposta de POST/Regenerate — plaintext mostrado UMA VEZ."""
    api_key: BaseProcessualApiKeyOut
    plaintext: str  # operador copia agora; nao tem recuperacao
