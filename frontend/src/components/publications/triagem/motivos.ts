// Marcadores de justificativa de decisão (pub007 / pub008).
//
// São a matéria-prima do estudo de automação: dizem POR QUE o operador
// divergiu da proposta. Custam um clique e só aparecem quando há divergência
// de fato — perguntar sempre viraria ruído e a pessoa clicaria em qualquer
// coisa para seguir.
//
// Todos viajam como chaves `_*` DENTRO de cada objeto de `payload_overrides`;
// o backend faz o pop delas para o audit e a whitelist do cliente L1 impede
// que vazem para a API do Legal One.

export interface MotivoOpcao {
  value: string;
  label: string;
  hint?: string;
}

/** Trocou o subtipo proposto pelo template. Aponta template errado. */
export const SUBTYPE_CHANGE_REASONS: MotivoOpcao[] = [
  { value: "template_errado", label: "Template errado", hint: "O template propõe o subtipo errado para esta classificação." },
  { value: "caso_especifico", label: "Caso específico", hint: "O template está certo no geral, mas este caso pede outro subtipo." },
  { value: "classificacao_errada", label: "Classificação errada", hint: "A IA classificou errado; o subtipo do template seguiu o erro." },
  { value: "template_ausente", label: "Faltava template", hint: "Não havia template para esta combinação." },
];

/** Mexeu na data proposta. Onde vive a regra tácita de prazo. */
export const DATE_CHANGE_REASONS: MotivoOpcao[] = [
  { value: "prazo_no_texto", label: "Prazo vinha no texto", hint: "A própria publicação trazia o prazo." },
  { value: "margem_subsidio", label: "Margem p/ subsídio", hint: "Antecipei para dar tempo de pedir subsídio ao cliente." },
  { value: "prazo_legal_diferente", label: "Prazo legal é outro", hint: "O prazo legal desta providência não é o do template." },
  { value: "carga_agenda", label: "Agenda da equipe", hint: "Ajustei pela carga de quem vai executar." },
];

/** Agendou apesar de já haver tarefa aberta da mesma família no L1. */
export const OPEN_TASK_REASONS: MotivoOpcao[] = [
  { value: "outra_providencia", label: "É outra providência", hint: "A tarefa aberta não cobre este ato." },
  { value: "novo_prazo", label: "Novo prazo", hint: "Novo prazo correndo, independente do anterior." },
  { value: "tarefa_antiga_parada", label: "A aberta está parada", hint: "A existente não anda; esta é a que vale." },
];

/** Removeu uma tarefa que o template propunha. */
export const REMOVE_TASK_REASONS: MotivoOpcao[] = [
  { value: "nao_se_aplica", label: "Não se aplica ao caso" },
  { value: "ja_existe", label: "Já existe na pasta" },
  { value: "template_propoe_demais", label: "Template propõe demais" },
];

/** Motivos de ignorar (pub006) — validados server-side no PATCH. */
export const IGNORE_REASONS: MotivoOpcao[] = [
  { value: "ja_agendado", label: "Já agendado / em tarefa aberta" },
  { value: "parte_adversa", label: "Providência da parte adversa" },
  { value: "informativa", label: "Apenas informativa" },
  { value: "classificacao_incorreta", label: "Classificação incorreta" },
  { value: "outro", label: "Outro motivo" },
];

/**
 * Motivos gravados por MÁQUINA — aparecem na auditoria, mas nunca no
 * dropdown do operador (ele não escolhe estes; a regra escolheu por ele).
 */
export const MOTIVOS_AUTOMATICOS: MotivoOpcao[] = [
  { value: "pauta_coletiva", label: "Pauta coletiva (regra, sem IA)" },
];

/** Rótulo de qualquer motivo — do operador ou de máquina. */
export function rotuloMotivo(valor?: string | null): string {
  if (!valor) return "";
  const achado = [...IGNORE_REASONS, ...MOTIVOS_AUTOMATICOS].find((m) => m.value === valor);
  return achado?.label || valor;
}

/** Desvio de data que dispara a pergunta, e o que a torna obrigatória. */
export const DESVIO_DATA_DIAS = 3;
export const DESVIO_DATA_OBRIGA = 10;

/** Rótulo do checkbox de consulta aos autos — o sinal do balde OPERADOR. */
export const LABEL_CONSULTOU_AUTOS = "Precisei abrir o processo pra decidir";

/** Diferença em dias entre a data proposta e a que o operador deixou. */
export function desvioEmDias(originalIso: string | null, atualBrtDate: string): number {
  if (!originalIso || !atualBrtDate) return 0;
  const orig = new Date(originalIso);
  const atual = new Date(`${atualBrtDate}T12:00:00`);
  if (Number.isNaN(orig.getTime()) || Number.isNaN(atual.getTime())) return 0;
  // Compara em dia-calendário; a hora não importa para "adiou/antecipou".
  const a = Date.UTC(orig.getFullYear(), orig.getMonth(), orig.getDate());
  const b = Date.UTC(atual.getFullYear(), atual.getMonth(), atual.getDate());
  return Math.round((b - a) / 86_400_000);
}

export function textoDesvio(dias: number): string {
  const n = Math.abs(dias);
  return dias > 0 ? `Você adiou ${n} dia${n === 1 ? "" : "s"}` : `Você antecipou ${n} dia${n === 1 ? "" : "s"}`;
}
