// O que JÁ existe de tarefa neste processo, lido do Legal One agora.
//
// A tela clássica tinha isto e a Triagem nasceu sem — então quem migrou passou
// a agendar às cegas, sem saber que já havia tarefa em curso para o mesmo
// processo. O custo disso não é hipotético: em 03/08/2026 uma operadora passou
// o dia achando que os agendamentos não tinham criado tarefa nenhuma. A tarefa
// existia (id 415212); o que faltava era poder cruzar as duas telas.
//
// Por isso o **#id em destaque e clicável**: é a MESMA coluna Id da grade de
// Compromissos e Tarefas do L1. Sem ele, casar as duas telas exige comparar
// descrição, que é onde o olho erra.
//
// Falhar aqui não pode travar a triagem — o painel é diagnóstico, não decisão.
// Quando o L1 não responde, a tela diz isso e oferece o link para olhar lá.

import { useEffect, useState } from "react";
import { Calendar, ExternalLink, Loader2 } from "lucide-react";
import { apiFetch } from "@/lib/api-client";
import { cn } from "@/lib/utils";

const API = "/api/v1/publications";
const L1_TAREFAS = "https://mdradvocacia.novajus.com.br/processos/Processos/DetailsCompromissosTarefas";

export interface TarefaL1 {
  task_id: number | null;
  description: string | null;
  status_id: number | null;
  status_label: string | null;
  type_name: string | null;
  subtype_id: number | null;
  subtype_name: string | null;
  creation_date: string | null;
  end_date_time: string | null;
  l1_url?: string | null;
}

export interface TarefasL1Resposta {
  pending: TarefaL1[];
  recent_completed: TarefaL1[];
  pending_count: number;
  recent_completed_count: number;
  truncated: boolean;
  check_failed: boolean;
}

/** Busca as tarefas do processo. Exportado porque o diálogo de duplicata
 *  precisa da mesma leitura — uma consulta, um formato. */
export async function buscarTarefasDoProcesso(lawsuitId: number): Promise<TarefasL1Resposta | null> {
  try {
    const res = await apiFetch(`${API}/groups/${lawsuitId}/recent-tasks?limit=5`);
    if (!res.ok) return null;
    return (await res.json()) as TarefasL1Resposta;
  } catch {
    return null;
  }
}

function dataCurta(iso?: string | null): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "2-digit" });
  } catch {
    return "";
  }
}

export function LinhaTarefa({ t, tom }: { t: TarefaL1; tom: "pendente" | "concluida" }) {
  const pendente = tom === "pendente";
  return (
    <li
      className={cn(
        "rounded border px-2 py-1.5 text-xs",
        pendente ? "border-amber-200 bg-amber-50/60" : "border-slate-200 bg-slate-50/60",
      )}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span
          className={cn(
            "rounded px-1.5 py-0.5 text-[10px] font-semibold",
            pendente ? "bg-amber-200 text-amber-900" : "bg-slate-200 text-slate-700",
          )}
        >
          {t.status_label || "—"}
        </span>
        {t.task_id && (
          <a
            href={t.l1_url || undefined}
            target="_blank"
            rel="noopener noreferrer"
            title="Abrir esta tarefa no Legal One"
            className={cn(
              "rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold ring-1",
              pendente
                ? "bg-amber-100 text-amber-900 ring-amber-300 hover:bg-amber-200"
                : "bg-slate-100 text-slate-700 ring-slate-300 hover:bg-slate-200",
            )}
          >
            #{t.task_id}
          </a>
        )}
        {t.subtype_name && <span className="font-medium">{t.subtype_name}</span>}
        {t.end_date_time && (
          <span className="text-muted-foreground">· vence {dataCurta(t.end_date_time)}</span>
        )}
      </div>
      {t.description && (
        <p className="mt-0.5 line-clamp-2 text-[11.5px] text-muted-foreground">{t.description}</p>
      )}
    </li>
  );
}

export function TarefasNoL1({ lawsuitId }: { lawsuitId: number | null | undefined }) {
  const [dados, setDados] = useState<TarefasL1Resposta | null>(null);
  const [carregando, setCarregando] = useState(false);

  useEffect(() => {
    if (!lawsuitId) { setDados(null); return; }
    let vivo = true;
    setCarregando(true);
    buscarTarefasDoProcesso(lawsuitId)
      .then((r) => { if (vivo) setDados(r); })
      .finally(() => { if (vivo) setCarregando(false); });
    return () => { vivo = false; };
  }, [lawsuitId]);

  if (!lawsuitId) return null;

  return (
    <div className="rounded-lg border bg-muted/20 p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-sm font-semibold">
          <Calendar className="h-4 w-4 text-muted-foreground" />
          Tarefas no Legal One
          <span className="text-[10px] font-normal text-muted-foreground">
            · lido do L1 agora · o #id é o mesmo da coluna Id de lá
          </span>
        </span>
        {dados?.truncated && (
          <span className="text-[10px] text-amber-700">
            Lista pode estar incompleta — abrir no L1 para ver tudo
          </span>
        )}
      </div>

      {carregando && (
        <div className="flex items-center gap-2 py-3 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Consultando tarefas do processo...
        </div>
      )}

      {!carregando && (!dados || dados.check_failed) && (
        <p className="py-2 text-xs text-muted-foreground">
          Não foi possível carregar agora. Abra{" "}
          <a
            href={`${L1_TAREFAS}/${lawsuitId}?renderOnlySection=True`}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-0.5 text-primary underline"
          >
            Compromissos e Tarefas no Legal One <ExternalLink className="h-3 w-3" />
          </a>{" "}
          para ver direto.
        </p>
      )}

      {!carregando && dados && !dados.check_failed && (
        <>
          <div className="mb-2">
            <p className="mb-1.5 text-xs font-medium text-amber-800">
              Pendentes ({dados.pending_count})
            </p>
            {dados.pending.length === 0 ? (
              <p className="text-xs italic text-muted-foreground">
                Nenhuma tarefa em aberto neste processo.
              </p>
            ) : (
              <ul className="space-y-1.5">
                {dados.pending.map((t) => (
                  <LinhaTarefa key={t.task_id ?? Math.random()} t={t} tom="pendente" />
                ))}
              </ul>
            )}
          </div>

          {dados.recent_completed.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs font-medium text-muted-foreground">
                Concluídas recentemente ({dados.recent_completed_count})
              </p>
              <ul className="space-y-1.5">
                {dados.recent_completed.map((t) => (
                  <LinhaTarefa key={t.task_id ?? Math.random()} t={t} tom="concluida" />
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default TarefasNoL1;
