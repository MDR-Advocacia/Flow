import { apiFetch } from "@/lib/api-client";
import {
  AjusAndamentoQueueListResponse,
  AjusClassifCancelResponse,
  AjusClassifDefaults,
  AjusClassifQueueItem,
  AjusClassifQueueListResponse,
  AjusClassifQueueUpdatePayload,
  AjusClassifDispatchResponse,
  AjusClassifUploadResponse,
  AjusSessionAccount,
  AjusSessionConfig,
  AjusSessionCreatePayload,
  AjusSessionUpdatePayload,
  AjusCodAndamento,
  AjusCodAndamentoCreatePayload,
  AjusDispatchBatchResponse,
  BatchExecution,
  LegalOnePositionFixControlResponse,
  LegalOnePositionFixStatus,
  PrazoInicialApplyBatchResponse,
  PrazoInicialBatchListResponse,
  PrazoInicialBatchSummary,
  PrazoInicialClassifyPendingResponse,
  PrazoInicialEnums,
  PrazoInicialHabilitacaoCheckResult,
  PrazoInicialIntakeDetail,
  PrazoInicialIntakeFilters,
  PrazoInicialIntakeListResponse,
  PrazoInicialIntakeSummary,
  PrazoInicialPatrocinio,
  PrazoInicialPatrocinioPatch,
  PrazoInicialUploadResponse,
  PrazoInicialLegacyTaskCancelQueueListResponse,
  PrazoInicialLegacyTaskCircuitBreakerResetResponse,
  PrazoInicialLegacyTaskQueueFilters,
  PrazoInicialLegacyTaskQueueItemActionResponse,
  PrazoInicialLegacyTaskQueueMetrics,
  PrazoInicialLegacyTaskQueueProcessResponse,
  PrazoInicialSchedulingConfirmationPayload,
  PrazoInicialSchedulingConfirmationResponse,
  PrazoInicialTaskTemplate,
  PrazoInicialTaskTemplateCreatePayload,
  PrazoInicialTaskTemplateFilters,
  PrazoInicialTaskTemplateListResponse,
  PrazoInicialTaskTemplateUpdatePayload,
  PublicationTreatmentControlResponse,
  PublicationTreatmentMonitor,
  PublicationTreatmentRun,
  PublicationTreatmentStartResponse,
  AdminNotice,
  AdminNoticeActive,
  AdminNoticeAudience,
  AdminNoticeCreatePayload,
  AdminNoticeUpdatePayload,
  AjusAndamentoQueueItem,
  AjusBackfillRequest,
  AjusBackfillResponse,
  AjusBlocklistStatsResponse,
  AjusBlocklistUploadResponse,
  AjusBulkResponse,
  AjusBulkVarsPayload,
  PatrocinioRelatorioFilters,
  PatrocinioRelatorioResponse,
  PrazoInicialLegacyTaskZombieListResponse,
  PrazoInicialLegacyTaskZombieRecoverResponse,
} from "@/types/api";


async function expectJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    // FastAPI/Pydantic 422 vem como `detail: [{loc, msg, type}, ...]`.
    // Tem que serializar pra string legivel, senao vira "[object Object]".
    let detail: string | undefined;
    if (typeof errorData.detail === "string") {
      detail = errorData.detail;
    } else if (Array.isArray(errorData.detail)) {
      detail = errorData.detail
        .map((e: any) => {
          const loc = Array.isArray(e?.loc) ? e.loc.join(".") : "";
          const msg = e?.msg || e?.message || String(e);
          return loc ? `${loc}: ${msg}` : msg;
        })
        .join("; ");
    } else if (errorData.detail) {
      detail = JSON.stringify(errorData.detail);
    }
    throw new Error(detail || `HTTP error! status: ${response.status}`);
  }
  return response.json();
}


export async function fetchBatchExecutions(): Promise<BatchExecution[]> {
  const response = await apiFetch("/api/v1/dashboard/batch-executions");
  return expectJson<BatchExecution[]>(response);
}


export async function retryBatchExecution(
  executionId: number,
  itemIds: number[] | null = null,
): Promise<{ message: string }> {
  const options: RequestInit = {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
  };

  if (itemIds && itemIds.length > 0) {
    options.body = JSON.stringify({ item_ids: itemIds });
  }

  const response = await apiFetch(`/api/v1/tasks/executions/${executionId}/retry`, options);
  return expectJson<{ message: string }>(response);
}


export async function pauseBatchExecution(executionId: number): Promise<{ message: string; status: string }> {
  const response = await apiFetch(`/api/v1/tasks/executions/${executionId}/pause`, { method: "POST" });
  return expectJson<{ message: string; status: string }>(response);
}


export async function resumeBatchExecution(executionId: number): Promise<{ message: string; status: string }> {
  const response = await apiFetch(`/api/v1/tasks/executions/${executionId}/resume`, { method: "POST" });
  return expectJson<{ message: string; status: string }>(response);
}


export async function cancelBatchExecution(executionId: number): Promise<{ message: string; status: string }> {
  const response = await apiFetch(`/api/v1/tasks/executions/${executionId}/cancel`, { method: "POST" });
  return expectJson<{ message: string; status: string }>(response);
}


export async function downloadBatchErrorReport(executionId: number): Promise<Blob> {
  const response = await apiFetch(`/api/v1/tasks/executions/${executionId}/error-report`);
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
  }
  return response.blob();
}


export async function fetchLegalOnePositionFixStatus(): Promise<LegalOnePositionFixStatus> {
  const response = await apiFetch("/api/v1/monitor/legal-one-position-fix/status");
  return expectJson<LegalOnePositionFixStatus>(response);
}


export async function updateLegalOnePositionFixControl(
  action: "pause" | "resume",
): Promise<LegalOnePositionFixControlResponse> {
  const response = await apiFetch("/api/v1/monitor/legal-one-position-fix/control", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ action }),
  });
  return expectJson<LegalOnePositionFixControlResponse>(response);
}


export async function fetchPublicationTreatmentMonitor(
  signal?: AbortSignal,
): Promise<PublicationTreatmentMonitor> {
  const response = await apiFetch("/api/v1/publications/treatment/monitor", { signal });
  return expectJson<PublicationTreatmentMonitor>(response);
}


export async function fetchPublicationTreatmentRuns(
  signal?: AbortSignal,
): Promise<PublicationTreatmentRun[]> {
  const response = await apiFetch("/api/v1/publications/treatment/runs", { signal });
  return expectJson<PublicationTreatmentRun[]>(response);
}


export async function startPublicationTreatmentRun(): Promise<PublicationTreatmentStartResponse> {
  const response = await apiFetch("/api/v1/publications/treatment/runs/start", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({}),
  });
  return expectJson<PublicationTreatmentStartResponse>(response);
}


export async function retryPublicationTreatmentItem(
  itemId: number,
): Promise<PublicationTreatmentStartResponse> {
  const response = await apiFetch(`/api/v1/publications/treatment/items/${itemId}/retry`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({}),
  });
  return expectJson<PublicationTreatmentStartResponse>(response);
}


export async function updatePublicationTreatmentRunControl(
  runId: number,
  action: "pause" | "resume",
): Promise<PublicationTreatmentControlResponse> {
  const response = await apiFetch(`/api/v1/publications/treatment/runs/${runId}/control`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ action }),
  });
  return expectJson<PublicationTreatmentControlResponse>(response);
}


// ─── Prazos Iniciais ──────────────────────────────────────────────────

export async function fetchPrazosIniciaisIntakes(
  filters: PrazoInicialIntakeFilters = {},
): Promise<PrazoInicialIntakeListResponse> {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.cnj_number) params.set("cnj_number", filters.cnj_number);
  // office_id agora aceita CSV no backend — serializamos como string.
  if (filters.office_id) params.set("office_id", filters.office_id);
  if (filters.natureza_processo) {
    params.set("natureza_processo", filters.natureza_processo);
  }
  if (filters.produto) params.set("produto", filters.produto);
  if (filters.probabilidade_exito_global) {
    params.set("probabilidade_exito_global", filters.probabilidade_exito_global);
  }
  if (filters.date_from) params.set("date_from", filters.date_from);
  if (filters.date_to) params.set("date_to", filters.date_to);
  if (typeof filters.has_error === "boolean") {
    params.set("has_error", String(filters.has_error));
  }
  if (typeof filters.batch_id === "number") {
    params.set("batch_id", String(filters.batch_id));
  }
  if (filters.treated_by_user_id) {
    params.set("treated_by_user_id", filters.treated_by_user_id);
  }
  if (typeof filters.dispatch_pending === "boolean") {
    params.set("dispatch_pending", String(filters.dispatch_pending));
  }
  if (filters.source) {
    params.set("source", filters.source);
  }
  if (filters.submitted_by_user_id) {
    params.set("submitted_by_user_id", filters.submitted_by_user_id);
  }
  if (filters.tipo_prazo) {
    params.set("tipo_prazo", filters.tipo_prazo);
  }
  if (typeof filters.pdf_extraction_failed === "boolean") {
    params.set("pdf_extraction_failed", String(filters.pdf_extraction_failed));
  }
  if (filters.patrocinio_decisao) {
    params.set("patrocinio_decisao", filters.patrocinio_decisao);
  }
  if (typeof filters.patrocinio_suspeita_devolucao === "boolean") {
    params.set(
      "patrocinio_suspeita_devolucao",
      String(filters.patrocinio_suspeita_devolucao),
    );
  }
  if (filters.patrocinio_review_status) {
    params.set("patrocinio_review_status", filters.patrocinio_review_status);
  }
  if (typeof filters.limit === "number") params.set("limit", String(filters.limit));
  if (typeof filters.offset === "number") params.set("offset", String(filters.offset));

  const qs = params.toString();
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/intakes${qs ? `?${qs}` : ""}`,
  );
  return expectJson<PrazoInicialIntakeListResponse>(response);
}


export async function fetchPrazoInicialDetail(
  intakeId: number,
): Promise<PrazoInicialIntakeDetail> {
  const response = await apiFetch(`/api/v1/prazos-iniciais/intakes/${intakeId}`);
  return expectJson<PrazoInicialIntakeDetail>(response);
}


/**
 * HITL — operador aprova/edita/rejeita decisão de patrocínio da IA.
 *
 * - `aprovado`: aceita a decisão da IA sem alterações; só carimba
 *   reviewed_by/at.
 * - `editado`: operador alterou ao menos um campo (`decisao`,
 *   `outro_advogado_*`, `suspeita_devolucao`, etc.). Só os campos
 *   enviados são atualizados.
 * - `rejeitado`: operador discordou da IA — registra mas NÃO altera os
 *   campos. Operador pode reanalisar manualmente.
 */
export async function patchPrazoInicialPatrocinio(
  intakeId: number,
  payload: PrazoInicialPatrocinioPatch,
): Promise<PrazoInicialPatrocinio> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/intakes/${intakeId}/patrocinio`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  return expectJson<PrazoInicialPatrocinio>(response);
}


/**
 * Sobe um PDF do processo na íntegra (USER_UPLOAD). Aceita também,
 * opcionalmente, o PDF de habilitação MDR — esse é preservado pra
 * GED L1 + AJUS. Backend roda extração mecânica (pdfplumber + extractor
 * PJe TJBA); resposta inclui mensagem traduzida pra UI exibir como toast.
 */
export async function uploadPrazoInicialPdf(
  processoPdf: File,
  habilitacaoPdf?: File | null,
): Promise<PrazoInicialUploadResponse> {
  const formData = new FormData();
  formData.append("processo_pdf", processoPdf);
  if (habilitacaoPdf) {
    formData.append("habilitacao_pdf", habilitacaoPdf);
  }
  const response = await apiFetch("/api/v1/prazos-iniciais/intake/upload", {
    method: "POST",
    body: formData,
  });
  return expectJson<PrazoInicialUploadResponse>(response);
}


export async function reprocessarPrazoInicialCnj(
  intakeId: number,
): Promise<PrazoInicialIntakeSummary> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/intakes/${intakeId}/reprocessar-cnj`,
    { method: "POST" },
  );
  return expectJson<PrazoInicialIntakeSummary>(response);
}


export async function cancelarPrazoInicial(
  intakeId: number,
): Promise<PrazoInicialIntakeSummary> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/intakes/${intakeId}/cancelar`,
    { method: "POST" },
  );
  return expectJson<PrazoInicialIntakeSummary>(response);
}


/**
 * Re-encaminha o intake pra nova classificacao. Apaga sugestoes e
 * pedidos persistidos da rodada anterior, limpa campos derivados pelo
 * classifier, e volta o status pra PRONTO_PARA_CLASSIFICAR. Util pros
 * casos antigos com SEM_DETERMINACAO legado e pros INDETERMINADO em
 * que a integra foi corrigida externamente.
 */
export async function reclassifyPrazoInicial(
  intakeId: number,
): Promise<PrazoInicialIntakeSummary> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/intakes/${intakeId}/reclassify`,
    { method: "POST" },
  );
  return expectJson<PrazoInicialIntakeSummary>(response);
}

/**
 * Tarefas recentes do processo no Legal One (5 mais recentes concluidas
 * + todas as pendentes). Reusa o endpoint de publicacoes —
 * `/publications/groups/{lawsuit_id}/recent-tasks` — porque ele recebe
 * lawsuit_id puro (nao tem nada de publicacao-especifico).
 *
 * Em prazos iniciais, usado no detalhe do intake pra dar contexto ao
 * operador (que outras tarefas existem nesse processo) antes de
 * confirmar agendamentos novos.
 */
export interface L1TaskRecent {
  task_id: number;
  description: string;
  status_id: number;
  status_label: string;
  type_id: number | null;
  type_name: string | null;
  subtype_id: number | null;
  subtype_name: string | null;
  creation_date: string | null;
  end_date_time: string | null;
  effective_end_date_time: string | null;
  l1_url: string;
}

export interface L1RecentTasksResult {
  pending: L1TaskRecent[];
  recent_completed: L1TaskRecent[];
  pending_count: number;
  recent_completed_count: number;
  truncated: boolean;
  check_failed: boolean;
}

export async function fetchRecentTasksForLawsuit(
  lawsuitId: number,
  limit = 5,
): Promise<L1RecentTasksResult> {
  const response = await apiFetch(
    `/api/v1/publications/groups/${lawsuitId}/recent-tasks?limit=${limit}`,
  );
  return expectJson<L1RecentTasksResult>(response);
}

/**
 * Reaplica templates em lote nas sugestoes ja materializadas dos
 * intakes filtrados. NAO chama IA (ao contrario de reclassify) — so
 * re-roda match_templates com a config atual e atualiza
 * task_subtype_id/responsavel/payload. Usado quando operador cadastra
 * template novo e quer aplicar no backlog em AGUARDANDO_CONFIG_TEMPLATE.
 *
 * Suporta dry_run pra preview do impacto antes de confirmar.
 */
export interface ReapplyTemplatesPayload {
  status_in?: string[]; // default: ["AGUARDANDO_CONFIG_TEMPLATE"]
  office_ids?: number[] | null;
  tipos_prazo?: string[] | null;
  dry_run?: boolean;
}

export interface ReapplyTemplatesResult {
  intakes_processed: number;
  intakes_promoted: number;
  sugestoes_updated: number;
  sugestoes_skipped_already_in_l1: number;
  sugestoes_skipped_edited: number;
  sugestoes_no_match: number;
  intake_ids_processed: number[];
  intake_ids_promoted: number[];
  dry_run: boolean;
}

export async function reapplyPrazosIniciaisTemplates(
  payload: ReapplyTemplatesPayload,
): Promise<ReapplyTemplatesResult> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/intakes/reapply-templates`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  return expectJson<ReapplyTemplatesResult>(response);
}

/**
 * HARD DELETE de um intake (admin only). Apaga registro + PDF + cascata.
 * Usado pra reinjetar o mesmo processo do zero durante testes. Vai virar
 * arquivamento depois.
 */
export async function deletePrazoInicialIntake(intakeId: number): Promise<void> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/intakes/${intakeId}`,
    { method: "DELETE" },
  );
  if (!response.ok && response.status !== 204) {
    let detail = "Falha ao deletar intake.";
    try {
      const data = await response.json();
      detail = data?.detail || detail;
    } catch (_) {
      // sem body
    }
    throw new Error(detail);
  }
}


/**
 * Bulk delete (admin only). Best-effort — continua mesmo se algum item
 * falhar. Retorna ids deletados, ids que falharam e erros por id.
 */
export interface BulkDeleteIntakesResult {
  deleted_count: number;
  deleted_ids: number[];
  failed_ids: number[];
  errors: Record<string, string>;
}

export async function bulkDeletePrazoInicialIntakes(
  intakeIds: number[],
): Promise<BulkDeleteIntakesResult> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/intakes/bulk-delete`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ intake_ids: intakeIds }),
    },
  );
  if (!response.ok) {
    let detail = "Falha no bulk delete.";
    try {
      const data = await response.json();
      detail = data?.detail || detail;
    } catch (_) {
      // sem body
    }
    throw new Error(detail);
  }
  return (await response.json()) as BulkDeleteIntakesResult;
}


export interface BulkArchiveIntakesResult {
  archived_count: number;
  archived_ids: number[];
}

/** Arquiva (soft-delete) intakes em lote — por IDs OU criterio (data+status). */
export async function bulkArchivePrazoInicialIntakes(payload: {
  intake_ids?: number[];
  before_date?: string;
  status_in?: string[];
}): Promise<BulkArchiveIntakesResult> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/intakes/bulk-archive`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  if (!response.ok) {
    let detail = "Falha ao arquivar em lote.";
    try {
      const data = await response.json();
      detail = data?.detail || detail;
    } catch (_) {
      // sem body
    }
    throw new Error(detail);
  }
  return (await response.json()) as BulkArchiveIntakesResult;
}


/**
 * Onda 3 #5 — dispara o tratamento web (GED + enqueue cancel da legacy)
 * de um intake AGENDADO/CONCLUIDO_SEM_PROVIDENCIA. Idempotente — se
 * `dispatch_pending` já estiver false, retorna `skipped:true`.
 */
export async function dispatchPrazoInicialTreatmentWeb(
  intakeId: number,
): Promise<{
  intake: PrazoInicialIntakeSummary;
  legacy_task_cancellation_item: unknown | null;
  skipped: boolean;
  reason?: string | null;
}> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/intakes/${intakeId}/dispatch-treatment-web`,
    { method: "POST" },
  );
  return expectJson(response);
}


/**
 * Onda 3 #5/#6 — dispara em lote N intakes pendentes em ordem cronológica.
 * Usado pelo botão "Disparar todos" e pelo worker periódico configurável.
 */
export async function dispatchPrazoInicialPendingBatch(
  batchLimit: number = 10,
): Promise<{
  candidates: number;
  success_count: number;
  skipped_count: number;
  failure_count: number;
  success_ids: number[];
  skipped_ids: number[];
  failed: { intake_id: number; error: string }[];
}> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/intakes/dispatch-pending/process-batch?batch_limit=${batchLimit}`,
    { method: "POST" },
  );
  return expectJson(response);
}


/**
 * Constrói a URL absoluta do PDF do intake (usada como `href` do link "Ver PDF").
 * O browser inclui o cookie/sessão via padrão fetch — mas neste app a auth é por
 * Bearer em header, então o link abre numa nova aba *apenas* se o usuário estiver
 * autenticado; caso contrário baixa o JSON de erro. Para preview confiável, usar
 * `fetchPrazoInicialPdfBlob` + object URL.
 */
export async function confirmarAgendamentoPrazoInicial(
  intakeId: number,
  payload: PrazoInicialSchedulingConfirmationPayload,
): Promise<PrazoInicialSchedulingConfirmationResponse> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/intakes/${intakeId}/confirmar-agendamento`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    },
  );
  return expectJson<PrazoInicialSchedulingConfirmationResponse>(response);
}


// ═══════════════════════════════════════════════════════════════════════
// Classificação em lote (Sonnet / Anthropic Batches) — Onda 1 manual
// ═══════════════════════════════════════════════════════════════════════

/**
 * Dispara um batch de classificação com todos os intakes em
 * PRONTO_PARA_CLASSIFICAR. Aceita `limit` opcional pra cortar o tamanho.
 * Retorna metadados do batch criado (ou flag `submitted=false` se não
 * houver intakes pendentes).
 */
export async function submitPrazosIniciaisClassifyPending(
  limit?: number,
): Promise<PrazoInicialClassifyPendingResponse> {
  const qs = typeof limit === "number" && limit > 0 ? `?limit=${limit}` : "";
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/classificar-pendentes${qs}`,
    { method: "POST" },
  );
  return expectJson<PrazoInicialClassifyPendingResponse>(response);
}

export async function fetchPrazosIniciaisBatches(
  limit = 50,
  offset = 0,
): Promise<PrazoInicialBatchListResponse> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/batches?${params.toString()}`,
  );
  return expectJson<PrazoInicialBatchListResponse>(response);
}

export async function refreshPrazosIniciaisBatch(
  batchId: number,
): Promise<PrazoInicialBatchSummary> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/batches/${batchId}/refresh`,
    { method: "POST" },
  );
  return expectJson<PrazoInicialBatchSummary>(response);
}

export async function applyPrazosIniciaisBatch(
  batchId: number,
): Promise<PrazoInicialApplyBatchResponse> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/batches/${batchId}/apply`,
    { method: "POST" },
  );
  return expectJson<PrazoInicialApplyBatchResponse>(response);
}

/**
 * Recalcula os agregados globais (valor_total_pedido/estimado/
 * aprovisionamento/probabilidade_exito_global) a partir dos pedidos
 * persistidos do intake. Não re-roda Sonnet, não gasta tokens.
 * Útil pra corrigir intakes órfãos de apply antigo.
 */
/**
 * Finaliza o intake SEM criar tarefa no Legal One (Caminho A).
 * Sobe habilitação pro GED, cancela a task legada, marca intake como
 * CONCLUIDO_SEM_PROVIDENCIA. Caso operacional pra processos sem
 * providência necessária (ex.: sentença de improcedência transitada,
 * arquivamento definitivo).
 */
export async function finalizarPrazoInicialSemProvidencia(
  intakeId: number,
  payload: { notes?: string | null; enqueue_legacy_task_cancellation?: boolean } = {},
): Promise<{ intake: Record<string, unknown>; legacy_task_cancellation_item: Record<string, unknown> | null }> {
  const body = {
    notes: payload.notes ?? null,
    enqueue_legacy_task_cancellation: payload.enqueue_legacy_task_cancellation ?? true,
  };
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/intakes/${intakeId}/finalizar-sem-providencia`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  return expectJson(response);
}


export async function recomputePrazoInicialGlobals(
  intakeId: number,
): Promise<{
  intake_id: number;
  valor_total_pedido: number | null;
  valor_total_estimado: number | null;
  aprovisionamento_sugerido: number | null;
  probabilidade_exito_global: string | null;
  pedidos_count: number;
}> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/intakes/${intakeId}/recompute-globals`,
    { method: "POST" },
  );
  return expectJson(response);
}


function _buildLegacyQueueParams(
  filters: PrazoInicialLegacyTaskQueueFilters,
): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.queue_status) params.set("queue_status", filters.queue_status);
  if (typeof filters.limit === "number") params.set("limit", String(filters.limit));
  if (typeof filters.offset === "number") params.set("offset", String(filters.offset));
  if (typeof filters.intake_id === "number") {
    params.set("intake_id", String(filters.intake_id));
  }
  if (filters.cnj_number) params.set("cnj_number", filters.cnj_number);
  if (filters.since) params.set("since", filters.since);
  if (filters.until) params.set("until", filters.until);
  return params;
}


export async function fetchPrazosIniciaisLegacyTaskCancelQueue(
  filters: PrazoInicialLegacyTaskQueueFilters = {},
): Promise<PrazoInicialLegacyTaskCancelQueueListResponse> {
  const qs = _buildLegacyQueueParams(filters).toString();
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/legacy-task-cancel-queue${qs ? `?${qs}` : ""}`,
  );
  return expectJson<PrazoInicialLegacyTaskCancelQueueListResponse>(response);
}


export async function processPrazosIniciaisLegacyTaskCancelQueue(
  limit = 20,
): Promise<PrazoInicialLegacyTaskQueueProcessResponse> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/legacy-task-cancel-queue/process-pending?limit=${limit}`,
    { method: "POST" },
  );
  return expectJson<PrazoInicialLegacyTaskQueueProcessResponse>(response);
}


export async function fetchPrazosIniciaisLegacyTaskCancelQueueMetrics(
  hours = 24,
): Promise<PrazoInicialLegacyTaskQueueMetrics> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/legacy-task-cancel-queue/metrics?hours=${hours}`,
  );
  return expectJson<PrazoInicialLegacyTaskQueueMetrics>(response);
}


export async function reprocessPrazosIniciaisLegacyTaskCancelItem(
  itemId: number,
): Promise<PrazoInicialLegacyTaskQueueItemActionResponse> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/legacy-task-cancel-queue/items/${itemId}/reprocessar`,
    { method: "POST" },
  );
  return expectJson<PrazoInicialLegacyTaskQueueItemActionResponse>(response);
}


export async function cancelPrazosIniciaisLegacyTaskCancelItem(
  itemId: number,
): Promise<PrazoInicialLegacyTaskQueueItemActionResponse> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/legacy-task-cancel-queue/items/${itemId}/cancelar`,
    { method: "POST" },
  );
  return expectJson<PrazoInicialLegacyTaskQueueItemActionResponse>(response);
}


export async function resetPrazosIniciaisLegacyTaskCancelCircuitBreaker(): Promise<PrazoInicialLegacyTaskCircuitBreakerResetResponse> {
  const response = await apiFetch(
    "/api/v1/prazos-iniciais/legacy-task-cancel-queue/circuit-breaker/reset",
    { method: "POST" },
  );
  return expectJson<PrazoInicialLegacyTaskCircuitBreakerResetResponse>(response);
}


/**
 * Faz download do CSV da fila respeitando os mesmos filtros do GET.
 * Retorna o Blob pra o caller transformar em download via URL.createObjectURL.
 */
export async function downloadPrazosIniciaisLegacyTaskCancelQueueCsv(
  filters: PrazoInicialLegacyTaskQueueFilters = {},
): Promise<Blob> {
  const qs = _buildLegacyQueueParams(filters).toString();
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/legacy-task-cancel-queue/export.csv${
      qs ? `?${qs}` : ""
    }`,
  );
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
  }
  return response.blob();
}


export function prazoInicialPdfUrl(intakeId: number): string {
  return `/api/v1/prazos-iniciais/intakes/${intakeId}/pdf`;
}


export async function fetchPrazoInicialPdfBlob(intakeId: number): Promise<Blob> {
  const response = await apiFetch(prazoInicialPdfUrl(intakeId));
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
  }
  return response.blob();
}


export function prazoInicialHabilitacaoPdfUrl(intakeId: number): string {
  return `/api/v1/prazos-iniciais/intakes/${intakeId}/habilitacao-pdf`;
}


export async function fetchPrazoInicialHabilitacaoPdfBlob(
  intakeId: number,
): Promise<Blob> {
  const response = await apiFetch(prazoInicialHabilitacaoPdfUrl(intakeId));
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
  }
  return response.blob();
}


/**
 * Re-roda a validacao heuristica da habilitacao do intake (pin023).
 * Util pra reprocessar intakes NAO_VERIFICADO (antigos) ou pra
 * forcar revalidacao apos ajuste de constantes do validator.
 * Status FALHA NAO bloqueia o intake — so sinaliza no painel.
 */
export async function recheckPrazoInicialHabilitacao(
  intakeId: number,
): Promise<PrazoInicialHabilitacaoCheckResult> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/intakes/${intakeId}/habilitacao/recheck`,
    { method: "POST" },
  );
  return expectJson<PrazoInicialHabilitacaoCheckResult>(response);
}


// ─── Templates de Prazos Iniciais ─────────────────────────────────────

/**
 * Retorna os enums que populam os selects do admin de templates (tipos de
 * prazo, naturezas, produtos, subtipos, prioridades, referências de data).
 */
export async function fetchPrazosIniciaisEnums(): Promise<PrazoInicialEnums> {
  const response = await apiFetch("/api/v1/prazos-iniciais/enums");
  return expectJson<PrazoInicialEnums>(response);
}


/**
 * Lista templates com filtros opcionais.
 *
 * Convenção pra "valor nulo":
 *   - `subtipo: ""`             → filtra templates com subtipo NULL (genéricos)
 *   - `natureza_aplicavel: ""`  → filtra templates com natureza NULL (genéricos)
 *   - `office_external_id: 0`   → filtra templates com office NULL (globais)
 *
 * Passar `undefined` omite o filtro (não é enviado no querystring).
 */
export async function listPrazosIniciaisTemplates(
  filters: PrazoInicialTaskTemplateFilters = {},
): Promise<PrazoInicialTaskTemplateListResponse> {
  const params = new URLSearchParams();
  if (filters.tipo_prazo !== undefined) params.set("tipo_prazo", filters.tipo_prazo);
  if (filters.subtipo !== undefined) params.set("subtipo", filters.subtipo);
  if (filters.natureza_aplicavel !== undefined) {
    params.set("natureza_aplicavel", filters.natureza_aplicavel);
  }
  if (typeof filters.office_external_id === "number") {
    params.set("office_external_id", String(filters.office_external_id));
  }
  if (typeof filters.is_active === "boolean") {
    params.set("is_active", String(filters.is_active));
  }
  if (typeof filters.limit === "number") params.set("limit", String(filters.limit));
  if (typeof filters.offset === "number") params.set("offset", String(filters.offset));

  const qs = params.toString();
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/templates${qs ? `?${qs}` : ""}`,
  );
  return expectJson<PrazoInicialTaskTemplateListResponse>(response);
}


export async function getPrazosIniciaisTemplate(
  templateId: number,
): Promise<PrazoInicialTaskTemplate> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/templates/${templateId}`,
  );
  return expectJson<PrazoInicialTaskTemplate>(response);
}


export async function createPrazosIniciaisTemplate(
  payload: PrazoInicialTaskTemplateCreatePayload,
): Promise<PrazoInicialTaskTemplate> {
  const response = await apiFetch("/api/v1/prazos-iniciais/templates", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return expectJson<PrazoInicialTaskTemplate>(response);
}


export async function updatePrazosIniciaisTemplate(
  templateId: number,
  payload: PrazoInicialTaskTemplateUpdatePayload,
): Promise<PrazoInicialTaskTemplate> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/templates/${templateId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  return expectJson<PrazoInicialTaskTemplate>(response);
}


/**
 * Soft-delete: marca is_active=False no backend. Retorna o template atualizado.
 */
export async function deletePrazosIniciaisTemplate(
  templateId: number,
): Promise<PrazoInicialTaskTemplate> {
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/templates/${templateId}`,
    { method: "DELETE" },
  );
  return expectJson<PrazoInicialTaskTemplate>(response);
}



// ─── Reanalisar (Bloco F) ────────────────────────────────────────────
export async function reanalyzePrazoInicial(intakeId: number): Promise<{
  intake_id: number;
  status: string;
  message: string;
}> {
  const res = await apiFetch(`/api/v1/prazos-iniciais/intakes/${intakeId}/reanalisar`, {
    method: "POST",
  });
  return expectJson(res);
}

// ─── Export XLSX (Bloco F) ───────────────────────────────────────────
//
// `status` e `office_id` aceitam CSV (mesmo formato do GET /intakes)
// pra que o operador possa exportar exatamente os filtros que ja estao
// na tela — incluindo o filtro padrao multi-status (RECEBIDO,
// PROCESSO_NAO_ENCONTRADO, EM_REVISAO, ...) que vinha vazio antes do
// fix do backend (que tratava o CSV inteiro como um status literal).
export async function exportPrazosIniciaisXlsx(filters: {
  status?: string;
  office_id?: string | number;
  date_from?: string;
  date_to?: string;
} = {}): Promise<Blob> {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.office_id) params.set("office_id", String(filters.office_id));
  if (filters.date_from) params.set("date_from", filters.date_from);
  if (filters.date_to) params.set("date_to", filters.date_to);
  const qs = params.toString() ? `?${params.toString()}` : "";
  // Path = /intakes-export.xlsx (NÃO /intakes/export.xlsx) — o segundo
  // colide com /intakes/{intake_id:int} no backend e devolve 422.
  const res = await apiFetch(
    `/api/v1/prazos-iniciais/intakes-export.xlsx${qs}`,
  );
  if (!res.ok) {
    throw new Error(`HTTP error! status: ${res.status}`);
  }
  return res.blob();
}


// ─── AJUS — códigos de andamento + fila ───────────────────────────────

export async function fetchAjusCodAndamento(
  onlyActive = false,
): Promise<AjusCodAndamento[]> {
  const qs = onlyActive ? "?only_active=true" : "";
  const res = await apiFetch(`/api/v1/ajus/cod-andamento${qs}`);
  return expectJson<AjusCodAndamento[]>(res);
}

export async function createAjusCodAndamento(
  payload: AjusCodAndamentoCreatePayload,
): Promise<AjusCodAndamento> {
  const res = await apiFetch(`/api/v1/ajus/cod-andamento`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return expectJson<AjusCodAndamento>(res);
}

export async function updateAjusCodAndamento(
  id: number,
  payload: AjusCodAndamentoCreatePayload,
): Promise<AjusCodAndamento> {
  const res = await apiFetch(`/api/v1/ajus/cod-andamento/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return expectJson<AjusCodAndamento>(res);
}

export async function deleteAjusCodAndamento(id: number): Promise<void> {
  const res = await apiFetch(`/api/v1/ajus/cod-andamento/${id}`, {
    method: "DELETE",
  });
  if (!res.ok && res.status !== 204) {
    let detail = "Falha ao deletar código.";
    try {
      const data = await res.json();
      detail = data?.detail || detail;
    } catch (_) { /* sem body */ }
    throw new Error(detail);
  }
}

export interface AjusAndamentoFilters {
  status?: string;       // CSV
  cnj_number?: string;
  limit?: number;
  offset?: number;
}

export async function fetchAjusAndamentos(
  filters: AjusAndamentoFilters = {},
): Promise<AjusAndamentoQueueListResponse> {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.cnj_number) params.set("cnj_number", filters.cnj_number);
  if (typeof filters.limit === "number") params.set("limit", String(filters.limit));
  if (typeof filters.offset === "number") params.set("offset", String(filters.offset));
  const qs = params.toString();
  const res = await apiFetch(
    `/api/v1/ajus/andamentos${qs ? `?${qs}` : ""}`,
  );
  return expectJson<AjusAndamentoQueueListResponse>(res);
}

export async function dispatchAjusAndamentosPending(
  batchLimit = 20,
): Promise<AjusDispatchBatchResponse> {
  const res = await apiFetch(
    `/api/v1/ajus/andamentos/dispatch-pending?batch_limit=${batchLimit}`,
    { method: "POST" },
  );
  return expectJson<AjusDispatchBatchResponse>(res);
}

export async function cancelAjusAndamento(itemId: number) {
  const res = await apiFetch(
    `/api/v1/ajus/andamentos/${itemId}/cancel`,
    { method: "POST" },
  );
  return expectJson(res);
}

export async function retryAjusAndamento(itemId: number) {
  const res = await apiFetch(
    `/api/v1/ajus/andamentos/${itemId}/retry`,
    { method: "POST" },
  );
  return expectJson(res);
}

// ─── Classificação AJUS (Chunk 1) ────────────────────────────────────

export async function fetchAjusClassifDefaults(): Promise<AjusClassifDefaults> {
  const res = await apiFetch(`/api/v1/ajus/classificacao/defaults`);
  return expectJson<AjusClassifDefaults>(res);
}

export async function updateAjusClassifDefaults(
  payload: { default_matter: string | null; default_risk_loss_probability: string | null },
): Promise<AjusClassifDefaults> {
  const res = await apiFetch(`/api/v1/ajus/classificacao/defaults`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return expectJson<AjusClassifDefaults>(res);
}

export interface AjusClassifFilters {
  status?: string;        // CSV
  origem?: "intake_auto" | "planilha";
  cnj_search?: string;
  limit?: number;
  offset?: number;
}

export async function fetchAjusClassif(
  filters: AjusClassifFilters = {},
): Promise<AjusClassifQueueListResponse> {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.origem) params.set("origem", filters.origem);
  if (filters.cnj_search) params.set("cnj_search", filters.cnj_search);
  if (typeof filters.limit === "number") params.set("limit", String(filters.limit));
  if (typeof filters.offset === "number") params.set("offset", String(filters.offset));
  const qs = params.toString();
  const res = await apiFetch(
    `/api/v1/ajus/classificacao${qs ? `?${qs}` : ""}`,
  );
  return expectJson<AjusClassifQueueListResponse>(res);
}

export async function fetchAjusClassifItem(itemId: number): Promise<AjusClassifQueueItem> {
  const res = await apiFetch(`/api/v1/ajus/classificacao/${itemId}`);
  return expectJson<AjusClassifQueueItem>(res);
}

export async function updateAjusClassifItem(
  itemId: number,
  payload: AjusClassifQueueUpdatePayload,
): Promise<AjusClassifQueueItem> {
  const res = await apiFetch(`/api/v1/ajus/classificacao/${itemId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return expectJson<AjusClassifQueueItem>(res);
}

export async function cancelAjusClassifItem(itemId: number): Promise<AjusClassifQueueItem> {
  const res = await apiFetch(
    `/api/v1/ajus/classificacao/${itemId}/cancel`,
    { method: "POST" },
  );
  return expectJson<AjusClassifQueueItem>(res);
}

export async function retryAjusClassifItem(itemId: number): Promise<AjusClassifQueueItem> {
  const res = await apiFetch(
    `/api/v1/ajus/classificacao/${itemId}/retry`,
    { method: "POST" },
  );
  return expectJson<AjusClassifQueueItem>(res);
}

export interface AjusClassifRetryBulkResponse {
  retried: number;
  ids: number[];
}

/**
 * Retry em massa de itens da fila de classificacao em status 'erro'.
 * Sem `itemIds` retoma TODOS os erros. Com `itemIds` restringe ao
 * conjunto (intersect com status=erro).
 */
export async function retryAjusClassifErrorsBulk(
  itemIds?: number[],
): Promise<AjusClassifRetryBulkResponse> {
  const body = itemIds && itemIds.length > 0 ? { item_ids: itemIds } : {};
  const res = await apiFetch(`/api/v1/ajus/classificacao/retry-errors`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return expectJson<AjusClassifRetryBulkResponse>(res);
}

export async function uploadAjusClassifXlsx(
  file: File,
  opts: { syncMode?: boolean } = {},
): Promise<AjusClassifUploadResponse> {
  const fd = new FormData();
  fd.append("file", file);
  const syncMode = opts.syncMode ?? true; // default: planilha absoluta
  const url =
    `/api/v1/ajus/classificacao/upload-xlsx` +
    `?sync_mode=${syncMode ? "true" : "false"}`;
  const res = await apiFetch(url, { method: "POST", body: fd });
  return expectJson<AjusClassifUploadResponse>(res);
}

/** URL absoluta pra link de download do template (operador clica direto). */
export function ajusClassifTemplateXlsxUrl(): string {
  return `/api/v1/ajus/classificacao/template.xlsx`;
}

/**
 * Baixa o XLSX modelo de classificacao via apiFetch (com token JWT) e
 * dispara o download no navegador. Necessario porque <a href download>
 * direto nao envia o header Authorization e o endpoint responde 401.
 */
export async function downloadAjusClassifTemplate(): Promise<void> {
  const res = await apiFetch(`/api/v1/ajus/classificacao/template.xlsx`);
  if (!res.ok) {
    throw new Error(`Falha ao baixar template (${res.status})`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = "ajus-classificacao-modelo.xlsx";
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    setTimeout(() => { try { URL.revokeObjectURL(url); } catch { /* noop */ } }, 1000);
  }
}

export interface AjusDebugScreenshot {
  name: string;
  size: number;
  mtime: number;
}

/** Lista screenshots de debug salvos pelo runner em falhas de login. */
export async function listAjusDebugScreenshots(
  accountId: number,
): Promise<AjusDebugScreenshot[]> {
  const res = await apiFetch(
    `/api/v1/ajus/classificacao/sessions/${accountId}/debug-screenshots`,
  );
  const data = await expectJson<{ files: AjusDebugScreenshot[] }>(res);
  return data.files;
}

/** URL absoluta pra abrir um screenshot em nova aba (img tag/<a href>). */
export function ajusDebugScreenshotUrl(
  accountId: number,
  filename: string,
): string {
  return `/api/v1/ajus/classificacao/sessions/${accountId}/debug-screenshots/${encodeURIComponent(filename)}`;
}

/**
 * Baixa o PNG do screenshot via apiFetch (com token JWT) e devolve um
 * blob URL. Necessario porque abrir a URL direta no navegador resulta
 * em "Not authenticated" — o endpoint exige header Authorization.
 *
 * O caller deve chamar URL.revokeObjectURL(blobUrl) quando descartar.
 */
export async function fetchAjusDebugScreenshotBlobUrl(
  accountId: number,
  filename: string,
): Promise<string> {
  const res = await apiFetch(
    `/api/v1/ajus/classificacao/sessions/${accountId}/debug-screenshots/${encodeURIComponent(filename)}`,
  );
  if (!res.ok) {
    throw new Error(`Falha ao buscar screenshot (${res.status})`);
  }
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

// ─── Sessões AJUS (Chunk 2) ─────────────────────────────────────────

export async function fetchAjusSessionConfig(): Promise<AjusSessionConfig> {
  const res = await apiFetch(`/api/v1/ajus/classificacao/sessions/config`);
  return expectJson<AjusSessionConfig>(res);
}

export async function fetchAjusSessions(): Promise<AjusSessionAccount[]> {
  const res = await apiFetch(`/api/v1/ajus/classificacao/sessions`);
  return expectJson<AjusSessionAccount[]>(res);
}

export async function createAjusSession(
  payload: AjusSessionCreatePayload,
): Promise<AjusSessionAccount> {
  const res = await apiFetch(`/api/v1/ajus/classificacao/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return expectJson<AjusSessionAccount>(res);
}

export async function updateAjusSession(
  id: number,
  payload: AjusSessionUpdatePayload,
): Promise<AjusSessionAccount> {
  const res = await apiFetch(`/api/v1/ajus/classificacao/sessions/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return expectJson<AjusSessionAccount>(res);
}

export async function deleteAjusSession(id: number): Promise<void> {
  const res = await apiFetch(`/api/v1/ajus/classificacao/sessions/${id}`, {
    method: "DELETE",
  });
  if (!res.ok && res.status !== 204) {
    let detail = "Falha ao deletar sessão.";
    try {
      const data = await res.json();
      detail = data?.detail || detail;
    } catch (_) { /* sem body */ }
    throw new Error(detail);
  }
}

export async function loginAjusSession(id: number): Promise<AjusSessionAccount> {
  const res = await apiFetch(
    `/api/v1/ajus/classificacao/sessions/${id}/login`,
    { method: "POST" },
  );
  return expectJson<AjusSessionAccount>(res);
}

export async function submitAjusSessionIpCode(
  id: number,
  code: string,
): Promise<AjusSessionAccount> {
  const res = await apiFetch(
    `/api/v1/ajus/classificacao/sessions/${id}/ip-code`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    },
  );
  return expectJson<AjusSessionAccount>(res);
}

export async function logoutAjusSession(id: number): Promise<AjusSessionAccount> {
  const res = await apiFetch(
    `/api/v1/ajus/classificacao/sessions/${id}/logout`,
    { method: "POST" },
  );
  return expectJson<AjusSessionAccount>(res);
}

export async function dispatchAjusClassif(): Promise<AjusClassifDispatchResponse> {
  // Endpoint apenas sinaliza pro ajus-runner pegar a fila — sem
  // query params (batch_per_account fica configurado via env do worker).
  const res = await apiFetch(`/api/v1/ajus/classificacao/dispatch`, {
    method: "POST",
  });
  return expectJson<AjusClassifDispatchResponse>(res);
}

/** Pausa o dispatcher AJUS (itens em curso terminam; novos batches nao sao claimados). */
export async function pauseAjusClassif(): Promise<AjusClassifDefaults> {
  const res = await apiFetch(`/api/v1/ajus/classificacao/pause`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paused: true }),
  });
  return expectJson<AjusClassifDefaults>(res);
}

/** Retoma o dispatcher AJUS apos uma pausa. */
export async function resumeAjusClassif(): Promise<AjusClassifDefaults> {
  const res = await apiFetch(`/api/v1/ajus/classificacao/resume`, {
    method: "POST",
  });
  return expectJson<AjusClassifDefaults>(res);
}

/** Cancela todos os itens pendentes nao-claimados (em curso continuam). */
export async function cancelAjusClassifPendentes(): Promise<AjusClassifCancelResponse> {
  const res = await apiFetch(`/api/v1/ajus/classificacao/cancel-pendentes`, {
    method: "POST",
  });
  return expectJson<AjusClassifCancelResponse>(res);
}

// ════════════════════════════════════════════════════════════════
// Funcoes restauradas 2026-05-07 (truncadas em ae93514).
// ════════════════════════════════════════════════════════════════

/** Lista avisos ativos pendentes de dismiss pro usuario corrente.
 *  Chamado pelo AdminNoticeBar a cada 30s. Retorna array vazio em
 *  401 (sessao expirou) — o componente nem mostra nada nesse caso. */
export async function fetchActiveAdminNotices(): Promise<AdminNoticeActive[]> {
  const res = await apiFetch(`/api/v1/admin/notices/active`);
  if (res.status === 401 || res.status === 403) return [];
  return expectJson<AdminNoticeActive[]>(res);
}

/** Marca aviso como fechado pro usuario corrente (idempotente). */
export async function dismissAdminNotice(noticeId: number): Promise<void> {
  const res = await apiFetch(`/api/v1/admin/notices/${noticeId}/dismiss`, {
    method: "POST",
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} ao dispensar aviso`);
  }
}

/** Registra impressao (avisos exibidos na tela) pro usuario corrente.
 *  Idempotente no backend (upsert em admin_notice_views). Best-effort:
 *  falha de rede e' silenciosa, nao quebra a UI. */
export async function markAdminNoticesSeen(ids: number[]): Promise<void> {
  if (!ids.length) return;
  try {
    await apiFetch(`/api/v1/admin/notices/seen`, {
      method: "POST",
      body: JSON.stringify({ ids }),
    });
  } catch (err) {
    console.warn("markAdminNoticesSeen: falha ao registrar impressao:", err);
  }
}

/** Lista TODOS os avisos (admin only — backend retorna 403 se nao). */
export async function fetchAllAdminNotices(): Promise<AdminNotice[]> {
  const res = await apiFetch(`/api/v1/admin/notices`);
  return expectJson<AdminNotice[]>(res);
}

export async function createAdminNotice(
  payload: AdminNoticeCreatePayload,
): Promise<AdminNotice> {
  const res = await apiFetch(`/api/v1/admin/notices`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return expectJson<AdminNotice>(res);
}

export async function updateAdminNotice(
  id: number,
  payload: AdminNoticeUpdatePayload,
): Promise<AdminNotice> {
  const res = await apiFetch(`/api/v1/admin/notices/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  return expectJson<AdminNotice>(res);
}

export async function deleteAdminNotice(id: number): Promise<void> {
  const res = await apiFetch(`/api/v1/admin/notices/${id}`, {
    method: "DELETE",
  });
  if (!res.ok && res.status !== 204) {
    throw new Error(`HTTP ${res.status} ao apagar aviso`);
  }
}

/** Audiencia de um aviso: quem viu (impressao) e quem confirmou/dispensou.
 *  Admin only — backend retorna 403 caso contrario. */
export async function fetchAdminNoticeAudience(
  id: number,
): Promise<AdminNoticeAudience> {
  const res = await apiFetch(`/api/v1/admin/notices/${id}/audience`);
  return expectJson<AdminNoticeAudience>(res);
}

/**
 * Backfill: enfileira na fila AJUS todos os intakes de prazos iniciais
 * ja' classificados que ainda nao tem item -- pra cobrir os processos
 * antigos anteriores ao auto-enqueue. Idempotente.
 *
 * Use `dry_run: true` pra obter o numero de candidatos antes de mandar
 * valendo. Intakes sem PDF entram na fila marcados pra anexo manual e
 * vem listados em `enqueued_without_pdf`.
 */
export async function backfillAjusFromIntakes(
  payload: AjusBackfillRequest = {},
): Promise<AjusBackfillResponse> {
  const res = await apiFetch(`/api/v1/ajus/andamentos/backfill-from-intakes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return expectJson<AjusBackfillResponse>(res);
}

/**
 * Cria N andamentos de capa (sem anexo de PDF) a partir de uma lista
 * de CNJs. Util pra cancelamento massivo / aviso massivo onde nao ha
 * arquivo associado. Mesmas variaveis comuns do bulk-upload.
 */
export async function bulkCnjAjusAndamentos(
  cnjList: string[],
  vars: AjusBulkVarsPayload,
): Promise<AjusBulkResponse> {
  const body = {
    cnj_list: cnjList,
    cod_andamento_id: vars.cod_andamento_id,
    situacao: vars.situacao ?? null,
    data_evento: vars.data_evento ?? null,
    data_agendamento: vars.data_agendamento ?? null,
    data_fatal: vars.data_fatal ?? null,
    hora_agendamento: vars.hora_agendamento ?? null,
    informacao_template_override: vars.informacao_template_override ?? null,
  };
  const res = await apiFetch(`/api/v1/ajus/andamentos/bulk-cnj`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return expectJson<AjusBulkResponse>(res);
}

/**
 * Upload em lote de PDFs com CNJ no nome do arquivo. Backend extrai
 * o CNJ via regex; arquivos sem CNJ no nome viram "skipped" no
 * retorno (operador re-envia depois). Cada arquivo vira 1 item da
 * fila com o PDF anexado e as variaveis comuns informadas.
 */
export async function bulkUploadAjusAndamentos(
  files: File[],
  vars: AjusBulkVarsPayload,
): Promise<AjusBulkResponse> {
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  fd.append("cod_andamento_id", String(vars.cod_andamento_id));
  if (vars.situacao) fd.append("situacao", vars.situacao);
  if (vars.data_evento) fd.append("data_evento", vars.data_evento);
  if (vars.data_agendamento) fd.append("data_agendamento", vars.data_agendamento);
  if (vars.data_fatal) fd.append("data_fatal", vars.data_fatal);
  if (vars.hora_agendamento) fd.append("hora_agendamento", vars.hora_agendamento);
  if (vars.informacao_template_override) {
    fd.append("informacao_template_override", vars.informacao_template_override);
  }
  const res = await apiFetch(`/api/v1/ajus/andamentos/bulk-upload`, {
    method: "POST",
    body: fd,
  });
  return expectJson<AjusBulkResponse>(res);
}

/**
 * Dispatcha em UMA request um conjunto de itens escolhidos pelo
 * operador (multi-select). Limite: 20 itens por chamada.
 */
export async function dispatchSelectedAjusAndamentos(
  itemIds: number[],
): Promise<AjusDispatchBatchResponse> {
  const res = await apiFetch(`/api/v1/ajus/andamentos/dispatch-selected`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_ids: itemIds }),
  });
  return expectJson<AjusDispatchBatchResponse>(res);
}

/**
 * Faz download do PDF da habilitacao anexado ao item da fila e devolve
 * um Blob URL pra abrir em nova aba ou usar como href. Caller eh
 * responsavel por chamar URL.revokeObjectURL depois.
 *
 * Lança erro com status 410 se o item existe mas nao tem PDF.
 */
export async function fetchAjusAndamentoPdfBlobUrl(
  itemId: number,
): Promise<string> {
  const res = await apiFetch(`/api/v1/ajus/andamentos/${itemId}/pdf`);
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(`Falha ao baixar PDF (HTTP ${res.status}): ${detail}`);
  }
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export async function fetchAjusClassificationBlocklistStats():
  Promise<AjusBlocklistStatsResponse> {
  const res = await apiFetch(
    "/api/v1/ajus/classification-blocklist/stats",
  );
  return expectJson<AjusBlocklistStatsResponse>(res);
}

/**
 * Anexa um PDF a um item existente que estava sem anexo. Retorna o
 * item atualizado (com `has_pdf=true`). Falha 409 se item ja' tem PDF
 * ou esta em status nao elegivel; 413 se PDF ultrapassa 10MB.
 */
export async function uploadAjusAndamentoPdf(
  itemId: number,
  file: File,
): Promise<AjusAndamentoQueueItem> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await apiFetch(`/api/v1/ajus/andamentos/${itemId}/pdf`, {
    method: "POST",
    body: fd,
  });
  return expectJson<AjusAndamentoQueueItem>(res);
}

export async function uploadAjusClassificationBlocklist(
  file: File,
): Promise<AjusBlocklistUploadResponse> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await apiFetch(
    "/api/v1/ajus/classification-blocklist/upload",
    { method: "POST", body: fd },
  );
  return expectJson<AjusBlocklistUploadResponse>(res);
}

/** Baixa o CSV do relatorio com os filtros atuais. Retorna Blob pra
 *  o caller fazer download via createObjectURL. */
export async function downloadPatrocinioRelatorioCsv(
  filters: PatrocinioRelatorioFilters = {},
): Promise<Blob> {
  const params = new URLSearchParams();
  if (filters.since) params.set("since", filters.since);
  if (filters.until) params.set("until", filters.until);
  if (filters.office_id != null) params.set("office_id", String(filters.office_id));
  const res = await apiFetch(
    `/api/v1/prazos-iniciais/patrocinio/relatorio/export.csv?${params.toString()}`,
  );
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} ao baixar CSV do relatorio`);
  }
  return await res.blob();
}

/** Lista paginada do relatorio de devolucoes aprovadas. */
export async function fetchPatrocinioRelatorio(
  filters: PatrocinioRelatorioFilters = {},
): Promise<PatrocinioRelatorioResponse> {
  const params = new URLSearchParams();
  if (filters.since) params.set("since", filters.since);
  if (filters.until) params.set("until", filters.until);
  if (filters.office_id != null) params.set("office_id", String(filters.office_id));
  params.set("limit", String(filters.limit ?? 50));
  params.set("offset", String(filters.offset ?? 0));
  const res = await apiFetch(
    `/api/v1/prazos-iniciais/patrocinio/relatorio?${params.toString()}`,
  );
  return expectJson<PatrocinioRelatorioResponse>(res);
}

export async function fetchPrazosIniciaisLegacyTaskCancelZombies(
  thresholdMinutes = 0,
  limit = 50,
): Promise<PrazoInicialLegacyTaskZombieListResponse> {
  const params = new URLSearchParams();
  if (thresholdMinutes > 0) params.set("threshold_minutes", String(thresholdMinutes));
  params.set("limit", String(limit));
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/legacy-task-cancel-queue/zombies?${params.toString()}`,
  );
  return expectJson<PrazoInicialLegacyTaskZombieListResponse>(response);
}

export async function recoverPrazosIniciaisLegacyTaskCancelZombies(
  thresholdMinutes = 0,
): Promise<PrazoInicialLegacyTaskZombieRecoverResponse> {
  const params = new URLSearchParams();
  if (thresholdMinutes > 0) params.set("threshold_minutes", String(thresholdMinutes));
  const qs = params.toString();
  const response = await apiFetch(
    `/api/v1/prazos-iniciais/legacy-task-cancel-queue/recover-zombies${qs ? `?${qs}` : ""}`,
    { method: "POST" },
  );
  return expectJson<PrazoInicialLegacyTaskZombieRecoverResponse>(response);
}


/** Dispatch pontual: envia 1 item especifico da fila pro AJUS.
 *  Aceita item em status pendente ou erro. Util pra debug + retry pontual. */
export async function dispatchAjusAndamento(
  itemId: number,
): Promise<{
  item_id: number;
  status_final: string;
  success: boolean;
  msg: string | null;
  cod_informacao_judicial: string | null;
}> {
  const res = await apiFetch(
    `/api/v1/ajus/andamentos/${itemId}/dispatch`,
    { method: "POST" },
  );
  return expectJson(res);
}

// ──────────────────────────────────────────────────────────────────────
// Feedback dos usuarios (botao flutuante + painel admin)
// ──────────────────────────────────────────────────────────────────────

import type {
  UserFeedback,
  UserFeedbackCreatePayload,
  UserFeedbackListResponse,
  UserFeedbackStats,
  UserFeedbackUpdatePayload,
} from "@/types/api";

/** Envia feedback livre da app (qualquer JWT). page_url e user_agent
 *  sao capturados pelo componente direto de window.location e
 *  navigator antes de chamar — backend so persiste. */
export async function createUserFeedback(
  payload: UserFeedbackCreatePayload,
): Promise<{ ok: boolean; id: number }> {
  const res = await apiFetch(`/api/v1/feedback`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return expectJson(res);
}

/** Lista feedbacks (admin only) com paginacao + filtros opcionais. */
export async function listUserFeedback(args: {
  limit?: number;
  offset?: number;
  status?: string;
  category?: string;
}): Promise<UserFeedbackListResponse> {
  const params = new URLSearchParams();
  if (args.limit != null) params.set("limit", String(args.limit));
  if (args.offset != null) params.set("offset", String(args.offset));
  if (args.status) params.set("status", args.status);
  if (args.category) params.set("category", args.category);
  const qs = params.toString();
  const url = `/api/v1/admin/feedback${qs ? `?${qs}` : ""}`;
  const res = await apiFetch(url);
  return expectJson<UserFeedbackListResponse>(res);
}

/** Contadores por status/categoria pra header da aba admin. */
export async function fetchUserFeedbackStats(): Promise<UserFeedbackStats> {
  const res = await apiFetch(`/api/v1/admin/feedback/stats`);
  return expectJson<UserFeedbackStats>(res);
}

/** Atualiza status e/ou nota interna de um feedback (admin). */
export async function updateUserFeedback(
  feedbackId: number,
  payload: UserFeedbackUpdatePayload,
): Promise<UserFeedback> {
  const res = await apiFetch(`/api/v1/admin/feedback/${feedbackId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  return expectJson<UserFeedback>(res);
}


// ─────────────────────────────────────────────────────────────────────
// Classificador (diagnostico de carteira) — Fase 2
// Tipos sao inline aqui pra evitar poluir types/api.ts antes da Fase 4
// (que vai introduzir muitos campos de relatorio).
// ─────────────────────────────────────────────────────────────────────

export interface ClassificadorLoteSummary {
  id: number;
  nome: string;
  cliente_nome: string | null;
  descricao: string | null;
  status: string;
  source_summary: Record<string, number> | null;
  filtros_aplicados: Record<string, unknown> | null;
  total_processos: number;
  total_processos_capturados: number;
  total_processos_classificados: number;
  total_processos_com_erro: number;
  valor_total_causa: number | null;
  valor_total_estimado: number | null;
  pcond_total: number | null;
  prob_exito_medio: number | null;
  analise_estrategica_carteira: string | null;
  snapshot_at: string | null;
  captura_l1_started_at: string | null;
  captura_l1_finished_at: string | null;
  classificacao_started_at: string | null;
  classificacao_finished_at: string | null;
  error_message: string | null;
  created_at: string | null;
  created_by_user_id: number | null;
}

export interface ClassificadorLotesListResponse {
  total: number;
  items: ClassificadorLoteSummary[];
}

export interface ClassificadorUploadResponse {
  lote: ClassificadorLoteSummary;
  warnings: string[];
}

export interface ClassificadorFromPiFiltros {
  data_inicio?: string | null;
  data_fim?: string | null;
  office_id?: number | null;
  cliente_nome_match?: string | null;
  statuses?: string[] | null;
}

export interface ClassificadorFromPiPreview {
  count: number;
  sample: Array<{
    id: number;
    cnj_number: string | null;
    status: string;
    received_at: string | null;
    office_id: number | null;
  }>;
  candidate_lotes?: Array<{
    id: number;
    nome: string;
    cliente_nome: string | null;
    status: string;
    total_processos: number;
    matching_intakes: number;
    created_at: string | null;
  }>;
}

export interface ClassificadorProcessoSummary {
  id: number;
  lote_id: number;
  source: string;
  source_intake_id: number | null;
  cnj_number: string | null;
  external_id: string | null;
  produto: string | null;
  categoria_id: number | null;
  subcategoria_id: number | null;
  polo: string | null;
  valor_estimado: number | null;
  pcond_sugerido: number | null;
  prob_exito: number | null;
  confianca: number | null;
  status: string;
  error_message: string | null;
  data_captura_l1: string | null;
  data_classificacao: string | null;
}

export interface ClassificadorProcessosListResponse {
  total: number;
  items: ClassificadorProcessoSummary[];
}

export async function fetchClassificadorLotes(params: {
  status?: string;
  cliente_nome?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<ClassificadorLotesListResponse> {
  const q = new URLSearchParams();
  if (params.status) q.set("status", params.status);
  if (params.cliente_nome) q.set("cliente_nome", params.cliente_nome);
  if (params.limit != null) q.set("limit", String(params.limit));
  if (params.offset != null) q.set("offset", String(params.offset));
  const qs = q.toString();
  const res = await apiFetch(
    `/api/v1/classificador/lotes${qs ? `?${qs}` : ""}`,
  );
  return expectJson<ClassificadorLotesListResponse>(res);
}

export async function createClassificadorLoteUpload(input: {
  nome: string;
  cliente_nome?: string;
  descricao?: string;
  file: File;
}): Promise<ClassificadorUploadResponse> {
  const fd = new FormData();
  fd.append("nome", input.nome);
  if (input.cliente_nome) fd.append("cliente_nome", input.cliente_nome);
  if (input.descricao) fd.append("descricao", input.descricao);
  fd.append("file", input.file);
  const res = await apiFetch("/api/v1/classificador/lotes", {
    method: "POST",
    body: fd,
  });
  return expectJson<ClassificadorUploadResponse>(res);
}

export async function previewClassificadorFromPi(
  filtros: ClassificadorFromPiFiltros,
): Promise<ClassificadorFromPiPreview> {
  const res = await apiFetch(
    "/api/v1/classificador/lotes/from-prazos-iniciais/preview",
    {
      method: "POST",
      body: JSON.stringify(filtros),
    },
  );
  return expectJson<ClassificadorFromPiPreview>(res);
}

export async function createClassificadorLoteFromPi(payload: {
  nome: string;
  cliente_nome?: string;
  descricao?: string;
  filtros: ClassificadorFromPiFiltros;
  merge_into_lote_id?: number;
  reset_classification?: boolean;
  only_new?: boolean;
}): Promise<{
  lote: ClassificadorLoteSummary;
  merge_stats?: {
    atualizados: number;
    criados: number;
    ignorados_ja_no_lote?: number;
    total_no_lote: number;
    reclassificar: boolean;
    only_new?: boolean;
  };
}> {
  const res = await apiFetch(
    "/api/v1/classificador/lotes/from-prazos-iniciais",
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
  return expectJson(res);
}

export async function fetchClassificadorLote(
  loteId: number,
): Promise<ClassificadorLoteSummary> {
  const res = await apiFetch(`/api/v1/classificador/lotes/${loteId}`);
  return expectJson<ClassificadorLoteSummary>(res);
}

// Fila do motor dormente (PDFs do robo)

export interface ClassificadorPendingItem {
  id: number;
  pdf_filename_original: string | null;
  pdf_sha256: string;
  pdf_bytes: number;
  cliente_nome: string | null;
  external_id: string | null;
  cnj_hint: string | null;
  produto: string | null;
  source: string;
  status: string; // PENDENTE | ALOCADO | PROCESSADO | ERRO
  lote_id: number | null;
  processo_id: number | null;
  error_message: string | null;
  received_at: string | null;
  allocated_at: string | null;
  processed_at: string | null;
}

export interface ClassificadorPendingListResponse {
  total: number;
  items: ClassificadorPendingItem[];
}

export async function fetchClassificadorPending(params: {
  status?: string;
  cliente_nome?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<ClassificadorPendingListResponse> {
  const q = new URLSearchParams();
  if (params.status) q.set("status", params.status);
  if (params.cliente_nome) q.set("cliente_nome", params.cliente_nome);
  if (params.limit != null) q.set("limit", String(params.limit));
  if (params.offset != null) q.set("offset", String(params.offset));
  const qs = q.toString();
  const res = await apiFetch(`/api/v1/classificador/pending${qs ? `?${qs}` : ""}`);
  return expectJson<ClassificadorPendingListResponse>(res);
}

export interface ClassificadorPendingMetrics {
  status_counts: {
    pendente: number;
    alocado: number;
    processado: number;
    erro: number;
  };
  throughput: {
    pdfs_hoje: number;
    pdfs_7d: number;
    pdfs_30d: number;
    media_diaria_30d: number;
  };
  latencia_segundos: {
    media_fila_para_lote: number | null;
    media_lote_para_processado: number | null;
    pendente_mais_antigo: number | null;
  };
  taxa_erro: number | null;
  generated_at: string;
}

export async function fetchClassificadorPendingMetrics(): Promise<ClassificadorPendingMetrics> {
  const res = await apiFetch("/api/v1/classificador/pending/metrics");
  return expectJson<ClassificadorPendingMetrics>(res);
}

// Filter options + Dashboard Global (cross-lote)

export interface ClassificadorFilterOptions {
  categorias: Array<{ id: number; nome: string }>;
  produtos: string[];
  naturezas: string[];
  patrocinios: string[];
}

export async function fetchClassificadorFilterOptions(
  loteId: number,
): Promise<ClassificadorFilterOptions> {
  const res = await apiFetch(`/api/v1/classificador/lotes/${loteId}/filter-options`);
  return expectJson<ClassificadorFilterOptions>(res);
}

export interface ClassificadorDashboardGlobal {
  total_lotes: number;
  kpis: {
    total_processos: number;
    total_classificados: number;
    total_com_erro: number;
    valor_total_causa: number | null;
    valor_total_estimado: number | null;
    pcond_total: number | null;
    prob_exito_medio: number | null;
  };
  por_categoria: Array<{
    label: string;
    qtd: number;
    valor_estimado: number | null;
    pcond: number | null;
    prob_exito_medio: number | null;
  }>;
  por_patrocinio: Array<{
    label: string;
    qtd: number;
    valor_estimado: number | null;
    pcond: number | null;
  }>;
  lotes: Array<{
    id: number;
    nome: string;
    cliente_nome: string | null;
    status: string;
    total_processos: number;
    total_classificados: number;
    valor_total_estimado: number | null;
    pcond_total: number | null;
    prob_exito_medio: number | null;
    created_at: string | null;
  }>;
  timeline: Array<{
    date: string;
    qtd_lotes: number;
    qtd_processos: number;
    valor: number;
    pcond: number;
  }>;
  generated_at: string;
  filtros: {
    cliente_nome: string | null;
    start: string | null;
    end: string | null;
    only_classified: boolean;
  };
}

export async function fetchClassificadorDashboardGlobal(params: {
  cliente_nome?: string;
  start?: string;
  end?: string;
  only_classified?: boolean;
  categoria_id?: number;
  produto?: string;
  uf?: string;
  patrocinio?: string;
} = {}): Promise<ClassificadorDashboardGlobal> {
  const q = new URLSearchParams();
  if (params.cliente_nome) q.set("cliente_nome", params.cliente_nome);
  if (params.start) q.set("start", params.start);
  if (params.end) q.set("end", params.end);
  if (params.only_classified) q.set("only_classified", "true");
  if (params.categoria_id != null) q.set("categoria_id", String(params.categoria_id));
  if (params.produto) q.set("produto", params.produto);
  if (params.uf) q.set("uf", params.uf);
  if (params.patrocinio) q.set("patrocinio", params.patrocinio);
  const qs = q.toString();
  const res = await apiFetch(
    `/api/v1/classificador/dashboard-global${qs ? `?${qs}` : ""}`,
  );
  return expectJson<ClassificadorDashboardGlobal>(res);
}

export interface ClassificadorGlobalFilterOptions {
  categorias: Array<{ id: number; nome: string }>;
  produtos: string[];
  naturezas: string[];
  ufs: string[];
  tribunais: string[];
  patrocinios: string[];
  clientes: string[];
}

export async function fetchClassificadorGlobalFilterOptions(): Promise<ClassificadorGlobalFilterOptions> {
  const res = await apiFetch("/api/v1/classificador/dashboard-global/filter-options");
  return expectJson<ClassificadorGlobalFilterOptions>(res);
}

// Dashboard agregado por lote (pro aba "Visao geral")
export interface ClassificadorDashboardKpis {
  total_processos: number;
  total_classificados: number;
  total_com_erro: number;
  valor_total_causa: number | null;
  valor_total_estimado: number | null;
  pcond_total: number | null;
  prob_exito_medio: number | null;
}

export interface ClassificadorDashboardBucket {
  label: string;
  qtd: number;
  valor_estimado: number | null;
  pcond: number | null;
  prob_exito_medio: number | null;
}

export interface ClassificadorDashboardTopProcesso {
  id: number;
  cnj_number: string | null;
  tribunal: string | null;
  valor_estimado: number | null;
  pcond_sugerido: number | null;
  prob_exito: number | null;
  categoria: string | null;
}

export interface ClassificadorDashboardData {
  lote: {
    id: number;
    nome: string;
    cliente_nome: string | null;
    status: string;
    snapshot_at: string | null;
    analise_estrategica_carteira: string | null;
  };
  kpis: ClassificadorDashboardKpis;
  por_categoria: ClassificadorDashboardBucket[];
  por_subcategoria: ClassificadorDashboardBucket[];
  por_patrocinio: ClassificadorDashboardBucket[];
  por_produto: ClassificadorDashboardBucket[];
  por_uf: ClassificadorDashboardBucket[];
  por_tribunal: ClassificadorDashboardBucket[];
  top_n_valor: ClassificadorDashboardTopProcesso[];
  pedidos_por_tipo: Array<{
    tipo_pedido: string;
    qtd: number;
    valor_indicado: number | null;
    valor_estimado: number | null;
    pcond: number | null;
  }>;
  sentencas_resumo: Record<string, number>;
  transito_julgado_resumo: { transitados: number; nao_transitados: number };
  contestacoes_resumo?: {
    total_contestacoes: number;
    genericas: number;
    nao_genericas: number;
    indeterminadas: number;
    pct_genericas: number | null;
    mdr_total: number;
    mdr_genericas: number;
    mdr_nao_genericas: number;
    mdr_pct_genericas: number | null;
    outros_total: number;
    outros_genericas: number;
    outros_nao_genericas: number;
    outros_pct_genericas: number | null;
  };
  audiencias_resumo?: {
    total_audiencias: number;
    processos_com_audiencia: number;
    agendadas_proximos_7_dias: number;
    agendadas_proximos_30_dias: number;
    agendadas_proximos_60_dias: number;
    por_status: Record<string, number>;
    por_tipo: Record<string, number>;
    proximas_lista: Array<{
      processo_id: number;
      cnj_number: string | null;
      data: string;
      hora: string | null;
      tipo: string | null;
      local_ou_link: string | null;
      dias_ate: number;
    }>;
  };
  generated_at: string;
}

export async function fetchClassificadorDashboardData(
  loteId: number,
): Promise<ClassificadorDashboardData> {
  const res = await apiFetch(`/api/v1/classificador/lotes/${loteId}/dashboard-data`);
  return expectJson<ClassificadorDashboardData>(res);
}

// Detalhe completo do processo (pro Drawer)
export interface ClassificadorPedidoDetail {
  id: number;
  tipo_pedido: string;
  natureza: string | null;
  valor_indicado: number | null;
  valor_estimado: number | null;
  fundamentacao_valor: string | null;
  probabilidade_perda: string | null;
  aprovisionamento: number | null;
  fundamentacao_risco: string | null;
}

export interface ClassificadorComparecimento {
  polo: "autor" | "reu" | null;
  advogado_nome: string | null;
  advogado_oab: string | null;
  e_mdr_ou_vinculada: boolean | null;
  parte_representada: string | null;
}

export interface ClassificadorAudiencia {
  data: string | null;       // ISO YYYY-MM-DD
  hora: string | null;       // HH:MM
  tipo: "conciliacao" | "instrucao" | "una" | "outra" | null;
  local_ou_link: string | null;
  status: "agendada" | "realizada" | "cancelada" | "redesignada" | null;
  comparecimentos: ClassificadorComparecimento[];
  resultado: string | null;
  fonte: string | null;
}

export interface ClassificadorProcessoDetail {
  id: number;
  lote_id: number;
  source: string;
  source_intake_id: number | null;
  cnj_number: string | null;
  lawsuit_id: number | null;
  external_id: string | null;
  capa_json: Record<string, unknown> | null;
  polo_ativo: unknown | null;
  polo_passivo: unknown | null;
  integra_json: Record<string, unknown> | null;
  metadata_json: Record<string, unknown> | null;
  natureza_processo: string | null;
  produto: string | null;
  patrocinio_json: Record<string, unknown> | null;
  categoria_id: number | null;
  categoria_nome: string | null;
  subcategoria_id: number | null;
  subcategoria_nome: string | null;
  polo: string | null;
  valor_estimado: number | null;
  pcond_sugerido: number | null;
  prob_exito: number | null;
  justificativa: string | null;
  analise_estrategica: string | null;
  confianca: number | null;
  classificacao_response_json: Record<string, unknown> | null;
  contestacao_existente_json: Record<string, unknown> | null;
  audiencias_json: ClassificadorAudiencia[];
  pdf_path: string | null;
  pdf_sha256: string | null;
  pdf_bytes: number | null;
  pdf_filename_original: string | null;
  pdf_extraction_failed: boolean;
  extractor_used: string | null;
  extraction_confidence: string | null;
  status: string;
  error_message: string | null;
  classification_batch_id: number | null;
  data_captura_l1: string | null;
  data_classificacao: string | null;
  created_at: string | null;
  updated_at: string | null;
  pedidos: ClassificadorPedidoDetail[];
}

export async function fetchClassificadorProcessoDetail(
  loteId: number,
  processoId: number,
): Promise<ClassificadorProcessoDetail> {
  const res = await apiFetch(
    `/api/v1/classificador/lotes/${loteId}/processos/${processoId}`,
  );
  return expectJson<ClassificadorProcessoDetail>(res);
}

export async function fetchClassificadorProcessos(
  loteId: number,
  params: {
    status?: string;
    source?: string;
    categoria_id?: number;
    polo?: string;
    cnj_match?: string;
    produto?: string;
    natureza_processo?: string;
    patrocinio?: string;
    contestacao_existe?: boolean;
    contestacao_generica?: string;  // "true" | "false" | "indeterminada"
    contestacao_apresentada_por_mdr?: boolean;
    limit?: number;
    offset?: number;
  } = {},
): Promise<ClassificadorProcessosListResponse> {
  const q = new URLSearchParams();
  if (params.status) q.set("status", params.status);
  if (params.source) q.set("source", params.source);
  if (params.categoria_id != null) q.set("categoria_id", String(params.categoria_id));
  if (params.polo) q.set("polo", params.polo);
  if (params.cnj_match) q.set("cnj_match", params.cnj_match);
  if (params.produto) q.set("produto", params.produto);
  if (params.natureza_processo) q.set("natureza_processo", params.natureza_processo);
  if (params.patrocinio) q.set("patrocinio", params.patrocinio);
  if (params.contestacao_existe != null) q.set("contestacao_existe", String(params.contestacao_existe));
  if (params.contestacao_generica) q.set("contestacao_generica", params.contestacao_generica);
  if (params.contestacao_apresentada_por_mdr != null) {
    q.set("contestacao_apresentada_por_mdr", String(params.contestacao_apresentada_por_mdr));
  }
  if (params.limit != null) q.set("limit", String(params.limit));
  if (params.offset != null) q.set("offset", String(params.offset));
  const qs = q.toString();
  const res = await apiFetch(
    `/api/v1/classificador/lotes/${loteId}/processos${qs ? `?${qs}` : ""}`,
  );
  return expectJson<ClassificadorProcessosListResponse>(res);
}

export async function deleteClassificadorLote(loteId: number): Promise<void> {
  const res = await apiFetch(`/api/v1/classificador/lotes/${loteId}`, {
    method: "DELETE",
  });
  if (!res.ok && res.status !== 204) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `HTTP error! status: ${res.status}`);
  }
}


// ─── Classificador Fase 3 (PDF intake + classify Sonnet) ─────────────

export interface ClassificadorPdfIntakeResult {
  processo: {
    id: number;
    lote_id: number;
    cnj_number: string | null;
    source: string;
    pdf_filename: string | null;
    pdf_sha256: string | null;
    pdf_bytes: number | null;
    extractor_used: string | null;
    extraction_confidence: string | null;
    pdf_extraction_failed: boolean;
    status: string;
    error_message: string | null;
    capa_json_keys: string[] | null;
    integra_json_keys: string[] | null;
  };
}

export interface ClassificadorBatchSummary {
  id: number;
  lote_id: number;
  anthropic_batch_id: string | null;
  anthropic_status: string | null;
  status: string;
  total_records: number;
  succeeded_count: number;
  errored_count: number;
  expired_count: number;
  canceled_count: number;
  model_used: string | null;
  results_url: string | null;
  error_message: string | null;
  requested_by_email: string | null;
  created_at: string | null;
  submitted_at: string | null;
  ended_at: string | null;
  applied_at: string | null;
}

export interface ClassificadorQuickPdfResult {
  lote: ClassificadorLoteSummary;
  processos: Array<{
    filename: string;
    ok: boolean;
    error_message: string | null;
    processo: ClassificadorPdfIntakeResult["processo"] | null;
  }>;
  summary: { total: number; ok: number; failed: number };
}

export async function createClassificadorLoteFromPdf(
  files: File[],
  opts?: {
    nome?: string;
    cliente_nome?: string;
    cnj_hint?: string;
    produto?: string;
    observacao?: string;
  },
): Promise<ClassificadorQuickPdfResult> {
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  if (opts?.nome) fd.append("nome", opts.nome);
  if (opts?.cliente_nome) fd.append("cliente_nome", opts.cliente_nome);
  if (opts?.cnj_hint) fd.append("cnj_hint", opts.cnj_hint);
  if (opts?.produto) fd.append("produto", opts.produto);
  if (opts?.observacao) fd.append("observacao", opts.observacao);
  const res = await apiFetch("/api/v1/classificador/lotes/quick-pdf", {
    method: "POST",
    body: fd,
  });
  return expectJson<ClassificadorQuickPdfResult>(res);
}

export async function uploadClassificadorProcessoPdf(
  loteId: number,
  file: File,
  opts?: {
    cnj_hint?: string;
    external_id?: string;
    produto?: string;
    observacao?: string;
  },
): Promise<ClassificadorPdfIntakeResult> {
  const fd = new FormData();
  fd.append("file", file);
  if (opts?.cnj_hint) fd.append("cnj_hint", opts.cnj_hint);
  if (opts?.external_id) fd.append("external_id", opts.external_id);
  if (opts?.produto) fd.append("produto", opts.produto);
  if (opts?.observacao) fd.append("observacao", opts.observacao);
  const res = await apiFetch(
    `/api/v1/classificador/lotes/${loteId}/processos/upload-pdf`,
    { method: "POST", body: fd },
  );
  return expectJson<ClassificadorPdfIntakeResult>(res);
}

export async function classifyClassificadorLote(
  loteId: number,
  opts?: { includeErrors?: boolean },
): Promise<{
  batch_id: number;
  anthropic_batch_id: string | null;
  anthropic_status: string | null;
  status: string;
  total_records: number;
  model_used: string | null;
  submitted_at: string | null;
}> {
  const qs = opts?.includeErrors ? "?include_errors=true" : "";
  const res = await apiFetch(
    `/api/v1/classificador/lotes/${loteId}/classify${qs}`,
    { method: "POST" },
  );
  return expectJson(res);
}

export async function cleanupClassificadorPedidosDuplicados(
  loteId: number,
): Promise<{
  lote_id: number;
  processos_afetados: number;
  pedidos_removidos: number;
  total_processos_no_lote: number;
}> {
  const res = await apiFetch(
    `/api/v1/classificador/lotes/${loteId}/cleanup-pedidos-duplicados`,
    { method: "POST" },
  );
  return expectJson(res);
}

export async function backfillClassificadorPartes(
  loteId: number,
): Promise<{
  lote_id: number;
  total_processos_no_lote: number;
  atualizados: number;
  com_partes_em_capa: number;
  sem_capa_json: number;
}> {
  const res = await apiFetch(
    `/api/v1/classificador/lotes/${loteId}/backfill-partes`,
    { method: "POST" },
  );
  return expectJson(res);
}

export async function reExtractClassificadorAudiencias(
  loteId: number,
): Promise<{
  lote_id: number;
  total_processos_no_lote: number;
  atualizados: number;
  processos_com_audiencia: number;
  total_audiencias_extraidas: number;
  sem_texto: number;
}> {
  const res = await apiFetch(
    `/api/v1/classificador/lotes/${loteId}/re-extract-audiencias-from-text`,
    { method: "POST" },
  );
  return expectJson(res);
}

export async function reExtractClassificadorPartes(
  loteId: number,
): Promise<{
  lote_id: number;
  total_processos_no_lote: number;
  atualizados: number;
  com_partes_extraidas: number;
  com_capa_enriquecida: number;
  sem_texto: number;
}> {
  const res = await apiFetch(
    `/api/v1/classificador/lotes/${loteId}/re-extract-partes`,
    { method: "POST" },
  );
  return expectJson(res);
}

export async function gerarAnaliseEstrategica(
  loteId: number,
): Promise<{
  lote_id: number;
  analise_estrategica: string;
  tamanho_chars: number;
}> {
  const res = await apiFetch(
    `/api/v1/classificador/lotes/${loteId}/gerar-analise-estrategica`,
    { method: "POST" },
  );
  return expectJson(res);
}

export async function updateAnaliseEstrategica(
  loteId: number,
  texto: string,
): Promise<{
  lote_id: number;
  analise_estrategica: string | null;
  tamanho_chars: number;
}> {
  const res = await apiFetch(
    `/api/v1/classificador/lotes/${loteId}/analise-estrategica`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ analise_estrategica: texto }),
    },
  );
  return expectJson(res);
}

export async function fetchClassificadorBatches(
  loteId: number,
): Promise<{ total: number; items: ClassificadorBatchSummary[] }> {
  const res = await apiFetch(`/api/v1/classificador/lotes/${loteId}/batches`);
  return expectJson(res);
}

export async function refreshClassificadorBatch(
  batchId: number,
): Promise<ClassificadorBatchSummary> {
  const res = await apiFetch(
    `/api/v1/classificador/batches/${batchId}/refresh-status`,
    { method: "POST" },
  );
  return expectJson<ClassificadorBatchSummary>(res);
}

export async function applyClassificadorBatch(
  batchId: number,
): Promise<{
  batch: ClassificadorBatchSummary;
  result: { succeeded: number; failed: number; skipped: number };
}> {
  const res = await apiFetch(
    `/api/v1/classificador/batches/${batchId}/apply`,
    { method: "POST" },
  );
  return expectJson(res);
}

// ─── Classificador — Relatorios (Fase 4 Round 1) ──────────────────────

export interface ClassificadorRelatorioSummary {
  id: number;
  lote_id?: number;
  formato: string;
  status: string;
  file_bytes: number | null;
  file_sha256?: string | null;
  error_message?: string | null;
  requested_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export async function fetchClassificadorRelatorios(
  loteId: number,
): Promise<{ total: number; items: ClassificadorRelatorioSummary[] }> {
  const res = await apiFetch(`/api/v1/classificador/lotes/${loteId}/relatorios`);
  return expectJson(res);
}

export async function generateClassificadorRelatorio(
  loteId: number,
  formato: "XLSX" | "PDF" = "XLSX",
): Promise<ClassificadorRelatorioSummary> {
  const res = await apiFetch(
    `/api/v1/classificador/lotes/${loteId}/relatorios`,
    {
      method: "POST",
      body: JSON.stringify({ formato }),
    },
  );
  return expectJson<ClassificadorRelatorioSummary>(res);
}

export function downloadClassificadorRelatorioUrl(
  loteId: number,
  relatorioId: number,
): string {
  // URL relativa — apiFetch ja injeta base + headers. Pra download via
  // <a href>, usamos a URL absoluta do backend (sem proxy).
  return `/api/v1/classificador/lotes/${loteId}/relatorios/${relatorioId}/download`;
}

export async function downloadClassificadorRelatorio(
  loteId: number,
  relatorioId: number,
  filename: string,
): Promise<void> {
  const res = await apiFetch(downloadClassificadorRelatorioUrl(loteId, relatorioId));
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `HTTP error! status: ${res.status}`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}


/**
 * Relatório Crítico de Performance (Publicações) — admin-only. Baixa o PDF
 * gerado server-side para o período [dateFrom, dateTo] (datas ISO YYYY-MM-DD).
 * O backend exige período mínimo de 5 dias.
 */
export async function downloadPublicationsPerformanceReport(
  dateFrom: string,
  dateTo: string,
): Promise<void> {
  const url = `/api/v1/publications/performance-report.pdf?date_from=${encodeURIComponent(
    dateFrom,
  )}&date_to=${encodeURIComponent(dateTo)}`;
  const res = await apiFetch(url);
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Erro ao gerar relatório (HTTP ${res.status})`);
  }
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = `relatorio-performance-publicacoes-${dateFrom}_${dateTo}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objectUrl);
}

/**
 * Relatório de Entradas e Tratamento por Escritório (Publicações) — PDF
 * executivo ou planilha Excel do período [dateFrom, dateTo]. `base` define o
 * dia da entrada: data de captura (carga da fila) ou data de publicação (fato no diário).
 */
export async function downloadPublicacoesEntradasEscritorioReport(
  dateFrom: string,
  dateTo: string,
  base: "captura" | "publicacao" = "captura",
  formato: "pdf" | "xlsx" = "pdf",
): Promise<void> {
  const url = `/api/v1/publications/entradas-escritorio-report.${formato}?date_from=${encodeURIComponent(
    dateFrom,
  )}&date_to=${encodeURIComponent(dateTo)}&base=${base}`;
  const res = await apiFetch(url);
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Erro ao gerar relatório (HTTP ${res.status})`);
  }
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = `entradas-tratamento-por-escritorio-${dateFrom}_${dateTo}.${formato}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objectUrl);
}


// ─── Varredura de andamentos (modulo incidental) ─────────────────────

export interface VarreduraOfficeOption {
  external_id: number;
  name: string;
  path: string | null;
  polo_scope: string;
}

export interface VarreduraRun {
  id: number;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  responsible_office_ids: number[];
  window_days: number;
  total_processos: number;
  total_processados: number;
  total_achados: number;
  total_falhas: number;
  triggered_by: string | null;
  error_message: string | null;
}

export interface VarreduraRunListResponse {
  total: number;
  items: VarreduraRun[];
}

export interface VarreduraProcessado {
  id: number;
  run_id: number;
  lawsuit_id: number;
  cnj_number: string | null;
  office_id: number | null;
  queue_status: string;
  attempt_count: number;
  last_attempt_at: string | null;
  completed_at: string | null;
  total_andamentos_lidos: number;
  total_achados: number;
  last_error: string | null;
  last_reason: string | null;
}

export interface VarreduraProcessadoListResponse {
  total: number;
  items: VarreduraProcessado[];
}

export interface VarreduraAchado {
  id: number;
  run_id: number;
  processado_id: number;
  lawsuit_id: number;
  cnj_number: string | null;
  andamento_data: string | null;
  andamento_hora: string | null;
  andamento_tipo: string | null;
  andamento_texto: string;
  andamento_movimentado_por: string | null;
  tipo_evento: string;
  regex_matched: string | null;
  tratado: boolean;
  tratado_em: string | null;
  tratado_por: string | null;
  observacao: string | null;
  created_at: string | null;
}

export interface VarreduraAchadoListResponse {
  total: number;
  items: VarreduraAchado[];
}

export interface VarreduraPattern {
  tipo: string;
  label: string;
  regex: string;
}

export async function fetchVarreduraOffices(): Promise<VarreduraOfficeOption[]> {
  const res = await apiFetch("/api/v1/varredura/offices-disponiveis");
  return expectJson<VarreduraOfficeOption[]>(res);
}

export async function fetchVarreduraPatterns(): Promise<{ patterns: VarreduraPattern[] }> {
  const res = await apiFetch("/api/v1/varredura/patterns");
  return expectJson<{ patterns: VarreduraPattern[] }>(res);
}

export async function createVarreduraRunFromList(payload: {
  identifiers: string[];
  window_days?: number;
}): Promise<{ run: VarreduraRun; unresolved: string[] }> {
  const res = await apiFetch("/api/v1/varredura/runs/from-list", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return expectJson(res);
}

export async function createVarreduraRun(payload: {
  responsible_office_ids: number[];
  window_days?: number;
  max_processos?: number;
}): Promise<VarreduraRun> {
  const res = await apiFetch("/api/v1/varredura/runs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return expectJson<VarreduraRun>(res);
}

export async function fetchVarreduraRuns(params: {
  status?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<VarreduraRunListResponse> {
  const q = new URLSearchParams();
  if (params.status) q.set("status", params.status);
  if (params.limit != null) q.set("limit", String(params.limit));
  if (params.offset != null) q.set("offset", String(params.offset));
  const qs = q.toString();
  const res = await apiFetch(`/api/v1/varredura/runs${qs ? `?${qs}` : ""}`);
  return expectJson<VarreduraRunListResponse>(res);
}

export async function fetchVarreduraRun(runId: number): Promise<VarreduraRun> {
  const res = await apiFetch(`/api/v1/varredura/runs/${runId}`);
  return expectJson<VarreduraRun>(res);
}

export async function fetchVarreduraProcessados(
  runId: number,
  params: { queue_status?: string; limit?: number; offset?: number } = {},
): Promise<VarreduraProcessadoListResponse> {
  const q = new URLSearchParams();
  if (params.queue_status) q.set("queue_status", params.queue_status);
  if (params.limit != null) q.set("limit", String(params.limit));
  if (params.offset != null) q.set("offset", String(params.offset));
  const qs = q.toString();
  const res = await apiFetch(
    `/api/v1/varredura/runs/${runId}/processados${qs ? `?${qs}` : ""}`,
  );
  return expectJson<VarreduraProcessadoListResponse>(res);
}

export async function cancelVarreduraRun(runId: number): Promise<VarreduraRun> {
  const res = await apiFetch(`/api/v1/varredura/runs/${runId}/cancel`, {
    method: "POST",
  });
  return expectJson<VarreduraRun>(res);
}

export async function recoverVarreduraZombies(
  runId: number,
  thresholdMinutes = 10,
): Promise<{ recovered_count: number; threshold_minutes: number }> {
  const q = new URLSearchParams();
  q.set("threshold_minutes", String(thresholdMinutes));
  const res = await apiFetch(
    `/api/v1/varredura/runs/${runId}/recover-zombies?${q.toString()}`,
    { method: "POST" },
  );
  return expectJson(res);
}

export async function fetchVarreduraAchados(params: {
  run_id?: number;
  tipo_evento?: string;
  tratado?: boolean;
  cnj_search?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<VarreduraAchadoListResponse> {
  const q = new URLSearchParams();
  if (params.run_id != null) q.set("run_id", String(params.run_id));
  if (params.tipo_evento) q.set("tipo_evento", params.tipo_evento);
  if (params.tratado != null) q.set("tratado", String(params.tratado));
  if (params.cnj_search) q.set("cnj_search", params.cnj_search);
  if (params.limit != null) q.set("limit", String(params.limit));
  if (params.offset != null) q.set("offset", String(params.offset));
  const qs = q.toString();
  const res = await apiFetch(`/api/v1/varredura/achados${qs ? `?${qs}` : ""}`);
  return expectJson<VarreduraAchadoListResponse>(res);
}

export async function updateVarreduraAchado(
  achadoId: number,
  payload: { tratado: boolean; observacao?: string | null },
): Promise<VarreduraAchado> {
  const res = await apiFetch(`/api/v1/varredura/achados/${achadoId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  return expectJson<VarreduraAchado>(res);
}

export async function downloadVarreduraRunXlsx(runId: number): Promise<void> {
  const res = await apiFetch(`/api/v1/varredura/runs/${runId}/achados.xlsx`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `varredura-${runId}.xlsx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}


// === GED LegalOne (envio em lote de arquivos pro GED do L1) ===

export interface GedUploadBatch {
  id: number;
  nome: string;
  mode: "SINGLE_FILE" | "MULTI_FILE";
  type_id: string | null;
  description: string | null;
  status: string;
  is_terminal: boolean;
  progress_pct: number;
  total_itens: number;
  total_sucesso: number;
  total_erro: number;
  total_pendente: number;
  shared_original_filename: string | null;
  error_message: string | null;
  created_by_user_id: number | null;
  created_at: string | null;
  updated_at: string | null;
  finished_at: string | null;
}

export interface GedUploadItem {
  id: number;
  batch_id: number;
  cnj_number: string | null;
  cnj_masked: string | null;
  lawsuit_id: number | null;
  original_filename: string | null;
  file_ext: string | null;
  size_bytes: number | null;
  status: string;
  ged_document_id: number | null;
  error_message: string | null;
  attempts: number;
  created_at: string | null;
  processed_at: string | null;
}

export interface GedBatchStatus {
  id: number;
  status: string;
  mode: string;
  is_terminal: boolean;
  progress_pct: number;
  total_itens: number;
  total_sucesso: number;
  total_erro: number;
  total_pendente: number;
  finished_at: string | null;
  updated_at: string | null;
}

export interface GedResolveSummary {
  resolved: number;
  nao_encontrado: number;
  total: number;
  invalid_cnjs?: string[];
  duplicates_removed?: number;
  files_sem_cnj?: string[];
}

export interface GedCreateBatchResponse {
  batch: GedUploadBatch;
  resolve_summary: GedResolveSummary;
}

export interface GedBatchListResponse {
  total: number;
  items: GedUploadBatch[];
}

export interface GedItemListResponse {
  total: number;
  items: GedUploadItem[];
}

export interface GedDocumentType {
  type_id: string | null;
  label: string;
}

export async function listGedDocumentTypes(): Promise<GedDocumentType[]> {
  const res = await apiFetch("/api/v1/ged-legalone/document-types");
  const data = await expectJson<{ items: GedDocumentType[] }>(res);
  return data.items;
}

export async function createGedBatchSingle(input: {
  nome: string;
  file: File;
  cnjList: string;
  typeId?: string | null;
  description?: string;
}): Promise<GedCreateBatchResponse> {
  const fd = new FormData();
  fd.append("nome", input.nome);
  fd.append("cnj_list", input.cnjList);
  fd.append("file", input.file);
  if (input.typeId) fd.append("type_id", input.typeId);
  if (input.description) fd.append("description", input.description);
  const res = await apiFetch("/api/v1/ged-legalone/batches/single", {
    method: "POST",
    body: fd,
  });
  return expectJson<GedCreateBatchResponse>(res);
}

export async function createGedBatchMulti(input: {
  nome: string;
  files: File[];
  typeId?: string | null;
  description?: string;
  cnjOverrides?: Record<string, string>;
}): Promise<GedCreateBatchResponse> {
  const fd = new FormData();
  fd.append("nome", input.nome);
  for (const f of input.files) fd.append("files", f);
  if (input.typeId) fd.append("type_id", input.typeId);
  if (input.description) fd.append("description", input.description);
  if (input.cnjOverrides && Object.keys(input.cnjOverrides).length > 0) {
    fd.append("cnj_overrides", JSON.stringify(input.cnjOverrides));
  }
  const res = await apiFetch("/api/v1/ged-legalone/batches/multi", {
    method: "POST",
    body: fd,
  });
  return expectJson<GedCreateBatchResponse>(res);
}

export async function listGedBatches(params: {
  status?: string;
  nome?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<GedBatchListResponse> {
  const q = new URLSearchParams();
  if (params.status) q.set("status", params.status);
  if (params.nome) q.set("nome", params.nome);
  if (params.limit != null) q.set("limit", String(params.limit));
  if (params.offset != null) q.set("offset", String(params.offset));
  const qs = q.toString();
  const res = await apiFetch(`/api/v1/ged-legalone/batches${qs ? `?${qs}` : ""}`);
  return expectJson<GedBatchListResponse>(res);
}

export async function getGedBatch(batchId: number): Promise<GedUploadBatch> {
  const res = await apiFetch(`/api/v1/ged-legalone/batches/${batchId}`);
  return expectJson<GedUploadBatch>(res);
}

export async function getGedBatchStatus(batchId: number): Promise<GedBatchStatus> {
  const res = await apiFetch(`/api/v1/ged-legalone/batches/${batchId}/status`);
  return expectJson<GedBatchStatus>(res);
}

export async function listGedBatchItems(
  batchId: number,
  params: { status?: string; limit?: number; offset?: number } = {},
): Promise<GedItemListResponse> {
  const q = new URLSearchParams();
  if (params.status) q.set("status", params.status);
  if (params.limit != null) q.set("limit", String(params.limit));
  if (params.offset != null) q.set("offset", String(params.offset));
  const qs = q.toString();
  const res = await apiFetch(
    `/api/v1/ged-legalone/batches/${batchId}/items${qs ? `?${qs}` : ""}`,
  );
  return expectJson<GedItemListResponse>(res);
}

export async function retryGedBatchFailed(
  batchId: number,
): Promise<{ batch: GedUploadBatch; re_enqueued: number } & GedResolveSummary> {
  const res = await apiFetch(
    `/api/v1/ged-legalone/batches/${batchId}/retry-failed`,
    { method: "POST" },
  );
  return expectJson<{ batch: GedUploadBatch; re_enqueued: number } & GedResolveSummary>(res);
}

export async function cancelGedBatch(batchId: number): Promise<GedUploadBatch> {
  const res = await apiFetch(`/api/v1/ged-legalone/batches/${batchId}/cancel`, {
    method: "POST",
  });
  return expectJson<GedUploadBatch>(res);
}

export async function deleteGedBatch(batchId: number): Promise<void> {
  const res = await apiFetch(`/api/v1/ged-legalone/batches/${batchId}`, {
    method: "DELETE",
  });
  if (!res.ok && res.status !== 204) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `HTTP error! status: ${res.status}`);
  }
}
