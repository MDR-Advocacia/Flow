// Seção "Reagendamentos" — vive DENTRO da página Minha Equipe.
//
// Mostra os ADIAMENTOS de prazo (tarefa empurrada pra frente durante o dia),
// detectados pelo bracket 07h/19h. Recortes: KPIs, por dia (série), por
// colaborador (ranking), por tarefa reincidente (a bola pra frente repetida) e
// por subtipo. O calo que era invisível.

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis,
} from "recharts";
import { AlertTriangle, ArrowRight, CalendarClock, Clock, ExternalLink, Loader2, Repeat, TrendingUp, X } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { InfoHint } from "@/components/performance/InfoHint";
import { type ReagEvento, type ReagResumo, getReagendamentoEventos, getReagendamentos } from "@/services/performance";
import { getDescricoes } from "@/services/balanceador";
import { useToast } from "@/hooks/use-toast";

const L1_URL = "https://mdradvocacia.novajus.com.br/processos/Processos/details";
const fmtDia = (iso: string) => {
  const [y, m, d] = iso.split("-");
  return `${d}/${m}`;
};
const fmtData = (iso: string | null) => {
  if (!iso) return "—";
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return iso.slice(0, 10);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(dt.getDate())}/${p(dt.getMonth() + 1)}/${dt.getFullYear()}`;
};

// Filtro de drill: qual barra foi clicada (pessoa / dia / subtipo).
type Drill =
  | { tipo: "pessoa"; pessoaId: number; label: string }
  | { tipo: "dia"; dia: string; label: string }
  | { tipo: "subtipo"; subtipo: string; label: string };

function Kpi({
  icone, valor, rotulo, hint, tone = "",
}: {
  icone: React.ReactNode; valor: React.ReactNode; rotulo: string; hint?: string; tone?: string;
}) {
  return (
    <div className="rounded-lg border p-3">
      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        {icone} {rotulo} {hint && <InfoHint text={hint} />}
      </div>
      <div className={`text-2xl font-bold ${tone}`}>{valor}</div>
    </div>
  );
}

export default function ReagendamentosSection({ team }: { team: string }) {
  const { toast } = useToast();
  const [data, setData] = useState<ReagResumo | null>(null);
  const [loading, setLoading] = useState(false);
  const [dias, setDias] = useState(30);

  // Drill: clicou numa barra → lista os processos daquele recorte embaixo.
  const [drill, setDrill] = useState<Drill | null>(null);
  const [eventos, setEventos] = useState<ReagEvento[]>([]);
  const [eventosTotal, setEventosTotal] = useState(0);
  const [eventosLoading, setEventosLoading] = useState(false);
  const [descMap, setDescMap] = useState<Record<number, string | null>>({});
  const [descLoading, setDescLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await getReagendamentos(team, dias));
    } catch (e) {
      toast({ title: "Erro ao carregar reagendamentos", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [team, dias, toast]);

  useEffect(() => { load(); }, [load]);
  // Troca de time/janela zera o drill (o recorte não faz mais sentido).
  useEffect(() => { setDrill(null); setEventos([]); setDescMap({}); }, [team, dias]);

  const abrirDrill = useCallback(async (d: Drill) => {
    setDrill(d);
    setEventosLoading(true);
    setDescMap({});
    try {
      const r = await getReagendamentoEventos(team, {
        days: dias, limit: 300,
        pessoaId: d.tipo === "pessoa" ? d.pessoaId : undefined,
        dia: d.tipo === "dia" ? d.dia : undefined,
        subtipo: d.tipo === "subtipo" ? d.subtipo : undefined,
      });
      setEventos(r.items);
      setEventosTotal(r.total);
      // Enriquece a DESCRIÇÃO ao vivo do L1 (não vive no snapshot) — best-effort.
      const ids = r.items.map((e) => e.l1_task_id).filter((x): x is number => !!x);
      if (ids.length) {
        setDescLoading(true);
        getDescricoes(team, ids.slice(0, 150))
          .then(setDescMap)
          .catch(() => undefined)
          .finally(() => setDescLoading(false));
      }
    } catch (e) {
      toast({ title: "Erro ao listar os reagendados", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setEventosLoading(false);
    }
  }, [team, dias, toast]);

  const k = data?.kpis;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          Adiamentos de prazo detectados comparando a foto das <b>07h</b> com a das <b>19h</b> de cada dia —
          o que a pessoa empurrou pra frente durante o expediente. Antecipar prazo não conta.
        </p>
        <Select value={String(dias)} onValueChange={(v) => setDias(Number(v))}>
          <SelectTrigger className="h-8 w-36 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="7">Últimos 7 dias</SelectItem>
            <SelectItem value="15">Últimos 15 dias</SelectItem>
            <SelectItem value="30">Últimos 30 dias</SelectItem>
            <SelectItem value="90">Últimos 90 dias</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {loading || !data ? (
        <p className="py-10 text-center text-sm text-muted-foreground">
          <Loader2 className="mr-1 inline h-4 w-4 animate-spin" /> Carregando…
        </p>
      ) : data.kpis.total === 0 ? (
        <Card><CardContent className="py-10 text-center text-sm text-muted-foreground">
          Nenhum adiamento no período. O bracket 07h/19h começa a acumular a partir da implantação —
          se acabou de subir, os dados aparecem no fim do primeiro dia.
        </CardContent></Card>
      ) : (
        <>
          {/* KPIs */}
          <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
            <Kpi icone={<CalendarClock className="h-3.5 w-3.5" />} valor={k?.total ?? "—"}
              rotulo="Adiamentos" hint="Total de vezes que um prazo foi empurrado pra frente no período." />
            <Kpi icone={<AlertTriangle className="h-3.5 w-3.5 text-rose-600" />} valor={k?.fatais_empurrados ?? "—"}
              rotulo="Fatais empurrados" tone="text-rose-700"
              hint="Tarefas que venciam HOJE de manhã e à noite já tinham prazo pra frente — o pior caso." />
            <Kpi icone={<Repeat className="h-3.5 w-3.5" />} valor={k?.tarefas ?? "—"}
              rotulo="Tarefas distintas" hint="Quantas tarefas diferentes foram adiadas ao menos uma vez." />
            <Kpi icone={<Clock className="h-3.5 w-3.5" />} valor={k ? `${k.dias_medio}d` : "—"}
              rotulo="Adiamento médio" hint="Média de dias que cada prazo foi empurrado." />
            <Kpi icone={<TrendingUp className="h-3.5 w-3.5" />} valor={k?.pessoas ?? "—"}
              rotulo="Colaboradores" hint="Quantas pessoas adiaram ao menos um prazo." />
          </div>

          {/* Por dia */}
          <Card><CardContent className="p-4">
            <div className="mb-2 text-sm font-semibold">Adiamentos por dia
              <span className="ml-1 text-[10px] font-normal text-muted-foreground">· clique num dia pra listar</span>
            </div>
            <ResponsiveContainer width="100%" height={Math.max(160, 200)}>
              <BarChart data={data.por_dia.map((d) => ({ ...d, label: fmtDia(d.dia), emdia: d.total - d.fatais }))}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="hsl(var(--border))" />
                <XAxis dataKey="label" fontSize={11} tickLine={false} axisLine={false} />
                <YAxis fontSize={11} allowDecimals={false} tickLine={false} axisLine={false} />
                <RTooltip cursor={{ fill: "hsl(var(--muted))", opacity: 0.4 }} />
                <Bar dataKey="fatais" name="Fatais empurrados" stackId="d" fill="#e11d48" cursor="pointer"
                  onClick={(d: any) => { const dia = d?.dia ?? d?.payload?.dia; if (dia) abrirDrill({ tipo: "dia", dia, label: `dia ${fmtDia(dia)}` }); }} />
                <Bar dataKey="emdia" name="Demais adiamentos" stackId="d" fill="#f59e0b" radius={[3, 3, 0, 0]} cursor="pointer"
                  onClick={(d: any) => { const dia = d?.dia ?? d?.payload?.dia; if (dia) abrirDrill({ tipo: "dia", dia, label: `dia ${fmtDia(dia)}` }); }} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent></Card>

          <div className="grid gap-4 lg:grid-cols-2">
            {/* Ranking por colaborador */}
            <Card><CardContent className="p-4">
              <div className="mb-2 flex items-center gap-1.5 text-sm font-semibold">
                Quem mais adia
                <InfoHint text="Ranking por nº de adiamentos. Reagendamento crônico costuma esconder tarefa que não vai sair." />
                <span className="text-[10px] font-normal text-muted-foreground">· clique numa pessoa pra listar</span>
              </div>
              <ResponsiveContainer width="100%" height={Math.max(200, data.por_pessoa.slice(0, 12).length * 26)}>
                <BarChart data={data.por_pessoa.slice(0, 12)} layout="vertical" margin={{ left: 8, right: 24 }}>
                  <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="hsl(var(--border))" />
                  <XAxis type="number" fontSize={11} allowDecimals={false} />
                  <YAxis type="category" dataKey="nome" width={130} fontSize={10}
                    tickFormatter={(n: string) => n.split(" ").slice(0, 2).join(" ")} tickLine={false} axisLine={false} />
                  <RTooltip cursor={{ fill: "hsl(var(--muted))", opacity: 0.4 }} />
                  <Bar dataKey="total" name="Adiamentos" radius={[0, 4, 4, 0]} cursor="pointer"
                    onClick={(d: any) => {
                      const p = d?.payload ?? d;
                      if (p?.pessoa_id) abrirDrill({ tipo: "pessoa", pessoaId: p.pessoa_id, label: p.nome });
                    }}>
                    {data.por_pessoa.slice(0, 12).map((p, i) => (
                      <Cell key={i} fill={p.fatais > 0 ? "#e11d48" : "#f59e0b"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
              <p className="mt-1 text-[10px] text-muted-foreground">Vermelho = tem fatal empurrado. Clique pra ver os processos.</p>
            </CardContent></Card>

            {/* Tarefas reincidentes */}
            <Card><CardContent className="p-4">
              <div className="mb-2 flex items-center gap-1.5 text-sm font-semibold">
                Bola pra frente repetida
                <InfoHint text="A MESMA tarefa adiada em vários dias distintos — o pior caso: prazo que só anda pra frente." />
              </div>
              <div className="max-h-[340px] overflow-y-auto rounded border">
                <Table>
                  <TableHeader className="sticky top-0 bg-background">
                    <TableRow>
                      <TableHead>Tarefa</TableHead>
                      <TableHead>Responsável</TableHead>
                      <TableHead className="text-right">Vezes</TableHead>
                      <TableHead className="text-right">Total</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.reincidentes.length === 0 && (
                      <TableRow><TableCell colSpan={4} className="py-8 text-center text-xs text-muted-foreground">
                        Nenhuma tarefa adiada em mais de um dia (ainda).
                      </TableCell></TableRow>
                    )}
                    {data.reincidentes.map((r) => (
                      <TableRow key={r.l1_task_id}>
                        <TableCell className="text-xs">
                          <a href={`${L1_URL}/${r.l1_task_id}`} target="_blank" rel="noreferrer"
                            className="font-medium text-primary hover:underline">
                            {r.pasta || r.cnj || `#${r.l1_task_id}`}
                          </a>
                          <div className="text-[10px] text-muted-foreground">{(r.subtipo || "—")}</div>
                        </TableCell>
                        <TableCell className="text-xs">{r.pessoa || "—"}</TableCell>
                        <TableCell className="text-right font-semibold tabular-nums">{r.vezes}×</TableCell>
                        <TableCell className="text-right tabular-nums text-rose-700">+{r.dias_total}d</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </CardContent></Card>
          </div>

          {/* Por subtipo */}
          {data.por_subtipo.length > 0 && (
            <Card><CardContent className="p-4">
              <div className="mb-2 text-sm font-semibold">Adiamentos por tipo de tarefa
                <span className="ml-1 text-[10px] font-normal text-muted-foreground">· clique num tipo pra listar</span>
              </div>
              <div className="space-y-1">
                {data.por_subtipo.map((s) => (
                  <button key={s.subtipo} type="button"
                    onClick={() => abrirDrill({ tipo: "subtipo", subtipo: s.subtipo, label: s.subtipo })}
                    className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left text-xs transition-colors hover:bg-muted/60">
                    <div className="w-56 truncate" title={s.subtipo}>{s.subtipo}</div>
                    <div className="h-3 flex-1 overflow-hidden rounded-full bg-muted">
                      <div className="h-full bg-amber-400" style={{ width: `${(s.total / data.por_subtipo[0].total) * 100}%` }} />
                    </div>
                    <div className="w-10 text-right font-semibold tabular-nums">{s.total}</div>
                  </button>
                ))}
              </div>
            </CardContent></Card>
          )}

          {/* Listagem do DRILL — processos reagendados do recorte clicado */}
          {drill && (
            <Card className="border-primary/40">
              <CardContent className="p-4">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <div className="text-sm font-semibold">
                    Processos reagendados — {drill.label}
                    <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                      {eventosLoading ? "carregando…" : `${eventosTotal} evento(s)`}
                      {descLoading && " · buscando descrições no L1…"}
                    </span>
                  </div>
                  <Button variant="ghost" size="sm" className="h-7 gap-1 text-xs" onClick={() => setDrill(null)}>
                    <X className="h-3.5 w-3.5" /> Fechar
                  </Button>
                </div>
                {eventosLoading ? (
                  <p className="py-8 text-center text-sm text-muted-foreground">
                    <Loader2 className="mr-1 inline h-4 w-4 animate-spin" /> Carregando…
                  </p>
                ) : eventos.length === 0 ? (
                  <p className="py-8 text-center text-sm text-muted-foreground">Nenhum processo neste recorte.</p>
                ) : (
                  <div className="max-h-[460px] overflow-auto rounded border">
                    <Table>
                      <TableHeader className="sticky top-0 z-10 bg-background">
                        <TableRow>
                          <TableHead>Processo / descrição</TableHead>
                          {drill.tipo !== "pessoa" && <TableHead>Responsável</TableHead>}
                          {drill.tipo !== "dia" && <TableHead>Dia</TableHead>}
                          <TableHead>De → Para</TableHead>
                          <TableHead className="text-right">Adiou</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {eventos.map((e, i) => (
                          <TableRow key={`${e.l1_task_id}-${i}`} className={e.era_fatal_hoje ? "bg-rose-50/40" : ""}>
                            <TableCell className="max-w-[440px] text-xs">
                              <a href={`${L1_URL}/${e.l1_task_id}`} target="_blank" rel="noreferrer"
                                className="font-medium text-primary hover:underline">
                                {(e.l1_task_id && descMap[e.l1_task_id]) || e.pasta || e.cnj || `#${e.l1_task_id}`}
                                <ExternalLink className="ml-1 inline h-3 w-3" />
                              </a>
                              <div className="text-[10px] text-muted-foreground">
                                {[e.subtipo, e.cnj || e.pasta].filter(Boolean).join(" · ")}
                                {e.era_fatal_hoje && <span className="ml-1 font-medium text-rose-600">· era fatal no dia</span>}
                              </div>
                            </TableCell>
                            {drill.tipo !== "pessoa" && <TableCell className="text-xs">{e.pessoa || "—"}</TableCell>}
                            {drill.tipo !== "dia" && <TableCell className="whitespace-nowrap text-xs tabular-nums">{fmtDia(e.dia)}</TableCell>}
                            <TableCell className="whitespace-nowrap text-xs tabular-nums">
                              <span className="text-muted-foreground">{fmtData(e.prazo_de)}</span>
                              <ArrowRight className="mx-1 inline h-3 w-3 text-muted-foreground" />
                              <span className="font-medium">{fmtData(e.prazo_para)}</span>
                            </TableCell>
                            <TableCell className="text-right font-semibold tabular-nums text-rose-700">+{e.dias_adiado}d</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
