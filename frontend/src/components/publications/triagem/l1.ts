// Links para o Legal One — mesmos endereços que a tela clássica usa.
//
// São dois hosts diferentes, e isso não é engano: a PASTA vive no Novajus
// (o web do L1 do escritório) e a PUBLICAÇÃO vive no firm.legalone. O link da
// TAREFA não é montado aqui — vem pronto do backend em `l1_url`, porque
// depende do returnUrl codificado que o L1 exige.

const L1_WEB_BASE = "https://mdradvocacia.novajus.com.br";
const L1_FIRM_BASE = "https://firm.legalone.com.br";

/**
 * Pasta do processo, já na aba de Compromissos e Tarefas — que é o que o
 * operador quer ver ao conferir se a providência já existe. Serve também
 * para recurso/incidente: `details` responde onde `edit` dá 404.
 */
export function urlPasta(lawsuitId: number | null | undefined): string | null {
  if (!lawsuitId) return null;
  return `${L1_WEB_BASE}/processos/Processos/DetailsCompromissosTarefas/${lawsuitId}?renderOnlySection=True`;
}

/** A publicação dentro do Legal One (host do firm, não do Novajus). */
export function urlPublicacao(updateId: number | null | undefined): string | null {
  if (!updateId) return null;
  return `${L1_FIRM_BASE}/publications?publicationId=${updateId}&treatStatus=3`;
}

/** Tarefa criada. O backend devolve pronto; aqui só normalizamos o vazio. */
export function urlTarefa(l1Url: string | null | undefined): string | null {
  return l1Url || null;
}
