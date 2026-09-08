// Raiz de Publicações — separa as duas naturezas de trabalho do módulo.
//
// Antes, uma tela só acumulava tudo: disparar busca, acompanhar classificação
// em lote, e tratar publicação. São ritmos e pessoas diferentes — quem cuida
// da captura olha o módulo algumas vezes por dia; quem trata fica horas na
// fila. Misturados, um atrapalhava o outro na mesma barra de ferramentas.
//
// Aqui a escolha é explícita e some depois do clique.

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle, ArrowRight, Bot, Clock, Inbox, Layers, ListChecks, Loader2,
} from "lucide-react";
import { apiFetch } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import { fmtData } from "@/components/publications/triagem/helpers";

const API = "/api/v1/publications";

interface AgingResumo {
  total_pendentes: number;
  vencidas: number;
  vence_hoje: number;
  mais_antiga: { dias_captura: number | null; created_at: string | null } | null;
}

interface Estatisticas {
  by_status?: Record<string, number>;
  last_search?: { created_at?: string | null; status?: string | null } | null;
}

export default function PublicationsHubPage() {
  const navigate = useNavigate();
  const [aging, setAging] = useState<AgingResumo | null>(null);
  const [stats, setStats] = useState<Estatisticas | null>(null);
  const [carregando, setCarregando] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const [a, s] = await Promise.all([
          apiFetch(`${API}/records/aging-summary`),
          apiFetch(`${API}/statistics`),
        ]);
        if (a.ok) setAging(await a.json());
        if (s.ok) setStats(await s.json());
      } catch {
        /* o hub continua útil sem os números */
      } finally {
        setCarregando(false);
      }
    })();
  }, []);

  const pendentes = aging?.total_pendentes ?? 0;
  const vencidas = aging?.vencidas ?? 0;
  const maisAntiga = aging?.mais_antiga?.dias_captura ?? null;
  // /statistics devolve as chaves em minúsculas ("novo", "classificado").
  const novas = stats?.by_status?.novo ?? 0;
  const classificadas = stats?.by_status?.classificado ?? 0;
  const comErro = stats?.by_status?.erro ?? 0;

  const cards = [
    {
      id: "classificacao",
      to: "/publications/classificacao",
      icone: Bot,
      titulo: "Classificação",
      resumo:
        "Buscar publicações no Legal One, acompanhar a classificação da IA e ajustar templates.",
      detalhe: "Captura, lotes de classificação, taxonomia e templates de agendamento.",
      metricas: [
        { rotulo: "aguardando IA", valor: novas },
        { rotulo: "já classificadas", valor: classificadas },
        ...(comErro ? [{ rotulo: "com erro", valor: comErro, alerta: true }] : []),
      ],
      tom: "azul" as const,
    },
    {
      id: "triagem",
      to: "/publications/triagem",
      icone: ListChecks,
      titulo: "Triagem",
      resumo:
        "Ler a publicação, decidir a providência e agendar a tarefa no Legal One.",
      detalhe: "Fila por escritório responsável, da mais antiga para a mais nova.",
      metricas: [
        { rotulo: "na fila", valor: pendentes },
        ...(vencidas ? [{ rotulo: "com prazo vencido", valor: vencidas, alerta: true }] : []),
      ],
      tom: "verde" as const,
    },
  ];

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4 md:p-8">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Publicações</h1>
        <p className="mt-1 text-muted-foreground">
          O módulo tem duas frentes: manter a captura e a classificação em dia, e tratar a
          fila que elas produzem. Escolha por onde entrar.
        </p>
      </div>

      {vencidas > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span className="flex-1">
            <b>{vencidas}</b> publicação(ões) com prazo estimado vencido ainda sem tratamento
            {maisAntiga ? <> — a mais antiga espera há <b>{maisAntiga} dias</b>.</> : "."}
          </span>
          <button
            type="button"
            onClick={() => navigate("/publications/triagem")}
            className="font-bold underline"
          >
            Ir para a triagem
          </button>
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        {cards.map((c) => {
          const Icone = c.icone;
          return (
            <button
              key={c.id}
              type="button"
              onClick={() => navigate(c.to)}
              className={cn(
                "group flex flex-col gap-4 rounded-2xl border bg-card/70 p-6 text-left shadow-sm backdrop-blur",
                "transition-all hover:-translate-y-0.5 hover:shadow-lg",
                c.tom === "azul" ? "hover:border-primary/40" : "hover:border-emerald-400/50",
              )}
            >
              <div className="flex items-start gap-3">
                <span
                  className={cn(
                    "inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-xl",
                    c.tom === "azul" ? "bg-primary/10 text-primary" : "bg-emerald-500/10 text-emerald-600",
                  )}
                >
                  <Icone className="h-5 w-5" />
                </span>
                <div className="min-w-0">
                  <h2 className="text-lg font-bold">{c.titulo}</h2>
                  <p className="mt-0.5 text-sm text-muted-foreground">{c.resumo}</p>
                </div>
              </div>

              <div className="flex flex-wrap gap-4">
                {carregando ? (
                  <span className="inline-flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> carregando…
                  </span>
                ) : (
                  c.metricas.map((m) => (
                    <div key={m.rotulo}>
                      <div
                        className={cn(
                          "text-2xl font-extrabold tracking-tight",
                          (m as any).alerta && "text-red-600",
                        )}
                      >
                        {m.valor}
                      </div>
                      <div className="text-[11px] font-medium text-muted-foreground">{m.rotulo}</div>
                    </div>
                  ))
                )}
              </div>

              <div className="mt-auto flex items-center gap-2 border-t border-dashed pt-3">
                <span className="min-w-0 flex-1 text-xs text-muted-foreground">{c.detalhe}</span>
                <span
                  className={cn(
                    "inline-flex shrink-0 items-center gap-1 text-sm font-bold",
                    c.tom === "azul" ? "text-primary" : "text-emerald-600",
                  )}
                >
                  Entrar <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
                </span>
              </div>
            </button>
          );
        })}
      </div>

      <div className="rounded-2xl border bg-card/50 p-4">
        <h3 className="mb-2 text-[11px] font-bold uppercase tracking-wide text-muted-foreground">
          Atalhos
        </h3>
        <div className="flex flex-wrap gap-2">
          {[
            { to: "/publications/dashboard", icone: Layers, label: "Dashboard" },
            { to: "/publications/treatment", icone: ListChecks, label: "Tratamento Web (robô)" },
            { to: "/publications/onenotify-bb", icone: Inbox, label: "Notificações BB" },
            { to: "/publications/templates", icone: Clock, label: "Templates de agendamento" },
          ].map((a) => {
            const Ic = a.icone;
            return (
              <button
                key={a.to}
                type="button"
                onClick={() => navigate(a.to)}
                className="inline-flex items-center gap-2 rounded-lg border bg-background px-3 py-1.5 text-sm hover:border-primary/40"
              >
                <Ic className="h-3.5 w-3.5 text-muted-foreground" />
                {a.label}
              </button>
            );
          })}
        </div>
      </div>

      {stats?.last_search?.created_at && (
        <p className="text-center text-xs text-muted-foreground">
          Última busca de publicações: {fmtData(stats.last_search.created_at)}
          {stats.last_search.status ? ` (${stats.last_search.status.toLowerCase()})` : ""}
        </p>
      )}
    </div>
  );
}
