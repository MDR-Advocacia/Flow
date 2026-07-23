// Seção "Balanceamento de agenda" — vive DENTRO da página Minha Equipe (seção
// recolhível). Diagnóstico de carga por colaborador + redistribuição.
// MOCK (2026-06-29): leitura real do pool; escrita simulada.

import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, ArrowLeftRight, CalendarClock, CalendarRange, Clock, Loader2, Star, X } from "lucide-react";
import { type DateRange } from "react-day-picker";

import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import { type Colaborador, type FaixaData, getDiagnostico, listarExecucoes } from "@/services/balanceador";
import ExecucoesDialog from "@/components/balanceador/ExecucoesDialog";
import RedistribuicaoModal from "@/components/balanceador/RedistribuicaoModal";

// Data local → YYYY-MM-DD (sem UTC shift — usa o fuso do navegador).
const toISO = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const fmtBR = (d: Date) => `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}/${d.getFullYear()}`;
const addDays = (d: Date, n: number) => {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
};

function cargoBadge(cargo: string | null): string {
  const c = (cargo || "").toLowerCase();
  if (c.includes("superv")) return "bg-indigo-100 text-indigo-700";
  if (c.includes("advog")) return "bg-violet-100 text-violet-700";
  if (c.includes("estag")) return "bg-sky-100 text-sky-700";
  if (c.includes("assist")) return "bg-amber-100 text-amber-700";
  return "bg-slate-100 text-slate-700";
}

function Bar({ a, f, fut }: { a: number; f: number; fut: number }) {
  const tot = a + f + fut || 1;
  return (
    <div className="flex h-2 w-28 overflow-hidden rounded-full bg-muted">
      <div className="bg-rose-500" style={{ width: `${(a / tot) * 100}%` }} />
      <div className="bg-amber-400" style={{ width: `${(f / tot) * 100}%` }} />
      <div className="bg-emerald-400" style={{ width: `${(fut / tot) * 100}%` }} />
    </div>
  );
}

export default function BalanceadorSection({ team, onAplicado }: { team: string; onAplicado?: () => void }) {
  const { toast } = useToast();
  const [data, setData] = useState<Colaborador[]>([]);
  const [loading, setLoading] = useState(false);
  const [sel, setSel] = useState<Set<number>>(new Set());
  // DOIS recortes de data INDEPENDENTES (a confusão anterior era ter um só):
  //  - tableRange: filtra a TABELA de baixo (análise do estoque por dia). Default
  //    vazio = todas as pendentes.
  //  - redistRange: faixa da REDISTRIBUIÇÃO, escolhida no modal do botão. Default
  //    hoje → hoje+30.
  const [tableRange, setTableRange] = useState<DateRange | undefined>(undefined);
  const [tableCalOpen, setTableCalOpen] = useState(false);
  const [redistRange, setRedistRange] = useState<DateRange | undefined>(() => {
    const hoje = new Date();
    return { from: hoje, to: addDays(hoje, 30) };
  });
  const [faixaModalOpen, setFaixaModalOpen] = useState(false);
  const [cargo, setCargo] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [execucoesOpen, setExecucoesOpen] = useState(false);
  const [temRodando, setTemRodando] = useState(false);

  // Faixa da TABELA (pro getDiagnostico) — só quando início e fim escolhidos.
  const tableFaixa: FaixaData | null = useMemo(() => {
    if (!tableRange?.from || !tableRange?.to) return null;
    return { inicio: toISO(tableRange.from), fim: toISO(tableRange.to) };
  }, [tableRange]);

  // Sinaliza no botão "Execuções" se há redistribuição rodando em 2º plano
  // (poll leve de 20s — o acompanhamento fino é dentro do painel).
  useEffect(() => {
    let vivo = true;
    const check = () =>
      listarExecucoes(team, 1, 0)
        .then((r) => vivo && setTemRodando(r.items.some((j) => j.status !== "done")))
        .catch(() => undefined);
    check();
    const t = setInterval(check, 20_000);
    return () => {
      vivo = false;
      clearInterval(t);
    };
  }, [team]);

  const load = useCallback(async () => {
    setLoading(true);
    setSel(new Set());
    try {
      setData(await getDiagnostico(team, tableFaixa));
    } catch (e) {
      toast({ title: "Erro ao carregar o diagnóstico", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [team, tableFaixa, toast]);

  useEffect(() => {
    load();
  }, [load]);

  const cargos = useMemo(
    () => Array.from(new Set(data.map((d) => d.cargo).filter(Boolean))) as string[],
    [data],
  );
  const dataView = useMemo(() => (cargo ? data.filter((d) => d.cargo === cargo) : data), [data, cargo]);
  const totais = useMemo(
    () =>
      dataView.reduce(
        (s, d) => ({ atrasado: s.atrasado + d.atrasado, fatal: s.fatal + d.fatal_hoje, futuro: s.futuro + d.futuro }),
        { atrasado: 0, fatal: 0, futuro: 0 },
      ),
    [dataView],
  );

  const toggle = (id: number) =>
    setSel((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });

  const selecionados = useMemo(
    () => data.filter((d) => sel.has(d.id)).map((d) => ({ id: d.id, nome: d.nome })),
    [data, sel],
  );

  // Faixa da REDISTRIBUIÇÃO (escolhida no modal do botão Redistribuir).
  const redistFaixa: FaixaData | null = useMemo(() => {
    if (!redistRange?.from || !redistRange?.to) return null;
    return { inicio: toISO(redistRange.from), fim: toISO(redistRange.to) };
  }, [redistRange]);
  const rangeLabel = (r: DateRange | undefined, vazio: string) =>
    r?.from ? (r.to ? `${fmtBR(r.from)} – ${fmtBR(r.to)}` : `${fmtBR(r.from)} – …`) : vazio;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          Carga pendente de cada colaborador. Filtre a tabela por data pra analisar o estoque de um dia
          (ou dias anteriores/futuros); selecione quem rebalancear e clique em Redistribuir — a faixa da
          redistribuição é escolhida lá.
          <span className="ml-1 text-emerald-700">Leitura e escrita ao vivo no L1.</span>
        </p>
        <Button size="sm" variant="outline" className="relative h-7 gap-1.5 text-xs" onClick={() => setExecucoesOpen(true)}>
          <Activity className="h-3.5 w-3.5" /> Execuções
          {temRodando && (
            <span className="absolute -right-1 -top-1 flex h-2.5 w-2.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-400 opacity-75" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-blue-500" />
            </span>
          )}
        </Button>
      </div>

      {/* KPIs do time */}
      <div className="grid grid-cols-3 gap-2">
        <div className="rounded-lg border bg-rose-50/50 p-3">
          <div className="flex items-center gap-1.5 text-[11px] text-rose-700"><AlertTriangle className="h-3.5 w-3.5" /> Atrasadas</div>
          <div className="text-2xl font-bold text-rose-700">{totais.atrasado}</div>
        </div>
        <div className="rounded-lg border bg-amber-50/50 p-3">
          <div className="flex items-center gap-1.5 text-[11px] text-amber-800"><CalendarClock className="h-3.5 w-3.5" /> Fatais hoje</div>
          <div className="text-2xl font-bold text-amber-800">{totais.fatal}</div>
        </div>
        <div className="rounded-lg border bg-emerald-50/50 p-3">
          <div className="flex items-center gap-1.5 text-[11px] text-emerald-700"><Clock className="h-3.5 w-3.5" /> Futuras</div>
          <div className="text-2xl font-bold text-emerald-700">{totais.futuro}</div>
        </div>
      </div>

      {/* controles de redistribuição */}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-muted/30 px-3 py-2">
        <span className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
          {sel.size > 0 ? `${sel.size} colaborador(es) selecionado(s)` : "Selecione colaboradores na tabela"}
          {sel.size > 0 && (
            <button
              type="button"
              onClick={() => setSel(new Set())}
              className="inline-flex items-center gap-0.5 rounded-full border px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              title="Desmarca todos os colaboradores selecionados"
            >
              <X className="h-3 w-3" /> Limpar seleção
            </button>
          )}
        </span>
        <Button
          size="sm"
          className="gap-1.5"
          disabled={sel.size === 0}
          title={sel.size === 0 ? "Selecione colaboradores na tabela" : "Escolher a faixa e redistribuir"}
          onClick={() => setFaixaModalOpen(true)}
        >
          <ArrowLeftRight className="h-4 w-4" /> Redistribuir
        </Button>
      </div>

      {/* filtro de DATA da tabela — análise do estoque pendente por conclusão
          prevista (hoje, dias anteriores, futuros). Independente da faixa da
          redistribuição. */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium text-muted-foreground">Estoque por data:</span>
        <Popover open={tableCalOpen} onOpenChange={setTableCalOpen}>
          <PopoverTrigger asChild>
            <Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs font-normal"
              title="Filtra a tabela pela DATA DE CONCLUSÃO PREVISTA das pendentes.">
              <CalendarRange className="h-3.5 w-3.5" /> {rangeLabel(tableRange, "Todas as pendentes")}
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-auto p-0" align="start">
            <div className="border-b px-3 py-2 text-[11px] text-muted-foreground">
              Filtra por <b>data de conclusão prevista</b> — veja o que cai num dia ou numa faixa.
            </div>
            <Calendar mode="range" numberOfMonths={2} selected={tableRange}
              onSelect={setTableRange} defaultMonth={tableRange?.from ?? new Date()} />
          </PopoverContent>
        </Popover>
        {tableFaixa && (
          <button type="button" onClick={() => setTableRange(undefined)}
            className="inline-flex items-center gap-0.5 rounded-full border px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">
            <X className="h-3 w-3" /> Limpar filtro
          </button>
        )}
      </div>

      {/* filtro por cargo */}
      {cargos.length > 1 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-xs font-medium text-muted-foreground">Cargo:</span>
          {[null, ...cargos].map((c) => {
            const active = cargo === c;
            return (
              <button
                key={c ?? "todos"}
                type="button"
                onClick={() => setCargo(c)}
                className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                  active ? "border-transparent bg-foreground text-background" : "bg-background text-muted-foreground hover:bg-muted"
                }`}
              >
                {c ?? "Todos"}
              </button>
            );
          })}
        </div>
      )}

      {loading ? (
        <p className="py-10 text-center text-sm text-muted-foreground">
          <Loader2 className="mr-1 inline h-4 w-4 animate-spin" /> Carregando…
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" />
                <TableHead>Colaborador</TableHead>
                <TableHead className="text-right">Atrasadas</TableHead>
                <TableHead className="text-right">Fatais hoje</TableHead>
                <TableHead className="text-right">Futuras</TableHead>
                <TableHead className="text-right">Total</TableHead>
                <TableHead>Mix</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {dataView.map((d) => (
                <TableRow
                  key={d.id}
                  className={`cursor-pointer ${sel.has(d.id) ? "bg-muted/50" : ""}`}
                  onClick={() => toggle(d.id)}
                >
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <Checkbox checked={sel.has(d.id)} onCheckedChange={() => toggle(d.id)} />
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1.5 text-sm font-medium">
                      {d.is_supervisor && <Star className="h-3.5 w-3.5 fill-indigo-400 text-indigo-400" />}
                      {d.nome}
                    </div>
                    {d.cargo && (
                      <span className={`mt-0.5 inline-block rounded-full px-1.5 py-0.5 text-[10px] font-medium ${cargoBadge(d.cargo)}`}>
                        {d.cargo}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {d.atrasado > 0 ? <span className="font-semibold text-rose-700">{d.atrasado}</span> : "—"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {d.fatal_hoje > 0 ? <span className="font-semibold text-amber-800">{d.fatal_hoje}</span> : "—"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{d.futuro || "—"}</TableCell>
                  <TableCell className="text-right font-semibold tabular-nums">{d.total}</TableCell>
                  <TableCell><Bar a={d.atrasado} f={d.fatal_hoje} fut={d.futuro} /></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {/* Modal da FAIXA da redistribuição — abre ao clicar Redistribuir. Deixa
          claro que este recorte é SÓ da redistribuição (o da tabela é outro). */}
      <Dialog open={faixaModalOpen} onOpenChange={setFaixaModalOpen}>
        <DialogContent className="max-w-fit">
          <DialogHeader>
            <DialogTitle>Faixa da redistribuição</DialogTitle>
            <DialogDescription>
              Quais tarefas entram no rebalanceamento de <b>{selecionados.length}</b> colaborador(es), pela{" "}
              <b>data de conclusão prevista</b>. As <b>vencidas entram sempre</b>. Não afeta a tabela.
            </DialogDescription>
          </DialogHeader>
          <Calendar mode="range" numberOfMonths={2} selected={redistRange}
            onSelect={setRedistRange} defaultMonth={redistRange?.from} />
          <div className="text-xs text-muted-foreground">
            Faixa: <span className="font-medium text-foreground">{rangeLabel(redistRange, "escolha início e fim")}</span>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFaixaModalOpen(false)}>Cancelar</Button>
            <Button
              className="gap-1.5"
              disabled={!redistFaixa}
              onClick={() => { setFaixaModalOpen(false); setModalOpen(true); }}
            >
              <ArrowLeftRight className="h-4 w-4" /> Redistribuir
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {modalOpen && redistFaixa && (
        <RedistribuicaoModal
          team={team}
          pessoas={selecionados}
          faixa={redistFaixa}
          onClose={() => setModalOpen(false)}
          onAplicado={() => {
            // Refresh AUTOMÁTICO da tabela de diagnóstico (Atrasadas/Fatais/
            // Futuras por pessoa): relê o snapshot, que o job já espelhou antes
            // de marcar 'done'. Sem isto o operador precisava dar F5. Também
            // borbulha pro pai (KPIs do topo do Minha Equipe).
            load();
            onAplicado?.();
          }}
        />
      )}

      {execucoesOpen && <ExecucoesDialog team={team} onClose={() => setExecucoesOpen(false)} />}
    </div>
  );
}
