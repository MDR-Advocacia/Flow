// Controle de Embargos (Controladoria): visão única dos embargos à execução do
// BB Autor — monitor do tribunal + Publicações com e sem pasta.
// Backend: /api/v1/embargos-execucao/controle (gate do time bb-cadastro).

import { apiFetch } from "@/lib/api-client";

const BASE = "/api/v1/embargos-execucao/controle";

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

export type Etapa = "vigilancia" | "pendente" | "cadastrado" | "descartado";

export const ORIGEM_LABEL: Record<string, string> = {
  TRIBUNAL: "Tribunal",
  PUB_SEM_PASTA: "Publicação sem pasta",
  PUB_NA_PASTA: "Publicação na pasta da execução",
  PUB_INCIDENTE: "Publicação na pasta dos embargos",
  L1: "Legal One",
};

export const IGNORADA_LABEL: Record<string, string> = {
  IGNORADA_MONITORIA: "embargos à monitória",
  IGNORADA_NAO_EMBARGOS_EXECUCAO: "não é embargos à execução (ex.: embargos de declaração)",
  IGNORADA_PROPRIOS_AUTOS: "embargos nos próprios autos (sem processo apartado)",
};

export const ORIGEM_HINT: Record<string, string> = {
  TRIBUNAL: "O advogado não está no processo: o monitor achou os embargos no DataJud/DJEN.",
  PUB_SEM_PASTA: "Chegou intimação dos embargos sem pasta no Legal One (fila sem pasta de Publicações).",
  PUB_NA_PASTA: "A publicação dos embargos caiu na pasta da execução: falta a pasta incidental (falha de cadastro).",
  PUB_INCIDENTE: "A publicação já chegou na pasta dos embargos.",
  L1: "O incidente foi achado direto no Legal One.",
};

export interface ControleCaso {
  tipo: "caso";
  id: number;
  cnj_embargos: string | null;
  pasta_execucao: string | null;
  cnj_execucao: string | null;
  execucao_id: number | null;
  execucao_estado: string | null;
  estado: string;
  falha_cadastro: boolean;
  vinculo_confirmado: boolean;
  origens: string[];
  primeira_origem: string | null;
  embargante: string | null;
  tarefa_l1_id: number | null;
  detectado_em: string | null;
  cadastrado_em: string | null;
  incidente_id: number | null;
  incidente_folder: string | null;
  verificado_l1_em: string | null;
  descartado_motivo: string | null;
  publicacoes: number;
  ultima_publicacao: string | null;
}

export interface ControleVigilancia {
  tipo: "execucao";
  id: number;
  execucao_id: number;
  pasta_execucao: string;
  cnj_execucao: string | null;
  estado: string;
  data_ajuizamento: string | null;
  proxima_consulta: string | null;
  consultas_feitas: number;
  responsavel_nome: string | null;
}

export interface ControleKpis {
  vigilancia: number;
  pendente: number;
  pendente_falha_cadastro: number;
  pendente_sem_execucao: number;
  pendente_a_verificar: number;
  publicacoes_ignoradas: Record<string, number>;
  cadastrado: number;
  descartado: number;
  pendente_por_origem: Record<string, number>;
}

export interface ControleStatus {
  running?: boolean;
  iniciado_em?: string | null;
  origem?: string;
  ultimo?: { em?: string; origem?: string; resumo?: Record<string, number>; erro?: string | null } | null;
}

export interface ControleResponse {
  etapa: Etapa;
  total: number;
  kpis: ControleKpis;
  status: ControleStatus;
  items: (ControleCaso | ControleVigilancia)[];
}

export interface CasoDetalhe {
  caso: ControleCaso;
  execucao: {
    id: number;
    pasta: string;
    cnj: string | null;
    estado: string;
    data_ajuizamento: string | null;
    orgao_nome: string | null;
    responsavel_nome: string | null;
  } | null;
  publicacoes: {
    id: number;
    publicacao_id: number;
    origem: string;
    data_publicacao: string | null;
    status_publicacao: string | null;
    linked_lawsuit_id: number | null;
    trecho: string | null;
  }[];
  candidato: {
    id: number;
    cnj: string;
    nivel: string;
    decisao: string;
    djen_status: string | null;
    djen_trecho: string | null;
    djen_data: string | null;
    orgao_nome: string | null;
    data_ajuizamento: string | null;
  } | null;
  eventos: { id: number; secao: string; nivel: string; mensagem: string; criado_em: string | null }[];
  cadastrado_agora?: boolean;
}

export async function listarControle(params: {
  etapa: Etapa;
  origem?: string;
  so_falha?: boolean;
  sem_execucao?: boolean;
  a_verificar?: boolean;
  busca?: string;
  limit?: number;
  offset?: number;
}): Promise<ControleResponse> {
  const qs = new URLSearchParams({ etapa: params.etapa });
  if (params.origem) qs.set("origem", params.origem);
  if (params.so_falha) qs.set("so_falha", "true");
  if (params.sem_execucao) qs.set("sem_execucao", "true");
  if (params.a_verificar) qs.set("a_verificar", "true");
  if (params.busca) qs.set("busca", params.busca);
  qs.set("limit", String(params.limit ?? 50));
  qs.set("offset", String(params.offset ?? 0));
  return json(await apiFetch(`${BASE}?${qs.toString()}`));
}

export async function getControleStatus(): Promise<ControleStatus> {
  return json(await apiFetch(`${BASE}/status`));
}

export async function sincronizarControle(): Promise<{ ok: boolean; mensagem: string }> {
  return json(await apiFetch(`${BASE}/sincronizar`, { method: "POST" }));
}

export async function getCaso(id: number): Promise<CasoDetalhe> {
  return json(await apiFetch(`${BASE}/casos/${id}`));
}

export async function verificarCaso(id: number): Promise<CasoDetalhe> {
  return json(await apiFetch(`${BASE}/casos/${id}/verificar`, { method: "POST" }));
}

export async function descartarCaso(id: number, motivo: string): Promise<CasoDetalhe> {
  return json(await apiFetch(`${BASE}/casos/${id}/descartar`, { method: "POST", body: JSON.stringify({ motivo }) }));
}

export async function reabrirCaso(id: number): Promise<CasoDetalhe> {
  return json(await apiFetch(`${BASE}/casos/${id}/reabrir`, { method: "POST" }));
}
