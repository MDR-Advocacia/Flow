// Responsável NOMINAL da pasta (processo) no Legal One.
//
// Junto da etiqueta, forma a identidade do processo: a etiqueta diz COMO
// tratar, o responsável diz DE QUEM é a pasta — quem procurar quando o texto
// não fecha, e de quem é a carteira que está parando na fila.
//
// Uma coisa que ele NÃO é: validação. Medido em produção (03/09/2026), só 16%
// das tarefas agendadas nos últimos 30 dias foram para o dono da pasta — o
// template roteia por equipe/especialidade, então a tarefa ir para outra
// pessoa é o normal. Por isso aqui não há alerta de divergência: seria ruído
// em 84% dos casos. É informação, e informação em peso de informação.
//
// Três estados, e a diferença entre os dois últimos importa:
//   • tem responsável       → avatar + nome
//   • pasta sem vínculo     → nada (não existe pasta de onde tirar o dono)
//   • pasta ainda não lida  → "responsável não consultado", em cinza
// "Não consultado" não é "sem responsável": o cache é alimentado quando as
// propostas são montadas, e cobre ~80% da fila.

import { History } from "lucide-react";
import { cn } from "@/lib/utils";
import { corDoNome, iniciais } from "./helpers";
import type { ResponsavelPasta as Resp } from "./types";

interface Props {
  responsavel?: Resp | null;
  /** true quando o grupo tem pasta vinculada — sem ela não há o que consultar. */
  temPasta?: boolean;
  /** "md" no card de leitura; "sm" em lista, cards e trilho. */
  tamanho?: "sm" | "md";
  /** Rótulo "Responsável da pasta:" antes do nome (só cabe no card grande). */
  comRotulo?: boolean;
  className?: string;
}

export function ResponsavelPasta({
  responsavel, temPasta, tamanho = "sm", comRotulo, className,
}: Props) {
  const md = tamanho === "md";

  if (!responsavel) {
    // Sem pasta não há pergunta a fazer; com pasta e sem resposta, o operador
    // precisa saber que o silêncio é falta de consulta, não ausência de dono.
    if (!temPasta) return null;
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1 text-muted-foreground",
          md ? "text-[11.5px]" : "text-[10px]",
          className,
        )}
        title="O responsável desta pasta ainda não foi lido do Legal One. É diferente de não haver responsável."
      >
        <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/40" />
        responsável não consultado
      </span>
    );
  }

  const nome = responsavel.nome || `Contato ${responsavel.id}`;
  const titulo = [
    `Responsável da pasta no Legal One: ${nome}`,
    responsavel.email || null,
    responsavel.desatualizado
      ? "Leitura com mais de 7 dias — pode ter havido troca de responsável desde então."
      : null,
  ].filter(Boolean).join("\n");

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border bg-background/70",
        md ? "px-2.5 py-1" : "px-1.5 py-0.5",
        className,
      )}
      title={titulo}
    >
      <span
        className={cn(
          "flex shrink-0 items-center justify-center rounded-full font-bold text-white",
          md ? "h-5 w-5 text-[9px]" : "h-4 w-4 text-[8px]",
          corDoNome(nome),
        )}
      >
        {iniciais(nome)}
      </span>
      {comRotulo && (
        <span className={cn("text-muted-foreground", md ? "text-[11px]" : "text-[10px]")}>
          Responsável da pasta:
        </span>
      )}
      <span className={cn("font-semibold leading-none", md ? "text-[12px]" : "text-[10.5px]")}>
        {nome}
      </span>
      {/* Leitura antiga: ressalva discreta, o dado continua valendo. */}
      {responsavel.desatualizado && (
        <History className={cn("shrink-0 text-muted-foreground", md ? "h-3 w-3" : "h-2.5 w-2.5")} />
      )}
    </span>
  );
}

export default ResponsavelPasta;
