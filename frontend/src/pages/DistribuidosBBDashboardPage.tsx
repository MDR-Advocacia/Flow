import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  BarChart3,
  Building2,
  CheckCircle2,
  List,
  CloudDownload,
  Upload,
  Download,
  FileSpreadsheet,
  Inbox,
  Layers,
  ListChecks,
  Loader2,
  type LucideIcon,
  RefreshCw,
  Settings,
  ShieldAlert,
  UserX,
  Users,
} from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from "recharts";
import ImportarAtivosDialog from "@/components/distribuidos-bb/ImportarAtivosDialog";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import {
  DashboardData,
  RunResumo,
  baixarPlanilhaArquivada,
  gerarPlanilhaNoHistorico,
  dispararColeta,
  getDashboard,
  getRun,
  rodarSeed,
} from "@/services/distribuidos-bb";

const CHART_COLORS = [
  "#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ef4444",
  "#06b6d4", "#ec4899", "#84cc16", "#f97316", "#6366f1",
];

function fmtDataCurta(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

function fmtDiaMes(d: string): string {
  const m = d.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}/${m[2]}` : d;
}

function fmtDataDMA(d: string): string {
  const m = d.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : d;
}

function Kpi({
  label,
  value,
  icon: Icon,
  tone,
  onClick,
}: {
  label: string;
  value: number;
  icon: LucideIcon;
  tone: string;
  onClick?: () => void;
}) {
  return (
    <Card
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      className={onClick ? "cursor-pointer transition-all hover:-translate-y-0.5 hover:shadow-md" : ""}
    >
      <CardContent className="flex items-center gap-3 p-4">
        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full ${tone}`}>
          <Icon className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <div className="text-2xl font-bold leading-none">{value}</div>
          <div className="truncate text-xs text-muted-foreground">{label}</div>
        </div>
      </CardContent>
    </Card>
  );
}

// O backend fala DD/MM/AAAA; o <input type="date"> nativo fala ISO (AAAA-MM-DD).
// Convertemos nos dois sentidos pra manter o payload/back intactos e ganhar o calendário.
function brParaIso(br: string): string {
  const m = br.trim().match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  return m ? `${m[3]}-${m[2]}-${m[1]}` : "";
}
function isoParaBr(iso: string): string {
  const m = iso.trim().match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : "";
}

export default function DistribuidosBBDashboardPage() {
  const navigate = useNavigate();
  const { toast } = useToast();
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(false);
  const [escViewGrafico, setEscViewGrafico] = useState(false);

  // Coleta
  const [coletaOpen, setColetaOpen] = useState(false);
  const [ativosOpen, setAtivosOpen] = useState(false);
  const [dataIni, setDataIni] = useState("");
  const [dataFim, setDataFim] = useState("");
  const [confirmarCiencia, setConfirmarCiencia] = useState(false);
  const [coletarEnvolvidos, setColetarEnvolvidos] = useState(true);
  const [disparando, setDisparando] = useState(false);
  const [runAtivo, setRunAtivo] = useState<RunResumo | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [gerandoPlanilha, setGerandoPlanilha] = useState(false);

  const gerarPlanilhaDoPool = useCallback(async () => {
    setGerandoPlanilha(true);
    try {
      const pl = await gerarPlanilhaNoHistorico();
      await baixarPlanilhaArquivada(pl.id, pl.nome_arquivo);
      toast({
        title: "Planilha gerada",
        description: `${pl.total_processos} processo(s) do pool exportado(s) e marcado(s) como "Planilha gerada".`,
      });
      setData(await getDashboard());
    } catch (e) {
      toast({ title: "Nada para gerar", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setGerandoPlanilha(false);
    }
  }, [toast]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const dash = await getDashboard();
      setData(dash);
      // Tracking PERSISTENTE: se há uma coleta rodando no servidor, reengata o
      // acompanhamento ao montar/atualizar a tela — não depende de o operador
      // ter ficado na página quando disparou (trocar de tela não perde o track).
      const ur = dash.ultima_run;
      if (ur && ur.status === "EM_ANDAMENTO") {
        setRunAtivo((atual) => (atual && atual.id === ur.id ? atual : ur));
      }
    } catch (e) {
      toast({ title: "Erro ao carregar", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  // Auto-poll do progresso da coleta enquanto EM_ANDAMENTO
  useEffect(() => {
    if (!runAtivo || runAtivo.status !== "EM_ANDAMENTO") {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return;
    }
    pollRef.current = setInterval(async () => {
      try {
        const r = await getRun(runAtivo.id);
        setRunAtivo(r);
        if (r.status !== "EM_ANDAMENTO") {
          load();
          toast({
            title: r.status === "CONCLUIDO" ? "Coleta concluída" : "Coleta encerrada com erro",
            description: `${r.total_coletados} capturados · ${r.total_ciencia} com ciência · ${r.total_distribuidos} distribuídos · ${r.total_erros} erro(s).`,
            variant: r.status === "CONCLUIDO" ? undefined : "destructive",
          });
        }
      } catch {
        /* mantém tentando no próximo tick */
      }
    }, 3000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [runAtivo, load, toast]);

  const iniciarColeta = async () => {
    setDisparando(true);
    try {
      const resp = await dispararColeta({
        data_inicial: dataIni.trim() || undefined,
        data_final: dataFim.trim() || undefined,
        confirmar_ciencia: confirmarCiencia,
        coletar_envolvidos: coletarEnvolvidos,
      });
      setColetaOpen(false);
      if (resp.aviso_ciencia) {
        toast({ title: "Atenção", description: resp.aviso_ciencia });
      }
      setRunAtivo({
        id: resp.run_id,
        data_inicial: dataIni || null,
        data_final: dataFim || null,
        status: resp.status,
        confirmar_ciencia: confirmarCiencia,
        total_coletados: 0,
        total_ciencia: 0,
        total_distribuidos: 0,
        total_cadastrados: 0,
        total_erros: 0,
        iniciado_em: null,
        concluido_em: null,
      });
    } catch (e) {
      toast({ title: "Não foi possível iniciar a coleta", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setDisparando(false);
    }
  };

  const k = data?.kpis;

  const semConfig = (data?.por_escritorio?.length ?? 0) === 0 && (k?.total ?? 0) === 0;

  const criarConfigInicial = async () => {
    try {
      const res = await rodarSeed(false);
      if (res.criado) {
        toast({
          title: "Configuração inicial criada",
          description:
            res.nao_resolvidos && res.nao_resolvidos.length > 0
              ? `${res.nao_resolvidos.length} nome(s) não casaram com o Legal One — ajuste na Configuração.`
              : "Escritórios e filas do BB criados a partir dos padrões do robô.",
        });
      } else {
        toast({ title: "Já existe configuração", description: "Nada a criar." });
      }
      load();
    } catch (e) {
      toast({ title: "Erro no seed", description: String((e as Error).message), variant: "destructive" });
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
            <Building2 className="h-6 w-6 text-[hsl(var(--dunatech-blue))]" />
            Cadastro de Processo — Acompanhamento
          </h1>
          <p className="text-sm text-muted-foreground">
            Processos distribuídos do Banco do Brasil: coleta, ciência, distribuição e cadastro no Legal One.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => setColetaOpen(true)}>
            <CloudDownload className="mr-2 h-4 w-4" />
            Nova coleta
          </Button>
          <Button variant="outline" size="sm" onClick={() => setAtivosOpen(true)}>
            <Upload className="mr-2 h-4 w-4" />
            Importar lista (Ativos)
          </Button>
          <Button variant="outline" size="sm" onClick={() => navigate("/distribuidos-bb")}>
            <ListChecks className="mr-2 h-4 w-4" />
            Processos
          </Button>
          <Button variant="outline" size="sm" onClick={() => navigate("/distribuidos-bb/config")}>
            <Settings className="mr-2 h-4 w-4" />
            Configuração
          </Button>
          <Button variant="outline" size="icon" onClick={load} disabled={loading}>
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </Button>
        </div>
      </div>

      {semConfig && (
        <Card className="border-amber-300 bg-amber-50/60">
          <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-sm">
              <div className="font-semibold text-amber-800">Módulo ainda sem configuração</div>
              <div className="text-amber-700">
                Crie os escritórios e filas do BB a partir dos padrões do robô legado (você ajusta depois na tela de
                Configuração).
              </div>
            </div>
            <Button onClick={criarConfigInicial} className="shrink-0">
              Criar configuração inicial
            </Button>
          </CardContent>
        </Card>
      )}

      {runAtivo && (
        <Card className="border-sky-300 bg-sky-50/50">
          <CardContent className="flex flex-wrap items-center gap-4 p-4">
            {runAtivo.status === "EM_ANDAMENTO" ? (
              <Loader2 className="h-5 w-5 animate-spin text-sky-600" />
            ) : runAtivo.status === "CONCLUIDO" ? (
              <CheckCircle2 className="h-5 w-5 text-emerald-600" />
            ) : (
              <AlertTriangle className="h-5 w-5 text-rose-600" />
            )}
            <div className="text-sm">
              <span className="font-semibold">Coleta #{runAtivo.id}</span> ·{" "}
              {runAtivo.status === "EM_ANDAMENTO" ? "em andamento" : runAtivo.status.toLowerCase()}
            </div>
            <div className="flex flex-wrap gap-4 text-sm text-muted-foreground">
              <span>{runAtivo.total_coletados} capturados</span>
              <span>{runAtivo.total_ciencia} com ciência</span>
              <span>{runAtivo.total_distribuidos} distribuídos</span>
              {runAtivo.total_erros > 0 && <span className="text-rose-600">{runAtivo.total_erros} erro(s)</span>}
            </div>
            {runAtivo.status !== "EM_ANDAMENTO" && (
              <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setRunAtivo(null)}>
                Fechar
              </Button>
            )}
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Kpi label="Total capturados" value={k?.total ?? 0} icon={Inbox} tone="bg-blue-100 text-blue-700" onClick={() => navigate("/distribuidos-bb")} />
        <Kpi label="Distribuídos" value={k?.distribuidos ?? 0} icon={Users} tone="bg-sky-100 text-sky-700" onClick={() => navigate("/distribuidos-bb?status=DISTRIBUIDO")} />
        <Kpi label="Cadastrados no L1" value={data?.planilhas?.cadastrado_l1 ?? 0} icon={CheckCircle2} tone="bg-emerald-100 text-emerald-700" onClick={() => navigate("/distribuidos-bb")} />
        <Kpi label="Sem responsável" value={k?.sem_responsavel ?? 0} icon={UserX} tone="bg-amber-100 text-amber-700" />
        <Kpi label="Erros / revisão" value={(k?.erros ?? 0) + (k?.revisao ?? 0)} icon={AlertTriangle} tone="bg-rose-100 text-rose-700" onClick={() => navigate("/distribuidos-bb?status=ERRO")} />
      </div>

      {/* Por cliente (BB / Ativos) */}
      {data?.por_cliente && data.por_cliente.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Por cliente</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-3">
            {data.por_cliente.map((c) => (
              <div key={c.cliente} className="flex items-center gap-2 rounded-md border bg-card px-3 py-2">
                <span
                  className={`h-2.5 w-2.5 rounded-full ${c.cliente === "ATIVOS" ? "bg-violet-500" : "bg-yellow-500"}`}
                />
                <span className="text-sm">{c.cliente === "ATIVOS" ? "Ativos" : "Banco do Brasil"}</span>
                <span className="text-base font-semibold">{c.total}</span>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {/* Distribuição por data (capturas) + última passagem */}
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
          <CardTitle className="text-base">Distribuição por data (capturas)</CardTitle>
          {data?.ultima_passagem?.data && (
            <span className="rounded-md bg-muted/50 px-2.5 py-1 text-xs text-muted-foreground">
              Última passagem:{" "}
              <strong className="text-foreground">{fmtDataCurta(data.ultima_passagem.data)}</strong> ·{" "}
              <strong className="text-foreground">{data.ultima_passagem.capturados}</strong> capturados
            </span>
          )}
        </CardHeader>
        <CardContent>
          {(data?.por_data ?? []).length === 0 ? (
            <div className="py-10 text-center text-sm text-muted-foreground">Sem capturas ainda.</div>
          ) : (
            <div className="h-[210px]">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={data!.por_data} margin={{ left: -14, right: 8, top: 6 }}>
                  <defs>
                    <linearGradient id="gData" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.35} />
                      <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                  <XAxis
                    dataKey="data"
                    fontSize={11}
                    tickLine={false}
                    minTickGap={24}
                    tickFormatter={fmtDiaMes}
                  />
                  <YAxis fontSize={11} tickLine={false} axisLine={false} allowDecimals={false} width={28} />
                  <RTooltip labelFormatter={(l) => fmtDataDMA(String(l))} />
                  <Area
                    type="monotone"
                    dataKey="total"
                    name="Capturados"
                    stroke="#3b82f6"
                    fill="url(#gData)"
                    strokeWidth={2}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
            <CardTitle className="text-base">Por escritório responsável</CardTitle>
            <div className="flex items-center gap-0.5 rounded-md border p-0.5">
              <button
                type="button"
                onClick={() => setEscViewGrafico(false)}
                title="Lista"
                className={`rounded p-1 ${!escViewGrafico ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted/50"}`}
              >
                <List className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={() => setEscViewGrafico(true)}
                title="Gráfico"
                className={`rounded p-1 ${escViewGrafico ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted/50"}`}
              >
                <BarChart3 className="h-4 w-4" />
              </button>
            </div>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {(data?.por_escritorio ?? []).length === 0 ? (
              <div className="py-6 text-center text-sm text-muted-foreground">Sem processos ainda.</div>
            ) : escViewGrafico ? (
              <div style={{ height: Math.max(160, data!.por_escritorio.length * 46) }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data!.por_escritorio} layout="vertical" margin={{ left: 8, right: 20 }}>
                    <XAxis type="number" fontSize={11} allowDecimals={false} hide />
                    <YAxis
                      type="category"
                      dataKey="escritorio"
                      width={320}
                      fontSize={10}
                      interval={0}
                      tickLine={false}
                      axisLine={false}
                    />
                    <RTooltip />
                    <Bar dataKey="total" fill="#3b82f6" radius={[0, 4, 4, 0]}>
                      {data!.por_escritorio.map((_, i) => (
                        <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : (
              data!.por_escritorio.map((row) => (
                <button
                  key={row.escritorio}
                  type="button"
                  onClick={() => navigate("/distribuidos-bb")}
                  className="flex items-center justify-between rounded-md border bg-card px-3 py-2 text-sm transition-colors hover:bg-muted/50"
                >
                  <span className="flex items-center gap-2">
                    <Building2 className="h-4 w-4 shrink-0 text-muted-foreground" />
                    {row.escritorio}
                  </span>
                  <span className="font-semibold">{row.total}</span>
                </button>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Por natureza</CardTitle>
          </CardHeader>
          <CardContent>
            {(data?.por_natureza ?? []).length === 0 ? (
              <div className="py-10 text-center text-sm text-muted-foreground">Sem processos ainda.</div>
            ) : (
              <>
                <div className="h-[190px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={data!.por_natureza}
                        dataKey="total"
                        nameKey="natureza"
                        cx="50%"
                        cy="50%"
                        innerRadius={42}
                        outerRadius={78}
                        paddingAngle={2}
                      >
                        {data!.por_natureza!.map((_, i) => (
                          <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                        ))}
                      </Pie>
                      <RTooltip />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
                  {data!.por_natureza!.map((n, i) => (
                    <span key={n.natureza} className="flex items-center gap-1.5">
                      <span
                        className="h-2.5 w-2.5 rounded-full"
                        style={{ background: CHART_COLORS[i % CHART_COLORS.length] }}
                      />
                      {n.natureza} ({n.total})
                    </span>
                  ))}
                </div>
              </>
            )}
            {data?.ultima_run && (
              <div className="mt-3 rounded-md border bg-muted/30 p-2 text-xs text-muted-foreground">
                Última coleta: {data.ultima_run.total_coletados} capturados · {data.ultima_run.status}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Gráficos: por estado (UF) + por responsável */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Distribuição por estado (UF)</CardTitle>
          </CardHeader>
          <CardContent>
            {(data?.por_estado ?? []).length === 0 ? (
              <div className="py-10 text-center text-sm text-muted-foreground">Sem processos ainda.</div>
            ) : (
              <div className="h-[220px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data!.por_estado} margin={{ left: -12, right: 8, top: 4 }}>
                    <XAxis dataKey="uf" fontSize={11} tickLine={false} />
                    <YAxis fontSize={11} tickLine={false} axisLine={false} allowDecimals={false} width={28} />
                    <RTooltip />
                    <Bar dataKey="total" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Por responsável</CardTitle>
          </CardHeader>
          <CardContent>
            {(data?.por_responsavel ?? []).length === 0 ? (
              <div className="py-10 text-center text-sm text-muted-foreground">Sem processos ainda.</div>
            ) : (
              <div style={{ height: Math.max(160, data!.por_responsavel!.length * 28) }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data!.por_responsavel} layout="vertical" margin={{ left: 8, right: 16 }}>
                    <XAxis type="number" fontSize={11} allowDecimals={false} hide />
                    <YAxis
                      type="category"
                      dataKey="responsavel"
                      width={140}
                      fontSize={11}
                      tickLine={false}
                      axisLine={false}
                    />
                    <RTooltip />
                    <Bar dataKey="total" fill="#10b981" radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Pool + planilhas pendentes de subir no Legal One */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-3 flex-row items-center justify-between space-y-0">
            <CardTitle className="flex items-center gap-2 text-base">
              <Layers className="h-4 w-4 text-amber-600" />
              Pool aguardando planilha
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">{data?.planilhas?.pool_novos ?? 0}</div>
            <p className="mt-1 text-sm text-muted-foreground">
              Processos novos distribuídos que ainda não entraram em nenhuma planilha. Gere quando
              quiser — a próxima coleta traz os novos.
            </p>
            {/* Ciclo do cadastro: Novo → Pendente cadastro → Confirmado no L1 */}
            <div className="mt-3 flex flex-wrap gap-2 text-xs">
              <span className="rounded-full bg-amber-100 px-2 py-1 text-amber-700">
                {data?.planilhas?.pool_novos ?? 0} Novo
              </span>
              <span className="rounded-full bg-sky-100 px-2 py-1 text-sky-700">
                {data?.planilhas?.pendente_cadastro ?? 0} Pendente cadastro
              </span>
              <span className="rounded-full bg-emerald-100 px-2 py-1 text-emerald-700">
                {data?.planilhas?.cadastrado_l1 ?? 0} Confirmado no L1
              </span>
            </div>
            <Button
              className="mt-4"
              size="sm"
              onClick={gerarPlanilhaDoPool}
              disabled={gerandoPlanilha || (data?.planilhas?.pool_novos ?? 0) === 0}
            >
              {gerandoPlanilha ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <FileSpreadsheet className="mr-2 h-4 w-4" />
              )}
              Gerar planilha do pool
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3 flex-row items-center justify-between space-y-0">
            <CardTitle className="flex items-center gap-2 text-base">
              <CloudDownload className="h-4 w-4 text-[hsl(var(--dunatech-blue))]" />
              Planilhas pendentes de subir no Legal One
            </CardTitle>
            <Button variant="ghost" size="sm" onClick={() => navigate("/distribuidos-bb")}>
              Ver todas
            </Button>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">{data?.planilhas?.pendentes ?? 0}</div>
            <p className="mt-1 text-sm text-muted-foreground">
              Planilhas já geradas que o operador ainda não marcou como subidas no Legal One.
            </p>
            <div className="mt-3 flex flex-col gap-2">
              {(data?.planilhas?.recentes_pendentes ?? []).length === 0 ? (
                <div className="rounded-md border bg-muted/30 p-3 text-xs text-muted-foreground">
                  Nenhuma planilha pendente de envio.
                </div>
              ) : (
                data!.planilhas!.recentes_pendentes.map((pl) => (
                  <div
                    key={pl.id}
                    className="flex items-center justify-between gap-2 rounded-md border bg-card px-3 py-2 text-sm"
                  >
                    <div className="min-w-0">
                      <div className="truncate font-mono text-xs">{pl.nome_arquivo}</div>
                      <div className="text-xs text-muted-foreground">
                        {pl.total_processos} processo(s) · {fmtDataCurta(pl.created_at)}
                      </div>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      className="shrink-0"
                      onClick={() => baixarPlanilhaArquivada(pl.id, pl.nome_arquivo)}
                    >
                      <Download className="mr-1.5 h-3.5 w-3.5" /> Baixar
                    </Button>
                  </div>
                ))
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Dialog de nova coleta */}
      <Dialog open={coletaOpen} onOpenChange={setColetaOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <CloudDownload className="h-5 w-5 text-[hsl(var(--dunatech-blue))]" />
              Nova coleta no portal BB
            </DialogTitle>
            <DialogDescription>
              O robô entra no portal (via OneLog), lê as notificações do intervalo e as registra. Datas em branco = hoje.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label className="text-xs">Data inicial</Label>
                <Input
                  type="date"
                  value={brParaIso(dataIni)}
                  onChange={(e) => setDataIni(isoParaBr(e.target.value))}
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Data final</Label>
                <Input
                  type="date"
                  value={brParaIso(dataFim)}
                  onChange={(e) => setDataFim(isoParaBr(e.target.value))}
                />
              </div>
            </div>
            <p className="-mt-1 text-xs text-muted-foreground">
              Clique no campo pra abrir o calendário. Deixe em branco = hoje.
            </p>

            <div className="flex items-start gap-2 rounded-md border p-3">
              <Checkbox
                id="coletar-envolvidos"
                checked={coletarEnvolvidos}
                onCheckedChange={(v) => setColetarEnvolvidos(v === true)}
                className="mt-0.5"
              />
              <div className="text-sm">
                <Label htmlFor="coletar-envolvidos" className="font-medium">
                  Capturar envolvidos (Pessoas do Processo)
                </Label>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Abre a capa do NPJ de cada processo e traz as partes com CPF/CNPJ, MCI e relação com o BB. Deixa a
                  coleta mais lenta.
                </p>
              </div>
            </div>

            <div className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50/60 p-3">
              <Checkbox
                id="confirmar-ciencia"
                checked={confirmarCiencia}
                onCheckedChange={(v) => setConfirmarCiencia(v === true)}
                className="mt-0.5"
              />
              <div className="text-sm">
                <Label htmlFor="confirmar-ciencia" className="flex items-center gap-1.5 font-medium text-amber-800">
                  <ShieldAlert className="h-4 w-4" />
                  Dar ciência (clicar SIM no BB)
                </Label>
                <p className="mt-0.5 text-xs text-amber-700">
                  Ação <strong>irreversível</strong> que inicia prazos. Só acontece se a trava global de segurança também
                  estiver ligada no servidor; caso contrário, roda em modo seguro (apenas coleta).
                </p>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setColetaOpen(false)}>
              Cancelar
            </Button>
            <Button onClick={iniciarColeta} disabled={disparando}>
              {disparando && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Iniciar coleta
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Importar lista (Ativos) — mesmo dialog da tela de Processos */}
      <ImportarAtivosDialog open={ativosOpen} onOpenChange={setAtivosOpen} onDone={load} />
    </div>
  );
}
