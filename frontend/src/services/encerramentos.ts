// Serviço do menu "Encerramentos" (rastro dos encerramentos executados no
// Legal One via Sistema de Encerramentos). Self-contained (tipos + chamadas)
// pra não inflar o api.ts gigante — mesmo padrão do onerequest.ts.

import { apiFetch } from "@/lib/api-client";

const BASE = "/api/v1/legalone";

export type EncerramentoStatus =
  | "ok"
  | "ja_encerrado"
  | "nao_encontrado"
  | "conflito"
  | "erro_l1";

export interface EncerramentoL1Item {
  id: number;
  created_at: string | null;
  numero_cnj: string;
  lawsuit_id: number | null;
  status: EncerramentoStatus;
  data_encerramento: string | null;
  motivo_encerramento: string | null;
  operador_nome: string | null;
  operador_email: string | null;
  justificativa: string | null;
  origem: string | null;
  detalhe: string | null;
}

export interface EncerramentoL1ListResponse {
  items: EncerramentoL1Item[];
  total: number;
  page: number;
  page_size: number;
  contadores: Record<EncerramentoStatus, number>;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body?.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export async function listarEncerramentosL1(params: {
  page: number;
  pageSize: number;
  status?: string;
  q?: string;
}): Promise<EncerramentoL1ListResponse> {
  const qs = new URLSearchParams({
    page: String(params.page),
    page_size: String(params.pageSize),
  });
  if (params.status) qs.set("status", params.status);
  if (params.q) qs.set("q", params.q);
  return json(await apiFetch(`${BASE}/encerramentos?${qs.toString()}`));
}

export async function exportarEncerramentosExcel(params: {
  status?: string;
  q?: string;
}): Promise<void> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.q) qs.set("q", params.q);
  const res = await apiFetch(`${BASE}/encerramentos/export?${qs.toString()}`);
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.json())?.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download =
    res.headers
      .get("Content-Disposition")
      ?.match(/filename="?([^"]+)"?/)?.[1] || "encerramentos-legalone.xlsx";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
