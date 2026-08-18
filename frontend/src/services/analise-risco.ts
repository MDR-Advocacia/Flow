// Serviço do painel "Análise de Risco" (aba do BB Réu no Minha Equipe).
// Backend: /api/v1/performance/analise-risco (gate por time, team=bb-reu).

import { apiFetch } from "@/lib/api-client";

const BASE = "/api/v1/performance/analise-risco";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `Erro ${res.status}`;
    try {
      detail = (await res.json())?.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export interface AnaliseRiscoItem {
  id: number;
  l1_task_id: number;
  subtipo: string | null;
  responsavel_nome: string | null;
  cumprida_por_nome: string | null;
  npj: string | null;
  cnj: string | null;
  agendada_em: string | null;
  prazo: string | null;
  status_l1: string | null; // Pendente | Cumprido
  concluida_em: string | null;
  verif_status: string; // PENDENTE | NA_FILA | VERIFICADA | ERRO
  portal_analise_feita: boolean | null;
  portal_estado: string | null;
  portal_exito: string | null;
  portal_verificado_em: string | null;
  divergente: boolean | null;
  trat_status: string | null;
  trat_anotacao: string | null;
  trat_em: string | null;
}

export interface AnaliseRiscoKpis {
  abertas: number;
  vencidas: number;
  cumpridas: number;
  aguardando_verificacao: number;
  divergentes: number;
}

export interface AnaliseRiscoResponse {
  total: number;
  kpis: AnaliseRiscoKpis;
  last_sync_at: string | null;
  subtipos: string[];
  responsaveis: string[];
  items: AnaliseRiscoItem[];
}

export interface AnaliseRiscoParams {
  team: string;
  status_l1?: string;
  responsavel?: string;
  divergente?: boolean;
  busca?: string;
  limit?: number;
  offset?: number;
}

export async function listarAnaliseRisco(
  params: AnaliseRiscoParams,
): Promise<AnaliseRiscoResponse> {
  const qs = new URLSearchParams({ team: params.team });
  if (params.status_l1) qs.set("status_l1", params.status_l1);
  if (params.responsavel) qs.set("responsavel", params.responsavel);
  if (params.divergente !== undefined) qs.set("divergente", String(params.divergente));
  if (params.busca) qs.set("busca", params.busca);
  qs.set("limit", String(params.limit ?? 50));
  qs.set("offset", String(params.offset ?? 0));
  return json(await apiFetch(`${BASE}?${qs.toString()}`));
}

export async function reverificarAnaliseRisco(
  id: number,
  team: string,
): Promise<{ ok: boolean; verif_status: string }> {
  return json(
    await apiFetch(`${BASE}/${id}/reverificar?team=${encodeURIComponent(team)}`, {
      method: "POST",
    }),
  );
}

export async function syncAnaliseRisco(team: string): Promise<{
  fonte: number;
  tarefas: number;
  inseridas: number;
  atualizadas: number;
  enfileiradas_verificacao: number;
  subtipos: string[];
}> {
  return json(await apiFetch(`${BASE}/sync?team=${encodeURIComponent(team)}`, { method: "POST" }));
}
