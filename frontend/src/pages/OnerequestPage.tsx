// frontend/src/pages/OnerequestPage.tsx
//
// OneRequest — painel de tratamento das DMIs (demandas diversas de assessoria)
// do Banco do Brasil. Capturadas por motor RPA externo, tratadas aqui e
// agendadas no Legal One. Acesso pela permissão dedicada can_use_onerequest.
//
// Abas: Novas · Vence Hoje · Todas · Busca/Auditoria. Sugestão de
// setor/responsável/data (motor parametrizado), processo clicável -> L1,
// tarefas pendentes/concluídas na pasta, e log de anotações por DMI.

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import UserSelector, { SelectableUser } from "@/components/ui/UserSelector";
import { useToast } from "@/hooks/use-toast";
import { useAuth } from "@/hooks/useAuth";
import { getGraphTokenForTeams, warmupTeamsAuth } from "@/lib/teams-graph";
import {
  AlertTriangle,
  Bell,
  CalendarCheck,
  CalendarClock,
  CalendarDays,
  CheckCircle2,
  ClipboardCopy,
  Clock,
  Download,
  ExternalLink,
  FileSearch,
  Inbox,
  Lightbulb,
  Loader2,
  type LucideIcon,
  ChevronsUpDown,
  Pause,
  Pencil,
  Play,
  RefreshCw,
  Search,
  UserCheck,
  X,
} from "lucide-react";
import {
  addAnotacao,
  agendarSolicitacao,
  downloadSolicitacoesExcel,
  AlertaResponsavel,
  Anotacao,
  Auditoria,
  enviarAlertaTeams,
  Estado,
  Farol,
  FormUser,
  getAlertasVenceHoje,
  getAuditoria,
  getEstado,
  getFormUsers,
  getL1Autorefresh,
  getL1Tarefas,
  getOptions,
  getSugestao,
  L1Autorefresh,
  L1Tarefas,
  setL1Autorefresh,
  ListParams,
  listAnotacoes,
  listSolicitacoes,
  OnerequestSolicitacao,
  StatusL1,
  Sugestao,
  updateTratamento,
  verificarProcessoL1,
  verificarStatusL1,
} from "@/services/onerequest";

const PAGE_SIZES = [25, 50, 100];

const FAROL_DOT: Record<Farol, string> = {
  cinza: "bg-slate-400", // sem prazo
  atrasado: "bg-rose-700", // vencida (prazo já passou)
  vermelho: "bg-red-500", // vence hoje
  amarelo: "bg-amber-400",
  roxo: "bg-purple-500",
  verde: "bg-emerald-500",
};

const KPI_DEFS: {
  key: string;
  label: string;
  farol: Farol;
  icon: LucideIcon;
  chip: string;
}[] = [
  { key: "atrasadas", label: "Atrasadas", farol: "atrasado", icon: AlertTriangle, chip: "bg-rose-100 text-rose-700" },
  { key: "hoje", label: "Vence hoje", farol: "vermelho", icon: CalendarClock, chip: "bg-red-100 text-red-600" },
  { key: "amanha", label: "Amanhã", farol: "amarelo", icon: Clock, chip: "bg-amber-100 text-amber-700" },
  { key: "fds", label: "Fim de semana", farol: "roxo", icon: CalendarDays, chip: "bg-purple-100 text-purple-700" },
  { key: "futuras", label: "Futuras", farol: "verde", icon: CalendarCheck, chip: "bg-emerald-100 text-emerald-700" },
];

type TabKey = "novas" | "atrasadas" | "hoje" | "todas" | "concluidas";

const TABS: { key: TabKey; label: string }[] = [
  { key: "novas", label: "Novas (sem responsável)" },
  { key: "atrasadas", label: "Atrasadas" },
  { key: "hoje", label: "Vence Hoje" },
  { key: "todas", label: "Todas (abertas)" },
  { key: "concluidas", label: "Concluídas" },
];

// Desfecho das DMIs concluídas: BB respondeu vs operador encerrou sem providência.
const DESFECHO_BADGE: Record<string, { label: string; cls: string }> = {
  respondida: { label: "Respondida", cls: "bg-emerald-100 text-emerald-800 border-emerald-200" },
  arquivada: { label: "Arquivada", cls: "bg-slate-100 text-slate-700 border-slate-200" },
};

const STATUS_TRAT_LABEL: Record<string, string> = {
  NOVO: "Novas",
  AGENDADO: "Agendadas",
  IGNORADO: "Sem providência",
  ERRO: "Com erro",
  AGUARDANDO_PROCESSO: "Aguardando processo",
};

// Chip de filtro ativo (drill-through do dashboard) — removível.
function FilterChip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border bg-muted/50 px-2.5 py-1 text-xs">
      {label}
      <button
        type="button"
        onClick={onClear}
        className="text-muted-foreground hover:text-foreground"
        aria-label="Remover filtro"
      >
        <X className="h-3 w-3" />
      </button>
    </span>
  );
}

// Status derivado do estado da DMI (não só do status_tratamento bruto):
// sem responsável = Nova; com responsável = Distribuída; com tarefa = Agendada.
function StatusBadge({ sol }: { sol: OnerequestSolicitacao }) {
  if (sol.created_task_id || sol.status_tratamento === "AGENDADO")
    return <Badge className="bg-emerald-600 hover:bg-emerald-600">Agendada</Badge>;
  if (sol.status_tratamento === "AGUARDANDO_PROCESSO")
    return <Badge className="bg-amber-500 hover:bg-amber-500">Aguardando processo</Badge>;
  if (sol.status_tratamento === "ERRO")
    return <Badge variant="destructive">Erro</Badge>;
  if (sol.status_tratamento === "IGNORADO")
    return <Badge variant="outline">Sem providência</Badge>;
  // RPA capturou o número mas ainda não detalhou (robô 2): título/NPJ/prazo vazios.
  if (!sol.titulo)
    return <Badge variant="outline" className="border-slate-300 text-slate-500">Aguardando detalhe</Badge>;
  if (sol.responsavel_user_id)
    return <Badge className="bg-sky-600 hover:bg-sky-600">Distribuída</Badge>;
  return <Badge variant="secondary">Nova</Badge>;
}

// Aplica o resultado da checagem no L1 (StatusL1) sobre os campos cacheados da linha.
function aplicarStatusL1(it: OnerequestSolicitacao, r: StatusL1): OnerequestSolicitacao {
  return {
    ...it,
    l1_checked_at: r.checked_at,
    l1_dmi_task_id: r.dmi_task_id,
    l1_dmi_status_id: r.dmi_status_id,
    l1_dmi_status_label: r.dmi_status_label,
    l1_dmi_respondida: r.dmi_respondida,
    l1_dmi_encontrada: r.dmi_encontrada,
    l1_pendentes_count: r.pendentes_count,
    l1_sem_pendencia: r.sem_pendencia,
    l1_task_url: r.dmi_task_url,
    linked_lawsuit_id: r.lawsuit_id ?? it.linked_lawsuit_id,
  };
}

// Coluna "Status L1": (A) a tarefa da DMI foi respondida (Cumprida)? e
// (B) a pasta tem pendência? Lê do cache da linha (preenchido sob demanda).
function StatusL1Cell({ sol }: { sol: OnerequestSolicitacao }) {
  if (!sol.l1_checked_at)
    return <span className="text-xs text-muted-foreground">não checado</span>;
  if (!sol.linked_lawsuit_id)
    return (
      <Badge variant="outline" className="border-slate-300 text-slate-500">
        pasta não localizada
      </Badge>
    );

  // (A) tarefa da DMI
  let badgeA: JSX.Element;
  if (!sol.l1_dmi_encontrada) {
    badgeA = (
      <Badge variant="outline" className="border-slate-300 text-slate-500">
        tarefa não localizada
      </Badge>
    );
  } else if (sol.l1_dmi_respondida) {
    badgeA = <Badge className="bg-emerald-600 hover:bg-emerald-600">Respondida (Cumprido)</Badge>;
  } else if (sol.l1_dmi_status_id === 2) {
    badgeA = <Badge variant="destructive">{sol.l1_dmi_status_label ?? "Não cumprido"}</Badge>;
  } else {
    badgeA = (
      <Badge className="bg-amber-500 hover:bg-amber-500">
        {sol.l1_dmi_status_label ?? "Pendente"}
      </Badge>
    );
  }
  const cellA = sol.l1_task_url ? (
    <a
      href={sol.l1_task_url}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex"
      title="Abrir a tarefa da DMI no Legal One"
    >
      {badgeA}
    </a>
  ) : (
    badgeA
  );

  // (B) pendências na pasta
  let badgeB: JSX.Element | null = null;
  if (sol.l1_sem_pendencia === true)
    badgeB = (
      <Badge variant="outline" className="border-emerald-300 text-emerald-700">
        pasta sem pendência
      </Badge>
    );
  else if (sol.l1_sem_pendencia === false)
    badgeB = (
      <Badge variant="outline" className="border-amber-300 text-amber-700">
        {sol.l1_pendentes_count} pendente(s) na pasta
      </Badge>
    );

  return (
    <div className="flex flex-col items-start gap-1">
      {cellA}
      {badgeB}
      <span className="text-[10px] text-muted-foreground">
        checado {fmtDateTime(sol.l1_checked_at)}
      </span>
    </div>
  );
}

function fmtDateTime(value: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (isNaN(d.getTime())) return value;
  return d.toLocaleString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// Converte entre o formato do <input type=date> (YYYY-MM-DD) e o do backend (DD/MM/YYYY).
function toISODate(s: string | null): string {
  const t = (s || "").trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(t)) return t;
  const m = t.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  return m ? `${m[3]}-${m[2]}-${m[1]}` : "";
}

function toBRDate(iso: string): string {
  const m = (iso || "").match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : iso;
}

// Quantos dias o prazo BB (DD/MM/YYYY) já passou em relação a hoje. 0 se futuro/hoje/inválido.
function diasAtraso(prazo: string | null): number {
  const iso = toISODate(prazo);
  if (!iso) return 0;
  const [y, m, d] = iso.split("-").map(Number);
  const due = new Date(y, m - 1, d);
  const hoje = new Date();
  hoje.setHours(0, 0, 0, 0);
  const diff = Math.floor((hoje.getTime() - due.getTime()) / 86400000);
  return diff > 0 ? diff : 0;
}

// recebido_em (ISO com hora) -> DD/MM/YYYY (data que a DMI chegou/foi disponibilizada).
function fmtDataBR(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleDateString("pt-BR");
}

function ingestInfo(iso: string | null): { texto: string; tone: "ok" | "warn" | "danger" } {
  if (!iso) return { texto: "Nenhuma ingestão de dados registrada ainda.", tone: "danger" };
  const d = new Date(iso);
  if (isNaN(d.getTime())) return { texto: "Data de ingestão inválida.", tone: "danger" };
  const mins = Math.floor((Date.now() - d.getTime()) / 60000);
  let rel: string;
  if (mins < 1) rel = "agora há pouco";
  else if (mins < 60) rel = `há ${mins} min`;
  else if (mins < 1440) rel = `há ${Math.floor(mins / 60)}h`;
  else rel = `há ${Math.floor(mins / 1440)} dia(s)`;
  // robô 1 roda de hora em hora; passou de ~2h é amarelo, >6h é alerta.
  const tone = mins <= 120 ? "ok" : mins <= 360 ? "warn" : "danger";
  const abs = d.toLocaleString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
  return { texto: `Última ingestão de dados: ${abs} · ${rel}`, tone };
}

export default function OnerequestPage() {
  const { toast } = useToast();
  const { user } = useAuth();
  const [tab, setTab] = useState<TabKey>("novas");
  const [items, setItems] = useState<OnerequestSolicitacao[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [estado, setEstado] = useState<Estado | null>(null);
  const [autoref, setAutoref] = useState<L1Autorefresh | null>(null);
  const [togglingAuto, setTogglingAuto] = useState(false);

  const [buscaInput, setBuscaInput] = useState("");
  const [busca, setBusca] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  const [users, setUsers] = useState<FormUser[]>([]);
  const [setores, setSetores] = useState<string[]>([]);

  // Modal de tratamento + sub-dados
  const [selected, setSelected] = useState<OnerequestSolicitacao | null>(null);
  const [editResponsavelExt, setEditResponsavelExt] = useState<string | null>(null);
  const [editSetor, setEditSetor] = useState("");
  const [editData, setEditData] = useState("");
  const [saving, setSaving] = useState(false);
  const [scheduling, setScheduling] = useState(false);
  const [sugestao, setSugestao] = useState<Sugestao | null>(null);
  const [sugerido, setSugerido] = useState(false);
  const [l1, setL1] = useState<L1Tarefas | null>(null);
  const [l1Loading, setL1Loading] = useState(false);
  const [anotacoes, setAnotacoes] = useState<Anotacao[]>([]);
  const [novaAnotacao, setNovaAnotacao] = useState("");
  const [resolvingL1Id, setResolvingL1Id] = useState<number | null>(null);

  // "Atualizar status L1": checa em lote (client-loop) as DMIs da página.
  const [checkingL1, setCheckingL1] = useState(false);
  const [l1Progress, setL1Progress] = useState({ done: 0, total: 0 });

  // "Verificar no L1": checa em lote se o processo de cada DMI da página existe.
  const [checkingProc, setCheckingProc] = useState(false);
  const [procProgress, setProcProgress] = useState({ done: 0, total: 0 });
  // Filtro "Fora do L1" (processo checado e não encontrado).
  const [soForaDoL1, setSoForaDoL1] = useState(false);

  // Filtro da aba Atrasadas: só as SEM anotação (precisam de ação).
  const [soSemAnotacao, setSoSemAnotacao] = useState(false);

  // Recortes de data (ISO YYYY-MM-DD) — disponibilização (recebido_em) e prazo
  // fatal (BB). Compõem com a aba/filtros e alimentam listagem E exportação.
  const [dispDe, setDispDe] = useState("");
  const [dispAte, setDispAte] = useState("");
  const [prazoDe, setPrazoDe] = useState("");
  const [prazoAte, setPrazoAte] = useState("");
  const [exporting, setExporting] = useState(false);

  // Filtro por farol vindo do clique nos KPI cards (toggle). Os cards são
  // GLOBAIS (todas as abertas), então o clique também muda a base pra
  // "Todas (abertas)" — senão o farol combinaria com a aba atual (ex.:
  // "Novas" ∩ atrasadas = vazio) e a tabela não bateria com o número do card.
  const [farolFilter, setFarolFilter] = useState<Farol | null>(null);

  // Drill-through vindo do dashboard (via URL): setor, responsável e status de
  // tratamento. Aparecem como chips removíveis e compõem com a aba.
  const [setorFilter, setSetorFilter] = useState<string | null>(null);
  const [responsavelFilter, setResponsavelFilter] = useState<number | null>(null);
  const [statusTratFilter, setStatusTratFilter] = useState<string | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();

  // Modal de Acompanhamento/Auditoria (DMIs já agendadas).
  const [auditSel, setAuditSel] = useState<OnerequestSolicitacao | null>(null);
  const [auditData, setAuditData] = useState<Auditoria | null>(null);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditNovaAnotacao, setAuditNovaAnotacao] = useState("");
  const [auditSavingAnot, setAuditSavingAnot] = useState(false);

  // Modal de Mensagens de Alerta (vence hoje, agrupadas por responsável).
  const [alertasOpen, setAlertasOpen] = useState(false);
  const [alertas, setAlertas] = useState<AlertaResponsavel[]>([]);
  const [alertasLoading, setAlertasLoading] = useState(false);
  const [enviandoTeams, setEnviandoTeams] = useState<number | null>(null);

  // Modal de Auditoria de processo/DMI — busca cross-status (todas as DMIs de um processo).
  const [auditoriaOpen, setAuditoriaOpen] = useState(false);
  const [auditoriaTermo, setAuditoriaTermo] = useState("");
  const [auditoriaResultados, setAuditoriaResultados] = useState<OnerequestSolicitacao[]>([]);
  const [auditoriaBuscando, setAuditoriaBuscando] = useState(false);
  const [auditoriaBuscou, setAuditoriaBuscou] = useState(false);

  // Monta os filtros a partir da aba + recortes de data. SEM paginação —
  // usado tanto pela listagem (que adiciona limit/offset) quanto pela
  // exportação (que exporta tudo). Garante que os dois "conversam".
  const buildParams = useCallback((): ListParams => {
    const params: ListParams = {
      busca: busca || undefined,
    };
    if (dispDe) params.disp_de = dispDe;
    if (dispAte) params.disp_ate = dispAte;
    if (prazoDe) params.prazo_de = prazoDe;
    if (prazoAte) params.prazo_ate = prazoAte;
    if (tab === "novas") {
      params.status_sistema = "ABERTO";
      params.sem_responsavel = true; // "novas" = ainda sem responsável (não distribuídas)
    } else if (tab === "atrasadas") {
      params.status_sistema = "ABERTO";
      params.farol = "atrasado"; // prazo BB já venceu
      if (soSemAnotacao) params.sem_anotacao = true; // só as que ainda precisam de ação
    } else if (tab === "hoje") {
      params.status_sistema = "ABERTO";
      params.farol = "vermelho";
    } else if (tab === "todas") {
      params.status_sistema = "ABERTO";
    } else if (tab === "concluidas") {
      params.concluidas = true; // BB respondeu OU operador encerrou sem providência
    }
    // Clique num KPI card sobrepõe o farol, mantendo a base da aba atual.
    if (farolFilter) params.farol = farolFilter;
    // Drill-through do dashboard (setor / responsável / status de tratamento).
    if (setorFilter) params.setor = setorFilter;
    if (responsavelFilter) params.responsavel_user_id = responsavelFilter;
    if (statusTratFilter) params.status_tratamento = statusTratFilter;
    if (soForaDoL1) params.sem_processo_l1 = true;
    return params;
  }, [tab, busca, soSemAnotacao, dispDe, dispAte, prazoDe, prazoAte, farolFilter, setorFilter, responsavelFilter, statusTratFilter, soForaDoL1]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params: ListParams = {
        ...buildParams(),
        limit: pageSize,
        offset: (page - 1) * pageSize,
      };
      const resp = await listSolicitacoes(params);
      setItems(resp.items);
      setTotal(resp.total);
      // KPI cards são estáticos: farol global vem do getEstado (não da lista filtrada).
      getEstado().then(setEstado).catch(() => {});
    } catch (e) {
      toast({ title: "Erro ao carregar", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [buildParams, page, pageSize, toast]);

  const exportarExcel = async () => {
    setExporting(true);
    try {
      await downloadSolicitacoesExcel(buildParams());
      toast({
        title: "Exportação iniciada",
        description: "O Excel com os filtros atuais está sendo baixado.",
      });
    } catch (e) {
      toast({ title: "Erro ao exportar", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setExporting(false);
    }
  };

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    getOptions().then((o) => setSetores(o.setores)).catch(() => {});
    getFormUsers().then(setUsers).catch(() => {});
    getL1Autorefresh().then(setAutoref).catch(() => {});
    warmupTeamsAuth(); // deixa o MSAL pronto → popup do Teams abre sem atraso
  }, []);

  // Drill-through do dashboard: lê os filtros da URL uma vez, aplica como chips
  // e limpa a URL pra não "grudar" o filtro num refresh.
  useEffect(() => {
    const t = searchParams.get("tab");
    const f = searchParams.get("farol");
    const s = searchParams.get("setor");
    const r = searchParams.get("responsavel");
    const st = searchParams.get("status_tratamento");
    let mudou = false;
    if (t && TABS.some((x) => x.key === t)) { setTab(t as TabKey); mudou = true; }
    if (f) { setFarolFilter(f as Farol); mudou = true; }
    if (s) { setSetorFilter(s); mudou = true; }
    if (r) { setResponsavelFilter(Number(r)); mudou = true; }
    if (st) { setStatusTratFilter(st); mudou = true; }
    if (mudou) { setPage(1); setSearchParams({}, { replace: true }); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleAutorefresh = async () => {
    if (!autoref) return;
    setTogglingAuto(true);
    try {
      const novo = await setL1Autorefresh(!autoref.enabled);
      setAutoref(novo);
      toast({
        title: novo.enabled ? "Auto-atualização ligada" : "Auto-atualização parada",
        description: novo.enabled
          ? "O status L1 das DMIs que vencem hoje será atualizado de hora em hora (já disparei uma agora)."
          : "As DMIs que vencem hoje não serão mais atualizadas automaticamente.",
      });
    } catch (e) {
      toast({ title: "Erro", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setTogglingAuto(false);
    }
  };

  const selectableUsers: SelectableUser[] = useMemo(
    () => users.map((u) => ({ id: u.id, external_id: u.external_id, name: u.name, squads: u.squads ?? [], email: u.email ?? null })),
    [users],
  );

  const responsavelNome = (id: number) => users.find((u) => u.id === id)?.name ?? `#${id}`;

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const firstRow = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const lastRow = Math.min(total, page * pageSize);

  const trocarTab = (t: TabKey) => {
    setPage(1);
    setSoSemAnotacao(false);
    setFarolFilter(null);
    setSetorFilter(null);
    setResponsavelFilter(null);
    setStatusTratFilter(null);
    setTab(t);
  };

  // ── Acompanhamento/Auditoria (DMIs já agendadas) ─────────────────────
  const isAgendada = (sol: OnerequestSolicitacao) =>
    sol.status_tratamento === "AGENDADO" || sol.created_task_id != null;

  const carregarAuditoria = (id: number) => {
    setAuditLoading(true);
    getAuditoria(id)
      .then(setAuditData)
      .catch((e) => toast({ title: "Erro", description: String((e as Error).message), variant: "destructive" }))
      .finally(() => setAuditLoading(false));
  };

  const abrirAuditoria = (sol: OnerequestSolicitacao) => {
    setAuditSel(sol);
    setAuditData(null);
    setAuditNovaAnotacao("");
    carregarAuditoria(sol.id);
  };

  // Auditoria de processo/DMI: busca SEM filtro de status — encontra a DMI em
  // qualquer situação e mostra todas as vinculadas ao mesmo processo.
  const buscarAuditoria = async () => {
    const termo = auditoriaTermo.trim();
    if (!termo) return;
    setAuditoriaBuscando(true);
    setAuditoriaBuscou(true);
    try {
      const resp = await listSolicitacoes({ busca: termo, limit: 200 });
      setAuditoriaResultados(resp.items);
    } catch (e) {
      toast({ title: "Erro na auditoria", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setAuditoriaBuscando(false);
    }
  };

  const salvarAuditAnotacao = async () => {
    if (!auditSel || !auditNovaAnotacao.trim()) return;
    setAuditSavingAnot(true);
    try {
      await addAnotacao(auditSel.id, auditNovaAnotacao.trim());
      setAuditNovaAnotacao("");
      carregarAuditoria(auditSel.id); // recarrega anotações
      load(); // atualiza o badge "anotada" na tabela
    } catch (e) {
      toast({ title: "Erro", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setAuditSavingAnot(false);
    }
  };

  // ── Mensagens de alerta (vence hoje, por responsável) ────────────────
  const abrirAlertas = async () => {
    setAlertasOpen(true);
    setAlertasLoading(true);
    try {
      setAlertas(await getAlertasVenceHoje());
    } catch (e) {
      toast({ title: "Erro ao gerar alertas", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setAlertasLoading(false);
    }
  };

  const copiarMensagem = async (texto: string) => {
    try {
      await navigator.clipboard.writeText(texto);
      toast({ title: "Copiado", description: "Mensagem na área de transferência." });
    } catch {
      toast({ title: "Não consegui copiar", description: "Selecione e copie manualmente.", variant: "destructive" });
    }
  };

  // Troca INLINE de responsável na tabela (combobox no clique — o operador
  // ajusta direto, sem abrir o Tratar).
  const [respEditFor, setRespEditFor] = useState<number | null>(null);
  const [respSaving, setRespSaving] = useState<number | null>(null);

  const trocarResponsavel = async (sol: OnerequestSolicitacao, u: FormUser | null) => {
    setRespSaving(sol.id);
    setRespEditFor(null);
    try {
      await updateTratamento(sol.id, { responsavel_user_id: u ? u.id : null });
      // Atualiza a linha localmente (sem reload da página inteira).
      setItems((prev) =>
        prev.map((it) =>
          it.id === sol.id
            ? { ...it, responsavel_user_id: u ? u.id : null, responsavel_nome: u ? u.name : null }
            : it,
        ),
      );
      toast({
        title: u ? "Responsável atualizado" : "Responsável removido",
        description: u ? `${sol.numero_solicitacao} → ${u.name}` : sol.numero_solicitacao,
      });
    } catch (e) {
      toast({ title: "Erro ao trocar responsável", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setRespSaving(null);
    }
  };

  // Picker "Alerta Teams": pra quem notificar (multiselect; default = o
  // responsável principal do grupo, quando endereçável).
  const [teamsPickerFor, setTeamsPickerFor] = useState<string | null>(null);
  const [teamsSel, setTeamsSel] = useState<Set<number>>(new Set());

  const grupoKey = (g: AlertaResponsavel) => String(g.responsavel_user_id ?? g.responsavel_nome);

  const abrirTeamsPicker = (g: AlertaResponsavel) => {
    const k = grupoKey(g);
    if (teamsPickerFor === k) {
      setTeamsPickerFor(null);
      return;
    }
    // Default: o responsável principal já marcado (se dá pra mandar pra ele).
    setTeamsSel(new Set(g.responsavel_user_id && g.teams_disponivel ? [g.responsavel_user_id] : []));
    setTeamsPickerFor(k);
  };

  const teamsEnderecavel = (g: AlertaResponsavel, email?: string | null) => {
    const dom = (g.teams_email_domain || "").toLowerCase();
    return !!dom && !!email && email.trim().toLowerCase().endsWith("@" + dom);
  };

  const enviarTeams = async (g: AlertaResponsavel, destinatarios: number[]) => {
    if (!destinatarios.length) return;
    setEnviandoTeams(g.responsavel_user_id ?? -1);
    try {
      // Token do Graph no nome da operadora logada (MSAL, silencioso quando possível).
      const token = await getGraphTokenForTeams(user?.email ?? "");
      const r = await enviarAlertaTeams(g.responsavel_user_id, token, destinatarios);
      toast({
        title: r.ok ? "Enviado no Teams" : r.resultados?.some((x) => x.ok) ? "Enviado parcialmente" : "Não enviado",
        description: r.mensagem,
        variant: r.ok ? undefined : "destructive",
      });
      if (r.resultados?.some((x) => x.ok)) setTeamsPickerFor(null);
    } catch (e) {
      toast({
        title: "Erro no Teams",
        description: String((e as Error).message),
        variant: "destructive",
      });
    } finally {
      setEnviandoTeams(null);
    }
  };

  const aplicarBusca = () => {
    setPage(1);
    setBusca(buscaInput.trim());
  };

  const resolveResponsavelId = (): number | null => {
    if (!editResponsavelExt) return null;
    const u = users.find((x) => String(x.external_id) === editResponsavelExt);
    return u ? u.id : null;
  };

  const openModal = (sol: OnerequestSolicitacao) => {
    setSelected(sol);
    const u = users.find((x) => x.id === sol.responsavel_user_id);
    setEditResponsavelExt(u ? String(u.external_id) : null);
    setEditSetor(sol.setor ?? "");
    setEditData(toISODate(sol.data_agendamento));
    setSugestao(null);
    setSugerido(false);
    setL1(null);
    setAnotacoes([]);
    setNovaAnotacao("");

    // Sugestão: pré-preenche SÓ os campos ainda VAZIOS de DMI sem tarefa criada.
    // Se a DMI já tem responsável/setor/data indicados (pelo operador ou pela
    // fonte), o campo mostra o valor ATUAL — a sugestão vira só informativa.
    // (Antes a sugestão sobrescrevia o campo e o modal divergia da tabela.)
    getSugestao(sol.id)
      .then((s) => {
        setSugestao(s);
        const naoAgendada = !sol.created_task_id;
        if (naoAgendada) {
          let aplicou = false;
          // "N/A" não é setor válido no Select — deixa vazio pro operador escolher.
          if (s.setor && s.setor !== "N/A" && !sol.setor) {
            setEditSetor(s.setor);
            aplicou = true;
          }
          if (s.responsavel_user_id && !sol.responsavel_user_id) {
            const su = users.find((x) => x.id === s.responsavel_user_id);
            if (su) {
              setEditResponsavelExt(String(su.external_id));
              aplicou = true;
            }
          }
          if (s.data_agendamento && !sol.data_agendamento) {
            setEditData(toISODate(s.data_agendamento));
            aplicou = true;
          }
          setSugerido(aplicou);
        }
      })
      .catch(() => {});

    // Tarefas na pasta + URL do processo no L1.
    setL1Loading(true);
    getL1Tarefas(sol.id)
      .then(setL1)
      .catch(() => {})
      .finally(() => setL1Loading(false));

    listAnotacoes(sol.id).then(setAnotacoes).catch(() => {});
  };

  const closeModal = () => setSelected(null);

  // Clicar no nº do processo na tabela -> resolve e abre no L1.
  const abrirProcessoNoL1 = async (sol: OnerequestSolicitacao) => {
    if (!sol.proc_utilizavel && !sol.npj_direcionador) {
      toast({ title: "Sem processo", description: "Esta DMI não tem CNJ nem NPJ pra resolver.", variant: "destructive" });
      return;
    }
    setResolvingL1Id(sol.id);
    try {
      const r = await getL1Tarefas(sol.id);
      if (r.l1_url) window.open(r.l1_url, "_blank", "noopener");
      else toast({ title: "Processo não encontrado no L1", variant: "destructive" });
    } catch (e) {
      toast({ title: "Erro ao abrir no L1", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setResolvingL1Id(null);
    }
  };

  // Checa o status no L1 das DMIs da página atual (só as com processo/NPJ),
  // em paralelo limitado, atualizando os badges conforme cada uma resolve.
  const atualizarStatusL1Pagina = async () => {
    const alvo = items.filter((s) => s.proc_utilizavel || s.npj_direcionador);
    if (alvo.length === 0) {
      toast({ title: "Nada para checar", description: "Nenhuma DMI com CNJ/NPJ nesta página." });
      return;
    }
    setCheckingL1(true);
    setL1Progress({ done: 0, total: alvo.length });
    const fila = [...alvo];
    let done = 0;
    const CONC = 4;
    const worker = async () => {
      while (fila.length) {
        const sol = fila.shift();
        if (!sol) break;
        try {
          const r = await verificarStatusL1(sol.id);
          setItems((prev) => prev.map((it) => (it.id === sol.id ? aplicarStatusL1(it, r) : it)));
        } catch {
          /* mantém a linha como estava; segue pras demais */
        } finally {
          done += 1;
          setL1Progress({ done, total: alvo.length });
        }
      }
    };
    try {
      await Promise.all(Array.from({ length: Math.min(CONC, alvo.length) }, worker));
      toast({ title: "Status L1 atualizado", description: `${done} DMI(s) checada(s) no Legal One.` });
    } finally {
      setCheckingL1(false);
    }
  };

  // Verifica em lote se o PROCESSO de cada DMI da página existe no L1 (sem agendar).
  const verificarProcessoL1Pagina = async () => {
    const alvo = items.filter((s) => s.proc_utilizavel || s.npj_direcionador);
    if (alvo.length === 0) {
      toast({ title: "Nada para verificar", description: "Nenhuma DMI com CNJ/NPJ nesta página." });
      return;
    }
    setCheckingProc(true);
    setProcProgress({ done: 0, total: alvo.length });
    const fila = [...alvo];
    let done = 0;
    let achados = 0;
    const CONC = 4;
    const worker = async () => {
      while (fila.length) {
        const sol = fila.shift();
        if (!sol) break;
        try {
          const r = await verificarProcessoL1(sol.id);
          if (r.encontrado) achados += 1;
          setItems((prev) =>
            prev.map((it) =>
              it.id === sol.id
                ? { ...it, proc_l1_encontrado: r.encontrado, proc_l1_via: r.via, proc_l1_checado_em: new Date().toISOString() }
                : it,
            ),
          );
        } catch {
          /* mantém a linha; segue */
        } finally {
          done += 1;
          setProcProgress({ done, total: alvo.length });
        }
      }
    };
    try {
      await Promise.all(Array.from({ length: Math.min(CONC, alvo.length) }, worker));
      toast({
        title: "Verificação no L1 concluída",
        description: `${done} checada(s) · ${achados} no L1 · ${done - achados} fora.`,
      });
    } finally {
      setCheckingProc(false);
    }
  };

  const saveTratamento = async (): Promise<boolean> => {
    if (!selected) return false;
    setSaving(true);
    try {
      await updateTratamento(selected.id, {
        responsavel_user_id: resolveResponsavelId(),
        setor: editSetor || null,
        data_agendamento: editData ? toBRDate(editData) : null,
      });
      toast({ title: "Tratamento salvo." });
      await load();
      return true;
    } catch (e) {
      toast({ title: "Erro ao salvar", description: String((e as Error).message), variant: "destructive" });
      return false;
    } finally {
      setSaving(false);
    }
  };

  const handleAgendar = async () => {
    if (!selected) return;
    const ok = await saveTratamento();
    if (!ok) return;
    setScheduling(true);
    try {
      let res = await agendarSolicitacao(selected.id);
      // Trava-duplo: já existe tarefa PENDENTE pra esta DMI → confirma e reenvia.
      if (res.requires_confirmation) {
        const det = res.tarefa_existente?.description
          ? `\n\nTarefa existente: ${res.tarefa_existente.description}`
          : "";
        if (window.confirm(`${res.mensagem}${det}`)) {
          res = await agendarSolicitacao(selected.id, true);
        } else {
          return; // operador cancelou — não cria 2ª tarefa
        }
      }
      toast({
        title: res.ok ? "Agendado no Legal One" : "Não agendado",
        description: res.mensagem,
        variant: res.ok ? undefined : "destructive",
      });
      if (res.ok) closeModal();
      await load();
    } catch (e) {
      toast({ title: "Erro ao agendar", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setScheduling(false);
    }
  };

  const handleIgnorar = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      await updateTratamento(selected.id, { status_tratamento: "IGNORADO" });
      toast({ title: "Marcada como sem providência." });
      closeModal();
      await load();
    } catch (e) {
      toast({ title: "Erro", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const handleAddAnotacao = async () => {
    if (!selected || !novaAnotacao.trim()) return;
    try {
      await addAnotacao(selected.id, novaAnotacao.trim());
      setNovaAnotacao("");
      const updated = await listAnotacoes(selected.id);
      setAnotacoes(updated);
    } catch (e) {
      toast({ title: "Erro ao anotar", description: String((e as Error).message), variant: "destructive" });
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Inbox className="h-6 w-6 shrink-0 text-primary" />
          <div>
            <h1 className="text-xl font-semibold">OneRequest — DMIs do Banco do Brasil</h1>
            <p className="text-sm text-muted-foreground">
              Capturadas do Portal Jurídico do BB. Direcione, agende no Legal One e acompanhe.
            </p>
          </div>
        </div>
        <Button variant="outline" className="shrink-0" onClick={() => setAuditoriaOpen(true)}>
          <FileSearch className="mr-2 h-4 w-4" />
          Auditoria de processo / DMI
        </Button>
      </div>

      {/* Barra de status condensada: ingestão (RPA heartbeat) + auto-atualização
          L1 + abertas. Neutra quando saudável; ganha cor só se a ingestão atrasa. */}
      {(() => {
        const info = ingestInfo(estado?.last_ingest_at ?? null);
        const bar =
          info.tone === "ok"
            ? "border bg-card"
            : info.tone === "warn"
              ? "border-amber-300 bg-amber-50"
              : "border-red-300 bg-red-50";
        const iconTone =
          info.tone === "ok"
            ? "text-emerald-600"
            : info.tone === "warn"
              ? "text-amber-600"
              : "text-red-600";
        const Icon = info.tone === "ok" ? CheckCircle2 : AlertTriangle;
        return (
          <div className={`flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg px-3 py-2 text-sm ${bar}`}>
            <div className="flex items-center gap-2">
              <Icon className={`h-4 w-4 shrink-0 ${iconTone}`} />
              <span className="font-medium">{info.texto}</span>
            </div>

            {autoref && (
              <>
                <span className="hidden h-4 w-px bg-border sm:block" />
                <div className="flex items-center gap-2">
                  <span
                    className={`h-2 w-2 shrink-0 rounded-full ${autoref.enabled ? "bg-emerald-500" : "bg-slate-400"}`}
                  />
                  <span className="text-muted-foreground">
                    Auto-atualização L1:{" "}
                    <span className={autoref.enabled ? "font-medium text-emerald-700" : "font-medium text-slate-600"}>
                      {autoref.enabled ? "ligada" : "parada"}
                    </span>
                    {autoref.last_run_at && (
                      <span className="hidden md:inline">
                        {" "}· última{" "}
                        {new Date(autoref.last_run_at).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}
                        {autoref.last_count != null && ` (${autoref.last_count})`}
                      </span>
                    )}
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 px-2"
                    onClick={toggleAutorefresh}
                    disabled={togglingAuto}
                    title="Liga/desliga a atualização automática (de hora em hora) do Status L1 das DMIs que vencem hoje"
                  >
                    {togglingAuto ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : autoref.enabled ? (
                      <Pause className="h-3.5 w-3.5" />
                    ) : (
                      <Play className="h-3.5 w-3.5" />
                    )}
                    <span className="ml-1">{autoref.enabled ? "Parar" : "Ligar"}</span>
                  </Button>
                </div>
              </>
            )}

            {estado && (
              <span className="ml-auto shrink-0 rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
                {estado.abertas} DMIs abertas
              </span>
            )}
          </div>
        );
      })()}

      {/* KPIs */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {/* Card ESTÁTICO: total de DMIs pendentes (abertas). Não clica/filtra —
            estilo distinto pra sinalizar que é o total, não um farol. */}
        <Card className="border-primary/30 bg-primary/5">
          <CardContent className="flex items-center gap-3 p-4">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/15 text-primary">
              <Inbox className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <div className="text-2xl font-bold leading-none tabular-nums">{estado?.abertas ?? 0}</div>
              <div className="mt-1 truncate text-xs text-muted-foreground">Pendentes</div>
            </div>
          </CardContent>
        </Card>
        {KPI_DEFS.map((kpi) => {
          const ativo = farolFilter === kpi.farol;
          const toggle = () => {
            setPage(1);
            if (farolFilter === kpi.farol) {
              setFarolFilter(null); // limpa o filtro, mantém a aba onde está
            } else {
              // O número do card é GLOBAL → a tabela precisa da base "Todas
              // (abertas)" pra listar exatamente aquelas DMIs.
              setTab("todas");
              setFarolFilter(kpi.farol);
            }
          };
          return (
            <Card
              key={kpi.key}
              role="button"
              tabIndex={0}
              onClick={toggle}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  toggle();
                }
              }}
              className={`cursor-pointer transition-all hover:-translate-y-0.5 hover:shadow-md ${
                ativo
                  ? "border-primary bg-primary/5 ring-2 ring-primary/60"
                  : "hover:border-foreground/20"
              }`}
              title={
                ativo
                  ? `Clique para limpar o filtro "${kpi.label}"`
                  : `Ver as DMIs: ${kpi.label.toLowerCase()}`
              }
            >
              <CardContent className="flex items-center gap-3 p-4">
                <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${kpi.chip}`}>
                  <kpi.icon className="h-5 w-5" />
                </span>
                <div className="min-w-0">
                  {/* Número ESTÁTICO: farol global das abertas (independe do filtro/aba).
                      O clique só filtra a TABELA (drill-through), sem mexer no número. */}
                  <div className="text-2xl font-bold leading-none tabular-nums">{estado?.kpis?.[kpi.key] ?? 0}</div>
                  <div className="mt-1 truncate text-xs text-muted-foreground">{kpi.label}</div>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* Chips de filtro ativo (drill-through do dashboard) */}
      {(setorFilter || responsavelFilter || statusTratFilter) && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">Filtrando:</span>
          {setorFilter && (
            <FilterChip label={`Setor: ${setorFilter}`} onClear={() => { setPage(1); setSetorFilter(null); }} />
          )}
          {responsavelFilter && (
            <FilterChip
              label={`Responsável: ${responsavelNome(responsavelFilter)}`}
              onClear={() => { setPage(1); setResponsavelFilter(null); }}
            />
          )}
          {statusTratFilter && (
            <FilterChip
              label={STATUS_TRAT_LABEL[statusTratFilter] ?? statusTratFilter}
              onClear={() => { setPage(1); setStatusTratFilter(null); }}
            />
          )}
        </div>
      )}

      {/* Abas + busca */}
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0 max-w-full overflow-x-auto">
          <Tabs value={tab} onValueChange={(v) => trocarTab(v as TabKey)}>
            <TabsList>
              {TABS.map((t) => (
                <TabsTrigger key={t.key} value={t.key}>
                  {t.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </div>
        <div className="flex flex-wrap gap-2">
          {tab === "hoje" && (
            <Button
              variant="default"
              onClick={abrirAlertas}
              title="Gera uma mensagem de alerta por responsável das DMIs que vencem hoje (pra copiar e mandar no Teams/WhatsApp)"
            >
              <Bell className="mr-2 h-4 w-4" />
              Gerar Mensagem de Alerta
            </Button>
          )}
          {tab === "atrasadas" && (
            <Button
              variant={soSemAnotacao ? "default" : "outline"}
              onClick={() => {
                setPage(1);
                setSoSemAnotacao((v) => !v);
              }}
              title="Mostra só as atrasadas SEM anotação (as que ainda precisam de ação)"
            >
              {soSemAnotacao ? "Só sem anotação ✓" : "Só sem anotação"}
            </Button>
          )}
          <div className="relative w-full lg:w-72">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="w-full pl-8"
              placeholder="Nº solicitação, processo ou título"
              value={buscaInput}
              onChange={(e) => setBuscaInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && aplicarBusca()}
            />
          </div>
          <Button variant="outline" onClick={() => load()} disabled={loading}>
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </Button>
          <Button
            variant="outline"
            onClick={atualizarStatusL1Pagina}
            disabled={checkingL1 || loading || items.length === 0}
            title="Checa no Legal One se a tarefa de cada DMI desta página foi respondida (Cumprida) e se a pasta tem pendência"
          >
            {checkingL1 ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                {l1Progress.done}/{l1Progress.total}
              </>
            ) : (
              <>
                <CheckCircle2 className="mr-2 h-4 w-4" />
                Status L1
              </>
            )}
          </Button>
          <Button
            variant="outline"
            onClick={verificarProcessoL1Pagina}
            disabled={checkingProc || loading || items.length === 0}
            title="Verifica no Legal One se o processo de cada DMI desta página existe (CNJ/NPJ) — sem criar tarefa"
          >
            {checkingProc ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                {procProgress.done}/{procProgress.total}
              </>
            ) : (
              <>
                <FileSearch className="mr-2 h-4 w-4" />
                Verificar L1
              </>
            )}
          </Button>
          <Button
            variant={soForaDoL1 ? "default" : "outline"}
            onClick={() => {
              setPage(1);
              setSoForaDoL1((v) => !v);
            }}
            title="Mostra só as DMIs cujo processo NÃO foi encontrado no Legal One"
          >
            <AlertTriangle className="mr-2 h-4 w-4" />
            {soForaDoL1 ? "Fora do L1 ✓" : "Fora do L1"}
          </Button>
        </div>
      </div>

      {/* Filtros de data + exportação — os dois recortes (disponibilização e
          prazo fatal) compõem com a aba/busca, e o export usa os MESMOS filtros. */}
      <div className="flex flex-col gap-3 rounded-lg border bg-muted/30 p-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Disponibilizada — de</Label>
            <Input
              type="date"
              className="h-9 w-[150px]"
              value={dispDe}
              max={dispAte || undefined}
              onChange={(e) => { setPage(1); setDispDe(e.target.value); }}
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">até</Label>
            <Input
              type="date"
              className="h-9 w-[150px]"
              value={dispAte}
              min={dispDe || undefined}
              onChange={(e) => { setPage(1); setDispAte(e.target.value); }}
            />
          </div>
          <div className="hidden w-px self-stretch bg-border sm:block" />
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">Prazo fatal — de</Label>
            <Input
              type="date"
              className="h-9 w-[150px]"
              value={prazoDe}
              max={prazoAte || undefined}
              onChange={(e) => { setPage(1); setPrazoDe(e.target.value); }}
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">até</Label>
            <Input
              type="date"
              className="h-9 w-[150px]"
              value={prazoAte}
              min={prazoDe || undefined}
              onChange={(e) => { setPage(1); setPrazoAte(e.target.value); }}
            />
          </div>
          {(dispDe || dispAte || prazoDe || prazoAte) && (
            <Button
              variant="ghost"
              size="sm"
              className="h-9"
              onClick={() => { setPage(1); setDispDe(""); setDispAte(""); setPrazoDe(""); setPrazoAte(""); }}
            >
              Limpar datas
            </Button>
          )}
        </div>
        <Button
          variant="outline"
          className="h-9 shrink-0"
          onClick={exportarExcel}
          disabled={exporting || loading}
          title="Exporta as DMIs com os filtros atuais (datas, aba e busca) para Excel"
        >
          {exporting ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Download className="mr-2 h-4 w-4" />
          )}
          Exportar Excel
        </Button>
      </div>

      {/* Tabela */}
      <Card>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-6"></TableHead>
                  <TableHead>Nº Solicitação</TableHead>
                  <TableHead className="min-w-[200px]">Título</TableHead>
                  <TableHead>Processo</TableHead>
                  <TableHead>Polo</TableHead>
                  <TableHead className="whitespace-nowrap">Disponibilizada</TableHead>
                  <TableHead>Prazo BB</TableHead>
                  <TableHead className="whitespace-nowrap">Agendada em</TableHead>
                  <TableHead>Responsável</TableHead>
                  <TableHead>Setor</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="min-w-[160px]">Status L1</TableHead>
                  <TableHead className="text-right">Ação</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading && items.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={13} className="py-10 text-center">
                      <Loader2 className="mx-auto h-6 w-6 animate-spin text-muted-foreground" />
                    </TableCell>
                  </TableRow>
                ) : items.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={13} className="py-10 text-center text-muted-foreground">
                      Nenhuma solicitação encontrada.
                    </TableCell>
                  </TableRow>
                ) : (
                  items.map((sol, idx) => (
                    <TableRow key={sol.id} className={idx % 2 === 1 ? "bg-muted/20" : undefined}>
                      <TableCell>
                        <span
                          className={`inline-block h-3 w-3 rounded-full ${FAROL_DOT[sol.farol]}`}
                          title={sol.prazo ? `Prazo BB: ${sol.prazo}` : "Sem prazo"}
                        />
                      </TableCell>
                      <TableCell className="whitespace-nowrap font-mono text-xs">
                        {sol.numero_solicitacao}
                      </TableCell>
                      <TableCell className="max-w-[280px] truncate text-sm" title={sol.titulo ?? ""}>
                        {sol.titulo ?? <span className="text-muted-foreground">—</span>}
                      </TableCell>
                      <TableCell className="text-xs">
                        {sol.proc_utilizavel || sol.npj_direcionador ? (
                          <button
                            type="button"
                            onClick={() => abrirProcessoNoL1(sol)}
                            className="inline-flex items-center gap-1 font-mono text-primary hover:underline"
                            title="Abrir processo no Legal One (resolve por CNJ ou NPJ)"
                          >
                            {resolvingL1Id === sol.id ? (
                              <Loader2 className="h-3 w-3 animate-spin" />
                            ) : (
                              <ExternalLink className="h-3 w-3" />
                            )}
                            {sol.proc_utilizavel ? sol.numero_processo : `NPJ ${sol.npj_direcionador}`}
                          </button>
                        ) : !sol.titulo ? (
                          <span className="text-muted-foreground">aguardando detalhe</span>
                        ) : (
                          <Badge variant="outline" className="border-amber-300 text-amber-700">
                            sem processo
                          </Badge>
                        )}
                        {sol.proc_l1_encontrado === false && (
                          <div
                            className="mt-0.5 flex w-fit items-center gap-1 rounded bg-red-100 px-1 py-0.5 text-[10px] font-medium text-red-700"
                            title="Verificado: processo não encontrado no Legal One (nem por CNJ nem por NPJ)"
                          >
                            <AlertTriangle className="h-3 w-3" /> fora do L1
                          </div>
                        )}
                        {sol.proc_l1_encontrado === true && (
                          <div
                            className="mt-0.5 flex w-fit items-center gap-1 text-[10px] text-emerald-700"
                            title={`Processo encontrado no L1${sol.proc_l1_via ? ` (por ${sol.proc_l1_via.toUpperCase()})` : ""}`}
                          >
                            <CheckCircle2 className="h-3 w-3" /> no L1
                          </div>
                        )}
                      </TableCell>
                      <TableCell className="text-xs">
                        {sol.polo ? (
                          <Badge variant="outline" className="capitalize">
                            {sol.polo}
                          </Badge>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell
                        className="whitespace-nowrap text-xs text-muted-foreground"
                        title={sol.recebido_em ? `Disponibilizada/recebida em ${sol.recebido_em}` : ""}
                      >
                        {fmtDataBR(sol.recebido_em)}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-sm">
                        {!sol.prazo ? (
                          "—"
                        ) : sol.farol === "atrasado" ? (
                          <span className="font-medium text-rose-700">
                            {sol.prazo}
                            <span className="block text-[10px] font-normal">
                              atrasada há {diasAtraso(sol.prazo)} dia(s)
                            </span>
                            <span
                              className={`mt-0.5 inline-block rounded px-1 py-0.5 text-[10px] font-medium ${
                                sol.tem_anotacao
                                  ? "bg-emerald-100 text-emerald-800"
                                  : "bg-amber-100 text-amber-800"
                              }`}
                              title={
                                sol.tem_anotacao
                                  ? "Tem anotação (ex.: atraso justificado, aguardando providência do cliente)"
                                  : "Sem anotação — atraso ainda não justificado"
                              }
                            >
                              {sol.tem_anotacao ? "✓ anotada" : "⚠ sem anotação"}
                            </span>
                          </span>
                        ) : (
                          sol.prazo
                        )}
                      </TableCell>
                      <TableCell
                        className="whitespace-nowrap text-xs text-muted-foreground"
                        title={
                          sol.scheduled_at
                            ? `Agendamento realizado em ${fmtDateTime(sol.scheduled_at)}${sol.data_agendamento ? ` · tarefa para ${toBRDate(sol.data_agendamento)}` : ""}${sol.scheduled_by_nome ? ` · por ${sol.scheduled_by_nome}` : ""}`
                            : "Ainda não agendada"
                        }
                      >
                        {sol.scheduled_at ? (
                          <span>
                            {fmtDataBR(sol.scheduled_at)}
                            {sol.data_agendamento && (
                              <span className="block text-[10px]">tarefa p/ {toBRDate(sol.data_agendamento)}</span>
                            )}
                          </span>
                        ) : sol.data_agendamento ? (
                          // DMI antiga (agendada fora do Flow): a fonte não registra
                          // QUANDO o agendamento foi feito — mostra a data da tarefa.
                          <span title="Agendada antes do Flow registrar o horário do ato — mostrando a data da tarefa agendada">
                            tarefa p/ {toBRDate(sol.data_agendamento)}
                          </span>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                      <TableCell className="text-sm">
                        {/* Dropdown de responsável (busca por nome) direto na listagem —
                            troca sem abrir o Tratar nem mexer no L1. */}
                        <Popover
                          open={respEditFor === sol.id}
                          onOpenChange={(o) => setRespEditFor(o ? sol.id : null)}
                        >
                          <PopoverTrigger asChild>
                            <button
                              type="button"
                              className="inline-flex w-full max-w-[190px] items-center justify-between gap-1 rounded-md border bg-background px-2 py-1 text-left text-xs shadow-sm transition-colors hover:bg-muted/60"
                              title="Trocar o responsável"
                              disabled={respSaving === sol.id}
                            >
                              {respSaving === sol.id ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                              ) : (
                                <span className={`truncate ${sol.responsavel_nome ? "" : "text-muted-foreground"}`}>
                                  {sol.responsavel_nome ?? "Atribuir…"}
                                </span>
                              )}
                              <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                            </button>
                          </PopoverTrigger>
                          <PopoverContent className="w-72 p-0" align="start">
                            <Command>
                              <CommandInput placeholder="Buscar por nome…" />
                              <CommandList className="max-h-56">
                                <CommandEmpty>Ninguém encontrado.</CommandEmpty>
                                <CommandGroup>
                                  {users.map((u) => (
                                    <CommandItem
                                      key={u.id}
                                      value={u.name}
                                      onSelect={() => trocarResponsavel(sol, u)}
                                    >
                                      <span className="min-w-0 flex-1 truncate">{u.name}</span>
                                      {sol.responsavel_user_id === u.id && (
                                        <CheckCircle2 className="ml-2 h-3.5 w-3.5 shrink-0 text-emerald-600" />
                                      )}
                                    </CommandItem>
                                  ))}
                                </CommandGroup>
                              </CommandList>
                            </Command>
                          </PopoverContent>
                        </Popover>
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-sm">{sol.setor ?? "—"}</TableCell>
                      <TableCell>
                        <StatusBadge sol={sol} />
                        {sol.desfecho && DESFECHO_BADGE[sol.desfecho] && (
                          <Badge
                            variant="outline"
                            className={`mt-1 block w-fit text-[10px] ${DESFECHO_BADGE[sol.desfecho].cls}`}
                          >
                            {DESFECHO_BADGE[sol.desfecho].label}
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell>
                        <StatusL1Cell sol={sol} />
                      </TableCell>
                      <TableCell className="text-right">
                        {isAgendada(sol) || tab === "concluidas" ? (
                          <Button size="sm" variant="ghost" onClick={() => abrirAuditoria(sol)}>
                            Acompanhar
                          </Button>
                        ) : (
                          <Button size="sm" variant="outline" onClick={() => openModal(sol)}>
                            Tratar
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>

          {/* Paginação */}
          <div className="flex flex-wrap items-center justify-between gap-3 border-t p-3 text-sm">
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground">Por página:</span>
              <Select value={String(pageSize)} onValueChange={(v) => { setPage(1); setPageSize(Number(v)); }}>
                <SelectTrigger className="w-20">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PAGE_SIZES.map((s) => (
                    <SelectItem key={s} value={String(s)}>{s}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="text-muted-foreground">
              {firstRow}–{lastRow} de {total} · Página {page} de {totalPages}
            </div>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1 || loading} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                Anterior
              </Button>
              <Button variant="outline" size="sm" disabled={page >= totalPages || loading} onClick={() => setPage((p) => Math.min(totalPages, p + 1))}>
                Próxima
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Modal de tratamento */}
      <Dialog open={!!selected} onOpenChange={(o) => !o && closeModal()}>
        <DialogContent className="max-h-[92vh] max-w-5xl overflow-y-auto overflow-x-hidden">
          {selected && (
            <>
              <DialogHeader>
                <DialogTitle className="font-mono text-base">DMI {selected.numero_solicitacao}</DialogTitle>
                <DialogDescription>{selected.titulo ?? "Sem título"}</DialogDescription>
              </DialogHeader>

              <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
                <div><span className="text-muted-foreground">NPJ:</span> {selected.npj_direcionador ?? "—"}</div>
                <div><span className="text-muted-foreground">Polo:</span> {selected.polo ?? "—"}</div>
                <div><span className="text-muted-foreground">Prazo BB:</span> {selected.prazo ?? "—"}</div>
                <div><span className="text-muted-foreground">Disponibilizada:</span> {fmtDataBR(selected.recebido_em)}</div>
                <div className="truncate"><span className="text-muted-foreground">Proc.:</span> {selected.numero_processo ?? "—"}</div>
              </div>

              {selected.texto_dmi && (
                <div>
                  <Label className="text-xs text-muted-foreground">Conteúdo da DMI</Label>
                  <div className="mt-1 max-h-[55vh] min-h-[8rem] overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted p-3 text-sm leading-relaxed">
                    {selected.texto_dmi}
                  </div>
                </div>
              )}

              {/* Painel Legal One: processo + tarefas na pasta */}
              <div className="rounded-md border p-3">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-sm font-medium">Processo no Legal One</span>
                  {l1?.l1_url && (
                    <a
                      href={l1.l1_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
                    >
                      <ExternalLink className="h-4 w-4" /> Abrir processo
                    </a>
                  )}
                </div>
                {l1Loading ? (
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" /> Consultando tarefas na pasta…
                  </div>
                ) : !l1 || !l1.resolvido ? (
                  <div className="text-sm text-muted-foreground">Processo não resolvido no L1 (sem CNJ utilizável).</div>
                ) : l1.check_failed ? (
                  <div className="flex items-center gap-2 text-sm text-amber-700">
                    <AlertTriangle className="h-4 w-4" /> Não foi possível consultar as tarefas agora.
                  </div>
                ) : (
                  <div className="space-y-2 text-sm">
                    <div>
                      <span className="font-medium text-red-600">Pendentes ({l1.pendentes.length})</span>
                      {l1.pendentes.length === 0 ? (
                        <span className="ml-2 text-muted-foreground">nenhuma</span>
                      ) : (
                        <ul className="mt-1 space-y-1">
                          {l1.pendentes.map((t) => (
                            <li key={t.task_id} className="flex min-w-0 items-start gap-2">
                              <Badge variant="secondary" className="shrink-0">{t.status_label}</Badge>
                              <a href={t.l1_url ?? "#"} target="_blank" rel="noopener noreferrer" className="min-w-0 break-words text-primary hover:underline">
                                {t.description || `Tarefa ${t.task_id}`}
                              </a>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                    {l1.concluidas.length > 0 && (
                      <div>
                        <span className="font-medium text-emerald-700">Concluídas (recentes)</span>
                        <ul className="mt-1 space-y-1 text-muted-foreground">
                          {l1.concluidas.slice(0, 5).map((t) => (
                            <li key={t.task_id} className="break-words">
                              {t.status_label} · {t.description || `Tarefa ${t.task_id}`}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Sugestão do motor */}
              {sugestao && (sugestao.setor || sugestao.responsavel_nome || sugestao.data_agendamento) && (
                <div className="flex items-start gap-2 rounded-md border border-sky-200 bg-sky-50 p-2 text-xs text-sky-900">
                  <Lightbulb className="mt-0.5 h-4 w-4 shrink-0" />
                  <div>
                    <span className="font-medium">
                      {sugerido ? "Sugestão pré-preenchida" : "Sugestão do motor (só informativa — o campo mostra o indicado atual)"}:
                    </span>{" "}
                    setor <b>{sugestao.setor}</b> ({sugestao.setor_confianca})
                    {sugestao.responsavel_nome && (
                      <>
                        {" "}· resp. <b>{sugestao.responsavel_nome}</b>
                        {sugestao.responsavel_regra === "trabalhista"
                          ? " (processo trabalhista — regra fixa)"
                          : sugestao.responsavel_confianca
                            ? ` (${sugestao.responsavel_confianca}%)`
                            : ""}
                      </>
                    )}
                    {sugestao.data_agendamento && <> · data <b>{sugestao.data_agendamento}</b></>}
                    . Confira e ajuste antes de agendar.
                  </div>
                </div>
              )}

              {/* Responsável da PASTA no L1 — informativo, com opção de 1 clique
                  (nunca pré-preenche sozinho). */}
              {sugestao?.pasta_responsavel_nome && (
                <div className="flex items-center gap-2 rounded-md border border-indigo-200 bg-indigo-50 p-2 text-xs text-indigo-900">
                  <UserCheck className="h-4 w-4 shrink-0" />
                  <div className="min-w-0 flex-1">
                    Responsável da <b>pasta</b> no Legal One: <b>{sugestao.pasta_responsavel_nome}</b>
                  </div>
                  {(() => {
                    const pu = sugestao.pasta_responsavel_user_id
                      ? users.find((x) => x.id === sugestao.pasta_responsavel_user_id)
                      : null;
                    return pu ? (
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-6 shrink-0 border-indigo-300 px-2 text-[11px] text-indigo-800 hover:bg-indigo-100"
                        onClick={() => setEditResponsavelExt(String(pu.external_id))}
                      >
                        Usar como responsável
                      </Button>
                    ) : (
                      <span className="shrink-0 text-[10px] text-indigo-700/70" title="Esse nome não está no catálogo de usuários do Flow">
                        fora do catálogo
                      </span>
                    );
                  })()}
                </div>
              )}

              {/* Form de tratamento */}
              <div className="grid gap-3">
                <div>
                  <Label className="text-xs">Responsável</Label>
                  <UserSelector users={selectableUsers} value={editResponsavelExt} onChange={setEditResponsavelExt} showEmail />
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div>
                    <Label className="text-xs">Setor</Label>
                    <Select value={editSetor} onValueChange={setEditSetor}>
                      <SelectTrigger><SelectValue placeholder="Selecione o setor" /></SelectTrigger>
                      <SelectContent>
                        {setores.map((s) => (
                          <SelectItem key={s} value={s}>{s}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <Label className="text-xs">Data de agendamento</Label>
                    <Input type="date" value={editData} onChange={(e) => setEditData(e.target.value)} />
                  </div>
                </div>
              </div>

              {/* Log de anotações (auditoria) */}
              <div className="rounded-md border p-3">
                <div className="mb-2 text-sm font-medium">Anotações (auditoria)</div>
                <div className="flex gap-2">
                  <Input
                    placeholder="Ex.: respondeu fora do prazo em 12/06…"
                    value={novaAnotacao}
                    onChange={(e) => setNovaAnotacao(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleAddAnotacao()}
                  />
                  <Button variant="secondary" onClick={handleAddAnotacao} disabled={!novaAnotacao.trim()}>
                    Anotar
                  </Button>
                </div>
                {anotacoes.length > 0 && (
                  <ul className="mt-2 max-h-32 space-y-1 overflow-auto text-xs">
                    {anotacoes.map((a) => (
                      <li key={a.id} className="border-b pb-1">
                        <span className="text-muted-foreground">{fmtDateTime(a.created_at)} · {a.autor_nome ?? "—"}:</span> {a.texto}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {selected.last_error && (
                <div className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 p-2 text-xs text-amber-800">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>{selected.last_error}</span>
                </div>
              )}

              <DialogFooter className="gap-2 sm:justify-between">
                <Button variant="ghost" onClick={handleIgnorar} disabled={saving || scheduling}>
                  Sem providência
                </Button>
                <div className="flex gap-2">
                  <Button variant="outline" onClick={saveTratamento} disabled={saving || scheduling}>
                    {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                    Salvar
                  </Button>
                  <Button onClick={handleAgendar} disabled={saving || scheduling}>
                    {scheduling ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CalendarCheck className="mr-2 h-4 w-4" />}
                    Agendar no Legal One
                  </Button>
                </div>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* Modal de Acompanhamento / Auditoria (DMIs já agendadas ou concluídas) */}
      <Dialog open={!!auditSel} onOpenChange={(o) => !o && setAuditSel(null)}>
        <DialogContent className="max-h-[92vh] max-w-4xl overflow-y-auto overflow-x-hidden">
          {auditSel && (
            <>
              <DialogHeader>
                <DialogTitle className="font-mono text-base">
                  Acompanhamento — DMI {auditSel.numero_solicitacao}
                </DialogTitle>
                <DialogDescription>{auditSel.titulo ?? "Sem título"}</DialogDescription>
              </DialogHeader>

              {auditLoading || !auditData ? (
                <div className="py-10 text-center">
                  <Loader2 className="mx-auto h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : (
                <div className="space-y-3 text-sm">
                  {/* Agendamento: quem / quando / pra quem / o quê */}
                  <div className="rounded-md border p-3">
                    <div className="mb-2 text-sm font-medium">Agendamento</div>
                    <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
                      <div>
                        <dt className="text-xs text-muted-foreground">Quem agendou</dt>
                        <dd>
                          {auditData.agendamento.scheduled_by_nome ?? "— (legado / fora do Flow)"}
                          {auditData.agendamento.scheduled_by_email && (
                            <span className="text-xs text-muted-foreground"> · {auditData.agendamento.scheduled_by_email}</span>
                          )}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-xs text-muted-foreground">Quando</dt>
                        <dd>{auditData.agendamento.scheduled_at ? fmtDateTime(auditData.agendamento.scheduled_at) : "—"}</dd>
                      </div>
                      <div>
                        <dt className="text-xs text-muted-foreground">Responsável (pra quem)</dt>
                        <dd>{auditData.agendamento.responsavel_nome ?? "—"}</dd>
                      </div>
                      <div>
                        <dt className="text-xs text-muted-foreground">Setor (tipo de tarefa)</dt>
                        <dd>{auditData.agendamento.setor ?? "—"}</dd>
                      </div>
                      <div>
                        <dt className="text-xs text-muted-foreground">Data agendada</dt>
                        <dd>{auditData.agendamento.data_agendamento ?? "—"}</dd>
                      </div>
                      <div>
                        <dt className="text-xs text-muted-foreground">Prazo BB</dt>
                        <dd>{auditData.agendamento.prazo_bb ?? "—"}</dd>
                      </div>
                      <div>
                        <dt className="text-xs text-muted-foreground">Status BB</dt>
                        <dd>{auditData.agendamento.status_sistema ?? "—"}</dd>
                      </div>
                      <div>
                        <dt className="text-xs text-muted-foreground">Tratamento</dt>
                        <dd>{auditData.agendamento.status_tratamento ?? "—"}</dd>
                      </div>
                    </dl>
                    {auditData.numero_processo && (
                      <div className="mt-1 text-xs text-muted-foreground">
                        CNJ: {auditData.numero_processo}
                        {auditData.npj_direcionador ? ` · NPJ: ${auditData.npj_direcionador}` : ""}
                      </div>
                    )}
                  </div>

                  {/* Tarefa viva no L1 */}
                  <div className="rounded-md border p-3">
                    <div className="mb-2 flex items-center justify-between">
                      <span className="text-sm font-medium">Tarefa no Legal One</span>
                      {auditData.tarefa_l1?.l1_url && (
                        <a
                          href={auditData.tarefa_l1.l1_url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                        >
                          Abrir no L1 <ExternalLink className="h-3 w-3" />
                        </a>
                      )}
                    </div>
                    {auditData.tarefa_l1?.task_id ? (
                      <div className="space-y-1">
                        <Badge variant="outline">{auditData.tarefa_l1.status_label ?? "—"}</Badge>
                        <div className="text-xs">{auditData.tarefa_l1.description ?? "—"}</div>
                        <div className="text-xs text-muted-foreground">
                          Prazo da tarefa: {auditData.tarefa_l1.end_date_time ? fmtDateTime(auditData.tarefa_l1.end_date_time) : "—"}
                        </div>
                      </div>
                    ) : (
                      <div className="text-xs text-muted-foreground">
                        Tarefa da DMI não localizada no L1.
                        {auditData.tarefa_l1?.lawsuit_url && (
                          <>
                            {" "}
                            <a href={auditData.tarefa_l1.lawsuit_url} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                              Abrir a pasta no L1
                            </a>
                            .
                          </>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Anotações */}
                  <div className="rounded-md border p-3">
                    <div className="mb-2 text-sm font-medium">Anotações</div>
                    <div className="flex gap-2">
                      <Input
                        placeholder="Ex.: aguardando documento do cliente…"
                        value={auditNovaAnotacao}
                        onChange={(e) => setAuditNovaAnotacao(e.target.value)}
                        onKeyDown={(e) => e.key === "Enter" && salvarAuditAnotacao()}
                      />
                      <Button variant="secondary" onClick={salvarAuditAnotacao} disabled={auditSavingAnot || !auditNovaAnotacao.trim()}>
                        {auditSavingAnot && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                        Anotar
                      </Button>
                    </div>
                    {auditData.anotacoes.length > 0 ? (
                      <ul className="mt-2 max-h-40 space-y-1 overflow-auto text-xs">
                        {auditData.anotacoes.map((a) => (
                          <li key={a.id} className="border-b pb-1">
                            <span className="text-muted-foreground">
                              {fmtDateTime(a.created_at)} · {a.autor_nome ?? "—"}:
                            </span>{" "}
                            {a.texto}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <div className="mt-2 text-xs text-muted-foreground">Sem anotações.</div>
                    )}
                  </div>
                </div>
              )}
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* Modal de Auditoria de processo / DMI (busca cross-status) */}
      <Dialog open={auditoriaOpen} onOpenChange={setAuditoriaOpen}>
        <DialogContent className="max-h-[92vh] max-w-4xl overflow-y-auto overflow-x-hidden">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <FileSearch className="h-5 w-5 text-primary" />
              Auditoria de processo / DMI
            </DialogTitle>
            <DialogDescription>
              Digite o nº do processo (CNJ) ou da DMI. Mostra todas as DMIs vinculadas — em qualquer
              status — com agendamento, responsável e conclusão.
            </DialogDescription>
          </DialogHeader>

          <div className="flex gap-2">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                autoFocus
                className="w-full pl-8"
                placeholder="Nº do processo (CNJ) ou da DMI"
                value={auditoriaTermo}
                onChange={(e) => setAuditoriaTermo(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && buscarAuditoria()}
              />
            </div>
            <Button onClick={buscarAuditoria} disabled={auditoriaBuscando || !auditoriaTermo.trim()}>
              {auditoriaBuscando ? <Loader2 className="h-4 w-4 animate-spin" /> : "Buscar"}
            </Button>
          </div>

          <div className="mt-1 space-y-2">
            {!auditoriaBuscou ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                Busque por um processo ou DMI para ver o histórico de tratamento.
              </p>
            ) : auditoriaBuscando ? (
              <div className="py-8 text-center">
                <Loader2 className="mx-auto h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : auditoriaResultados.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                Nenhuma DMI encontrada para “{auditoriaTermo}”.
              </p>
            ) : (
              <>
                <div className="text-xs text-muted-foreground">
                  {auditoriaResultados.length} DMI(s) encontrada(s)
                </div>
                {auditoriaResultados.map((sol) => (
                  <div key={sol.id} className="rounded-lg border p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-sm font-medium">{sol.numero_solicitacao}</span>
                          <StatusBadge sol={sol} />
                          {sol.desfecho && DESFECHO_BADGE[sol.desfecho] && (
                            <Badge variant="outline" className={`text-[10px] ${DESFECHO_BADGE[sol.desfecho].cls}`}>
                              {DESFECHO_BADGE[sol.desfecho].label}
                            </Badge>
                          )}
                        </div>
                        <div className="truncate text-xs text-muted-foreground" title={sol.titulo ?? ""}>
                          {sol.titulo ?? "Sem título"}
                        </div>
                      </div>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="shrink-0"
                        onClick={() => {
                          setAuditoriaOpen(false);
                          abrirAuditoria(sol);
                        }}
                      >
                        Ver detalhe
                      </Button>
                    </div>
                    <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
                      <div>
                        <dt className="text-muted-foreground">Processo</dt>
                        <dd className="font-mono">{sol.numero_processo ?? "—"}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Prazo BB</dt>
                        <dd>{sol.prazo ?? "—"}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Responsável</dt>
                        <dd>{sol.responsavel_nome ?? "—"}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Setor</dt>
                        <dd>{sol.setor ?? "—"}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Agendada p/</dt>
                        <dd>{sol.data_agendamento ?? "—"}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Agendada por</dt>
                        <dd>
                          {sol.scheduled_by_nome ?? "—"}
                          {sol.scheduled_at ? ` · ${fmtDataBR(sol.scheduled_at)}` : ""}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Status BB</dt>
                        <dd>{sol.status_sistema}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Tratamento</dt>
                        <dd>{sol.status_tratamento}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Status L1</dt>
                        <dd>{sol.l1_dmi_status_label ?? "—"}</dd>
                      </div>
                    </dl>
                  </div>
                ))}
              </>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Modal de Mensagens de Alerta (vence hoje, por responsável) */}
      <Dialog open={alertasOpen} onOpenChange={setAlertasOpen}>
        <DialogContent className="max-h-[92vh] max-w-2xl overflow-y-auto overflow-x-hidden">
          <DialogHeader>
            <DialogTitle>Mensagens de alerta — vence hoje</DialogTitle>
            <DialogDescription>
              Uma mensagem por responsável. Copie e envie no Teams/WhatsApp.
            </DialogDescription>
          </DialogHeader>
          {alertasLoading ? (
            <div className="py-10 text-center">
              <Loader2 className="mx-auto h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : alertas.length === 0 ? (
            <div className="py-8 text-center text-sm text-muted-foreground">Nenhuma DMI vencendo hoje. 🎉</div>
          ) : (
            <div className="space-y-3">
              {alertas.map((g) => (
                <div key={g.responsavel_user_id ?? g.responsavel_nome} className="rounded-md border p-3">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-sm font-medium">
                      {g.responsavel_nome} <span className="text-xs text-muted-foreground">· {g.count} DMI(s)</span>
                    </span>
                    <div className="flex gap-2">
                      {(g.teams_enabled ?? g.teams_disponivel) && (
                        <Button
                          size="sm"
                          variant={teamsPickerFor === grupoKey(g) ? "secondary" : "default"}
                          onClick={() => abrirTeamsPicker(g)}
                          disabled={enviandoTeams != null}
                          title="Escolher quem notificar no Teams (o responsável já vem marcado)"
                        >
                          <Bell className="mr-2 h-4 w-4" />
                          Alerta Teams
                        </Button>
                      )}
                      <Button size="sm" variant="outline" onClick={() => copiarMensagem(g.mensagem)}>
                        <ClipboardCopy className="mr-2 h-4 w-4" />
                        Copiar
                      </Button>
                    </div>
                  </div>

                  {/* Picker de destinatários (multiselect; default = responsável) */}
                  {teamsPickerFor === grupoKey(g) && (
                    <div className="mb-2 rounded-md border bg-muted/20 p-2">
                      <div className="mb-1 text-xs font-medium text-muted-foreground">
                        Notificar no Teams ({teamsSel.size} selecionado/s) — a DM sai no seu nome
                      </div>
                      <Command className="rounded-md border bg-background">
                        <CommandInput placeholder="Buscar colaborador…" />
                        <CommandList className="max-h-44">
                          <CommandEmpty>Ninguém encontrado.</CommandEmpty>
                          <CommandGroup>
                            {[...users]
                              .sort((a, b) => {
                                const ea = teamsEnderecavel(g, a.email) ? 0 : 1;
                                const eb = teamsEnderecavel(g, b.email) ? 0 : 1;
                                return ea - eb || a.name.localeCompare(b.name);
                              })
                              .map((u) => {
                                const ok = teamsEnderecavel(g, u.email);
                                const marcado = teamsSel.has(u.id);
                                return (
                                  <CommandItem
                                    key={u.id}
                                    value={u.name}
                                    disabled={!ok}
                                    onSelect={() =>
                                      setTeamsSel((s) => {
                                        const n = new Set(s);
                                        n.has(u.id) ? n.delete(u.id) : n.add(u.id);
                                        return n;
                                      })
                                    }
                                  >
                                    <Checkbox checked={marcado} className="mr-2" />
                                    <span className="min-w-0 flex-1 truncate">
                                      {u.name}
                                      {g.responsavel_user_id === u.id && (
                                        <span className="ml-1 text-[10px] text-sky-700">(responsável)</span>
                                      )}
                                    </span>
                                    {!ok && (
                                      <span className="ml-2 shrink-0 text-[10px] text-muted-foreground">
                                        sem Teams corporativo
                                      </span>
                                    )}
                                  </CommandItem>
                                );
                              })}
                          </CommandGroup>
                        </CommandList>
                      </Command>
                      <div className="mt-2 flex justify-end gap-2">
                        <Button size="sm" variant="ghost" onClick={() => setTeamsPickerFor(null)}>
                          Cancelar
                        </Button>
                        <Button
                          size="sm"
                          disabled={teamsSel.size === 0 || enviandoTeams != null}
                          onClick={() => enviarTeams(g, [...teamsSel])}
                        >
                          {enviandoTeams === (g.responsavel_user_id ?? -1) ? (
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                          ) : (
                            <Bell className="mr-2 h-4 w-4" />
                          )}
                          Enviar ({teamsSel.size})
                        </Button>
                      </div>
                    </div>
                  )}

                  <pre className="whitespace-pre-wrap break-words rounded bg-muted/50 p-2 text-xs">{g.mensagem}</pre>
                </div>
              ))}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
