// Serviço do Fluxo Embargos à Execução (aba da Controladoria no Minha Equipe).
// Backend: /api/v1/embargos-execucao (gate do time bb-cadastro).

import { apiFetch } from "@/lib/api-client";

const BASE = "/api/v1/embargos-execucao";

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

export const ESTADOS = [
  "ENCONTRADO",
  "MONITORANDO",
  "AGUARDANDO_JANELA",
  "SEM_CNJ",
  "CONFIRMADO",
  "CONCLUIDO",
  "JA_CADASTRADO",
  "SEM_EMBARGOS",
  "ENCERRADO",
] as const;

export const ESTADO_LABEL: Record<string, string> = {
  ENCONTRADO: "Embargos encontrados",
  MONITORANDO: "Monitorando",
  AGUARDANDO_JANELA: "Aguardando janela",
  SEM_CNJ: "Sem CNJ",
  CONFIRMADO: "Vínculo confirmado",
  CONCLUIDO: "Tarefas disparadas",
  JA_CADASTRADO: "Já cadastrado no L1",
  SEM_EMBARGOS: "Sem embargos (teto)",
  ENCERRADO: "Encerrado",
};

export const ESTADO_HINT: Record<string, string> = {
  ENCONTRADO: "O tribunal tem embargos com evidência forte de vínculo — o monitoramento parou e espera a conferência.",
  MONITORANDO: "A janela já passou: o Flow consulta o tribunal a cada intervalo de dias úteis.",
  AGUARDANDO_JANELA: "Ainda dentro dos 15/20/25 dias úteis após o ajuizamento.",
  SEM_CNJ: "A pasta entrou sem número de processo — o Flow procura o CNJ no Legal One uma vez por dia.",
  CONFIRMADO: "Vínculo confirmado: cadastre o incidente no Legal One e no portal do BB e dispare as tarefas.",
  CONCLUIDO: "Incidente cadastrado e tarefas disparadas — fluxo fechado.",
  JA_CADASTRADO: "A pasta já tem incidente de embargos cadastrado no Legal One.",
  SEM_EMBARGOS: "Chegou ao teto do monitoramento sem achar embargos.",
  ENCERRADO: "Tirado do fluxo pelo operador.",
};

export const PARTES_LABEL: Record<string, string> = {
  PENDENTE: "Aguardando portal BB",
  OK: "Partes lidas",
  ERRO: "Erro no portal BB",
  SEM_NPJ: "Sem NPJ",
  NAO_APLICA: "Não é BB",
};

export const NIVEL_LABEL: Record<string, string> = {
  CONFIRMADO_DJEN: "Embargante é parte (DJEN)",
  PROVAVEL: "Provável (dependência + petição no dia)",
  FRACO: "A verificar (só vara + classe + data)",
  DESCARTADO: "Descartado (embargante de outro processo)",
  DESCARTADO_EMBARGADO: "Descartado (embargado não é o cliente)",
};

export const PRIORIDADE_LABEL: Record<string, string> = { Low: "Baixa", Normal: "Normal", High: "Alta" };

export interface EmbargosItem {
  id: number;
  pasta: string;
  cnj: string | null;
  npj: string | null;
  cliente: string | null;
  escritorio: string | null;
  responsavel_nome: string | null;
  executante_nome: string | null;
  uf: string | null;
  origem: string;
  l1_task_id: number | null;
  data_ajuizamento: string;
  estado: string;
  dias_uteis_janela: number;
  inicio_monitoramento: string | null;
  proxima_consulta: string | null;
  ultima_consulta_em: string | null;
  consultas_feitas: number;
  ultimo_erro: string | null;
  partes_status: string;
  partes_tentativas: number;
  partes_erro: string | null;
  partes_em: string | null;
  tribunal: string | null;
  orgao_nome: string | null;
  encontrado_em: string | null;
  aviso_enviado_em: string | null;
  incidente_folder: string | null;
  incidente_cnj: string | null;
  incidente_id: number | null;
  confirmado_candidato_id: number | null;
  anotacao: string | null;
  criado_em: string | null;
  candidatos?: number;
  candidatos_fortes_pendentes?: number;
  partes_demandadas?: number;
}

export interface EmbargosParametros {
  corte_relatorio: string | null;
  janela_dias_uteis: number;
  janelas_permitidas: number[];
  intervalo_dias_uteis: number;
  teto_dias: number;
  aviso_emails: string[];
  relatorio_titulo: string;
  relatorio_modelo_id: number;
}

export interface RelatorioUltimo {
  ok?: boolean;
  report_id?: number;
  novas?: number;
  atualizadas?: number;
  antes_do_corte?: number;
  sem_dados?: boolean;
  erro?: string;
  em?: string;
  corte?: string | null;
}

export interface RelatorioStatus {
  running?: boolean;
  fase?: string | null;
  iniciado_em?: string | null;
  ultimo?: RelatorioUltimo | null;
}

export interface PartesStatus {
  running?: boolean;
  iniciado_em?: string | null;
  na_fila?: number;
  origem?: string;
  ultimo?: { na_fila?: number; origem?: string; em?: string; erro?: string } | null;
}

export interface EmbargosListaResponse {
  total: number;
  kpis: {
    por_estado: Record<string, number>;
    partes_erro: number;
    consultas_vencidas: number;
    total: number;
  };
  parametros: EmbargosParametros;
  relatorio: RelatorioStatus;
  partes: PartesStatus;
  items: EmbargosItem[];
}

export interface EmbargosParte {
  id: number;
  origem: string;
  polo: string | null;
  nome: string;
  cpf_cnpj: string | null;
  tipo_pessoa: string | null;
  relacao_bb: string | null;
  demandada: boolean;
}

export interface EmbargosCandidato {
  id: number;
  cnj: string;
  data_ajuizamento: string | null;
  classe_nome: string | null;
  orgao_nome: string | null;
  distribuicao_dependencia: boolean;
  peticao_mesmo_dia: boolean;
  nivel: string;
  djen_status: string | null;
  djen_embargantes: string[];
  djen_embargados: string[];
  l1_litigation_id: number | null;
  l1_folder: string | null;
  djen_nomes_casados: string[];
  djen_trecho: string | null;
  djen_data: string | null;
  djen_consultado_em: string | null;
  decisao: string;
  decidido_em: string | null;
  criado_em: string | null;
}

export interface EmbargosEvento {
  id: number;
  secao: string;
  nivel: string;
  mensagem: string;
  dados: Record<string, unknown> | null;
  user_id: number | null;
  criado_em: string | null;
}

export interface EmbargosDisparo {
  id: number;
  template_nome: string | null;
  subtipo_id: number | null;
  incidente_id: number | null;
  responsavel_contact_id: number | null;
  prazo: string | null;
  descricao: string | null;
  status: string; // CRIADA | FALHA
  l1_task_id: number | null;
  erro: string | null;
  criado_em: string | null;
}

export interface EmbargosDetalhe {
  execucao: EmbargosItem;
  l1_task_ids: number[];
  disparos: EmbargosDisparo[];
  partes: EmbargosParte[];
  candidatos: EmbargosCandidato[];
  eventos: EmbargosEvento[];
  partes_disparada?: boolean;
  resultado_disparo?: { criadas: number; falhas: number; puladas: number; estado: string };
}

export interface EmbargosTemplate {
  id: number;
  nome: string;
  ativo: boolean;
  ordem: number;
  tipo_id: number;
  subtipo_id: number;
  subtipo_nome: string | null;
  responsavel_modo: string; // FIXO | ADVOGADO_RESPONSAVEL
  responsavel_contact_id: number | null;
  responsavel_nome: string | null;
  prazo_dias_uteis: number;
  prioridade: string;
  descricao_template: string;
  observacoes_template: string | null;
  atualizado_em: string | null;
}

export interface EmbargosTemplatesResponse {
  total: number;
  items: EmbargosTemplate[];
  placeholders: Record<string, string>;
  prioridades: string[];
  responsavel_modos: Record<string, string>;
}

export interface EmbargosTemplateIn {
  nome: string;
  ativo: boolean;
  ordem: number;
  subtipo_id: number;
  responsavel_modo: string;
  responsavel_contact_id: number | null;
  prazo_dias_uteis: number;
  prioridade: string;
  descricao_template: string;
  observacoes_template: string | null;
}

export interface EmbargosPreviaTarefa {
  template_id: number;
  nome: string;
  subtipo_nome: string | null;
  responsavel_contact_id: number | null;
  responsavel_nome: string | null;
  prazo: string;
  prioridade: string;
  descricao: string;
  observacoes: string | null;
  erro: string | null;
  ja_disparada: boolean;
  l1_task_id: number | null;
}

export interface EmbargosPrevia {
  incidente: { id: number; folder: string | null; cnj: string | null; office_id: number | null } | null;
  tarefas: EmbargosPreviaTarefa[];
  placeholders: Record<string, string>;
}

export interface EmbargosListaParams {
  estado?: string;
  cliente?: string;
  partes_status?: string;
  busca?: string;
  ordenar?: string;
  direcao?: "asc" | "desc";
  limit?: number;
  offset?: number;
}

export async function listarEmbargos(params: EmbargosListaParams): Promise<EmbargosListaResponse> {
  const qs = new URLSearchParams();
  if (params.estado) qs.set("estado", params.estado);
  if (params.cliente) qs.set("cliente", params.cliente);
  if (params.partes_status) qs.set("partes_status", params.partes_status);
  if (params.busca) qs.set("busca", params.busca);
  if (params.ordenar) qs.set("ordenar", params.ordenar);
  if (params.direcao) qs.set("direcao", params.direcao);
  qs.set("limit", String(params.limit ?? 50));
  qs.set("offset", String(params.offset ?? 0));
  return json(await apiFetch(`${BASE}?${qs.toString()}`));
}

export async function getEmbargosDetalhe(id: number): Promise<EmbargosDetalhe> {
  return json(await apiFetch(`${BASE}/${id}`));
}

export async function getEmbargosParametros(): Promise<EmbargosParametros> {
  return json(await apiFetch(`${BASE}/parametros`));
}

export async function salvarEmbargosParametros(
  body: Partial<Pick<EmbargosParametros, "corte_relatorio" | "janela_dias_uteis" | "intervalo_dias_uteis" | "teto_dias" | "aviso_emails">>,
): Promise<EmbargosParametros> {
  return json(await apiFetch(`${BASE}/parametros`, { method: "PUT", body: JSON.stringify(body) }));
}

export async function getRelatorioStatus(): Promise<RelatorioStatus> {
  return json(await apiFetch(`${BASE}/relatorio/status`));
}

export async function gerarRelatorioEmbargos(): Promise<{ ok: boolean; mensagem: string }> {
  return json(await apiFetch(`${BASE}/relatorio/gerar`, { method: "POST" }));
}

export async function getPartesStatus(): Promise<PartesStatus> {
  return json(await apiFetch(`${BASE}/partes/status`));
}

export async function coletarPartesAgora(): Promise<{ ok: boolean; mensagem: string }> {
  return json(await apiFetch(`${BASE}/partes/coletar`, { method: "POST" }));
}

export async function importarPlanilhaEmbargos(file: File): Promise<Record<string, unknown> & { novas: number; atualizadas: number; sem_data: number; sem_identificador: number }> {
  const fd = new FormData();
  fd.append("arquivo", file);
  return json(await apiFetch(`${BASE}/importar`, { method: "POST", body: fd }));
}

export async function incluirEmbargosManual(body: {
  pasta?: string;
  cnj?: string;
  npj?: string;
  data_ajuizamento: string;
}): Promise<{ novas: number; atualizadas: number }> {
  return json(await apiFetch(`${BASE}/manual`, { method: "POST", body: JSON.stringify(body) }));
}

export async function ajustarEmbargos(
  id: number,
  body: { dias_uteis_janela?: number; anotacao?: string },
): Promise<EmbargosDetalhe> {
  return json(await apiFetch(`${BASE}/${id}`, { method: "PATCH", body: JSON.stringify(body) }));
}

export async function consultarEmbargosAgora(id: number): Promise<{ ok: boolean; mensagem: string }> {
  return json(await apiFetch(`${BASE}/${id}/consultar`, { method: "POST" }));
}

export async function recoletarPartesEmbargos(id: number): Promise<EmbargosDetalhe> {
  return json(await apiFetch(`${BASE}/${id}/partes`, { method: "POST" }));
}

export async function decidirCandidatoEmbargos(
  id: number,
  candidatoId: number,
  decisao: "CONFIRMADO" | "RECUSADO",
): Promise<EmbargosDetalhe> {
  return json(
    await apiFetch(`${BASE}/${id}/candidatos/${candidatoId}/decisao`, {
      method: "POST",
      body: JSON.stringify({ decisao }),
    }),
  );
}

export async function encerrarEmbargos(id: number, motivo?: string): Promise<EmbargosDetalhe> {
  return json(await apiFetch(`${BASE}/${id}/encerrar`, { method: "POST", body: JSON.stringify({ motivo }) }));
}

export async function reabrirEmbargos(id: number): Promise<EmbargosDetalhe> {
  return json(await apiFetch(`${BASE}/${id}/reabrir`, { method: "POST" }));
}

export async function previaTarefasEmbargos(id: number): Promise<EmbargosPrevia> {
  return json(await apiFetch(`${BASE}/${id}/tarefas/previa`));
}

export async function dispararTarefasEmbargos(id: number): Promise<EmbargosDetalhe> {
  return json(await apiFetch(`${BASE}/${id}/tarefas/disparar`, { method: "POST" }));
}

export async function listarTemplatesEmbargos(): Promise<EmbargosTemplatesResponse> {
  return json(await apiFetch(`${BASE}/templates`));
}

export async function salvarTemplateEmbargos(body: EmbargosTemplateIn, id?: number): Promise<EmbargosTemplate> {
  return json(
    await apiFetch(id ? `${BASE}/templates/${id}` : `${BASE}/templates`, {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(body),
    }),
  );
}

export async function excluirTemplateEmbargos(id: number): Promise<{ ok: boolean }> {
  return json(await apiFetch(`${BASE}/templates/${id}`, { method: "DELETE" }));
}

// Catálogos compartilhados com os templates de Publicações (qualquer usuário logado).
export interface CatalogoTipo {
  external_id: number;
  name: string;
  subtypes: { external_id: number; name: string }[];
}
export interface CatalogoUsuario {
  id: number;
  external_id: number | null;
  name: string;
  email?: string | null;
  squads?: { id: number; name: string }[];
}

export async function getTiposTarefa(): Promise<CatalogoTipo[]> {
  return json(await apiFetch(`/api/v1/task-templates/meta/task-types`));
}

export async function getUsuariosL1(): Promise<CatalogoUsuario[]> {
  return json(await apiFetch(`/api/v1/task-templates/meta/users`));
}
