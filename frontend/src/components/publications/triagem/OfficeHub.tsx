// Hub da triagem: um card grande por escritório responsável, do mais crítico
// (quem espera há mais tempo) pro menos. É a porta de entrada — o operador
// trabalha em função do escritório, então a escolha vem antes da fila.

import { ArrowRight, Building2, Flame, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { BANDS, fmtDataCurta, nomeCurtoEscritorio, pathCurto } from "./helpers";
import type { OfficeSummaryResponse } from "./types";

interface Props {
  data: OfficeSummaryResponse | null;
  loading: boolean;
  onPick: (officeId: number | null) => void;
  /** Rótulo do recorte ativo (filtro de classificação), pra explicar números menores. */
  filtroLabel?: string | null;
}

function BarraFaixas({ faixas, total }: { faixas: OfficeSummaryResponse["offices"][0]["faixas"]; total: number }) {
  if (!total) return null;
  return (
    <div className="space-y-1.5">
      <div className="flex h-2.5 overflow-hidden rounded-full bg-muted">
        {BANDS.map((b) => {
          const n = faixas[b.key] || 0;
          if (!n) return null;
          return (
            <span
              key={b.key}
              style={{ width: `${Math.max(3, (n / total) * 100)}%`, background: b.hex }}
              title={`${b.label}: ${n}`}
            />
          );
        })}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        {BANDS.map((b) => {
          const n = faixas[b.key] || 0;
          if (!n) return null;
          return (
            <span key={b.key} className="inline-flex items-center gap-1">
              <span className="inline-block h-2 w-2 rounded-sm" style={{ background: b.hex }} />
              {b.short} <b className="text-foreground">{n}</b>
            </span>
          );
        })}
      </div>
    </div>
  );
}

export function OfficeHub({ data, loading, onPick, filtroLabel }: Props) {
  if (loading && !data) {
    return (
      <div className="flex items-center justify-center py-24 text-muted-foreground">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" />
        Carregando o backlog por escritório…
      </div>
    );
  }

  const offices = data?.offices ?? [];
  const totalPendentes = data?.total_pendentes ?? 0;
  const prontas = offices.reduce((n, o) => n + o.classificados, 0);
  const aguardando = offices.reduce((n, o) => n + o.novos, 0);
  const comErro = offices.reduce((n, o) => n + o.erros, 0);
  const maisCritico = offices[0] ?? null;

  if (!offices.length) {
    return (
      <div className="rounded-2xl border border-dashed bg-card/60 py-20 text-center">
        <div className="text-5xl">🎉</div>
        <h2 className="mt-3 text-xl font-semibold">Nada pendente</h2>
        <p className="mt-1 text-muted-foreground">
          {filtroLabel
            ? `Nenhuma publicação pendente com o filtro ${filtroLabel}.`
            : "Não há publicações aguardando tratamento."}
        </p>
      </div>
    );
  }

  const kpis = [
    { label: "Pendentes na fila", valor: totalPendentes, sub: `${offices.length} escritório(s) com fila`, cor: "" },
    { label: "Já classificadas", valor: prontas, sub: "prontas para revisar e agendar", cor: "text-primary" },
    { label: "Aguardando IA", valor: aguardando, sub: comErro ? `${comErro} com erro` : "ainda sem classificação", cor: "text-amber-600" },
    {
      label: "Mais antiga na fila",
      valor: maisCritico ? `${maisCritico.dias_mais_antiga}d` : "—",
      sub: maisCritico ? `${nomeCurtoEscritorio(maisCritico)} · ${fmtDataCurta(maisCritico.mais_antiga_em)}` : "",
      cor: maisCritico && maisCritico.dias_mais_antiga > 30 ? "text-red-600" : "",
    },
  ];

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Por onde começar?</h1>
        <p className="mt-0.5 text-muted-foreground">
          Escolha um escritório para tratar a fila — os mais críticos aparecem primeiro.
          A barra colorida mostra há quanto tempo as publicações esperam.
          {filtroLabel && <> Recorte ativo: <b className="text-foreground">{filtroLabel}</b>.</>}
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {kpis.map((k) => (
          <div key={k.label} className="rounded-2xl border bg-card/70 p-4 shadow-sm backdrop-blur">
            <div className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">{k.label}</div>
            <div className={cn("mt-0.5 text-3xl font-extrabold tracking-tight", k.cor)}>{k.valor}</div>
            <div className="text-xs text-muted-foreground">{k.sub}</div>
          </div>
        ))}
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {offices.map((o) => {
          const critico = o.dias_mais_antiga > 30;
          const atencao: string[] = [];
          if (o.classificados) atencao.push(`${o.classificados} classificadas`);
          if (o.novos) atencao.push(`${o.novos} aguard. IA`);
          if (o.erros) atencao.push(`${o.erros} com erro`);
          return (
            <button
              key={String(o.office_id)}
              type="button"
              onClick={() => onPick(o.office_id)}
              className={cn(
                "flex w-full flex-col gap-3 rounded-2xl border bg-card/70 p-5 text-left shadow-sm backdrop-blur",
                "transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-lg",
                critico && "border-red-200 ring-2 ring-red-50",
              )}
            >
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                    <Building2 className="h-3 w-3 shrink-0" />
                    <span className="truncate">{pathCurto(o.office_path) || "sem escritório cadastrado"}</span>
                  </div>
                  <div className="mt-0.5 truncate text-lg font-bold">{nomeCurtoEscritorio(o)}</div>
                  {o.polo_scope && (
                    <Badge variant="outline" className="mt-1.5 text-[10px] uppercase">
                      {o.polo_scope === "sem_pasta" ? "Fila sem pasta" : `Polo ${o.polo_scope}`}
                    </Badge>
                  )}
                </div>
                {critico && <Flame className="h-5 w-5 shrink-0 text-red-500" />}
              </div>

              <div className="flex items-end gap-4">
                <div>
                  <div className="text-4xl font-extrabold leading-none tracking-tight">{o.total}</div>
                  <div className="mt-1 text-xs font-medium text-muted-foreground">
                    pendente{o.total === 1 ? "" : "s"}
                  </div>
                </div>
                <div className="ml-auto text-right">
                  <div
                    className="text-xl font-bold"
                    style={{ color: BANDS[Math.min(4, o.dias_mais_antiga > 30 ? 4 : o.dias_mais_antiga > 15 ? 3 : o.dias_mais_antiga > 7 ? 2 : o.dias_mais_antiga > 2 ? 1 : 0)].hex }}
                  >
                    {o.dias_mais_antiga === 0 ? "hoje" : `${o.dias_mais_antiga} dias`}
                  </div>
                  <div className="text-[11px] font-medium text-muted-foreground">
                    mais antiga · {fmtDataCurta(o.mais_antiga_em)}
                  </div>
                </div>
              </div>

              <BarraFaixas faixas={o.faixas} total={o.total} />

              <div className="mt-auto flex items-center gap-2 border-t border-dashed pt-3">
                <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                  {atencao.join(" · ")}
                  {o.vencidas > 0 && <b className="text-red-600"> · {o.vencidas} com prazo vencido</b>}
                </span>
                <span className="inline-flex shrink-0 items-center gap-1 text-sm font-bold text-primary">
                  Tratar fila <ArrowRight className="h-3.5 w-3.5" />
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default OfficeHub;
