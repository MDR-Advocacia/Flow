// Mini auditoria do escritório: o que a equipe acabou de tratar.
//
// Responde a pergunta que o operador faz antes de abrir uma publicação —
// "isso já não foi tratado?" — sem obrigá-lo a sair da fila e ir ao Legal One.
// Mostra quem tratou, quando, o que virou tarefa, e o motivo quando a decisão
// foi ignorar. Links levam à pasta e à tarefa criada.

import { useMemo } from "react";
import {
  CalendarCheck2, CheckCircle2, ChevronDown, ExternalLink, EyeOff, Loader2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Collapsible, CollapsibleContent, CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import { corDoNome, iniciais } from "./helpers";
import { rotuloMotivo } from "./motivos";
import { urlPasta } from "./l1";
import type { TratadaRecente } from "./types";

interface Props {
  itens: TratadaRecente[];
  loading?: boolean;
  aberto: boolean;
  onOpenChange: (v: boolean) => void;
}


/** "há 5 min", "há 3 h", "ontem", ou a data — o recente é o que importa. */
function quandoRelativo(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const min = Math.floor((Date.now() - d.getTime()) / 60_000);
  if (min < 1) return "agora";
  if (min < 60) return `há ${min} min`;
  const h = Math.floor(min / 60);
  if (h < 24) return `há ${h} h`;
  const dias = Math.floor(h / 24);
  if (dias === 1) return "ontem";
  if (dias < 7) return `há ${dias} dias`;
  return d.toLocaleDateString("pt-BR");
}

export function AuditoriaCard({ itens, loading, aberto, onOpenChange }: Props) {
  const resumo = useMemo(() => {
    const agendadas = itens.filter((i) => i.acao === "agendada").length;
    return { agendadas, ignoradas: itens.length - agendadas };
  }, [itens]);

  return (
    <Collapsible open={aberto} onOpenChange={onOpenChange}>
      <div className="rounded-2xl border bg-card/70 shadow-sm backdrop-blur">
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="flex w-full items-center gap-3 px-4 py-3 text-left"
          >
            <CalendarCheck2 className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="text-sm font-bold">Tratadas recentemente</span>
            {!loading && itens.length > 0 && (
              <span className="text-xs text-muted-foreground">
                {resumo.agendadas} agendada(s) · {resumo.ignoradas} ignorada(s)
              </span>
            )}
            {loading && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
            <ChevronDown
              className={cn(
                "ml-auto h-4 w-4 shrink-0 text-muted-foreground transition-transform",
                aberto && "rotate-180",
              )}
            />
          </button>
        </CollapsibleTrigger>

        <CollapsibleContent>
          <div className="max-h-[380px] overflow-y-auto border-t">
            {itens.length === 0 && !loading && (
              <p className="px-4 py-6 text-center text-sm text-muted-foreground">
                Nada tratado neste escritório ainda.
              </p>
            )}
            {itens.map((it) => {
              const pasta = urlPasta(it.lawsuit_id);
              const agendada = it.acao === "agendada";
              return (
                <div key={it.record_id} className="border-b px-4 py-2.5 last:border-b-0">
                  <div className="flex flex-wrap items-center gap-2">
                    {agendada ? (
                      <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600" />
                    ) : (
                      <EyeOff className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    )}
                    <span className={cn("text-xs font-bold", agendada ? "text-emerald-700" : "text-muted-foreground")}>
                      {agendada ? "Agendada" : "Ignorada"}
                    </span>
                    <span className="text-[11px] text-muted-foreground">{quandoRelativo(it.quando)}</span>
                    {it.por_nome && (
                      <span className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
                        <span
                          className="inline-flex h-4 w-4 items-center justify-center rounded-full text-[7px] font-bold text-white"
                          style={{ background: corDoNome(it.por_nome) }}
                        >
                          {iniciais(it.por_nome)}
                        </span>
                        {it.por_nome}
                      </span>
                    )}
                    {pasta && (
                      <a
                        href={pasta}
                        target="_blank"
                        rel="noreferrer"
                        className="ml-auto inline-flex shrink-0 items-center gap-1 text-[11px] font-semibold text-primary hover:underline"
                      >
                        pasta no L1 <ExternalLink className="h-3 w-3" />
                      </a>
                    )}
                  </div>

                  <div className="mt-1 flex flex-wrap items-center gap-2 text-[11.5px]">
                    <span className="font-mono text-muted-foreground">{it.cnj || "sem processo"}</span>
                    {it.subcategoria && (
                      <Badge variant="outline" className="text-[10px]">{it.subcategoria}</Badge>
                    )}
                    {it.consultou_autos && (
                      <Badge variant="outline" className="border-blue-200 text-[10px] text-blue-700">
                        abriu o processo
                      </Badge>
                    )}
                  </div>

                  {agendada && it.tarefas.length > 0 && (
                    <ul className="mt-1 space-y-0.5">
                      {it.tarefas.map((t, i) => (
                        <li key={t.task_id ?? i} className="truncate text-[11px] text-muted-foreground">
                          → {t.descricao || `tarefa ${t.task_id ?? ""}`}
                        </li>
                      ))}
                    </ul>
                  )}

                  {!agendada && it.motivo && (
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      Motivo: <b className="font-semibold">{rotuloMotivo(it.motivo)}</b>
                      {it.motivo_nota ? ` — ${it.motivo_nota}` : ""}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}

export default AuditoriaCard;
