// Tipos da visualização de triagem de publicações.
// Espelham os da PublicationsPage (que os declara localmente, sem exportar) —
// aqui ficam exportados porque a triagem é dividida em vários componentes.

export interface Classification {
  categoria: string;
  subcategoria: string;
  polo: "ativo" | "passivo" | "ambos";
  confianca?: string;
  justificativa?: string;
  audiencia_data?: string | null;
  audiencia_hora?: string | null;
  audiencia_link?: string | null;
  prazo_dias?: number | null;
  prazo_tipo?: "util" | "corrido" | null;
  prazo_fundamentacao?: string | null;
}

export interface PublicationRecord {
  id: number;
  search_id: number;
  legal_one_update_id: number;
  origin_type: string | null;
  update_type_id: number | null;
  description_preview: string;
  description?: string;
  notes?: string;
  publication_date: string | null;
  creation_date: string | null;
  linked_lawsuit_id: number | null;
  linked_lawsuit_cnj: string | null;
  linked_office_id: number | null;
  status: string;
  category: string | null;
  subcategory: string | null;
  polo?: string | null;
  uf?: string | null;
  natureza_processo?: string | null;
  prazo_estimado?: string | null;
  created_at?: string | null;
  audiencia_data?: string | null;
  audiencia_hora?: string | null;
  audiencia_link?: string | null;
  classifications?: Classification[];
  has_proposal?: boolean;
  proposals_count?: number;
  /** pub010 — de quem é o ato e se ele exige providência nossa. */
  quem_pratica_ato?: string | null;
  exige_providencia_nossa?: boolean | null;
  /** Fila sem pasta (pub014) — só em publicação sem processo vinculado. */
  sem_pasta?: SemPastaInfo | null;
}

/**
 * Fila sem pasta (pub014): o que a regra e a IA descobriram para achar a
 * pasta. `nossos` são os CNJs do texto reconhecidos na nossa base (piso:
 * ela cobre ~70% das pastas); `cnj_origem` é o processo originário que a
 * IA leu no texto (agravo → origem).
 */
export interface SemPastaInfo {
  n_cnj: number;
  cnjs: string[];
  nossos: { cnj: string; lawsuit_id: number; office_id: number | null; office_path: string | null }[];
  pauta_coletiva: boolean;
  cnj_origem: string | null;
  /** Quem classificou ("regra" | "ia") e o tipo dado pelo motor. */
  motor?: string | null;
  tipo?: string | null;
  cliente?: string | null;
  /**
   * Quem representamos no processo e o quanto dá para confiar nisso. A
   * confiança NÃO é a auto-declarada pela IA: vem de evidência verificável
   * (pasta citada, nome no texto, papel processual, advogado da casa).
   */
  /** Justiça comum / juizado especial / trabalhista, e de onde veio. */
  rito?: {
    rito: "juizado" | "comum" | "trabalhista" | null;
    rotulo?: string | null;
    fonte: "texto" | "datajud" | null;
    evidencia: string | null;
  } | null;
  cliente_info?: {
    cliente: string | null;
    confianca: "alta" | "media" | "baixa" | "nenhuma";
    fonte: string;
    evidencias: string[];
    alternativas: string[];
    cliente_ia: string | null;
  } | null;
  /** Ficha de cadastro do caso (tipos críticos): campo → valor extraído. */
  ficha?: Record<string, string | null> | null;
}

export interface SuggestedResponsible {
  id: number;
  name: string | null;
  email: string | null;
  source: string;
}

export interface ProposedTask {
  description: string;
  priority: string;
  startDateTime: string;
  endDateTime: string;
  typeId: number;
  subTypeId: number;
  responsibleOfficeId: number | null;
  participants: any[];
  notes: string | null;
  template_name?: string;
  suggested_responsible?: SuggestedResponsible | null;
  is_custom?: boolean;
  /** Roteamento do template por equipe (squad) — quando existe. */
  target_role?: string | null;
  target_squad_id?: number | null;
}

/**
 * Etiqueta (tag) do processo no L1. A API REST não expõe: vem do cache que o
 * backend alimenta pelo caminho web (`pub_l1_etiqueta_cache`).
 */
export interface L1Etiqueta {
  id: number | null;
  name: string | null;
  /** Classe de cor do próprio L1, ex.: "tag-color-orange". */
  class_name: string | null;
  color_id: number | null;
}

/**
 * Responsável nominal da PASTA no L1 (≠ responsável da tarefa).
 *
 * Vem do `lawsuit_cache`, não da API ao vivo. Ausente no grupo significa "ainda
 * não consultado" — que é diferente de "sem responsável", e a tela distingue.
 */
export interface ResponsavelPasta {
  id: number | null;
  nome: string | null;
  email: string | null;
  /** Quando o cache leu essa informação do L1 (ISO). */
  atualizado_em: string | null;
  /** Leitura com mais de 7 dias — vale, mas com ressalva. */
  desatualizado: boolean;
}

export interface GroupedRecord {
  lawsuit_id: number | null;
  lawsuit_cnj: string | null;
  office_id: number | null;
  records: PublicationRecord[];
  proposed_task: ProposedTask | null;
  proposed_tasks: ProposedTask[];
  classifications: Classification[];
  proposals_built?: boolean;
  /** null = processo ainda não enriquecido; [] = consultado e sem etiqueta. */
  l1_etiquetas?: L1Etiqueta[] | null;
  /** null/ausente = pasta ainda não consultada (≠ pasta sem responsável). */
  responsavel_pasta?: ResponsavelPasta | null;
}

export interface GroupedResponse {
  total_groups: number;
  total_records: number;
  offset: number;
  limit: number;
  groups: GroupedRecord[];
  /** Etiquetas presentes no escopo atual — alimenta o filtro. */
  available_etiquetas?: string[];
  /** UF/região derivada do CNJ presente no escopo — alimenta o filtro. */
  available_ufs?: string[];
  /** Donos de pasta com publicação na fila, por volume — alimenta o filtro. */
  available_responsaveis?: { id: number; nome: string; total: number }[];
}

/** Uma linha do hub: backlog pendente de um escritório (GET /records/office-summary). */
export interface OfficeSummaryItem {
  office_id: number | null;
  office_name: string;
  office_path: string | null;
  polo_scope: string | null;
  total: number;
  novos: number;
  classificados: number;
  erros: number;
  vencidas: number;
  vence_hoje: number;
  dias_mais_antiga: number;
  mais_antiga_em: string | null;
  faixas: { d0_2: number; d3_7: number; d8_15: number; d16_30: number; d31_mais: number };
}

export interface OfficeSummaryResponse {
  hoje: string;
  total_pendentes: number;
  offices: OfficeSummaryItem[];
}

export interface Office {
  id: number;
  external_id: number;
  name: string;
  path: string | null;
  polo_scope?: string | null;
}

export interface TaskSubtype {
  id: number;
  external_id: number;
  name: string;
}

export interface TaskType {
  id: number;
  external_id: number;
  name: string;
  subtypes: TaskSubtype[];
}

export interface AppUser {
  id: number;
  external_id: number;
  name: string;
  email?: string | null;
  squads?: { id: number; name: string }[];
}

/** Tarefa em edição no compositor (antes de virar payload do L1). */
export interface DraftTask {
  uid: string;
  description: string;
  notes: string;
  subTypeId: number | null;
  typeId: number | null;
  /** external_id do responsável (contact id do L1). */
  responsibleExternalId: number | null;
  /** Data do prazo em BRT, formato YYYY-MM-DD (o que o input date usa). */
  dueDate: string;
  /** Hora em BRT, HH:MM. */
  dueTime: string;
  templateName?: string;
  isCustom: boolean;
  priority: string;
  responsibleOfficeId: number | null;

  // ── o que o template propôs (pub007/pub008) ──
  // Guardado na abertura para detectar divergência: sem o valor original não
  // dá para saber se o operador trocou algo, e o motivo só é perguntado
  // quando houve troca de fato.
  origSubTypeId: number | null;
  origDueIso: string | null;
  /** De onde veio o responsável sugerido: template, pasta do L1, squad… */
  respSource?: string | null;

  // ── marcadores de justificativa ──
  subtipoMotivo: string | null;
  dataMotivo: string | null;
  tarefaAbertaMotivo: string | null;
  /** Removida da remessa (fica visível, com motivo, mas não é enviada). */
  removida: boolean;
  removidaMotivo: string | null;
}

/** Tarefa já aberta no L1 no mesmo subtipo — vem do check-duplicates. */
export interface TarefaAbertaL1 {
  id: number | null;
  description?: string | null;
  endDateTime?: string | null;
  l1_url?: string | null;
  subTypeId?: number | null;
}

/** Uma linha da mini auditoria de tratamento do escritório. */
export interface TratadaRecente {
  record_id: number;
  acao: "agendada" | "ignorada";
  quando: string | null;
  por_nome: string | null;
  por_email: string | null;
  lawsuit_id: number | null;
  cnj: string | null;
  office_id: number | null;
  categoria: string | null;
  subcategoria: string | null;
  motivo: string | null;
  motivo_nota: string | null;
  consultou_autos: boolean | null;
  publication_date: string | null;
  tarefas: { task_id: number | null; descricao: string | null; subtipo_id: number | null; prazo?: string | null }[];
  motivos?: {
    subtipo_troca_motivo: string | null;
    data_troca_motivo: string | null;
    data_delta_dias: number | null;
    agendou_com_tarefa_aberta_motivo: string | null;
    tarefa_removida_motivo: string | null;
    override_detected: boolean;
  } | null;
}

/** Quem está com quantas publicações na tag de leitura (pub013). */
export interface DistribuicaoItem {
  user_id: number | null;
  nome: string;
  total: number;
}

export type LayoutKind = "focus" | "cards" | "split";
