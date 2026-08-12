// frontend/src/components/performance/PainelEquipe.tsx
//
// Painel analítico do setor (a "seção do dashboard") — só gráficos:
//   1. Vazão       — quem mais concluiu (quem dá mais vazão).
//   2. Pool/Atraso — pool pendente de cada um, com a fatia atrasada em vermelho.
//   3. Jornada     — horário em que cada pessoa começa e termina o dia + hands-on.
//   4. Top tarefas — as tarefas mais importantes do setor, coloridas por natureza.
//
// Tudo com (?) explicativo. Lê de /performance/dashboard.

import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AlertTriangle, Clock, Download, type LucideIcon, Settings2, TrendingUp } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { InfoHint } from "@/components/performance/InfoHint";
import {
  type DashboardData,
  type DuplicadasResp,
  type PoolPorTipo,
  type SubtipoDetalhe,
  downloadExport,
  getDashboard,
  getDuplicadas,
  getPoolPorTipo,
  getSubtipoDetalhe,
} from "@/services/performance";
import { useToast } from "@/hooks/use-toast";
import EditarBoardDialog from "@/components/performance/EditarBoardDialog";
import DuplicadasBox from "@/components/performance/DuplicadasBox";

const cargoColor = (cargo: string | null): string => {
  const c = (cargo || "").toLowerCase();
  if (c.includes("advog")) return "#8b5cf6";
  if (c.includes("estag")) return "#0ea5e9";
  if (c.includes("assist")) return "#f59e0b";
  return "#94a3b8";
};

// Paleta da pizza do pool. Pessoas chegam a ter ~30 subtipos distintos no pool;
// pizza com 30 fatias é ilegível, então mostramos as MAIORES e somamos a cauda
// em "Outros" (a tabela abaixo do gráfico continua listando tudo, item a item).
const PIE_COLORS = [
  "#e11d48", "#f59e0b", "#8b5cf6", "#0ea5e9", "#10b981",
  "#f43f5e", "#6366f1", "#14b8a6", "#eab308", "#94a3b8",
];
const PIE_MAX_FATIAS = 9;

function piePool(d: { itens: { subtipo: string; total: number }[] }) {
  const itens = [...d.itens].sort((a, b) => b.total - a.total);
  if (itens.length <= PIE_MAX_FATIAS) return itens;
  const cabeca = itens.slice(0, PIE_MAX_FATIAS);
  const cauda = itens.slice(PIE_MAX_FATIAS);
  return [
    ...cabeca,
    { subtipo: `Outros (${cauda.length} tipos)`, total: cauda.reduce((s, i) => s + i.total, 0) },
  ];
}

const CAT_COLOR: Record<string, string> = {
  operacional: "#378ADD",
  profundo: "#1D9E75",
  ruido: "#94a3b8",
};

const NAT_LABEL: Record<string, string> = {
  operacional: "Operacional",
  profundo: "Profundo",
  ruido: "Ruído",
};

const firstName = (n: string): string => n.split(" ").slice(0, 2).join(" ");
const fmtH = (h: number): string => {
  const hh = Math.floor(h);
  const mm = Math.round((h - hh) * 60);
  return `${hh}:${String(mm).padStart(2, "0")}`;
};
const truncTipo = (s: string): string => (s.length > 24 ? s.slice(0, 24) + "…" : s);

// Duração legível a partir de segundos: s → min → h → dias, conforme a grandeza.
const fmtSeg = (s: number | null | undefined): string => {
  if (s == null) return "—";
  if (s < 90) return `${Math.round(s)}s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 86400 * 2) return `${(s / 3600).toFixed(1)} h`;
  return `${(s / 86400).toFixed(1)} dias`;
};

// Tick do YAxis do board: rótulo do subtipo + (i) clicável que abre o detalhe.
function TipoTick(props: any) {
  const { x, y, payload, onPick } = props;
  const sub: string = payload?.value ?? "";
  return (
    <g transform={`translate(${x},${y})`} style={{ cursor: "pointer" }} onClick={() => onPick?.(sub)}>
      <title>Ver detalhe e capacity deste tipo</title>
      <text x={-16} dy={3} textAnchor="end" fontSize={10} fill="#475569">
        {truncTipo(sub)}
      </text>
      <circle cx={-7} cy={0} r={5} fill="none" stroke="hsl(var(--dunatech-blue))" strokeWidth={1} />
      <text x={-7} dy={2.6} textAnchor="middle" fontSize={7.5} fontWeight={700} fill="hsl(var(--dunatech-blue))">
        i
      </text>
    </g>
  );
}

function ChartCard({
  title,
  hint,
  children,
  legend,
}: {
  title: string;
  hint: string;
  children: React.ReactNode;
  legend?: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="flex items-center gap-1.5 text-sm">
          {title}
          <InfoHint text={hint} />
        </CardTitle>
        {legend}
      </CardHeader>
      <CardContent className="pt-2">{children}</CardContent>
    </Card>
  );
}

function MiniKpi({ label, hint, value, icon: Icon, tone }: { label: string; hint: string; value: number; icon: LucideIcon; tone: string }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border bg-card p-3">
      <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full ${tone}`}>
        <Icon className="h-4 w-4" />
      </span>
      <div>
        <div className="text-xl font-bold leading-none">{value}</div>
        <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
          {label}
          <InfoHint text={hint} />
        </div>
      </div>
    </div>
  );
}

const CargoLegend = () => (
  <div className="flex items-center gap-2.5 text-[10px] text-muted-foreground">
    <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: "#8b5cf6" }} /> Advogado</span>
    <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: "#0ea5e9" }} /> Estagiário</span>
    <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: "#f59e0b" }} /> Assistente</span>
  </div>
);

function JornadaTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  if (!d) return null;
  return (
    <div className="rounded-md border bg-popover px-3 py-2 text-xs shadow-md">
      <div className="font-semibold">{d.nome}</div>
      <div className="mt-1 space-y-0.5 text-muted-foreground">
        <div>Começa ~ <span className="font-medium text-foreground">{fmtH(d.inicio_h)}</span> · termina ~ <span className="font-medium text-foreground">{fmtH(d.fim_h)}</span></div>
        <div>Hands-on/dia: <span className="font-medium text-foreground">{d.hands_on_h}h</span> · Ócio: <span className="font-medium text-foreground">{d.ocio_pct ?? "—"}%</span></div>
        <div>Operacional: {d.oper_share}% · {d.dias} dias ativos</div>
      </div>
    </div>
  );
}

function TopTipoTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  if (!d) return null;
  return (
    <div className="rounded-md border bg-popover px-3 py-2 text-xs shadow-md">
      <div className="font-semibold">{d.subtipo}</div>
      <div className="mt-1 space-y-0.5 text-muted-foreground">
        <div className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-full" style={{ background: CAT_COLOR[d.categoria] || "#94a3b8" }} />
          {NAT_LABEL[d.categoria] ?? d.categoria}
        </div>
        <div>Concluídas: <span className="font-medium text-foreground">{d.volume}</span></div>
        <div>
          Pendentes: <span className="font-medium text-foreground">{d.pendente}</span>{" "}
          (Atrasadas <span className="font-medium text-rose-600">{d.atrasado}</span>)
        </div>
      </div>
    </div>
  );
}

export default function PainelEquipe({ days, team }: { days: number; team: string }) {
  const { toast } = useToast();
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(false);
  const [reload, setReload] = useState(0);
  const [editBoard, setEditBoard] = useState(false);
  const [pendingExport, setPendingExport] = useState<
    { escopo: "atrasado" | "pendente" | "concluido"; extra: { subtipo?: string; pessoa_id?: number }; label: string } | null
  >(null);
  const [detalhe, setDetalhe] = useState<{
    subtipo: string;
    loading: boolean;
    data: SubtipoDetalhe | null;
    dups: DuplicadasResp | null;
  } | null>(null);

  // Pizza do pool de uma pessoa (substitui o export-ao-clicar na barra).
  const [pool, setPool] = useState<{
    nome: string;
    escopo: "atrasado" | "pendente";
    loading: boolean;
    data: PoolPorTipo | null;
  } | null>(null);

  const abrirPool = (pessoaId: number, nome: string, escopo: "atrasado" | "pendente") => {
    setPool({ nome, escopo, loading: true, data: null });
    getPoolPorTipo(team, pessoaId, escopo)
      .then((d) => setPool({ nome, escopo, loading: false, data: d }))
      .catch((e) => {
        setPool(null);
        toast({ title: "Erro ao abrir a quebra por tipo", description: String((e as Error).message), variant: "destructive" });
      });
  };

  const abrirDetalhe = (subtipo: string) => {
    setDetalhe({ subtipo, loading: true, data: null, dups: null });
    Promise.all([getSubtipoDetalhe(team, subtipo, days), getDuplicadas(team, subtipo).catch(() => null)])
      .then(([d, dups]) => setDetalhe({ subtipo, loading: false, data: d, dups }))
      .catch((e) => {
        setDetalhe(null);
        toast({ title: "Erro ao carregar o detalhe", description: String((e as Error).message), variant: "destructive" });
      });
  };

  useEffect(() => {
    setLoading(true);
    getDashboard(team, days)
      .then(setData)
      .catch((e) => toast({ title: "Erro ao carregar o painel", description: String((e as Error).message), variant: "destructive" }))
      .finally(() => setLoading(false));
  }, [days, team, toast, reload]);

  if (!data) {
    return (
      <Card>
        <CardContent className="py-12 text-center text-sm text-muted-foreground">
          {loading ? "Carregando painel…" : "Sem dados."}
        </CardContent>
      </Card>
    );
  }

  const vazao = data.vazao.slice(0, 15);
  const backlog = data.backlog
    .filter((b) => b.backlog > 0)
    .slice(0, 15)
    .map((b) => ({ ...b, emdia: Math.max(0, b.backlog - b.atrasado) }));
  const jornada = data.jornada.map((j) => ({ ...j, base: j.inicio_h, dur: Math.max(0.1, j.fim_h - j.inicio_h) }));
  const jornadaH = Math.max(300, jornada.length * 22);
  const topTipos = data.top_tipos.map((t) => ({ ...t, emdia: Math.max(0, t.pendente - t.atrasado) }));

  const handleExport = (
    escopo: "atrasado" | "pendente" | "concluido",
    extra: { subtipo?: string; pessoa_id?: number } = {},
  ) => {
    downloadExport({ escopo, days, team, ...extra }).catch((e) =>
      toast({ title: "Erro ao exportar", description: String((e as Error).message), variant: "destructive" }),
    );
  };
  // recharts entrega o ponto clicado ora no topo, ora em .payload — cobre os dois.
  const barSub = (d: any): string | undefined => d?.subtipo ?? d?.payload?.subtipo;
  const barPid = (d: any): number | undefined => d?.id ?? d?.payload?.id;
  const barName = (d: any): string => d?.nome ?? d?.payload?.nome ?? "pessoa";
  const ESC_LABEL: Record<string, string> = { atrasado: "Atrasadas", pendente: "Pendentes", concluido: "Concluídas" };
  // Clique no gráfico não baixa direto — abre confirmação (evita travar baixando tudo).
  const requestExport = (
    escopo: "atrasado" | "pendente" | "concluido",
    extra: { subtipo?: string; pessoa_id?: number },
    alvo: string,
  ) => setPendingExport({ escopo, extra, label: `${ESC_LABEL[escopo]} de ${alvo}` });

  return (
    <div className="space-y-4">
      {/* KPIs de risco do painel */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <MiniKpi label="Pendentes (pool aberto)" hint="Total de tarefas em aberto (não concluídas) do setor — a carga acumulada." value={data.kpis.backlog_total} icon={Clock} tone="bg-amber-100 text-amber-700" />
        <MiniKpi label="Atrasadas" hint="Tarefas pendentes cujo prazo previsto já passou. É o que precisa de atenção agora." value={data.kpis.atrasado_total} icon={AlertTriangle} tone="bg-rose-100 text-rose-700" />
        <MiniKpi label="% do pool atrasado" hint="Quanto do pool aberto já está vencido." value={data.kpis.backlog_total ? Math.round((100 * data.kpis.atrasado_total) / data.kpis.backlog_total) : 0} icon={TrendingUp} tone="bg-slate-100 text-slate-700" />
      </div>

      {/* Exportar recortes em Excel (pro operador agir nas tarefas) */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-card/40 p-2.5">
        <span className="flex items-center gap-1 text-xs font-medium text-muted-foreground">
          <Download className="h-3.5 w-3.5" /> Exportar tarefas do setor (Excel):
        </span>
        <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => handleExport("atrasado")}>
          Atrasadas
        </Button>
        <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => handleExport("pendente")}>
          Pendentes
        </Button>
        <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => handleExport("concluido")}>
          Concluídas
        </Button>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Vazão */}
        <ChartCard
          title="Quem dá mais vazão"
          hint="Total de tarefas concluídas por pessoa no período — quem mais produz. Cor = cargo (compare advogado com advogado, estagiário com estagiário)."
          legend={<CargoLegend />}
        >
          <ResponsiveContainer width="100%" height={Math.max(280, vazao.length * 26)}>
            <BarChart data={vazao} layout="vertical" margin={{ left: 8, right: 28, top: 4 }}>
              <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="hsl(var(--border))" />
              <XAxis type="number" fontSize={11} allowDecimals={false} />
              <YAxis type="category" dataKey="nome" width={118} fontSize={11} tickFormatter={firstName} tickLine={false} axisLine={false} />
              <RTooltip formatter={(v: any) => [v, "Concluídas"]} cursor={{ fill: "hsl(var(--muted))", opacity: 0.4 }} />
              <Bar dataKey="concluido" name="Concluídas" radius={[0, 4, 4, 0]}>
                {vazao.map((d, i) => (
                  <Cell key={i} fill={cargoColor(d.cargo)} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        {/* Pool pendente / atrasados */}
        <ChartCard
          title="Pool pendente — e quanto está atrasado"
          hint="Tarefas em aberto por pessoa. Vermelho = já vencidas (atrasadas); âmbar = ainda no prazo. Mostra quem está acumulando e quem está em risco."
          legend={
            <div className="flex items-center gap-2.5 text-[10px] text-muted-foreground">
              <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: "#e11d48" }} /> Atrasadas</span>
              <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: "#f59e0b" }} /> No prazo</span>
            </div>
          }
        >
          <p className="mb-1 text-[10px] text-muted-foreground">Clique na barra de uma pessoa para ver <span className="font-semibold">quais tipos de tarefa</span> estão nesse pool (vermelho = atrasadas, âmbar = no prazo). O Excel continua disponível dentro do detalhe.</p>
          <ResponsiveContainer width="100%" height={Math.max(280, backlog.length * 26)}>
            <BarChart data={backlog} layout="vertical" margin={{ left: 8, right: 28, top: 4 }}>
              <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="hsl(var(--border))" />
              <XAxis type="number" fontSize={11} allowDecimals={false} />
              <YAxis type="category" dataKey="nome" width={118} fontSize={11} tickFormatter={firstName} tickLine={false} axisLine={false} />
              <RTooltip cursor={{ fill: "hsl(var(--muted))", opacity: 0.4 }} />
              <Bar dataKey="atrasado" name="Atrasadas" stackId="b" fill="#e11d48" cursor="pointer"
                onClick={(d: any) => barPid(d) && abrirPool(barPid(d)!, barName(d), "atrasado")} />
              <Bar dataKey="emdia" name="No prazo" stackId="b" fill="#f59e0b" radius={[0, 4, 4, 0]} cursor="pointer"
                onClick={(d: any) => barPid(d) && abrirPool(barPid(d)!, barName(d), "pendente")} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      {/* Jornada do dia */}
      <ChartCard
        title="Jornada do dia — quando cada um começa e termina"
        hint="Faixa do horário típico de trabalho de cada pessoa: do horário mediano da 1ª conclusão até o da última conclusão do dia. Passe o mouse pra ver hands-on (tempo efetivo) e ócio. Mais fiel em quem é majoritariamente operacional."
        legend={<CargoLegend />}
      >
        <ResponsiveContainer width="100%" height={jornadaH}>
          <BarChart data={jornada} layout="vertical" margin={{ left: 8, right: 24, top: 4 }} barCategoryGap={2}>
            <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="hsl(var(--border))" />
            <XAxis type="number" domain={[6, 20]} ticks={[6, 8, 10, 12, 14, 16, 18, 20]} tickFormatter={(h) => `${h}h`} fontSize={11} />
            <YAxis type="category" dataKey="nome" width={118} fontSize={10} tickFormatter={firstName} tickLine={false} axisLine={false} />
            <RTooltip content={<JornadaTooltip />} cursor={{ fill: "hsl(var(--muted))", opacity: 0.4 }} />
            <Bar dataKey="base" stackId="j" fill="transparent" />
            <Bar dataKey="dur" stackId="j" radius={3}>
              {jornada.map((d, i) => (
                <Cell key={i} fill={cargoColor(d.cargo)} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>

      {/* Top tarefas do setor — estado: concluídas / pendentes / atrasadas */}
      <ChartCard
        title="Tarefas mais importantes do setor"
        hint="Os tipos de maior volume e o ESTADO de cada um: a parte colorida = concluídas no período (cor pela natureza — operacional/profundo/ruído); âmbar = pendentes no prazo; vermelho = atrasadas. Mostra o que saiu e o que está represado/vencido por tipo."
        legend={
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[10px] text-muted-foreground">
            <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: CAT_COLOR.operacional }} /> Operac.</span>
            <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: CAT_COLOR.profundo }} /> Profundo</span>
            <span className="text-muted-foreground/40">concluídas</span>
            <span className="text-muted-foreground/40">·</span>
            <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: "#f59e0b" }} /> Pendente</span>
            <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: "#e11d48" }} /> Atrasada</span>
          </div>
        }
      >
        <div className="mb-1 flex items-start justify-between gap-3">
          <p className="text-[10px] text-muted-foreground">Clique numa barra para exportar as tarefas daquele tipo em Excel — vermelho = atrasadas (campanha focada), âmbar = pendentes, colorido = concluídas. Clique no <span className="font-semibold text-[hsl(var(--dunatech-blue))]">ⓘ</span> ao lado do nome pra abrir o detalhe e o tempo de capacity do tipo.</p>
          <div className="flex shrink-0 items-center gap-2">
            <span className="whitespace-nowrap text-[10px] text-muted-foreground">
              {data.board_curado ? "seleção fixa" : "top-12 automático"}
            </span>
            <Button size="sm" variant="outline" className="h-7 gap-1 text-xs" onClick={() => setEditBoard(true)}>
              <Settings2 className="h-3.5 w-3.5" /> Editar tarefas
            </Button>
          </div>
        </div>
        <ResponsiveContainer width="100%" height={Math.max(300, topTipos.length * 30)}>
          <BarChart data={topTipos} layout="vertical" margin={{ left: 8, right: 28, top: 4 }}>
            <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="hsl(var(--border))" />
            <XAxis type="number" fontSize={11} allowDecimals={false} />
            <YAxis
              type="category"
              dataKey="subtipo"
              width={185}
              tickLine={false}
              axisLine={false}
              interval={0}
              tick={<TipoTick onPick={abrirDetalhe} />}
            />
            <RTooltip content={<TopTipoTooltip />} cursor={{ fill: "hsl(var(--muted))", opacity: 0.4 }} />
            <Bar dataKey="volume" name="Concluídas" stackId="s" cursor="pointer"
              onClick={(d: any) => barSub(d) && requestExport("concluido", { subtipo: barSub(d) }, barSub(d)!)}>
              {topTipos.map((d, i) => (
                <Cell key={i} fill={CAT_COLOR[d.categoria] || "#94a3b8"} />
              ))}
            </Bar>
            <Bar dataKey="emdia" name="Pendentes no prazo" stackId="s" fill="#f59e0b" cursor="pointer"
              onClick={(d: any) => barSub(d) && requestExport("pendente", { subtipo: barSub(d) }, barSub(d)!)} />
            <Bar dataKey="atrasado" name="Atrasadas" stackId="s" fill="#e11d48" radius={[0, 4, 4, 0]} cursor="pointer"
              onClick={(d: any) => barSub(d) && requestExport("atrasado", { subtipo: barSub(d) }, barSub(d)!)} />
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>

      {/* Pizza: quais TIPOS de tarefa formam o pool da pessoa clicada. */}
      <Dialog open={pool != null} onOpenChange={(o) => !o && setPool(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="text-base">
              {pool?.escopo === "atrasado" ? "Tipos de tarefa atrasados" : "Tipos de tarefa no prazo"}
              {pool ? ` — ${pool.nome}` : ""}
            </DialogTitle>
          </DialogHeader>
          {pool?.loading && <p className="py-8 text-center text-sm text-muted-foreground">Carregando…</p>}
          {pool && !pool.loading && pool.data && (
            pool.data.total === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">Nada nesse recorte.</p>
            ) : (
              <>
                <p className="text-xs text-muted-foreground">
                  <span className="font-semibold text-foreground">{pool.data.total}</span>{" "}
                  {pool.escopo === "atrasado" ? "tarefa(s) vencida(s)" : "tarefa(s) em aberto no prazo"} em{" "}
                  {pool.data.itens.length} tipo(s).
                </p>
                <ResponsiveContainer width="100%" height={260}>
                  <PieChart>
                    <Pie
                      data={piePool(pool.data)}
                      dataKey="total"
                      nameKey="subtipo"
                      cx="50%"
                      cy="50%"
                      outerRadius={88}
                      label={(e: any) => (e.percent >= 0.06 ? `${Math.round(e.percent * 100)}%` : "")}
                      labelLine={false}
                    >
                      {piePool(pool.data).map((s, i) => (
                        <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                      ))}
                    </Pie>
                    <RTooltip formatter={(v: any, n: any) => [`${v} tarefa(s)`, n]} />
                    <Legend verticalAlign="bottom" height={72} iconSize={9}
                      wrapperStyle={{ fontSize: 10, lineHeight: "14px", overflowY: "auto", maxHeight: 72 }} />
                  </PieChart>
                </ResponsiveContainer>
                <div className="max-h-40 overflow-y-auto rounded border">
                  <table className="w-full text-xs">
                    <tbody>
                      {pool.data.itens.map((it) => (
                        <tr key={it.subtipo} className="border-b last:border-0">
                          <td className="px-2 py-1">{it.subtipo}</td>
                          <td className="px-2 py-1 text-right font-semibold tabular-nums">{it.total}</td>
                          <td className="px-2 py-1 text-right text-muted-foreground">
                            {Math.round((100 * it.total) / pool.data!.total)}%
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  className="gap-1 self-start text-xs"
                  onClick={() =>
                    handleExport(pool.escopo, { pessoa_id: pool.data!.pessoa.id })
                  }
                >
                  <Download className="h-3.5 w-3.5" /> Baixar estas tarefas em Excel
                </Button>
              </>
            )
          )}
        </DialogContent>
      </Dialog>

      <AlertDialog open={pendingExport != null} onOpenChange={(o) => !o && setPendingExport(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Baixar este recorte em Excel?</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingExport ? `Vai baixar: ${pendingExport.label}.` : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Não</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (pendingExport) handleExport(pendingExport.escopo, pendingExport.extra);
                setPendingExport(null);
              }}
            >
              Sim, baixar
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog open={detalhe != null} onOpenChange={(o) => !o && setDetalhe(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base">
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ background: CAT_COLOR[detalhe?.data?.categoria ?? "profundo"] || "#94a3b8" }}
              />
              <span className="truncate">{detalhe?.subtipo}</span>
            </DialogTitle>
          </DialogHeader>
          {!detalhe || detalhe.loading || !detalhe.data ? (
            <p className="py-8 text-center text-sm text-muted-foreground">Calculando…</p>
          ) : (
            <div className="space-y-3 text-sm">
              <p className="text-xs text-muted-foreground">
                Natureza <b>{NAT_LABEL[detalhe.data.categoria] ?? detalhe.data.categoria}</b> · últimos{" "}
                {detalhe.data.periodo_dias} dias · {detalhe.data.pessoas} pessoa(s) executando
              </p>
              <div className="grid grid-cols-3 gap-2">
                {[
                  { label: "Concluídas", value: detalhe.data.concluido, color: "#1D9E75" },
                  { label: "Pendentes", value: detalhe.data.pendente, color: "#f59e0b" },
                  { label: "Atrasadas", value: detalhe.data.atrasado, color: "#e11d48" },
                ].map((s) => (
                  <div key={s.label} className="rounded-lg border p-2.5 text-center">
                    <div className="text-lg font-semibold tabular-nums" style={{ color: s.color }}>
                      {s.value}
                    </div>
                    <div className="text-[10px] text-muted-foreground">{s.label}</div>
                  </div>
                ))}
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-lg border p-2.5">
                  <div className="text-[11px] text-muted-foreground">No prazo</div>
                  <div className="text-lg font-semibold tabular-nums">
                    {detalhe.data.no_prazo_pct == null ? "—" : `${detalhe.data.no_prazo_pct}%`}
                  </div>
                </div>
                <div className="rounded-lg border p-2.5">
                  <div className="text-[11px] text-muted-foreground">Tempo de conclusão</div>
                  <div className="text-lg font-semibold tabular-nums">{fmtSeg(detalhe.data.tempo_conclusao_seg)}</div>
                  <div className="text-[10px] text-muted-foreground">cadastro → conclusão (latência)</div>
                </div>
              </div>
              <div className="rounded-lg border border-[hsl(var(--dunatech-blue))]/30 bg-[hsl(var(--dunatech-blue))]/5 p-2.5">
                <div className="flex items-baseline justify-between gap-2">
                  <div className="text-[11px] font-medium text-muted-foreground">Tempo de trabalho / tarefa (capacity)</div>
                  <div className="text-xl font-bold tabular-nums text-[hsl(var(--dunatech-blue))]">
                    {fmtSeg(detalhe.data.tempo_trabalho_seg)}
                  </div>
                </div>
                <div className="mt-1 text-[10px] leading-snug text-muted-foreground">
                  Esforço efetivo por tarefa — mediana do intervalo entre conclusões do tipo, descontadas pausas &gt;
                  30min. É o número que entra no cálculo de quantas cabem num dia.{" "}
                  {detalhe.data.amostra_trabalho > 0
                    ? `Amostra: ${detalhe.data.amostra_trabalho} intervalos.`
                    : "Amostra insuficiente no período."}
                </div>
              </div>

              {detalhe.dups && (
                <DuplicadasBox team={team} dups={detalhe.dups} onDone={() => setReload((x) => x + 1)} />
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>

      {editBoard && (
        <EditarBoardDialog
          team={team}
          onClose={() => setEditBoard(false)}
          onChanged={() => setReload((x) => x + 1)}
        />
      )}
    </div>
  );
}
