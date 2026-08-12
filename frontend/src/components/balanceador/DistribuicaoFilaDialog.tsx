// Distribuição em fila (round-robin): pega as tarefas de UM OU MAIS subtipos de
// uma pessoa e espalha igualmente entre vários colaboradores escolhidos. Ex.: 7
// tarefas pra 3 estagiários → 3 / 2 / 2. Em multi-subtipo, cada subtipo é
// distribuído por inteiro (Todas) entre os mesmos alvos.

import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Scale, Split, UserPlus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { type UsuarioBusca, getSugestoesFila, getUsuarios, registrarFilaPref } from "@/services/balanceador";

type Pessoa = { id: number; nome: string };
export type DistItem = { toId: number; toNome: string; qtd: number };
export type FilaSubtipo = { subtipo: string; max: number };
// Resultado por subtipo: como a fila daquele subtipo ficou entre os alvos.
export type FilaResultado = { subtipo: string; dist: DistItem[] };

function splitRR(count: number, targets: Pessoa[]): DistItem[] {
  if (!targets.length || count <= 0) return [];
  const base = Math.floor(count / targets.length);
  const rem = count % targets.length;
  return targets
    .map((t, i) => ({ toId: t.id, toNome: t.nome, qtd: base + (i < rem ? 1 : 0) }))
    .filter((d) => d.qtd > 0);
}

// "Igualar": distribuição multi-tipo com compensação de sobras. No splitRR puro
// a sobra de CADA tipo cai sempre nos primeiros da lista e o desequilíbrio
// acumula (1º da fila termina com vários a mais). Aqui a sobra de cada tipo vai
// pra quem tem o MENOR total acumulado até então — o total final por pessoa
// fecha com diferença de no máximo 1 tarefa, qualquer que seja o nº de tipos.
function splitMultiIgualado(itens: FilaSubtipo[], targets: Pessoa[]): FilaResultado[] {
  if (!targets.length) return [];
  const acumulado = new Map<number, number>(targets.map((t) => [t.id, 0]));
  const out: FilaResultado[] = [];
  for (const it of itens) {
    if (it.max <= 0) continue;
    const base = Math.floor(it.max / targets.length);
    const rem = it.max % targets.length;
    const qtds = new Map<number, number>(targets.map((t) => [t.id, base]));
    // sort estável: empate mantém a ordem original da lista
    const porMenorTotal = [...targets].sort(
      (a, b) => (acumulado.get(a.id) ?? 0) - (acumulado.get(b.id) ?? 0),
    );
    for (let i = 0; i < rem; i++) {
      const alvo = porMenorTotal[i];
      qtds.set(alvo.id, (qtds.get(alvo.id) ?? 0) + 1);
    }
    targets.forEach((t) => acumulado.set(t.id, (acumulado.get(t.id) ?? 0) + (qtds.get(t.id) ?? 0)));
    const dist = targets
      .map((t) => ({ toId: t.id, toNome: t.nome, qtd: qtds.get(t.id) ?? 0 }))
      .filter((d) => d.qtd > 0);
    if (dist.length) out.push({ subtipo: it.subtipo, dist });
  }
  return out;
}

export default function DistribuicaoFilaDialog({
  team,
  fromPessoa,
  itens,
  alvos,
  onConfirm,
  onClose,
}: {
  team: string;
  fromPessoa: Pessoa;
  itens: FilaSubtipo[]; // 1 = comportamento antigo; N = distribui vários de uma vez
  alvos: Pessoa[];
  onConfirm: (resultado: FilaResultado[]) => void;
  onClose: () => void;
}) {
  const single = itens.length === 1;
  const totalMax = useMemo(() => itens.reduce((s, it) => s + it.max, 0), [itens]);
  const outros = useMemo(() => alvos.filter((a) => a.id !== fromPessoa.id), [alvos, fromPessoa.id]);
  const [todos, setTodos] = useState(true);
  const [qtd, setQtd] = useState(single ? itens[0].max : totalMax);
  // multi-tipo: compensa as sobras entre os tipos pra fechar totais iguais
  // (diferença máx. de 1 tarefa por pessoa). Ligado por padrão.
  const [igualar, setIgualar] = useState(true);
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [externos, setExternos] = useState<Pessoa[]>([]);
  const [searchOpen, setSearchOpen] = useState(false);
  const [busca, setBusca] = useState("");
  const [cand, setCand] = useState<UsuarioBusca[]>([]);
  const [recorrentes, setRecorrentes] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (!searchOpen) return;
    getUsuarios(team, busca)
      .then(setCand)
      .catch(() => undefined);
  }, [searchOpen, busca, team]);

  // Sugere destinos RECORRENTES no topo, já marcados. Em single, por subtipo;
  // em multi, une as sugestões de todos os subtipos selecionados.
  useEffect(() => {
    Promise.all(itens.map((it) => getSugestoesFila(team, fromPessoa.id, it.subtipo).catch(() => [])))
      .then((listas) => {
        const sugs = listas.flat();
        if (!sugs.length) return;
        const recIds = new Set<number>();
        const novos: Pessoa[] = [];
        const selNovos: number[] = [];
        for (const s of sugs) {
          const naTabela = outros.find((o) => o.nome.toLowerCase() === s.nome.toLowerCase());
          if (naTabela) {
            recIds.add(naTabela.id);
            selNovos.push(naTabela.id);
          } else if (s.id != null) {
            if (!novos.some((n) => n.id === s.id)) novos.push({ id: s.id, nome: s.nome });
            recIds.add(s.id);
            selNovos.push(s.id);
          }
        }
        setExternos((prev) => [...novos.filter((n) => !prev.some((p) => p.id === n.id)), ...prev]);
        setRecorrentes(recIds);
        setSel((prev) => {
          const n = new Set(prev);
          selNovos.forEach((id) => n.add(id));
          return n;
        });
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pool = useMemo(
    () => [...outros, ...externos].sort((a, b) => Number(recorrentes.has(b.id)) - Number(recorrentes.has(a.id))),
    [outros, externos, recorrentes],
  );
  const targets = pool.filter((o) => sel.has(o.id));

  const addExterno = (u: UsuarioBusca) => {
    setSearchOpen(false);
    setBusca("");
    if (u.id === fromPessoa.id) return; // não distribui pra própria origem
    const jaNaTabela = outros.find((o) => o.nome.toLowerCase() === u.nome.toLowerCase());
    if (jaNaTabela) {
      setSel((s) => new Set(s).add(jaNaTabela.id));
    } else {
      if (!externos.some((e) => e.id === u.id)) setExternos((prev) => [...prev, u]);
      setSel((s) => new Set(s).add(u.id));
    }
  };

  // single: respeita "Todas/número"; multi: Todas de cada subtipo (com "Igualar"
  // compensando as sobras entre os tipos, ligado por padrão).
  const resultado = useMemo<FilaResultado[]>(() => {
    if (!targets.length) return [];
    if (single) {
      const n = todos ? itens[0].max : Math.max(1, Math.min(qtd || 0, itens[0].max));
      const d = splitRR(n, targets);
      return d.length ? [{ subtipo: itens[0].subtipo, dist: d }] : [];
    }
    if (igualar) return splitMultiIgualado(itens, targets);
    return itens
      .map((it) => ({ subtipo: it.subtipo, dist: splitRR(it.max, targets) }))
      .filter((r) => r.dist.length);
  }, [single, todos, qtd, targets, itens, igualar]);

  const totalDistribuido = useMemo(
    () => resultado.reduce((s, r) => s + r.dist.reduce((a, d) => a + d.qtd, 0), 0),
    [resultado],
  );

  // total final por pessoa (multi) — é o número que o "Igualar" deixa parelho
  const totaisPorPessoa = useMemo(() => {
    const m = new Map<number, { nome: string; total: number }>();
    for (const r of resultado) {
      for (const d of r.dist) {
        const cur = m.get(d.toId) ?? { nome: d.toNome, total: 0 };
        cur.total += d.qtd;
        m.set(d.toId, cur);
      }
    }
    return [...m.values()].sort((a, b) => b.total - a.total);
  }, [resultado]);

  const toggle = (id: number) =>
    setSel((s) => {
      const c = new Set(s);
      c.has(id) ? c.delete(id) : c.add(id);
      return c;
    });

  const confirmar = () => {
    resultado.forEach((r) =>
      registrarFilaPref(
        team,
        fromPessoa.id,
        r.subtipo,
        r.dist.map((d) => ({ id: d.toId, nome: d.toNome })),
      ).catch(() => undefined),
    );
    onConfirm(resultado);
  };

  return (
    // modal={false}: aninhado dentro do modal de redistribuição — não deve marcar
    // o pai como inert nem travar pointer-events (senão trava o footer/X ao fechar).
    <Dialog open modal={false} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[88vh] max-w-lg overflow-y-auto" style={{ pointerEvents: "auto" }}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <Split className="h-4 w-4 text-[hsl(var(--dunatech-blue))]" />
            {single ? "Distribuir em fila" : `Distribuir ${itens.length} tipos em fila`}
          </DialogTitle>
        </DialogHeader>
        <p className="text-xs text-muted-foreground">
          {single ? (
            <>
              <b>{itens[0].subtipo}</b> · de {fromPessoa.nome}. Espalha igualmente (round-robin) entre os escolhidos.
            </>
          ) : (
            <>
              {itens.length} tipos ({totalMax} tarefas) · de {fromPessoa.nome}. Cada tipo é espalhado por inteiro
              entre os escolhidos — com <b>Igualar</b> ligado, as sobras dos tipos se compensam e o total final
              por pessoa fecha parelho.
            </>
          )}
        </p>

        {/* lista de subtipos (multi) — o que vai ser distribuído */}
        {!single && (
          <div className="max-h-28 space-y-1 overflow-y-auto rounded-lg border p-2">
            {itens.map((it) => (
              <div key={it.subtipo} className="flex items-center justify-between gap-2 text-xs">
                <span className="truncate" title={it.subtipo}>{it.subtipo}</span>
                <span className="shrink-0 font-semibold tabular-nums text-muted-foreground">{it.max}</span>
              </div>
            ))}
          </div>
        )}

        {/* quantas (só single — em multi é sempre Todas de cada) */}
        {single && (
          <div className="space-y-2 rounded-lg border p-3">
            <div className="text-xs font-medium text-muted-foreground">Quantas distribuir?</div>
            <div className="flex items-center gap-3">
              <label className="flex items-center gap-1.5 text-sm">
                <Checkbox checked={todos} onCheckedChange={(c) => setTodos(!!c)} /> Todas ({itens[0].max})
              </label>
              {!todos && (
                <Input
                  type="number"
                  min={1}
                  max={itens[0].max}
                  value={qtd}
                  onChange={(e) => setQtd(Number(e.target.value))}
                  className="h-8 w-24"
                />
              )}
            </div>
          </div>
        )}

        {/* pra quem (multiselect da tabela + busca de qualquer colaborador) */}
        <div className="space-y-2 rounded-lg border p-3">
          <div className="flex items-center justify-between gap-2">
            <div className="text-xs font-medium text-muted-foreground">Para quem? ({targets.length} selecionado/s)</div>
            <Popover open={searchOpen} onOpenChange={setSearchOpen}>
              <PopoverTrigger asChild>
                <Button size="sm" variant="outline" className="h-7 gap-1 text-xs">
                  <UserPlus className="h-3.5 w-3.5" /> Buscar colaborador
                </Button>
              </PopoverTrigger>
              <PopoverContent className="w-72 p-0" align="end">
                <Command shouldFilter={false}>
                  <CommandInput placeholder="Buscar no L1…" value={busca} onValueChange={setBusca} />
                  <CommandList>
                    <CommandEmpty>Ninguém encontrado.</CommandEmpty>
                    <CommandGroup>
                      {cand.map((u) => (
                        <CommandItem key={`${u.setor ? "s" : "x"}-${u.id}`} value={u.nome} onSelect={() => addExterno(u)}>
                          <span className="truncate">{u.nome}</span>
                          {!u.setor && <span className="ml-auto shrink-0 text-[10px] text-amber-700">fora do setor</span>}
                        </CommandItem>
                      ))}
                    </CommandGroup>
                  </CommandList>
                </Command>
              </PopoverContent>
            </Popover>
          </div>
          <p className="text-[10px] text-muted-foreground">
            Mistura os da tabela com qualquer colaborador buscado — destino só recebe (não carrega as tarefas dele).
          </p>
          {pool.length === 0 ? (
            <p className="text-xs text-muted-foreground">Use a busca pra escolher pra quem distribuir.</p>
          ) : (
            <div className="max-h-40 space-y-1 overflow-y-auto">
              {pool.map((o) => (
                <label key={o.id} className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-sm hover:bg-muted/50">
                  <Checkbox checked={sel.has(o.id)} onCheckedChange={() => toggle(o.id)} />
                  <span className="flex-1">{o.nome}</span>
                  {recorrentes.has(o.id) ? (
                    <span className="text-[10px] font-medium text-indigo-600">★ recorrente</span>
                  ) : externos.some((e) => e.id === o.id) ? (
                    <span className="text-[10px] text-muted-foreground">buscado</span>
                  ) : null}
                </label>
              ))}
            </div>
          )}
        </div>

        {/* prévia da fila */}
        {resultado.length > 0 && (
          <div className="space-y-2 rounded-lg border bg-muted/20 p-3 text-xs">
            <div className="flex items-center justify-between gap-2">
              <div className="font-medium">Prévia da fila ({totalDistribuido} tarefa/s):</div>
              {!single && targets.length > 1 && (
                <button
                  type="button"
                  onClick={() => setIgualar((v) => !v)}
                  title="Compensa as sobras entre os tipos pra fechar o total por pessoa parelho (diferença máx. de 1 tarefa). Desligado, cada tipo recomeça do topo da lista e as sobras acumulam nos primeiros."
                  className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium transition-colors ${
                    igualar
                      ? "border-indigo-300 bg-indigo-50 text-indigo-700"
                      : "border-muted-foreground/30 text-muted-foreground hover:bg-muted/50"
                  }`}
                >
                  <Scale className="h-3 w-3" />
                  Igualar {igualar ? "· ligado" : "· desligado"}
                </button>
              )}
            </div>
            {/* total final por pessoa — o número que o Igualar deixa parelho */}
            {!single && totaisPorPessoa.length > 1 && (
              <div className="flex flex-wrap gap-x-3 gap-y-1 rounded-md border border-dashed bg-background/60 px-2 py-1.5">
                <span className="font-medium text-muted-foreground">Total por pessoa:</span>
                {totaisPorPessoa.map((p) => (
                  <span key={p.nome} className="tabular-nums">
                    <span className="font-semibold">{p.total}×</span> {p.nome}
                  </span>
                ))}
              </div>
            )}
            {resultado.map((r) => (
              <div key={r.subtipo}>
                {!single && <div className="truncate text-[11px] font-medium" title={r.subtipo}>{r.subtipo}</div>}
                <div className="flex flex-wrap gap-x-3 gap-y-1">
                  {r.dist.map((d) => (
                    <span key={d.toId} className="tabular-nums">
                      <span className="font-semibold">{d.qtd}×</span> {d.toNome}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancelar</Button>
          <Button className="gap-1.5" disabled={resultado.length === 0} onClick={confirmar}>
            Distribuir <ArrowRight className="h-4 w-4" />
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
