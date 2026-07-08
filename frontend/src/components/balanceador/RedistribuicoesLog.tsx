// Log de redistribuições do time — vive na aba "Relatórios" do Minha Equipe.
// Cada entrada é uma execução de "Aplicar": data, autor, totais, e o detalhe
// expandível (cada movimento: N× subtipo · DE → PARA). Paginado (padrão da casa).

import { useCallback, useEffect, useState } from "react";
import { ArrowRight, ChevronDown, History, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { type RedistribuicaoLog, listarLogs } from "@/services/balanceador";

const PAGE = 10;

const p2 = (n: number) => String(n).padStart(2, "0");
function fmtDataHora(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${p2(d.getDate())}/${p2(d.getMonth() + 1)}/${d.getFullYear()} ${p2(d.getHours())}:${p2(d.getMinutes())}`;
}

export default function RedistribuicoesLog({ team, reloadKey }: { team: string; reloadKey?: number }) {
  const [logs, setLogs] = useState<RedistribuicaoLog[]>([]);
  const [total, setTotal] = useState(0);
  const [pagina, setPagina] = useState(0);
  const [loading, setLoading] = useState(false);
  const [aberto, setAberto] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await listarLogs(team, PAGE, pagina * PAGE);
      setLogs(r.logs);
      setTotal(r.total);
    } catch {
      /* silencioso */
    } finally {
      setLoading(false);
    }
  }, [team, pagina]);

  useEffect(() => {
    load();
  }, [load, reloadKey]);

  // Nova aplicação (reloadKey muda) volta pra 1ª página, onde ela aparece.
  useEffect(() => {
    setPagina(0);
  }, [reloadKey, team]);

  const totalPaginas = Math.max(1, Math.ceil(total / PAGE));

  return (
    <div className="space-y-2 border-t pt-3">
      <div className="flex items-center gap-1.5 text-sm font-semibold">
        <History className="h-4 w-4 text-muted-foreground" /> Redistribuições
        <span className="text-xs font-normal text-muted-foreground">({total})</span>
      </div>

      {loading ? (
        <p className="py-4 text-center text-xs text-muted-foreground">
          <Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" /> Carregando…
        </p>
      ) : logs.length === 0 ? (
        <p className="rounded-lg border bg-muted/20 py-4 text-center text-xs text-muted-foreground">
          Nenhuma redistribuição registrada ainda.
        </p>
      ) : (
        <div className="space-y-1.5">
          {logs.map((l) => (
            <div key={l.id} className="rounded-lg border">
              <button
                type="button"
                className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left hover:bg-muted/40"
                onClick={() => setAberto(aberto === l.id ? null : l.id)}
              >
                <div className="flex min-w-0 items-center gap-2">
                  <ChevronDown
                    className={`h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform ${aberto === l.id ? "" : "-rotate-90"}`}
                  />
                  <span className="text-sm font-medium">{fmtDataHora(l.criado_em)}</span>
                  <span className="truncate text-xs text-muted-foreground">{l.criado_por_nome || "—"}</span>
                </div>
                <div className="flex shrink-0 items-center gap-2 text-xs">
                  <span className="font-medium tabular-nums">
                    {l.total_movimentos} mov · {l.total_tarefas} tarefas
                  </span>
                  {l.origem === "mock" && (
                    <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800">mock</span>
                  )}
                </div>
              </button>
              {aberto === l.id && (
                <div className="space-y-1 border-t px-3 py-2">
                  {l.detalhe.length === 0 && <p className="text-[11px] text-muted-foreground">Sem detalhe.</p>}
                  {l.detalhe.map((m, i) => (
                    <div key={i} className="flex items-center gap-2 text-[11px]">
                      <span className="shrink-0 font-semibold tabular-nums">{m.qtd}×</span>
                      <span className="min-w-0 flex-1 truncate" title={m.subtipo}>
                        {m.subtipo}
                        {m.individual ? " (escolhidas a dedo)" : ""}
                      </span>
                      <span className="flex shrink-0 items-center gap-1 text-muted-foreground">
                        <span className="max-w-[130px] truncate">{m.fromNome}</span>
                        <ArrowRight className="h-3 w-3" />
                        <span className="max-w-[130px] truncate">{m.toNome}</span>
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {total > PAGE && (
        <div className="flex items-center justify-between pt-1 text-xs text-muted-foreground">
          <span>
            Página {pagina + 1} de {totalPaginas} · {total} registro(s)
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
      )}
    </div>
  );
}
