/**
 * PublicationsPage — Busca, revisão e agendamento de publicações judiciais
 *
 * Fluxo:
 *   1. Operador dispara busca (período + escritório + tipo)
 *   2. Motor busca, enriquece, classifica e monta proposta de tarefa
 *   3. Operador revisa processos agrupados, confirma ou edita
 *   4. Ao confirmar → tarefa criada no Legal One
 */

import { useEffect, useMemo, useRef, useState, useCallback, type ComponentType } from "react";
import {
  AlertCircle,
  BarChart as BarChartIcon,
  BookOpen,
  Building2,
  Calendar,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  ChevronUp,
  CheckSquare,
  Clock,
  Eye,
  EyeOff,
  ExternalLink,
  FileDown,
  Filter,
  Layers,
  Link2,
  Loader2,
  MessageSquareWarning,
  Newspaper,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Send,
  Settings,
  ThumbsDown,
  TrendingUp,
  UserCircle2,
  XCircle,
} from "lucide-react";
import { Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import UserSelector from "@/components/ui/UserSelector";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { cn } from "@/lib/utils";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { useToast } from "@/hooks/use-toast";
import { MultiSelect } from "@/components/ui/MultiSelect";
import { apiFetch } from "@/lib/api-client";

const API = "/api/v1/publications";
const API_V1 = "/api/v1";

// Status considerados "pendentes de tratamento final" — sao o foco
// operacional da pagina. Os finalizados (AGENDADO, IGNORADO, OBSOLETO,
// DESCARTADO_DUPLICATA) ficam fora do default; operador marca
// explicitamente nos filtros pra ver. ERRO entra no default porque
// precisa atencao do operador (similar aos ERRO_* em prazos iniciais).
const DEFAULT_PENDING_PUBLICATION_STATUSES = [
  "NOVO",
  "CLASSIFICADO",
  "ERRO",
];
const DEFAULT_PENDING_PUBLICATION_STATUSES_CSV =
  DEFAULT_PENDING_PUBLICATION_STATUSES.join(",");

// ─── Timezone helpers ────────────────────────────────────────────────
// O backend persiste datas/horas como ISO UTC (sufixo "Z"), e o L1
// renderiza em BRT (UTC-3). O modal de agendamento precisa exibir e
// editar em BRT pra bater com o que o usuario ve nas publicacoes e no
// LegalOne. Estes helpers fazem a ponte UTC <-> BRT.
const BR_TZ = "America/Sao_Paulo";

/** Extrai "YYYY-MM-DD" no fuso BRT a partir de um ISO UTC. */
function brtDateFromIso(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: BR_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return fmt.format(d); // "YYYY-MM-DD"
}

/** Extrai "HH:MM" no fuso BRT a partir de um ISO UTC. */
function brtTimeFromIso(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const fmt = new Intl.DateTimeFormat("en-GB", {
    timeZone: BR_TZ,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return fmt.format(d); // "HH:MM"
}

/**
 * Constroi ISO UTC com sufixo "Z" a partir de data + hora locais BRT.
 * dateStr "YYYY-MM-DD" + timeStr "HH:MM" (ou "HH:MM:SS") em BRT.
 * Brasil nao tem mais DST; usamos offset fixo -03:00.
 */
function brtToUtcIso(dateStr: string, timeStr: string): string {
  if (!dateStr) return "";
  const [yStr, mStr, dStr] = dateStr.split("-");
  const [hStr = "0", minStr = "0", sStr = "0"] = timeStr.split(":");
  const y = Number(yStr);
  const m = Number(mStr);
  const d = Number(dStr);
  const h = Number(hStr);
  const min = Number(minStr);
  const s = Number(sStr);
  if ([y, m, d, h, min, s].some((n) => isNaN(n))) return "";
  // BRT = UTC-3, entao UTC = BRT + 3h. Date.UTC normaliza overflow do dia.
  const ms = Date.UTC(y, m - 1, d, h + 3, min, s);
  const out = new Date(ms).toISOString();
  return out.replace(/\.\d{3}Z$/, "Z");
}

// ─── Types ──────────────────────────────────────────────────────────────────

interface Statistics {
  total_records: number;
  by_status: {
    novo: number;
    classificado: number;
    agendado: number;
    ignorado: number;
    erro: number;
    descartado_duplicada: number;
    descartado_obsoleta: number;
    sem_providencia: number;
  };
  operational: {
    pendentes: number;
    aguardando_confirmacao: number;
    agendadas: number;
    sem_providencia: number;
    erros: number;
  };
  total_searches: number;
  last_search: SearchItem | null;
  available_naturezas: string[];
}

type InsightPeriod = "day" | "week" | "month" | "all";

interface OperationalInsights {
  period: InsightPeriod;
  period_label: string;
  bucket_kind: "hour" | "day" | "month";
  generated_at: string;
  window_start: string | null;
  window_end: string;
  current: {
    pendentes: number;
    aguardando_confirmacao: number;
    agendadas: number;
    sem_providencia: number;
    erros: number;
    total_monitorado: number;
  };
  summary: {
    recebidas: number;
    pendentes: number;
    aguardando_confirmacao: number;
    agendadas: number;
    sem_providencia: number;
    erros: number;
    buscas: number;
  };
  series: Array<{
    bucket_start: string;
    received: number;
    pending: number;
    awaiting_confirmation: number;
    scheduled: number;
    without_providence: number;
    errors: number;
  }>;
}

interface SearchItem {
  id: number;
  status: string;
  date_from: string;
  date_to: string | null;
  origin_type: string;
  office_filter: string | null;
  total_found: number;
  total_new: number;
  total_duplicate: number;
  progress_step: string | null;
  progress_detail: string | null;
  progress_pct: number | null;
  requested_by_email: string | null;
  error_message: string | null;
  created_at: string | null;
  finished_at: string | null;
}

interface Classification {
  categoria: string;
  subcategoria: string;
  polo: "ativo" | "passivo" | "ambos";
  confianca?: string;
  justificativa?: string;
  audiencia_data?: string | null;
  audiencia_hora?: string | null;
  audiencia_link?: string | null;
  // Prazo fatal — identificado pelo classificador a partir do texto da
  // publicação (CPC). Null quando a publicação NÃO abre prazo.
  prazo_dias?: number | null;
  prazo_tipo?: "util" | "corrido" | null;
  prazo_fundamentacao?: string | null;
}

interface PublicationRecord {
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
  polo: "ativo" | "passivo" | "ambos" | null;
  audiencia_data: string | null;
  audiencia_hora: string | null;
  audiencia_link: string | null;
  natureza_processo: string | null;
  classifications: Classification[] | null;
  // Trilha de autoria do agendamento (pub002). Só preenchido quando status=AGENDADO.
  scheduled_by_user_id?: number | null;
  scheduled_by_email?: string | null;
  scheduled_by_name?: string | null;
  scheduled_at?: string | null;
  created_at: string | null;
  raw_relationships?: any;
}

interface PublicationBatch {
  id: number;
  anthropic_batch_id: string | null;
  status: "ENVIADO" | "EM_PROCESSAMENTO" | "PRONTO" | "APLICADO" | "FALHA" | "CANCELADO";
  anthropic_status: string | null;
  total_records: number;
  succeeded_count: number;
  errored_count: number;
  expired_count: number;
  canceled_count: number;
  model_used: string | null;
  requested_by_email: string | null;
  error_message: string | null;
  created_at: string | null;
  submitted_at: string | null;
  ended_at: string | null;
  applied_at: string | null;
  error_details: Record<string, string> | null;
}

interface SuggestedResponsible {
  id: number;
  name: string | null;
  email: string | null;
  source: string;
}

interface ProposedTask {
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
}

// Etiqueta (tag) do processo no L1 — vem do cache do backend (caminho web).
interface L1Etiqueta {
  id: number | null;
  name: string | null;
  class_name: string | null; // ex.: "tag-color-orange" (classe do próprio L1)
  color_id: number | null;
}

interface GroupedRecord {
  lawsuit_id: number | null;
  lawsuit_cnj: string | null;
  office_id: number | null;
  records: PublicationRecord[];
  proposed_task: ProposedTask | null;
  proposed_tasks: ProposedTask[];
  classifications: Classification[];
  // null/undefined = ainda não enriquecido; [] = consultado e sem etiqueta.
  l1_etiquetas?: L1Etiqueta[] | null;
}

// Mapa das cores de etiqueta do L1 → classes Tailwind (fallback: cinza).
const L1_TAG_COLORS: Record<string, string> = {
  "tag-color-red": "bg-red-100 text-red-800 border-red-300",
  "tag-color-orange": "bg-orange-100 text-orange-800 border-orange-300",
  "tag-color-yellow": "bg-amber-100 text-amber-800 border-amber-300",
  "tag-color-green": "bg-emerald-100 text-emerald-800 border-emerald-300",
  "tag-color-lime": "bg-lime-100 text-lime-800 border-lime-300",
  "tag-color-teal": "bg-teal-100 text-teal-800 border-teal-300",
  "tag-color-blue": "bg-blue-100 text-blue-800 border-blue-300",
  "tag-color-indigo": "bg-indigo-100 text-indigo-800 border-indigo-300",
  "tag-color-purple": "bg-purple-100 text-purple-800 border-purple-300",
  "tag-color-pink": "bg-pink-100 text-pink-800 border-pink-300",
  "tag-color-gray": "bg-slate-100 text-slate-700 border-slate-300",
};

function L1EtiquetaChips({ etiquetas }: { etiquetas?: L1Etiqueta[] | null }) {
  if (!etiquetas || etiquetas.length === 0) return null;
  return (
    <div className="mt-1 flex max-w-[150px] flex-wrap gap-1">
      {etiquetas.map((t, i) => {
        const cores = L1_TAG_COLORS[t.class_name ?? ""] ?? "bg-slate-100 text-slate-700 border-slate-300";
        const destaque = /ESTRAT|PRIORI|URGEN/i.test(t.name ?? "");
        return (
          <span
            key={`${t.id ?? i}`}
            title={`Etiqueta do processo no Legal One: ${t.name ?? ""}`}
            className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-semibold leading-none ${cores} ${
              destaque ? "ring-1 ring-current" : ""
            }`}
          >
            {destaque ? "★ " : ""}{t.name}
          </span>
        );
      })}
    </div>
  );
}

interface GroupedResponse {
  total_groups: number;
  total_records?: number;
  offset: number;
  limit: number;
  groups: GroupedRecord[];
}

interface Office {
  id: number;
  external_id: number;
  name: string;
  path: string;
}

interface TaskSubtype {
  external_id: number;
  name: string;
}

interface TaskType {
  external_id: number;
  name: string;
  subtypes: TaskSubtype[];
}

interface AppUser {
  external_id: number;
  name: string;
  email: string | null;
}

/** Retorna o label completo do escritório: usa path se disponível, senão name */
const officeLabel = (o: Office) => o.path || o.name;

// ─── Helpers ────────────────────────────────────────────────────────────────

const statusColor = (status: string): "default" | "secondary" | "destructive" | "outline" => {
  const map: Record<string, any> = {
    CONCLUIDO: "default", EXECUTANDO: "secondary", FALHA: "destructive", CANCELADO: "outline",
    NOVO: "secondary", CLASSIFICADO: "default", AGENDADO: "default", IGNORADO: "outline", ERRO: "destructive",
    DESCARTADO_DUPLICADA: "outline", DESCARTADO_OBSOLETA: "outline",
  };
  return map[status] || "secondary";
};

const formatDate = (iso: string | null) => {
  if (!iso) return "-";
  try { return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }); }
  catch { return iso; }
};

const formatDateShort = (iso: string | null) => {
  if (!iso) return "-";
  try { return new Date(iso).toLocaleDateString("pt-BR"); }
  catch { return iso; }
};

const INSIGHT_PERIOD_OPTIONS: Array<{ value: InsightPeriod; label: string }> = [
  { value: "day", label: "Hoje" },
  { value: "week", label: "Semana" },
  { value: "month", label: "Mês" },
  { value: "all", label: "Tudo" },
];

const formatInsightBucketLabel = (iso: string, bucketKind: OperationalInsights["bucket_kind"]) => {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  if (bucketKind === "hour") {
    return date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  }
  if (bucketKind === "month") {
    return date.toLocaleDateString("pt-BR", { month: "short", year: "2-digit" });
  }
  return date.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
};

/** Badge label/color for the polo tag (ativo/passivo/ambos). */
const poloLabel = (polo: string | null | undefined) => {
  if (!polo) return null;
  const map: Record<string, { label: string; className: string }> = {
    ativo: { label: "Polo Ativo", className: "bg-emerald-100 text-emerald-800 border-emerald-300" },
    passivo: { label: "Polo Passivo", className: "bg-rose-100 text-rose-800 border-rose-300" },
    ambos: { label: "Ambos os Polos", className: "bg-sky-100 text-sky-800 border-sky-300" },
  };
  return map[polo] || null;
};

/**
 * Extrai UF (ou região) a partir do CNJ. Formato: NNNNNNN-DD.AAAA.J.TR.OOOO
 *   J=8 (Justiça Estadual) → TR identifica o TJ, mapeado para UF.
 *   J=4 (Justiça Federal) → TR é a região (TRF1..TRF6).
 *   J=7 (Justiça do Trabalho) → TR é a região (TRT1..TRT24).
 * Retorna null se o padrão não bater.
 */
const ufFromCnj = (cnj: string | null | undefined): string | null => {
  if (!cnj) return null;
  const digits = cnj.replace(/\D/g, "");
  if (digits.length !== 20) return null;
  // Posições no CNJ sem pontuação: 0-6 num, 7-8 dv, 9-12 ano, 13 J, 14-15 TR, 16-19 origem
  const j = digits.charAt(13);
  const tr = digits.substring(14, 16);
  const ESTADUAL: Record<string, string> = {
    "01": "AC", "02": "AL", "03": "AP", "04": "AM", "05": "BA", "06": "CE",
    "07": "DF", "08": "ES", "09": "GO", "10": "MA", "11": "MT", "12": "MS",
    "13": "MG", "14": "PA", "15": "PB", "16": "PR", "17": "PE", "18": "PI",
    "19": "RJ", "20": "RN", "21": "RS", "22": "RO", "23": "RR", "24": "SC",
    "25": "SP", "26": "SE", "27": "TO",
  };
  if (j === "8") return ESTADUAL[tr] ?? null;
  if (j === "4") return `TRF${parseInt(tr, 10) || tr}`;
  if (j === "7") return `TRT${parseInt(tr, 10) || tr}`;
  if (j === "5") return `JME${parseInt(tr, 10) || tr}`; // Justiça Militar
  if (j === "6") return `TRE-${ESTADUAL[tr] ?? tr}`;    // Justiça Eleitoral
  return null;
};

const batchStatusColor = (status: string): "default" | "secondary" | "destructive" | "outline" => {
  const map: Record<string, any> = {
    ENVIADO: "secondary",
    EM_PROCESSAMENTO: "secondary",
    PRONTO: "default",
    APLICADO: "default",
    FALHA: "destructive",
    CANCELADO: "outline",
  };
  return map[status] || "secondary";
};

const batchStatusLabel = (status: string): string => {
  const map: Record<string, string> = {
    ENVIADO: "Enviado",
    EM_PROCESSAMENTO: "Em processamento",
    PRONTO: "Pronto",
    APLICADO: "Aplicado",
    FALHA: "Falha",
    CANCELADO: "Cancelado",
  };
  return map[status] || status;
};

const OperationalStatCard = ({
  title,
  value,
  hint,
  tone,
  icon: Icon,
}: {
  title: string;
  value: number;
  hint?: string;
  tone: string;
  icon: ComponentType<{ className?: string }>;
}) => (
  <Card>
    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
      <CardTitle className="text-sm font-medium">{title}</CardTitle>
      <div className={`rounded-lg p-2 ${tone}`}>
        <Icon className="h-4 w-4" />
      </div>
    </CardHeader>
    <CardContent>
      <div className="text-2xl font-bold">{value}</div>
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </CardContent>
  </Card>
);

// ─── SubtypePicker ─────────────────────────────────────────────────────────
// Combobox com busca pro campo "Subtipo de tarefa" (~900 itens no catalogo
// do L1). Troca o Select tradicional por Popover+Command pra permitir
// filtro instantaneo. A busca casa tanto no nome do subtipo quanto no nome
// do tipo pai — via concatenacao "tipo::subtipo" no value do CommandItem,
// que eh onde o cmdk aplica o matcher.
interface SubtypePickerProps {
  value: number | null;
  parentType: TaskType | null;
  taskTypes: TaskType[];
  onChange: (subId: number, parentType: TaskType | null) => void;
}

const SubtypePicker = ({ value, parentType, taskTypes, onChange }: SubtypePickerProps) => {
  const [open, setOpen] = useState(false);

  // Label do botao: "Tipo · Subtipo" quando ha selecao, placeholder caso contrario.
  const selectedLabel = (() => {
    if (!value) return null;
    for (const t of taskTypes) {
      const s = t.subtypes.find((x) => x.external_id === value);
      if (s) return { typeName: t.name, subName: s.name };
    }
    return null;
  })();

  return (
    <div className="grid gap-1.5">
      <Label className="text-xs font-medium">Subtipo de tarefa *</Label>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            role="combobox"
            aria-expanded={open}
            className="h-9 w-full justify-between text-sm font-normal"
          >
            {selectedLabel ? (
              <span className="truncate">
                <span className="text-muted-foreground">{selectedLabel.typeName} · </span>
                <span className="font-medium">{selectedLabel.subName}</span>
              </span>
            ) : (
              <span className="text-muted-foreground">Selecione o subtipo</span>
            )}
            <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent
          className="w-[--radix-popover-trigger-width] p-0"
          align="start"
        >
          <Command
            // Matcher customizado: busca case-insensitive sem acento em
            // "tipo | subtipo". O cmdk passa o `value` bruto do CommandItem
            // (que cadastramos como "tipo::subtipo::id") e o termo digitado.
            filter={(itemValue, search) => {
              const norm = (s: string) =>
                s.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
              return norm(itemValue).includes(norm(search)) ? 1 : 0;
            }}
          >
            <CommandInput placeholder="Buscar por tipo ou subtipo..." />
            <CommandList className="max-h-80">
              <CommandEmpty>Nenhum resultado.</CommandEmpty>
              {taskTypes.map((t) => (
                <CommandGroup key={t.external_id} heading={t.name}>
                  {t.subtypes.map((s) => {
                    const itemValue = `${t.name}::${s.name}::${s.external_id}`;
                    const isSelected = value === s.external_id;
                    return (
                      <CommandItem
                        key={s.external_id}
                        value={itemValue}
                        onSelect={() => {
                          onChange(s.external_id, t);
                          setOpen(false);
                        }}
                      >
                        <Check
                          className={cn(
                            "mr-2 h-4 w-4",
                            isSelected ? "opacity-100" : "opacity-0"
                          )}
                        />
                        <span className="truncate">{s.name}</span>
                      </CommandItem>
                    );
                  })}
                </CommandGroup>
              ))}
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
    </div>
  );
};

// ─── Component ──────────────────────────────────────────────────────────────

const PublicationsPage = () => {
  const { toast } = useToast();

  const [offices, setOffices] = useState<Office[]>([]);
  const [taskTypes, setTaskTypes] = useState<TaskType[]>([]);
  const [appUsers, setAppUsers] = useState<AppUser[]>([]);
  // Adapta `appUsers` (catalogo /api/v1/users sem squads) ao shape
  // SelectableUser do UserSelector. Email preservado pra que `showEmail`
  // funcione e `cmdk` filtre por email tambem.
  const appUsersForPicker = useMemo(
    () =>
      appUsers.map((u) => ({
        id: u.external_id,
        external_id: u.external_id,
        name: u.name,
        email: u.email,
        squads: [],
      })),
    [appUsers],
  );
  const [taxonomy, setTaxonomy] = useState<Record<string, string[]>>({});
  const [reclassifyingGroup, setReclassifyingGroup] = useState<string | null>(null);
  const [stats, setStats] = useState<Statistics | null>(null);
  const [insightsOpen, setInsightsOpen] = useState(false);
  const [insightsLoading, setInsightsLoading] = useState(false);
  const [insightPeriod, setInsightPeriod] = useState<InsightPeriod>("week");
  const [insights, setInsights] = useState<OperationalInsights | null>(null);

  // Search form
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [originType, setOriginType] = useState("OfficialJournalsCrawler");
  const [searchOfficeId, setSearchOfficeId] = useState<string>("");
  const [searchOnlyUnlinked, setSearchOnlyUnlinked] = useState(false);
  const [indexStatus, setIndexStatus] = useState<{
    total_ids: number;
    in_progress: boolean;
    progress_pct: number;
    last_full_sync_at: string | null;
    last_sync_status: string | null;
    is_fresh: boolean;
  } | null>(null);
  const [isSearching, setIsSearching] = useState(false);

  // Search history
  const [searches, setSearches] = useState<SearchItem[]>([]);
  const [searchesExpanded, setSearchesExpanded] = useState(false);
  // Paginacao client-side do Historico de Buscas. Default 10 por pagina;
  // a lista vem com ate 100 do servidor. Quando o operador filtra ou
  // recarrega, o offset eh resetado pra 0 no useEffect que monitora
  // searches.length.
  const [searchesPage, setSearchesPage] = useState(0);
  const [searchesPageSize, setSearchesPageSize] = useState<10 | 25 | 50>(10);

  // Grouped records
  const [grouped, setGrouped] = useState<GroupedResponse | null>(null);
  // Default = pendentes de tratamento final (NOVO + CLASSIFICADO + ERRO).
  // Os finalizados (AGENDADO, IGNORADO, OBSOLETO, DESCARTADO_DUPLICATA)
  // ficam fora do default; operador marca explicitamente nos filtros pra
  // ver. Indicador visual no header da listagem avisa que o filtro padrao
  // esta ativo + botao "Mostrar todos" pra zerar so o filtro de status.
  const [filterStatus, setFilterStatus] = useState<string>(
    DEFAULT_PENDING_PUBLICATION_STATUSES_CSV,
  );
  const [filterOffice, setFilterOffice] = useState<string>("");
  const [filterDateFrom, setFilterDateFrom] = useState<string>("");
  const [filterDateTo, setFilterDateTo] = useState<string>("");
  const [filterCategory, setFilterCategory] = useState<string>("");
  const [filterUf, setFilterUf] = useState<string>("");
  const [filterVinculo, setFilterVinculo] = useState<string>("");
  const [filterNatureza, setFilterNatureza] = useState<string>("");
  const [filterPolo, setFilterPolo] = useState<string>("");
  // Busca livre por CNJ — backend faz match tolerante por dígitos, então o
  // usuário pode digitar "0000161", "161-07", ou o CNJ inteiro com máscara.
  const [filterCnj, setFilterCnj] = useState<string>("");
  // CSV de user_ids (LegalOneUser.id) — operador que cadastrou (scheduled_by_user_id).
  const [filterScheduledBy, setFilterScheduledBy] = useState<string>("");
  // Controla se o painel de filtros está expandido no mobile (no desktop fica sempre visível via md:block)
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);
  const [groupPage, setGroupPage] = useState(0);
  // Tamanho de página — operador escolhe entre 20/50/100.
  const [groupPageSize, setGroupPageSize] = useState<20 | 50 | 100>(20);

  // Detail dialog
  const [selectedRecord, setSelectedRecord] = useState<PublicationRecord | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);

  /**
   * Sumário de tarefas L1 do processo no detalhe da publicação.
   * Busca: todas pendentes (status 0/1/2) + 5 últimas concluídas.
   * Renderiza só quando `selectedRecord.linked_lawsuit_id` existe.
   * Cache curto no backend (15s) absorve abre/fecha sequencial.
   */
  const [recentTasks, setRecentTasks] = useState<{
    pending: any[];
    recent_completed: any[];
    pending_count: number;
    recent_completed_count: number;
    truncated: boolean;
    check_failed: boolean;
  } | null>(null);
  const [recentTasksLoading, setRecentTasksLoading] = useState(false);

  // Schedule dialog
  const [scheduleGroup, setScheduleGroup] = useState<GroupedRecord | null>(null);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [editedPayloads, setEditedPayloads] = useState<Partial<ProposedTask>[]>([]);
  // Snapshot do responsavel original de cada task quando o modal abre.
  // Indexado pelo idx no `editedPayloads`. Diferenca entre original e atual
  // = override manual → regra do assistente nao se aplica.
  const [originalResponsibleByIndex, setOriginalResponsibleByIndex] = useState<Map<number, number | null>>(new Map());
  // Índices (em editedPayloads) onde o OPERADOR mexeu no responsável —
  // inclusive re-escolhendo a MESMA pessoa da proposta. Regra de negócio:
  // a decisão final do operador SEMPRE vence a regra automática de
  // squad/assistente (comparar ids não detecta a re-escolha deliberada).
  const [touchedResponsible, setTouchedResponsible] = useState<Set<number>>(new Set());
  const [scheduling, setScheduling] = useState(false);
  // Checagem de duplicatas (tarefa pendente no L1) — Onda 1.
  // Estrutura: { [subTypeId]: [{task_id, description, status_label, end_date_time, l1_url}] }
  const [duplicatesBySubtype, setDuplicatesBySubtype] = useState<Record<number, any[]>>({});
  const [duplicateCheckLoading, setDuplicateCheckLoading] = useState(false);
  const [duplicateCheckFailed, setDuplicateCheckFailed] = useState(false);

  // Bulk selection de grupos (Processos com Publicações)
  const [selectedGroupKeys, setSelectedGroupKeys] = useState<Set<string>>(new Set());
  const [bulkProcessing, setBulkProcessing] = useState(false);
  // "Selecionar todos os filtrados" — quando o operador escolhe agir em
  // toda a base que bate com os filtros (não só a página visível), a
  // gente pré-carrega TODOS os grupos do filtro pra esse state e os
  // bulk handlers operam sobre ele. Mantém intocada a paginação visual.
  const [bulkScopeAllGroups, setBulkScopeAllGroups] = useState<GroupedRecord[]>([]);
  const [bulkScopeLoading, setBulkScopeLoading] = useState(false);

  // Batch classification
  const [batches, setBatches] = useState<PublicationBatch[]>([]);
  // Paginação client-side da listagem de batches. Antes a tabela usava
  // <ScrollArea> que não cooperava com o conteúdo dinâmico (mostrava só
  // 3 linhas mesmo com 12 batches). 10/página cabe sem scroll vertical
  // e dá controle pra navegar conforme o histórico cresce.
  const [batchPage, setBatchPage] = useState(0);
  const BATCHES_PAGE_SIZE = 10;
  const [batchesExpanded, setBatchesExpanded] = useState(true);
  const [submittingBatch, setSubmittingBatch] = useState(false);
  const [refreshingBatchId, setRefreshingBatchId] = useState<number | null>(null);
  const [applyingBatchId, setApplyingBatchId] = useState<number | null>(null);
  const [batchOfficeId, setBatchOfficeId] = useState<string>("");
  const [batchLimit, setBatchLimit] = useState<string>("");
  const [batchOnlyUnlinked, setBatchOnlyUnlinked] = useState(false);
  const [retryingBatchId, setRetryingBatchId] = useState<number | null>(null);
  const [errorDetailsBatchId, setErrorDetailsBatchId] = useState<number | null>(null);
  const [removedTaskIndices, setRemovedTaskIndices] = useState<Set<number>>(new Set());

  // Feedback explícito (thumbs-down)
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackRecord, setFeedbackRecord] = useState<PublicationRecord | null>(null);
  const [feedbackErrorType, setFeedbackErrorType] = useState<string>("category");
  const [feedbackCategory, setFeedbackCategory] = useState<string>("");
  const [feedbackSubcategory, setFeedbackSubcategory] = useState<string>("");
  const [feedbackPolo, setFeedbackPolo] = useState<string>("");
  const [feedbackNatureza, setFeedbackNatureza] = useState<string>("");
  const [feedbackNote, setFeedbackNote] = useState<string>("");
  const [submittingFeedback, setSubmittingFeedback] = useState(false);

  const [error, setError] = useState<string | null>(null);

  // ─── Duplicate divergences ───────────────────────────────────────────
  const [showDuplicates, setShowDuplicates] = useState(false);
  const [loadingDuplicates, setLoadingDuplicates] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [savedFilters, setSavedFilters] = useState<any[]>([]);
  const [isSaveFilterDialogOpen, setIsSaveFilterDialogOpen] = useState(false);
  const [filterName, setFilterName] = useState("");
  const [isFilterDefault, setIsFilterDefault] = useState(false);
  const [isSavingFilter, setIsSavingFilter] = useState(false);
  const [duplicates, setDuplicates] = useState<{
    total: number;
    divergences: Array<{
      legal_one_update_id: number;
      original: PublicationRecord;
      duplicate: PublicationRecord;
    }>;
  } | null>(null);

  // ─── Data loading ────────────────────────────────────────────────────

  const loadOffices = useCallback(async () => {
    try {
      const res = await apiFetch("/api/v1/offices");
      if (res.ok) setOffices(await res.json());
    } catch { /* ignore */ }
  }, []);

  const loadTaskMeta = useCallback(async () => {
    try {
      const [ttRes, usrRes, taxRes] = await Promise.all([
        apiFetch("/api/v1/task-templates/meta/task-types"),
        apiFetch("/api/v1/task-templates/meta/users"),
        apiFetch("/api/v1/publications/classification-taxonomy"),
      ]);
      if (ttRes.ok) setTaskTypes(await ttRes.json());
      if (usrRes.ok) setAppUsers(await usrRes.json());
      if (taxRes.ok) {
        const data = await taxRes.json();
        setTaxonomy(data.taxonomy || {});
      }
    } catch { /* ignore */ }
  }, []);

  const loadStats = useCallback(async () => {
    try {
      const res = await apiFetch(`${API}/statistics`);
      if (res.ok) setStats(await res.json());
    } catch { /* ignore */ }
  }, []);

  const loadInsights = useCallback(async (period: InsightPeriod) => {
    setInsightsLoading(true);
    try {
      const res = await apiFetch(`${API}/insights?period=${period}`);
      if (!res.ok) throw new Error("Falha ao carregar indicadores.");
      setInsights(await res.json());
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Falha ao carregar indicadores.";
      toast({ title: "Erro", description: msg, variant: "destructive" });
    } finally {
      setInsightsLoading(false);
    }
  }, [toast]);

  const loadSearches = useCallback(async () => {
    try {
      // limit=100 pra carregar uma janela razoavel de historico — a UI
      // pagina client-side em chunks (default 10 por pagina). Se precisar
      // de mais, operador filtra por data ou abre o admin direto.
      const res = await apiFetch(`${API}/searches?limit=100`);
      if (res.ok) setSearches(await res.json());
    } catch { /* ignore */ }
  }, []);

  // Cancelamento cooperativo: o backend (PublicationSearchService) verifica
  // search.status a cada commit de lote de 500 no PERSIST. A interrupção
  // pode demorar alguns segundos — o tempo do lote em andamento terminar.
  // Registros já persistidos em lotes anteriores são mantidos.
  const cancelSearch = useCallback(async (searchId: number) => {
    if (!window.confirm(
      `Cancelar Busca #${searchId}?\n\nRegistros já persistidos em lotes anteriores serão mantidos. A interrupção pode levar alguns segundos até o lote atual terminar.`
    )) return;
    try {
      const res = await apiFetch(`${API}/searches/${searchId}/cancel`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Falha ao cancelar busca.");
      }
      toast({
        title: "Busca cancelada",
        description: `Busca #${searchId} será interrompida no próximo lote.`,
      });
      await loadSearches();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Erro desconhecido.";
      toast({ title: "Erro", description: msg, variant: "destructive" });
    }
  }, [toast, loadSearches]);

  const loadDuplicates = useCallback(async () => {
    setLoadingDuplicates(true);
    try {
      const res = await apiFetch(`${API}/records/duplicate-divergences?limit=50`);
      if (res.ok) setDuplicates(await res.json());
    } catch { /* ignore */ }
    finally { setLoadingDuplicates(false); }
  }, []);

  // Guarda de sequencia: so aplica a resposta de loadGrouped se for a mais
  // recente. Sem isso, ao trocar de filtro rapido a resposta lenta de um
  // filtro antigo chega por ultimo e sobrescreve a contagem/linhas atuais.
  const latestGroupedLoadId = useRef(0);
  // Debounce das trocas de filtro (handleFilterChange): um burst de cliques
  // em multi-select vira um unico fetch — cada fetch roda varias queries
  // pesadas no backend, entao sem debounce a tela trava.
  const filterFetchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadGrouped = useCallback(async (
    page = 0, status = "", officeId = "", dateFrom = "", dateTo = "", category = "", ufParam = "", vinculoParam = "", naturezaParam = "", poloParam = "", cnjParam = "", scheduledByParam = "",
  ) => {
    const loadId = ++latestGroupedLoadId.current;
    try {
      let url = `${API}/records/grouped?limit=${groupPageSize}&offset=${page * groupPageSize}`;
      if (status) url += `&status=${status}`;
      if (officeId) url += `&linked_office_id=${officeId}`;
      if (dateFrom) url += `&date_from=${dateFrom}`;
      if (dateTo) url += `&date_to=${dateTo}`;
      if (category) url += `&category=${encodeURIComponent(category)}`;
      if (ufParam) url += `&uf=${encodeURIComponent(ufParam)}`;
      if (vinculoParam) url += `&vinculo=${vinculoParam}`;
      if (naturezaParam) url += `&natureza=${encodeURIComponent(naturezaParam)}`;
      if (poloParam) url += `&polo=${encodeURIComponent(poloParam)}`;
      if (cnjParam) url += `&cnj_search=${encodeURIComponent(cnjParam)}`;
      if (scheduledByParam) url += `&scheduled_by_user_id=${encodeURIComponent(scheduledByParam)}`;
      const res = await apiFetch(url);
      if (!res.ok) return;
      const data = await res.json();
      // Descarta resposta obsoleta: outra chamada de loadGrouped comecou
      // depois desta (filtro/pagina mais novos) — ela e a fonte de verdade.
      if (loadId !== latestGroupedLoadId.current) return;
      setGrouped(data);
    } catch { /* ignore */ }
    // groupPageSize precisa estar nas deps senão o useCallback captura
    // o valor inicial (20) e ignora o dropdown — bug que fazia "100 por
    // página" nunca aplicar. Outras deps são args explícitos da função.
  }, [groupPageSize]);

  const handleExportExcel = async () => {
    if (isExporting) return;
    setIsExporting(true);
    try {
      const params = new URLSearchParams();
      if (filterStatus) params.set("status", filterStatus);
      if (filterOffice) params.set("linked_office_id", filterOffice);
      if (filterDateFrom) params.set("date_from", filterDateFrom);
      if (filterDateTo) params.set("date_to", filterDateTo);
      if (filterCategory) params.set("category", filterCategory);
      if (filterUf) params.set("uf", filterUf);
      if (filterVinculo) params.set("vinculo", filterVinculo);
      if (filterNatureza) params.set("natureza", filterNatureza);
      if (filterPolo) params.set("polo", filterPolo);
      if (filterCnj) params.set("cnj_search", filterCnj);
      if (filterScheduledBy) params.set("scheduled_by_user_id", filterScheduledBy);
      const qs = params.toString();
      const url = `${API}/records/grouped/export${qs ? `?${qs}` : ""}`;

      const res = await apiFetch(url);
      if (!res.ok) {
        throw new Error(`Falha ao exportar (HTTP ${res.status}).`);
      }
      const blob = await res.blob();

      // Extrai o filename do Content-Disposition quando presente,
      // senão usa um nome padrão com timestamp local.
      let filename = `publicacoes-${new Date()
        .toISOString()
        .replace(/[-:T]/g, "")
        .slice(0, 14)}.xlsx`;
      const disposition = res.headers.get("content-disposition") || "";
      const match = disposition.match(/filename="?([^";]+)"?/i);
      if (match?.[1]) filename = match[1];

      const blobUrl = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = blobUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(blobUrl);
    } catch (err) {
      console.error(err);
      setError(err instanceof Error ? err.message : "Erro ao exportar Excel.");
    } finally {
      setIsExporting(false);
    }
  };

  const loadRecordDetail = async (id: number) => {
    try {
      const res = await apiFetch(`${API}/records/${id}`);
      if (res.ok) { setSelectedRecord(await res.json()); setDetailOpen(true); }
    } catch { /* ignore */ }
  };

  const loadBatches = useCallback(async () => {
    try {
      const res = await apiFetch(`${API}/classify-batch?limit=50`);
      if (res.ok) setBatches(await res.json());
    } catch { /* ignore */ }
  }, []);

  const loadSavedFilters = useCallback(async () => {
    try {
      const res = await apiFetch("/api/v1/me/saved-filters?module=publications");
      if (res.ok) {
        const data = await res.json();
        setSavedFilters(Array.isArray(data) ? data : data.items || []);
      }
    } catch (err) {
      console.error("Erro ao carregar filtros salvos:", err);
    }
  }, []);

  const handleSaveFilter = async () => {
    if (!filterName.trim()) {
      toast({ title: "Erro", description: "Digite um nome para o filtro.", variant: "destructive" });
      return;
    }
    setIsSavingFilter(true);
    try {
      const filtersJson = {
        status: filterStatus,
        office: filterOffice,
        dateFrom: filterDateFrom,
        dateTo: filterDateTo,
        category: filterCategory,
        uf: filterUf,
        vinculo: filterVinculo,
        natureza: filterNatureza,
        polo: filterPolo,
      };
      const res = await apiFetch("/api/v1/me/saved-filters", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: filterName,
          module: "publications",
          filters_json: filtersJson,
          is_default: isFilterDefault,
        }),
      });
      if (!res.ok) throw new Error("Falha ao salvar filtro");
      toast({ title: "Sucesso", description: "Filtro salvo com sucesso." });
      setIsSaveFilterDialogOpen(false);
      setFilterName("");
      setIsFilterDefault(false);
      loadSavedFilters();
    } catch (err: any) {
      toast({ title: "Erro", description: err.message, variant: "destructive" });
    } finally {
      setIsSavingFilter(false);
    }
  };

  const handleApplySavedFilter = (filter: any) => {
    try {
      const parsed = typeof filter.filters_json === 'string' ? JSON.parse(filter.filters_json) : filter.filters_json;
      handleFilterChange(parsed.status || "", parsed.office || "", parsed.dateFrom, parsed.dateTo, parsed.category, parsed.uf || "", parsed.vinculo || "", parsed.natureza || "", parsed.polo || "");
    } catch (err) {
      toast({ title: "Erro", description: "Não foi possível aplicar o filtro.", variant: "destructive" });
    }
  };

  const handleDeleteFilter = async (filterId: number) => {
    try {
      const res = await apiFetch(`/api/v1/me/saved-filters/${filterId}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Falha ao deletar filtro");
      toast({ title: "Sucesso", description: "Filtro deletado." });
      loadSavedFilters();
    } catch (err: any) {
      toast({ title: "Erro", description: err.message, variant: "destructive" });
    }
  };

  useEffect(() => {
    loadOffices();
    loadTaskMeta();
    loadStats();
    loadSearches();
    // Passa o estado inicial dos filtros (filterStatus ja nasce com o default
    // de pendentes) — antes ia tudo vazio e a 1a carga trazia TODOS os status,
    // divergindo dos chips de Status exibidos.
    loadGrouped(0, filterStatus, filterOffice, filterDateFrom, filterDateTo, filterCategory, filterUf, filterVinculo, filterNatureza, filterPolo, filterCnj, filterScheduledBy);
    loadBatches();
    loadSavedFilters();
  }, []);

  useEffect(() => {
    if (!insightsOpen) return;
    loadInsights(insightPeriod);
  }, [insightsOpen, insightPeriod, loadInsights]);

  // Re-fetch quando o operador muda o tamanho de página. Separado do
  // evento do Select pra evitar stale closure em loadGrouped (o useCallback
  // captura o state antigo senão). Só dispara depois do primeiro mount,
  // pra não refazer o load inicial da montagem.
  const isFirstPageSizeEffect = useRef(true);
  useEffect(() => {
    if (isFirstPageSizeEffect.current) {
      isFirstPageSizeEffect.current = false;
      return;
    }
    loadGrouped(
      0, filterStatus, filterOffice, filterDateFrom, filterDateTo,
      filterCategory, filterUf, filterVinculo, filterNatureza,
      filterPolo, filterCnj, filterScheduledBy,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupPageSize]);

  // ─── Office Lawsuit Index ─────────────────────────────────────────────
  const loadIndexStatus = useCallback(async (officeId: string) => {
    // Multi-office: não carrega status individual (UI já esconde o painel).
    if (!officeId || officeId.includes(",")) {
      setIndexStatus(null);
      return;
    }
    try {
      const res = await apiFetch(`${API_V1}/offices/${officeId}/lawsuit-index`);
      if (res.ok) setIndexStatus(await res.json());
    } catch { /* ignore */ }
  }, []);

  const handleSyncIndex = async (forceFull: boolean = false) => {
    if (!searchOfficeId || searchOfficeId.includes(",")) return;
    try {
      const res = await apiFetch(
        `${API_V1}/offices/${searchOfficeId}/lawsuit-index/sync?force_full=${forceFull}`,
        { method: "POST" }
      );
      if (res.ok) setIndexStatus(await res.json());
    } catch (err: any) {
      toast({ title: "Erro", description: err.message, variant: "destructive" });
    }
  };

  useEffect(() => {
    loadIndexStatus(searchOfficeId);
  }, [searchOfficeId, loadIndexStatus]);

  useEffect(() => {
    if (!searchOfficeId || !indexStatus?.in_progress) return;
    const t = setInterval(() => loadIndexStatus(searchOfficeId), 3000);
    return () => clearInterval(t);
  }, [searchOfficeId, indexStatus?.in_progress, loadIndexStatus]);

  // ─── Fetch tarefas L1 quando abre detalhe da publicação ─────────────
  // Auto-load: assim que `detailOpen` vira true E o record tem
  // `linked_lawsuit_id`, busca pending+recent. Limpa estado anterior pra
  // não vazar dados de uma publicação pra outra (lawsuit diferente).
  // Cache no backend (TTL 15s) absorve operador abrindo/fechando rápido.
  useEffect(() => {
    if (!detailOpen || !selectedRecord?.linked_lawsuit_id) {
      setRecentTasks(null);
      return;
    }
    const lawsuitId = selectedRecord.linked_lawsuit_id;
    const controller = new AbortController();
    setRecentTasksLoading(true);
    setRecentTasks(null);
    apiFetch(`${API}/groups/${lawsuitId}/recent-tasks?limit=5`, {
      signal: controller.signal,
    })
      .then(async (res) => {
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        const data = await res.json();
        setRecentTasks(data);
      })
      .catch((err) => {
        if (err.name === "AbortError") return;
        // Falha na busca — UI mostra mensagem "não foi possível carregar"
        // com link pro botão de Compromissos do L1. Não toast porque o
        // detalhe da publicação serve mesmo sem isso.
        setRecentTasks({
          pending: [],
          recent_completed: [],
          pending_count: 0,
          recent_completed_count: 0,
          truncated: false,
          check_failed: true,
        });
      })
      .finally(() => setRecentTasksLoading(false));
    return () => controller.abort();
  }, [detailOpen, selectedRecord?.id, selectedRecord?.linked_lawsuit_id]);

  // ─── Polling de progresso de busca ativa ─────────────────────────────
  const activeSearch = searches.find((s) => s.status === "EXECUTANDO");
  useEffect(() => {
    if (!activeSearch) return;
    const t = setInterval(async () => {
      await loadSearches();
    }, 2000);
    return () => {
      clearInterval(t);
      // Quando polling para (busca terminou), recarrega dados
      loadStats();
      loadGrouped(groupPage, filterStatus, filterOffice, filterDateFrom, filterDateTo, filterCategory, filterUf, filterVinculo, filterNatureza, filterPolo, filterCnj, filterScheduledBy);
    };
  }, [activeSearch?.id]);

  // ─── Actions ─────────────────────────────────────────────────────────

  const handleSearch = async () => {
    if (!dateFrom) { setError("Data inicial é obrigatória."); return; }
    setIsSearching(true);
    setError(null);
    try {
      const payload = {
        date_from: new Date(dateFrom).toISOString(),
        date_to: dateTo ? new Date(dateTo).toISOString() : null,
        origin_type: originType,
        // Backend aceita CSV ("23,45,67") ou int single. Passamos a
        // string direto pra suportar multi-office.
        responsible_office_id: searchOfficeId || null,
        auto_classify: false,
        only_unlinked: searchOnlyUnlinked,
      };
      const res = await apiFetch(`${API}/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "Erro ao iniciar busca");
      }
      toast({ title: "Busca iniciada", description: "Acompanhe o progresso no histórico." });
      [3000, 8000, 15000, 30000].forEach((delay) => {
        setTimeout(() => { loadSearches(); loadStats(); loadGrouped(0, filterStatus, filterOffice, filterDateFrom, filterDateTo, filterCategory, filterUf, filterVinculo, filterNatureza, filterPolo, filterCnj, filterScheduledBy); }, delay);
      });
    } catch (err: any) {
      setError(err.message);
    } finally {
      setIsSearching(false);
    }
  };

  const handleFilterChange = (
    status: string, officeId: string, dateFrom?: string, dateTo?: string, category?: string, ufParam?: string, vinculoParam?: string, naturezaParam?: string, poloParam?: string, cnjParam?: string, scheduledByParam?: string,
  ) => {
    setFilterStatus(status);
    setFilterOffice(officeId);
    if (dateFrom !== undefined) setFilterDateFrom(dateFrom);
    if (dateTo !== undefined) setFilterDateTo(dateTo);
    if (category !== undefined) setFilterCategory(category);
    if (ufParam !== undefined) setFilterUf(ufParam);
    if (vinculoParam !== undefined) setFilterVinculo(vinculoParam);
    if (naturezaParam !== undefined) setFilterNatureza(naturezaParam);
    if (poloParam !== undefined) setFilterPolo(poloParam);
    if (cnjParam !== undefined) setFilterCnj(cnjParam);
    if (scheduledByParam !== undefined) setFilterScheduledBy(scheduledByParam);
    const df = dateFrom ?? filterDateFrom;
    const dt = dateTo ?? filterDateTo;
    const cat = category ?? filterCategory;
    const uf = ufParam ?? filterUf;
    const vin = vinculoParam ?? filterVinculo;
    const nat = naturezaParam ?? filterNatureza;
    const pol = poloParam ?? filterPolo;
    const cnj = cnjParam ?? filterCnj;
    const sb = scheduledByParam ?? filterScheduledBy;
    setGroupPage(0);
    setSelectedGroupKeys(new Set());
    // Debounce: a troca de filtro so dispara o fetch apos 300ms de quietude,
    // entao marcar varios chips em sequencia vira UM fetch (e a guarda de
    // sequencia em loadGrouped descarta qualquer corrida que reste).
    if (filterFetchTimerRef.current) clearTimeout(filterFetchTimerRef.current);
    filterFetchTimerRef.current = setTimeout(() => {
      loadGrouped(0, status, officeId, df, dt, cat, uf, vin, nat, pol, cnj, sb);
    }, 300);
  };

  const handleGroupPageChange = (newPage: number) => {
    setGroupPage(newPage);
    setSelectedGroupKeys(new Set());
    loadGrouped(newPage, filterStatus, filterOffice, filterDateFrom, filterDateTo, filterCategory, filterUf, filterVinculo, filterNatureza, filterPolo, filterCnj, filterScheduledBy);
  };

  const handleIgnoreRecord = async (recordId: number) => {
    try {
      await apiFetch(`${API}/records/${recordId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "IGNORADO" }),
      });
      toast({ title: "Registro ignorado" });
      loadGrouped(groupPage, filterStatus, filterOffice, filterDateFrom, filterDateTo, filterCategory, filterUf, filterVinculo, filterNatureza, filterPolo, filterCnj, filterScheduledBy);
      loadStats();
    } catch { /* ignore */ }
  };

  const handleReclassifyGroup = async (
    groupKey: string,
    recordIds: number[],
    value: string,
  ) => {
    // `value` formato: "Categoria" ou "Categoria|||Subcategoria"
    const [category, subcategory] = value.split("|||");
    if (!category) return;
    setReclassifyingGroup(groupKey);
    try {
      const res = await apiFetch(`${API}/records/reclassify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          record_ids: recordIds,
          category,
          subcategory: subcategory || null,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Falha ao reclassificar.");
      }
      toast({
        title: "Reclassificado",
        description: `${category}${subcategory ? " → " + subcategory : ""} aplicado a ${recordIds.length} publicação(ões). Proposta de tarefa atualizada.`,
      });
      loadGrouped(groupPage, filterStatus, filterOffice, filterDateFrom, filterDateTo, filterCategory, filterUf, filterVinculo, filterNatureza, filterPolo, filterCnj, filterScheduledBy);
      loadStats();
    } catch (err: any) {
      toast({ title: "Erro", description: err.message, variant: "destructive" });
    } finally {
      setReclassifyingGroup(null);
    }
  };

  // ─── Feedback explícito (thumbs-down) ─────────────────────────────────
  const openFeedback = (rec: PublicationRecord) => {
    setFeedbackRecord(rec);
    setFeedbackErrorType("category");
    setFeedbackCategory(rec.category || "");
    setFeedbackSubcategory(rec.subcategory || "");
    setFeedbackPolo(rec.polo || "");
    setFeedbackNatureza(rec.natureza_processo || "");
    setFeedbackNote("");
    setFeedbackOpen(true);
  };

  const handleSubmitFeedback = async () => {
    if (!feedbackRecord) return;
    if (!feedbackCategory) {
      toast({ title: "Erro", description: "Selecione a categoria correta.", variant: "destructive" });
      return;
    }
    setSubmittingFeedback(true);
    try {
      const res = await apiFetch(`${API}/records/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          record_id: feedbackRecord.id,
          error_type: feedbackErrorType,
          corrected_category: feedbackCategory,
          corrected_subcategory: feedbackSubcategory || null,
          corrected_polo: feedbackPolo || null,
          corrected_natureza: feedbackNatureza || null,
          user_note: feedbackNote || null,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Erro ao enviar feedback.");
      }
      toast({ title: "Feedback registrado", description: "Obrigado! O classificador vai aprender com essa correção." });
      setFeedbackOpen(false);
      loadGrouped(groupPage, filterStatus, filterOffice, filterDateFrom, filterDateTo, filterCategory, filterUf, filterVinculo, filterNatureza, filterPolo, filterCnj, filterScheduledBy);
    } catch (err: any) {
      toast({ title: "Erro", description: err.message, variant: "destructive" });
    } finally {
      setSubmittingFeedback(false);
    }
  };

  const handleRefreshAll = () => {
    loadStats(); loadSearches(); loadGrouped(groupPage, filterStatus, filterOffice, filterDateFrom, filterDateTo, filterCategory, filterUf, filterVinculo, filterNatureza, filterPolo, filterCnj, filterScheduledBy); loadBatches();
    if (insightsOpen) loadInsights(insightPeriod);
  };

  // ─── Batch classification ────────────────────────────────────────────

  const handleSubmitBatch = async () => {
    setSubmittingBatch(true);
    try {
      const payload: Record<string, unknown> = {};
      // Backend espera string (Optional[str]) desde a mudanca do
      // linked_office_id pra aceitar CSV ("61,62,63"). Nao usar parseInt
      // aqui — Pydantic V2 rejeita com 422.
      if (batchOfficeId) payload.linked_office_id = String(batchOfficeId);
      if (batchLimit) {
        const parsed = parseInt(batchLimit);
        if (!Number.isNaN(parsed) && parsed > 0) payload.limit = parsed;
      }
      if (batchOnlyUnlinked) payload.only_unlinked = true;
      const res = await apiFetch(`${API}/classify-batch/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Erro ao enviar lote");
      }
      const created = await res.json();
      toast({
        title: "Lote enviado à Anthropic",
        description: `Batch #${created.id} com ${created.total_records} publicações. Aguarde processamento.`,
      });
      loadBatches();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      toast({ title: "Erro ao enviar lote", description: msg, variant: "destructive" });
    } finally {
      setSubmittingBatch(false);
    }
  };

  const handleRefreshBatch = async (batchId: number) => {
    setRefreshingBatchId(batchId);
    try {
      const res = await apiFetch(`${API}/classify-batch/${batchId}/refresh`, { method: "POST" });
      if (!res.ok) throw new Error("Falha ao atualizar status");
      await loadBatches();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      toast({ title: "Erro", description: msg, variant: "destructive" });
    } finally {
      setRefreshingBatchId(null);
    }
  };

  const handleApplyBatch = async (batchId: number) => {
    setApplyingBatchId(batchId);
    try {
      const res = await apiFetch(`${API}/classify-batch/${batchId}/apply`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Falha ao aplicar resultados");
      }
      toast({
        title: "Aplicando resultados",
        description: "Os registros serão classificados em background.",
      });
      // Pequeno polling para refletir o efeito na UI
      [3000, 8000, 20000].forEach((delay) => {
        setTimeout(() => { loadBatches(); loadStats(); loadGrouped(groupPage, filterStatus, filterOffice, filterDateFrom, filterDateTo, filterCategory, filterUf, filterVinculo, filterNatureza, filterPolo, filterCnj, filterScheduledBy); }, delay);
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      toast({ title: "Erro", description: msg, variant: "destructive" });
    } finally {
      setApplyingBatchId(null);
    }
  };

  const handleRetryBatchErrors = async (batchId: number) => {
    setRetryingBatchId(batchId);
    try {
      const res = await apiFetch(`${API}/classify-batch/${batchId}/retry-errors`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Falha ao reprocessar erros");
      }
      const result = await res.json();
      toast({
        title: "Reprocessamento iniciado",
        description: `Novo batch #${result.new_batch?.id} criado com ${result.new_batch?.total_records || 0} registros.`,
      });
      loadBatches();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      toast({ title: "Erro", description: msg, variant: "destructive" });
    } finally {
      setRetryingBatchId(null);
    }
  };

  // Reclassifica TODOS os records do lote (incluindo os que ja deram
  // sucesso). Usa quando o prompt foi atualizado e operador quer
  // reaplicar a classificacao com a logica nova. Custo proporcional
  // ao tamanho do lote — exige confirmacao explicita.
  const handleReclassifyFullBatch = async (batchId: number, totalRecords: number) => {
    const confirmed = window.confirm(
      `Reclassificar TODOS os ${totalRecords} registros do lote #${batchId}?\n\n` +
      `Isso vai criar um novo batch e gastar ~${totalRecords} chamadas Anthropic. ` +
      `O lote original fica preservado como histórico. Continuar?`
    );
    if (!confirmed) return;
    setRetryingBatchId(batchId);
    try {
      const res = await apiFetch(`${API}/classify-batch/${batchId}/reclassify-all`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Falha ao reclassificar lote");
      }
      const result = await res.json();
      toast({
        title: "Reclassificação iniciada",
        description: `Novo batch #${result.new_batch?.id} criado com ${result.total_records} registros.`,
      });
      loadBatches();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      toast({ title: "Erro", description: msg, variant: "destructive" });
    } finally {
      setRetryingBatchId(null);
    }
  };

  // ─── Rebuild proposals ───────────────────────────────────────────────
  const [rebuildingProposals, setRebuildingProposals] = useState(false);

  const handleRebuildProposals = async () => {
    setRebuildingProposals(true);
    try {
      // Manda TODOS os filtros atuais pro backend — assim o rebuild fica
      // escopado ao que o operador está vendo na tela (util pra aplicar
      // um template novo só nas publicações de um escritório específico
      // sem retocar os 50k outros registros).
      const params = new URLSearchParams();
      if (filterOffice) params.set("linked_office_id", filterOffice);
      if (filterCategory) params.set("category", filterCategory);
      if (filterUf) params.set("uf", filterUf);
      if (filterPolo) params.set("polo", filterPolo);
      if (filterNatureza) params.set("natureza", filterNatureza);
      if (filterVinculo) params.set("vinculo", filterVinculo);
      if (filterDateFrom) params.set("date_from", filterDateFrom);
      if (filterDateTo) params.set("date_to", filterDateTo);
      const qs = params.toString() ? `?${params.toString()}` : "";
      const res = await apiFetch(`${API}/rebuild-proposals${qs}`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Falha ao reconstruir propostas");
      }
      const activeFiltersCount = [
        filterOffice, filterCategory, filterUf, filterPolo,
        filterNatureza, filterVinculo, filterDateFrom, filterDateTo,
      ].filter(Boolean).length;
      const scopeLabel = activeFiltersCount > 0
        ? `${activeFiltersCount} filtro${activeFiltersCount > 1 ? "s" : ""} ativo${activeFiltersCount > 1 ? "s" : ""}`
        : "todas as publicações classificadas";
      toast({
        title: "Reconstrução iniciada",
        description: `Propostas sendo reconstruídas (escopo: ${scopeLabel}). Atualize em instantes.`,
      });
      setTimeout(() => {
        loadGrouped(groupPage, filterStatus, filterOffice, filterDateFrom, filterDateTo, filterCategory, filterUf, filterVinculo, filterNatureza, filterPolo, filterCnj, filterScheduledBy);
      }, 3000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      toast({ title: "Erro", description: msg, variant: "destructive" });
    } finally {
      setRebuildingProposals(false);
    }
  };

  // ─── Scheduling ──────────────────────────────────────────────────────

  // Helper: converte uma mensagem de erro multi-linha (backend retorna com \n
  // entre categorias tipo "Campos obrigatórios não enviados: X, Y") num nó
  // JSX com uma linha por item e marcador visual, pra deixar o toast legível
  // em vez de parágrafo cru.
  const renderScheduleErrorDescription = (msg: string) => {
    const lines = (msg || "")
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);
    if (lines.length === 0) return "Falha no agendamento.";
    if (lines.length === 1) return lines[0];
    return (
      <div className="space-y-1.5 pt-1">
        {lines.map((line, i) => {
          // Separa "Título: item1, item2" em heading + bullets quando
          // houver ":" na linha. Deixa visualmente mais fácil de ler.
          const colonIdx = line.indexOf(":");
          if (colonIdx > 0 && colonIdx < line.length - 1) {
            const heading = line.slice(0, colonIdx).trim();
            const items = line
              .slice(colonIdx + 1)
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean);
            return (
              <div key={i}>
                <div className="font-semibold">{heading}</div>
                <ul className="ml-4 list-disc">
                  {items.map((item, j) => (
                    <li key={j}>{item}</li>
                  ))}
                </ul>
              </div>
            );
          }
          return <div key={i}>{line}</div>;
        })}
      </div>
    );
  };

  const openScheduleDialog = async (group: GroupedRecord) => {
    setScheduleGroup(group);
    const tasks = group.proposed_tasks?.length > 0 ? group.proposed_tasks : (group.proposed_task ? [group.proposed_task] : []);

    // Snapshot do LIDER original do template (do _render_proposal). Mantido
    // SEMPRE como o lider, mesmo apos a troca pro assistente abaixo. O
    // submit usa essa snapshot pra detectar override manual: se o operador
    // editar o user no UserSelector apos abrir o modal, current != original
    // e a logica de /claim e' pulada. Tambem usado pelo backend (compara
    // current vs original do template).
    const snap = new Map<number, number | null>();
    tasks.forEach((t, idx) => {
      const id = (t.participants || []).find(
        (p: any) => p?.isResponsible && p?.contact?.id,
      )?.contact?.id ?? null;
      snap.set(idx, id);
    });
    setOriginalResponsibleByIndex(snap);
    setTouchedResponsible(new Set());

    // ── Pre-resolve assistente da squad ANTES de mostrar o modal ──
    // Quando o template marca target_role='assistente' (ou aponta pra squad
    // de suporte via target_squad_id), o UserSelector tem que mostrar pro
    // operador o ASSISTENTE que vai receber a tarefa, nao o lider do
    // template. Operador e' a ponta da operacao — ele tem que ver
    // exatamente quem vai pegar.
    //
    // Chamamos /squads/resolve-target/claim (commit=true) pra avancar o
    // round-robin uma unica vez no momento de abrir o modal. Substituimos
    // participants[0].contact.id pelo assistente resolvido. Se o operador
    // cancelar o modal, o round-robin fica avancado mesmo (Mel/Julia
    // alternam mesmo se nada for criado no L1) — custo aceitavel pra
    // garantir UX correta.
    //
    // Falha individual (squad sem assistente, rede off, etc.) mantem o
    // lider original e exibe um toast — operador escolhe se troca manual
    // ou se cancela e arruma a config da squad.
    const resolvedTasks = await Promise.all(tasks.map(async (t) => {
      const tr = (t as any).target_role || "principal";
      const tsq = (t as any).target_squad_id || null;
      if (tr !== "assistente" && !tsq) return t;

      const responsibleId = (t.participants || []).find(
        (p: any) => p?.isResponsible && p?.contact?.id,
      )?.contact?.id;
      if (!responsibleId) return t;

      const officeId =
        (t as any).responsibleOfficeId || (t as any).originOfficeId || null;
      try {
        const res = await apiFetch("/api/v1/squads/resolve-target/claim", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            target_role: tr,
            target_squad_id: tsq,
            responsible_user_external_id: responsibleId,
            office_external_id: officeId,
            task_subtype_external_id: t.subTypeId || null,
          }),
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          toast({
            title: "Falha ao resolver assistente da squad",
            description:
              body.detail ||
              `HTTP ${res.status} — exibindo o líder do template; troque manualmente se necessário.`,
            variant: "destructive",
          });
          return t;
        }
        const data = (await res.json()) as {
          user_external_id: number;
          fallback_reason: string | null;
        };
        if (data.fallback_reason === "user_not_in_any_squad") {
          toast({
            title: "Líder fora de squad principal",
            description:
              "Tarefa fica com o líder mesmo. Cadastre a squad em /admin/squads ou troque o responsável manualmente.",
            variant: "destructive",
          });
        }
        return {
          ...t,
          participants: [
            {
              contact: { id: data.user_external_id },
              isResponsible: true,
              isExecuter: true,
              isRequester: true,
            },
          ],
        };
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        toast({
          title: "Erro de rede ao resolver assistente",
          description: msg + " — exibindo líder do template.",
          variant: "destructive",
        });
        return t;
      }
    }));

    // ── Recalcula a data dos templates com referencia "data atual" ──────
    // A data da proposta e' congelada quando o template e' aplicado; pra
    // publicacao antiga com template "data atual" ela fica velha. Aqui, na
    // ABERTURA do modal, pedimos ao backend a data refeita (hoje + N dias
    // uteis) e aplicamos UMA UNICA vez (respeita edicao manual posterior do
    // operador). Falha graceful: mantem a data da proposta.
    let dateAdjustedTasks = resolvedTasks;
    if (group.lawsuit_id) {
      try {
        const dres = await apiFetch(
          `${API}/groups/${group.lawsuit_id}/refresh-today-dates`,
          { method: "POST" },
        );
        if (dres.ok) {
          const dmap =
            (await dres.json())?.refreshed_dates_by_subtype || {};
          dateAdjustedTasks = resolvedTasks.map((t) => {
            const r = dmap[String(t.subTypeId)] ?? dmap[t.subTypeId];
            return r?.startDateTime
              ? { ...t, startDateTime: r.startDateTime, endDateTime: r.endDateTime }
              : t;
          });
        }
      } catch {
        /* mantem a data da proposta */
      }
    }
    setEditedPayloads(dateAdjustedTasks.map((t) => ({ ...t })));
    // Reset do estado de duplicatas — a checagem roda via useEffect abaixo.
    setDuplicatesBySubtype({});
    setDuplicateCheckFailed(false);
    // CRITICO: resetar removedTaskIndices ao abrir. Antes ele so era zerado
    // apos envio bem-sucedido, entao 'Remover tarefa' do processo A vazava
    // como indices ja marcados como removidos no processo B.
    setRemovedTaskIndices(new Set());
    setScheduleOpen(true);
  };

  // Check-duplicates: quando modal abre (ou subtipos mudam), consulta o
  // backend e carrega a lista de tasks já pendentes no L1. Debounce leve
  // pra quando o usuário muda um subtipo várias vezes em poucos segundos.
  useEffect(() => {
    if (!scheduleOpen || !scheduleGroup?.lawsuit_id) {
      // Avulsas sem processo: backend não suporta check eficiente sem
      // lawsuit_id, então nem tenta — limpa estado.
      setDuplicatesBySubtype({});
      setDuplicateCheckFailed(false);
      return;
    }
    const activeTasks = editedPayloads.filter((_, i) => !removedTaskIndices.has(i));
    const subtypeIds = Array.from(new Set(
      activeTasks.map((t) => t.subTypeId).filter((x): x is number => !!x)
    ));
    if (subtypeIds.length === 0) {
      setDuplicatesBySubtype({});
      setDuplicateCheckFailed(false);
      return;
    }
    const lawsuitId = scheduleGroup.lawsuit_id;
    setDuplicateCheckLoading(true);
    const controller = new AbortController();
    const handle = setTimeout(async () => {
      try {
        const res = await apiFetch(
          `${API}/groups/${lawsuitId}/check-duplicates`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ subtype_ids: subtypeIds }),
            signal: controller.signal,
          }
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (data.check_failed) {
          setDuplicateCheckFailed(true);
          setDuplicatesBySubtype({});
        } else {
          setDuplicateCheckFailed(false);
          // Backend retorna com keys string (JSON). Normaliza pra number.
          const raw = data.duplicates_by_subtype || {};
          const normalized: Record<number, any[]> = {};
          for (const k of Object.keys(raw)) {
            normalized[Number(k)] = raw[k];
          }
          setDuplicatesBySubtype(normalized);
        }
      } catch (err: any) {
        if (err?.name === "AbortError") return;
        setDuplicateCheckFailed(true);
        setDuplicatesBySubtype({});
      } finally {
        setDuplicateCheckLoading(false);
      }
    }, 300);
    return () => {
      clearTimeout(handle);
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scheduleOpen, scheduleGroup?.lawsuit_id, editedPayloads.map((p) => p.subTypeId).join(","), Array.from(removedTaskIndices).join(",")]);

  // Adiciona uma tarefa avulsa (manual) no modal de agendamento.
  // Começa em branco — usuário preenche subtipo, responsável, data etc.
  const handleAddCustomTask = () => {
    const in7Days = new Date();
    in7Days.setDate(in7Days.getDate() + 7);
    // Usa data BRT (nao UTC) pra que "daqui a 7 dias" reflita 7 dias do
    // calendario do usuario, e horario 23:59 local BRT = 02:59:00Z do dia
    // seguinte em UTC.
    const dateStr = brtDateFromIso(in7Days.toISOString());
    const defaultIso = brtToUtcIso(dateStr, "23:59:00");

    const newTask: Partial<ProposedTask> = {
      description: "",
      priority: "Normal",
      startDateTime: defaultIso,
      endDateTime: defaultIso,
      typeId: undefined,
      subTypeId: undefined,
      responsibleOfficeId: null,
      participants: [],
      notes: null,
      is_custom: true,
    };
    setEditedPayloads((prev) => [...prev, newTask]);
  };

  const handleConfirmSchedule = async () => {
    if (!scheduleGroup) return;

    const activeTasks = editedPayloads.filter((_, i) => !removedTaskIndices.has(i));

    // Validação: bloqueia envio se alguma tarefa ativa (especialmente avulsas) estiver incompleta
    for (let i = 0; i < activeTasks.length; i++) {
      const t = activeTasks[i];
      const label = t.is_custom ? `Tarefa avulsa ${i + 1}` : `Tarefa ${i + 1}`;
      if (!t.description || !t.description.trim()) {
        toast({ title: `${label}: preencha a descrição`, variant: "destructive" });
        return;
      }
      if (!t.subTypeId) {
        toast({ title: `${label}: selecione um subtipo de tarefa`, variant: "destructive" });
        return;
      }
      const hasResponsible = (t.participants || []).some(
        (p: any) => p?.isResponsible && p?.contact?.id
      );
      if (!hasResponsible) {
        toast({ title: `${label}: selecione um responsável`, variant: "destructive" });
        return;
      }
      if (!t.endDateTime) {
        toast({ title: `${label}: defina o prazo/data`, variant: "destructive" });
        return;
      }
    }

    // Resolve quem recebe a tarefa pra cada task com target_role/target_squad
    // configurados no template. Backend de Publicações nao tem builder pra
    // resolver — frontend chama /api/v1/squads/resolve-target/claim e troca
    // o participant.contact.id antes de mandar o payload.
    //
    // 4 cenarios cobertos pelo endpoint:
    //   1. target_role=principal, target_squad=null → respo padrao (skip)
    //   2. target_role=assistente, target_squad=null → assist da squad principal
    //   3. target_role=principal, target_squad=X → leader da squad de suporte
    //   4. target_role=assistente, target_squad=X → assist da support (round-robin)
    //
    // Override manual: se o operador trocou o responsavel no modal (current
    // != original), a escolha dele vence. Operador na ponta da operacao.
    const resolvedTasks: any[] = [];
    for (let i = 0; i < activeTasks.length; i++) {
      const t = activeTasks[i];
      const targetRole = (t as any).target_role || "principal";
      const targetSquadId = (t as any).target_squad_id || null;
      // Cenario 1: nao precisa resolver
      if (targetRole === "principal" && !targetSquadId) {
        resolvedTasks.push(t);
        continue;
      }
      const responsibleId = (t.participants || []).find(
        (p: any) => p?.isResponsible && p?.contact?.id,
      )?.contact?.id;
      // Encontra o indice REAL no editedPayloads (activeTasks pula removed).
      const realIdx = editedPayloads.findIndex((p) => p === t);
      const originalId = originalResponsibleByIndex.get(realIdx) ?? null;
      if (touchedResponsible.has(realIdx)) {
        // Operador mexeu no responsável (mesmo re-escolhendo a mesma
        // pessoa) — decisão final dele. Marcador avisa o backend pra
        // também não re-rotear (regra de negócio: operador sempre vence).
        resolvedTasks.push({ ...t, _responsible_overridden: true });
        continue;
      }
      if (responsibleId && originalId !== null && originalId !== responsibleId) {
        // Operador overrideu — pula regra. Operador na ponta vence.
        resolvedTasks.push({ ...t, _responsible_overridden: true });
        continue;
      }
      try {
        const officeId = (t as any).responsibleOfficeId || (t as any).originOfficeId;
        const body: any = {
          target_role: targetRole,
          target_squad_id: targetSquadId,
          responsible_user_external_id: responsibleId || null,
          office_external_id: officeId || null,
          task_subtype_external_id: t.subTypeId || null,
        };
        const res = await apiFetch("/api/v1/squads/resolve-target/claim", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          toast({
            title: "Falha ao resolver assistente",
            description: body.detail || `HTTP ${res.status} — usando responsável original`,
            variant: "destructive",
          });
          resolvedTasks.push(t);
          continue;
        }
        const data = await res.json() as { user_external_id: number; squad_name: string | null; fallback_reason: string | null };
        // Substitui participant pelo assistente resolvido.
        const newTask = {
          ...t,
          participants: [{
            contact: { id: data.user_external_id },
            isResponsible: true,
            isExecuter: true,
            isRequester: true,
          }],
        };
        resolvedTasks.push(newTask);
      } catch (err) {
        toast({
          title: "Erro de rede ao resolver assistente",
          description: err instanceof Error ? err.message : "Usando responsável original",
          variant: "destructive",
        });
        resolvedTasks.push(t);
      }
    }

    // Remove campos de metadata (frontend-only) antes de enviar ao backend
    const sanitizedTasks = resolvedTasks.map((t) => {
      const { is_custom: _ic, template_name: _tn, suggested_responsible: _sr, target_role: _tr, template_id: _tid, ...rest } = t as any;
      return rest;
    });

    // Detecta duplicata e pede confirmação antes de enviar. Só bloqueia
    // se o check-duplicates retornou algo pro subtipo de pelo menos uma
    // tarefa ativa (subtipos fora da lista não aparecem no map).
    const hasDup = activeTasks.some((t) => {
      const sid = t.subTypeId;
      return sid && (duplicatesBySubtype[sid]?.length ?? 0) > 0;
    });
    let forceDuplicate = false;
    if (hasDup) {
      const affected = activeTasks
        .filter((t) => t.subTypeId && (duplicatesBySubtype[t.subTypeId!]?.length ?? 0) > 0)
        .map((t) => {
          const dups = duplicatesBySubtype[t.subTypeId!] || [];
          return `• ${t.description?.slice(0, 40) || "(sem descrição)"} — ${dups.length} tarefa(s) já pendente(s)`;
        })
        .join("\n");
      const ok = window.confirm(
        `Já existe(m) tarefa(s) pendente(s) no Legal One para:\n\n${affected}\n\n` +
        `Deseja agendar mesmo assim? (Clique em Cancelar para rever no painel do L1 e remover duplicatas.)`
      );
      if (!ok) return;
      forceDuplicate = true;
    }

    setScheduling(true);
    const isNoProcess = !scheduleGroup.lawsuit_id;
    try {
      const results: string[] = [];

      if (isNoProcess) {
        // Publicações sem processo: agendamento único com N payloads
        const recordIds = scheduleGroup.records.map((r) => r.id);
        const body: any = {
          record_ids: recordIds,
          payload_overrides: sanitizedTasks,
          force_duplicate: forceDuplicate,
        };
        const res = await apiFetch(`${API}/groups/records/schedule`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          throw new Error(data.detail || "Erro ao agendar tarefa.");
        }
        const result = await res.json();
        const ids = result.created_task_ids ?? [result.created_task_id ?? result.task_id ?? "?"];
        results.push(...ids.map(String));
      } else {
        // Publicações com processo vinculado: agendamento único com N payloads
        const body: any = {
          payload_overrides: sanitizedTasks,
          force_duplicate: forceDuplicate,
        };
        const res = await apiFetch(`${API}/groups/${scheduleGroup.lawsuit_id}/schedule`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          const detail = data.detail || "Erro ao agendar tarefa.";
          // Backend pode retornar 409 com detail "DUPLICATE_BLOCKED:..." caso
          // o check local não tenha sido feito (race condition) — traduz aqui.
          if (typeof detail === "string" && detail.startsWith("DUPLICATE_BLOCKED:")) {
            throw new Error("Já existe tarefa pendente similar no L1. Recarregue o modal para ver os detalhes.");
          }
          throw new Error(detail);
        }
        const result = await res.json();
        const ids = result.created_task_ids ?? [result.task_id ?? result.created_task_id ?? "?"];
        results.push(...ids.map(String));
      }

      toast({
        title: `${results.length} tarefa(s) agendada(s)!`,
        description: `IDs: ${results.join(", ")}`,
      });
      setScheduleOpen(false);
      setRemovedTaskIndices(new Set());
      loadGrouped(groupPage, filterStatus, filterOffice, filterDateFrom, filterDateTo, filterCategory, filterUf, filterVinculo, filterNatureza, filterPolo, filterCnj, filterScheduledBy);
      loadStats();
    } catch (err: any) {
      // Duration maior pra erros porque o backend agora devolve detalhe
      // do L1 humanizado (campo faltando, validação, etc.) que o operador
      // precisa ler. renderScheduleErrorDescription transforma a mensagem
      // multi-linha numa lista visual com heading + bullets.
      toast({
        title: "Erro ao agendar tarefa",
        description: renderScheduleErrorDescription(err.message),
        variant: "destructive",
        duration: 15000,
      });
    } finally {
      setScheduling(false);
    }
  };

  // ─── Bulk actions (seleção múltipla de grupos) ───────────────────────

  // Chave estável do grupo (não depende do índice da iteração)
  const groupKey = (group: GroupedRecord): string =>
    `${group.lawsuit_id ?? "nl"}-${group.records[0]?.id ?? "x"}`;

  const toggleGroupSelection = (key: string) => {
    setSelectedGroupKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  const toggleSelectAllVisible = () => {
    if (!grouped) return;
    const visibleKeys = grouped.groups.map(groupKey);
    setSelectedGroupKeys((prev) => {
      const allSelected = visibleKeys.every((k) => prev.has(k));
      if (allSelected) {
        const next = new Set(prev);
        visibleKeys.forEach((k) => next.delete(k));
        return next;
      }
      return new Set([...prev, ...visibleKeys]);
    });
  };

  const clearSelection = () => {
    setSelectedGroupKeys(new Set());
    setBulkScopeAllGroups([]);
  };

  // Pré-carrega TODOS os grupos com os filtros atuais (cap em 500 do
  // backend) e marca todos como selecionados. Bulk handlers passam a
  // operar sobre `bulkScopeAllGroups` em vez de `grouped.groups`.
  const handleSelectAllFiltered = async () => {
    if (bulkScopeLoading) return;
    setBulkScopeLoading(true);
    try {
      let url = `${API}/records/grouped?limit=500&offset=0`;
      if (filterStatus) url += `&status=${filterStatus}`;
      if (filterOffice) url += `&linked_office_id=${filterOffice}`;
      if (filterDateFrom) url += `&date_from=${filterDateFrom}`;
      if (filterDateTo) url += `&date_to=${filterDateTo}`;
      if (filterCategory) url += `&category=${encodeURIComponent(filterCategory)}`;
      if (filterUf) url += `&uf=${encodeURIComponent(filterUf)}`;
      if (filterVinculo) url += `&vinculo=${filterVinculo}`;
      if (filterNatureza) url += `&natureza=${encodeURIComponent(filterNatureza)}`;
      if (filterPolo) url += `&polo=${encodeURIComponent(filterPolo)}`;
      if (filterCnj) url += `&cnj_search=${encodeURIComponent(filterCnj)}`;
      if (filterScheduledBy) url += `&scheduled_by_user_id=${encodeURIComponent(filterScheduledBy)}`;
      const res = await apiFetch(url);
      if (!res.ok) {
        toast({ title: "Erro ao carregar todos", variant: "destructive" });
        return;
      }
      const data = await res.json();
      const allGroups: GroupedRecord[] = data.groups || [];
      const totalGroups: number = data.total_groups ?? allGroups.length;
      // Backend cap em 500 — avisa se atingiu o teto.
      if (totalGroups > allGroups.length) {
        toast({
          title: `Carregados ${allGroups.length} de ${totalGroups}`,
          description: "O backend retorna no máximo 500 por requisição. Refine os filtros pra cobrir o restante.",
          variant: "destructive",
          duration: 10000,
        });
      }
      setBulkScopeAllGroups(allGroups);
      setSelectedGroupKeys(new Set(allGroups.map(groupKey)));
      toast({
        title: `${allGroups.length} grupo(s) selecionado(s)`,
        description: "Use 'Confirmar agendamentos' ou 'Ignorar selecionados' para agir.",
      });
    } catch (e) {
      toast({
        title: "Falha ao selecionar todos",
        description: e instanceof Error ? e.message : String(e),
        variant: "destructive",
      });
    } finally {
      setBulkScopeLoading(false);
    }
  };

  const handleBulkConfirm = async () => {
    if (!grouped && bulkScopeAllGroups.length === 0) return;
    // Se "todos os filtrados" foi acionado, opera sobre o scope completo;
    // senão, sobre o que está visível na página atual.
    const sourceGroups = bulkScopeAllGroups.length > 0 ? bulkScopeAllGroups : (grouped?.groups || []);
    const selected = sourceGroups.filter((g) => selectedGroupKeys.has(groupKey(g)));
    const schedulable = selected.filter((g) => {
      const tasks = g.proposed_tasks || (g.proposed_task ? [g.proposed_task] : []);
      const status = groupStatusSummary(g);
      return tasks.length > 0 && status !== "AGENDADO";
    });

    if (schedulable.length === 0) {
      toast({
        title: "Nada a confirmar",
        description: "Nenhum grupo selecionado possui proposta pendente.",
        variant: "destructive",
      });
      return;
    }

    setBulkProcessing(true);
    let ok = 0;
    const errors: string[] = [];

    for (const group of schedulable) {
      const tasks = group.proposed_tasks && group.proposed_tasks.length > 0
        ? group.proposed_tasks
        : (group.proposed_task ? [group.proposed_task] : []);
      const payloadOverrides = tasks.map((t) => ({ ...t }));

      try {
        if (!group.lawsuit_id) {
          const recordIds = group.records.map((r) => r.id);
          const res = await apiFetch(`${API}/groups/records/schedule`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ record_ids: recordIds, payload_overrides: payloadOverrides }),
          });
          if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data.detail || "Erro ao agendar.");
          }
        } else {
          const res = await apiFetch(`${API}/groups/${group.lawsuit_id}/schedule`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ payload_overrides: payloadOverrides }),
          });
          if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data.detail || "Erro ao agendar.");
          }
        }
        ok += 1;
      } catch (err: any) {
        errors.push(`${group.lawsuit_cnj ?? group.lawsuit_id ?? "sem processo"}: ${err.message}`);
      }
    }

    toast({
      title: `${ok} grupo(s) agendado(s)`,
      description: errors.length > 0 ? `Falhas: ${errors.slice(0, 3).join(" | ")}${errors.length > 3 ? ` (+${errors.length - 3})` : ""}` : undefined,
      variant: errors.length > 0 ? "destructive" : "default",
      // Erros em bulk frequentemente trazem detalhes do L1 (campo faltando,
      // duplicata, etc.) — operador precisa de tempo pra ler cada linha.
      duration: errors.length > 0 ? 15000 : 5000,
    });

    clearSelection();
    setBulkProcessing(false);
    loadGrouped(groupPage, filterStatus, filterOffice, filterDateFrom, filterDateTo, filterCategory, filterUf, filterVinculo, filterNatureza, filterPolo, filterCnj, filterScheduledBy);
    loadStats();
  };

  const handleBulkIgnore = async () => {
    if (!grouped && bulkScopeAllGroups.length === 0) return;
    const sourceGroups = bulkScopeAllGroups.length > 0 ? bulkScopeAllGroups : (grouped?.groups || []);
    const selected = sourceGroups.filter((g) => selectedGroupKeys.has(groupKey(g)));
    const recordIds = selected.flatMap((g) =>
      g.records.filter((r) => r.status !== "AGENDADO" && r.status !== "IGNORADO").map((r) => r.id),
    );

    if (recordIds.length === 0) {
      toast({
        title: "Nada a ignorar",
        description: "As publicações selecionadas já estão agendadas ou ignoradas.",
        variant: "destructive",
      });
      return;
    }

    setBulkProcessing(true);
    const results = await Promise.allSettled(
      recordIds.map((id) =>
        apiFetch(`${API}/records/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: "IGNORADO" }),
        }),
      ),
    );
    const ok = results.filter((r) => r.status === "fulfilled").length;
    const failed = results.length - ok;

    toast({
      title: `${ok} publicação(ões) ignorada(s)`,
      description: failed > 0 ? `${failed} falharam.` : undefined,
      variant: failed > 0 ? "destructive" : "default",
    });

    clearSelection();
    setBulkProcessing(false);
    loadGrouped(groupPage, filterStatus, filterOffice, filterDateFrom, filterDateTo, filterCategory, filterUf, filterVinculo, filterNatureza, filterPolo, filterCnj, filterScheduledBy);
    loadStats();
  };

  // ─── Derived ─────────────────────────────────────────────────────────

  const totalPages = grouped ? Math.ceil(grouped.total_groups / groupPageSize) : 0;

  // UFs disponíveis globalmente — vêm do backend no campo
  // `available_ufs` da resposta /records/grouped, calculado sobre
  // TODA a base filtrada (ignora apenas o próprio filtro de UF pra não
  // sumir opções conforme o operador seleciona). Fallback pra derivar
  // da página atual caso o backend seja uma versão antiga (sem o campo).
  const availableUfs: string[] = (() => {
    const serverUfs = (grouped as unknown as { available_ufs?: string[] } | null)?.available_ufs;
    if (serverUfs && serverUfs.length > 0) {
      const set = new Set(serverUfs);
      if (filterUf) filterUf.split(",").filter(Boolean).forEach((u) => set.add(u));
      return Array.from(set).sort();
    }
    // Fallback: derivar da página atual (comportamento legado).
    if (grouped) {
      const set = new Set(
        grouped.groups
          .map((g) => ufFromCnj(g.lawsuit_cnj))
          .filter((u): u is string => !!u),
      );
      if (filterUf) filterUf.split(",").filter(Boolean).forEach((u) => set.add(u));
      return Array.from(set).sort();
    }
    return filterUf ? filterUf.split(",").filter(Boolean) : [];
  })();

  // Operadores ("Cadastrado por") que aparecem nos resultados da query
  // atual. Backend envia `available_scheduled_by` em /records/grouped —
  // ignora o próprio filtro pra não sumir opções ao selecionar uma.
  const availableScheduledBy: { user_id: number; name: string; email: string }[] = (() => {
    const raw = (grouped as unknown as {
      available_scheduled_by?: { user_id: number; name: string; email: string }[];
    } | null)?.available_scheduled_by;
    return Array.isArray(raw) ? raw : [];
  })();

  // O filtro UF agora é server-side, então os grupos já vêm filtrados.
  const visibleGroups: GroupedRecord[] = grouped ? grouped.groups : [];

  const groupStatusSummary = (group: GroupedRecord) => {
    const statuses = group.records.map((r) => r.status);
    if (statuses.every((s) => s === "AGENDADO")) return "AGENDADO";
    if (statuses.some((s) => s === "CLASSIFICADO")) return "CLASSIFICADO";
    if (statuses.some((s) => s === "ERRO")) return "ERRO";
    if (statuses.some((s) => s === "IGNORADO") && statuses.every((s) => s === "IGNORADO" || s === "AGENDADO")) return "IGNORADO";
    return "NOVO";
  };

  const officeName = (id: number | null) => {
    if (!id) return null;
    const o = offices.find((o) => o.external_id === id);
    return o ? (o.path || o.name) : String(id);
  };

  // ─── Render ──────────────────────────────────────────────────────────

  const operationalStats = stats?.operational;
  const insightSeries = (insights?.series || []).map((item) => ({
    ...item,
    label: formatInsightBucketLabel(item.bucket_start, insights?.bucket_kind || "day"),
  }));

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
            <Newspaper className="h-6 w-6" />
            Publicações Legal One
          </h1>
          <p className="text-muted-foreground">
            Busque, classifique e agende tarefas a partir de publicações judiciais.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => setInsightsOpen(true)}>
            <BarChartIcon className="mr-2 h-4 w-4" />
            Indicadores
          </Button>
          <Button variant="outline" size="sm" asChild>
            <Link to="/publications/templates">
              <Settings className="mr-2 h-4 w-4" />
              Configurar Templates
            </Link>
          </Button>
          <Button variant="outline" size="sm" onClick={handleRefreshAll}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Atualizar
          </Button>
        </div>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Erro</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Stats */}
      {stats && operationalStats && (
        <div className="space-y-3">
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
            <OperationalStatCard
              title="Pendentes"
              value={operationalStats.pendentes}
              hint="Aguardando classificação"
              tone="bg-blue-50 text-blue-700"
              icon={Clock}
            />
            <OperationalStatCard
              title="Aguardando confirmação"
              value={operationalStats.aguardando_confirmacao}
              hint="Já classificadas"
              tone="bg-amber-50 text-amber-700"
              icon={CheckCircle2}
            />
            <OperationalStatCard
              title="Agendadas"
              value={operationalStats.agendadas}
              hint="Prontas no Legal One"
              tone="bg-emerald-50 text-emerald-700"
              icon={Calendar}
            />
            <OperationalStatCard
              title="Sem providência"
              value={operationalStats.sem_providencia}
              hint="Ignoradas, descartadas e obsoletas"
              tone="bg-slate-100 text-slate-700"
              icon={ThumbsDown}
            />
            <OperationalStatCard
              title="Erros"
              value={operationalStats.erros}
              hint="Itens que exigem revisão"
              tone="bg-rose-50 text-rose-700"
              icon={XCircle}
            />
            <div className="hidden">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Aguardando confirmação</CardTitle>
              <CheckCircle2 className="h-4 w-4 text-amber-500" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-amber-600">{operationalStats.aguardando_confirmacao}</div>
              <p className="text-xs text-muted-foreground">Aguardando classificação</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Sem providência</CardTitle>
              <Calendar className="h-4 w-4 text-green-500" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">{operationalStats.agendadas}</div>
              <p className="text-xs text-muted-foreground">Aguardando confirmação</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Sem providência</CardTitle>
              <ThumbsDown className="h-4 w-4 text-slate-500" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-slate-700">{operationalStats.sem_providencia}</div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Erros</CardTitle>
              <XCircle className="h-4 w-4 text-red-500" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-red-600">
                {operationalStats.erros}
              </div>
            </CardContent>
          </Card>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">Contexto rápido</span>
            <Badge variant="secondary">Total monitorado: {stats.total_records}</Badge>
            <Badge variant="secondary">Buscas: {stats.total_searches}</Badge>
            {stats.last_search?.created_at ? (
              <span>Última busca em {formatDate(stats.last_search.created_at)}</span>
            ) : (
              <span>Nenhuma busca recente registrada.</span>
            )}
          </div>
        </div>
      )}

      <Sheet open={insightsOpen} onOpenChange={setInsightsOpen}>
        <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-2xl">
          <SheetHeader>
            <SheetTitle className="flex items-center gap-2">
              <TrendingUp className="h-5 w-5" />
              Indicadores operacionais
            </SheetTitle>
            <SheetDescription>
              Recortes leves para leitura diária, sem tirar o foco da esteira de tratamento.
            </SheetDescription>
          </SheetHeader>

          <div className="mt-6 space-y-6">
            <div className="flex flex-wrap gap-2">
              {INSIGHT_PERIOD_OPTIONS.map((option) => (
                <Button
                  key={option.value}
                  size="sm"
                  variant={insightPeriod === option.value ? "default" : "outline"}
                  onClick={() => setInsightPeriod(option.value)}
                  disabled={insightsLoading && insightPeriod === option.value}
                >
                  {option.label}
                </Button>
              ))}
            </div>

            {insightsLoading && !insights ? (
              <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Carregando indicadores...
              </div>
            ) : null}

            {insights ? (
              <>
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  <OperationalStatCard
                    title={`Recebidas no ${insights.period_label.toLowerCase()}`}
                    value={insights.summary.recebidas}
                    hint={`${insights.summary.buscas} busca(s) no recorte`}
                    tone="bg-blue-50 text-blue-700"
                    icon={BarChartIcon}
                  />
                  <OperationalStatCard
                    title="Agendadas no recorte"
                    value={insights.summary.agendadas}
                    hint="Publicações que já saíram para tarefa"
                    tone="bg-emerald-50 text-emerald-700"
                    icon={Calendar}
                  />
                  <OperationalStatCard
                    title="Sem providência no recorte"
                    value={insights.summary.sem_providencia}
                    hint="Ignoradas, descartadas e obsoletas"
                    tone="bg-slate-100 text-slate-700"
                    icon={ThumbsDown}
                  />
                  <OperationalStatCard
                    title="Pendentes no recorte"
                    value={insights.summary.pendentes}
                    hint="Ainda aguardando classificação"
                    tone="bg-blue-50 text-blue-700"
                    icon={Clock}
                  />
                  <OperationalStatCard
                    title="Confirmação no recorte"
                    value={insights.summary.aguardando_confirmacao}
                    hint="Classificadas aguardando revisão"
                    tone="bg-amber-50 text-amber-700"
                    icon={CheckCircle2}
                  />
                  <OperationalStatCard
                    title="Erros no recorte"
                    value={insights.summary.erros}
                    hint="Itens que precisam de atenção"
                    tone="bg-rose-50 text-rose-700"
                    icon={XCircle}
                  />
                </div>

                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-base">
                      <TrendingUp className="h-4 w-4" />
                      Evolução do recorte
                    </CardTitle>
                    <CardDescription>
                      Recebidas vs saídas úteis no período {insights.period_label.toLowerCase()}.
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="h-[280px]">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={insightSeries}>
                          <CartesianGrid strokeDasharray="3 3" vertical={false} />
                          <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={16} />
                          <YAxis allowDecimals={false} tickLine={false} axisLine={false} />
                          <RTooltip />
                          <Legend />
                          <Bar dataKey="received" name="Recebidas" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                          <Bar dataKey="scheduled" name="Agendadas" fill="#10b981" radius={[4, 4, 0, 0]} />
                          <Bar dataKey="without_providence" name="Sem providência" fill="#64748b" radius={[4, 4, 0, 0]} />
                          <Bar dataKey="errors" name="Erros" fill="#ef4444" radius={[4, 4, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">Snapshot atual</CardTitle>
                    <CardDescription>
                      Estoque vivo do módulo para comparação rápida com o recorte.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="grid gap-3 sm:grid-cols-2">
                    <div className="rounded-lg border p-3">
                      <p className="text-xs text-muted-foreground">Total monitorado</p>
                      <p className="mt-1 text-2xl font-bold">{insights.current.total_monitorado}</p>
                    </div>
                    <div className="rounded-lg border p-3">
                      <p className="text-xs text-muted-foreground">Pendentes agora</p>
                      <p className="mt-1 text-2xl font-bold text-blue-600">{insights.current.pendentes}</p>
                    </div>
                    <div className="rounded-lg border p-3">
                      <p className="text-xs text-muted-foreground">Aguardando confirmação</p>
                      <p className="mt-1 text-2xl font-bold text-amber-600">{insights.current.aguardando_confirmacao}</p>
                    </div>
                    <div className="rounded-lg border p-3">
                      <p className="text-xs text-muted-foreground">Agendadas</p>
                      <p className="mt-1 text-2xl font-bold text-emerald-600">{insights.current.agendadas}</p>
                    </div>
                    <div className="rounded-lg border p-3">
                      <p className="text-xs text-muted-foreground">Sem providência</p>
                      <p className="mt-1 text-2xl font-bold text-slate-700">{insights.current.sem_providencia}</p>
                    </div>
                    <div className="rounded-lg border p-3">
                      <p className="text-xs text-muted-foreground">Erros</p>
                      <p className="mt-1 text-2xl font-bold text-rose-600">{insights.current.erros}</p>
                    </div>
                  </CardContent>
                </Card>

                <p className="text-xs text-muted-foreground">
                  Atualizado em {formatDate(insights.generated_at)}.
                </p>
              </>
            ) : null}
          </div>
        </SheetContent>
      </Sheet>

      {/* Search Panel */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Search className="h-5 w-5" />
            Disparar Nova Busca
          </CardTitle>
          <CardDescription>
            Busca publicações, enriquece com escritório responsável, classifica automaticamente e monta proposta de tarefa.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-end gap-4">
            <div className="grid gap-1.5">
              <Label htmlFor="dateFrom">Data Inicial *</Label>
              <Input id="dateFrom" type="date" value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)} className="w-[160px]" />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="dateTo">Data Final</Label>
              <Input id="dateTo" type="date" value={dateTo}
                onChange={(e) => setDateTo(e.target.value)} className="w-[160px]" />
            </div>
            <div className="grid gap-1.5">
              <Label>Origem</Label>
              <Select value={originType} onValueChange={setOriginType}>
                <SelectTrigger className="w-[200px]"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="OfficialJournalsCrawler">Diário Oficial</SelectItem>
                  <SelectItem value="ProgressesCrawler">Andamentos (Crawler)</SelectItem>
                  <SelectItem value="Manual">Manual</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label>Escritório(s) Responsável(eis)</Label>
              {/* Multi-select: aceita N escritórios na mesma busca.
                  searchOfficeId fica como CSV ("23,45,67") — o backend
                  já aceita esse formato. Se vazio, busca em todos. */}
              <MultiSelect
                options={offices.map((o) => ({
                  value: String(o.external_id),
                  label: officeLabel(o),
                }))}
                defaultValue={searchOfficeId ? searchOfficeId.split(",").filter(Boolean) : []}
                onValueChange={(vals) => setSearchOfficeId(vals.join(","))}
                placeholder="Todos os escritórios"
                className="w-[260px]"
                maxCount={2}
              />
            </div>
            <div className="flex items-center gap-2 self-end">
              <Checkbox
                id="searchOnlyUnlinked"
                checked={searchOnlyUnlinked}
                onCheckedChange={(v) => setSearchOnlyUnlinked(!!v)}
              />
              <Label htmlFor="searchOnlyUnlinked" className="text-xs cursor-pointer whitespace-nowrap">
                Apenas sem processo
              </Label>
            </div>
            <Button onClick={handleSearch} disabled={isSearching || !dateFrom} className="self-end">
              {isSearching ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Search className="mr-2 h-4 w-4" />}
              Buscar
            </Button>
          </div>
          {/* Painel de índice só faz sentido com 1 escritório. Pra
              múltiplos, o sync acontece automático no backend (ensure_sync
              roda por escritório no service durante a busca). */}
          {searchOfficeId && !searchOfficeId.includes(",") && indexStatus && (
            <div className="mt-3 rounded-md border bg-muted/30 p-3 text-xs flex flex-wrap items-center gap-3">
              <span className="font-medium">Índice de processos:</span>
              <span>{indexStatus.total_ids.toLocaleString()} processos</span>
              {indexStatus.is_fresh ? (
                <span className="rounded bg-green-100 text-green-800 px-2 py-0.5">atualizado</span>
              ) : (
                <span className="rounded bg-amber-100 text-amber-800 px-2 py-0.5">desatualizado</span>
              )}
              {indexStatus.last_full_sync_at && (
                <span className="text-muted-foreground">
                  último full: {new Date(indexStatus.last_full_sync_at).toLocaleString("pt-BR")}
                </span>
              )}
              {indexStatus.in_progress ? (
                <span className="flex items-center gap-2">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  sincronizando... {indexStatus.progress_pct}%
                </span>
              ) : (
                <>
                  <Button size="sm" variant="outline" className="h-6 text-xs"
                    onClick={() => handleSyncIndex(false)}>
                    Atualizar
                  </Button>
                  <Button size="sm" variant="ghost" className="h-6 text-xs"
                    onClick={() => handleSyncIndex(true)}>
                    Full sync
                  </Button>
                </>
              )}
              {indexStatus.last_sync_status === "error" && (
                <span className="text-red-600">erro no último sync</span>
              )}
            </div>
          )}
          {activeSearch && (
            <div className="mt-3 rounded-md border border-blue-200 bg-blue-50 p-3 space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin text-blue-600" />
                  <span className="text-sm font-medium text-blue-800">
                    Busca #{activeSearch.id} em andamento
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-blue-700">
                    {activeSearch.progress_pct ?? 0}%
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-6 px-2 text-xs text-red-600 hover:bg-red-50 hover:text-red-700"
                    onClick={() => cancelSearch(activeSearch.id)}
                    title="Cancelar busca em andamento"
                  >
                    <XCircle className="mr-1 h-3 w-3" />
                    Cancelar
                  </Button>
                </div>
              </div>
              <Progress value={activeSearch.progress_pct ?? 0} className="h-2" />
              <p className="text-xs text-blue-700">
                {activeSearch.progress_detail || "Iniciando..."}
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Search History (collapsible) */}
      {searches.length > 0 && (
        <Card>
          <CardHeader className="cursor-pointer select-none py-3"
            onClick={() => setSearchesExpanded(!searchesExpanded)}>
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-medium">
                Histórico de Buscas
                <Badge variant="secondary" className="ml-2">{searches.length}</Badge>
              </CardTitle>
              {searchesExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </div>
          </CardHeader>
          {searchesExpanded && (() => {
            // Slice client-side da pagina atual. Total = searches.length;
            // pageStart/pageEnd usados pra mostrar "X-Y de N".
            const total = searches.length;
            const pageStart = searchesPage * searchesPageSize;
            const pageEnd = Math.min(pageStart + searchesPageSize, total);
            const pageItems = searches.slice(pageStart, pageEnd);
            const totalPages = Math.max(1, Math.ceil(total / searchesPageSize));
            return (
            <CardContent className="pt-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>ID</TableHead>
                    <TableHead>Período</TableHead>
                    <TableHead>Escritório</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Encontradas</TableHead>
                    <TableHead>Novas</TableHead>
                    <TableHead>Data</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {pageItems.map((s) => (
                    <TableRow key={s.id}>
                      <TableCell className="font-mono text-xs">#{s.id}</TableCell>
                      <TableCell className="text-xs">
                        {formatDateShort(s.date_from)}{s.date_to ? ` → ${formatDateShort(s.date_to)}` : ""}
                      </TableCell>
                      <TableCell className="text-xs">
                        {s.office_filter ? (officeName(parseInt(s.office_filter)) || s.office_filter) : "—"}
                      </TableCell>
                      <TableCell>
                        <Badge variant={statusColor(s.status)} className="text-xs">{s.status}</Badge>
                      </TableCell>
                      <TableCell className="font-semibold">{s.total_found}</TableCell>
                      <TableCell className="text-green-600">{s.total_new}</TableCell>
                      <TableCell className="text-xs">{formatDate(s.created_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <div className="mt-3 flex flex-col items-center justify-between gap-2 sm:flex-row">
                <div className="text-xs text-muted-foreground">
                  {total === 0
                    ? "Sem registros"
                    : `Mostrando ${pageStart + 1}-${pageEnd} de ${total}`}
                </div>
                <div className="flex items-center gap-2">
                  <select
                    className="h-8 rounded-md border bg-background px-2 text-xs"
                    value={searchesPageSize}
                    onChange={(e) => {
                      setSearchesPageSize(Number(e.target.value) as 10 | 25 | 50);
                      setSearchesPage(0);
                    }}
                  >
                    <option value="10">10/pág.</option>
                    <option value="25">25/pág.</option>
                    <option value="50">50/pág.</option>
                  </select>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 px-2"
                    disabled={searchesPage === 0}
                    onClick={() => setSearchesPage((p) => Math.max(0, p - 1))}
                  >
                    <ChevronLeft className="h-3.5 w-3.5" />
                  </Button>
                  <span className="min-w-[80px] text-center text-xs">
                    {totalPages === 0 ? "—" : `Pág. ${searchesPage + 1}/${totalPages}`}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 px-2"
                    disabled={searchesPage + 1 >= totalPages}
                    onClick={() => setSearchesPage((p) => p + 1)}
                  >
                    <ChevronRight className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            </CardContent>
            );
          })()}
        </Card>
      )}

      {/* Batch classification (Anthropic Message Batches API) */}
      <Card>
        <CardHeader className="cursor-pointer select-none py-3"
          onClick={() => setBatchesExpanded(!batchesExpanded)}>
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-sm font-medium">
              <Layers className="h-4 w-4" />
              Classificação em Lote (Batch API)
              {batches.length > 0 && <Badge variant="secondary">{batches.length}</Badge>}
            </CardTitle>
            {batchesExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </div>
          <CardDescription>
            Envia milhares de publicações em um único lote à Anthropic (50% mais barato, até 24h). Use para volumes grandes.
          </CardDescription>
        </CardHeader>
        {batchesExpanded && (
          <CardContent className="space-y-4">
            {/* Form para submeter um novo batch */}
            <div className="flex flex-wrap items-end gap-3 rounded border bg-muted/30 p-3">
              <div className="grid gap-1.5">
                <Label className="text-xs">Escritório (opcional)</Label>
                <Select value={batchOfficeId || "_all"}
                  onValueChange={(v) => setBatchOfficeId(v === "_all" ? "" : v)}>
                  <SelectTrigger className="w-[220px]">
                    <SelectValue placeholder="Todos" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="_all">Todos os escritórios</SelectItem>
                    {offices.map((o) => (
                      <SelectItem key={o.external_id} value={String(o.external_id)}>
                        {officeLabel(o)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-1.5">
                <Label className="text-xs">Limite (opcional)</Label>
                <Input type="number" placeholder="Ex.: 3000"
                  value={batchLimit} onChange={(e) => setBatchLimit(e.target.value)}
                  className="w-[140px]" />
              </div>
              <div className="flex items-center gap-2 self-end">
                <Checkbox
                  id="batchOnlyUnlinked"
                  checked={batchOnlyUnlinked}
                  onCheckedChange={(v) => setBatchOnlyUnlinked(!!v)}
                />
                <Label htmlFor="batchOnlyUnlinked" className="text-xs cursor-pointer whitespace-nowrap">
                  Apenas sem processo
                </Label>
              </div>
              <Button onClick={handleSubmitBatch} disabled={submittingBatch}>
                {submittingBatch
                  ? <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  : <Play className="mr-2 h-4 w-4" />}
                Enviar lote
              </Button>
              <Button variant="outline" onClick={loadBatches} size="sm">
                <RefreshCw className="mr-2 h-3.5 w-3.5" />
                Atualizar lista
              </Button>
              <span className="ml-auto text-xs text-muted-foreground">
                Deduplicação: uma publicação por processo/dia é enviada ao agente. As demais herdam a classificação.
              </span>
            </div>

            {/* Lista de batches */}
            {batches.length === 0 ? (
              <div className="flex h-20 items-center justify-center text-xs text-muted-foreground">
                Nenhum lote enviado ainda.
              </div>
            ) : (() => {
              // Paginação client-side: tabela mostra `BATCHES_PAGE_SIZE`
              // por página e os controles abaixo navegam o histórico.
              const totalPages = Math.max(
                1,
                Math.ceil(batches.length / BATCHES_PAGE_SIZE),
              );
              const safePage = Math.min(batchPage, totalPages - 1);
              const pagedBatches = batches.slice(
                safePage * BATCHES_PAGE_SIZE,
                (safePage + 1) * BATCHES_PAGE_SIZE,
              );
              return (
              <div className="rounded border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[70px]">ID</TableHead>
                      <TableHead className="w-[120px]">Status</TableHead>
                      <TableHead className="w-[80px] text-right">Total</TableHead>
                      <TableHead className="w-[90px] text-right">Sucesso</TableHead>
                      <TableHead className="w-[80px] text-right">Erros</TableHead>
                      <TableHead className="w-[180px]">Criado</TableHead>
                      <TableHead className="w-[180px]">Ações</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {pagedBatches.map((b) => (
                      <TableRow key={b.id}>
                        <TableCell className="font-mono text-xs">#{b.id}</TableCell>
                        <TableCell>
                          <Badge variant={batchStatusColor(b.status)} className="text-xs">
                            {batchStatusLabel(b.status)}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right text-xs">{b.total_records}</TableCell>
                        <TableCell className="text-right text-xs text-green-700">
                          {b.succeeded_count}
                        </TableCell>
                        <TableCell className="text-right text-xs text-red-600">
                          {b.errored_count + b.expired_count + b.canceled_count}
                        </TableCell>
                        <TableCell className="text-xs">{formatDate(b.created_at)}</TableCell>
                        <TableCell>
                          <div className="flex gap-1">
                            {b.status !== "APLICADO" && b.status !== "FALHA" && (
                              <Button size="sm" variant="outline"
                                className="h-7 px-2 text-xs"
                                disabled={refreshingBatchId === b.id}
                                onClick={() => handleRefreshBatch(b.id)}
                                title="Consultar status na Anthropic">
                                {refreshingBatchId === b.id
                                  ? <Loader2 className="h-3 w-3 animate-spin" />
                                  : <RefreshCw className="h-3 w-3" />}
                              </Button>
                            )}
                            {b.status === "PRONTO" && (
                              <Button size="sm"
                                className="h-7 px-2 text-xs"
                                disabled={applyingBatchId === b.id}
                                onClick={() => handleApplyBatch(b.id)}
                                title="Baixar e aplicar classificações">
                                {applyingBatchId === b.id
                                  ? <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                                  : <Send className="mr-1 h-3 w-3" />}
                                Aplicar
                              </Button>
                            )}
                          </div>
                          {/* Retry errors button */}
                          {(
                            (b.status === "APLICADO" && (
                              b.errored_count > 0 || b.expired_count > 0 || b.canceled_count > 0
                            ))
                            || (b.status === "FALHA" && b.total_records > 0)
                          ) && (
                            <div className="mt-1 flex gap-1">
                              <Button size="sm" variant="outline"
                                className="h-6 px-2 text-[10px] text-orange-700 border-orange-300"
                                disabled={retryingBatchId === b.id}
                                onClick={() => handleRetryBatchErrors(b.id)}
                                title="Reprocessar itens que falharam">
                                {retryingBatchId === b.id
                                  ? <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                                  : <RotateCcw className="mr-1 h-3 w-3" />}
                                {b.status === "FALHA"
                                  ? `Reenviar ${b.total_records}`
                                  : `Retry ${b.errored_count + b.expired_count + b.canceled_count} erros`}
                              </Button>
                              {b.error_details && (
                                <Button size="sm" variant="ghost"
                                  className="h-6 px-2 text-[10px]"
                                  onClick={() => setErrorDetailsBatchId(
                                    errorDetailsBatchId === b.id ? null : b.id
                                  )}
                                  title="Ver detalhes dos erros">
                                  <Eye className="h-3 w-3" />
                                </Button>
                              )}
                              {b.status === "APLICADO" && b.total_records > 0 && (
                                <Button size="sm" variant="outline"
                                  className="h-6 px-2 text-[10px] text-blue-700 border-blue-300 hover:bg-blue-50"
                                  disabled={retryingBatchId === b.id}
                                  onClick={() => handleReclassifyFullBatch(b.id, b.total_records)}
                                  title="Reclassifica TODOS os registros do lote (inclui sucesso) — usa quando o prompt foi atualizado">
                                  {retryingBatchId === b.id
                                    ? <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                                    : <RotateCcw className="mr-1 h-3 w-3" />}
                                  Reclassificar lote ({b.total_records})
                                </Button>
                              )}
                            </div>
                          )}
                          {b.error_message && (
                            <div className="mt-0.5 max-w-[240px] truncate text-[10px] text-red-600"
                              title={b.error_message}>
                              {b.error_message}
                            </div>
                          )}
                          {/* Error details expansion */}
                          {errorDetailsBatchId === b.id && b.error_details && (
                            <div className="mt-1 max-h-[120px] overflow-y-auto rounded border border-red-200 bg-red-50 p-1.5 text-[10px]">
                              {Object.entries(b.error_details).map(([recId, reason]) => (
                                <div key={recId} className="flex gap-1 py-0.5">
                                  <span className="font-mono font-semibold text-red-700">#{recId}:</span>
                                  <span className="text-red-600">{reason}</span>
                                </div>
                              ))}
                            </div>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                {/* Controles de paginação — só aparecem se há >1 página */}
                {totalPages > 1 && (
                  <div className="flex items-center justify-between border-t px-3 py-2 text-xs text-muted-foreground">
                    <div>
                      Mostrando {safePage * BATCHES_PAGE_SIZE + 1}–
                      {Math.min(
                        (safePage + 1) * BATCHES_PAGE_SIZE,
                        batches.length,
                      )}{" "}
                      de {batches.length} lotes
                    </div>
                    <div className="flex items-center gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-7 px-2"
                        onClick={() => setBatchPage((p) => Math.max(0, p - 1))}
                        disabled={safePage === 0}
                      >
                        Anterior
                      </Button>
                      <span>
                        Página {safePage + 1} de {totalPages}
                      </span>
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-7 px-2"
                        onClick={() =>
                          setBatchPage((p) => Math.min(totalPages - 1, p + 1))
                        }
                        disabled={safePage >= totalPages - 1}
                      >
                        Próxima
                      </Button>
                    </div>
                  </div>
                )}
              </div>
              );
            })()}
          </CardContent>
        )}
      </Card>

      {/* Groups */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between mb-4">
            <CardTitle className="flex items-center gap-2">
              <BookOpen className="h-5 w-5" />
              Processos com Publicações
              {grouped && (
                <>
                  <Badge variant="secondary">{grouped.total_groups} grupos</Badge>
                  {typeof grouped.total_records === "number" && (
                    <Badge variant="outline">{grouped.total_records} publicações</Badge>
                  )}
                  {(() => {
                    // Conta filtros realmente aplicados pelo operador: o status
                    // DEFAULT (pendentes) nao conta, e inclui Cadastrado por +
                    // CNJ (antes ficavam de fora -> badge mostrava numero errado).
                    const active = [
                      filterStatus && filterStatus !== DEFAULT_PENDING_PUBLICATION_STATUSES_CSV,
                      filterOffice, filterCategory, filterUf,
                      filterVinculo, filterNatureza, filterPolo,
                      filterDateFrom, filterDateTo,
                      filterCnj, filterScheduledBy,
                    ].filter(Boolean).length;
                    return active > 0 ? (
                      <Badge
                        variant="default"
                        className="bg-blue-600 hover:bg-blue-700 cursor-help"
                        title="Quantidade de filtros aplicados agora — Reaplicar Templates e Exportar Excel respeitam esse escopo"
                      >
                        {active} filtro{active > 1 ? "s" : ""} ativo{active > 1 ? "s" : ""}
                      </Badge>
                    ) : null;
                  })()}
                </>
              )}
            </CardTitle>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setIsSaveFilterDialogOpen(true)}
              >
                <Save className="h-4 w-4 mr-2" />
                Salvar Filtros
              </Button>
              {filterStatus === DEFAULT_PENDING_PUBLICATION_STATUSES_CSV ? (
                <Button
                  size="sm"
                  variant="outline"
                  className="border-blue-200 bg-blue-50 text-blue-900 hover:bg-blue-100"
                  onClick={() => {
                    setFilterStatus("");
                    handleFilterChange("", filterOffice, filterDateFrom, filterDateTo, filterCategory, filterUf, filterVinculo, filterNatureza, filterPolo, filterCnj, filterScheduledBy);
                  }}
                  title="Filtro padrão mostra apenas pendentes (NOVO, CLASSIFICADO, ERRO). Clique pra ver também finalizados (Agendado, Ignorado, Obsoleto, Descartado)."
                >
                  <Filter className="h-4 w-4 mr-2" />
                  Mostrar todos os status
                </Button>
              ) : null}
              {savedFilters.length > 0 && (
                <Select onValueChange={(v) => {
                  const filter = savedFilters.find(f => f.id === parseInt(v));
                  if (filter) handleApplySavedFilter(filter);
                }}>
                  <SelectTrigger className="w-[180px]">
                    <SelectValue placeholder="Filtros salvos" />
                  </SelectTrigger>
                  <SelectContent>
                    {savedFilters.map(f => (
                      <SelectItem key={f.id} value={String(f.id)}>
                        {f.name} {f.is_default ? "(padrão)" : ""}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>
          </div>
          {/* ─── Filtros ─────────────────────────────────────── */}
          {/* Toggle mobile — no desktop (md+) o bloco fica sempre visível */}
          {(() => {
            const activeFiltersCount = [
              filterStatus && filterStatus !== DEFAULT_PENDING_PUBLICATION_STATUSES_CSV,
              filterOffice,
              filterCategory,
              filterUf,
              filterVinculo,
              filterNatureza,
              filterPolo,
              filterDateFrom,
              filterDateTo,
              filterCnj,
              filterScheduledBy,
            ].filter(Boolean).length;
            return (
              <div className="md:hidden">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-8 w-full justify-between text-xs"
                  onClick={() => setMobileFiltersOpen((v) => !v)}
                  aria-expanded={mobileFiltersOpen}
                  aria-controls="publications-filters-panel"
                >
                  <span className="flex items-center gap-2">
                    <Filter className="h-3.5 w-3.5" />
                    Filtros
                    {activeFiltersCount > 0 && (
                      <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
                        {activeFiltersCount}
                      </Badge>
                    )}
                  </span>
                  {mobileFiltersOpen ? (
                    <ChevronUp className="h-3.5 w-3.5" />
                  ) : (
                    <ChevronDown className="h-3.5 w-3.5" />
                  )}
                </Button>
              </div>
            );
          })()}
          <div
            id="publications-filters-panel"
            className={`space-y-3 ${mobileFiltersOpen ? "" : "hidden md:block"}`}
          >
            {/* Linha 1: filtros principais */}
            <div className="flex flex-wrap items-end gap-3">
              <div className="space-y-1">
                <Label className="text-[10px] uppercase tracking-wide text-muted-foreground">Status</Label>
                <MultiSelect
                  options={[
                    { value: "NOVO", label: "Novos" },
                    { value: "CLASSIFICADO", label: "Classificados" },
                    { value: "AGENDADO", label: "Agendados" },
                    { value: "IGNORADO", label: "Ignorados" },
                    { value: "ERRO", label: "Com erro" },
                    { value: "DESCARTADO_OBSOLETA", label: "Obsoletas" },
                  ]}
                  defaultValue={filterStatus ? filterStatus.split(",").filter(Boolean) : []}
                  onValueChange={(vals) => handleFilterChange(vals.join(","), filterOffice)}
                  placeholder="Todos"
                  className="h-8 min-w-[160px] text-xs"
                  maxCount={2}
                />
              </div>

              <div className="space-y-1">
                <Label className="text-[10px] uppercase tracking-wide text-muted-foreground">Escritório</Label>
                <MultiSelect
                  options={offices.map((o) => ({
                    value: String(o.external_id),
                    label: officeLabel(o),
                  }))}
                  defaultValue={filterOffice ? filterOffice.split(",").filter(Boolean) : []}
                  onValueChange={(vals) => handleFilterChange(filterStatus, vals.join(","))}
                  placeholder="Todos"
                  className="h-8 min-w-[220px] text-xs"
                  maxCount={2}
                />
              </div>

              <div className="space-y-1">
                <Label className="text-[10px] uppercase tracking-wide text-muted-foreground">Classificação</Label>
                <MultiSelect
                  options={Object.entries(taxonomy).map(([cat]) => ({ value: cat, label: cat }))}
                  defaultValue={filterCategory ? filterCategory.split(",").filter(Boolean) : []}
                  onValueChange={(vals) => handleFilterChange(filterStatus, filterOffice, undefined, undefined, vals.join(","))}
                  placeholder="Todas"
                  className="h-8 min-w-[200px] text-xs"
                  maxCount={2}
                />
              </div>

              <div className="space-y-1">
                <Label className="text-[10px] uppercase tracking-wide text-muted-foreground">Vínculo</Label>
                <MultiSelect
                  options={[
                    { value: "com_processo", label: "Com processo" },
                    { value: "sem_processo", label: "Sem processo" },
                  ]}
                  defaultValue={filterVinculo ? filterVinculo.split(",").filter(Boolean) : []}
                  onValueChange={(vals) => handleFilterChange(filterStatus, filterOffice, undefined, undefined, undefined, undefined, vals.join(","))}
                  placeholder="Todos"
                  className="h-8 min-w-[170px] text-xs"
                  maxCount={2}
                />
              </div>

              {stats?.available_naturezas && stats.available_naturezas.length > 0 && (
                <div className="space-y-1">
                  <Label className="text-[10px] uppercase tracking-wide text-muted-foreground">Natureza</Label>
                  <MultiSelect
                    options={stats.available_naturezas.map((n) => ({ value: n, label: n }))}
                    defaultValue={filterNatureza ? filterNatureza.split(",").filter(Boolean) : []}
                    onValueChange={(vals) => handleFilterChange(filterStatus, filterOffice, undefined, undefined, undefined, undefined, undefined, vals.join(","))}
                    placeholder="Todas"
                    className="h-8 min-w-[220px] text-xs"
                    maxCount={2}
                  />
                </div>
              )}

              <div className="space-y-1">
                <Label className="text-[10px] uppercase tracking-wide text-muted-foreground">Polo</Label>
                <MultiSelect
                  options={[
                    { value: "ativo", label: "Polo Ativo" },
                    { value: "passivo", label: "Polo Passivo" },
                    { value: "ambos", label: "Ambos os Polos" },
                  ]}
                  defaultValue={filterPolo ? filterPolo.split(",").filter(Boolean) : []}
                  onValueChange={(vals) => handleFilterChange(filterStatus, filterOffice, undefined, undefined, undefined, undefined, undefined, undefined, vals.join(","))}
                  placeholder="Todos"
                  className="h-8 min-w-[180px] text-xs"
                  maxCount={2}
                />
              </div>
            </div>

            {/* Linha 2: UF, período e ações */}
            <div className="flex flex-wrap items-end gap-3">
              <div className="space-y-1">
                <Label className="text-[10px] uppercase tracking-wide text-muted-foreground">UF</Label>
                <MultiSelect
                  options={availableUfs.map((uf) => ({ value: uf, label: uf }))}
                  defaultValue={filterUf ? filterUf.split(",").filter(Boolean) : []}
                  onValueChange={(vals) => handleFilterChange(filterStatus, filterOffice, undefined, undefined, undefined, vals.join(","))}
                  placeholder="Todas"
                  className="h-8 min-w-[120px] text-xs"
                  maxCount={3}
                />
              </div>

              {/* Filtro "Cadastrado por" — operador que finalizou o
                  agendamento (PublicationRecord.scheduled_by_user_id). Vem
                  do campo `available_scheduled_by` da resposta /records/grouped,
                  que respeita os outros filtros mas ignora o próprio
                  scheduled_by_user_id pra não sumir opções. */}
              <div className="space-y-1">
                <Label className="text-[10px] uppercase tracking-wide text-muted-foreground">Cadastrado por</Label>
                <MultiSelect
                  options={availableScheduledBy.map((u) => ({
                    value: String(u.user_id),
                    label: u.name || u.email || `Usuário #${u.user_id}`,
                  }))}
                  defaultValue={filterScheduledBy ? filterScheduledBy.split(",").filter(Boolean) : []}
                  onValueChange={(vals) =>
                    handleFilterChange(
                      filterStatus, filterOffice,
                      undefined, undefined, undefined, undefined,
                      undefined, undefined, undefined, undefined,
                      vals.join(","),
                    )
                  }
                  placeholder="Todos"
                  className="h-8 min-w-[200px] text-xs"
                  maxCount={2}
                />
              </div>

              <div className="space-y-1">
                <Label className="text-[10px] uppercase tracking-wide text-muted-foreground">Período (captura)</Label>
                <div className="flex items-center gap-1">
                  <div className="relative">
                    <Input
                      type="date"
                      value={filterDateFrom}
                      onChange={(e) => handleFilterChange(filterStatus, filterOffice, e.target.value, undefined)}
                      onClick={(e) => (e.currentTarget as HTMLInputElement).showPicker?.()}
                      onFocus={(e) => (e.currentTarget as HTMLInputElement).showPicker?.()}
                      className="h-8 w-[130px] text-xs pl-7 cursor-pointer"
                    />
                    <Calendar className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none" />
                  </div>
                  <span className="text-xs text-muted-foreground">a</span>
                  <div className="relative">
                    <Input
                      type="date"
                      value={filterDateTo}
                      onChange={(e) => handleFilterChange(filterStatus, filterOffice, undefined, e.target.value)}
                      onClick={(e) => (e.currentTarget as HTMLInputElement).showPicker?.()}
                      onFocus={(e) => (e.currentTarget as HTMLInputElement).showPicker?.()}
                      className="h-8 w-[130px] text-xs pl-7 cursor-pointer"
                    />
                    <Calendar className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none" />
                  </div>
                  {(filterDateFrom || filterDateTo) && (
                    <button
                      type="button"
                      onClick={() => handleFilterChange(filterStatus, filterOffice, "", "")}
                      className="rounded p-0.5 text-muted-foreground hover:text-destructive transition-colors"
                      title="Limpar período"
                    >
                      <XCircle className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              </div>

              {/* Busca livre por CNJ — match tolerante: backend compara por dígitos, ignora máscara */}
              <div className="space-y-1">
                <Label className="text-[10px] uppercase tracking-wide text-muted-foreground">Buscar processo</Label>
                <div className="relative">
                  <Input
                    type="text"
                    inputMode="numeric"
                    value={filterCnj}
                    onChange={(e) => setFilterCnj(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        handleFilterChange(filterStatus, filterOffice, undefined, undefined, undefined, undefined, undefined, undefined, undefined, filterCnj.trim());
                      }
                    }}
                    onBlur={() => {
                      // Aplica automaticamente quando o usuário sai do campo.
                      // handleFilterChange é idempotente — se o valor já for o mesmo
                      // o re-fetch só repete a request, sem efeito colateral.
                      handleFilterChange(filterStatus, filterOffice, undefined, undefined, undefined, undefined, undefined, undefined, undefined, filterCnj.trim());
                    }}
                    placeholder="CNJ (pode digitar só dígitos)"
                    className="h-8 w-[220px] text-xs pl-7"
                    title="Busca por CNJ. Aceita com ou sem máscara — comparamos só os dígitos."
                  />
                  <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none" />
                  {filterCnj && (
                    <button
                      type="button"
                      onClick={() => {
                        setFilterCnj("");
                        handleFilterChange(filterStatus, filterOffice, undefined, undefined, undefined, undefined, undefined, undefined, undefined, "");
                      }}
                      className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-destructive transition-colors"
                      title="Limpar CNJ"
                    >
                      <XCircle className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              </div>

              {/* Botão limpar filtros — volta pro default operacional
                  (so pendentes), nao pro estado completamente vazio. Pra
                  ver finalizados, operador usa "Mostrar todos" no
                  indicador do header da listagem. */}
              {((filterStatus && filterStatus !== DEFAULT_PENDING_PUBLICATION_STATUSES_CSV) || filterOffice || filterCategory || filterUf || filterVinculo || filterNatureza || filterPolo || filterDateFrom || filterDateTo || filterCnj || filterScheduledBy) && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 text-xs text-muted-foreground"
                  onClick={() => {
                    setFilterCnj("");
                    setFilterScheduledBy("");
                    handleFilterChange(DEFAULT_PENDING_PUBLICATION_STATUSES_CSV, "", "", "", "", "", "", "", "", "", "");
                  }}
                >
                  <XCircle className="h-3.5 w-3.5 mr-1" />
                  Limpar filtros
                </Button>
              )}

              <div className="ml-auto flex items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-8"
                  onClick={handleRebuildProposals}
                  disabled={rebuildingProposals}
                  title="Reaplica os templates de tarefa nos registros que estão batendo os filtros atuais. Útil após criar templates novos pra escopo específico."
                >
                  <RefreshCw className={`h-4 w-4 ${rebuildingProposals ? "animate-spin" : ""}`} />
                  <span className="ml-2 text-xs">Reaplicar Templates</span>
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-8"
                  onClick={handleExportExcel}
                  disabled={isExporting}
                  title="Exporta as publicações conforme os filtros atuais para um arquivo Excel"
                >
                  {isExporting ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <FileDown className="h-4 w-4" />
                  )}
                  <span className="ml-2 text-xs">Exportar Excel</span>
                </Button>
              </div>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {!grouped || visibleGroups.length === 0 ? (
            <div className="flex h-32 items-center justify-center text-muted-foreground">
              {grouped && filterUf
                ? `Nenhum grupo encontrado para a UF ${filterUf} com os filtros atuais.`
                : "Nenhum registro encontrado. Dispare uma busca acima."}
            </div>
          ) : (
            <>
              {selectedGroupKeys.size > 0 && (
                <div className="mb-2 flex items-center justify-between gap-2 rounded border border-primary/30 bg-primary/5 px-3 py-2 text-sm">
                  <span>
                    <strong>{selectedGroupKeys.size}</strong> grupo(s) selecionado(s)
                  </span>
                  <div className="flex gap-2">
                    <Button size="sm" variant="default" disabled={bulkProcessing} onClick={handleBulkConfirm}>
                      <Send className="mr-1 h-3 w-3" />
                      Confirmar agendamentos
                    </Button>
                    <Button size="sm" variant="outline" disabled={bulkProcessing} onClick={handleBulkIgnore}>
                      <EyeOff className="mr-1 h-3 w-3" />
                      Ignorar selecionados
                    </Button>
                    <Button size="sm" variant="ghost" disabled={bulkProcessing} onClick={clearSelection}>
                      Limpar seleção
                    </Button>
                  </div>
                </div>
              )}
              {/* Hint mobile: indica que a tabela scrolla horizontalmente */}
              <div className="mb-1 flex items-center gap-1 text-[11px] text-muted-foreground md:hidden">
                <ChevronLeft className="h-3 w-3" />
                <span>Arraste para o lado para ver todas as colunas</span>
                <ChevronRight className="h-3 w-3" />
              </div>
              <div className="h-[min(820px,calc(100vh-280px))] overflow-auto rounded border text-[13px]">
                <div className="min-w-[1100px] md:min-w-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[40px]">
                        <Checkbox
                          checked={
                            visibleGroups.length > 0 &&
                            visibleGroups.every((g) => selectedGroupKeys.has(groupKey(g)))
                          }
                          onCheckedChange={toggleSelectAllVisible}
                          aria-label="Selecionar todos os grupos visíveis"
                        />
                      </TableHead>
                      <TableHead className="w-[130px]">Processo</TableHead>
                      <TableHead className="w-[160px]">Escritório</TableHead>
                      <TableHead className="w-[100px]">Datas</TableHead>
                      <TableHead>Publicações</TableHead>
                      <TableHead className="w-[120px]">Classificação</TableHead>
                      <TableHead className="w-[90px]">Status</TableHead>
                      <TableHead className="w-[190px]">Proposta de Tarefa</TableHead>
                      <TableHead className="w-[110px]">Ações</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {visibleGroups.map((group, gi) => {
                      const status = groupStatusSummary(group);
                      // Classificações: usa array do grupo ou fallback dos records
                      const groupClassifications = group.classifications && group.classifications.length > 0
                        ? group.classifications
                        : [];
                      const categories = groupClassifications.length > 0
                        ? [...new Set(groupClassifications.map((c) => c.categoria))]
                        : [...new Set(group.records.map((r) => r.category).filter(Boolean))];
                      const subcategories = groupClassifications.length > 0
                        ? [...new Set(groupClassifications.map((c) => c.subcategoria).filter((s) => s && s !== "-"))]
                        : [...new Set(group.records.map((r) => r.subcategory).filter((s) => s && s !== "-"))];
                      const polos = groupClassifications.length > 0
                        ? [...new Set(groupClassifications.map((c) => c.polo).filter(Boolean))] as string[]
                        : [...new Set(group.records.map((r) => r.polo).filter(Boolean))] as string[];
                      // Pega a data/hora de audiência do primeiro record que tem
                      const audRecord = group.records.find((r) => r.audiencia_data);
                      const proposedTasks = group.proposed_tasks || (group.proposed_task ? [group.proposed_task] : []);
                      const hasProposal = proposedTasks.length > 0;

                      const gKey = groupKey(group);
                      return (
                        <TableRow key={`${group.lawsuit_id}-${gi}`}>
                          <TableCell className="w-[40px]">
                            <Checkbox
                              checked={selectedGroupKeys.has(gKey)}
                              onCheckedChange={() => toggleGroupSelection(gKey)}
                              aria-label="Selecionar grupo"
                            />
                          </TableCell>
                          <TableCell className="font-mono text-xs">
                            {group.lawsuit_cnj ? (
                              <div title={group.lawsuit_cnj}>
                                <div className="flex items-center gap-1">
                                  <div className="max-w-[110px] truncate font-medium">{group.lawsuit_cnj}</div>
                                  {(() => {
                                    const uf = ufFromCnj(group.lawsuit_cnj);
                                    if (!uf) return null;
                                    return (
                                      <span className="inline-flex shrink-0 rounded border border-slate-300 bg-slate-100 px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-slate-700"
                                        title="UF derivada do CNJ">
                                        {uf}
                                      </span>
                                    );
                                  })()}
                                </div>
                                <div className="text-muted-foreground">ID: {group.lawsuit_id}</div>
                                <L1EtiquetaChips etiquetas={group.l1_etiquetas} />
                              </div>
                            ) : group.lawsuit_id ? (
                              <div>
                                <span className="text-muted-foreground">{group.lawsuit_id}</span>
                                <L1EtiquetaChips etiquetas={group.l1_etiquetas} />
                              </div>
                            ) : (
                              <div>
                                <span className="italic text-orange-600">sem processo</span>
                                {(() => {
                                  const cnj = group.records.find((r) => r.linked_lawsuit_cnj)?.linked_lawsuit_cnj;
                                  return cnj ? (
                                    <div className="mt-0.5 font-mono text-[10px] text-muted-foreground" title={cnj}>
                                      {cnj}
                                    </div>
                                  ) : null;
                                })()}
                                {(() => {
                                  const naturezas = [...new Set(
                                    group.records
                                      .map((r) => r.natureza_processo)
                                      .filter(Boolean)
                                  )];
                                  return naturezas.length > 0 ? (
                                    <div className="flex flex-wrap gap-1 mt-0.5">
                                      {naturezas.map((n: string) => (
                                        <Badge key={n} variant="outline" className="text-[9px] px-1 py-0 border-orange-300 text-orange-700 font-normal">
                                          {n}
                                        </Badge>
                                      ))}
                                    </div>
                                  ) : null;
                                })()}
                              </div>
                            )}
                          </TableCell>
                          <TableCell className="text-xs">
                            {group.office_id ? (
                              <div className="flex items-center gap-1">
                                <Building2 className="h-3 w-3 flex-shrink-0 text-muted-foreground" />
                                <span className="truncate">{officeName(group.office_id) || group.office_id}</span>
                              </div>
                            ) : <span className="text-muted-foreground">—</span>}
                          </TableCell>
                          <TableCell className="text-xs">
                            {(() => {
                              const first = group.records[0];
                              return (
                                <div className="space-y-1">
                                  <div title="Data da publicação (tribunal) — para contagem de prazo">
                                    <div className="text-[10px] text-muted-foreground">Publicação</div>
                                    <div className="font-medium">{formatDateShort(first.publication_date)}</div>
                                  </div>
                                  <div title="Data de captura (Ajus) — referência do fluxo de trabalho">
                                    <div className="text-[10px] text-muted-foreground">Captura</div>
                                    <div>{formatDateShort(first.creation_date)}</div>
                                  </div>
                                </div>
                              );
                            })()}
                          </TableCell>
                          <TableCell>
                            <div className="space-y-0.5">
                              {group.records.slice(0, 2).map((r) => (
                                <div key={r.id} className="flex items-center gap-1">
                                  <span className="max-w-[200px] truncate text-xs text-muted-foreground">
                                    {r.description_preview || String(r.legal_one_update_id)}
                                  </span>
                                  <Button variant="ghost" size="sm" className="h-5 w-5 flex-shrink-0 p-0"
                                    onClick={() => loadRecordDetail(r.id)}
                                    title="Ver detalhes">
                                    <Eye className="h-3 w-3" />
                                  </Button>
                                </div>
                              ))}
                              {group.records.length > 2 && (
                                <div className="text-xs text-muted-foreground">
                                  + {group.records.length - 2} mais
                                </div>
                              )}
                            </div>
                          </TableCell>
                          <TableCell className="text-xs">
                            {(() => {
                              const groupKey = `${group.lawsuit_id ?? "null"}-${gi}`;
                              const recordIds = group.records.map((r) => r.id);
                              const primaryCat = categories[0] || "";
                              const primarySub = subcategories[0] || "";
                              const currentValue = primaryCat
                                ? primarySub
                                  ? `${primaryCat}|||${primarySub}`
                                  : primaryCat
                                : "";
                              const isReclassifying = reclassifyingGroup === groupKey;
                              return (
                                <div className="space-y-1">
                                  {categories.length > 0 && (
                                    <div className="space-y-0.5">
                                      {categories.map((cat, ci) => (
                                        <div key={ci}>
                                          <div className="font-medium">{cat}</div>
                                          {subcategories[ci] && (
                                            <div className="text-muted-foreground">{subcategories[ci]}</div>
                                          )}
                                        </div>
                                      ))}
                                      {categories.length > 1 && (
                                        <div className="text-[10px] text-muted-foreground italic">
                                          {categories.length} classificações
                                        </div>
                                      )}
                                    </div>
                                  )}

                                  {/* Dropdown de reclassificação manual */}
                                  <Select
                                    value={currentValue}
                                    disabled={isReclassifying}
                                    onValueChange={(v) => {
                                      if (v === currentValue) return;
                                      handleReclassifyGroup(groupKey, recordIds, v);
                                    }}
                                  >
                                    <SelectTrigger className="h-6 border-dashed px-1 py-0 text-[10px]">
                                      <SelectValue placeholder={
                                        isReclassifying
                                          ? "Aplicando..."
                                          : categories.length === 0
                                            ? "Classificar manualmente"
                                            : "Alterar classificação"
                                      } />
                                    </SelectTrigger>
                                    <SelectContent className="max-h-80">
                                      {Object.entries(taxonomy).map(([cat, subs]) => (
                                        <div key={cat}>
                                          {subs && subs.length > 0 ? (
                                            <>
                                              <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                                                {cat}
                                              </div>
                                              {subs.map((sub) => (
                                                <SelectItem
                                                  key={`${cat}|||${sub}`}
                                                  value={`${cat}|||${sub}`}
                                                >
                                                  {sub}
                                                </SelectItem>
                                              ))}
                                            </>
                                          ) : (
                                            <SelectItem value={cat}>{cat}</SelectItem>
                                          )}
                                        </div>
                                      ))}
                                    </SelectContent>
                                  </Select>
                                {polos.length > 0 && (
                                  <div className="flex flex-wrap gap-1 pt-0.5">
                                    {polos.map((p) => {
                                      const info = poloLabel(p);
                                      return info ? (
                                        <span key={p}
                                          className={`inline-flex rounded border px-1 py-0 text-[10px] font-medium ${info.className}`}>
                                          {info.label}
                                        </span>
                                      ) : null;
                                    })}
                                  </div>
                                )}
                                {audRecord && (
                                  <div className="mt-0.5 space-y-0.5">
                                    <div className="flex items-center gap-1 text-[10px] font-semibold text-violet-700"
                                      title="Data/hora da audiência extraída pelo classificador">
                                      <Calendar className="h-3 w-3" />
                                      {audRecord.audiencia_data
                                        ? new Date(audRecord.audiencia_data + "T12:00:00").toLocaleDateString("pt-BR")
                                        : "—"
                                      }
                                      {audRecord.audiencia_hora && (
                                        <span className="ml-0.5">{audRecord.audiencia_hora}</span>
                                      )}
                                    </div>
                                    {audRecord.audiencia_link && (
                                      <a href={audRecord.audiencia_link} target="_blank" rel="noopener noreferrer"
                                        className="flex items-center gap-0.5 text-[10px] font-medium text-blue-600 hover:underline"
                                        title={audRecord.audiencia_link}>
                                        <Link2 className="h-3 w-3" />
                                        Link da audiência
                                        <ExternalLink className="h-2.5 w-2.5" />
                                      </a>
                                    )}
                                  </div>
                                )}
                                </div>
                              );
                            })()}
                          </TableCell>
                          <TableCell>
                            <div className="flex flex-col gap-1">
                              <div className="flex items-center gap-1">
                                <Badge variant={statusColor(status)} className="text-xs">{status}</Badge>
                                {categories.length > 0 && (
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    className="h-5 w-5 p-0 text-muted-foreground hover:text-red-600"
                                    title="Reportar classificação errada"
                                    onClick={() => openFeedback(group.records[0])}
                                  >
                                    <ThumbsDown className="h-3 w-3" />
                                  </Button>
                                )}
                              </div>
                              {/* Trilha do AGENDADO: mostra quem agendou (populado via pub002). */}
                              {status === "AGENDADO" && (() => {
                                const scheduled = group.records.find(
                                  (r) => r.status === "AGENDADO" && (r.scheduled_by_name || r.scheduled_by_email)
                                );
                                if (!scheduled) return null;
                                const who = scheduled.scheduled_by_name || scheduled.scheduled_by_email;
                                const when = scheduled.scheduled_at
                                  ? new Date(scheduled.scheduled_at).toLocaleString("pt-BR", {
                                      day: "2-digit", month: "2-digit", year: "2-digit",
                                      hour: "2-digit", minute: "2-digit",
                                    })
                                  : null;
                                return (
                                  <div
                                    className="flex items-center gap-1 text-[10px] text-muted-foreground"
                                    title={`Agendado por ${scheduled.scheduled_by_name ?? ""}${scheduled.scheduled_by_email ? ` <${scheduled.scheduled_by_email}>` : ""}${when ? ` em ${when}` : ""}`}
                                  >
                                    <UserCircle2 className="h-3 w-3 flex-shrink-0" />
                                    <span className="truncate max-w-[140px]">
                                      por <span className="font-medium text-foreground">{who}</span>
                                    </span>
                                  </div>
                                );
                              })()}
                            </div>
                          </TableCell>
                          <TableCell className="text-xs">
                            {hasProposal ? (
                              <div className="space-y-1">
                                {proposedTasks.slice(0, 3).map((pt, pi) => (
                                  <div key={pi} className={pi > 0 ? "border-t pt-1" : ""}>
                                    <div className="max-w-[180px] truncate font-medium text-green-700"
                                      title={pt.description}>
                                      {pt.description}
                                    </div>
                                    <div className="text-muted-foreground">
                                      Prazo: {formatDateShort(pt.endDateTime ?? null)}
                                    </div>
                                    {pt.suggested_responsible?.name && (
                                      <div className="text-[10px] text-blue-600 truncate max-w-[180px]"
                                        title={`Responsável da pasta: ${pt.suggested_responsible.name}`}>
                                        Resp.: {pt.suggested_responsible.name}
                                      </div>
                                    )}
                                  </div>
                                ))}
                                {proposedTasks.length > 3 && (
                                  <div className="text-[10px] text-muted-foreground italic">
                                    + {proposedTasks.length - 3} tarefas
                                  </div>
                                )}
                              </div>
                            ) : (
                              <span className="italic text-muted-foreground">
                                {status === "AGENDADO" ? "Já agendado" : "Sem template"}
                              </span>
                            )}
                          </TableCell>
                          <TableCell>
                            <div className="flex flex-col gap-1">
                              {status !== "AGENDADO" && (
                                <Button size="sm"
                                  className="h-7 px-2 text-xs"
                                  variant={hasProposal ? "default" : "outline"}
                                  onClick={() => openScheduleDialog(group)}>
                                  <Send className="mr-1 h-3 w-3" />
                                  {hasProposal ? "Confirmar" : "Agendar"}
                                </Button>
                              )}
                              {group.records.some((r) => r.status !== "AGENDADO" && r.status !== "IGNORADO") && (
                                <Button size="sm" variant="ghost"
                                  className="h-7 px-2 text-xs text-muted-foreground"
                                  onClick={() => group.records.filter((r) => r.status !== "AGENDADO" && r.status !== "IGNORADO")
                                    .forEach((r) => handleIgnoreRecord(r.id))}>
                                  Ignorar
                                </Button>
                              )}
                            </div>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
                </div>
              </div>

              {grouped && grouped.total_groups > 0 && (
                <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                  <span className="text-sm text-muted-foreground">
                    Mostrando {groupPage * groupPageSize + 1}–
                    {Math.min(
                      (groupPage + 1) * groupPageSize,
                      grouped.total_groups,
                    )}{" "}
                    de {grouped.total_groups} grupos
                    {typeof grouped.total_records === "number" && (
                      <> · {grouped.total_records} publicações</>
                    )}
                    {totalPages > 1 && (
                      <> · Página {groupPage + 1} de {totalPages}</>
                    )}
                  </span>
                  {/* Selecionar todos os filtrados — fica visível quando há
                      mais de uma página de resultados. Carrega TODOS os
                      grupos do filtro num state paralelo (cap em 500 do
                      backend) e marca todos. Bulk handlers usam esse scope. */}
                  {totalPages > 1 && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={handleSelectAllFiltered}
                      disabled={bulkScopeLoading || bulkProcessing}
                      title={`Marca todos os ${grouped.total_groups} grupos que batem com os filtros atuais (não só a página visível).`}
                    >
                      {bulkScopeLoading
                        ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                        : <CheckSquare className="mr-2 h-3.5 w-3.5" />}
                      Selecionar todos os {grouped.total_groups} filtrados
                    </Button>
                  )}
                  <div className="flex items-center gap-3">
                    {/* Selector de tamanho de página */}
                    <div className="flex items-center gap-2">
                      <Label className="text-xs text-muted-foreground">
                        Itens por página:
                      </Label>
                      <Select
                        value={String(groupPageSize)}
                        onValueChange={(v) => {
                          const next = Number(v) as 20 | 50 | 100;
                          if (![20, 50, 100].includes(next)) return;
                          // Reset de página + page size novo. O useEffect
                          // de baixo escuta groupPageSize e re-fetcha com
                          // o valor atualizado (evita stale closure).
                          setGroupPage(0);
                          setGroupPageSize(next);
                        }}
                      >
                        <SelectTrigger className="h-8 w-[70px] text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="20">20</SelectItem>
                          <SelectItem value="50">50</SelectItem>
                          <SelectItem value="100">100</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    {/* Paginação */}
                    {totalPages > 1 && (
                      <div className="flex gap-2">
                        <Button variant="outline" size="sm" disabled={groupPage === 0}
                          onClick={() => handleGroupPageChange(groupPage - 1)}>
                          <ChevronLeft className="h-4 w-4" />
                        </Button>
                        <Button variant="outline" size="sm" disabled={groupPage >= totalPages - 1}
                          onClick={() => handleGroupPageChange(groupPage + 1)}>
                          <ChevronRight className="h-4 w-4" />
                        </Button>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {/* Detail Dialog */}
      <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
        <DialogContent className="!max-w-[min(95vw,72rem)] max-h-[92vh] w-[95vw] overflow-y-auto overflow-x-hidden p-5 sm:p-6">
          <DialogHeader>
            <DialogTitle className="text-xl">
              Publicação #{selectedRecord?.id} (LO: {selectedRecord?.legal_one_update_id})
            </DialogTitle>
            <DialogDescription>Detalhe completo do registro.</DialogDescription>
          </DialogHeader>
          {selectedRecord && (
            <div className="space-y-4">
              <div className="grid grid-cols-1 gap-4 text-base sm:grid-cols-2">
                <div>
                  <span className="font-medium text-muted-foreground">Status: </span>
                  <Badge variant={statusColor(selectedRecord.status)}>{selectedRecord.status}</Badge>
                </div>
                <div>
                  <span className="font-medium text-muted-foreground">Data da Publicação: </span>
                  {formatDate(selectedRecord.publication_date)}
                </div>
                {selectedRecord.creation_date && (
                  <div>
                    <span className="font-medium text-muted-foreground">Data de Captura (Ajus): </span>
                    {formatDate(selectedRecord.creation_date)}
                  </div>
                )}
                <div>
                  <span className="font-medium text-muted-foreground">Processo: </span>
                  {selectedRecord.linked_lawsuit_cnj || selectedRecord.linked_lawsuit_id || "N/A"}
                </div>
                <div>
                  <span className="font-medium text-muted-foreground">Escritório: </span>
                  {officeName(selectedRecord.linked_office_id) || "N/A"}
                </div>
                {selectedRecord.category && (
                  <>
                    <div>
                      <span className="font-medium text-muted-foreground">Categoria: </span>
                      {selectedRecord.category}
                    </div>
                    <div>
                      <span className="font-medium text-muted-foreground">Subcategoria: </span>
                      {selectedRecord.subcategory || "-"}
                    </div>
                  </>
                )}
                {selectedRecord.classifications && selectedRecord.classifications.length > 1 && (
                  <div className="col-span-2">
                    <span className="font-medium text-muted-foreground">Classificações adicionais: </span>
                    <div className="mt-1 space-y-1">
                      {selectedRecord.classifications.slice(1).map((c, i) => (
                        <div key={i} className="rounded border bg-muted/30 px-2 py-1 text-xs">
                          <span className="font-medium">{c.categoria}</span>
                          {c.subcategoria && c.subcategoria !== "-" && (
                            <span className="text-muted-foreground"> / {c.subcategoria}</span>
                          )}
                          {c.polo && (() => {
                            const info = poloLabel(c.polo);
                            return info ? (
                              <span className={`ml-2 inline-flex rounded border px-1 py-0 text-[10px] font-medium ${info.className}`}>
                                {info.label}
                              </span>
                            ) : null;
                          })()}
                          {c.confianca && (
                            <span className="ml-2 text-muted-foreground">({c.confianca})</span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {selectedRecord.polo && (() => {
                  const info = poloLabel(selectedRecord.polo);
                  return info ? (
                    <div className="col-span-2">
                      <span className="font-medium text-muted-foreground">Polo: </span>
                      <span
                        className={`inline-flex rounded border px-2 py-0.5 text-xs font-medium ${info.className}`}>
                        {info.label}
                      </span>
                    </div>
                  ) : null;
                })()}
                {selectedRecord.audiencia_data && (
                  <div className="col-span-2">
                    <span className="font-medium text-muted-foreground">Audiência: </span>
                    <span className="inline-flex items-center gap-1 rounded border border-violet-300 bg-violet-50 px-2 py-0.5 text-xs font-semibold text-violet-800">
                      <Calendar className="h-3 w-3" />
                      {new Date(selectedRecord.audiencia_data + "T12:00:00").toLocaleDateString("pt-BR")}
                      {selectedRecord.audiencia_hora && (
                        <span className="ml-1">às {selectedRecord.audiencia_hora}</span>
                      )}
                    </span>
                  </div>
                )}
                {selectedRecord.audiencia_link && (
                  <div className="col-span-2">
                    <span className="font-medium text-muted-foreground">Link audiência: </span>
                    <a href={selectedRecord.audiencia_link} target="_blank" rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 rounded border border-blue-300 bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 hover:underline">
                      <Link2 className="h-3 w-3" />
                      {selectedRecord.audiencia_link.length > 50
                        ? selectedRecord.audiencia_link.slice(0, 50) + "..."
                        : selectedRecord.audiencia_link}
                      <ExternalLink className="h-3 w-3" />
                    </a>
                  </div>
                )}
                {/* Prazo fatal — vem da classificação primária (Classification.prazo_*).
                    Mostramos quando o classificador identificou que a publicação
                    abre prazo processual. Tipo "util" indica contagem em dias
                    úteis (CPC art. 219); "corrido" indica exceção legal. */}
                {(() => {
                  const cls = selectedRecord.classifications?.[0];
                  if (!cls?.prazo_dias) return null;
                  const tipoLabel = cls.prazo_tipo === "corrido" ? "corridos" : "úteis";
                  return (
                    <div className="col-span-2">
                      <span className="font-medium text-muted-foreground">Prazo fatal: </span>
                      <span className="inline-flex items-center gap-1 rounded border border-amber-300 bg-amber-50 px-2 py-0.5 text-xs font-semibold text-amber-800">
                        <Clock className="h-3 w-3" />
                        {cls.prazo_dias} dia{cls.prazo_dias === 1 ? "" : "s"} {tipoLabel}
                      </span>
                      {cls.prazo_fundamentacao && (
                        <p className="mt-1 text-xs text-muted-foreground italic">
                          {cls.prazo_fundamentacao}
                        </p>
                      )}
                    </div>
                  );
                })()}
                {selectedRecord.legal_one_update_id && (
                  <div className="col-span-2">
                    <span className="font-medium text-muted-foreground">Publicação no Legal One: </span>
                    <a
                      href={`https://firm.legalone.com.br/publications?publicationId=${selectedRecord.legal_one_update_id}&treatStatus=3`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 rounded border border-blue-300 bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 hover:underline"
                    >
                      <Link2 className="h-3 w-3" />
                      Abrir no Legal One
                      <ExternalLink className="h-3 w-3" />
                    </a>
                  </div>
                )}
                {/* Atalho pra "Compromissos e Tarefas" do PROCESSO no Legal One.
                    Diferente do link de publicação acima (que usa
                    `legal_one_update_id`, o external_id da publicação na API
                    L1), aqui usamos `linked_lawsuit_id`, que vem do
                    `relationships[Litigation].linkId` do payload L1 — é o
                    external_id do processo. Por isso a condição é independente
                    do legal_one_update_id (uma publicação pode ter um sem o
                    outro, ex: publicação avulsa sem pasta vinculada). */}
                {selectedRecord.linked_lawsuit_id && (
                  <div className="col-span-2">
                    <span className="font-medium text-muted-foreground">Compromissos e Tarefas: </span>
                    <a
                      href={`https://mdradvocacia.novajus.com.br/processos/Processos/DetailsCompromissosTarefas/${selectedRecord.linked_lawsuit_id}?renderOnlySection=True`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 rounded border border-violet-300 bg-violet-50 px-2 py-0.5 text-xs font-medium text-violet-700 hover:underline"
                    >
                      <Calendar className="h-3 w-3" />
                      Abrir no Legal One
                      <ExternalLink className="h-3 w-3" />
                    </a>
                  </div>
                )}
              </div>
              <div>
                <Label className="text-sm text-muted-foreground">Texto da Publicação</Label>
                <ScrollArea className="mt-1 h-[360px] rounded border p-4">
                  <p className="whitespace-pre-wrap text-[15px] leading-relaxed">
                    {selectedRecord.description || "Sem texto disponível."}
                  </p>
                </ScrollArea>
              </div>
              {selectedRecord.notes && (
                <div>
                  <Label className="text-sm text-muted-foreground">Observações</Label>
                  <p className="mt-1 whitespace-pre-wrap text-[15px] leading-relaxed">{selectedRecord.notes}</p>
                </div>
              )}
              {/* Diagnóstico: tarefas L1 do processo. Aparece só quando
                  publicação tem lawsuit vinculado. Duas seções: pendentes
                  (todas, com badge amber) + últimas concluídas (até 5,
                  neutras). Falha graceful — operador ainda consegue
                  agendar mesmo sem o diagnóstico. */}
              {selectedRecord.linked_lawsuit_id && (
                <div className="rounded-lg border bg-muted/20 p-3">
                  <div className="mb-2 flex items-center justify-between">
                    <Label className="text-sm font-semibold flex items-center gap-1.5">
                      <Calendar className="h-4 w-4 text-muted-foreground" />
                      Tarefas no Legal One
                    </Label>
                    {recentTasks?.truncated && (
                      <span className="text-[10px] text-amber-700">
                        Lista pode estar incompleta — abrir no L1 para ver tudo
                      </span>
                    )}
                  </div>
                  {recentTasksLoading && (
                    <div className="flex items-center gap-2 py-3 text-xs text-muted-foreground">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      Consultando tarefas do processo...
                    </div>
                  )}
                  {!recentTasksLoading && recentTasks?.check_failed && (
                    <p className="py-2 text-xs text-muted-foreground">
                      Não foi possível carregar agora. Abra os{" "}
                      <a
                        href={`https://mdradvocacia.novajus.com.br/processos/Processos/DetailsCompromissosTarefas/${selectedRecord.linked_lawsuit_id}?renderOnlySection=True`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-violet-700 underline"
                      >
                        Compromissos e Tarefas no Legal One
                      </a>
                      {" "}pra ver direto.
                    </p>
                  )}
                  {!recentTasksLoading &&
                    recentTasks &&
                    !recentTasks.check_failed && (
                      <>
                        {/* Pendentes — todas */}
                        <div className="mb-3">
                          <p className="mb-1.5 text-xs font-medium text-amber-800">
                            Pendentes ({recentTasks.pending_count})
                          </p>
                          {recentTasks.pending.length === 0 ? (
                            <p className="text-xs text-muted-foreground italic">
                              Nenhuma tarefa em aberto neste processo.
                            </p>
                          ) : (
                            <ul className="space-y-1.5">
                              {recentTasks.pending.map((t: any) => (
                                <li
                                  key={t.task_id}
                                  className="rounded border border-amber-200 bg-amber-50/60 px-2 py-1.5 text-xs"
                                >
                                  <div className="flex items-center gap-1.5 flex-wrap">
                                    <span className="rounded bg-amber-200 px-1.5 py-0.5 text-[10px] font-semibold text-amber-900">
                                      {t.status_label}
                                    </span>
                                    {t.subtype_name && (
                                      <span className="text-amber-900 font-medium">
                                        {t.subtype_name}
                                      </span>
                                    )}
                                    {t.l1_url && (
                                      <a
                                        href={t.l1_url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="ml-auto text-[10px] text-blue-600 hover:underline inline-flex items-center gap-0.5"
                                      >
                                        Abrir <ExternalLink className="h-2.5 w-2.5" />
                                      </a>
                                    )}
                                  </div>
                                  {t.description && (
                                    <p className="mt-0.5 text-amber-900/80 line-clamp-2">
                                      {t.description}
                                    </p>
                                  )}
                                  {t.creation_date && (
                                    <p className="mt-0.5 text-[10px] text-muted-foreground">
                                      Criada em{" "}
                                      {new Date(t.creation_date).toLocaleDateString("pt-BR")}
                                    </p>
                                  )}
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                        {/* Últimas concluídas */}
                        <div>
                          <p className="mb-1.5 text-xs font-medium text-muted-foreground">
                            Últimas concluídas ({recentTasks.recent_completed_count})
                          </p>
                          {recentTasks.recent_completed.length === 0 ? (
                            <p className="text-xs text-muted-foreground italic">
                              Sem tarefas terminadas recentemente.
                            </p>
                          ) : (
                            <ul className="space-y-1.5">
                              {recentTasks.recent_completed.map((t: any) => (
                                <li
                                  key={t.task_id}
                                  className="rounded border bg-background px-2 py-1.5 text-xs"
                                >
                                  <div className="flex items-center gap-1.5 flex-wrap">
                                    <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground">
                                      {t.status_label}
                                    </span>
                                    {t.subtype_name && (
                                      <span className="font-medium">{t.subtype_name}</span>
                                    )}
                                    {t.l1_url && (
                                      <a
                                        href={t.l1_url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="ml-auto text-[10px] text-blue-600 hover:underline inline-flex items-center gap-0.5"
                                      >
                                        Abrir <ExternalLink className="h-2.5 w-2.5" />
                                      </a>
                                    )}
                                  </div>
                                  {t.description && (
                                    <p className="mt-0.5 text-muted-foreground line-clamp-2">
                                      {t.description}
                                    </p>
                                  )}
                                  <p className="mt-0.5 text-[10px] text-muted-foreground">
                                    {t.creation_date && (
                                      <>
                                        Criada em{" "}
                                        {new Date(t.creation_date).toLocaleDateString("pt-BR")}
                                      </>
                                    )}
                                    {(t.effective_end_date_time || t.end_date_time) && (
                                      <>
                                        {t.creation_date ? " · " : ""}
                                        Concluída em{" "}
                                        {new Date(
                                          t.effective_end_date_time || t.end_date_time,
                                        ).toLocaleDateString("pt-BR")}
                                      </>
                                    )}
                                  </p>
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                      </>
                    )}
                </div>
              )}
              <div className="flex flex-wrap gap-2 pt-2">
                {selectedRecord.status !== "AGENDADO" && selectedRecord.status !== "IGNORADO" && (
                  <>
                    {/* Confirmar agendamento — abre o modal de scheduling
                        com o grupo correspondente ao record. Procura entre
                        os grupos da página atual: por lawsuit_id se houver,
                        ou pelo próprio record.id (grupos no-lawsuit). */}
                    <Button
                      size="sm"
                      onClick={() => {
                        const groups = grouped?.groups || [];
                        const group = groups.find((g) => {
                          if (selectedRecord.linked_lawsuit_id != null) {
                            return g.lawsuit_id === selectedRecord.linked_lawsuit_id;
                          }
                          // Grupo no-lawsuit: contém só esse record.
                          return (
                            g.lawsuit_id == null &&
                            g.records.some((r) => r.id === selectedRecord.id)
                          );
                        });
                        if (!group) {
                          toast({
                            title: "Não foi possível abrir o agendamento",
                            description:
                              "O grupo desta publicação não está na página atual. Volte pra listagem e tente de lá.",
                            variant: "destructive",
                          });
                          return;
                        }
                        setDetailOpen(false);
                        openScheduleDialog(group);
                      }}
                    >
                      <Check className="mr-1 h-3.5 w-3.5" />
                      Confirmar agendamento
                    </Button>
                    <Button size="sm" variant="outline"
                      onClick={() => { handleIgnoreRecord(selectedRecord.id); setDetailOpen(false); }}>
                      <EyeOff className="mr-1 h-3.5 w-3.5" />
                      Ignorar publicação
                    </Button>
                  </>
                )}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* ─── Duplicate Divergences Section ─────────────────────────────── */}
      <Card className="border-yellow-200">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="flex items-center gap-2 text-base font-semibold hover:text-yellow-700 transition-colors"
                onClick={() => {
                  if (!showDuplicates) { setShowDuplicates(true); loadDuplicates(); }
                  else setShowDuplicates(false);
                }}
              >
                {showDuplicates ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                Registros Duplicados com Textos Divergentes
              </button>
              {duplicates && (
                <Badge variant="outline" className="text-yellow-700">
                  {duplicates.total} par{duplicates.total !== 1 ? "es" : ""}
                </Badge>
              )}
            </div>
            {showDuplicates && (
              <Button variant="ghost" size="sm" onClick={loadDuplicates} disabled={loadingDuplicates}>
                <RefreshCw className={`h-3.5 w-3.5 ${loadingDuplicates ? "animate-spin" : ""}`} />
              </Button>
            )}
          </div>
          <CardDescription>
            Pares de registros com o mesmo <code className="rounded bg-muted px-1 text-xs">legal_one_update_id</code>{" "}
            mas textos divergentes. Útil para identificar atualizações parciais ou registros corrompidos.
          </CardDescription>
        </CardHeader>

        {showDuplicates && (
          <CardContent className="pt-0">
            {loadingDuplicates ? (
              <div className="flex h-24 items-center justify-center gap-2 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Carregando divergências...
              </div>
            ) : !duplicates || duplicates.divergences.length === 0 ? (
              <div className="rounded-md border bg-muted/30 p-6 text-center text-sm text-muted-foreground">
                Nenhum par duplicado com texto divergente encontrado. ✓
              </div>
            ) : (
              <div className="space-y-3">
                <p className="text-xs text-muted-foreground">
                  Mostrando {duplicates.divergences.length} de {duplicates.total} par{duplicates.total !== 1 ? "es" : ""}.
                </p>
                <ScrollArea className="max-h-[480px] rounded-md border">
                  <div className="divide-y">
                    {duplicates.divergences.map((pair) => (
                      <div key={pair.legal_one_update_id} className="p-4">
                        <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
                          <Badge variant="outline" className="font-mono text-[10px]">
                            update_id: {pair.legal_one_update_id}
                          </Badge>
                          <span>{formatDate(pair.original.publication_date)}</span>
                          <Badge variant={statusColor(pair.original.status)} className="text-[10px]">
                            original: {pair.original.status}
                          </Badge>
                          <Badge variant={statusColor(pair.duplicate.status)} className="text-[10px]">
                            duplicado: {pair.duplicate.status}
                          </Badge>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <p className="mb-1 text-[11px] font-medium text-muted-foreground">
                              Original (ID {pair.original.id})
                            </p>
                            <p className="rounded bg-muted/50 p-2 text-xs leading-relaxed">
                              {pair.original.description_preview}
                            </p>
                          </div>
                          <div>
                            <p className="mb-1 text-[11px] font-medium text-yellow-600">
                              Duplicado (ID {pair.duplicate.id})
                            </p>
                            <p className="rounded bg-yellow-50 border border-yellow-200 p-2 text-xs leading-relaxed">
                              {pair.duplicate.description_preview}
                            </p>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </ScrollArea>
              </div>
            )}
          </CardContent>
        )}
      </Card>

      {/* Schedule Dialog */}
      <Dialog
        open={scheduleOpen}
        onOpenChange={(next) => {
          setScheduleOpen(next);
          // Ao fechar (X, Esc, click-fora, Cancelar), limpa estados locais
          // do modal pra nao vazar pra proxima abertura. Redundante com o
          // reset feito em openScheduleDialog, mas defesa em profundidade.
          if (!next) {
            setRemovedTaskIndices(new Set());
            setDuplicatesBySubtype({});
            setDuplicateCheckFailed(false);
          }
        }}
      >
        {/* max-w-4xl (~896px) em vez de 2xl (~672px): com o SubtypePicker
            mostrando "Tipo · Subtipo" no trigger, labels do catálogo L1
            (ex.: "BB Execução e Encerramento · Cumprimento de Sentença -
            BB Execução e Encerramento") quebravam horizontalmente em 2xl. */}
        <DialogContent className="flex max-h-[90vh] max-w-4xl flex-col gap-0 p-0">
          {/* ── Header fixo ── */}
          <div className="border-b px-6 py-4">
            <DialogTitle className="flex items-center gap-2 text-base font-semibold">
              <Send className="h-4 w-4 text-primary" />
              Confirmar Agendamento
            </DialogTitle>
            <DialogDescription className="mt-1 text-sm">
              {scheduleGroup?.lawsuit_cnj
                ? <>Processo <span className="font-medium text-foreground">{scheduleGroup.lawsuit_cnj}</span></>
                : scheduleGroup?.lawsuit_id
                ? <>Processo <span className="font-medium text-foreground">#{scheduleGroup.lawsuit_id}</span></>
                : "Publicações sem processo vinculado"
              }
              {" · "}{scheduleGroup?.records.length ?? 0} publicação(ões)
            </DialogDescription>
          </div>

          {/* ── Corpo scrollável ──
              overflow-x-hidden mata a scrollbar horizontal que aparecia
              quando algum filho (combobox de subtipo, badge longo, etc.)
              estourava a largura. min-w-0 protege de novo. */}
          <div className="relative flex-1 min-w-0 overflow-y-auto overflow-x-hidden px-6 py-4">
            {/* Overlay bloqueante enquanto consulta o L1. Sem isso o usuário
                podia apertar Enviar antes da checagem terminar e causar
                agendamento duplicado. */}
            {scheduleOpen && scheduleGroup?.lawsuit_id && duplicateCheckLoading && (
              <div className="absolute inset-0 z-40 flex flex-col items-center justify-center gap-3 bg-background/85 backdrop-blur-sm">
                <div className="rounded-full bg-primary/10 p-4">
                  <Loader2 className="h-8 w-8 animate-spin text-primary" />
                </div>
                <div className="space-y-1 text-center">
                  <p className="text-sm font-semibold text-foreground">
                    Verificando tarefas pendentes no Legal One...
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Aguarde — isso evita agendamento duplicado do mesmo subtipo.
                  </p>
                </div>
              </div>
            )}
            {scheduleGroup && (
              <div className="space-y-5">

                {/* Texto da publicação */}
                <div>
                  <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Texto da publicação
                  </p>
                  <div className="max-h-48 overflow-y-auto rounded-md border bg-muted/20 p-3">
                    {scheduleGroup.records.map((r, ri) => (
                      <div key={r.id} className={ri > 0 ? "mt-3 border-t pt-3" : ""}>
                        {scheduleGroup.records.length > 1 && (
                          <p className="mb-1 text-[10px] font-medium text-muted-foreground">
                            Publicação {ri + 1} — {r.description_preview || r.legal_one_update_id}
                          </p>
                        )}
                        <p className="whitespace-pre-wrap text-xs leading-relaxed text-foreground/80">
                          {r.description || r.description_preview || "Sem texto disponível."}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Publicações incluídas */}
                <div>
                  <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Publicações incluídas
                  </p>
                  <div className="space-y-1 rounded-md border bg-muted/30 p-2">
                    {scheduleGroup.records.map((r) => (
                      <div key={r.id} className="flex items-center gap-2 text-xs">
                        <Badge variant={statusColor(r.status)} className="shrink-0">{r.status}</Badge>
                        <span className="min-w-0 flex-1 truncate text-muted-foreground">
                          {r.description_preview || r.legal_one_update_id}
                        </span>
                        <span className="shrink-0 tabular-nums text-muted-foreground">
                          {formatDateShort(r.publication_date)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>

                <Separator />

                {/* Aviso de audiência */}
                {(() => {
                  const audRec = scheduleGroup.records.find((r) => r.audiencia_data);
                  if (!audRec) return null;
                  return (
                    <div className="flex items-start gap-3 rounded-md border border-violet-200 bg-violet-50 p-3">
                      <Calendar className="mt-0.5 h-4 w-4 shrink-0 text-violet-600" />
                      <div>
                        <p className="text-sm font-semibold text-violet-800">Audiência identificada</p>
                        <p className="text-xs text-violet-700">
                          Data e horário extraídos automaticamente. Verifique antes de confirmar.
                        </p>
                      </div>
                    </div>
                  );
                })()}

                {/* Tarefas propostas */}
                {editedPayloads.length > 0 ? (
                  <div className="space-y-4">
                    <p className="flex items-center gap-1.5 text-sm font-medium text-emerald-700">
                      <span className="flex h-4 w-4 items-center justify-center rounded-full bg-emerald-100 text-[10px] font-bold text-emerald-700">✓</span>
                      {editedPayloads.length} tarefa(s) a enviar — revise e confirme
                    </p>

                    {/* Aviso se o check-duplicates falhou (overlay some, mas
                        o usuário precisa saber que não temos certeza sobre
                        duplicatas). */}
                    {scheduleGroup?.lawsuit_id && !duplicateCheckLoading && duplicateCheckFailed && (
                      <div className="mb-3 flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 p-2.5 text-xs text-amber-900">
                        <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-600" />
                        <div>
                          <p className="font-semibold">Não foi possível verificar duplicatas no Legal One</p>
                          <p className="mt-0.5 text-[11px] text-amber-800">
                            Pode ser instabilidade temporária. Prossiga com cautela ou feche e reabra o modal.
                          </p>
                        </div>
                      </div>
                    )}

                    {editedPayloads.map((payload, idx) => {
                      const isRemoved = removedTaskIndices.has(idx);
                      return (
                        <div
                          key={idx}
                          className={`rounded-lg border bg-background shadow-sm transition-opacity ${
                            isRemoved ? "opacity-40" : ""
                          }`}
                        >
                          {/* Cabeçalho do bloco */}
                          <div className="flex items-center justify-between rounded-t-lg border-b bg-muted/40 px-4 py-2">
                            <span className="flex items-center gap-2 text-xs font-semibold text-muted-foreground">
                              Tarefa {idx + 1}
                              {payload.is_custom ? (
                                <Badge variant="outline" className="border-amber-300 bg-amber-50 text-[10px] font-medium text-amber-700">
                                  Avulsa
                                </Badge>
                              ) : payload.template_name ? (
                                <span className="font-normal text-muted-foreground/70">
                                  · {payload.template_name}
                                </span>
                              ) : null}
                            </span>
                            <Button
                              variant="ghost"
                              size="sm"
                              className={`h-6 px-2 text-xs ${isRemoved ? "text-emerald-600" : "text-destructive hover:text-destructive"}`}
                              onClick={() => {
                                const next = new Set(removedTaskIndices);
                                if (isRemoved) next.delete(idx); else next.add(idx);
                                setRemovedTaskIndices(next);
                              }}
                            >
                              {isRemoved ? "Restaurar" : "Remover"}
                            </Button>
                          </div>

                          {/* Banner de duplicata (Onda 1): exibido quando o
                              check-duplicates encontrou tasks pendentes no L1
                              com mesmo subTypeId + mesmo processo.
                              Layout forte vermelho/laranja pra destacar e
                              interromper o fluxo natural do operador. */}
                          {!isRemoved && payload.subTypeId && (duplicatesBySubtype[payload.subTypeId]?.length ?? 0) > 0 && (() => {
                            const dups = duplicatesBySubtype[payload.subTypeId!] ?? [];
                            return (
                              <div className="mx-4 mt-3 overflow-hidden rounded-lg border-2 border-red-400 bg-gradient-to-br from-red-50 to-orange-50 shadow-sm">
                                {/* Faixa superior com contador grande */}
                                <div className="flex items-center gap-3 border-b-2 border-red-300 bg-red-100 px-4 py-2.5">
                                  <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full bg-red-500 text-white shadow">
                                    <AlertCircle className="h-5 w-5" />
                                  </div>
                                  <div className="flex-1">
                                    <p className="text-sm font-bold text-red-800">
                                      {dups.length === 1
                                        ? "Tarefa duplicada detectada"
                                        : `${dups.length} tarefas duplicadas detectadas`}
                                    </p>
                                    <p className="text-[11px] text-red-700">
                                      Já existe{dups.length > 1 ? "m" : ""} tarefa{dups.length > 1 ? "s" : ""} em aberto no Legal One com este subtipo para este processo.
                                    </p>
                                  </div>
                                </div>
                                {/* Lista de tasks existentes */}
                                <ul className="divide-y divide-red-200 px-4 py-2">
                                  {dups.map((d: any) => (
                                    <li key={d.task_id} className="flex items-center gap-3 py-2">
                                      <div className="min-w-0 flex-1">
                                        <div className="flex items-center gap-2">
                                          <span className="rounded bg-red-200 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-red-800">
                                            {d.status_label}
                                          </span>
                                          <span className="text-xs font-semibold text-red-900">
                                            Tarefa #{d.task_id}
                                          </span>
                                        </div>
                                        {d.description && (
                                          <p className="mt-1 truncate text-xs text-red-800/90" title={d.description}>
                                            {d.description}
                                          </p>
                                        )}
                                      </div>
                                      {d.l1_url && (
                                        <a
                                          href={d.l1_url}
                                          target="_blank"
                                          rel="noopener noreferrer"
                                          className="inline-flex flex-shrink-0 items-center gap-1 rounded-md border border-red-500 bg-white px-3 py-1.5 text-xs font-semibold text-red-700 shadow-sm transition-colors hover:bg-red-500 hover:text-white"
                                        >
                                          Ver no L1
                                          <ExternalLink className="h-3.5 w-3.5" />
                                        </a>
                                      )}
                                    </li>
                                  ))}
                                </ul>
                                {/* Rodapé com ação sugerida */}
                                <div className="flex items-center justify-between gap-2 border-t border-red-200 bg-red-50/80 px-4 py-2">
                                  <p className="text-[11px] text-red-800">
                                    Recomendado: remova esta tarefa da lista.
                                  </p>
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    className="h-7 border-red-400 bg-white text-xs font-semibold text-red-700 hover:bg-red-500 hover:text-white"
                                    onClick={() => {
                                      const next = new Set(removedTaskIndices);
                                      next.add(idx);
                                      setRemovedTaskIndices(next);
                                    }}
                                  >
                                    Remover tarefa
                                  </Button>
                                </div>
                              </div>
                            );
                          })()}

                          {/* Campos do bloco */}
                          {!isRemoved && (() => {
                            // Subtipo selecionado e tipo associado (lookup nas metas)
                            const currentSubId = payload.subTypeId ?? null;
                            const parentType = taskTypes.find((t) =>
                              t.subtypes.some((s) => s.external_id === currentSubId)
                            ) || null;
                            // Responsável atual = primeiro participant com isResponsible (ou suggested)
                            const currentResp = (payload.participants || []).find(
                              (p: any) => p?.isResponsible
                            );
                            const currentRespId: number | null =
                              currentResp?.contact?.id ?? null;
                            return (
                              <div className="space-y-3 p-4">
                                <div className="grid gap-1.5">
                                  <Label className="text-xs font-medium">Descrição *</Label>
                                  <Textarea
                                    rows={2}
                                    className="resize-none text-sm"
                                    value={payload.description ?? ""}
                                    onChange={(e) => {
                                      const next = [...editedPayloads];
                                      next[idx] = { ...next[idx], description: e.target.value };
                                      setEditedPayloads(next);
                                    }}
                                  />
                                  <p className="text-[10px] text-muted-foreground">
                                    Máx. 250 caracteres ({(payload.description ?? "").length}/250)
                                  </p>
                                </div>

                                {/* Subtipo de tarefa — combobox com busca (antes era Select de
                                    ~900 itens, impossivel de rolar). Cadastramos o rótulo
                                    "Tipo · Subtipo" no `value` do CommandItem pra que a busca
                                    nativa do shadcn case tanto pelo nome do subtipo quanto
                                    pelo tipo pai. Digitar "BB" retorna subtipos de BB; digitar
                                    "publicação" retorna todos os subtipos de Publicação. */}
                                <SubtypePicker
                                  value={currentSubId}
                                  parentType={parentType}
                                  taskTypes={taskTypes}
                                  onChange={(newSubId, newType) => {
                                    const next = [...editedPayloads];
                                    next[idx] = {
                                      ...next[idx],
                                      subTypeId: newSubId,
                                      typeId: newType?.external_id ?? next[idx].typeId,
                                    };
                                    setEditedPayloads(next);
                                  }}
                                />
                                {parentType && (
                                  <p className="text-[10px] text-muted-foreground -mt-1">
                                    Tipo: {parentType.name}
                                  </p>
                                )}

                                {/* Responsável */}
                                <div className="grid gap-1.5">
                                  <Label className="text-xs font-medium">Responsável *</Label>
                                  <UserSelector
                                    users={appUsersForPicker}
                                    value={currentRespId ? String(currentRespId) : null}
                                    onChange={(v) => {
                                      const userId = v ? parseInt(v, 10) : null;
                                      const next = [...editedPayloads];
                                      next[idx] = {
                                        ...next[idx],
                                        participants: userId
                                          ? [{
                                              contact: { id: userId },
                                              isResponsible: true,
                                              isExecuter: true,
                                              isRequester: true,
                                            }]
                                          : [],
                                      };
                                      setEditedPayloads(next);
                                      setTouchedResponsible((prev) => new Set(prev).add(idx));
                                    }}
                                    placeholder="Selecione um usuário"
                                    showEmail
                                  />
                                  {payload.suggested_responsible &&
                                    payload.suggested_responsible.id !== currentRespId && (
                                      <button
                                        type="button"
                                        className="self-start text-[10px] text-blue-700 underline hover:text-blue-900"
                                        onClick={() => {
                                          const next = [...editedPayloads];
                                          next[idx] = {
                                            ...next[idx],
                                            participants: [{
                                              contact: { id: payload.suggested_responsible!.id },
                                              isResponsible: true,
                                              isExecuter: true,
                                              isRequester: true,
                                            }],
                                          };
                                          setEditedPayloads(next);
                                          setTouchedResponsible((prev) => new Set(prev).add(idx));
                                        }}
                                      >
                                        Usar sugerido: {payload.suggested_responsible.name}
                                      </button>
                                    )}
                                  {/* Aviso de roteamento de squad — o openScheduleDialog
                                      ja' chamou /resolve-target/claim e substituiu
                                      participants[0] pelo assistente. O UserSelector
                                      acima mostra DIRETAMENTE o assistente que vai
                                      receber a tarefa. Esse badge so' confirma o motivo
                                      (template marcado como assistente / squad suporte)
                                      e avisa que trocar manualmente desliga a regra.
                                      Se operador trocou (current != original do
                                      template = lider), badge some — operador na ponta
                                      vence. */}
                                  {(() => {
                                    const tr = (payload as any).target_role || "principal";
                                    const tsq = (payload as any).target_squad_id || null;
                                    if (tr !== "assistente" && !tsq) return null;
                                    const orig = originalResponsibleByIndex.get(idx) ?? null;
                                    // Se current === original (lider do template), significa
                                    // que o /claim no openScheduleDialog FALHOU (squad sem
                                    // assistente, rede off, etc.) — operador esta vendo o
                                    // lider, badge avisa por que.
                                    // Se current !== original, foi auto-resolvido pra
                                    // assistente OU operador trocou. Badge so' aparece se
                                    // foi auto-resolvido (heurística: operador teria sido
                                    // notificado por toast em qualquer outro caso).
                                    const autoResolved =
                                      currentRespId != null &&
                                      orig != null &&
                                      currentRespId !== orig;
                                    const label = tr === "assistente" ? "assistente" : "líder";
                                    const squadHint = tsq
                                      ? " da squad de suporte configurada"
                                      : " da squad principal";
                                    if (autoResolved) {
                                      return (
                                        <div className="rounded border border-blue-200 bg-blue-50 px-2 py-1 text-[10px] text-blue-900 mt-1">
                                          🤖 Atribuído automaticamente ao{" "}
                                          <strong>{label}</strong>{squadHint} (template
                                          marca rota assistente). Trocar manualmente
                                          desliga a regra.
                                        </div>
                                      );
                                    }
                                    // current === original (não foi resolvido). Avisa que
                                    // a regra do template não foi aplicada e como arrumar.
                                    return (
                                      <div className="rounded border border-amber-200 bg-amber-50 px-2 py-1 text-[10px] text-amber-900 mt-1">
                                        ⚠ Template marca rota <strong>{label}</strong>
                                        {squadHint}, mas o assistente não pôde ser
                                        resolvido (squad sem cadastro ou líder fora dela).
                                        A tarefa vai pro líder. Cadastre a squad em
                                        /admin/squads ou troque manualmente.
                                      </div>
                                    );
                                  })()}
                                </div>

                                {/* Escritório responsável */}
                                <div className="grid gap-1.5">
                                  <Label className="text-xs font-medium">Escritório responsável</Label>
                                  <Select
                                    value={
                                      payload.responsibleOfficeId
                                        ? String(payload.responsibleOfficeId)
                                        : "_none"
                                    }
                                    onValueChange={(v) => {
                                      const next = [...editedPayloads];
                                      next[idx] = {
                                        ...next[idx],
                                        responsibleOfficeId:
                                          v === "_none" ? null : parseInt(v, 10),
                                      };
                                      setEditedPayloads(next);
                                    }}
                                  >
                                    <SelectTrigger className="text-sm">
                                      <SelectValue placeholder="—" />
                                    </SelectTrigger>
                                    <SelectContent className="max-h-72">
                                      <SelectItem value="_none">— Não definir —</SelectItem>
                                      {offices.map((o) => (
                                        <SelectItem
                                          key={o.external_id}
                                          value={String(o.external_id)}
                                        >
                                          {officeLabel(o)}
                                        </SelectItem>
                                      ))}
                                    </SelectContent>
                                  </Select>
                                </div>

                                <div className="grid grid-cols-12 gap-3">
                                  <div className="col-span-5 grid gap-1.5">
                                    <Label className="text-xs font-medium">
                                      {scheduleGroup.records.some((r) => r.audiencia_data)
                                        ? "Data da Audiência *"
                                        : "Prazo / Data"}
                                    </Label>
                                    <Input
                                      type="date"
                                      className="text-sm"
                                      value={brtDateFromIso(payload.endDateTime)}
                                      onChange={(e) => {
                                        const newDate = e.target.value; // BRT
                                        const currentTime = brtTimeFromIso(payload.endDateTime) || "23:59";
                                        const iso = brtToUtcIso(newDate, `${currentTime}:00`);
                                        const next = [...editedPayloads];
                                        next[idx] = { ...next[idx], endDateTime: iso, startDateTime: iso };
                                        setEditedPayloads(next);
                                      }}
                                    />
                                  </div>
                                  <div className="col-span-3 grid gap-1.5">
                                    <Label className="text-xs font-medium">Horário</Label>
                                    <Input
                                      type="time"
                                      step={60}
                                      className="text-sm"
                                      value={brtTimeFromIso(payload.endDateTime)}
                                      onChange={(e) => {
                                        const newTime = e.target.value || "23:59"; // BRT
                                        const currentDate =
                                          brtDateFromIso(payload.endDateTime) ||
                                          brtDateFromIso(new Date().toISOString());
                                        const iso = brtToUtcIso(currentDate, `${newTime}:00`);
                                        const next = [...editedPayloads];
                                        next[idx] = { ...next[idx], endDateTime: iso, startDateTime: iso };
                                        setEditedPayloads(next);
                                      }}
                                    />
                                  </div>
                                  <div className="col-span-4 grid gap-1.5">
                                    <Label className="text-xs font-medium">Prioridade</Label>
                                    <Select
                                      value={payload.priority ?? "Normal"}
                                      onValueChange={(v) => {
                                        const next = [...editedPayloads];
                                        next[idx] = { ...next[idx], priority: v };
                                        setEditedPayloads(next);
                                      }}
                                    >
                                      <SelectTrigger className="text-sm"><SelectValue /></SelectTrigger>
                                      <SelectContent>
                                        <SelectItem value="Low">Baixa</SelectItem>
                                        <SelectItem value="Normal">Normal</SelectItem>
                                        <SelectItem value="High">Alta</SelectItem>
                                      </SelectContent>
                                    </Select>
                                  </div>
                                </div>
                                {scheduleGroup.records.some((r) => r.audiencia_data) && (
                                  <p className="text-[10px] text-violet-700">
                                    Audiência detectada — confirme o horário extraído da publicação.
                                  </p>
                                )}

                                <div className="grid gap-1.5">
                                  <Label className="text-xs font-medium">Observações</Label>
                                  <Textarea
                                    rows={3}
                                    className="resize-none text-sm"
                                    placeholder="Opcional"
                                    value={payload.notes ?? ""}
                                    onChange={(e) => {
                                      const next = [...editedPayloads];
                                      next[idx] = { ...next[idx], notes: e.target.value || null };
                                      setEditedPayloads(next);
                                    }}
                                  />
                                </div>
                              </div>
                            );
                          })()}
                        </div>
                      );
                    })}

                    {/* Botão para adicionar tarefa avulsa extra */}
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="w-full border-dashed"
                      onClick={handleAddCustomTask}
                    >
                      <Plus className="mr-2 h-4 w-4" />
                      Adicionar tarefa avulsa
                    </Button>
                  </div>
                ) : (
                  <div className="space-y-3">
                    <Alert>
                      <AlertCircle className="h-4 w-4" />
                      <AlertTitle>Sem template configurado</AlertTitle>
                      <AlertDescription>
                        Não há template para a classificação deste processo.{" "}
                        <Link to="/publications/templates" className="font-medium underline">
                          Configurar templates
                        </Link>{" "}
                        para habilitar o agendamento automático — ou adicione uma tarefa avulsa abaixo.
                      </AlertDescription>
                    </Alert>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="w-full border-dashed"
                      onClick={handleAddCustomTask}
                    >
                      <Plus className="mr-2 h-4 w-4" />
                      Adicionar tarefa avulsa
                    </Button>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* ── Footer fixo ── */}
          <div className="flex items-center justify-end gap-3 border-t bg-background px-6 py-4">
            <Button variant="outline" onClick={() => setScheduleOpen(false)} disabled={scheduling}>
              Cancelar
            </Button>
            <Button
              onClick={handleConfirmSchedule}
              disabled={
                scheduling
                || editedPayloads.length === 0
                || removedTaskIndices.size === editedPayloads.length
                || duplicateCheckLoading
              }
              title={duplicateCheckLoading ? "Aguardando verificação de duplicatas no L1..." : undefined}
            >
              {scheduling || duplicateCheckLoading
                ? <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                : <Send className="mr-2 h-4 w-4" />}
              {duplicateCheckLoading
                ? "Verificando..."
                : `Enviar ${editedPayloads.length - removedTaskIndices.size} tarefa(s)`}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Save Filter Dialog */}
      <Dialog open={isSaveFilterDialogOpen} onOpenChange={setIsSaveFilterDialogOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Salvar Filtros Atuais</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label htmlFor="filter-name">Nome do Filtro</Label>
              <Input
                id="filter-name"
                value={filterName}
                onChange={(e) => setFilterName(e.target.value)}
                placeholder="Ex: Novos do Escritório SP"
              />
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="is-default"
                checked={isFilterDefault}
                onCheckedChange={(c) => setIsFilterDefault(!!c)}
              />
              <Label htmlFor="is-default" className="font-normal cursor-pointer">
                Marcar como filtro padrão
              </Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setIsSaveFilterDialogOpen(false)}>
              Cancelar
            </Button>
            <Button onClick={handleSaveFilter} disabled={isSavingFilter}>
              {isSavingFilter && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Salvar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ─── Dialog: Feedback explícito (thumbs-down) ─────────────── */}
      <Dialog open={feedbackOpen} onOpenChange={setFeedbackOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <MessageSquareWarning className="h-5 w-5 text-red-500" />
              Reportar classificação errada
            </DialogTitle>
            <DialogDescription>
              Corrija a classificação e, opcionalmente, adicione uma nota. O classificador aprenderá com esse feedback.
            </DialogDescription>
          </DialogHeader>

          {feedbackRecord && (
            <div className="space-y-4">
              {/* Classificação atual (errada) */}
              <div className="rounded-md border border-red-200 bg-red-50 p-3">
                <p className="text-xs font-medium text-red-700 mb-1">Classificação atual</p>
                <p className="text-sm font-semibold">
                  {feedbackRecord.category || "—"}
                  {feedbackRecord.subcategory && feedbackRecord.subcategory !== "-"
                    ? ` → ${feedbackRecord.subcategory}`
                    : ""}
                </p>
                {feedbackRecord.polo && (
                  <p className="text-xs text-red-600">Polo: {feedbackRecord.polo}</p>
                )}
              </div>

              {/* Tipo de erro */}
              <div className="space-y-1">
                <Label className="text-xs">O que estava errado?</Label>
                <Select value={feedbackErrorType} onValueChange={setFeedbackErrorType}>
                  <SelectTrigger className="h-8 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="category">Categoria</SelectItem>
                    <SelectItem value="subcategory">Subcategoria</SelectItem>
                    <SelectItem value="polo">Polo</SelectItem>
                    <SelectItem value="natureza">Natureza do processo</SelectItem>
                    <SelectItem value="multiple">Vários campos</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {/* Classificação correta */}
              <div className="space-y-1">
                <Label className="text-xs">Classificação correta</Label>
                <Select
                  value={feedbackSubcategory ? `${feedbackCategory}|||${feedbackSubcategory}` : feedbackCategory}
                  onValueChange={(v) => {
                    const [cat, sub] = v.split("|||");
                    setFeedbackCategory(cat);
                    setFeedbackSubcategory(sub || "");
                  }}
                >
                  <SelectTrigger className="h-8 text-xs">
                    <SelectValue placeholder="Selecione a classificação correta" />
                  </SelectTrigger>
                  <SelectContent className="max-h-80">
                    {Object.entries(taxonomy).map(([cat, subs]) => (
                      <div key={cat}>
                        {subs && subs.length > 0 ? (
                          <>
                            <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                              {cat}
                            </div>
                            {subs.map((sub) => (
                              <SelectItem key={`${cat}|||${sub}`} value={`${cat}|||${sub}`}>
                                {sub}
                              </SelectItem>
                            ))}
                          </>
                        ) : (
                          <SelectItem value={cat}>{cat}</SelectItem>
                        )}
                      </div>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Polo correto */}
              <div className="space-y-1">
                <Label className="text-xs">Polo correto</Label>
                <Select value={feedbackPolo} onValueChange={setFeedbackPolo}>
                  <SelectTrigger className="h-8 text-xs">
                    <SelectValue placeholder="Selecione o polo" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="ativo">Ativo</SelectItem>
                    <SelectItem value="passivo">Passivo</SelectItem>
                    <SelectItem value="ambos">Ambos</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {/* Natureza (só para sem processo) */}
              {!feedbackRecord.linked_lawsuit_id && (
                <div className="space-y-1">
                  <Label className="text-xs">Natureza do processo</Label>
                  <Input
                    className="h-8 text-xs"
                    placeholder="Ex.: Embargos à Execução, Agravo de Instrumento..."
                    value={feedbackNatureza}
                    onChange={(e) => setFeedbackNatureza(e.target.value)}
                  />
                </div>
              )}

              {/* Nota do operador */}
              <div className="space-y-1">
                <Label className="text-xs">Nota / regra (opcional)</Label>
                <Textarea
                  className="text-xs min-h-[60px]"
                  placeholder="Ex.: 'Quando menciona embargante, sempre é Embargos à Execução'"
                  value={feedbackNote}
                  onChange={(e) => setFeedbackNote(e.target.value)}
                />
                <p className="text-[10px] text-muted-foreground">
                  Dica: descreva uma regra geral para que o classificador aprenda com essa correção.
                </p>
              </div>
            </div>
          )}

          <DialogFooter>
            <Button variant="secondary" onClick={() => setFeedbackOpen(false)} disabled={submittingFeedback}>
              Cancelar
            </Button>
            <Button onClick={handleSubmitFeedback} disabled={submittingFeedback}>
              {submittingFeedback && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Enviar feedback
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default PublicationsPage;
