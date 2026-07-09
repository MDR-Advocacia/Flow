import { apiFetch } from "@/lib/api-client";

const BASE = "/api/v1/distribuidos-bb";

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

// ── Tipos ────────────────────────────────────────────────────────────────
export interface DashboardKpis {
  total: number;
  coletados: number;
  ciencia_dada: number;
  distribuidos: number;
  cadastrados: number;
  erros: number;
  revisao: number;
  sem_responsavel: number;
  envolvidos_pendentes: number;
}

export interface RunResumo {
  id: number;
  data_inicial: string | null;
  data_final: string | null;
  status: string;
  confirmar_ciencia: boolean;
  total_coletados: number;
  total_ciencia: number;
  total_distribuidos: number;
  total_cadastrados: number;
  total_erros: number;
  iniciado_em: string | null;
  concluido_em: string | null;
}

export interface DashboardData {
  kpis: DashboardKpis;
  por_status: Record<string, number>;
  por_escritorio: { escritorio: string; total: number }[];
  ultima_run: RunResumo | null;
}

export interface Processo {
  id: number;
  cnj: string | null;
  npj: string | null;
  polo: string | null;
  posicao: string | null;
  natureza: string | null;
  acao: string | null;
  valor_causa: number | null;
  data_ajuizamento: string | null;
  situacao: string | null;
  adverso_principal: string | null;
  responsavel_user_id: number | null;
  responsavel_nome: string | null;
  escritorio_id: number | null;
  escritorio_path: string | null;
  observacao: string | null;
  status: string;
  ciencia_dada_em: string | null;
  l1_lawsuit_id: number | null;
  erro: string | null;
  created_at: string | null;
}

export interface Evento {
  id: number;
  run_id: number | null;
  processo_id: number | null;
  secao: string;
  acao: string | null;
  nivel: string;
  mensagem: string;
  dados: Record<string, unknown> | null;
  created_at: string | null;
}

export interface Envolvido {
  id: number;
  nome: string;
  papel: string | null;
  cpf_cnpj: string | null;
  tipo_pessoa: string | null;
  status_contato: string;
  l1_contact_id: number | null;
}

export interface EnvolvidoEquipe {
  membro_user_id: number;
  nome: string | null;
  classificacao: string;
  origem: string; // "equipe" | "ajuizamento"
}

export interface Auditoria {
  processo: Processo;
  envolvidos: Envolvido[];
  envolvidos_equipe: EnvolvidoEquipe[];
  eventos: Evento[];
}

export interface ResponsavelFila {
  id: number;
  user_id: number;
  nome: string | null;
  ordem: number;
  ativo: boolean;
}

export interface Escritorio {
  id: number;
  nome: string;
  escritorio_path: string;
  criterio_polo: string | null;
  criterio_natureza: string | null;
  responsavel_fixo_user_id: number | null;
  responsavel_fixo_nome: string | null;
  observacao_padrao: string | null;
  ativo: boolean;
  ordem: number;
  responsaveis: ResponsavelFila[];
}

export interface UsuarioL1 {
  id: number;
  name: string;
}

export interface ListaProcessos {
  total: number;
  items: Processo[];
}
export interface ListaEventos {
  total: number;
  items: Evento[];
}

// ── Dashboard / dados ──────────────────────────────────────────────────────
export async function getDashboard(): Promise<DashboardData> {
  return json(await apiFetch(`${BASE}/dashboard`));
}

export async function listarProcessos(params: {
  status?: string;
  escritorio_id?: number;
  busca?: string;
  limit?: number;
  offset?: number;
}): Promise<ListaProcessos> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.escritorio_id) qs.set("escritorio_id", String(params.escritorio_id));
  if (params.busca) qs.set("busca", params.busca);
  qs.set("limit", String(params.limit ?? 50));
  qs.set("offset", String(params.offset ?? 0));
  return json(await apiFetch(`${BASE}/processos?${qs.toString()}`));
}

export async function getAuditoria(processoId: number): Promise<Auditoria> {
  return json(await apiFetch(`${BASE}/processos/${processoId}/auditoria`));
}

export async function listarEventos(params: {
  secao?: string;
  nivel?: string;
  limit?: number;
  offset?: number;
}): Promise<ListaEventos> {
  const qs = new URLSearchParams();
  if (params.secao) qs.set("secao", params.secao);
  if (params.nivel) qs.set("nivel", params.nivel);
  qs.set("limit", String(params.limit ?? 100));
  qs.set("offset", String(params.offset ?? 0));
  return json(await apiFetch(`${BASE}/eventos?${qs.toString()}`));
}

export async function rodarSeed(forcar = false): Promise<{ criado: boolean; nao_resolvidos?: string[] }> {
  return json(await apiFetch(`${BASE}/seed?forcar=${forcar}`, { method: "POST" }));
}

export interface ColetaResposta {
  run_id: number;
  status: string;
  ciencia_efetiva: boolean;
  aviso_ciencia: string | null;
}

export async function dispararColeta(payload: {
  data_inicial?: string;
  data_final?: string;
  confirmar_ciencia: boolean;
  coletar_envolvidos?: boolean;
}): Promise<ColetaResposta> {
  return json(await apiFetch(`${BASE}/coletar`, { method: "POST", body: JSON.stringify(payload) }));
}

export async function getRun(runId: number): Promise<RunResumo & { erro: string | null }> {
  return json(await apiFetch(`${BASE}/runs/${runId}`));
}

export interface TesteOnelog {
  ok: boolean;
  configurado: boolean;
  cookies?: number;
  user_agent?: string | null;
  api_url?: string;
  usuario?: string | null;
  erro: string | null;
}

export async function testarOnelog(): Promise<TesteOnelog> {
  return json(await apiFetch(`${BASE}/testar-onelog`, { method: "POST" }));
}

// ── Configuração (tabelas editáveis) ───────────────────────────────────────
export async function listarUsuarios(busca?: string): Promise<UsuarioL1[]> {
  const qs = busca ? `?busca=${encodeURIComponent(busca)}` : "";
  return json(await apiFetch(`${BASE}/config/usuarios${qs}`));
}

export async function listarEscritorios(): Promise<Escritorio[]> {
  return json(await apiFetch(`${BASE}/config/escritorios`));
}

export interface EscritorioPayload {
  nome?: string;
  escritorio_path?: string;
  criterio_polo?: string | null;
  criterio_natureza?: string | null;
  responsavel_fixo_user_id?: number | null;
  observacao_padrao?: string | null;
  ativo?: boolean;
  ordem?: number;
}

export async function criarEscritorio(payload: EscritorioPayload): Promise<Escritorio> {
  return json(await apiFetch(`${BASE}/config/escritorios`, { method: "POST", body: JSON.stringify(payload) }));
}
export async function editarEscritorio(id: number, payload: EscritorioPayload): Promise<Escritorio> {
  return json(await apiFetch(`${BASE}/config/escritorios/${id}`, { method: "PATCH", body: JSON.stringify(payload) }));
}
export async function desativarEscritorio(id: number): Promise<{ ok: boolean }> {
  return json(await apiFetch(`${BASE}/config/escritorios/${id}`, { method: "DELETE" }));
}
export async function adicionarResponsavel(payload: {
  escritorio_id: number;
  user_id: number;
  ordem?: number;
}): Promise<Escritorio> {
  return json(await apiFetch(`${BASE}/config/responsaveis`, { method: "POST", body: JSON.stringify(payload) }));
}
export async function removerResponsavel(id: number): Promise<{ ok: boolean }> {
  return json(await apiFetch(`${BASE}/config/responsaveis/${id}`, { method: "DELETE" }));
}

// ── Equipes / Envolvidos (por responsável) ─────────────────────────────────
export interface Classificacao {
  id: number;
  nome: string;
  situacao: string | null;
  participante_tipo: string | null;
  position_id_l1: number | null;
}
export interface ResponsavelDistinto {
  user_id: number;
  nome: string | null;
  membros: number;
}
export interface EquipeMembro {
  id: number;
  membro_user_id: number;
  membro_nome: string | null;
  classificacao: string;
  ativo: boolean;
}

export async function listarClassificacoes(): Promise<Classificacao[]> {
  return json(await apiFetch(`${BASE}/config/classificacoes`));
}
export async function listarResponsaveisDistintos(): Promise<ResponsavelDistinto[]> {
  return json(await apiFetch(`${BASE}/config/responsaveis`));
}
export async function listarEquipe(responsavelUserId: number): Promise<EquipeMembro[]> {
  return json(await apiFetch(`${BASE}/config/equipe/${responsavelUserId}`));
}
export async function adicionarMembroEquipe(payload: {
  responsavel_user_id: number;
  membro_user_id: number;
  classificacao: string;
}): Promise<{ id: number; ok: boolean }> {
  return json(await apiFetch(`${BASE}/config/equipe`, { method: "POST", body: JSON.stringify(payload) }));
}
export async function removerMembroEquipe(membroId: number): Promise<{ ok: boolean }> {
  return json(await apiFetch(`${BASE}/config/equipe/${membroId}`, { method: "DELETE" }));
}

// ── Regras de Observação (ativam o workflow no L1) ─────────────────────────
export interface RegraObservacao {
  id: number;
  nome: string;
  criterio_posicao: string | null;
  criterio_natureza: string | null;
  criterio_cnj: string | null;
  texto: string;
  ativo: boolean;
  ordem: number;
}
export interface RegraObservacaoPayload {
  nome?: string;
  criterio_posicao?: string | null;
  criterio_natureza?: string | null;
  criterio_cnj?: string | null;
  texto?: string;
  ativo?: boolean;
  ordem?: number;
}

export async function listarRegrasObservacao(): Promise<RegraObservacao[]> {
  return json(await apiFetch(`${BASE}/config/regras-observacao`));
}
export async function criarRegraObservacao(payload: RegraObservacaoPayload): Promise<RegraObservacao> {
  return json(await apiFetch(`${BASE}/config/regras-observacao`, { method: "POST", body: JSON.stringify(payload) }));
}
export async function editarRegraObservacao(id: number, payload: RegraObservacaoPayload): Promise<RegraObservacao> {
  return json(await apiFetch(`${BASE}/config/regras-observacao/${id}`, { method: "PATCH", body: JSON.stringify(payload) }));
}
export async function removerRegraObservacao(id: number): Promise<{ ok: boolean }> {
  return json(await apiFetch(`${BASE}/config/regras-observacao/${id}`, { method: "DELETE" }));
}
