// Compositor de tarefas da triagem.
//
// É aqui que o operador revisa antes de remeter: descrição, subtipo,
// responsável, prazo e observações são editáveis, a tarefa pode ser removida,
// e dá para acrescentar quantas tarefas avulsas quiser.
//
// Cada tarefa nasce RECOLHIDA num resumo de uma linha — quando o template já
// acertou, o operador só confere e confirma. Abre para editar quando quiser, e
// já abre sozinha quando falta preencher algo.
//
// Sobre os marcadores de justificativa (pub007/pub008): eles só aparecem
// quando há divergência REAL do que o template propôs — trocou o subtipo,
// mexeu na data além de 3 dias, agendou apesar de tarefa aberta, removeu uma
// tarefa. Perguntar sempre viraria ruído e a pessoa clicaria em qualquer coisa
// para seguir; perguntar só na exceção é o que mantém o dado confiável.

import { useMemo, useState } from "react";
import {
  AlertTriangle, CalendarClock, ChevronDown, ChevronUp, ExternalLink,
  Plus, RotateCcw, Trash2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import SubtypePicker from "@/components/ui/SubtypePicker";
import UserSelector, { type SelectableUser } from "@/components/ui/UserSelector";
import { cn } from "@/lib/utils";
import type { AppUser, DraftTask, TarefaAbertaL1, TaskType } from "./types";
import { brtToUtcIso, corDoNome, fmtData, hojeBrtDate, iniciais, prazoInfo } from "./helpers";
import {
  DATE_CHANGE_REASONS, DESVIO_DATA_DIAS, DESVIO_DATA_OBRIGA, OPEN_TASK_REASONS,
  REMOVE_TASK_REASONS, SUBTYPE_CHANGE_REASONS, desvioEmDias, textoDesvio,
  type MotivoOpcao,
} from "./motivos";

interface Props {
  drafts: DraftTask[];
  onChange: (drafts: DraftTask[]) => void;
  taskTypes: TaskType[];
  users: AppUser[];
  /** Escritório do processo — vira responsibleOfficeId das tarefas novas. */
  officeId: number | null;
  /** Tarefas já abertas no L1, por subtipo (vem do check-duplicates). */
  duplicatasPorSubtipo?: Record<number, TarefaAbertaL1[]>;
  /** Vencimento estimado do prazo processual (o fatal), em "YYYY-MM-DD". */
  prazoFatal?: string | null;
  disabled?: boolean;
}

let uidSeq = 0;
export function novoDraft(partial: Partial<DraftTask> = {}): DraftTask {
  uidSeq += 1;
  return {
    uid: `draft-${Date.now()}-${uidSeq}`,
    description: "",
    notes: "",
    subTypeId: null,
    typeId: null,
    responsibleExternalId: null,
    dueDate: hojeBrtDate(),
    dueTime: "23:59",
    isCustom: true,
    priority: "Normal",
    responsibleOfficeId: null,
    origSubTypeId: null,
    origDueIso: null,
    subtipoMotivo: null,
    dataMotivo: null,
    tarefaAbertaMotivo: null,
    removida: false,
    removidaMotivo: null,
    ...partial,
  };
}

/**
 * De onde veio o responsável sugerido. O backend manda o slug; aqui ele vira
 * frase curta, porque "template" sozinho não diz nada a quem está lendo.
 */
const ORIGEM_RESPONSAVEL: Record<string, string> = {
  template: "definido no template",
  lawsuit: "responsável da pasta",
  lawsuit_responsible: "responsável da pasta",
  squad: "roteado pela equipe",
  fallback: "padrão do escritório",
};

/** Uma tarefa só entra na remessa com descrição, subtipo, responsável e prazo. */
export function draftValido(d: DraftTask): boolean {
  if (d.removida) return false;
  return Boolean(d.description.trim() && d.subTypeId && d.responsibleExternalId && d.dueDate);
}

function pendencias(d: DraftTask): string[] {
  const falta: string[] = [];
  if (!d.description.trim()) falta.push("descrição");
  if (!d.subTypeId) falta.push("subtipo");
  if (!d.responsibleExternalId) falta.push("responsável");
  if (!d.dueDate) falta.push("conclusão prevista");
  return falta;
}

/** Trocou o subtipo que o template havia proposto? */
export const trocouSubtipo = (d: DraftTask): boolean =>
  d.origSubTypeId != null && d.subTypeId !== d.origSubTypeId;

/** Desvio da data proposta, em dias (negativo = antecipou). */
export const desvioData = (d: DraftTask): number =>
  d.origDueIso ? desvioEmDias(d.origDueIso, d.dueDate) : 0;

/** Motivo de data é obrigatório acima de 10 dias de desvio. */
export function faltaMotivoObrigatorio(d: DraftTask): boolean {
  if (d.removida) return false;
  return Math.abs(desvioData(d)) > DESVIO_DATA_OBRIGA && !d.dataMotivo;
}

/** Chips de motivo — um clique, e clicar de novo desmarca. */
// Exportado para o diálogo de duplicata usar os MESMOS chips: o vocabulário
// de motivos é um só, e reimplementá-lo lá geraria duas listas que divergem.
export function ChipsMotivo({
  titulo, opcoes, valor, onPick, obrigatorio, disabled,
}: {
  titulo: string;
  opcoes: MotivoOpcao[];
  valor: string | null;
  onPick: (v: string | null) => void;
  obrigatorio?: boolean;
  disabled?: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border px-3 py-2",
        obrigatorio && !valor ? "border-red-300 bg-red-50/60" : "border-dashed bg-muted/30",
      )}
    >
      <p className={cn("mb-1.5 text-[11px] font-semibold", obrigatorio && !valor ? "text-red-700" : "text-muted-foreground")}>
        {titulo}
        {obrigatorio && !valor && " — obrigatório para continuar"}
      </p>
      <div className="flex flex-wrap gap-1.5">
        {opcoes.map((o) => (
          <button
            key={o.value}
            type="button"
            title={o.hint}
            disabled={disabled}
            onClick={() => onPick(valor === o.value ? null : o.value)}
            className={cn(
              "rounded-full border px-2.5 py-1 text-[11.5px] font-semibold transition-colors",
              valor === o.value
                ? "border-primary bg-primary text-primary-foreground"
                : "bg-background hover:border-primary/40",
            )}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export function TaskComposer({
  drafts, onChange, taskTypes, users, officeId, duplicatasPorSubtipo,
  prazoFatal, disabled,
}: Props) {
  const [abertos, setAbertos] = useState<Set<string>>(new Set());

  const selectableUsers: SelectableUser[] = useMemo(
    () =>
      users.map((u) => ({
        id: u.id,
        external_id: u.external_id,
        name: u.name,
        email: u.email ?? undefined,
        squads: (u.squads || []) as any,
      })),
    [users],
  );

  const nomeSubtipo = useMemo(() => {
    const mapa = new Map<number, string>();
    taskTypes.forEach((t) =>
      (t.subtypes || []).forEach((s) => mapa.set(s.external_id, `${t.name} · ${s.name}`)),
    );
    return mapa;
  }, [taskTypes]);

  const patch = (uid: string, campos: Partial<DraftTask>) =>
    onChange(drafts.map((d) => (d.uid === uid ? { ...d, ...campos } : d)));

  const alternar = (uid: string) =>
    setAbertos((prev) => {
      const c = new Set(prev);
      if (c.has(uid)) c.delete(uid);
      else c.add(uid);
      return c;
    });

  const adicionar = () => {
    const novo = novoDraft({ responsibleOfficeId: officeId });
    onChange([...drafts, novo]);
    setAbertos((prev) => new Set(prev).add(novo.uid));
  };

  /**
   * Tarefa do template é REMOVIDA (fica na tela, com motivo, e não é enviada);
   * tarefa avulsa é descartada de vez, porque não há o que justificar em algo
   * que o próprio operador acabou de criar.
   */
  const removerOuDescartar = (d: DraftTask) => {
    if (d.isCustom) {
      onChange(drafts.filter((x) => x.uid !== d.uid));
      return;
    }
    patch(d.uid, { removida: true });
  };

  return (
    <div className="space-y-2.5">
      {drafts.map((d, idx) => {
        const falta = pendencias(d);
        const incompleta = !d.removida && falta.length > 0;
        const aberta = !d.removida && (abertos.has(d.uid) || incompleta);
        const info = prazoInfo(d.dueDate || null);
        const resp =
          d.responsibleExternalId == null
            ? undefined
            : users.find((u) => u.external_id === d.responsibleExternalId);
        // O template escolhe a pessoa a partir da equipe, então saber de qual
        // equipe ela é explica a sugestão em vez de só afirmá-la.
        const equipes = (resp?.squads || []).map((sq) => sq.name).filter(Boolean);
        const origemResp = ORIGEM_RESPONSAVEL[d.respSource || ""] || null;
        // Conclusão depois do fatal = prazo perdido. Comparação de data pura
        // ("YYYY-MM-DD"), que ordena como string sem passar por fuso.
        const estouraFatal = Boolean(prazoFatal && d.dueDate && d.dueDate > prazoFatal.slice(0, 10));

        const mudouSubtipo = trocouSubtipo(d);
        const desvio = desvioData(d);
        const perguntaData = Math.abs(desvio) > DESVIO_DATA_DIAS;
        const obrigaData = Math.abs(desvio) > DESVIO_DATA_OBRIGA;
        const abertasNoL1 = (d.subTypeId && duplicatasPorSubtipo?.[d.subTypeId]) || [];

        return (
          <div
            key={d.uid}
            className={cn(
              "rounded-xl border transition-colors",
              d.removida && "border-dashed bg-muted/40 opacity-70",
              !d.removida && incompleta && "border-amber-300 bg-amber-50/40",
              !d.removida && !incompleta && "border-border bg-card/60",
            )}
          >
            {/* ── resumo ── */}
            <div className="flex flex-wrap items-center gap-2 px-4 py-3">
              <span className={cn("text-sm font-semibold", d.removida && "line-through")}>
                Tarefa {idx + 1}
              </span>
              {d.isCustom ? (
                <Badge variant="outline" className="text-[10px]">Avulsa</Badge>
              ) : (
                d.templateName && (
                  <Badge variant="secondary" className="max-w-[220px] truncate text-[10px]">
                    {d.templateName}
                  </Badge>
                )
              )}
              {d.removida && <Badge variant="outline" className="text-[10px] text-muted-foreground">não será enviada</Badge>}
              {mudouSubtipo && !d.removida && (
                <Badge variant="outline" className="border-amber-300 text-[10px] text-amber-700">subtipo trocado</Badge>
              )}
              {perguntaData && !d.removida && (
                <Badge variant="outline" className="border-amber-300 text-[10px] text-amber-700">
                  {desvio > 0 ? `+${desvio}d` : `${desvio}d`}
                </Badge>
              )}

              {d.removida && (
                <span className="min-w-0 flex-1 truncate text-sm text-muted-foreground line-through">
                  {d.description}
                </span>
              )}
              <div className="ml-auto flex items-center gap-1">
                {!incompleta && !d.removida && (
                  <Button
                    type="button" variant="ghost" size="sm"
                    className="h-7 text-muted-foreground"
                    onClick={() => alternar(d.uid)}
                    disabled={disabled}
                    aria-expanded={aberta}
                  >
                    {aberta ? <><ChevronUp className="mr-1 h-3.5 w-3.5" /> Fechar</> : <><ChevronDown className="mr-1 h-3.5 w-3.5" /> Editar</>}
                  </Button>
                )}
                {d.removida ? (
                  <Button
                    type="button" variant="ghost" size="sm" className="h-7"
                    onClick={() => patch(d.uid, { removida: false, removidaMotivo: null })}
                    disabled={disabled}
                  >
                    <RotateCcw className="mr-1 h-3.5 w-3.5" /> Restaurar
                  </Button>
                ) : (
                  (drafts.filter((x) => !x.removida).length > 1 || d.isCustom) && (
                    <Button
                      type="button" variant="ghost" size="sm"
                      className="h-7 text-muted-foreground hover:text-destructive"
                      onClick={() => removerOuDescartar(d)}
                      disabled={disabled}
                    >
                      <Trash2 className="mr-1 h-3.5 w-3.5" /> Remover
                    </Button>
                  )
                )}
              </div>

            </div>

            {/* Prévia: o que sobe pro Legal One, sem precisar abrir a edição. */}
            {!aberta && !d.removida && (
              <div className="border-t px-4 py-3">
                {/* Os quatro campos que definem o agendamento, todos rotulados:
                    sem o rótulo, a descrição solta no topo não se identificava. */}
                <div className="grid gap-x-4 gap-y-2.5 sm:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_auto]">
                  <div className="min-w-0 sm:col-span-3">
                    <div className="text-[10px] font-bold uppercase tracking-wide text-muted-foreground">
                      Descrição da tarefa
                    </div>
                    <p className="line-clamp-2 text-[14px] font-semibold leading-snug text-foreground">
                      {d.description || <em className="font-normal text-muted-foreground">sem descrição</em>}
                    </p>
                  </div>

                  <div className="min-w-0">
                    <div className="text-[10px] font-bold uppercase tracking-wide text-muted-foreground">
                      Subtipo da tarefa
                    </div>
                    <div className="text-[13px] font-medium leading-snug text-foreground">
                      {d.subTypeId
                        ? (nomeSubtipo.get(d.subTypeId) || `subtipo ${d.subTypeId}`)
                        : <span className="text-amber-700">a definir</span>}
                    </div>
                  </div>

                  <div className="min-w-0">
                    <div className="text-[10px] font-bold uppercase tracking-wide text-muted-foreground">
                      Responsável
                    </div>
                    {resp ? (
                      <div className="flex items-start gap-2">
                        <span
                          className="mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[9px] font-bold text-white"
                          style={{ background: corDoNome(resp.name) }}
                        >
                          {iniciais(resp.name)}
                        </span>
                        <div className="min-w-0">
                          <div className="text-[13px] font-medium leading-snug text-foreground">
                            {resp.name}
                          </div>
                          {equipes.length > 0 && (
                            <div className="text-[11px] leading-snug text-muted-foreground">
                              {equipes.join(" · ")}
                            </div>
                          )}
                          {origemResp && (
                            <div className="text-[10.5px] leading-snug text-muted-foreground/80">
                              {origemResp}
                            </div>
                          )}
                        </div>
                      </div>
                    ) : (
                      <div className="text-[13px] font-medium text-amber-700">a definir</div>
                    )}
                  </div>

                  <div className="sm:text-right">
                    <div className="text-[10px] font-bold uppercase tracking-wide text-muted-foreground">
                      Conclusão prevista
                    </div>
                    <div
                      className={cn(
                        "text-[13px] font-bold leading-snug",
                        estouraFatal ? "text-red-600" : "text-foreground",
                      )}
                    >
                      {fmtData(d.dueDate)}
                    </div>
                    {info && (
                      <div
                        className={cn(
                          "text-[11px] font-semibold leading-snug",
                          info.estado === "vencida" && "text-red-600",
                          info.estado === "vence_hoje" && "text-orange-600",
                          info.estado === "no_prazo" && "text-muted-foreground",
                        )}
                      >
                        {info.label}
                      </div>
                    )}
                  </div>

                  {estouraFatal && (
                    <div className="sm:col-span-3">
                      <p className="flex items-center gap-1.5 rounded-md bg-red-50 px-2.5 py-1.5 text-[11.5px] font-semibold text-red-700">
                        <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                        A conclusão cai depois do prazo fatal estimado ({fmtData(prazoFatal)}) — a
                        tarefa venceria com o prazo já perdido.
                      </p>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* ── motivo da remoção (tarefa de template) ── */}
            {d.removida && !d.isCustom && (
              <div className="border-t px-4 py-3">
                <ChipsMotivo
                  titulo="Por que esta tarefa não vai?"
                  opcoes={REMOVE_TASK_REASONS}
                  valor={d.removidaMotivo}
                  onPick={(v) => patch(d.uid, { removidaMotivo: v })}
                  disabled={disabled}
                />
              </div>
            )}

            {/* ── formulário ── */}
            {aberta && (
              <div className="space-y-3 border-t px-4 py-3">
                <div className="space-y-1.5">
                  <Label className="text-xs">Descrição *</Label>
                  <Textarea
                    value={d.description}
                    onChange={(e) => patch(d.uid, { description: e.target.value })}
                    placeholder="O que precisa ser feito nesta pasta"
                    rows={2}
                    maxLength={250}
                    disabled={disabled}
                    className="resize-none text-sm"
                  />
                  <p className="text-right text-[11px] text-muted-foreground">{d.description.length}/250</p>
                </div>

                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label className="text-xs">Subtipo de tarefa *</Label>
                    <SubtypePicker
                      value={d.subTypeId}
                      taskTypes={taskTypes as any}
                      onChange={(subtypeId, parentType) =>
                        patch(d.uid, {
                          subTypeId: subtypeId,
                          typeId: parentType?.external_id ?? null,
                          // Trocar de novo zera o motivo: ele se refere à
                          // troca que o operador acabou de desfazer.
                          subtipoMotivo: null,
                        })
                      }
                      disabled={disabled}
                      placeholder="Escolher tipo · subtipo"
                      label=""
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-xs">Responsável *</Label>
                    <UserSelector
                      users={selectableUsers}
                      value={d.responsibleExternalId ? String(d.responsibleExternalId) : null}
                      onChange={(v) => patch(d.uid, { responsibleExternalId: v ? Number(v) : null })}
                      disabled={disabled}
                      placeholder="Escolher responsável"
                      showEmail
                    />
                  </div>
                </div>

                {mudouSubtipo && (
                  <ChipsMotivo
                    titulo="Por que trocou o subtipo? (opcional — é o que nos diz qual template corrigir)"
                    opcoes={SUBTYPE_CHANGE_REASONS}
                    valor={d.subtipoMotivo}
                    onPick={(v) => patch(d.uid, { subtipoMotivo: v })}
                    disabled={disabled}
                  />
                )}

                <div className="grid gap-3 sm:grid-cols-[1fr_auto_auto] sm:items-end">
                  <div className="space-y-1.5">
                    <Label className="text-xs">Conclusão prevista *</Label>
                    <Input
                      type="date"
                      value={d.dueDate}
                      onChange={(e) => patch(d.uid, { dueDate: e.target.value })}
                      disabled={disabled}
                      className="text-sm"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-xs">Horário</Label>
                    <Input
                      type="time"
                      value={d.dueTime}
                      onChange={(e) => patch(d.uid, { dueTime: e.target.value })}
                      disabled={disabled}
                      className="w-[110px] text-sm"
                    />
                  </div>
                  {info && (
                    <span
                      className={cn(
                        "inline-flex items-center gap-1 rounded-md px-2 py-1.5 text-xs font-semibold",
                        info.estado === "vencida" && "bg-red-50 text-red-700",
                        info.estado === "vence_hoje" && "bg-orange-50 text-orange-700",
                        info.estado === "no_prazo" && "bg-muted text-muted-foreground",
                      )}
                    >
                      <CalendarClock className="h-3.5 w-3.5" />
                      {info.label}
                    </span>
                  )}
                </div>

                {prazoFatal && (
                  <p className={cn(
                    "text-[11px] font-medium",
                    estouraFatal ? "text-red-700" : "text-muted-foreground",
                  )}>
                    Prazo fatal estimado: <b>{fmtData(prazoFatal)}</b>
                    {estouraFatal && " — a conclusão está depois dele."}
                  </p>
                )}

                {perguntaData && (
                  <ChipsMotivo
                    titulo={`${textoDesvio(desvio)} — por quê?`}
                    opcoes={DATE_CHANGE_REASONS}
                    valor={d.dataMotivo}
                    onPick={(v) => patch(d.uid, { dataMotivo: v })}
                    obrigatorio={obrigaData}
                    disabled={disabled}
                  />
                )}

                {abertasNoL1.length > 0 && (
                  <div className="space-y-2">
                    <div className="rounded-lg border border-orange-200 bg-orange-50 px-3 py-2">
                      <p className="text-[11.5px] font-semibold text-orange-800">
                        Já existe {abertasNoL1.length} tarefa(s) aberta(s) deste subtipo nesta pasta:
                      </p>
                      <ul className="mt-1 space-y-0.5">
                        {abertasNoL1.slice(0, 3).map((t, i) => (
                          <li key={t.id ?? i} className="flex items-center gap-1.5 text-[11px] text-orange-900">
                            <span className="truncate">{t.description || `tarefa ${t.id}`}</span>
                            {t.l1_url && (
                              <a
                                href={t.l1_url}
                                target="_blank"
                                rel="noreferrer"
                                className="inline-flex shrink-0 items-center gap-0.5 font-semibold underline"
                              >
                                abrir <ExternalLink className="h-3 w-3" />
                              </a>
                            )}
                          </li>
                        ))}
                      </ul>
                    </div>
                    <ChipsMotivo
                      titulo="Por que agendar mesmo assim?"
                      opcoes={OPEN_TASK_REASONS}
                      valor={d.tarefaAbertaMotivo}
                      onPick={(v) => patch(d.uid, { tarefaAbertaMotivo: v })}
                      disabled={disabled}
                    />
                  </div>
                )}

                <details>
                  <summary className="cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground">
                    Observações {d.notes ? "(preenchidas)" : "(opcional)"}
                  </summary>
                  <Textarea
                    value={d.notes}
                    onChange={(e) => patch(d.uid, { notes: e.target.value })}
                    rows={4}
                    disabled={disabled}
                    className="mt-2 resize-y text-sm"
                    placeholder="Texto que vai nas observações da tarefa no Legal One"
                  />
                </details>

                {incompleta && (
                  <p className="flex items-center gap-1.5 text-xs text-amber-700">
                    <AlertTriangle className="h-3.5 w-3.5" />
                    Falta preencher: {falta.join(", ")}.
                  </p>
                )}
              </div>
            )}
          </div>
        );
      })}

      <Button
        type="button"
        variant="outline"
        onClick={adicionar}
        disabled={disabled}
        className="w-full border-dashed"
      >
        <Plus className="mr-2 h-4 w-4" />
        Adicionar outra tarefa
      </Button>
    </div>
  );
}

/**
 * Converte o rascunho no payload que o backend manda pro Legal One.
 *
 * Os marcadores viajam como chaves `_*` no mesmo objeto — o backend faz o pop
 * delas para o audit e a whitelist do cliente L1 impede que cheguem à API.
 * Diferente da tela clássica, aqui a anexação é um passe direto e incondicional:
 * lá ela ficava depois de um `continue` do roteamento de squad e não acontecia
 * no caso mais comum.
 */
export function draftParaPayload(
  d: DraftTask,
  officeId: number | null,
  extras: { consultouAutos?: boolean; motivoRemocao?: string | null } = {},
) {
  const dueIso = brtToUtcIso(d.dueDate, d.dueTime || "23:59");
  const office = d.responsibleOfficeId ?? officeId ?? null;
  const payload: Record<string, unknown> = {
    description: d.description.trim().slice(0, 250),
    priority: d.priority || "Normal",
    startDateTime: dueIso,
    endDateTime: dueIso,
    typeId: d.typeId,
    subTypeId: d.subTypeId,
    notes: d.notes?.trim() ? d.notes.trim() : null,
    status: { id: 0 },
    participants: d.responsibleExternalId
      ? [
          {
            contact: { id: d.responsibleExternalId },
            isResponsible: true,
            isExecuter: true,
            isRequester: true,
          },
        ]
      : [],
  };
  if (office) {
    payload.responsibleOfficeId = office;
    payload.originOfficeId = office;
  }

  if (trocouSubtipo(d) && d.subtipoMotivo) payload._subtipo_troca_motivo = d.subtipoMotivo;
  if (d.dataMotivo) payload._data_troca_motivo = d.dataMotivo;
  if (d.tarefaAbertaMotivo) payload._agendou_com_tarefa_aberta_motivo = d.tarefaAbertaMotivo;
  if (extras.consultouAutos) payload._consultou_autos = true;
  // A tarefa removida não é enviada, então o motivo dela pega carona na
  // primeira que vai — é como o backend espera recebê-lo.
  if (extras.motivoRemocao) payload._tarefa_removida_motivo = extras.motivoRemocao;

  return payload;
}

export default TaskComposer;
