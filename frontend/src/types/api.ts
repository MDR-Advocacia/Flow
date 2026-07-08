export interface BatchExecutionItem {
  id: number;
  process_number: string;
  status: "SUCESSO" | "FALHA" | "PENDENTE" | "REPROCESSANDO";
  created_task_id: number | null;
  error_message: string | null;
  fingerprint?: string | null;
}

export interface BatchExecution {
  id: number;
  source: string;
  source_filename?: string | null;
  requested_by_email?: string | null;
  status: string;
  start_time: string;
  end_time: string | null;
  total_items: number;
  success_count: number;
  failure_count: number;
  items: BatchExecutionItem[];
}

export interface LegalOnePositionFixProgressItem {
  index: number;
  cnj: string;
  lawsuitId?: number | null;
  sequenceNumber?: string | null;
  status: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  error?: string | null;
  positionSnippet?: string | null;
}

export interface LegalOnePositionFixWorkerStatus {
  id: string;
  label?: string | null;
  state?: string | null;
  total_items?: number | null;
  processed_items?: number | null;
  updated_count?: number | null;
  failed_count?: number | null;
  retry_pending_count?: number | null;
  remaining_items?: number | null;
  current_batch?: number | null;
  total_batches?: number | null;
  generated_at?: string | null;
}

export interface LegalOnePositionFixStatus {
  available: boolean;
  file_path: string;
  generated_at: string | null;
  state?: string | null;
  batch_size?: number | null;
  current_batch?: number | null;
  total_batches?: number | null;
  sleep_until?: string | null;
  control_file?: string | null;
  control_signal?: string | null;
  total_items: number;
  processed_items: number;
  updated_count: number;
  failed_count: number;
  retry_pending_count?: number | null;
  remaining_items: number;
  progress_percentage: number;
  average_update_seconds?: number | null;
  effective_average_seconds?: number | null;
  estimated_remaining_seconds?: number | null;
  estimated_completion_at?: string | null;
  active_queue_type?: string | null;
  retry_pass?: number | null;
  max_attempts?: number | null;
  workers?: LegalOnePositionFixWorkerStatus[];
  items: LegalOnePositionFixProgressItem[];
}

export interface LegalOnePositionFixControlResponse {
  message: string;
  action: "pause" | "resume";
  signal: string;
  control_file: string;
}

export interface PublicationTreatmentSummary {
  total_items: number;
  eligible_records: number;
  queue_count: number;
  pending_count: number;
  processing_count: number;
  completed_count: number;
  failed_count: number;
  cancelled_count: number;
  treated_target_count: number;
  without_providence_target_count: number;
}

export interface PublicationTreatmentItem {
  id: number;
  publication_record_id: number;
  legal_one_update_id: number;
  linked_lawsuit_id: number | null;
  linked_lawsuit_cnj: string | null;
  linked_office_id: number | null;
  publication_date: string | null;
  source_record_status: string;
  target_status: string;
  queue_status: string;
  attempt_count: number;
  last_run_id: number | null;
  last_attempt_at: string | null;
  treated_at: string | null;
  last_error: string | null;
  last_response: any;
  created_at: string | null;
  updated_at: string | null;
  record_status: string | null;
  publication_link: string | null;
}

export interface PublicationTreatmentRun {
  id: number;
  status: string;
  trigger_type: string;
  triggered_by_email: string | null;
  automation_id: number | null;
  total_items: number;
  processed_items: number;
  success_count: number;
  failed_count: number;
  retry_pending_count: number;
  batch_size: number | null;
  total_batches: number | null;
  current_batch: number | null;
  max_attempts: number | null;
  generated_at: string | null;
  sleep_until: string | null;
  started_at: string | null;
  finished_at: string | null;
  error_message: string | null;
  is_final: boolean;
  input_file_path?: string | null;
  status_file_path?: string | null;
  control_file_path?: string | null;
  log_file_path?: string | null;
  error_log_file_path?: string | null;
}

export interface PublicationTreatmentAutorun {
  enabled: boolean;
  cron_expression: string | null;
  next_run_at: string | null;
}

export interface PublicationTreatmentMonitor {
  summary: PublicationTreatmentSummary;
  active_run: PublicationTreatmentRun | null;
  available: boolean;
  progress_percentage: number;
  control_signal: string;
  recent_items: PublicationTreatmentItem[];
  recent_failures: PublicationTreatmentItem[];
  autorun?: PublicationTreatmentAutorun | null;
}

export interface PublicationTreatmentStartResponse {
  started: boolean;
  reason: string;
  backfill: {
    created: number;
    updated: number;
    cancelled: number;
    scanned: number;
  };
  run: PublicationTreatmentRun | null;
}

export interface PublicationTreatmentControlResponse {
  message: string;
  action: string;
  signal: string;
  control_file: string;
  run: PublicationTreatmentRun;
}

// ─── Prazos Iniciais ──────────────────────────────────────────────────

// Mantido em sincronia com as constantes INTAKE_STATUS_* do backend
// (app/models/prazo_inicial.py). Deixamos como string pra não quebrar
// quando o backend adicionar novos estados durante a evolução do fluxo.
export type PrazoInicialIntakeStatus =
  | "RECEBIDO"
  | "PROCESSO_NAO_ENCONTRADO"
  | "PRONTO_PARA_CLASSIFICAR"
  | "EM_CLASSIFICACAO"
  | "CLASSIFICADO"
  | "EM_REVISAO"
  | "AGENDADO"
  | "CONCLUIDO_SEM_PROVIDENCIA"
  | "GED_ENVIADO"
  | "CONCLUIDO"
  | "ERRO_CLASSIFICACAO"
  | "ERRO_AGENDAMENTO"
  | "ERRO_GED"
  | "CANCELADO"
  | string;

export interface PrazoInicialIntakeSummary {
  id: number;
  external_id: string;
  cnj_number: string;
  lawsuit_id: number | null;
  office_id: number | null;
  status: PrazoInicialIntakeStatus;
  natureza_processo?: string | null;
  produto?: string | null;
  // Bloco C — info de agravo (só natureza=AGRAVO_INSTRUMENTO).
  agravo_processo_origem_cnj?: string | null;
  agravo_decisao_agravada_resumo?: string | null;
  // Bloco E — agregados globais.
  valor_total_pedido?: number | null;
  valor_total_estimado?: number | null;
  aprovisionamento_sugerido?: number | null;
  probabilidade_exito_global?: string | null;
  analise_estrategica?: string | null;
  error_message: string | null;
  pdf_filename_original: string | null;
  pdf_bytes: number | null;
  ged_document_id: number | null;
  ged_uploaded_at: string | null;
  received_at: string;
  updated_at: string;
  sugestoes_count: number;
  // Tipos de prazo distintos das sugestoes do intake (ex.: ["CONTESTAR",
  // "AUDIENCIA"]). Usado pela UI de listagem pra exibir a "classificacao".
  // Lista vazia se intake nao foi classificado.
  tipos_prazo?: string[];
  // Data fatal mais proxima entre as sugestoes (ISO YYYY-MM-DD ou null).
  // UI usa pra exibir cor por urgencia.
  prazo_fatal_mais_proximo?: string | null;
  // Tratado por (pin011) — quem confirmou agendamentos OU finalizou.
  treated_by_user_id?: number | null;
  treated_by_email?: string | null;
  treated_by_name?: string | null;
  treated_at?: string | null;
  // Disparo desacoplado de GED + cancel (pin012, Onda 3 #5).
  // True = aguardando o operador acionar "Disparar agora" na Tratamento Web
  // (ou worker periódico).
  dispatch_pending?: boolean;
  dispatched_at?: string | null;
  dispatch_error_message?: string | null;
  // Origem do intake (pin016).
  source?: "EXTERNAL_API" | "USER_UPLOAD" | string;
  source_provider_name?: string | null;
  submitted_by_user_id?: number | null;
  submitted_by_email?: string | null;
  submitted_by_name?: string | null;
  submitted_at?: string | null;
  // True = USER_UPLOAD em que o PDF não tinha texto extraível
  // (escaneado). UI exibe badge "Classificar manualmente".
  pdf_extraction_failed?: boolean;
  extractor_used?: string | null;
  extraction_confidence?: "high" | "partial" | "low" | string | null;
  has_habilitacao_pdf?: boolean;
  habilitacao_pdf_filename_original?: string | null;
  habilitacao_pdf_bytes?: number | null;
  // Status agregado da validacao heuristica do PDF da habilitacao
  // (pin023). Detalhe completo (lista de checks) so' vai no Detail.
  habilitacao_check_status?:
    | "NAO_VERIFICADO"
    | "OK"
    | "ALERTA"
    | "FALHA"
    | "ERRO_EXTRACAO"
    | string;
  // Patrocinio (pin018) — sumário pra badge na listagem.
  patrocinio_decisao?:
    | "MDR_ADVOCACIA"
    | "OUTRO_ESCRITORIO"
    | "CONDUCAO_INTERNA"
    | string
    | null;
  patrocinio_suspeita_devolucao?: boolean;
  patrocinio_review_status?:
    | "pendente"
    | "aprovado"
    | "editado"
    | "rejeitado"
    | string
    | null;
  // Despacho de citação (pin024) — sinal-âncora; badge forte na listagem.
  despacho_citacao_existe?: boolean;
  despacho_citacao_data?: string | null;
  despacho_citacao_modalidade?:
    | "eletronica"
    | "correio"
    | "oficial_justica"
    | "edital"
    | "outra"
    | string
    | null;
  despacho_citacao_efetivada?: boolean | null;
  despacho_citacao_justificativa?: string | null;
  // Vinculada Master no polo passivo (pin024) — flag neutra, substitui patrocínio.
  vinculada_master_presente?: boolean;
  vinculada_master_nome?: string | null;
  vinculada_master_cnpj?: string | null;
  vinculada_master_polo_confirmado?: boolean | null;
  vinculada_master_observacao?: string | null;
}

export interface PrazoInicialPatrocinio {
  id: number;
  intake_id: number;
  decisao: "MDR_ADVOCACIA" | "OUTRO_ESCRITORIO" | "CONDUCAO_INTERNA" | string;
  outro_escritorio_nome: string | null;
  outro_advogado_nome: string | null;
  outro_advogado_oab: string | null;
  outro_advogado_data_habilitacao: string | null;
  suspeita_devolucao: boolean;
  motivo_suspeita: string | null;
  natureza_acao:
    | "CONSUMERISTA"
    | "CIVIL_PUBLICA"
    | "INQUERITO_ADMINISTRATIVO"
    | "TRABALHISTA"
    | "OUTRO"
    | string
    | null;
  polo_passivo_confirmado: boolean;
  polo_passivo_observacao: string | null;
  confianca: "alta" | "media" | "baixa" | string | null;
  fundamentacao: string | null;
  review_status: "pendente" | "aprovado" | "editado" | "rejeitado" | string;
  reviewed_by_user_id: number | null;
  reviewed_by_email: string | null;
  reviewed_by_name: string | null;
  reviewed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface PrazoInicialPatrocinioPatch {
  review_action: "aprovado" | "editado" | "rejeitado";
  decisao?: "MDR_ADVOCACIA" | "OUTRO_ESCRITORIO" | "CONDUCAO_INTERNA";
  outro_escritorio_nome?: string | null;
  outro_advogado_nome?: string | null;
  outro_advogado_oab?: string | null;
  outro_advogado_data_habilitacao?: string | null;
  suspeita_devolucao?: boolean;
  motivo_suspeita?: string | null;
  natureza_acao?:
    | "CONSUMERISTA"
    | "CIVIL_PUBLICA"
    | "INQUERITO_ADMINISTRATIVO"
    | "TRABALHISTA"
    | "OUTRO";
  polo_passivo_confirmado?: boolean;
  polo_passivo_observacao?: string | null;
}

export interface PrazoInicialParteProcessual {
  nome: string;
  documento?: string | null;
}

export interface PrazoInicialCapaProcesso {
  tribunal?: string | null;
  vara?: string | null;
  classe?: string | null;
  assunto?: string | null;
  valor_causa?: number | null;
  data_distribuicao?: string | null;
  polo_ativo?: PrazoInicialParteProcessual[];
  polo_passivo?: PrazoInicialParteProcessual[];
  segredo_justica?: boolean;
  [extra: string]: unknown;
}

export interface PrazoInicialSugestao {
  id: number;
  tipo_prazo: string;
  subtipo: string | null;
  data_base: string | null;
  prazo_dias: number | null;
  prazo_tipo: string | null;
  data_final_calculada: string | null;
  // Bloco B — prazo fatal absoluto (considera últimas decisões + PI).
  prazo_fatal_data: string | null;
  prazo_fatal_fundamentacao: string | null;
  prazo_base_decisao: string | null;
  audiencia_data: string | null;
  audiencia_hora: string | null;
  audiencia_link: string | null;
  confianca: string | null;
  justificativa: string | null;
  responsavel_sugerido_id: number | null;
  task_type_id: number | null;
  task_subtype_id: number | null;
  payload_proposto: Record<string, unknown> | null;
  review_status: string;
  reviewed_by_email: string | null;
  reviewed_at: string | null;
  created_task_id: number | null;
  created_at: string;
  // Roteamento de squad (vem do template casado via payload_proposto.template_id)
  // + preview do responsavel resolvido (assistente / lider de support squad).
  // Campos populados pelo backend em GET /intakes/{id}; null quando a sugestao
  // nao tem squad routing OU quando target_role='principal' sem support squad
  // (nesse caso UI usa responsavel_sugerido_id direto).
  target_role: "principal" | "assistente" | null;
  target_squad_id: number | null;
  target_squad_name: string | null;
  resolved_responsible_user_external_id: number | null;
  resolved_responsible_user_name: string | null;
  resolution_warning: string | null;
}

export interface PrazoInicialPedido {
  id: number;
  intake_id: number;
  tipo_pedido: string;
  natureza: string | null;
  valor_indicado: number | null;
  valor_estimado: number | null;
  fundamentacao_valor: string | null;
  probabilidade_perda: "remota" | "possivel" | "provavel" | null;
  aprovisionamento: number | null;
  fundamentacao_risco: string | null;
}

// Validacao heuristica da habilitacao (pin023). Cada check eh uma
// linha individual com criticidade (CRITICO|AVISO) e status
// (OK|ALERTA|FALHA|PULADO). FALHA sinaliza mas nao bloqueia.
export interface PrazoInicialHabilitacaoCheck {
  id: string;
  label: string;
  criticidade: "CRITICO" | "AVISO" | string;
  status: "OK" | "ALERTA" | "FALHA" | "PULADO" | string;
  detalhe: string | null;
}

export interface PrazoInicialHabilitacaoCheckResult {
  status:
    | "NAO_VERIFICADO"
    | "OK"
    | "ALERTA"
    | "FALHA"
    | "ERRO_EXTRACAO"
    | string;
  checks: PrazoInicialHabilitacaoCheck[];
  checked_at: string | null;
}

export interface PrazoInicialIntakeDetail extends PrazoInicialIntakeSummary {
  capa_json: PrazoInicialCapaProcesso;
  metadata_json: Record<string, unknown> | null;
  sugestoes: PrazoInicialSugestao[];
  pedidos: PrazoInicialPedido[];
  patrocinio: PrazoInicialPatrocinio | null;
  // pin023 — bloco completo da validacao heuristica. null quando
  // nunca rodou (intake antigo NAO_VERIFICADO).
  habilitacao_check?: PrazoInicialHabilitacaoCheckResult | null;
}

export interface PrazoInicialIntakeListResponse {
  total: number;
  items: PrazoInicialIntakeSummary[];
}

// Batch de classificacao (Onda 1 — botao manual na tela de Prazos Iniciais).
// Espelha backend/app/api/v1/endpoints/prazos_iniciais.py::BatchSummary.
export interface PrazoInicialClassifyPendingResponse {
  submitted: boolean;
  batch_id: number | null;
  anthropic_batch_id: string | null;
  intakes_count: number;
  message: string;
}

export interface PrazoInicialBatchSummary {
  id: number;
  anthropic_batch_id: string | null;
  // ENVIADO | EM_PROCESSAMENTO | PRONTO | APLICADO | ERRO
  status: string;
  anthropic_status: string | null;
  total_records: number;
  succeeded_count: number;
  errored_count: number;
  expired_count: number;
  canceled_count: number;
  model_used: string | null;
  requested_by_email: string | null;
  intake_ids: number[] | null;
  created_at: string | null;
  submitted_at: string | null;
  ended_at: string | null;
  applied_at: string | null;
}

export interface PrazoInicialBatchListResponse {
  total: number;
  items: PrazoInicialBatchSummary[];
}

export interface PrazoInicialApplyBatchResponse {
  succeeded: number;
  failed: number;
  skipped: number;
  total_results: number;
  total_sugestoes: number;
}

export interface PrazoInicialIntakeFilters {
  // Todos os filtros abaixo aceitam CSV quando é multi-valor.
  status?: string;           // "CLASSIFICADO,AGENDADO"
  cnj_number?: string;
  office_id?: string;        // "61,62" (string porque backend aceita CSV agora)
  natureza_processo?: string;
  produto?: string;
  probabilidade_exito_global?: string; // "remota,possivel,provavel"
  date_from?: string;        // "YYYY-MM-DD"
  date_to?: string;
  has_error?: boolean;
  batch_id?: number;
  treated_by_user_id?: string;  // CSV de user_ids: "5,8"
  dispatch_pending?: boolean;   // true = só pendentes; false = só já disparados
  // Origem do intake (pin016).
  source?: string;                  // CSV: "EXTERNAL_API,USER_UPLOAD"
  submitted_by_user_id?: string;    // CSV — atalho "Minha fila"
  // Classificacao = tipo_prazo das sugestoes. Filtra intakes com AO
  // MENOS uma sugestao em algum desses tipos. CSV: "AUDIENCIA,CONTESTAR".
  tipo_prazo?: string;
  pdf_extraction_failed?: boolean;  // true = só uploads com extração falha
  // Patrocinio (pin018)
  patrocinio_decisao?: string;          // CSV: "MDR_ADVOCACIA,OUTRO_ESCRITORIO,..."
  patrocinio_suspeita_devolucao?: boolean;
  patrocinio_review_status?: string;    // CSV: "pendente,aprovado,..."
  limit?: number;
  offset?: number;
}

export interface PrazoInicialUploadResponse {
  intake_id: number;
  external_id: string;
  status: string;
  extractor_used: string | null;
  extraction_confidence: string | null;
  pdf_extraction_failed: boolean;
  has_habilitacao_pdf: boolean;
  already_existed: boolean;
  user_message: string | null;
}

export type PrazoInicialLegacyTaskCancelQueueStatus =
  | "PENDENTE"
  | "PROCESSANDO"
  | "CONCLUIDO"
  | "FALHA"
  | "CANCELADO"
  | string;

export interface PrazoInicialLegacyTaskCancelQueueItem {
  id: number;
  intake_id: number;
  lawsuit_id: number | null;
  cnj_number: string | null;
  office_id: number | null;
  legacy_task_type_external_id: number;
  legacy_task_subtype_external_id: number;
  queue_status: PrazoInicialLegacyTaskCancelQueueStatus;
  attempt_count: number;
  selected_task_id: number | null;
  cancelled_task_id: number | null;
  last_reason: string | null;
  last_attempt_at: string | null;
  completed_at: string | null;
  last_error: string | null;
  last_result: any;
  created_at: string | null;
  updated_at: string | null;
}

export interface PrazoInicialLegacyTaskCancelQueueListResponse {
  total: number;
  items: PrazoInicialLegacyTaskCancelQueueItem[];
}

export interface PrazoInicialConfirmSchedulingSuggestion {
  suggestion_id: number;
  created_task_id?: number | null;
  review_status?: string | null;
  // Overrides opcionais — operador editou campos da sugestao no Modal
  // de Agendar. Backend aplica antes de criar a task no L1.
  override_task_subtype_external_id?: number | null;
  override_responsible_user_external_id?: number | null;
  override_data_base?: string | null; // ISO YYYY-MM-DD
  override_data_final_calculada?: string | null;
  override_prazo_dias?: number | null;
  override_prazo_tipo?: string | null; // util | corrido
  override_priority?: string | null; // Low | Normal | High
  override_description?: string | null;
  override_notes?: string | null;
}

/**
 * Tarefa avulsa adicionada pelo operador no modal de Confirmar
 * Agendamento — nao casa com sugestao da IA. Backend cria no L1 +
 * persiste sugestao sintetica (tipo_prazo='AVULSA').
 */
export interface PrazoInicialCustomTaskPayload {
  task_subtype_external_id: number;
  responsible_user_external_id: number;
  description: string;
  due_date: string; // ISO YYYY-MM-DD
  priority?: string; // Low/Normal/High
  notes?: string | null;
}

export interface PrazoInicialSchedulingConfirmationPayload {
  suggestions?: PrazoInicialConfirmSchedulingSuggestion[];
  custom_tasks?: PrazoInicialCustomTaskPayload[];
  enqueue_legacy_task_cancellation?: boolean;
  legacy_task_type_external_id?: number;
  legacy_task_subtype_external_id?: number;
}

export interface PrazoInicialSchedulingConfirmationResponse {
  intake: PrazoInicialIntakeSummary;
  confirmed_suggestion_ids: number[];
  created_task_ids: number[];
  legacy_task_cancellation_item: PrazoInicialLegacyTaskCancelQueueItem | null;
}

export interface PrazoInicialLegacyTaskQueueProcessResult {
  item: PrazoInicialLegacyTaskCancelQueueItem;
  result: any;
}

export interface PrazoInicialLegacyTaskQueueProcessResponse {
  processed_count: number;
  eligible_count?: number;
  success_count?: number;
  failure_count?: number;
  circuit_breaker_tripped?: boolean;
  circuit_breaker_tripped_during_tick?: boolean;
  tick_id?: string | null;
  items: PrazoInicialLegacyTaskQueueProcessResult[];
}

export interface PrazoInicialLegacyTaskCircuitBreakerResetResponse {
  success: boolean;
  circuit_breaker: PrazoInicialLegacyTaskCircuitBreakerSnapshot;
}

export interface PrazoInicialLegacyTaskQueueFilters {
  queue_status?: string;
  intake_id?: number;
  cnj_number?: string;
  since?: string; // ISO 8601
  until?: string; // ISO 8601
  limit?: number;
  offset?: number;
}

export interface PrazoInicialLegacyTaskCircuitBreakerSnapshot {
  tripped: boolean;
  tripped_until: string | null;
  consecutive_failures: number;
  threshold: number;
  cooldown_minutes: number;
  last_trip_reason: string | null;
  last_trip_at: string | null;
  last_reset_at: string | null;
  counted_reasons: string[];
}

// ─── AJUS (módulo de andamentos pra sistema do cliente) ──────────────

export type AjusQueueStatus =
  | "pendente"
  | "enviando"
  | "sucesso"
  | "erro"
  | "cancelado";

export interface AjusCodAndamento {
  id: number;
  codigo: string;
  label: string;
  descricao: string | null;
  situacao: "A" | "C";
  dias_agendamento_offset_uteis: number;
  dias_fatal_offset_uteis: number;
  informacao_template: string;
  is_default: boolean;
  is_active: boolean;
}

export interface AjusCodAndamentoCreatePayload {
  codigo: string;
  label: string;
  descricao?: string | null;
  situacao: "A" | "C";
  dias_agendamento_offset_uteis: number;
  dias_fatal_offset_uteis: number;
  informacao_template: string;
  is_default: boolean;
  is_active: boolean;
}

export interface AjusAndamentoQueueItem {
  id: number;
  intake_id: number;
  cnj_number: string;
  cod_andamento_id: number;
  cod_andamento_codigo: string | null;
  cod_andamento_label: string | null;
  situacao: "A" | "C";
  data_evento: string;          // YYYY-MM-DD
  data_agendamento: string;
  data_fatal: string;
  hora_agendamento: string | null;
  informacao: string;
  has_pdf: boolean;
  status: AjusQueueStatus;
  cod_informacao_judicial: string | null;
  error_message: string | null;
  created_at: string;
  dispatched_at: string | null;
}

export interface AjusAndamentoQueueListResponse {
  total: number;
  items: AjusAndamentoQueueItem[];
}

export interface AjusDispatchBatchResponse {
  candidates: number;
  success_count: number;
  error_count: number;
  success_ids: number[];
  errored: { id: number; msg: string }[];
}

// ─── Classificação AJUS (Chunk 1) ─────────────────────────────────────

export type AjusClassifStatus =
  | "pendente"
  | "processando"
  | "sucesso"
  | "erro"
  | "cancelado"
  | "nao_encontrado";

export type AjusClassifOrigem = "intake_auto" | "planilha";

export interface AjusClassifDefaults {
  default_matter: string | null;
  default_risk_loss_probability: string | null;
  updated_at: string | null;
  is_paused: boolean;
  paused_at: string | null;
  paused_by: string | null;
}

export interface AjusClassifCancelResponse {
  cancelled: number;
  ids: number[];
}

export interface AjusClassifQueueItem {
  id: number;
  cnj_number: string;
  intake_id: number | null;
  origem: AjusClassifOrigem;
  uf: string | null;
  comarca: string | null;
  matter: string | null;
  justice_fee: string | null;
  risk_loss_probability: string | null;
  status: AjusClassifStatus;
  error_message: string | null;
  last_log: string | null;
  executed_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  dispatched_by_account_id: number | null;
}

export interface AjusClassifQueueListResponse {
  total: number;
  items: AjusClassifQueueItem[];
}

export interface AjusClassifQueueUpdatePayload {
  uf?: string | null;
  comarca?: string | null;
  matter?: string | null;
  justice_fee?: string | null;
  risk_loss_probability?: string | null;
}

export interface AjusClassifUploadResponse {
  created: number;
  updated: number;
  skipped: { cnj: string; motivo: string }[];
  /**
   * Itens hard-deleted pelo modo absoluto (sync_mode=true). Contem
   * itens com origem='planilha' em status pendente/processando/erro
   * que sumiram da nova planilha. Sucesso e intake_auto sao preservados.
   */
  deleted: { id: number; cnj: string; status_anterior: string }[];
}

// ─── Sessões AJUS (Chunk 2 — multi-conta) ─────────────────────────────

export type AjusAccountStatus =
  | "offline"
  | "logando"
  | "aguardando_ip_code"
  | "online"
  | "executando"
  | "erro";

export interface AjusSessionAccount {
  id: number;
  label: string;
  login: string;
  status: AjusAccountStatus;
  has_storage_state: boolean;
  has_pending_ip_code: boolean;
  last_error_message: string | null;
  last_error_at: string | null;
  last_used_at: string | null;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface AjusSessionConfig {
  crypto_configured: boolean;
  portal_base_url: string;
}

export interface AjusSessionCreatePayload {
  label: string;
  login: string;
  password: string;
}

export interface AjusSessionUpdatePayload {
  label?: string;
  login?: string;
  password?: string;        // se vier, troca senha + invalida storage
  is_active?: boolean;
}

export interface AjusClassifDispatchResponse {
  candidates: number;
  success_count: number;
  error_count: number;
  success_ids: number[];
  errored: { id: number; msg: string }[];
  accounts_used: number[];
  // Modo soft-trigger: accepted=true quando ha trabalho a fazer
  // (pendentes > 0 e ao menos 1 conta online). message eh humano-readable.
  accepted?: boolean;
  accounts_online?: number;
  message?: string;
}

export interface PrazoInicialLegacyTaskLastTickState {
  tick_id: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  eligible_count: number;
  processed_count: number;
  success_count: number;
  failure_count: number;
  circuit_breaker_tripped: boolean;
  circuit_breaker_tripped_during_tick: boolean;
  error: string | null;
}

export interface PrazoInicialLegacyTaskQueueMetrics {
  window_hours: number;
  window_start: string;
  now: string;
  totals_by_status: Record<string, number>;
  completed_in_window: number;
  failures_in_window: number;
  failures_by_reason_in_window: Record<string, number>;
  avg_latency_ms_in_window: number | null;
  latency_samples_in_window: number;
  circuit_breaker: PrazoInicialLegacyTaskCircuitBreakerSnapshot;
  rate_limit_seconds: number;
  last_tick: PrazoInicialLegacyTaskLastTickState;
}

export interface PrazoInicialLegacyTaskQueueItemActionResponse {
  item: PrazoInicialLegacyTaskCancelQueueItem;
}


// ─── Templates de Prazos Iniciais ─────────────────────────────────────

export interface PrazoInicialTaskTemplate {
  id: number;
  name: string;
  tipo_prazo: string;
  subtipo: string | null;
  natureza_aplicavel: string | null;
  office_external_id: number | null;
  office_name: string | null;
  // Template "no-op" (pin014): casa normal, mas finaliza sem criar
  // tarefa no L1. Quando true, task_subtype_external_id e
  // responsible_user_external_id sao null.
  skip_task_creation: boolean;
  task_subtype_external_id: number | null;
  task_subtype_name: string | null;
  responsible_user_external_id: number | null;
  responsible_user_name: string | null;
  priority: string;
  due_business_days: number;
  due_date_reference: string;
  description_template: string | null;
  notes_template: string | null;
  is_active: boolean;
  // Roteamento de squad (sqd001 + sqd004). 'principal' (default) =
  // tarefa vai pro responsible_user_external_id direto; 'assistente' =
  // backend redireciona pro assistente da squad principal do
  // responsavel via resolve_target. target_squad_id != null = squad de
  // suporte explicita (kind='support'); combinado com target_role.
  target_role: "principal" | "assistente";
  target_squad_id: number | null;
  target_squad_name: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface PrazoInicialTaskTemplateListResponse {
  total: number;
  items: PrazoInicialTaskTemplate[];
}

export interface PrazoInicialTaskTemplateCreatePayload {
  name: string;
  tipo_prazo: string;
  subtipo?: string | null;
  natureza_aplicavel?: string | null;
  office_external_id?: number | null;
  // Template "no-op" (pin014). Quando true, envie task_subtype/responsible
  // como null — backend valida.
  skip_task_creation?: boolean;
  task_subtype_external_id?: number | null;
  responsible_user_external_id?: number | null;
  priority?: string;
  due_business_days?: number;
  due_date_reference?: string;
  description_template?: string | null;
  notes_template?: string | null;
  is_active?: boolean;
  // Roteamento de squad — sem isso o frontend mandava esses campos via
  // (t as any) e o tipo nao sinalizava se eles estavam realmente sendo
  // enviados. Tipar explicito reduz risco de regressao silenciosa.
  target_role?: "principal" | "assistente";
  target_squad_id?: number | null;
}

// Update aceita qualquer subconjunto dos campos do Create.
export type PrazoInicialTaskTemplateUpdatePayload =
  Partial<PrazoInicialTaskTemplateCreatePayload>;

export interface PrazoInicialTaskTemplateFilters {
  tipo_prazo?: string;
  /** "" (string vazia) = filtra por NULL (genérico); undefined = sem filtro */
  subtipo?: string;
  /** "" (string vazia) = filtra por NULL (genérico); undefined = sem filtro */
  natureza_aplicavel?: string;
  /** 0 = filtra por NULL (global); undefined = sem filtro */
  office_external_id?: number;
  is_active?: boolean;
  limit?: number;
  offset?: number;
}

export interface PrazoInicialEnums {
  tipos_prazo: string[];
  naturezas: string[];
  produtos: string[];
  subtipos_audiencia: string[];
  subtipos_julgamento: string[];
  priorities: string[];
  due_date_references: string[];
}

// ════════════════════════════════════════════════════════════════
// Types restaurados 2026-05-07 (truncados em ae93514).
// ════════════════════════════════════════════════════════════════

export type AdminNoticeSeverity = "info" | "warning" | "danger";

export type AdminNoticeStatus = "agendado" | "ativo" | "expirado";

/** Subset do aviso visivel pra qualquer usuario (GET /admin/notices/active). */
export interface AdminNoticeActive {
  id: number;
  title: string;
  message: string;
  severity: AdminNoticeSeverity;
  /** true -> exibir como pop-up bloqueante (exige clicar "Ciente"). */
  require_ack: boolean;
  starts_at: string | null;
  ends_at: string | null;
}

/** Versao completa pro CRUD admin (inclui contadores + status calculado). */
export interface AdminNotice {
  id: number;
  title: string;
  message: string;
  severity: AdminNoticeSeverity;
  require_ack: boolean;
  starts_at: string;
  ends_at: string;
  created_by_user_id: number | null;
  created_at: string;
  updated_at: string;
  status: AdminNoticeStatus;
  seen_count: number;
  dismissed_count: number;
}

export interface AdminNoticeCreatePayload {
  title: string;
  message: string;
  severity: AdminNoticeSeverity;
  require_ack: boolean;
  starts_at: string;
  ends_at: string;
}

export type AdminNoticeUpdatePayload = Partial<AdminNoticeCreatePayload>;

/** Um usuario que viu ou confirmou um aviso (GET /admin/notices/{id}/audience). */
export interface AdminNoticeAudienceEntry {
  user_id: number;
  name: string | null;
  email: string | null;
  at: string;
}

/** Detalhe de audiencia de um aviso: quem viu e quem confirmou. */
export interface AdminNoticeAudience {
  notice_id: number;
  seen: AdminNoticeAudienceEntry[];
  acknowledged: AdminNoticeAudienceEntry[];
}

export interface AjusBlocklistItem {
  id: number;
  cnj_number: string;
  cod_ajus: string | null;
  materia: string | null;
  first_seen_at: string;
  last_seen_at: string;
}

export interface AjusBlocklistListResponse {
  total: number;
  items: AjusBlocklistItem[];
}

export interface AjusBlocklistStatsResponse {
  total_no_blocklist: number;
  items_fila_bloqueados: number;
  ultimo_upload_at: string | null;
}

export interface AjusBlocklistUploadResponse {
  added: number;
  updated: number;
  removed: number;
  total_after: number;
}

export interface PatrocinioRelatorioFilters {
  since?: string | null;
  until?: string | null;
  office_id?: number | null;
  limit?: number;
  offset?: number;
}

export interface PatrocinioRelatorioItem {
  intake_id: number;
  cnj_number: string | null;
  lawsuit_id: number | null;
  office_id: number | null;
  intake_status: string;
  received_at: string | null;
  decisao: string;
  suspeita_devolucao: boolean;
  natureza_acao: string | null;
  motivo_suspeita: string | null;
  outro_advogado_nome: string | null;
  outro_advogado_oab: string | null;
  outro_advogado_data_habilitacao: string | null;
  outro_escritorio_nome: string | null;
  polo_passivo_confirmado: boolean;
  confianca: string | null;
  fundamentacao: string | null;
  review_status: string;
  reviewed_by_email: string | null;
  reviewed_by_name: string | null;
  reviewed_at: string | null;
  ajus_queue_status: string | null;
  ajus_queue_id: number | null;
}

export interface PatrocinioRelatorioResponse {
  total: number;
  limit: number;
  offset: number;
  items: PatrocinioRelatorioItem[];
}

export interface PrazoInicialContestacaoExistente {
  existe: boolean;
  apresentada_por_mdr: boolean | null;
  apresentada_por_nome: string | null;
  apresentada_por_oab: string | null;
  parte_representada: string | null;
  data_apresentacao: string | null;
  generica: boolean | null;
  analise_qualidade: string | null;
}

// ──────────────────────────────────────────────────────────────────────
// Feedback dos usuarios (botao flutuante + painel admin)
// ──────────────────────────────────────────────────────────────────────

export type UserFeedbackCategory =
  | "bug"
  | "sugestao"
  | "duvida"
  | "elogio"
  | "outro";

export type UserFeedbackStatus = "novo" | "lido" | "arquivado";

export interface UserFeedback {
  id: number;
  user_id: number;
  user_name: string | null;
  user_email: string | null;
  category: UserFeedbackCategory;
  message: string;
  page_url: string | null;
  user_agent: string | null;
  status: UserFeedbackStatus;
  admin_note: string | null;
  reviewed_by_user_id: number | null;
  reviewed_by_name: string | null;
  reviewed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface UserFeedbackListResponse {
  total: number;
  items: UserFeedback[];
}

export interface UserFeedbackStats {
  total: number;
  novo: number;
  lido: number;
  arquivado: number;
  by_category: Record<string, number>;
}

export interface UserFeedbackCreatePayload {
  category: UserFeedbackCategory;
  message: string;
  page_url?: string | null;
  user_agent?: string | null;
}

export interface UserFeedbackUpdatePayload {
  status?: UserFeedbackStatus;
  admin_note?: string | null;
}

