// Serviço do módulo "Minha Equipe" (Performance de Equipes).
// Self-contained (tipos + chamadas) pra não inflar o api.ts.

import { apiFetch } from "@/lib/api-client";

const BASE = "/api/v1/performance";

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

export type Categoria = "operacional" | "profundo" | "ruido";

export interface PessoaMetrica {
  id: number;
  nome: string;
  cargo: string | null;
  squad: string | null;
  posicao: string | null;
  concluido: number;
  dias_ativos: number;
  throughput_dia: number;
  no_prazo_pct: number | null;
  cycle_dias: number | null;
  backlog: number;
  operacional_n: number;
  profundo_n: number;
  ruido_n: number;
}

export interface EquipeKpis {
  concluido: number;
  backlog: number;
  pessoas_ativas: number;
  pessoas_total: number;
  no_prazo_pct: number | null;
}

export interface EquipeResponse {
  periodo_dias: number;
  kpis: EquipeKpis;
  pessoas: PessoaMetrica[];
}

export interface MixItem {
  subtipo: string;
  categoria: Categoria;
  volume: number;
  cycle_dias: number | null;
  no_prazo_pct: number | null;
  tempo_tarefa_seg: number | null;
}

export interface RitmoOcio {
  volume: number;
  cadencia_seg: number | null;
  ocio_pct: number | null;
  dias: number;
  oper_share: number | null;
  inicio_h: number | null;
  fim_h: number | null;
}

export interface PassadoKpis {
  concluido: number;
  dias_ativos: number;
  throughput_dia: number;
  no_prazo_pct: number | null;
  cycle_dias: number | null;
}

export interface PendenteTipo {
  subtipo: string;
  categoria: Categoria;
  total: number;
  atrasado: number;
}

export interface UrgenteItem {
  subtipo: string;
  prazo: string | null;
  dias: number | null;
  atrasado: boolean;
  cnj: string | null;
  pasta: string | null;
}

export interface PessoaDetalhe {
  pessoa: { id: number; nome: string; cargo: string | null; squad: string | null; posicao: string | null };
  periodo_dias: number;
  passado: { kpis: PassadoKpis; ritmo: RitmoOcio; mix: MixItem[] };
  futuro: {
    pendente: number;
    atrasado: number;
    sem_prazo: number;
    por_tipo: PendenteTipo[];
    urgentes: UrgenteItem[];
  };
}

export interface TipoItem {
  subtipo: string;
  categoria: Categoria;
  volume: number;
  pessoas: number;
  cycle_dias: number | null;
  densidade: number | null;
}

export async function getEquipe(team: string, days = 30, cargo?: string): Promise<EquipeResponse> {
  const qs = new URLSearchParams({ team, days: String(days) });
  if (cargo) qs.set("cargo", cargo);
  return json(await apiFetch(`${BASE}/equipe?${qs.toString()}`));
}

export async function getCargos(team: string): Promise<string[]> {
  const r = await json<{ cargos: string[] }>(await apiFetch(`${BASE}/cargos?team=${team}`));
  return r.cargos;
}

export async function getPessoa(id: number, team: string, days = 30): Promise<PessoaDetalhe> {
  return json(await apiFetch(`${BASE}/pessoa/${id}?team=${team}&days=${days}`));
}

export async function getTipos(team: string, days = 30): Promise<TipoItem[]> {
  const r = await json<{ tipos: TipoItem[] }>(await apiFetch(`${BASE}/tipos?team=${team}&days=${days}`));
  return r.tipos;
}

export interface VazaoItem {
  id: number;
  nome: string;
  cargo: string | null;
  concluido: number;
  throughput_dia: number;
}

export interface BacklogItem {
  id: number;
  nome: string;
  cargo: string | null;
  backlog: number;
  atrasado: number;
}

export interface JornadaItem {
  id: number;
  nome: string;
  cargo: string | null;
  inicio_h: number;
  fim_h: number;
  hands_on_h: number;
  ocio_pct: number | null;
  dias: number;
  oper_share: number;
}

export interface TopTipoItem {
  subtipo: string;
  categoria: Categoria;
  volume: number;
  pendente: number;
  atrasado: number;
}

export interface DashboardData {
  periodo_dias: number;
  kpis: { atrasado_total: number; backlog_total: number };
  vazao: VazaoItem[];
  backlog: BacklogItem[];
  jornada: JornadaItem[];
  top_tipos: TopTipoItem[];
  board_curado?: boolean; // true = board com seleção fixa do supervisor; false/ausente = top-12 automático
}

export async function getDashboard(team: string, days = 30): Promise<DashboardData> {
  return json(await apiFetch(`${BASE}/dashboard?team=${team}&days=${days}`));
}

// Detalhe (capacity) de UM subtipo do board "Tarefas mais importantes".
export interface SubtipoDetalhe {
  subtipo: string;
  categoria: Categoria;
  concluido: number;
  pendente: number;
  atrasado: number;
  pessoas: number;
  no_prazo_pct: number | null;
  tempo_conclusao_seg: number | null; // latência: cadastro → conclusão (mediana)
  tempo_trabalho_seg: number | null; // esforço: gap entre conclusões do tipo (mediana, capado 30min)
  amostra_trabalho: number;
  periodo_dias: number;
}

export async function getSubtipoDetalhe(team: string, subtipo: string, days = 30): Promise<SubtipoDetalhe> {
  const qs = new URLSearchParams({ team, subtipo, days: String(days) });
  return json(await apiFetch(`${BASE}/subtipo-detalhe?${qs.toString()}`));
}

// Quebra por subtipo do pool de uma pessoa — alimenta a pizza do "Pool pendente".
// O total bate com a barra clicada (mesma definição de atrasado no backend).
export interface PoolPorTipoItem {
  subtipo: string;
  categoria: string;
  total: number;
  mais_antigo: string | null;
}

export interface PoolPorTipo {
  pessoa: { id: number; nome: string; cargo: string | null };
  escopo: "atrasado" | "pendente";
  total: number;
  itens: PoolPorTipoItem[];
}

export async function getPoolPorTipo(
  team: string,
  pessoaId: number,
  escopo: "atrasado" | "pendente" = "atrasado",
): Promise<PoolPorTipo> {
  const qs = new URLSearchParams({ team, escopo });
  return json(await apiFetch(`${BASE}/pessoa/${pessoaId}/atrasadas-por-tipo?${qs.toString()}`));
}

// Preview de duplicadas (mesma pasta + mesmo subtipo): mantém a mais antiga.
export interface DupTarefa {
  task_id: number;
  cadastrado_em: string | null;
  pessoa: string | null;
  prazo: string | null;
}
export interface DupGrupo {
  pasta: string;
  cnj: string | null;
  manter: DupTarefa;
  cancelar: DupTarefa[];
}
export interface DuplicadasResp {
  subtipo: string;
  total_grupos: number;
  total_cancelar: number;
  grupos: DupGrupo[];
}
export async function getDuplicadas(team: string, subtipo: string): Promise<DuplicadasResp> {
  const qs = new URLSearchParams({ team, subtipo });
  return json(await apiFetch(`${BASE}/duplicadas?${qs.toString()}`));
}

// Cancelamento em lote de duplicadas (fase B): job persistido + polling.
export interface CancelStatus {
  job_id: string;
  status: "running" | "aborting" | "done";
  fase: "scanning" | "cancelling" | "done"; // varredura ao vivo → cancelamento
  scan_total: number; // pastas-candidatas a varrer
  scan_feito: number;
  total: number; // tarefas reais a cancelar (definido após a varredura)
  feito: number;
  cancelled: number;
  preservadas: number;
  falhas: number;
  erros: { task_id: number; reason: string | null }[];
}
export async function startCancelarDuplicadas(team: string, subtipo: string): Promise<{ job_id: string }> {
  return json(
    await apiFetch(`${BASE}/duplicadas/cancelar?team=${team}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subtipo }),
    }),
  );
}
export async function abortCancelarDuplicadas(team: string, jobId: string): Promise<void> {
  await apiFetch(`${BASE}/duplicadas/cancelar/abort?team=${team}&job_id=${jobId}`, { method: "POST" });
}
export async function getCancelStatus(team: string, jobId: string): Promise<CancelStatus> {
  const qs = new URLSearchParams({ team, job_id: jobId });
  return json(await apiFetch(`${BASE}/duplicadas/cancelar/status?${qs.toString()}`));
}

// Curadoria do board "Tarefas mais importantes".
export interface BoardConfig {
  curado: boolean;
  subtipos: string[];
}
export interface BoardCatalogoItem {
  subtipo: string;
  volume: number;
}
export async function getBoardTarefas(team: string): Promise<BoardConfig> {
  return json(await apiFetch(`${BASE}/board-tarefas?team=${team}`));
}
export async function getBoardCatalogo(team: string, busca = ""): Promise<BoardCatalogoItem[]> {
  const qs = new URLSearchParams({ team, busca });
  const r = await json<{ subtipos: BoardCatalogoItem[] }>(
    await apiFetch(`${BASE}/board-tarefas/catalogo?${qs.toString()}`),
  );
  return r.subtipos;
}
export async function addBoardTarefa(team: string, subtipo: string): Promise<BoardConfig> {
  return json(
    await apiFetch(`${BASE}/board-tarefas?team=${team}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subtipo }),
    }),
  );
}
export async function removeBoardTarefa(team: string, subtipo: string): Promise<BoardConfig> {
  const qs = new URLSearchParams({ team, subtipo });
  return json(await apiFetch(`${BASE}/board-tarefas?${qs.toString()}`, { method: "DELETE" }));
}

export async function downloadExport(params: {
  escopo: "atrasado" | "pendente" | "concluido";
  team: string;
  days: number;
  pessoa_id?: number;
  subtipo?: string;
}): Promise<void> {
  const qs = new URLSearchParams({ escopo: params.escopo, team: params.team, days: String(params.days) });
  if (params.pessoa_id) qs.set("pessoa_id", String(params.pessoa_id));
  if (params.subtipo) qs.set("subtipo", params.subtipo);
  const res = await apiFetch(`${BASE}/export?${qs.toString()}`);
  if (!res.ok) throw new Error(`Erro ${res.status} ao exportar`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `minha-equipe-${params.escopo}.xlsx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// Baixa o PDF (download via <a>) em vez de window.open: o open roda DEPOIS dos
// ~20s de geração (Sonnet), fora do gesto do usuário, e o navegador bloqueia o
// popup silenciosamente. O download por <a download> funciona após o await.
async function fetchPdfAndDownload(path: string, filename: string): Promise<void> {
  const res = await apiFetch(path);
  if (!res.ok) throw new Error(`Erro ${res.status} ao gerar o relatório`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

export async function abrirRelatorioSetor(days: number): Promise<void> {
  return fetchPdfAndDownload(`${BASE}/relatorio-setor?days=${days}`, "relatorio-minha-equipe-setor.pdf");
}

export async function abrirRelatorioPessoa(id: number, days: number): Promise<void> {
  return fetchPdfAndDownload(`${BASE}/pessoa/${id}/relatorio?days=${days}`, `raio-x-pessoa-${id}.pdf`);
}

// ── Relatórios como job persistente ──
export interface RelatorioItem {
  id: number;
  tipo: string;
  label: string;
  days: number;
  status: "processando" | "pronto" | "erro";
  erro: string | null;
  criado_em: string | null;
  concluido_em: string | null;
}

export async function criarRelatorio(
  tipo: "setor" | "pessoa",
  team: string,
  days: number,
  pessoa_id?: number,
): Promise<{ id: number; label: string; status: string }> {
  return json(
    await apiFetch(`${BASE}/relatorios`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tipo, team, days, pessoa_id }),
    }),
  );
}

export async function listarRelatorios(): Promise<RelatorioItem[]> {
  const r = await json<{ items: RelatorioItem[] }>(await apiFetch(`${BASE}/relatorios`));
  return r.items;
}

export async function downloadRelatorioById(id: number): Promise<void> {
  return fetchPdfAndDownload(`${BASE}/relatorios/${id}/download`, `relatorio-minha-equipe-${id}.pdf`);
}

// ── Ingestão dos dados (download do relatório do L1) ──
export interface SyncRunning {
  running: boolean;
  iniciado_em?: string;
  por?: string | null;
  fase?: string;
}

export interface SyncStatus {
  last_sync: {
    ok: boolean;
    tarefas: number;
    data: string;
    relatorio: string;
    em: string;
    bytes: number;
  } | null;
  ja_sincronizou_hoje: boolean;
  running?: SyncRunning | null;
  server_now?: string;
}

export async function getSyncStatus(): Promise<SyncStatus> {
  return json(await apiFetch(`${BASE}/sync`));
}

export async function triggerSync(): Promise<{ ok: boolean; mensagem: string; running?: boolean }> {
  return json(await apiFetch(`${BASE}/sync`, { method: "POST" }));
}

// ── Manutenção do roster (editor de equipe) ──
export interface RosterPessoa {
  id: number;
  nome: string;
  cargo: string | null;
  equipe: string | null;
  is_supervisor: boolean;
  ativo: boolean;
  squad: string | null;
  posicao: string | null;
  concluido: number;
  pendente: number;
}

export async function getRoster(team: string): Promise<RosterPessoa[]> {
  const r = await json<{ pessoas: RosterPessoa[] }>(await apiFetch(`${BASE}/roster?team=${team}`));
  return r.pessoas;
}

export async function updateRosterPessoa(
  id: number,
  updates: Partial<{ cargo: string; equipe: string; is_supervisor: boolean; ativo: boolean }>,
): Promise<void> {
  const res = await apiFetch(`${BASE}/roster/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(`Erro ${res.status} ao salvar`);
}

export interface Candidato {
  nome: string;
  equipe_atual: string | null;
}

export async function getCandidatos(team: string, busca?: string): Promise<Candidato[]> {
  const qs = new URLSearchParams({ team });
  if (busca) qs.set("busca", busca);
  const r = await json<{ candidatos: Candidato[] }>(await apiFetch(`${BASE}/roster/candidatos?${qs.toString()}`));
  return r.candidatos;
}

export async function adicionarPessoa(nome: string, team: string): Promise<void> {
  const res = await apiFetch(`${BASE}/roster/adicionar`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nome, team }),
  });
  if (!res.ok) throw new Error(`Erro ${res.status} ao adicionar`);
}

export async function excluirPessoa(id: number): Promise<void> {
  const res = await apiFetch(`${BASE}/roster/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Erro ${res.status} ao excluir`);
}

// ── Reagendamentos (adiamentos de prazo) ──
export interface ReagKpis {
  total: number;
  tarefas: number;
  pessoas: number;
  fatais_empurrados: number;
  dias_medio: number;
  dias_max: number;
}
export interface ReagPessoa {
  pessoa_id: number | null;
  nome: string;
  total: number;
  fatais: number;
  dias_medio: number;
  tarefas: number;
}
export interface ReagDia {
  dia: string;
  total: number;
  fatais: number;
}
export interface ReagReincidente {
  l1_task_id: number;
  pessoa: string | null;
  subtipo: string | null;
  pasta: string | null;
  cnj: string | null;
  vezes: number;
  primeiro_prazo: string | null;
  ultimo_prazo: string | null;
  dias_total: number;
}
export interface ReagResumo {
  kpis: ReagKpis;
  por_pessoa: ReagPessoa[];
  por_dia: ReagDia[];
  por_subtipo: { subtipo: string; total: number }[];
  reincidentes: ReagReincidente[];
}

export async function getReagendamentos(team: string, days = 30): Promise<ReagResumo> {
  const qs = new URLSearchParams({ team, days: String(days) });
  return json(await apiFetch(`${BASE}/reagendamentos?${qs.toString()}`));
}

export interface ReagEvento {
  dia: string;
  l1_task_id: number;
  pessoa: string | null;
  subtipo: string | null;
  pasta: string | null;
  cnj: string | null;
  prazo_de: string | null;
  prazo_para: string | null;
  dias_adiado: number | null;
  era_fatal_hoje: boolean;
}
export async function getReagendamentoEventos(
  team: string, params: { pessoaId?: number; days?: number; limit?: number; offset?: number } = {},
): Promise<{ total: number; items: ReagEvento[] }> {
  const qs = new URLSearchParams({ team, days: String(params.days ?? 30) });
  if (params.pessoaId != null) qs.set("pessoa_id", String(params.pessoaId));
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.offset != null) qs.set("offset", String(params.offset));
  return json(await apiFetch(`${BASE}/reagendamentos/eventos?${qs.toString()}`));
}
