// Serviço do módulo "Balanceador de Agenda".
// MOCK: leitura real (diagnóstico/matriz/detalhe do pool); escrita simulada.

import { apiFetch } from "@/lib/api-client";

const BASE = "/api/v1/balanceador";

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

export type Situacao = "atrasado" | "fatal_hoje" | "futuro" | "sem_prazo";

export interface Colaborador {
  id: number;
  nome: string;
  cargo: string | null;
  is_supervisor: boolean;
  atrasado: number;
  fatal_hoje: number;
  futuro: number;
  sem_prazo: number;
  total: number;
}

export interface MatrizItem {
  pessoa_id: number;
  subtipo: string;
  total: number;
  atrasado: number;
  fatal_hoje: number;
}

export interface TarefaDetalhe {
  l1_task_id: number | null;
  subtipo: string | null;
  descricao?: string | null; // assunto/anotações (vem no live; no snapshot é enriquecido à parte)
  cnj?: string | null;
  pasta?: string | null;
  uf?: string | null;
  prazo: string | null;
  situacao: Situacao;
}

// ── LIVE: pendentes de uma pessoa direto do L1 (matriz + detalhe) ──
export interface LivePessoaSub {
  subtipo: string;
  total: number;
  atrasado: number;
  fatal_hoje: number;
}
export interface LivePessoa {
  pessoa_id: number;
  nome: string | null;
  resolvido: boolean;
  total_real?: number | null; // total de pendentes COM prazo no L1 (pode ser > carregadas)
  carregadas?: number; // quantas vieram (teto das mais urgentes)
  capado?: boolean; // true = estourou o teto; há mais além das mais urgentes
  subtipos: LivePessoaSub[];
  tarefas: TarefaDetalhe[];
}
// Destinos recorrentes (preferência aprendida) p/ (origem, subtipo) da fila.
export interface SugestaoFila {
  id: number | null;
  nome: string;
  vezes: number;
}
export async function getSugestoesFila(
  team: string,
  origemPessoaId: number,
  subtipo: string,
): Promise<SugestaoFila[]> {
  const qs = new URLSearchParams({ team, origem_pessoa_id: String(origemPessoaId), subtipo });
  const r = await json<{ sugestoes: SugestaoFila[] }>(await apiFetch(`${BASE}/fila-pref?${qs.toString()}`));
  return r.sugestoes;
}
export async function registrarFilaPref(
  team: string,
  origemPessoaId: number,
  subtipo: string,
  alvos: { id: number; nome: string }[],
): Promise<void> {
  const res = await apiFetch(`${BASE}/fila-pref?team=${team}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ origem_pessoa_id: origemPessoaId, subtipo, alvos }),
  });
  if (!res.ok) throw new Error(`Erro ${res.status} ao salvar recorrência`);
}

// Busca leve de colaboradores (só id+nome) — destinos da fila, SEM carregar as
// tarefas deles (a economia que importa: alvo de fila só recebe).
export interface UsuarioBusca {
  id: number;
  nome: string;
  setor: boolean; // true = roster do time; false = externo (catálogo L1)
}
export async function getUsuarios(team: string, busca: string): Promise<UsuarioBusca[]> {
  const qs = new URLSearchParams({ team, busca });
  const r = await json<{ usuarios: UsuarioBusca[] }>(await apiFetch(`${BASE}/usuarios?${qs.toString()}`));
  return r.usuarios;
}

export async function getLivePessoa(
  team: string,
  pessoaId: number,
  dias: number,
  incluirAtrasadas = true,
): Promise<LivePessoa> {
  const qs = new URLSearchParams({
    team,
    pessoa_id: String(pessoaId),
    dias: String(dias),
    incluir_atrasadas: String(incluirAtrasadas),
  });
  return json(await apiFetch(`${BASE}/live-pessoa?${qs.toString()}`));
}

export async function getDiagnostico(team: string): Promise<Colaborador[]> {
  const r = await json<{ colaboradores: Colaborador[] }>(await apiFetch(`${BASE}/diagnostico?team=${team}`));
  return r.colaboradores;
}

export async function getMatriz(team: string, pessoaIds: number[], dias: number): Promise<MatrizItem[]> {
  const qs = new URLSearchParams({ team, pessoas: pessoaIds.join(","), dias: String(dias) });
  const r = await json<{ matriz: MatrizItem[] }>(await apiFetch(`${BASE}/redistribuir?${qs.toString()}`));
  return r.matriz;
}

export async function getTarefas(
  team: string,
  pessoaId: number,
  subtipo: string,
  dias: number,
): Promise<TarefaDetalhe[]> {
  const qs = new URLSearchParams({ team, pessoa_id: String(pessoaId), subtipo, dias: String(dias) });
  const r = await json<{ tarefas: TarefaDetalhe[] }>(await apiFetch(`${BASE}/tarefas?${qs.toString()}`));
  return r.tarefas;
}

// Descrição (assunto/anotações) ao vivo do L1 — não vem no snapshot.
export async function getDescricoes(team: string, ids: number[]): Promise<Record<number, string | null>> {
  if (!ids.length) return {};
  const qs = new URLSearchParams({ team, ids: ids.join(",") });
  const r = await json<{ descricoes: Record<number, string | null> }>(
    await apiFetch(`${BASE}/descricoes?${qs.toString()}`),
  );
  return r.descricoes;
}

// ── Modelo local de "mudanças pendentes" (escrita simulada no mock) ──
export interface MovePendente {
  id: string; // chave local
  fromId: number;
  fromNome: string;
  toId: number;
  toNome: string;
  subtipo: string;
  qtd: number;
  individual: boolean; // true = tarefas escolhidas a dedo; false = em massa por número
  taskIds?: number[];
}

// ── Log de redistribuição (aba Relatórios) ──
export interface RedistribuicaoLog {
  id: number;
  criado_em: string | null;
  criado_por_nome: string | null;
  total_movimentos: number;
  total_tarefas: number;
  origem: string;
  detalhe: MovePendente[];
}

export async function registrarLog(team: string, movimentos: MovePendente[]): Promise<void> {
  const res = await apiFetch(`${BASE}/log?team=${team}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ movimentos }),
  });
  if (!res.ok) throw new Error(`Erro ${res.status} ao registrar o log`);
}

export async function listarLogs(
  team: string,
  limit = 10,
  offset = 0,
): Promise<{ total: number; logs: RedistribuicaoLog[] }> {
  const qs = new URLSearchParams({ team, limit: String(limit), offset: String(offset) });
  return json(await apiFetch(`${BASE}/logs?${qs.toString()}`));
}

// ── Execuções de reatribuição (jobs) — acompanhamento + histórico ──
export interface ExecucaoJob {
  job_id: string;
  status: "running" | "aborting" | "done";
  dry_run: boolean;
  total: number;
  feito: number;
  reatribuidas: number;
  workflow_bloqueadas: number;
  falhas: number;
  criado_por_nome: string | null;
  iniciado_em: string | null;
  terminado_em: string | null;
}

export interface ExecucaoTarefa {
  task_id: number;
  to_id: number | null;
  to_nome: string | null;
  reason: string;
  resultado: string; // motivo legível (vem do backend)
  http: number | null;
  subtipo?: string | null;
  pasta?: string | null;
  cnj?: string | null;
}

export async function listarExecucoes(
  team: string,
  limit = 10,
  offset = 0,
): Promise<{ total: number; items: ExecucaoJob[] }> {
  const qs = new URLSearchParams({ team, limit: String(limit), offset: String(offset) });
  return json(await apiFetch(`${BASE}/reatribuir/jobs?${qs.toString()}`));
}

export async function getExecucaoDetalhe(team: string, jobId: string): Promise<ExecucaoTarefa[]> {
  const r = await json<{ tarefas: ExecucaoTarefa[] }>(
    await apiFetch(`${BASE}/reatribuir/jobs/${jobId}/detalhe?team=${team}`),
  );
  return r.tarefas;
}

export async function downloadExecucaoExcel(team: string, jobId: string): Promise<void> {
  const res = await apiFetch(`${BASE}/reatribuir/jobs/${jobId}/excel?team=${team}`);
  if (!res.ok) throw new Error(`Erro ${res.status} ao gerar o Excel`);
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  const m = /filename="?([^";]+)"?/.exec(cd);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = m?.[1] || `redistribuicao_${jobId}.xlsx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
}

// ── Reatribuição REAL no L1 (job server-backed com progresso) ──
// Um item = uma tarefa a reatribuir (troca responsável+executante pro to_id).
export interface ReatribuirItem {
  task_id: number;
  to_id: number | null;
  to_nome: string | null;
}

export interface ReatribuirStatus {
  job_id?: string;
  status: "running" | "aborting" | "done" | "not_found";
  dry_run?: boolean;
  total: number;
  feito: number;
  reatribuidas: number; // PATCH normal OK (ou, em dry-run, "seria reatribuída")
  workflow_bloqueadas: number; // API trava (Workflow) — tratamento manual/RPA
  falhas: number;
  detalhe?: { task_id: number; to_id: number | null; to_nome: string | null; reason: string; http: number | null }[];
}

export async function iniciarReatribuicao(
  team: string,
  itens: ReatribuirItem[],
  movimentos: MovePendente[],
  dryRun = false,
): Promise<{ job_id: string; status: ReatribuirStatus }> {
  const qs = new URLSearchParams({ team, dry_run: String(dryRun) });
  const res = await apiFetch(`${BASE}/reatribuir?${qs.toString()}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ itens, movimentos }),
  });
  return json(res);
}

export async function statusReatribuicao(team: string, jobId: string): Promise<ReatribuirStatus> {
  const qs = new URLSearchParams({ team, job_id: jobId });
  return json(await apiFetch(`${BASE}/reatribuir/status?${qs.toString()}`));
}

export async function abortarReatribuicao(team: string, jobId: string): Promise<void> {
  const qs = new URLSearchParams({ team, job_id: jobId });
  await apiFetch(`${BASE}/reatribuir/abort?${qs.toString()}`, { method: "POST" });
}
