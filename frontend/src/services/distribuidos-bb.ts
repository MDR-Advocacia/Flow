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

export interface PlanilhasResumo {
  total: number;
  pendentes: number;
  pool_novos: number;
  pendente_cadastro: number;
  cadastrado_l1: number;
  recentes_pendentes: {
    id: number;
    nome_arquivo: string;
    total_processos: number;
    origem: string;
    created_at: string | null;
  }[];
}

export interface DashboardData {
  kpis: DashboardKpis;
  por_status: Record<string, number>;
  por_escritorio: { escritorio: string; total: number }[];
  ultima_run: RunResumo | null;
  planilhas?: PlanilhasResumo;
  por_natureza?: { natureza: string; total: number }[];
  por_posicao?: { posicao: string; total: number }[];
  por_responsavel?: { responsavel: string; total: number }[];
  por_estado?: { uf: string; total: number }[];
  por_data?: { data: string; total: number }[];
  ultima_passagem?: { data: string | null; capturados: number; status: string } | null;
  por_cliente?: { cliente: string; total: number }[];
}

export interface AtivosLote {
  id: number;
  nome_arquivo: string | null;
  total: number;
  processados: number;
  encontrados: number;
  nao_encontrados: number;
  criados: number;
  duplicados: number;
  invalidos: number;
  status: string; // EM_ANDAMENTO | CONCLUIDO | ERRO
  erro: string | null;
  iniciado_em: string | null;
  concluido_em: string | null;
}

export interface Processo {
  id: number;
  cliente: string; // BB | ATIVOS
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
  planilha_status: string; // NOVO | PENDENTE_CADASTRO | CADASTRADO_L1
  planilha_id: number | null;
  planilha_gerada_em: string | null;
  cadastro_confirmado_em: string | null;
  l1_verificado_em: string | null;
  l1_folder: string | null;
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
  criterio_cliente: string | null;
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
  planilhaStatus?: string;
  posicao?: string;
  cliente?: string;
  cadastroDe?: string;
  cadastroAte?: string;
  limit?: number;
  offset?: number;
}): Promise<ListaProcessos> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.escritorio_id) qs.set("escritorio_id", String(params.escritorio_id));
  if (params.busca) qs.set("busca", params.busca);
  if (params.planilhaStatus) qs.set("planilha_status", params.planilhaStatus);
  if (params.posicao) qs.set("posicao", params.posicao);
  if (params.cliente) qs.set("cliente", params.cliente);
  if (params.cadastroDe) qs.set("cadastro_de", params.cadastroDe);
  if (params.cadastroAte) qs.set("cadastro_ate", params.cadastroAte);
  qs.set("limit", String(params.limit ?? 50));
  qs.set("offset", String(params.offset ?? 0));
  return json(await apiFetch(`${BASE}/processos?${qs.toString()}`));
}

export async function exportarProcessos(params: {
  status?: string;
  escritorio_id?: number;
  busca?: string;
  planilhaStatus?: string;
  posicao?: string;
  cliente?: string;
  cadastroDe?: string;
  cadastroAte?: string;
}): Promise<number> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.escritorio_id) qs.set("escritorio_id", String(params.escritorio_id));
  if (params.busca) qs.set("busca", params.busca);
  if (params.planilhaStatus) qs.set("planilha_status", params.planilhaStatus);
  if (params.posicao) qs.set("posicao", params.posicao);
  if (params.cliente) qs.set("cliente", params.cliente);
  if (params.cadastroDe) qs.set("cadastro_de", params.cadastroDe);
  if (params.cadastroAte) qs.set("cadastro_ate", params.cadastroAte);
  const res = await apiFetch(`${BASE}/processos/exportar?${qs.toString()}`);
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.json())?.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  const total = Number(res.headers.get("X-Total") || "0");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `processos_cadastro_bb.xlsx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  return total;
}

export async function getAuditoria(processoId: number): Promise<Auditoria> {
  return json(await apiFetch(`${BASE}/processos/${processoId}/auditoria`));
}

export async function baixarPlanilha(params: { ids?: number[]; status?: string }): Promise<number> {
  const qs = new URLSearchParams();
  if (params.ids && params.ids.length) qs.set("ids", params.ids.join(","));
  if (params.status) qs.set("status", params.status);
  const res = await apiFetch(`${BASE}/planilha?${qs.toString()}`);
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.json())?.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  const total = Number(res.headers.get("X-Total-Processos") || "0");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "PLANILHA_MIGRACAO_DISTRIBUIDOS_BB.xlsx";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  return total;
}

// ── Histórico de planilhas geradas ────────────────────────────────────────
export interface PlanilhaHist {
  id: number;
  run_id: number | null;
  nome_arquivo: string;
  total_processos: number;
  tamanho_bytes: number;
  origem: string; // AUTOMATICA | MANUAL
  status_origem: string | null;
  subido_legalone: boolean;
  subido_em: string | null;
  subido_por: string | null;
  created_at: string | null;
}

export async function listarPlanilhas(params: {
  apenasPendentes?: boolean;
  limit?: number;
  offset?: number;
}): Promise<{ total: number; pendentes: number; items: PlanilhaHist[] }> {
  const qs = new URLSearchParams();
  if (params.apenasPendentes) qs.set("apenas_pendentes", "true");
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.offset != null) qs.set("offset", String(params.offset));
  return json(await apiFetch(`${BASE}/planilhas?${qs.toString()}`));
}

export async function gerarPlanilhaNoHistorico(params: {
  ids?: number[];
} = {}): Promise<PlanilhaHist> {
  const qs = new URLSearchParams();
  if (params.ids && params.ids.length) qs.set("ids", params.ids.join(","));
  return json(await apiFetch(`${BASE}/planilhas/gerar?${qs.toString()}`, { method: "POST" }));
}

export async function baixarPlanilhaArquivada(id: number, nomeArquivo: string): Promise<void> {
  const res = await apiFetch(`${BASE}/planilhas/${id}/download`);
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
  a.download = nomeArquivo || "planilha.xlsx";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function marcarPlanilhaSubida(id: number, subido: boolean): Promise<PlanilhaHist> {
  return json(
    await apiFetch(`${BASE}/planilhas/${id}/subido`, {
      method: "POST",
      body: JSON.stringify({ subido }),
    }),
  );
}

export interface PlanilhaDetalhe {
  planilha: {
    id: number;
    nome_arquivo: string;
    total_processos: number;
    origem: string;
    subido_legalone: boolean;
    subido_em: string | null;
    created_at: string | null;
  };
  progresso: { total: number; cadastrados: number; pendentes: number };
  processos: Processo[];
}

export async function getPlanilhaDetalhe(id: number): Promise<PlanilhaDetalhe> {
  return json(await apiFetch(`${BASE}/planilhas/${id}`));
}

// ── Duplicados da ingestão Ativos (CNJs que já existem no L1, pulados) ──
export interface DuplicadoAtivos {
  id: number;
  lote_id: number;
  cnj: string;
  cnj_digitos: string;
  motivo: "JA_CADASTRADO" | "REPETIDO_LOTE";
  motivo_label: string;
  parte: string | null;
  l1_lawsuit_id: number | null;
  l1_folder: string | null;
  l1_url: string | null;
  criado_em: string | null;
}

export interface DuplicadosResp {
  total: number;
  items: DuplicadoAtivos[];
  kpis: { total: number; com_pasta: number; ja_cadastrado: number; repetido_lote: number };
}

export async function listarDuplicadosAtivos(params: {
  loteId?: number;
  motivo?: string;
  comPasta?: boolean;
  busca?: string;
  limit?: number;
  offset?: number;
}): Promise<DuplicadosResp> {
  const qs = new URLSearchParams();
  if (params.loteId != null) qs.set("lote_id", String(params.loteId));
  if (params.motivo) qs.set("motivo", params.motivo);
  if (params.comPasta != null) qs.set("com_pasta", String(params.comPasta));
  if (params.busca) qs.set("busca", params.busca);
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.offset != null) qs.set("offset", String(params.offset));
  return json(await apiFetch(`${BASE}/ativos/duplicados?${qs.toString()}`));
}

export async function resolverPastasDuplicados(params: {
  ids?: number[];
  loteId?: number;
}): Promise<{ resolvidos: number; nao_encontrados: number; pendentes: number }> {
  return json(
    await apiFetch(`${BASE}/ativos/duplicados/resolver-pastas`, {
      method: "POST",
      body: JSON.stringify({ ids: params.ids, lote_id: params.loteId }),
    }),
  );
}

// ── Agendamento de tarefa em lote sobre os duplicados ──
export interface AgendTaskType {
  external_id: number;
  name: string;
  subtypes: { external_id: number; name: string }[];
}
export interface AgendUser {
  id: number;
  external_id: number;
  name: string;
  email?: string;
  squads?: { id: number; name: string }[];
}

export async function getTaskTypesMeta(): Promise<AgendTaskType[]> {
  return json(await apiFetch(`/api/v1/task-templates/meta/task-types`));
}
export async function getUsersMeta(): Promise<AgendUser[]> {
  return json(await apiFetch(`/api/v1/task-templates/meta/users`));
}

export interface AgendPreview {
  total_pastas: number;
  sem_pasta: number;
  por_responsavel: { responsavel_id: number; responsavel_nome: string; total: number }[];
}
export async function previewAgendamento(body: {
  duplicado_ids: number[];
  responsavel_ids: number[];
  dividir_igual: boolean;
}): Promise<AgendPreview> {
  return json(await apiFetch(`${BASE}/ativos/duplicados/agendar/preview`, {
    method: "POST", body: JSON.stringify(body),
  }));
}

export interface AgendConfig {
  duplicado_ids: number[];
  responsavel_ids: number[];
  dividir_igual: boolean;
  dry_run: boolean;
  subtype_id: number;
  type_id: number;
  subtipo_nome?: string;
  data_iso: string;
  publish_date_iso?: string;
  office_external_id?: number;
  prioridade: string;
  descricao?: string;
  observacoes?: string;
}
export async function dispararAgendamento(body: AgendConfig): Promise<{ job_id: number; total: number; dry_run: boolean }> {
  return json(await apiFetch(`${BASE}/ativos/duplicados/agendar`, {
    method: "POST", body: JSON.stringify(body),
  }));
}

export interface AgendJobStatus {
  id: number;
  status: "EM_ANDAMENTO" | "CONCLUIDO" | "ERRO";
  dry_run: boolean;
  total: number;
  processados: number;
  criados: number;
  pulados: number;
  falhas: number;
  erro: string | null;
  itens: {
    cnj: string; folder: string | null; lawsuit_id: number;
    responsavel_nome: string; status: string; task_id: number | null; erro: string | null;
  }[];
}
export async function statusAgendamento(jobId: number): Promise<AgendJobStatus> {
  return json(await apiFetch(`${BASE}/ativos/duplicados/agendar/status/${jobId}`));
}

export async function verificarCadastroAgora(): Promise<{
  verificados: number;
  confirmados: number;
  sem_cnj_ignorados: number;
}> {
  return json(await apiFetch(`${BASE}/monitor-cadastro/verificar`, { method: "POST" }));
}

export interface CadastroL1Relatorio {
  dry_run: boolean;
  file: string;
  resultado: string;
  importado_por?: { user_id: number | null; nome: string };
  status_import?: {
    revisingLitigationsCount?: number;
    importingLitigationsErrorsCount?: number;
    importedLitigationCount?: number;
  };
  passos: { passo: string; ok: boolean; message?: string }[];
}

export async function cadastrarPlanilhaL1(id: number, dryRun: boolean): Promise<CadastroL1Relatorio> {
  return json(
    await apiFetch(`${BASE}/planilhas/${id}/cadastrar-l1?dry_run=${dryRun}`, { method: "POST" }),
  );
}

export async function listarEventos(params: {
  secao?: string;
  nivel?: string;
  busca?: string;
  limit?: number;
  offset?: number;
}): Promise<ListaEventos> {
  const qs = new URLSearchParams();
  if (params.secao) qs.set("secao", params.secao);
  if (params.nivel) qs.set("nivel", params.nivel);
  if (params.busca) qs.set("busca", params.busca);
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
  criterio_cliente?: string | null;
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
  criterio_cliente: string | null;
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
  criterio_cliente?: string | null;
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

// ── Classificações / Posições (catálogo) ───────────────────────────────────
export interface ClassificacaoPayload {
  nome?: string;
  situacao?: string | null;
  participante_tipo?: string | null;
  position_id_l1?: number | null;
  ativo?: boolean;
  ordem?: number;
}
export interface ClassificacaoFull extends Classificacao {
  ativo: boolean;
  ordem: number;
}
export async function criarClassificacao(payload: ClassificacaoPayload): Promise<ClassificacaoFull> {
  return json(await apiFetch(`${BASE}/config/classificacoes`, { method: "POST", body: JSON.stringify(payload) }));
}
export async function editarClassificacao(id: number, payload: ClassificacaoPayload): Promise<ClassificacaoFull> {
  return json(await apiFetch(`${BASE}/config/classificacoes/${id}`, { method: "PATCH", body: JSON.stringify(payload) }));
}
export async function removerClassificacao(id: number): Promise<{ ok: boolean }> {
  return json(await apiFetch(`${BASE}/config/classificacoes/${id}`, { method: "DELETE" }));
}

// ── Grupos de Ajuizamento ──────────────────────────────────────────────────
export interface GrupoMembro {
  id: number;
  membro_user_id: number;
  nome: string | null;
  classificacao: string;
}
export interface GrupoAjuizamento {
  id: number;
  nome: string;
  ativo: boolean;
  ordem: number;
  membros: GrupoMembro[];
}
export async function listarGruposAjuizamento(): Promise<GrupoAjuizamento[]> {
  return json(await apiFetch(`${BASE}/config/grupos-ajuizamento`));
}
export async function criarGrupoAjuizamento(nome: string): Promise<GrupoAjuizamento> {
  return json(await apiFetch(`${BASE}/config/grupos-ajuizamento`, { method: "POST", body: JSON.stringify({ nome }) }));
}
export async function editarGrupoAjuizamento(id: number, payload: { nome?: string; ativo?: boolean }): Promise<GrupoAjuizamento> {
  return json(await apiFetch(`${BASE}/config/grupos-ajuizamento/${id}`, { method: "PATCH", body: JSON.stringify(payload) }));
}
export async function removerGrupoAjuizamento(id: number): Promise<{ ok: boolean }> {
  return json(await apiFetch(`${BASE}/config/grupos-ajuizamento/${id}`, { method: "DELETE" }));
}
export async function adicionarMembroGrupo(payload: {
  grupo_id: number;
  membro_user_id: number;
  classificacao: string;
}): Promise<GrupoAjuizamento> {
  return json(await apiFetch(`${BASE}/config/grupos-ajuizamento/membros`, { method: "POST", body: JSON.stringify(payload) }));
}
export async function removerMembroGrupo(id: number): Promise<{ ok: boolean }> {
  return json(await apiFetch(`${BASE}/config/grupo-membros/${id}`, { method: "DELETE" }));
}

// ── Valores Padrão ─────────────────────────────────────────────────────────
export interface ValorPadrao {
  chave: string;
  valor: string | null;
  descricao: string | null;
}
export async function listarValores(): Promise<ValorPadrao[]> {
  return json(await apiFetch(`${BASE}/config/valores`));
}
export async function atualizarValores(valores: Record<string, string | null>): Promise<ValorPadrao[]> {
  return json(await apiFetch(`${BASE}/config/valores`, { method: "PATCH", body: JSON.stringify({ valores }) }));
}

// ── Ativos: ingestão de lista seca (upload → DataJud) ─────────────────────
export async function importarAtivos(file: File): Promise<{ lote_id: number; total: number }> {
  const fd = new FormData();
  fd.append("arquivo", file);
  return json(await apiFetch(`${BASE}/ativos/importar`, { method: "POST", body: fd }));
}

export async function getLoteAtivos(id: number): Promise<AtivosLote> {
  return json(await apiFetch(`${BASE}/ativos/lotes/${id}`));
}

// ── Pasta avulsa (modal de criação manual — cadastro imediato no L1) ───────
export interface OfficeL1 {
  id: number;
  name: string;
  path: string;
  external_id: number;
}
export async function listarOfficesL1(): Promise<OfficeL1[]> {
  // Lista completa de escritórios responsáveis do L1 (a mesma de Publicações).
  return json(await apiFetch(`/api/v1/offices`));
}

export interface CapaAvulso {
  encontrado: boolean;
  classe?: string | null;
  assunto?: string | null;
  orgao_julgador?: string | null;
  uf?: string | null;
  data_ajuizamento?: string | null;
}
export async function buscarCapaAvulso(cnj: string): Promise<CapaAvulso> {
  return json(await apiFetch(`${BASE}/avulso/capa?cnj=${encodeURIComponent(cnj)}`));
}

export interface SugestaoAvulso {
  tem_fila: boolean;
  responsavel_sugerido_id: number | null;
  responsavel_sugerido_nome: string | null;
  observacao_sugerida: string | null;
}
export async function sugestaoAvulso(params: {
  escritorio_path: string;
  cliente_cpf_cnpj?: string;
  posicao?: string;
  natureza?: string;
  cnj?: string;
}): Promise<SugestaoAvulso> {
  const qs = new URLSearchParams();
  qs.set("escritorio_path", params.escritorio_path);
  if (params.cliente_cpf_cnpj) qs.set("cliente_cpf_cnpj", params.cliente_cpf_cnpj);
  if (params.posicao) qs.set("posicao", params.posicao);
  if (params.natureza) qs.set("natureza", params.natureza);
  if (params.cnj) qs.set("cnj", params.cnj);
  return json(await apiFetch(`${BASE}/avulso/sugestao?${qs.toString()}`));
}

export interface PastaAvulsaPayload {
  cnj?: string | null;
  cliente_nome: string;
  cliente_cpf_cnpj?: string | null;
  cliente_tipo?: string | null;
  posicao: string;
  natureza?: string | null;
  acao?: string | null;
  data_ajuizamento?: string | null;
  uf?: string | null;
  comarca?: string | null;
  orgao?: string | null;
  valor_causa?: number | null;
  adverso_nome?: string | null;
  adverso_cpf_cnpj?: string | null;
  adverso_tipo?: string | null;
  escritorio_path: string;
  responsavel_user_id?: number | null;
  consumir_rodizio?: boolean;
  observacao?: string | null;
}
export interface PastaAvulsaResultado {
  processo_id: number;
  cnj: string | null;
  cliente: string;
  cadastrado: boolean;
  planilha_id?: number;
  erro?: string;
}
export async function criarPastaAvulsa(payload: PastaAvulsaPayload): Promise<PastaAvulsaResultado> {
  return json(await apiFetch(`${BASE}/processos/avulso`, { method: "POST", body: JSON.stringify(payload) }));
}

// ── Acompanhamento Réu/Autor (vínculos da parte com o MDR) ─────────────────
export interface VinculoItem {
  id: number;
  npj: string;
  cnj: string | null;
  contrario_nome: string | null;
  advogado_bb: string | null;
  situacao: string | null;
  natureza: string | null;
  polo: string | null;
  posicao_banco: string | null;
  l1_lawsuit_id: number | null;
  l1_folder: string | null;
  responsavel_atual_nome: string | null;
  na_equipe_mista: boolean;
  transicao_pendente: boolean;
  transicao_concluida_em: string | null;
  nome_parte: string | null;
  doc_parte: string | null;
}
export interface PainelVinculoItem {
  processo_id: number;
  cliente: string;
  cnj: string | null;
  npj: string | null;
  posicao: string | null;
  natureza: string | null;
  adverso_principal: string | null;
  responsavel_nome: string | null;
  l1_lawsuit_id: number | null;
  l1_folder: string | null;
  escritorio_path: string | null;
  valor_causa: number | null;
  cenario: string;
  vinculos_qtd: number;
  verificado_em: string | null;
  criado_em: string | null;
  vinculos: VinculoItem[];
}
export interface PainelVinculos {
  total: number;
  items: PainelVinculoItem[];
  kpis: { total: number; cenario_1: number; cenario_2: number; transicoes_pendentes: number };
}
export async function listarPainelVinculos(params: {
  cenario?: string;
  transicao?: string;
  busca?: string;
  limit?: number;
  offset?: number;
}): Promise<PainelVinculos> {
  const qs = new URLSearchParams();
  if (params.cenario) qs.set("cenario", params.cenario);
  if (params.transicao) qs.set("transicao", params.transicao);
  if (params.busca) qs.set("busca", params.busca);
  qs.set("limit", String(params.limit ?? 50));
  qs.set("offset", String(params.offset ?? 0));
  return json(await apiFetch(`${BASE}/vinculos/painel?${qs.toString()}`));
}
export async function marcarTransicaoVinculo(vinculoId: number, concluida: boolean): Promise<{ ok: boolean }> {
  return json(await apiFetch(`${BASE}/vinculos/${vinculoId}/transicao?concluida=${concluida}`, { method: "POST" }));
}
