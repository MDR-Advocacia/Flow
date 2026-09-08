// Fila SEM PASTA (pub014): o que liga esta publicação a uma pasta.
//
// A publicação chegou sem processo vinculado. Antes de o operador ler o
// texto, este bloco diz o que a regra e a IA já descobriram para achar a
// pasta: quantos CNJs o texto tem (21+ é pauta de sessão, não processo),
// qual é a origem citada (agravo → processo originário) e, principalmente,
// QUAIS dos CNJs citados são pastas nossas — com link direto para o L1.
//
// "Nenhum reconhecido" é dito com a ressalva certa: a base local tem CNJ
// para ~70% das pastas (45% no Master), então silêncio não prova nada.

import { useState } from "react";
import { ChevronDown, ChevronUp, ExternalLink, FolderOpen, ListOrdered, ShieldCheck } from "lucide-react";
import { cn } from "@/lib/utils";
import { urlPasta } from "./l1";
import type { SemPastaInfo } from "./types";

function pathCurto(p?: string | null): string {
  return (p || "").split(" / ").slice(-2).join(" / ");
}

/** Rótulos dos campos da ficha — a ordem aqui é a ordem de leitura. */
const ROTULOS: [string, string][] = [
  ["cliente", "Cliente"], ["polo_cliente", "Polo do cliente"], ["parte_contraria", "Parte contrária"],
  ["juizo", "Juízo"], ["tribunal", "Tribunal"], ["cnj", "CNJ"], ["cnj_origem", "CNJ de origem"],
  ["cnj_execucao", "CNJ da execução"], ["agravante", "Agravante"], ["agravado", "Agravado"],
  ["embargante", "Embargante"], ["embargado", "Embargado"], ["exequente", "Exequente"],
  ["executado", "Executado"], ["obrigacao", "Obrigação"], ["situacao", "Situação"], ["fase", "Fase"],
  ["ato", "Ato"], ["prazo_mencionado", "Prazo citado"], ["prazo_para_cumprir", "Prazo para cumprir"],
  ["multa_diaria", "Multa diária"], ["valor_mencionado", "Valor citado"],
  ["efeito_suspensivo", "Efeito suspensivo"],
];

/** Cor e rótulo da confiabilidade — o executor decide olhando isto. */
const CONFIANCA: Record<string, { rotulo: string; classe: string }> = {
  alta: { rotulo: "confiança alta", classe: "border-emerald-300 bg-emerald-50 text-emerald-800" },
  media: { rotulo: "confiança média", classe: "border-amber-300 bg-amber-50 text-amber-900" },
  baixa: { rotulo: "confiança baixa", classe: "border-orange-300 bg-orange-50 text-orange-900" },
  nenhuma: { rotulo: "não identificado", classe: "border-slate-300 bg-slate-50 text-slate-600" },
};

/**
 * Quem representamos no processo, com a confiabilidade e o PORQUÊ.
 *
 * A evidência fica à mão de propósito: a confiança não é palpite do modelo,
 * é o que dá para verificar no texto e na nossa base — e quem decide é o
 * executor, que precisa ver em cima do que a máquina se apoiou.
 */
function ClienteRepresentado({ info }: { info: NonNullable<SemPastaInfo["cliente_info"]> }) {
  const [aberto, setAberto] = useState(false);
  const c = CONFIANCA[info.confianca] ?? CONFIANCA.nenhuma;
  const evidencias = info.evidencias || [];

  return (
    <div className="mt-2 rounded-lg border border-amber-200/80 bg-white/70 px-3 py-2">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">
          Cliente que representamos
        </span>
        <span className="text-[13px] font-bold">
          {info.cliente || "não identificado"}
        </span>
        <span className={cn("rounded-full border px-2 py-0.5 text-[10.5px] font-semibold", c.classe)}>
          {c.rotulo}
        </span>
        {info.alternativas.length > 0 && (
          <span className="text-[11.5px] text-muted-foreground">
            outra hipótese: {info.alternativas.join(", ")}
          </span>
        )}
        {evidencias.length > 0 && (
          <button
            type="button"
            onClick={() => setAberto((v) => !v)}
            className="ml-auto inline-flex items-center gap-1 text-[11.5px] font-semibold text-primary"
          >
            {aberto ? "esconder base" : "em que se baseou"}
            {aberto ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          </button>
        )}
      </div>
      {aberto && (
        <ul className="mt-1.5 space-y-0.5 border-t pt-1.5 text-[12px] text-muted-foreground">
          {evidencias.map((e, i) => (
            <li key={i} className="flex gap-1.5">
              <span aria-hidden>·</span>
              <span>{e}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function SemPastaResumo({ info }: { info?: SemPastaInfo | null }) {
  if (!info) return null;
  const nossos = info.nossos || [];

  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50/70 px-3 py-2 text-[12.5px] text-amber-950">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="text-[11px] font-extrabold uppercase tracking-wide">Sem pasta vinculada</span>
        {info.pauta_coletiva ? (
          <span className="inline-flex items-center gap-1">
            <ListOrdered className="h-3.5 w-3.5" /> lista coletiva com {info.n_cnj} processos
          </span>
        ) : info.n_cnj > 1 ? (
          <span>{info.n_cnj} CNJs no texto</span>
        ) : null}
        {info.cnj_origem && (
          <span>
            origem citada: <b className="font-mono">{info.cnj_origem}</b>
          </span>
        )}
        {/* Rito: muda a providência (recurso inominado x apelação, custas,
            prazos), então fica na primeira linha, junto do resto da identidade. */}
        {info.rito?.rito && (
          <span
            title={info.rito.evidencia || undefined}
            className={cn(
              "rounded-full border px-2 py-0.5 text-[10.5px] font-semibold",
              info.rito.rito === "juizado" && "border-violet-300 bg-violet-50 text-violet-800",
              info.rito.rito === "comum" && "border-sky-300 bg-sky-50 text-sky-800",
              info.rito.rito === "trabalhista" && "border-teal-300 bg-teal-50 text-teal-800",
            )}
          >
            {info.rito.rotulo || info.rito.rito}
            {info.rito.fonte === "datajud" && " · DataJud"}
          </span>
        )}
      </div>

      {info.cliente_info && <ClienteRepresentado info={info.cliente_info} />}

      {/* Ficha de cadastro (tipos críticos): o que a equipe precisa para
          criar a pasta, extraído pela IA. O resumo vem primeiro; o resto
          é grade de pares, só os preenchidos. */}
      {info.ficha && (
        <div className="mt-2 rounded-lg border border-amber-200/80 bg-white/70 px-3 py-2">
          <div className="mb-1 text-[10.5px] font-extrabold uppercase tracking-wide text-amber-900/80">
            Ficha do caso{info.tipo ? ` · ${info.tipo}` : ""}
          </div>
          {info.ficha.resumo && <p className="mb-1.5 text-[13px] text-foreground">{info.ficha.resumo}</p>}
          <dl className="grid grid-cols-1 gap-x-4 gap-y-0.5 sm:grid-cols-2">
            {ROTULOS.filter(([k]) => info.ficha?.[k]).map(([k, rotulo]) => (
              <div key={k} className="flex gap-1.5 text-[12px]">
                <dt className="shrink-0 text-muted-foreground">{rotulo}:</dt>
                <dd className={k.startsWith("cnj") ? "font-mono" : ""}>{info.ficha?.[k]}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {nossos.length > 0 ? (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          <span>Pasta{nossos.length > 1 ? "s" : ""} nossa{nossos.length > 1 ? "s" : ""} citada{nossos.length > 1 ? "s" : ""}:</span>
          {nossos.slice(0, 8).map((n) => (
            <a
              key={n.cnj}
              href={urlPasta(n.lawsuit_id) || "#"}
              target="_blank"
              rel="noreferrer"
              title={n.office_path || "Abrir a pasta no Legal One"}
              className="inline-flex items-center gap-1 rounded-lg border border-amber-300 bg-white px-2 py-0.5 font-mono text-[11.5px] font-semibold text-primary hover:border-primary/50"
            >
              <FolderOpen className="h-3 w-3" /> {n.cnj}
              {n.office_path && (
                <span className="font-sans font-normal text-muted-foreground">· {pathCurto(n.office_path)}</span>
              )}
              <ExternalLink className="h-3 w-3 opacity-60" />
            </a>
          ))}
          {nossos.length > 8 && <span className="text-muted-foreground">+{nossos.length - 8}</span>}
        </div>
      ) : (
        <div className="mt-1 text-amber-950/80">
          Nenhum CNJ do texto foi reconhecido na nossa base — ela cobre ~70% das pastas, então isso não prova que não é nosso.
        </div>
      )}
    </div>
  );
}

export default SemPastaResumo;
