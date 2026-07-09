// Serviço do módulo OneRequest (DMIs do Banco do Brasil).
// Self-contained (tipos + chamadas) pra não inflar o api.ts gigante.

import { apiFetch } from "@/lib/api-client";

const BASE = "/api/v1/onerequest";

export type Farol = "cinza" | "atrasado" | "vermelho" | "amarelo" | "roxo" | "verde";

export interface OnerequestSolicitacao {
  id: number;
  numero_solicitacao: string;
  titulo: string | null;
  npj_direcionador: string | null;
  prazo: string | null;
  texto_dmi: string | null;
  numero_processo: string | null;
  proc_utilizavel: boolean;
  polo: string | null;
  recebido_em: string | null;
  status_sistema: string;
  status_tratamento: string;
  desfecho: string | null;
  responsavel_user_id: number | null;
  responsavel_nome: string | null;
  setor: string | null;
  data_agendamento: string | null;
  anotacao: string | null;
  tem_anotacao: boolean;
  created_task_id: number | null;
  linked_lawsuit_id: number | null;
  last_error: string | null;
  scheduled_by_nome: string | null;
  scheduled_at: string | null;
  farol: Farol;
  // Status no L1 (cacheado pelo botão "Atualizar status L1").
  l1_checked_at: string | null;
  l1_dmi_task_id: number | null;
  l1_dmi_status_id: number | null;
  l1_dmi_status_label: string | null;
  l1_dmi_respondida: boolean;
  l1_dmi_encontrada: boolean;
  l1_pendentes_count: number | null;
  l1_sem_pendencia: boolean | null;
  l1_task_url: string | null;
  // Verificação proativa de existência do processo no L1.
  proc_l1_checado_em: string | null;
  proc_l1_encontrado: boolean | null;
  proc_l1_via: string | null;
}

export interface ListResponse {
  total: number;
  kpis: Record<string, number>;
  items: OnerequestSolicitacao[];
}

export interface ListParams {
  status_sistema?: string;
  status_tratamento?: string;
  responsavel_user_id?: number;
  setor?: string;
  busca?: string;
  farol?: string;
  sem_responsavel?: boolean;
  sem_anotacao?: boolean;
  // Apenas DMIs cujo processo NÃO foi encontrado no L1 (checado e não achado).
  sem_processo_l1?: boolean;
  // Concluídas = BB respondeu (RESPONDIDO) ou operador encerrou sem providência (IGNORADO).
  concluidas?: boolean;
  // Recortes de data (ISO YYYY-MM-DD). disp_* = disponibilização (recebido_em);
  // prazo_* = prazo fatal do BB.
  disp_de?: string;
  disp_ate?: string;
  prazo_de?: string;
  prazo_ate?: string;
  limit?: number;
  offset?: number;
}

export interface Sugestao {
  setor: string | null;
  setor_confianca: string | null;
  responsavel_user_id: number | null;
  responsavel_nome: string | null;
  responsavel_confianca: number | null;
  // "trabalhista" = regra determinística (dígito J=5 do CNJ → Antônio Uemerson).
  responsavel_regra: string | null;
  data_agendamento: string | null;
  // Responsável da PASTA no L1 (informativo; opção de 1 clique — não pré-preenche).
  pasta_responsavel_nome: string | null;
  pasta_responsavel_user_id: number | null;
}

export interface L1Task {
  task_id: number | null;
  description: string | null;
  status_id: number | null;
  status_label: string | null;
  end_date_time: string | null;
  l1_url: string | null;
}

export interface L1Tarefas {
  lawsuit_id: number | null;
  l1_url: string | null;
  pendentes: L1Task[];
  concluidas: L1Task[];
  resolvido: boolean;
  check_failed: boolean;
}

export interface Anotacao {
  id: number;
  texto: string;
  autor_nome: string | null;
  created_at: string | null;
}

export interface StatusL1 {
  checked_at: string | null;
  resolvido: boolean;
  lawsuit_id: number | null;
  l1_url: string | null;
  dmi_task_id: number | null;
  dmi_task_url: string | null;
  dmi_status_id: number | null;
  dmi_status_label: string | null;
  dmi_respondida: boolean;
  dmi_encontrada: boolean;
  pendentes_count: number | null;
  sem_pendencia: boolean | null;
}

export interface Estado {
  last_ingest_at: string | null;
  abertas: number;
  // Farol GLOBAL das abertas (KPI cards estáticos, independem do filtro).
  kpis?: Record<string, number>;
}

export interface L1Autorefresh {
  enabled: boolean;
  last_run_at: string | null;
  last_count: number | null;
  intervalo: string;
  alvo: string;
}

export interface AuditAgendamento {
  agendado: boolean;
  scheduled_by_nome: string | null;
  scheduled_by_email: string | null;
  scheduled_at: string | null;
  responsavel_nome: string | null;
  setor: string | null;
  data_agendamento: string | null;
  prazo_bb: string | null;
  created_task_id: number | null;
  status_sistema: string | null;
  status_tratamento: string | null;
  last_error: string | null;
}

export interface AuditTarefaL1 {
  task_id: number | null;
  description: string | null;
  status_id: number | null;
  status_label: string | null;
  start_date_time: string | null;
  end_date_time: string | null;
  l1_url: string | null;
  lawsuit_url: string | null;
}

export interface Auditoria {
  id: number;
  numero_solicitacao: string;
  numero_processo: string | null;
  npj_direcionador: string | null;
  titulo: string | null;
  agendamento: AuditAgendamento;
  tarefa_l1: AuditTarefaL1 | null;
  anotacoes: Anotacao[];
}

export interface AlertaResponsavel {
  responsavel_user_id: number | null;
  responsavel_nome: string;
  responsavel_email: string | null;
  teams_disponivel: boolean;
  count: number;
  mensagem: string;
}

export interface UpdateTratamentoBody {
  responsavel_user_id?: number | null;
  setor?: string | null;
  data_agendamento?: string | null;
  anotacao?: string | null;
  status_tratamento?: string | null;
}

export interface TarefaExistente {
  task_id: number;
  status_id: number | null;
  status_label: string | null;
  description: string | null;
  l1_url: string | null;
}

export interface AgendarResult {
  ok: boolean;
  status_tratamento: string;
  created_task_id: number | null;
  mensagem: string;
  requires_confirmation?: boolean;
  tarefa_existente?: TarefaExistente | null;
}

export interface FormUser {
  id: number;
  external_id: number;
  name: string;
  squads: { id: number; name: string }[];
  email?: string | null;
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

// Builder ÚNICO de query — listagem e exportação usam exatamente os mesmos
// filtros (eles "conversam" por construção). Não inclui limit/offset.
function buildListQuery(params: ListParams): URLSearchParams {
  const qs = new URLSearchParams();
  if (params.status_sistema) qs.set("status_sistema", params.status_sistema);
  if (params.status_tratamento) qs.set("status_tratamento", params.status_tratamento);
  if (params.responsavel_user_id)
    qs.set("responsavel_user_id", String(params.responsavel_user_id));
  if (params.setor) qs.set("setor", params.setor);
  if (params.busca) qs.set("busca", params.busca);
  if (params.farol) qs.set("farol", params.farol);
  if (params.sem_responsavel) qs.set("sem_responsavel", "true");
  if (params.sem_anotacao) qs.set("sem_anotacao", "true");
  if (params.sem_processo_l1) qs.set("sem_processo_l1", "true");
  if (params.concluidas) qs.set("concluidas", "true");
  if (params.disp_de) qs.set("disp_de", params.disp_de);
  if (params.disp_ate) qs.set("disp_ate", params.disp_ate);
  if (params.prazo_de) qs.set("prazo_de", params.prazo_de);
  if (params.prazo_ate) qs.set("prazo_ate", params.prazo_ate);
  return qs;
}

export async function listSolicitacoes(params: ListParams): Promise<ListResponse> {
  const qs = buildListQuery(params);
  qs.set("limit", String(params.limit ?? 50));
  qs.set("offset", String(params.offset ?? 0));
  return json(await apiFetch(`${BASE}/solicitacoes?${qs.toString()}`));
}

// Exporta as DMIs com os MESMOS filtros da listagem (sem paginação) e baixa o xlsx.
export async function downloadSolicitacoesExcel(params: ListParams): Promise<void> {
  const qs = buildListQuery(params);
  const res = await apiFetch(`${BASE}/solicitacoes/export?${qs.toString()}`);
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
  a.download = `onerequest-dmis-${new Date().toISOString().slice(0, 10)}.xlsx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function getOptions(): Promise<{ setores: string[] }> {
  return json(await apiFetch(`${BASE}/options`));
}

export async function updateTratamento(
  id: number,
  body: UpdateTratamentoBody,
): Promise<{ ok: boolean; id: number; status_tratamento: string }> {
  return json(
    await apiFetch(`${BASE}/solicitacoes/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  );
}

export async function agendarSolicitacao(
  id: number,
  confirmar = false,
): Promise<AgendarResult> {
  const qs = confirmar ? "?confirmar=true" : "";
  return json(
    await apiFetch(`${BASE}/solicitacoes/${id}/agendar${qs}`, { method: "POST" }),
  );
}

// Reusa o endpoint de dados do formulário de tarefas pra popular o
// UserSelector (mesma forma {id, external_id, name, squads}).
export async function getFormUsers(): Promise<FormUser[]> {
  const data = await json<{ users: FormUser[] }>(
    await apiFetch(`/api/v1/tasks/task-creation-data`),
  );
  return data.users ?? [];
}

export async function getEstado(): Promise<Estado> {
  return json(await apiFetch(`${BASE}/estado`));
}

export interface DashboardData {
  kpis: Record<string, number>;
  farol: Record<string, number>;
  recebimentos: { dia: string; n: number }[];
  agendamentos: { dia: string; n: number }[];
  por_responsavel: { id: number; nome: string; abertas: number; atrasadas: number; agendadas: number }[];
  por_setor: { setor: string; n: number }[];
  periodo_dias: number;
}

// Dashboard do OneRequest (KPIs + séries diárias + distribuições, operacional + risco).
export async function getDashboard(days = 30): Promise<DashboardData> {
  return json(await apiFetch(`${BASE}/dashboard?days=${days}`));
}

// Regra de auto-atualização horária do status L1 (DMIs que vencem hoje).
export async function getL1Autorefresh(): Promise<L1Autorefresh> {
  return json(await apiFetch(`${BASE}/l1-autorefresh`));
}

export async function setL1Autorefresh(enabled: boolean): Promise<L1Autorefresh> {
  return json(
    await apiFetch(`${BASE}/l1-autorefresh`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
  );
}

export async function getSugestao(id: number): Promise<Sugestao> {
  return json(await apiFetch(`${BASE}/solicitacoes/${id}/sugestao`));
}

export async function getL1Tarefas(id: number): Promise<L1Tarefas> {
  return json(await apiFetch(`${BASE}/solicitacoes/${id}/l1-tarefas`));
}

// Checa no L1 se a tarefa da DMI foi respondida (Cumprida) + pendências na pasta.
// Cacheia no backend; devolve o resultado já calculado.
export async function verificarStatusL1(id: number): Promise<StatusL1> {
  return json(
    await apiFetch(`${BASE}/solicitacoes/${id}/status-l1`, { method: "POST" }),
  );
}

export interface ProcessoL1Resultado {
  encontrado: boolean;
  via: string | null;
  lawsuit_id: number | null;
}

// Verifica se o processo da DMI existe no L1 (CNJ->NPJ) sem criar tarefa.
export async function verificarProcessoL1(id: number): Promise<ProcessoL1Resultado> {
  return json(
    await apiFetch(`${BASE}/solicitacoes/${id}/verificar-processo-l1`, { method: "POST" }),
  );
}

export async function listAnotacoes(id: number): Promise<Anotacao[]> {
  return json(await apiFetch(`${BASE}/solicitacoes/${id}/anotacoes`));
}

// Auditoria total da DMI (quem agendou, o que, pra quem + tarefa viva no L1 + anotações).
export async function getAuditoria(id: number): Promise<Auditoria> {
  return json(await apiFetch(`${BASE}/solicitacoes/${id}/auditoria`));
}

// Mensagens de alerta (uma por responsável) das DMIs que vencem hoje.
export async function getAlertasVenceHoje(): Promise<AlertaResponsavel[]> {
  return json(await apiFetch(`${BASE}/alertas/vence-hoje`));
}

// Envia o alerta do responsável via Teams (Microsoft Graph). O token delegado
// (MSAL, no nome da operadora) vai junto e o backend faz a chamada ao Graph.
export async function enviarAlertaTeams(
  responsavel_user_id: number,
  graph_token: string,
): Promise<{ ok: boolean; mensagem: string }> {
  return json(
    await apiFetch(`${BASE}/alertas/enviar-teams`, {
      method: "POST",
      body: JSON.stringify({ responsavel_user_id, graph_token }),
    }),
  );
}

export async function addAnotacao(id: number, texto: string): Promise<Anotacao> {
  return json(
    await apiFetch(`${BASE}/solicitacoes/${id}/anotacoes`, {
      method: "POST",
      body: JSON.stringify({ texto }),
    }),
  );
}
