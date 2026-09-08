// Rito do processo — comum × juizado × trabalhista (pub016).
//
// Existe como componente, e não como markup solto, porque o rito aparece em
// dois lugares (o resumo da fila sem pasta e o card de qualquer publicação) e
// vocabulário visual duplicado é como duas telas passam a dizer a mesma coisa
// de cores diferentes. Uma definição só.
//
// Por que o operador se importa: rito não muda a providência, muda o QUE se
// faz com ela — recurso inominado no juizado contra apelação na comum, custas
// e preparo diferentes, e o prazo em dobro que no juizado não existe. Ler isso
// no card evita descobrir depois de já ter agendado a tarefa errada.

import { cn } from "@/lib/utils";

export type Rito = "juizado" | "comum" | "trabalhista" | null | undefined;

const ESTILO: Record<string, { rotulo: string; classe: string }> = {
  juizado: {
    rotulo: "Juizado Especial",
    classe: "border-violet-300 bg-violet-50 text-violet-800",
  },
  comum: {
    rotulo: "Justiça Comum",
    classe: "border-sky-300 bg-sky-50 text-sky-800",
  },
  trabalhista: {
    rotulo: "Justiça do Trabalho",
    classe: "border-teal-300 bg-teal-50 text-teal-800",
  },
};

interface Props {
  rito: Rito;
  /** "texto" | "datajud" — some quando não veio de lugar nenhum. */
  fonte?: string | null;
  /** Evidência (o trecho ou o órgão julgador), vira title do chip. */
  evidencia?: string | null;
  className?: string;
}

export function RitoBadge({ rito, fonte, evidencia, className }: Props) {
  if (!rito) return null;
  const e = ESTILO[rito];
  if (!e) return null;

  // A fonte fica visível só quando é DataJud: aí a informação não está no
  // texto que o operador tem na frente, e ele precisa saber que veio de
  // fora antes de confiar.
  return (
    <span
      title={evidencia || undefined}
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[10.5px] font-semibold",
        e.classe,
        className,
      )}
    >
      {e.rotulo}
      {fonte === "datajud" && <span className="font-normal opacity-70">· DataJud</span>}
    </span>
  );
}

export default RitoBadge;
