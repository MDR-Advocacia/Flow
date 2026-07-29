// Tipos e constantes compartilhados pelas abas do Painel Administrativo.
// Extraídos do AdminPage.tsx (que tinha 1.548 linhas e 4 componentes
// grandes dentro — faixa em que o Edit já truncou arquivo neste projeto).

// Blocos LEGADOS — escondidos da tela a pedido do operador (29/07/2026) por
// já terem cumprido seu papel. O código fica: é só voltar pra `true` se algum
// dia precisar. O que sai de vista (e em que valor cada flag CONGELA):
//   - aba Taxonomia (o motor roda sozinho; a curadoria não é mais usada)
//   - Toggle Taxonomy v1<->v2  → congela em taxonomy_active_version = 'v2'
//   - Modo árvore enxuta        → congela em template_driven_taxonomy = true
//   - Índice de Processos por Escritório / Cache de Dados de Processos
// Nada some do backend: os endpoints e os valores em app_settings seguem lá.
export const MOSTRAR_LEGADO = false;

// Permissões de módulo exibidas no dropdown condensado da tabela de usuários.
export const PERMISSOES = [
  { key: "can_schedule_batch", label: "LegalOne", abbr: "L1" },
  { key: "can_use_publications", label: "Publicações", abbr: "Pub" },
  { key: "can_use_prazos_iniciais", label: "Prazos Iniciais", abbr: "PI" },
  { key: "can_use_onerequest", label: "OneRequest", abbr: "OR" },
  { key: "can_use_minha_equipe", label: "Minha Equipe", abbr: "ME" },
  { key: "can_manage_distribuidos_bb", label: "Cadastro de Processo", abbr: "CP" },
  { key: "notify_onerequest_errors", label: "Notificação OneRequest", abbr: "Notif" },
] as const;


// --- Tipos de Dados ---
export interface Sector { id: number; name: string; }
export interface Squad { id: number; name: string; }
export interface AdminUser {
  id: number;
  name: string;
  email: string;
  external_id: number;
  is_active: boolean;
  role: string;
  can_schedule_batch: boolean;
  can_use_publications: boolean;
  can_use_prazos_iniciais: boolean;
  can_use_onerequest: boolean;
  can_use_minha_equipe: boolean;
  minha_equipe_equipes: string[];
  can_manage_distribuidos_bb: boolean;
  notify_onerequest_errors: boolean;
  default_office_id: number | null;
  has_password: boolean;
  is_sso: boolean;
  must_change_password: boolean;
}
export interface Office {
  id: number;
  name: string;
}

