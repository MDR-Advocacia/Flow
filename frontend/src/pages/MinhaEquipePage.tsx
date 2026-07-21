// frontend/src/pages/MinhaEquipePage.tsx
//
// "Minha Equipe" — dashboard de desempenho por pessoa a partir das tarefas do
// Legal One. Estilo do OneRequest (responsivo, cards modernos). CADA métrica
// carrega um (?) explicativo (componente InfoHint). Restrito a administradores.
//
// Filosofia: cadência/ócio só são confiáveis no trabalho operacional; trabalho
// profundo mede-se por volume + cycle time + prazo. O detalhe da pessoa sempre
// informa a "fatia operacional" pra deixar claro quanto da leitura é confiável.

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { teamLabel } from "@/lib/teams";
import {
  Activity,
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  Clock,
  Database,
  FileText,
  Gauge,
  Layers,
  type LucideIcon,
  RefreshCw,
  Search,
  UserCog,
  Users,
} from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import { InfoHint, MetricLabel } from "@/components/performance/InfoHint";
import PainelEquipe from "@/components/performance/PainelEquipe";
import CollapsibleSection from "@/components/performance/CollapsibleSection";
import RaioXPessoa from "@/components/performance/RaioXPessoa";
import RelatoriosSection from "@/components/performance/RelatoriosSection";
import RosterEditor from "@/components/performance/RosterEditor";
import BalanceadorSection from "@/components/performance/BalanceadorSection";
import RedistribuicoesLog from "@/components/balanceador/RedistribuicoesLog";
import AcompanhamentoVinculosTab from "@/components/distribuidos-bb/AcompanhamentoVinculosTab";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/hooks/useAuth";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  type Categoria,
  type EquipeResponse,
  type PessoaDetalhe,
  type SyncStatus,
  criarRelatorio,
  getCargos,
  getEquipe,
  getSyncStatus,
  triggerSync,
} from "@/services/performance";

const PERIODOS = [7, 14, 30, 60, 90, 180];

const HINTS: Record<string, string> = {
  concluido:
    "Tarefas marcadas como cumpridas no período, pela data de conclusão efetiva no Legal One.",
  throughput:
    "Média de tarefas concluídas por dia ATIVO — dias em que a pessoa concluiu ao menos uma tarefa. Mostra o ritmo real de produção, descontando folgas e ausências.",
  no_prazo:
    "Percentual das tarefas concluídas dentro do prazo previsto (conclusão ≤ data prevista no L1). Mede cumprimento de prazo. '—' quando as tarefas não têm prazo definido.",
  cycle:
    "Mediana do tempo entre o cadastro da tarefa e a sua conclusão. É quanto tempo a tarefa fica 'viva' até ser resolvida — quanto menor, mais ágil.",
  backlog:
    "Tarefas ainda abertas (não concluídas) sob responsabilidade da pessoa. É a fila em aberto — backlog alto significa risco de atraso.",
  composicao:
    "Divisão das tarefas concluídas por natureza. Operacional = alta frequência, feita em lote (ex.: Agendar Prazos). Profundo = trabalho pesado e esparso (ex.: Contestação). Ruído = tipos raros. A métrica adequada muda conforme a natureza.",
  kpi_concluido: "Total de tarefas concluídas por toda a equipe no período selecionado.",
  kpi_backlog: "Total de tarefas pendentes (abertas) da equipe — a carga em aberto.",
  kpi_no_prazo: "Percentual das tarefas da equipe concluídas dentro do prazo previsto, no período.",
  kpi_ativas:
    "Pessoas que concluíram ao menos uma tarefa no período, sobre o total do roster da equipe.",
  cadencia:
    "Tempo mediano entre uma conclusão e a próxima, no mesmo dia (intervalos acima de 30 min contam como pausa, não como tempo de tarefa). É o 'custo de tempo' por tarefa. Confiável sobretudo em trabalho operacional.",
  ocio:
    "Percentual do tempo da jornada (entre a 1ª e a última conclusão do dia) gasto em pausas, não em concluir tarefas. Leitura mais fiel em quem é majoritariamente operacional.",
  oper_share:
    "Quanto das conclusões da pessoa é de tarefas operacionais. Quanto maior, mais confiável a leitura de cadência e ócio — em trabalho profundo, o tempo 'parado' costuma ser trabalho não cronometrado, e não ócio.",
  categoria:
    "Natureza do tipo de tarefa: operacional (cadência/ócio valem), profundo (medir por volume/ciclo/prazo) ou ruído (tipo raro, fora das métricas finas).",
};

const CAT_STYLE: Record<Categoria, { label: string; cls: string; dot: string }> = {
  operacional: { label: "Operacional", cls: "bg-sky-100 text-sky-700", dot: "#378ADD" },
  profundo: { label: "Profundo", cls: "bg-emerald-100 text-emerald-700", dot: "#1D9E75" },
  ruido: { label: "Ruído", cls: "bg-slate-100 text-slate-600", dot: "#94a3b8" },
};

function cargoBadge(cargo: string | null): string {
  const c = (cargo || "").toLowerCase();
  if (c.includes("advog")) return "bg-violet-100 text-violet-700";
  if (c.includes("estag")) return "bg-sky-100 text-sky-700";
  if (c.includes("assist")) return "bg-amber-100 text-amber-700";
  return "bg-slate-100 text-slate-700";
}

const fmtCad = (s: number | null): string =>
  s == null ? "—" : s < 60 ? `${s}s` : s % 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${Math.floor(s / 60)}m`;
const pct = (v: number | null): string => (v == null ? "—" : `${v}%`);
const diasFmt = (v: number | null): string => (v == null ? "—" : `${v}d`);

function CompBar({ oper, prof, ruido }: { oper: number; prof: number; ruido: number }) {
  const total = oper + prof + ruido || 1;
  const seg = (n: number, color: string) =>
    n > 0 ? <div className="h-full" style={{ width: `${(100 * n) / total}%`, background: color }} /> : null;
  return (
    <div
      className="flex h-2 w-full min-w-[64px] overflow-hidden rounded-full bg-muted"
      title={`Operacional ${oper} · Profundo ${prof} · Ruído ${ruido}`}
    >
      {seg(oper, "#378ADD")}
      {seg(prof, "#1D9E75")}
      {seg(ruido, "#94a3b8")}
    </div>
  );
}

function Kpi({
  label,
  hint,
  value,
  icon: Icon,
  tone,
}: {
  label: string;
  hint: string;
  value: string | number;
  icon: LucideIcon;
  tone: string;
}) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3 p-4">
        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full ${tone}`}>
          <Icon className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <div className="text-2xl font-bold leading-none">{value}</div>
          <div className="truncate text-xs text-muted-foreground">
            <MetricLabel label={label} hint={hint} />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export default function MinhaEquipePage() {
  const { toast } = useToast();
  const { team = "bb-reu" } = useParams<{ team: string }>();
  const [days, setDays] = useState(30);
  const [cargo, setCargo] = useState<string | null>(null);
  const [cargos, setCargos] = useState<string[]>([]);
  const [data, setData] = useState<EquipeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);
  const [buscaOpen, setBuscaOpen] = useState(false);
  const [gerandoRel, setGerandoRel] = useState(false);
  const [relReloadKey, setRelReloadKey] = useState(0);
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [rosterOpen, setRosterOpen] = useState(false);
  // Equipe Mista tem uma aba extra com a base de processos vinculados.
  const ehMista = team === "equipe-mista";
  const [abaMista, setAbaMista] = useState<"desempenho" | "base">("desempenho");

  useEffect(() => {
    getCargos(team).then(setCargos).catch(() => undefined);
    getSyncStatus().then(setSyncStatus).catch(() => undefined);
  }, [team]);

  const handleSyncNow = async () => {
    setSyncing(true);
    const prev = syncStatus?.last_sync?.em ?? null;
    try {
      await triggerSync();
      toast({
        title: "Atualização disparada",
        description: "Baixando o relatório do L1 e atualizando os dados — roda no servidor (~1–2 min).",
      });
      let tries = 0;
      const poll = setInterval(async () => {
        tries += 1;
        try {
          const st = await getSyncStatus();
          setSyncStatus(st);
          if (st.last_sync?.em && st.last_sync.em !== prev) {
            clearInterval(poll);
            setSyncing(false);
            toast({ title: "Dados atualizados!", description: `${st.last_sync.tarefas} tarefas · relatório de ${st.last_sync.data}.` });
            load();
          }
        } catch {
          /* ignore */
        }
        if (tries > 24) {
          clearInterval(poll);
          setSyncing(false);
        }
      }, 15000);
    } catch (e) {
      toast({ title: "Erro ao disparar atualização", description: String((e as Error).message), variant: "destructive" });
      setSyncing(false);
    }
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await getEquipe(team, days, cargo || undefined));
    } catch (e) {
      toast({ title: "Erro ao carregar a equipe", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [team, days, cargo, toast]);

  useEffect(() => {
    load();
  }, [load]);

  const handleRelatorioSetor = async () => {
    setGerandoRel(true);
    try {
      await criarRelatorio("setor", team, days);
      setRelReloadKey((k) => k + 1);
      toast({
        title: "Relatório do setor em geração",
        description: "Roda no servidor — pode sair da tela. Aparece na seção 'Relatórios' quando ficar pronto.",
      });
    } catch (e) {
      toast({ title: "Erro ao gerar o relatório", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setGerandoRel(false);
    }
  };

  const k = data?.kpis;
  const pessoas = data?.pessoas ?? [];

  const filtros = useMemo(() => [null, ...cargos], [cargos]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/70">
            Minha Equipe
          </div>
          <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
            <Users className="h-6 w-6 text-[hsl(var(--dunatech-blue))]" />
            {teamLabel(team)}
          </h1>
          <p className="max-w-2xl text-sm text-muted-foreground">
            Desempenho de cada pessoa a partir das tarefas do Legal One — produção, ritmo, prazo e
            carga. Passe o mouse no{" "}
            <span className="inline-flex translate-y-0.5">
              <InfoHint text="É assim que cada termo é explicado: passe o mouse para entender o que a medição significa." />
            </span>{" "}
            de cada métrica pra entender o que ela significa.
          </p>
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Database className="h-3.5 w-3.5" />
            {syncStatus?.last_sync ? (
              <span>
                Atualizado{" "}
                <span className="font-medium text-foreground">
                  {syncStatus.last_sync.data}
                  {syncStatus.last_sync.em
                    ? ` ${new Date(syncStatus.last_sync.em).toLocaleTimeString("pt-BR", {
                        hour: "2-digit",
                        minute: "2-digit",
                      })}`
                    : ""}
                </span>{" "}
                · {syncStatus.last_sync.tarefas.toLocaleString("pt-BR")} tarefas
              </span>
            ) : (
              <span>Sincronização automática diária (9h)</span>
            )}
            <Button
              variant="ghost"
              size="sm"
              className="h-6 gap-1 px-2 text-xs"
              onClick={handleSyncNow}
              disabled={syncing}
            >
              <RefreshCw className={`h-3 w-3 ${syncing ? "animate-spin" : ""}`} />
              {syncing ? "Atualizando…" : "Atualizar agora"}
            </Button>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {/* Quem enxerga a página deste time já passou pelo gate por-time —
              o ajuste de equipe acompanha (antes era só admin; o backend
              valida de novo, inclusive ao mover pessoa pra outro time). */}
          <Button variant="outline" className="gap-2" onClick={() => setRosterOpen(true)}>
            <UserCog className="h-4 w-4" /> Ajustar equipe
          </Button>
          <Button variant="outline" className="gap-2" onClick={() => setBuscaOpen(true)}>
            <Search className="h-4 w-4" /> Análise individual
          </Button>
          <Button variant="outline" className="gap-2" onClick={handleRelatorioSetor} disabled={gerandoRel}>
            <FileText className="h-4 w-4" /> {gerandoRel ? "Gerando…" : "Relatório do setor"}
          </Button>
          <Select value={String(days)} onValueChange={(v) => setDays(Number(v))}>
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PERIODOS.map((p) => (
                <SelectItem key={p} value={String(p)}>
                  Últimos {p} dias
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="outline" size="icon" onClick={() => load()} disabled={loading}>
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </Button>
        </div>
      </div>

      {/* Equipe Mista: além do desempenho padrão, uma aba com a BASE identificada
          (processos vinculados que a equipe conduz). Nos demais times, render direto. */}
      {ehMista ? (
        <Tabs value={abaMista} onValueChange={(v) => setAbaMista(v as "desempenho" | "base")}>
          <TabsList>
            <TabsTrigger value="desempenho">
              <Gauge className="mr-1.5 h-4 w-4" /> Desempenho
            </TabsTrigger>
            <TabsTrigger value="base">
              <Layers className="mr-1.5 h-4 w-4" /> Base identificada
            </TabsTrigger>
          </TabsList>
        </Tabs>
      ) : null}

      {ehMista && abaMista === "base" && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Processos que a Equipe Mista conduz por <strong>vínculo da parte</strong> — a mesma
            pessoa/empresa tem mais de uma ação ativa com o escritório. A base cresce com a
            captura diária e com o estoque distribuído por dentro do Legal One.
          </p>
          <AcompanhamentoVinculosTab />
        </div>
      )}

      {(!ehMista || abaMista === "desempenho") && (
      <>
      <CollapsibleSection title="Desempenho da equipe" subtitle={`Últimos ${days} dias`}>
      {/* KPIs */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Kpi label="Concluídas" hint={HINTS.kpi_concluido} value={k?.concluido ?? 0} icon={CheckCircle2} tone="bg-emerald-100 text-emerald-700" />
        <Kpi label="No prazo" hint={HINTS.kpi_no_prazo} value={pct(k?.no_prazo_pct ?? null)} icon={CalendarClock} tone="bg-sky-100 text-sky-700" />
        <Kpi label="Backlog" hint={HINTS.kpi_backlog} value={k?.backlog ?? 0} icon={Clock} tone="bg-amber-100 text-amber-700" />
        <Kpi label="Pessoas ativas" hint={HINTS.kpi_ativas} value={`${k?.pessoas_ativas ?? 0}/${k?.pessoas_total ?? 0}`} icon={Activity} tone="bg-violet-100 text-violet-700" />
        <Kpi label="Tarefas/pessoa" hint="Média de tarefas concluídas por pessoa ativa no período." value={k && k.pessoas_ativas ? Math.round(k.concluido / k.pessoas_ativas) : 0} icon={Gauge} tone="bg-slate-100 text-slate-700" />
      </div>

      {/* Painel do setor — gráficos (vazão, pool/atraso, jornada, top tarefas) */}
      <PainelEquipe days={days} team={team} />

      {/* ───── Equipe (tabela detalhada por pessoa) ───── */}
      <div className="flex items-center gap-2 pt-2">
        <Users className="h-5 w-5 text-muted-foreground" />
        <h2 className="text-lg font-semibold">Equipe — detalhe por pessoa</h2>
      </div>

      {/* Filtro por cargo */}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-xs font-medium text-muted-foreground">Cargo:</span>
        {filtros.map((c) => {
          const active = cargo === c;
          return (
            <button
              key={c ?? "todos"}
              type="button"
              onClick={() => setCargo(c)}
              className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                active
                  ? "border-transparent bg-foreground text-background"
                  : "bg-background text-muted-foreground hover:bg-muted"
              }`}
            >
              {c ?? "Todos"}
            </button>
          );
        })}
      </div>

      {/* Tabela da equipe */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <CardTitle className="text-base">
            Equipe <span className="font-normal text-muted-foreground">({pessoas.length})</span>
          </CardTitle>
          <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
            <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: "#378ADD" }} /> Operacional</span>
            <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: "#1D9E75" }} /> Profundo</span>
            <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: "#94a3b8" }} /> Ruído</span>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Pessoa</TableHead>
                  <TableHead className="text-right"><MetricLabel className="justify-end" label="Concluídas" hint={HINTS.concluido} /></TableHead>
                  <TableHead className="text-right"><MetricLabel className="justify-end" label="Ritmo/dia" hint={HINTS.throughput} /></TableHead>
                  <TableHead className="text-right"><MetricLabel className="justify-end" label="No prazo" hint={HINTS.no_prazo} /></TableHead>
                  <TableHead className="text-right"><MetricLabel className="justify-end" label="Cycle time" hint={HINTS.cycle} /></TableHead>
                  <TableHead className="text-right"><MetricLabel className="justify-end" label="Backlog" hint={HINTS.backlog} /></TableHead>
                  <TableHead className="min-w-[120px]"><MetricLabel label="Composição" hint={HINTS.composicao} /></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {pessoas.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={7} className="py-10 text-center text-muted-foreground">
                      {loading ? "Carregando…" : "Nenhuma pessoa no período."}
                    </TableCell>
                  </TableRow>
                ) : (
                  pessoas.map((p) => (
                    <TableRow
                      key={p.id}
                      className="cursor-pointer"
                      onClick={() => setSelected(p.id)}
                    >
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium">{p.nome}</span>
                          {p.cargo && (
                            <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${cargoBadge(p.cargo)}`}>
                              {p.cargo.replace("(a)", "").replace("(a)", "")}
                            </span>
                          )}
                          {p.squad && (
                            <span className="text-[10px] text-muted-foreground">sq{p.squad}</span>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="text-right font-medium tabular-nums">{p.concluido}</TableCell>
                      <TableCell className="text-right tabular-nums">{p.throughput_dia}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {p.no_prazo_pct == null ? (
                          "—"
                        ) : (
                          <span className={p.no_prazo_pct < 50 ? "font-medium text-rose-700" : ""}>{p.no_prazo_pct}%</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{diasFmt(p.cycle_dias)}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {p.backlog > 0 ? <span className="font-medium text-amber-700">{p.backlog}</span> : p.backlog}
                      </TableCell>
                      <TableCell>
                        <CompBar oper={p.operacional_n} prof={p.profundo_n} ruido={p.ruido_n} />
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      </CollapsibleSection>

      <CollapsibleSection title="Balanceamento de agenda" subtitle="Diagnóstico de carga + redistribuição" defaultOpen={false}>
        <BalanceadorSection team={team} onAplicado={() => setRelReloadKey((k) => k + 1)} />
      </CollapsibleSection>

      <CollapsibleSection title="Relatórios" subtitle="PDF do servidor + log de redistribuições">
        <RelatoriosSection reloadKey={relReloadKey} />
        <RedistribuicoesLog team={team} reloadKey={relReloadKey} />
      </CollapsibleSection>
      </>
      )}

      <CommandDialog open={buscaOpen} onOpenChange={setBuscaOpen}>
        <CommandInput placeholder="Buscar pessoa pelo nome…" />
        <CommandList>
          <CommandEmpty>Ninguém encontrado.</CommandEmpty>
          <CommandGroup heading="Equipe">
            {pessoas.map((p) => (
              <CommandItem
                key={p.id}
                value={p.nome}
                onSelect={() => {
                  setSelected(p.id);
                  setBuscaOpen(false);
                }}
              >
                <span>{p.nome}</span>
                {p.cargo && <span className="ml-2 text-xs text-muted-foreground">{p.cargo}</span>}
              </CommandItem>
            ))}
          </CommandGroup>
        </CommandList>
      </CommandDialog>

      <RaioXPessoa
        pessoaId={selected}
        team={team}
        days={days}
        onClose={() => setSelected(null)}
        onRelatorioCriado={() => setRelReloadKey((k) => k + 1)}
      />

      <RosterEditor team={team} open={rosterOpen} onClose={() => setRosterOpen(false)} onChanged={load} />
    </div>
  );
}
