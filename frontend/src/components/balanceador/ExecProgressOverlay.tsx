// Overlay de progresso BLOQUEANTE: cobre a tela inteira enquanto a redistribuição
// (ou a reversão) executa — o usuário não navega nem fecha nada até terminar.
// MOCK: a barra avança por passo simulado; na versão real, 1 passo = 1 tarefa
// reatribuída no L1 (API ou POST Workflow).

import { CheckCircle2, Loader2, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";

export interface ExecResultado {
  reatribuidas: number;
  workflow_bloqueadas: number;
  falhas: number;
  dry_run?: boolean;
}

export interface ExecState {
  mode: "aplicar" | "reverter";
  total: number;
  done: number;
  label: string;
  finished: boolean;
  resultado?: ExecResultado | null; // breakdown real ao concluir (aplicar)
}

export default function ExecProgressOverlay({ exec, onClose }: { exec: ExecState | null; onClose: () => void }) {
  if (!exec) return null;
  const pct = exec.total > 0 ? Math.round((exec.done / exec.total) * 100) : 100;
  const reverter = exec.mode === "reverter";
  const titulo = reverter ? "Revertendo alterações" : "Aplicando redistribuição";

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="w-[92vw] max-w-md rounded-xl border bg-background p-6 shadow-2xl">
        <div className="flex items-center gap-2">
          {exec.finished ? (
            <CheckCircle2 className="h-5 w-5 text-emerald-600" />
          ) : reverter ? (
            <RotateCcw className="h-5 w-5 animate-spin text-amber-600" />
          ) : (
            <Loader2 className="h-5 w-5 animate-spin text-[hsl(var(--dunatech-blue))]" />
          )}
          <h3 className="text-base font-semibold">{exec.finished ? "Concluído" : titulo}</h3>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          {exec.finished
            ? `${exec.total} tarefa(s) ${reverter ? "revertida(s)" : "processada(s)"}${exec.resultado?.dry_run ? " (simulação)" : ""}.`
            : exec.resultado?.dry_run
              ? "Simulando — lendo o L1 sem gravar nada."
              : "A reatribuição roda no servidor — se fechar a janela, ela continua. Acompanhe o progresso aqui."}
        </p>

        <div className="mt-4 h-2.5 w-full overflow-hidden rounded-full bg-muted">
          <div
            className={`h-full transition-all duration-300 ${reverter ? "bg-amber-500" : "bg-[hsl(var(--dunatech-blue))]"}`}
            style={{ width: `${pct}%` }}
          />
        </div>
        <div className="mt-2 flex items-center justify-between text-xs">
          <span className="tabular-nums text-muted-foreground">{exec.done} / {exec.total}</span>
          <span className="tabular-nums font-semibold">{pct}%</span>
        </div>
        {exec.label && !exec.finished && (
          <p className="mt-2 truncate text-[11px] text-muted-foreground">{exec.label}</p>
        )}

        {exec.finished && exec.resultado && (
          <div className="mt-4 space-y-2">
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="rounded-md bg-emerald-50 px-2 py-1.5">
                <div className="text-lg font-bold tabular-nums text-emerald-700">{exec.resultado.reatribuidas}</div>
                <div className="text-[10px] text-emerald-700">{exec.resultado.dry_run ? "reatribuiria" : "reatribuídas"}</div>
              </div>
              <div className="rounded-md bg-amber-50 px-2 py-1.5">
                <div className="text-lg font-bold tabular-nums text-amber-700">{exec.resultado.workflow_bloqueadas}</div>
                <div className="text-[10px] text-amber-700">de Workflow</div>
              </div>
              <div className="rounded-md bg-rose-50 px-2 py-1.5">
                <div className="text-lg font-bold tabular-nums text-rose-700">{exec.resultado.falhas}</div>
                <div className="text-[10px] text-rose-700">falhas</div>
              </div>
            </div>
            {exec.resultado.workflow_bloqueadas > 0 && (
              <p className="rounded-md bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-800">
                {exec.resultado.workflow_bloqueadas} tarefa(s) são de <b>Workflow</b> (Modelo de Procedimento) — a API do
                L1 trava a troca. Reatribua essas pela tela do L1 (envolvimento em lote) ou via RPA.
              </p>
            )}
          </div>
        )}

        {exec.finished && (
          <div className="mt-4 flex justify-end">
            <Button size="sm" onClick={onClose}>Fechar</Button>
          </div>
        )}
      </div>
    </div>
  );
}
