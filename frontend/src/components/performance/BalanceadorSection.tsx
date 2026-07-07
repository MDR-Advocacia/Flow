// Seção "Balanceamento de agenda" — vive DENTRO da página Minha Equipe (seção
// recolhível). Diagnóstico de carga por colaborador + redistribuição.
// MOCK (2026-06-29): leitura real do pool; escrita simulada.

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowLeftRight, CalendarClock, Clock, Loader2, Star } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import { type Colaborador, getDiagnostico } from "@/services/balanceador";
import RedistribuicaoModal from "@/components/balanceador/RedistribuicaoModal";

const PERIODOS = [
  { v: 0, l: "Todas as pendentes" },
  { v: 7, l: "Próximos 7 dias" },
  { v: 15, l: "Próximos 15 dias" },
  { v: 30, l: "Próximos 30 dias" },
  { v: 90, l: "Próximos 90 dias" },
];

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
  const [dias, setDias] = useState(0);
  const [incluirAtrasadas, setIncluirAtrasadas] = useState(true);
  const [cargo, setCargo] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

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

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Carga pendente de cada colaborador. Selecione quem quer rebalancear + o período e clique em Redistribuir.
        <span className="ml-1 text-emerald-700">Leitura e escrita ao vivo no L1.</span>
      </p>

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
        <span className="text-xs font-medium text-muted-foreground">
          {sel.size > 0 ? `${sel.size} colaborador(es) selecionado(s)` : "Selecione colaboradores na tabela"}
        </span>
        <div className="flex items-center gap-2">
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground" title="Inclui as vencidas junto com o período escolhido">
            <Checkbox checked={incluirAtrasadas} onCheckedChange={(c) => setIncluirAtrasadas(!!c)} /> Incluir atrasadas
          </label>
          <Select value={String(dias)} onValueChange={(v) => setDias(Number(v))}>
            <SelectTrigger className="h-8 w-44 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              {PERIODOS.map((p) => (
                <SelectItem key={p.v} value={String(p.v)}>{p.l}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button size="sm" className="gap-1.5" disabled={sel.size === 0} onClick={() => setModalOpen(true)}>
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

      {modalOpen && (
        <RedistribuicaoModal
          team={team}
          pessoas={selecionados}
          dias={dias}
          incluirAtrasadas={incluirAtrasadas}
          onClose={() => setModalOpen(false)}
          onAplicado={onAplicado}
        />
      )}
    </div>
  );
}
