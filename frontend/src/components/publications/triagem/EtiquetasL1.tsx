// Etiquetas do processo no Legal One.
//
// Não são enfeite: a etiqueta é o que dispara direcionamento específico —
// carteira, prioridade, tratamento combinado com o responsável. Quem trata
// precisa vê-la ANTES de decidir, não depois de abrir a pasta.
//
// A cor vem do próprio L1 (`class_name`, ex.: "tag-color-orange"), então a
// etiqueta chega aqui com a mesma cor que a pessoa vê lá — reconhecimento
// imediato, sem precisar ler.

import { cn } from "@/lib/utils";
import type { L1Etiqueta } from "./types";

/** Classes do L1 → paleta local. Mesmo mapa da tela clássica. */
const CORES: Record<string, string> = {
  "tag-color-red": "bg-red-100 text-red-800 border-red-300",
  "tag-color-orange": "bg-orange-100 text-orange-800 border-orange-300",
  "tag-color-yellow": "bg-amber-100 text-amber-800 border-amber-300",
  "tag-color-green": "bg-emerald-100 text-emerald-800 border-emerald-300",
  "tag-color-lime": "bg-lime-100 text-lime-800 border-lime-300",
  "tag-color-teal": "bg-teal-100 text-teal-800 border-teal-300",
  "tag-color-blue": "bg-blue-100 text-blue-800 border-blue-300",
  "tag-color-indigo": "bg-indigo-100 text-indigo-800 border-indigo-300",
  "tag-color-purple": "bg-purple-100 text-purple-800 border-purple-300",
  "tag-color-pink": "bg-pink-100 text-pink-800 border-pink-300",
  "tag-color-gray": "bg-slate-100 text-slate-700 border-slate-300",
};
const PADRAO = "bg-slate-100 text-slate-700 border-slate-300";

/** Etiquetas que mudam a ordem de trabalho ganham estrela e anel. */
const DESTAQUE = /ESTRAT|PRIORI|URGEN|SUSTENTA/i;

interface Props {
  etiquetas?: L1Etiqueta[] | null;
  /** "md" no card de leitura; "sm" em lista e cards, onde o espaço é curto. */
  tamanho?: "sm" | "md";
  className?: string;
}

export function EtiquetasL1({ etiquetas, tamanho = "sm", className }: Props) {
  // null = o job de enriquecimento ainda não visitou o processo;
  // [] = visitou e não há etiqueta. Nos dois casos não há o que mostrar.
  if (!etiquetas || etiquetas.length === 0) return null;

  return (
    <div className={cn("flex flex-wrap items-center gap-1", className)}>
      {etiquetas.map((t, i) => {
        const cores = CORES[t.class_name ?? ""] ?? PADRAO;
        const destaque = DESTAQUE.test(t.name ?? "");
        return (
          <span
            key={t.id ?? i}
            title={`Etiqueta do processo no Legal One: ${t.name ?? ""}`}
            className={cn(
              "inline-flex items-center rounded border font-semibold leading-none",
              tamanho === "md" ? "px-2 py-1 text-[11.5px]" : "px-1.5 py-0.5 text-[10px]",
              cores,
              destaque && "ring-1 ring-current",
            )}
          >
            {destaque ? "★ " : ""}{t.name}
          </span>
        );
      })}
    </div>
  );
}

export default EtiquetasL1;
