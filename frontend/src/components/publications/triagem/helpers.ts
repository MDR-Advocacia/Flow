// Formatação, régua de envelhecimento e conversão de fuso da triagem.
// O fuso segue a mesma regra do modal clássico: o L1 ignora o offset ISO e
// trata o número literal como UTC, então a hora BRT precisa virar UTC com "Z"
// ANTES de subir (senão 11:30 aparece como 08:30 na agenda).

import type { Classification, GroupedRecord, OfficeSummaryItem, PublicationRecord } from "./types";

export const MS_DIA = 86_400_000;

/* ─── faixas de idade na fila (mesmas do aging-summary) ─── */
export interface Band {
  key: keyof OfficeSummaryItem["faixas"];
  label: string;
  short: string;
  min: number;
  max: number;
  /** cor da tarja/barra */
  hex: string;
  chip: string;
}

export const BANDS: Band[] = [
  { key: "d0_2", label: "0–2 dias", short: "0–2d", min: 0, max: 2, hex: "#94a3b8", chip: "bg-slate-100 text-slate-600 border-slate-200" },
  { key: "d3_7", label: "3–7 dias", short: "3–7d", min: 3, max: 7, hex: "#60a5fa", chip: "bg-blue-50 text-blue-700 border-blue-200" },
  { key: "d8_15", label: "8–15 dias", short: "8–15d", min: 8, max: 15, hex: "#fbbf24", chip: "bg-amber-50 text-amber-700 border-amber-200" },
  { key: "d16_30", label: "16–30 dias", short: "16–30d", min: 16, max: 30, hex: "#fb923c", chip: "bg-orange-50 text-orange-700 border-orange-200" },
  { key: "d31_mais", label: "Há mais de 30 dias", short: "+30d", min: 31, max: 99_999, hex: "#ef4444", chip: "bg-red-50 text-red-700 border-red-200" },
];

export function bandIndex(dias: number): number {
  if (dias <= 2) return 0;
  if (dias <= 7) return 1;
  if (dias <= 15) return 2;
  if (dias <= 30) return 3;
  return 4;
}

export const bandOf = (dias: number): Band => BANDS[bandIndex(dias)];

/* ─── datas ─── */
export function parseIso(v: string | null | undefined): Date | null {
  if (!v) return null;
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function fmtData(v: string | Date | null | undefined): string {
  const d = v instanceof Date ? v : parseIso(v as string);
  return d ? d.toLocaleDateString("pt-BR") : "—";
}

export function fmtDataCurta(v: string | Date | null | undefined): string {
  const d = v instanceof Date ? v : parseIso(v as string);
  return d ? d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" }) : "—";
}

/** Dias entre a captura (created_at) e agora. É a "idade na fila". */
export function idadeDias(rec: PublicationRecord | null | undefined): number {
  const d = parseIso(rec?.created_at || rec?.creation_date);
  if (!d) return 0;
  return Math.max(0, Math.floor((Date.now() - d.getTime()) / MS_DIA));
}

/** Idade do grupo = a da publicação mais antiga (a que define a urgência). */
export function idadeDoGrupo(g: GroupedRecord): number {
  return g.records.reduce((max, r) => Math.max(max, idadeDias(r)), 0);
}

export function recordMaisAntigo(g: GroupedRecord): PublicationRecord | null {
  if (!g.records.length) return null;
  return g.records.reduce((a, b) => (idadeDias(a) >= idadeDias(b) ? a : b));
}

/** Menor prazo estimado do grupo (ISO date) — null quando nenhum tem prazo. */
export function prazoDoGrupo(g: GroupedRecord): string | null {
  const prazos = g.records.map((r) => r.prazo_estimado).filter(Boolean) as string[];
  if (!prazos.length) return null;
  return prazos.sort()[0];
}

export interface PrazoInfo {
  estado: "vencida" | "vence_hoje" | "no_prazo";
  dias: number;
  label: string;
}

export function prazoInfo(prazoIso: string | null): PrazoInfo | null {
  if (!prazoIso) return null;
  const hoje = new Date();
  hoje.setHours(0, 0, 0, 0);
  const p = new Date(`${prazoIso.slice(0, 10)}T00:00:00`);
  const dias = Math.round((p.getTime() - hoje.getTime()) / MS_DIA);
  if (dias < 0) return { estado: "vencida", dias: -dias, label: `prazo vencido há ${-dias}d` };
  if (dias === 0) return { estado: "vence_hoje", dias: 0, label: "prazo vence hoje" };
  return { estado: "no_prazo", dias, label: `prazo em ${dias}d` };
}

export function labelIdade(dias: number): string {
  if (dias <= 0) return "chegou hoje";
  if (dias === 1) return "na fila há 1 dia";
  return `na fila há ${dias} dias`;
}

/* ─── escritório ─── */

/** Nome curto do escritório: os 2 últimos segmentos do path ("Banco do Brasil · Autor"). */
/**
 * Segmentos que são só continente na hierarquia do L1 — não identificam o
 * escritório. Ficam fora do nome curto: com eles, "Área operacional /
 * Publicações sem pasta" virava "Área operacional · Publicações sem pasta" e
 * o card cortava justamente a metade que importa.
 */
const SEGMENTOS_GENERICOS = /^(mdr advocacia|área operacional|area operacional)$/i;

export function nomeCurtoEscritorio(item: { office_path: string | null; office_name: string }): string {
  const path = (item.office_path || "").trim();
  if (!path) return item.office_name;
  const partes = path.split("/").map((p) => p.trim()).filter(Boolean);
  // Os dois últimos segmentos ÚTEIS: nas carteiras isso dá "Banco do Brasil ·
  // Réu" (carteira + polo); em escritório sem polo dá só o nome dele.
  const uteis = partes.filter((p) => !SEGMENTOS_GENERICOS.test(p));
  const base = uteis.length ? uteis : partes;
  if (base.length <= 1) return base[0] || item.office_name;
  return base.slice(-2).join(" · ");
}

/** Caminho sem o prefixo do escritório raiz, para caber no card. */
export function pathCurto(path: string | null): string {
  if (!path) return "";
  return path.replace(/^MDR Advocacia\s*\/\s*/i, "");
}

/* ─── fuso (BRT ⇄ UTC) ─── */
const BRT_OFFSET_FALLBACK = "-03:00";

/** ISO UTC → "YYYY-MM-DD" no fuso de São Paulo. */
export function isoToBrtDate(iso: string | null | undefined): string {
  const d = parseIso(iso);
  if (!d) return "";
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Sao_Paulo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return fmt.format(d);
}

/** ISO UTC → "HH:MM" no fuso de São Paulo. */
export function isoToBrtTime(iso: string | null | undefined): string {
  const d = parseIso(iso);
  if (!d) return "";
  const fmt = new Intl.DateTimeFormat("pt-BR", {
    timeZone: "America/Sao_Paulo",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return fmt.format(d);
}

/**
 * "YYYY-MM-DD" + "HH:MM" em BRT → ISO UTC com sufixo Z.
 * O L1 lê o número literal como UTC, então a conversão precisa acontecer aqui.
 */
export function brtToUtcIso(date: string, time: string): string {
  const hhmm = (time || "23:59").slice(0, 5);
  const base = new Date(`${date}T${hhmm}:00${BRT_OFFSET_FALLBACK}`);
  if (Number.isNaN(base.getTime())) return "";
  return base.toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** Soma dias úteis (sem feriados — só fim de semana, como fallback do front). */
export function addDiasUteis(base: Date, n: number): Date {
  const d = new Date(base);
  let add = 0;
  while (add < n) {
    d.setDate(d.getDate() + 1);
    const wd = d.getDay();
    if (wd !== 0 && wd !== 6) add += 1;
  }
  return d;
}

export function hojeBrtDate(): string {
  return isoToBrtDate(new Date().toISOString());
}

/**
 * Classificação a exibir para o grupo.
 *
 * `classifications` vem preenchido quando a IA classificou pelo fluxo novo,
 * mas registros antigos (e os reclassificados manualmente) só trazem
 * `category`/`subcategory` na própria publicação — sem esse fallback a tela
 * dizia "sem classificação" para publicação classificada.
 */
export function classificacaoDoGrupo(g: GroupedRecord): Classification | null {
  if (g.classifications?.length) return g.classifications[0];
  const rec = g.records.find((r) => r.category);
  if (!rec) return null;
  return {
    categoria: rec.category || "",
    subcategoria: rec.subcategory || "",
    polo: (rec.polo as Classification["polo"]) || "ambos",
  };
}

/* ─── status do grupo ─── */
export type GroupStatus = "classificado" | "novo" | "sem_template" | "erro";

export function statusDoGrupo(g: GroupedRecord): GroupStatus {
  if (g.records.some((r) => r.status === "ERRO")) return "erro";
  const temClassificacao = g.classifications?.length > 0 || g.records.some((r) => r.category);
  if (!temClassificacao) return "novo";
  if (g.proposed_tasks?.length) return "classificado";
  return "sem_template";
}

export const STATUS_LABEL: Record<GroupStatus, string> = {
  classificado: "CLASSIFICADO",
  novo: "AGUARDANDO IA",
  sem_template: "SEM TEMPLATE",
  erro: "ERRO",
};

/** Iniciais para o avatar do responsável. */
export function iniciais(nome: string | null | undefined): string {
  if (!nome) return "?";
  return nome
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0])
    .join("")
    .toUpperCase();
}

/** Cor estável por nome (mesma pessoa, mesma cor em toda a tela). */
const AVATAR_CORES = [
  "#2563eb", "#7c3aed", "#0891b2", "#ea580c",
  "#16a34a", "#be185d", "#4f46e5", "#b45309",
];
export function corDoNome(nome: string | null | undefined): string {
  const s = nome || "?";
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return AVATAR_CORES[h % AVATAR_CORES.length];
}
