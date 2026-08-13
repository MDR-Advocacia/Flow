import { apiFetch } from "@/lib/api-client";

const BASE = "/api/v1/publications/onenotify-bb";

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Erro HTTP ${response.status}`);
  }
  return response.json();
}

export interface OneNotifyBBStats {
  total: number;
  matched: number;
  matched_pct: number;
  auto_conciliated: number;
  auto_conciliated_pct: number;
  cnj_divergent: number;
  pending_document: number;
  no_match: number;
}

export interface OneNotifyBBSummary {
  id: number;
  external_group_id: string;
  source: string;
  notify_ids: number[];
  npj?: string;
  data_notificacao?: string;
  notification_date_iso?: string;
  publication_date?: string;
  numero_processo_cnj?: string;
  cnj_publicacao?: string;
  cnj_principal_notify?: string;
  cnj_divergent: boolean;
  adverso_principal?: string;
  posicao_cliente?: string;
  tipos_notificacao: string[];
  flow_status: string;
  action_suggested?: string;
  matched_publication_record_id?: number;
  matched_legal_one_update_id?: number;
  matched_publication_status?: string;
  match_score?: number;
  match_strategy?: string;
  document_summary?: Record<string, unknown>;
}

export interface DiffRow {
  kind: "equal" | "replace" | "delete" | "insert";
  left_line?: number | null;
  right_line?: number | null;
  left: string;
  right: string;
}

export interface OneNotifyBBDetail extends OneNotifyBBSummary {
  andamentos: unknown[];
  documentos?: Record<string, unknown> | null;
  conteudo: Record<string, unknown>;
  raw_payload: Record<string, unknown>;
  text_content: string;
  match_reason?: string;
  matched_publication?: Record<string, unknown> | null;
  diff: {
    score: number;
    rows: DiffRow[];
  };
}

export async function getOneNotifyBBStats(): Promise<OneNotifyBBStats> {
  return json(await apiFetch(`${BASE}/stats`));
}

export async function listOneNotifyBBNotifications(params: {
  status?: string;
  action?: string;
  q?: string;
  limit?: number;
  offset?: number;
}): Promise<{ total: number; limit: number; offset: number; items: OneNotifyBBSummary[] }> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.action) qs.set("action", params.action);
  if (params.q) qs.set("q", params.q);
  qs.set("limit", String(params.limit ?? 50));
  qs.set("offset", String(params.offset ?? 0));
  return json(await apiFetch(`${BASE}/notifications?${qs.toString()}`));
}

export async function getOneNotifyBBDetail(id: number): Promise<OneNotifyBBDetail> {
  return json(await apiFetch(`${BASE}/notifications/${id}`));
}
