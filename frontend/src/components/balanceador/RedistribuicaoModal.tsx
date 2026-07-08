// Modal amplo de redistribuição: 1 coluna por colaborador escolhido, cards de
// subtipo arrastáveis entre colunas (com quantidade), (i) → detalhe individual,
// e painel de "mudanças pendentes". Rebalanceia visualmente ao vivo.
// Aplicar = reatribuição REAL no L1 (job server-backed com progresso): troca
// responsável+executante (PATCH normal; Workflow vai pro bucket bloqueado).
// "Simular" faz um dry-run (lê participantes, não grava).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Check, FlaskConical, Info, Loader2, RotateCcw, Split, Trash2, Users } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useToast } from "@/hooks/use-toast";
import { teamLabel } from "@/lib/teams";
import {
  type MatrizItem,
  type MovePendente,
  type ReatribuirItem,
  type TarefaDetalhe,
  getLivePessoa,
  iniciarReatribuicao,
  statusReatribuicao,
} from "@/services/balanceador";
import DetalheSubtipoModal from "@/components/balanceador/DetalheSubtipoModal";
import DistribuicaoFilaDialog from "@/components/balanceador/DistribuicaoFilaDialog";
import ExecProgressOverlay, { type ExecState } from "@/components/balanceador/ExecProgressOverlay";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

type Pessoa = { id: number; nome: string };
type Dragged = { fromId: number; subtipo: string; total: number } | null;
type DropCtx = { fromId: number; fromNome: string; toId: number; toNome: string; subtipo: string; max: number } | null;

const PERIODO_LABEL: Record<number, string> = {
  0: "todas as pendentes",
  7: "próximos 7 dias",
  15: "próximos 15 dias",
  30: "próximos 30 dias",
  90: "próximos 90 dias",
};

let _moveSeq = 0;

export default function RedistribuicaoModal({
  team,
  pessoas,
  dias,
  incluirAtrasadas = true,
  onClose,
  onAplicado,
}: {
  team: string;
  pessoas: Pessoa[];
  dias: number;
  incluirAtrasadas?: boolean;
  onClose: () => void;
  onAplicado?: () => void;
}) {
  const { toast } = useToast();
  const [matriz, setMatriz] = useState<MatrizItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [tarefas, setTarefas] = useState<Record<number, TarefaDetalhe[]>>({});
  const [totais, setTotais] = useState<Record<number, { carregadas: number; total: number | null; capado: boolean }>>({});
  const [progresso, setProgresso] = useState<{ done: number; total: number; nome: string } | null>(null);
  const [naoResolvidos, setNaoResolvidos] = useState<string[]>([]);
  const [moves, setMoves] = useState<MovePendente[]>([]);
  const [dropCtx, setDropCtx] = useState<DropCtx>(null);
  const [qtd, setQtd] = useState<number>(0);
  const [detalhe, setDetalhe] = useState<{ fromPessoa: Pessoa; subtipo: string } | null>(null);
  const [filaCtx, setFilaCtx] = useState<{ fromPessoa: Pessoa; itens: { subtipo: string; max: number }[] } | null>(null);
  // Seleção de vários subtipos (de UMA pessoa) pra distribuir em fila de uma vez.
  const [filaSel, setFilaSel] = useState<{ fromId: number; subs: Record<string, number> } | null>(null);
  const [exec, setExec] = useState<ExecState | null>(null);
  const dragged = useRef<Dragged>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (pollTimer.current) clearTimeout(pollTimer.current);
  }, []);

  const nomeById = useMemo(() => Object.fromEntries(pessoas.map((p) => [p.id, p.nome])), [pessoas]);

  const load = useCallback(async () => {
    // Puxa AO VIVO do L1, uma pessoa por vez (com progresso). Monta a matriz e
    // guarda as tarefas (com descrição) pro detalhe — sem fetch extra depois.
    setLoading(true);
    setProgresso({ done: 0, total: pessoas.length, nome: "" });
    const mat: MatrizItem[] = [];
    const tar: Record<number, TarefaDetalhe[]> = {};
    const tot: Record<number, { carregadas: number; total: number | null; capado: boolean }> = {};
    const naoRes: string[] = [];
    for (let i = 0; i < pessoas.length; i++) {
      const p = pessoas[i];
      setProgresso({ done: i, total: pessoas.length, nome: p.nome });
      try {
        const lp = await getLivePessoa(team, p.id, dias, incluirAtrasadas);
        if (!lp.resolvido) naoRes.push(p.nome);
        for (const s of lp.subtipos) {
          mat.push({ pessoa_id: p.id, subtipo: s.subtipo, total: s.total, atrasado: s.atrasado, fatal_hoje: s.fatal_hoje });
        }
        tar[p.id] = lp.tarefas;
        tot[p.id] = { carregadas: lp.carregadas ?? lp.tarefas.length, total: lp.total_real ?? null, capado: lp.capado ?? false };
      } catch {
        naoRes.push(p.nome);
      }
    }
    setMatriz(mat);
    setTarefas(tar);
    setTotais(tot);
    setNaoResolvidos(naoRes);
    setFilaSel(null);
    setProgresso(null);
    setLoading(false);
  }, [team, pessoas, dias, incluirAtrasadas]);

  useEffect(() => {
    load();
  }, [load]);

  // Salvaguarda do bug conhecido do Radix: ao fechar um Dialog aninhado (detalhe /
  // quantidade) ou desmontar este modal enquanto `open`, o body pode ficar com
  // pointer-events:none e travar os cliques. Restaura sempre que não há modal
  // aninhado aberto, e também no unmount.
  useEffect(() => {
    if (!detalhe && !dropCtx && !filaCtx) document.body.style.pointerEvents = "";
  }, [detalhe, dropCtx, filaCtx]);
  useEffect(() => () => {
    document.body.style.pointerEvents = "";
  }, []);

  // aplica um move no estado local (rebalanceia as colunas ao vivo)
  const applyMove = (fromId: number, toId: number, subtipo: string, q: number) => {
    setMatriz((prev) => {
      const next = prev.map((m) => ({ ...m }));
      const src = next.find((m) => m.pessoa_id === fromId && m.subtipo === subtipo);
      if (src) {
        src.total -= q;
        src.atrasado = Math.min(src.atrasado, src.total);
        src.fatal_hoje = Math.min(src.fatal_hoje, src.total);
      }
      let dst = next.find((m) => m.pessoa_id === toId && m.subtipo === subtipo);
      if (!dst) {
        dst = { pessoa_id: toId, subtipo, total: 0, atrasado: 0, fatal_hoje: 0 };
        next.push(dst);
      }
      dst.total += q;
      return next.filter((m) => m.total > 0);
    });
  };

  const registrar = (m: Omit<MovePendente, "id">) => {
    setMoves((prev) => [{ ...m, id: `mv${++_moveSeq}` }, ...prev]);
    applyMove(m.fromId, m.toId, m.subtipo, m.qtd);
  };

  // Multiselect de subtipos pra fila: seleção é sempre de UMA pessoa (a fila sai
  // de uma origem). Marcar card de outra pessoa reinicia a seleção nela.
  const toggleFilaSel = (fromId: number, subtipo: string, total: number) => {
    setFilaSel((cur) => {
      const subs = cur && cur.fromId === fromId ? { ...cur.subs } : {};
      if (subs[subtipo] != null) delete subs[subtipo];
      else subs[subtipo] = total;
      return Object.keys(subs).length ? { fromId, subs } : null;
    });
  };
  const filaSelCount = (fromId: number) =>
    filaSel && filaSel.fromId === fromId ? Object.keys(filaSel.subs).length : 0;
  const abrirFilaMulti = (p: Pessoa) => {
    if (!filaSel || filaSel.fromId !== p.id) return;
    const itens = Object.entries(filaSel.subs).map(([subtipo, max]) => ({ subtipo, max: max as number }));
    if (itens.length) setFilaCtx({ fromPessoa: p, itens });
  };

  const onDrop = (toId: number) => {
    const d = dragged.current;
    dragged.current = null;
    if (!d || d.fromId === toId || d.total <= 0) return;
    setQtd(d.total);
    setDropCtx({ fromId: d.fromId, fromNome: nomeById[d.fromId], toId, toNome: nomeById[toId], subtipo: d.subtipo, max: d.total });
  };

  const confirmarDrop = () => {
    if (!dropCtx) return;
    const q = Math.max(1, Math.min(qtd || 0, dropCtx.max));
    registrar({
      fromId: dropCtx.fromId, fromNome: dropCtx.fromNome, toId: dropCtx.toId, toNome: dropCtx.toNome,
      subtipo: dropCtx.subtipo, qtd: q, individual: false,
    });
    setDropCtx(null);
  };

  const removerMove = (id: string) => {
    const mv = moves.find((m) => m.id === id);
    if (mv) applyMove(mv.toId, mv.fromId, mv.subtipo, mv.qtd); // desfaz
    setMoves((prev) => prev.filter((m) => m.id !== id));
  };

  // Resolve os movimentos → lista plana de tarefas a reatribuir (task_id →
  // destino). Individuais já trazem taskIds; agregados (drag/fila) consomem os
  // task_ids reais que o operador viu (tarefas[fromId] do subtipo), sem repetir
  // entre movimentos da mesma (origem, subtipo).
  const resolverItens = (lista: MovePendente[]): ReatribuirItem[] => {
    const consumido = new Map<string, Set<number>>();
    const marcar = (key: string, id: number) => {
      const s = consumido.get(key) ?? new Set<number>();
      s.add(id);
      consumido.set(key, s);
    };
    const itens: ReatribuirItem[] = [];
    for (const m of lista) {
      const key = `${m.fromId}|${m.subtipo}`;
      if (m.taskIds && m.taskIds.length) {
        for (const tid of m.taskIds) {
          itens.push({ task_id: tid, to_id: m.toId, to_nome: m.toNome });
          marcar(key, tid);
        }
        continue;
      }
      const usados = consumido.get(key) ?? new Set<number>();
      const pool = (tarefas[m.fromId] || []).filter(
        (t) => t.subtipo === m.subtipo && t.l1_task_id != null && !usados.has(t.l1_task_id),
      );
      for (const t of pool.slice(0, m.qtd)) {
        itens.push({ task_id: t.l1_task_id as number, to_id: m.toId, to_nome: m.toNome });
        marcar(key, t.l1_task_id as number);
      }
    }
    return itens;
  };

  // Reatribuição REAL no L1 (server-backed): dispara o job e faz polling do
  // progresso. dryRun = só simula (lê participantes, não grava). O job roda no
  // servidor — se fechar a tela, ele continua.
  const aplicar = async (dryRun: boolean) => {
    if (!moves.length || exec) return;
    const lista = [...moves];
    const itens = resolverItens(lista);
    if (!itens.length) {
      toast({
        title: "Nada a reatribuir",
        description: "Não consegui resolver as tarefas dos movimentos (recarregue o L1 e tente de novo).",
        variant: "destructive",
      });
      return;
    }
    setExec({
      mode: "aplicar",
      total: itens.length,
      done: 0,
      label: dryRun ? "Simulando…" : "Iniciando…",
      finished: false,
      resultado: { reatribuidas: 0, workflow_bloqueadas: 0, falhas: 0, dry_run: dryRun },
    });

    let jobId: string;
    try {
      const r = await iniciarReatribuicao(team, itens, lista, dryRun);
      jobId = r.job_id;
    } catch (e) {
      setExec(null);
      toast({ title: "Falha ao iniciar", description: String((e as Error)?.message || e), variant: "destructive" });
      return;
    }

    const poll = async () => {
      try {
        const st = await statusReatribuicao(team, jobId);
        const done = st.status === "done";
        setExec((e) =>
          e
            ? {
                ...e,
                total: st.total || itens.length,
                done: st.feito || 0,
                label: done ? "" : `${(st.reatribuidas || 0) + (st.workflow_bloqueadas || 0) + (st.falhas || 0)} processada(s)`,
                finished: done,
                resultado: {
                  reatribuidas: st.reatribuidas || 0,
                  workflow_bloqueadas: st.workflow_bloqueadas || 0,
                  falhas: st.falhas || 0,
                  dry_run: dryRun,
                },
              }
            : e,
        );
        if (done) {
          if (!dryRun) {
            setMoves([]); // as tarefas já foram reatribuídas no L1
            onAplicado?.();
          }
          return;
        }
      } catch {
        /* transitório — segue tentando */
      }
      pollTimer.current = setTimeout(poll, 1500);
    };
    poll();
  };

  const reverterTudo = async () => {
    if (!moves.length || exec) return;
    const lista = [...moves];
    setExec({ mode: "reverter", total: lista.length, done: 0, label: "", finished: false });
    for (let i = 0; i < lista.length; i++) {
      const m = lista[lista.length - 1 - i]; // ordem inversa
      applyMove(m.toId, m.fromId, m.subtipo, m.qtd); // desfaz o movimento
      setExec((e) => (e ? { ...e, done: i, label: `Revertendo ${m.qtd}× ${m.subtipo} · ${m.toNome} → ${m.fromNome}` } : e));
      await sleep(350);
    }
    setMoves([]);
    setExec((e) => (e ? { ...e, done: lista.length, finished: true, label: "" } : e));
  };

  return (
    <>
      <Dialog open onOpenChange={(o) => { if (!o && !exec) onClose(); }}>
        <DialogContent
          className="flex max-h-[92vh] w-[94vw] max-w-[1400px] flex-col overflow-hidden"
          style={{ pointerEvents: "auto" }}
          // Não fechar em clique/interação FORA do conteúdo: os diálogos aninhados
          // são modal={false}, então o pointerdown dentro deles vaza pra cá e o
          // Radix fechava tudo. Fecha só no X ou no botão Fechar.
          onPointerDownOutside={(e) => e.preventDefault()}
          onInteractOutside={(e) => e.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle className="flex flex-wrap items-center gap-2">
              <Users className="h-5 w-5 text-[hsl(var(--dunatech-blue))]" />
              Redistribuição — {teamLabel(team)}
              <span className="text-sm font-normal text-muted-foreground">
                {pessoas.length} colaborador(es) · {PERIODO_LABEL[dias] ?? `${dias} dias`}
              </span>
            </DialogTitle>
          </DialogHeader>
          <p className="text-xs text-muted-foreground">
            Arraste um tipo de tarefa de uma pessoa para outra e informe a quantidade — ou clique no{" "}
            <Info className="inline h-3 w-3" /> pra escolher tarefa a tarefa, no <Split className="inline h-3 w-3" /> pra
            distribuir em fila, ou <b>marque vários tipos</b> e distribua todos de uma vez. Troca
            <b> responsável + executante</b>, mantém o solicitante.{" "}
            <span className="text-emerald-700">Leitura e escrita ao vivo no L1 · use “Simular” pra conferir antes.</span>
          </p>

          {naoResolvidos.length > 0 && (
            <p className="rounded-md bg-amber-50 px-3 py-1.5 text-[11px] text-amber-800">
              ⚠ Não consegui resolver no L1: {naoResolvidos.join(", ")} — o nome diverge do catálogo de usuários (sai das colunas).
            </p>
          )}

          {loading ? (
            <div className="py-16 text-center">
              <Loader2 className="mb-3 inline h-5 w-5 animate-spin text-[hsl(var(--dunatech-blue))]" />
              <p className="text-sm text-muted-foreground">
                Puxando do L1 ao vivo{progresso ? ` — ${progresso.done}/${progresso.total}` : ""}…
              </p>
              {progresso?.nome && <p className="mt-1 text-xs text-muted-foreground">{progresso.nome}</p>}
              {progresso && progresso.total > 0 && (
                <div className="mx-auto mt-3 h-2 w-64 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full bg-[hsl(var(--dunatech-blue))] transition-all"
                    style={{ width: `${(progresso.done / progresso.total) * 100}%` }}
                  />
                </div>
              )}
            </div>
          ) : (
            <div className="flex min-h-0 flex-1 gap-3">
              {/* colunas por colaborador */}
              <div className="flex min-h-0 flex-1 gap-3 overflow-x-auto pb-2">
                {pessoas.map((p) => {
                  const cards = matriz
                    .filter((m) => m.pessoa_id === p.id)
                    .sort((a, b) => b.total - a.total);
                  const totalP = cards.reduce((s, c) => s + c.total, 0);
                  return (
                    <div
                      key={p.id}
                      onDragOver={(e) => e.preventDefault()}
                      onDrop={() => onDrop(p.id)}
                      className="flex w-64 shrink-0 flex-col rounded-lg border bg-muted/20"
                    >
                      <div className="sticky top-0 z-10 rounded-t-lg border-b bg-background/95 px-3 py-2">
                        <div className="truncate text-sm font-semibold" title={p.nome}>{p.nome}</div>
                        <div className="text-[11px] text-muted-foreground">
                          {totalP} tarefa(s) · {cards.length} tipos
                          {totais[p.id]?.capado && (
                            <span className="text-amber-700"> · mais urgentes (de {totais[p.id]!.total} c/ prazo)</span>
                          )}
                        </div>
                        {filaSelCount(p.id) > 0 && (
                          <button
                            onClick={() => abrirFilaMulti(p)}
                            className="mt-1.5 flex w-full items-center justify-center gap-1 rounded-md bg-[hsl(var(--dunatech-blue))] px-2 py-1 text-[11px] font-medium text-white transition-opacity hover:opacity-90"
                          >
                            <Split className="h-3 w-3" /> Distribuir {filaSelCount(p.id)} tipo(s) em fila
                          </button>
                        )}
                      </div>
                      <div className="flex-1 space-y-1.5 overflow-y-auto p-2">
                        {cards.length === 0 && (
                          <p className="py-6 text-center text-[11px] text-muted-foreground">Sem carga no período</p>
                        )}
                        {cards.map((c) => (
                          <div
                            key={c.subtipo}
                            draggable
                            onDragStart={() => (dragged.current = { fromId: p.id, subtipo: c.subtipo, total: c.total })}
                            className="group cursor-grab rounded-md border bg-background p-2 shadow-sm active:cursor-grabbing"
                          >
                            <div className="flex items-start justify-between gap-1">
                              <div className="flex min-w-0 items-start gap-1.5">
                                <span
                                  className="mt-0.5 shrink-0"
                                  onClick={(e) => e.stopPropagation()}
                                  onMouseDown={(e) => e.stopPropagation()}
                                >
                                  <Checkbox
                                    className="h-3.5 w-3.5"
                                    checked={filaSel?.fromId === p.id && filaSel.subs[c.subtipo] != null}
                                    onCheckedChange={() => toggleFilaSel(p.id, c.subtipo, c.total)}
                                    title="Selecionar pra distribuir vários em fila de uma vez"
                                  />
                                </span>
                                <span className="text-xs font-medium leading-tight" title={c.subtipo}>
                                  {c.subtipo}
                                </span>
                              </div>
                              <div className="flex shrink-0 items-center gap-1">
                                <button
                                  className="text-muted-foreground hover:text-[hsl(var(--dunatech-blue))]"
                                  title="Distribuir em fila (round-robin) entre vários"
                                  onClick={() => setFilaCtx({ fromPessoa: p, itens: [{ subtipo: c.subtipo, max: c.total }] })}
                                >
                                  <Split className="h-3.5 w-3.5" />
                                </button>
                                <button
                                  className="text-muted-foreground hover:text-[hsl(var(--dunatech-blue))]"
                                  title="Detalhar / escolher tarefas"
                                  onClick={() => setDetalhe({ fromPessoa: p, subtipo: c.subtipo })}
                                >
                                  <Info className="h-3.5 w-3.5" />
                                </button>
                              </div>
                            </div>
                            <div className="mt-1 flex items-center gap-1.5">
                              <span className="text-lg font-bold leading-none tabular-nums">{c.total}</span>
                              {c.atrasado > 0 && (
                                <span className="rounded-full bg-rose-100 px-1.5 py-0.5 text-[10px] font-medium text-rose-700">
                                  {c.atrasado} atras.
                                </span>
                              )}
                              {c.fatal_hoje > 0 && (
                                <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800">
                                  {c.fatal_hoje} hoje
                                </span>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* painel de mudanças pendentes */}
              <div className="flex w-72 shrink-0 flex-col rounded-lg border">
                <div className="border-b px-3 py-2 text-sm font-semibold">
                  Mudanças pendentes <span className="text-muted-foreground">({moves.length})</span>
                </div>
                <div className="flex-1 space-y-1.5 overflow-y-auto p-2">
                  {moves.length === 0 && (
                    <p className="py-8 text-center text-[11px] text-muted-foreground">
                      Arraste tipos entre as colunas pra montar a redistribuição.
                    </p>
                  )}
                  {moves.map((m) => (
                    <div key={m.id} className="rounded-md border bg-muted/20 p-2 text-[11px]">
                      <div className="flex items-center justify-between">
                        <span className="font-semibold tabular-nums">
                          {m.qtd}× {m.individual ? "(escolhidas)" : ""}
                        </span>
                        <button className="text-muted-foreground hover:text-rose-600" onClick={() => removerMove(m.id)}>
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                      <div className="truncate font-medium" title={m.subtipo}>{m.subtipo}</div>
                      <div className="flex items-center gap-1 text-muted-foreground">
                        <span className="truncate">{m.fromNome}</span>
                        <ArrowRight className="h-3 w-3 shrink-0" />
                        <span className="truncate">{m.toNome}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          <DialogFooter className="gap-2 border-t pt-3 sm:justify-between">
            <Button variant="outline" disabled={!!exec} onClick={onClose}>Fechar</Button>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                className="gap-1.5 text-amber-700 hover:text-amber-800"
                disabled={moves.length === 0 || !!exec}
                onClick={reverterTudo}
              >
                <RotateCcw className="h-4 w-4" /> Reverter tudo
              </Button>
              <Button
                variant="outline"
                className="gap-1.5"
                disabled={moves.length === 0 || !!exec}
                onClick={() => aplicar(true)}
                title="Simula sem gravar no L1 — lê os participantes e conta o que seria reatribuído"
              >
                <FlaskConical className="h-4 w-4" /> Simular
              </Button>
              <Button className="gap-1.5" disabled={moves.length === 0 || !!exec} onClick={() => aplicar(false)}>
                <Check className="h-4 w-4" /> Aplicar {moves.length > 0 ? `(${moves.length})` : ""}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* dialog de quantidade no drop — modal={false}: aninhado dentro do modal
          principal, NÃO deve marcar o pai como inert/travar pointer-events (senão
          ao fechar deixa o footer/X congelados até dar refresh — bug do Radix). */}
      <Dialog open={dropCtx != null} modal={false} onOpenChange={(o) => !o && setDropCtx(null)}>
        <DialogContent className="max-w-sm" style={{ pointerEvents: "auto" }}>
          <DialogHeader>
            <DialogTitle className="text-base">Mover quantas?</DialogTitle>
          </DialogHeader>
          {dropCtx && (
            <div className="space-y-3">
              <p className="text-sm">
                <b>{dropCtx.subtipo}</b>
                <br />
                <span className="text-muted-foreground">{dropCtx.fromNome}</span> →{" "}
                <span className="font-medium">{dropCtx.toNome}</span>
              </p>
              <div className="flex items-center gap-2">
                <Input
                  type="number"
                  min={1}
                  max={dropCtx.max}
                  value={qtd}
                  onChange={(e) => setQtd(Number(e.target.value))}
                  className="w-28"
                  autoFocus
                  onKeyDown={(e) => e.key === "Enter" && confirmarDrop()}
                />
                <span className="text-xs text-muted-foreground">de {dropCtx.max} disponível(eis)</span>
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDropCtx(null)}>Cancelar</Button>
            <Button onClick={confirmarDrop}>Mover</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* drill individual */}
      {detalhe && (
        <DetalheSubtipoModal
          team={team}
          dias={dias}
          fromPessoa={detalhe.fromPessoa}
          subtipo={detalhe.subtipo}
          alvos={pessoas}
          tarefasIniciais={(tarefas[detalhe.fromPessoa.id] || []).filter((t) => t.subtipo === detalhe.subtipo)}
          onClose={() => setDetalhe(null)}
          onTransfer={(taskIds, toId, toNome) =>
            registrar({
              fromId: detalhe.fromPessoa.id, fromNome: detalhe.fromPessoa.nome, toId, toNome,
              subtipo: detalhe.subtipo, qtd: taskIds.length, individual: true, taskIds,
            })
          }
        />
      )}

      {/* distribuição em fila (round-robin) — 1 ou vários subtipos */}
      {filaCtx && (
        <DistribuicaoFilaDialog
          team={team}
          fromPessoa={filaCtx.fromPessoa}
          itens={filaCtx.itens}
          alvos={pessoas}
          onClose={() => setFilaCtx(null)}
          onConfirm={(resultado) => {
            resultado.forEach((r) =>
              r.dist.forEach((d) =>
                registrar({
                  fromId: filaCtx.fromPessoa.id, fromNome: filaCtx.fromPessoa.nome,
                  toId: d.toId, toNome: d.toNome, subtipo: r.subtipo, qtd: d.qtd, individual: false,
                }),
              ),
            );
            setFilaCtx(null);
            setFilaSel(null);
          }}
        />
      )}

      {/* progresso (aplicar / reverter) — a reatribuição roda no servidor */}
      <ExecProgressOverlay
        exec={exec}
        onClose={() => {
          if (pollTimer.current) clearTimeout(pollTimer.current);
          setExec(null);
        }}
      />
    </>
  );
}
