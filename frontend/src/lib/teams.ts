// Times (setores/supervisões) do Minha Equipe — espelha app/services/performance/teams.py.
// Cada time é um item de menu + uma permissão (árvore do admin) + o slug da rota.

export const GRUPO_PASSIVO = "Contencioso Passivo";
export const GRUPO_CREDITO = "Recuperação de Crédito";

export const TEAMS = [
  { key: "bb-reu", label: "BB Réu", grupo: GRUPO_PASSIVO },
  { key: "bb-execucao", label: "BB Execução & Encerramento", grupo: GRUPO_PASSIVO },
  { key: "bb-acordos", label: "BB Acordos", grupo: GRUPO_PASSIVO },
  { key: "bb-estrategico", label: "BB Estratégico", grupo: GRUPO_PASSIVO },
  { key: "bb-cadastro", label: "BB Cadastro", grupo: GRUPO_PASSIVO },
  { key: "master-reu", label: "Master Réu", grupo: GRUPO_PASSIVO },
  { key: "ativos-reu", label: "Ativos Réu", grupo: GRUPO_PASSIVO },
  { key: "trabalhista", label: "Trabalhista", grupo: GRUPO_PASSIVO },
  { key: "bb-autor-processual", label: "BB Autor — Processual", grupo: GRUPO_CREDITO },
  { key: "ativos-autor", label: "Ativos Autor", grupo: GRUPO_CREDITO },
  { key: "autor-recursal", label: "Autor — Recursal", grupo: GRUPO_CREDITO },
  { key: "ajuizamento", label: "Ajuizamento", grupo: GRUPO_CREDITO },
  { key: "estrategico-autor", label: "Estratégico Autor", grupo: GRUPO_CREDITO },
] as const;

export const TEAM_KEYS = TEAMS.map((t) => t.key);

export function teamLabel(key: string): string {
  return TEAMS.find((t) => t.key === key)?.label ?? key;
}

export function isValidTeam(key: string | undefined): boolean {
  return !!key && TEAM_KEYS.includes(key as (typeof TEAMS)[number]["key"]);
}
