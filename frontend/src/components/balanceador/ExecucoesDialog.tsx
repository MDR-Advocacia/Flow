// Painel "Execuções" do Balanceamento de agenda: acompanhamento AO VIVO das
// redistribuições em andamento (inclusive as minimizadas em 2º plano) +
// histórico paginado com sucesso/falha por tarefa (motivo legível) e download
// do Excel com os resultados. Aberto pelo botão no header da seção.

import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, ChevronDown, FileSpreadsheet, Loader2, RefreshCw, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import {
  type ExecucaoJob,
  type ExecucaoTarefa,
  downloadExecucaoExcel,
  getExecucaoDetalhe,
  listarExecucoes,
  retentarExecucao,
} from "@/services/balanceador";

const PAGE = 10;

const p2 = (n: number) => String(n).padStart(2, "0");
function fmtDataHora(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${p2(d.getDate())}/${p2(d.getMonth() + 1)}/${d.getFullYear()} ${p2(d.getHours())}:${p2(d.getMinutes())}`;
}

// Cor do motivo: verde = trocou; âmbar = manual; vermelho = falha.
function reasonTone(reason: string): string {
  if (reason === "reassigned" || reason === "reassigned_web" || reason === "dry_ok") return "text-emerald-700";
  if (reason.includes("web") || reason.includes("workflow")) return "text-amber-700";
  return "text-rose-700";
}

function StatusBadge({ j }: { j: ExecucaoJob }) {
  if (j.status !== "done") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-blue-100 px-2 py-0.5 text-[10px] font-medium text-blue-700">
        <Loader2 className="h-3 w-3 animate-spin" /> Em andamento
      </span>
    );
  }
  if (j.dry_run) {
    return <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-600">Simulação</span>;
  }
  if (j.falhas > 0 || j.workflow_bloqueadas > 0) {
    return <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-800">Concluída c/ pendências</span>;
  }
  return <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-medium text-emerald-700">Concluída</span>;
}

export default function ExecucoesDialog({ team, onClose }: { team: string; onClose: () => void }) {
  const { toast } = useToast();
  const [items, setItems] = useState<ExecucaoJob[]>([]);
  const [total, setTotal] = useState(0);
  const [pagina, setPagina] = useState(0);
  const [loading, setLoading] = useState(false);
  const [aberto, setAberto] = useState<string | null>(null);
  const [tarefas, setTarefas] = useState<Record<string, ExecucaoTarefa[]>>({});
  const [baixando, setBaixando] = useState<string | null>(null);
  const [retentando, setRetentando] = useState<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(
    async (quiet = false) => {
      if (!quiet) setLoading(true);
      try {
        const r = await listarExecucoes(team, PAGE, pagina * PAGE);
        setItems(r.items);
        setTotal(r.total);
        return r.items;
      } catch {
        return [];
      } finally {
        if (!quiet) setLoading(false);
      }
    },
    [team, pagina],
  );

  // Poll: enquanto houver execução em andamento na página, recarrega a cada 2,5s
  // (barra ao vivo — é assim que se acompanha uma redistribuição minimizada).
  useEffect(() => {
    let vivo = true;
    const tick = async () => {
      const its = await load(true);
      if (!vivo) return;
      if (its.some((j) => j.status !== "done")) {
        pollTimer.current = setTimeout(tick, 2500);
      }
    };
    load().then((its) => {
      if (vivo && its.some((j) => j.status !== "done")) {
        pollTimer.current = setTimeout(tick, 2500);
      }
    });
    return () => {
      vivo = false;
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, [load]);

  const abrirDetalhe = async (jobId: string) => {
    if (aberto === jobId) {
      setAberto(null);
      return;
    }
    setAberto(jobId);
    if (!tarefas[jobId]) {
      try {
        const t = await getExecucaoDetalhe(team, jobId);
        setTarefas((prev) => ({ ...prev, [jobId]: t }));
      } catch {
        /* silencioso */
      }
    }
  };

  const baixar = async (jobId: string) => {
    setBaixando(jobId);
    try {
      await downloadExecucaoExcel(team, jobId);
    } catch (e) {
      toast({ title: "Falha ao gerar o Excel", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setBaixando(null);
    }
  };

  // Refaz só as pendentes/falhas dessa execução → novo job (aparece no topo,
  // ao vivo). Volta pra página 0 pra acompanhar.
  const retentar = async (j: ExecucaoJob) => {
    setRetentando(j.job_id);
    try {
      const r = await retentarExecucao(team, j.job_id);
      toast({ title: "Retentativa iniciada", description: `${r.total} tarefa(s) sendo refeitas no L1.` });
      setAberto(null);
      if (pagina === 0) load();
      else setPagina(0);
    } catch (e) {
      toast({ title: "Não foi possível refazer", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setRetentando(null);
    }
  };

  const totalPaginas = Math.max(1, Math.ceil(total / PAGE));

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="flex max-h-[88vh] max-w-3xl flex-col overflow-hidden" style={{ pointerEvents: "auto" }}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <Activity className="h-4 w-4 text-[hsl(var(--dunatech-blue))]" /> Execuções de redistribuição
            <span className="text-xs font-normal text-muted-foreground">({total})</span>
            <button
              type="button"
              onClick={() => load()}
              className="ml-auto text-muted-foreground transition-colors hover:text-foreground"
              title="Atualizar"
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </button>
          </DialogTitle>
        </DialogHeader>
        <p className="text-xs text-muted-foreground">
          Em andamento (ao vivo) + histórico. Expanda pra ver o resultado tarefa a tarefa (com o motivo) e baixe o
          Excel de qualquer execução.
        </p>

        <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-1">
          {loading && items.length === 0 ? (
            <p className="py-8 text-center text-xs text-muted-foreground">
              <Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" /> Carregando…
            </p>
          ) : items.length === 0 ? (
            <p className="rounded-lg border bg-muted/20 py-8 text-center text-xs text-muted-foreground">
              Nenhuma execução ainda.
            </p>
          ) : (
            items.map((j) => {
              const pct = j.total > 0 ? Math.round((j.feito / j.total) * 100) : 0;
              const rodando = j.status !== "done";
              return (
                <div key={j.job_id} className={`rounded-lg border ${rodando ? "border-blue-200 bg-blue-50/30" : ""}`}>
                  <button
                    type="button"
                    className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left hover:bg-muted/40"
                    onClick={() => abrirDetalhe(j.job_id)}
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <ChevronDown
                        className={`h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform ${aberto === j.job_id ? "" : "-rotate-90"}`}
                      />
                      <span className="text-sm font-medium">{fmtDataHora(j.iniciado_em)}</span>
                      <span className="truncate text-xs text-muted-foreground">{j.criado_por_nome || "—"}</span>
                      <StatusBadge j={j} />
                    </div>
                    <div className="flex shrink-0 items-center gap-2 text-xs tabular-nums">
                      {rodando ? (
                        <span className="font-medium text-blue-700">{j.feito}/{j.total} · {pct}%</span>
                      ) : (
                        <>
                          <span className="font-semibold text-emerald-700">{j.reatribuidas}✓</span>
                          {j.workflow_bloqueadas > 0 && <span className="font-semibold text-amber-700">{j.workflow_bloqueadas} manual</span>}
                          {j.falhas > 0 && <span className="font-semibold text-rose-700">{j.falhas}✗</span>}
                        </>
                      )}
                    </div>
                  </button>

                  {rodando && (
                    <div className="px-3 pb-2">
                      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                        <div className="h-full bg-[hsl(var(--dunatech-blue))] transition-all" style={{ width: `${pct}%` }} />
                      </div>
                    </div>
                  )}

                  {aberto === j.job_id && (
                    <div className="space-y-2 border-t px-3 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[11px] text-muted-foreground">
                          {j.total} tarefa(s) · {j.dry_run ? "simulação (não gravou no L1)" : "escrita real no L1"}
                        </span>
                        <div className="flex items-center gap-1.5">
                          {j.status === "done" && !j.dry_run && j.falhas + j.workflow_bloqueadas > 0 && (
                            <Button
                              size="sm"
                              variant="outline"
                              className="h-7 gap-1 border-amber-300 text-xs text-amber-800 hover:bg-amber-50"
                              disabled={retentando === j.job_id}
                              onClick={() => retentar(j)}
                              title="Refaz só as tarefas que falharam ou ficaram pendentes"
                            >
                              {retentando === j.job_id ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <RotateCcw className="h-3.5 w-3.5" />
                              )}
                              Tentar novamente ({j.falhas + j.workflow_bloqueadas})
                            </Button>
                          )}
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 gap-1 text-xs"
                            disabled={baixando === j.job_id}
                            onClick={() => baixar(j.job_id)}
                          >
                            {baixando === j.job_id ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <FileSpreadsheet className="h-3.5 w-3.5" />
                            )}
                            Excel
                          </Button>
                        </div>
                      </div>
                      {!tarefas[j.job_id] ? (
                        <p className="py-2 text-center text-[11px] text-muted-foreground">
                          <Loader2 className="mr-1 inline h-3 w-3 animate-spin" /> Carregando detalhe…
                        </p>
                      ) : tarefas[j.job_id].length === 0 ? (
                        <p className="text-[11px] text-muted-foreground">Sem detalhe por tarefa.</p>
                      ) : (
                        <div className="max-h-64 overflow-y-auto rounded-md border">
                          <table className="w-full text-[11px]">
                            <thead className="sticky top-0 bg-muted/80 text-muted-foreground">
                              <tr>
                                <th className="px-2 py-1 text-left font-medium">Tarefa</th>
                                <th className="px-2 py-1 text-left font-medium">Subtipo</th>
                                <th className="px-2 py-1 text-left font-medium">Destino</th>
                                <th className="px-2 py-1 text-left font-medium">Resultado</th>
                              </tr>
                            </thead>
                            <tbody>
                              {tarefas[j.job_id].map((t, i) => (
                                <tr key={`${t.task_id}-${i}`} className="border-t">
                                  <td className="px-2 py-1 tabular-nums">{t.task_id}</td>
                                  <td className="max-w-[180px] truncate px-2 py-1" title={t.subtipo || ""}>{t.subtipo || "—"}</td>
                                  <td className="max-w-[140px] truncate px-2 py-1" title={t.to_nome || ""}>{t.to_nome || "—"}</td>
                                  <td className={`px-2 py-1 font-medium ${reasonTone(t.reason)}`}>
                                    {t.resultado}
                                    {t.http && t.reason === "error" ? ` (HTTP ${t.http})` : ""}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>

        {/* paginação padrão da casa */}
        <div className="flex items-center justify-between border-t pt-2 text-xs text-muted-foreground">
          <span>
            Página {pagina + 1} de {totalPaginas} · {total} execução(ões)
          </span>
          <div className="flex items-center gap-1.5">
            <Button size="sm" variant="outline" className="h-7 text-xs" disabled={pagina === 0} onClick={() => setPagina((p) => p - 1)}>
              Anterior
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              disabled={pagina + 1 >= totalPaginas}
              onClick={() => setPagina((p) => p + 1)}
            >
              Próxima
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
