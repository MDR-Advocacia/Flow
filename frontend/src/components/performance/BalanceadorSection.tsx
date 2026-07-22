// Seção "Balanceamento de agenda" — vive DENTRO da página Minha Equipe (seção
// recolhível). Diagnóstico de carga por colaborador + redistribuição.
// MOCK (2026-06-29): leitura real do pool; escrita simulada.

import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, ArrowLeftRight, CalendarClock, CalendarRange, Clock, Loader2, Star, X } from "lucide-react";
import { type DateRange } from "react-day-picker";

import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Checkbox } from "@/components/ui/checkbox";
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
  // Faixa EXATA por data de conclusão prevista. Default: hoje → hoje+30.
  const [range, setRange] = useState<DateRange | undefined>(() => {
    const hoje = new Date();
    return { from: hoje, to: addDays(hoje, 30) };
  });
  const [calOpen, setCalOpen] = useState(false);
  const [cargo, setCargo] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [execucoesOpen, setExecucoesOpen] = useState(false);
  const [temRodando, setTemRodando] = useState(false);

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
      setData(await getDiagnostico(team));
    } catch (e) {
      toast({ title: "Erro ao carregar o diagnóstico", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [team, toast]);

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

  // Faixa pronta pro modal (só quando início e fim estão escolhidos).
  const faixa: FaixaData | null = useMemo(() => {
    if (!range?.from || !range?.to) return null;
    return { inicio: toISO(range.from), fim: toISO(range.to) };
  }, [range]);
  const faixaLabel = range?.from
    ? range.to
      ? `${fmtBR(range.from)} – ${fmtBR(range.to)}`
      : `${fmtBR(range.from)} – …`
    : "Escolher faixa";

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          Carga pendente de cada colaborador. Selecione quem quer rebalancear + o período e clique em Redistribuir.
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
        <div className="flex items-center gap-2">
          <Popover open={calOpen} onOpenChange={setCalOpen}>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                className="h-8 gap-1.5 text-xs font-normal"
                title="Faixa por DATA DE CONCLUSÃO PREVISTA. As vencidas entram sempre."
              >
                <CalendarRange className="h-3.5 w-3.5" /> {faixaLabel}
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-auto p-0" align="end">
              <div className="border-b px-3 py-2 text-[11px] text-muted-foreground">
                Faixa por <b>data de conclusão prevista</b>. Vencidas incluídas sempre.
              </div>
              <Calendar
                mode="range"
                numberOfMonths={2}
                selected={range}
                onSelect={setRange}
                defaultMonth={range?.from}
              />
            </PopoverContent>
          </Popover>
          <Button
            size="sm"
            className="gap-1.5"
            disabled={sel.size === 0 || !faixa}
            title={!faixa ? "Escolha o início e o fim da faixa" : undefined}
            onClick={() => setModalOpen(true)}
          >
            <ArrowLeftRight className="h-4 w-4" /> Redistribuir
          </Button>
        </div>
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

      {modalOpen && faixa && (
        <RedistribuicaoModal
          team={team}
          pessoas={selecionados}
          faixa={faixa}
          onClose={() => setModalOpen(false)}
          onAplicado={onAplicado}
        />
      )}

      {execucoesOpen && <ExecucoesDialog team={team} onClose={() => setExecucoesOpen(false)} />}
    </div>
  );
}
