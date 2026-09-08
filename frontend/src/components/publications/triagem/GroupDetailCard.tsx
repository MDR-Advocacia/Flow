// Card de detalhe de um grupo (processo + suas publicações).
// Compartilhado pelos layouts Foco e Split. Traz o texto da publicação em
// tamanho legível, a classificação, os links para o Legal One e o compositor
// de tarefas — onde o operador revisa e edita antes de remeter.

import { useMemo, useState } from "react";
import {
  ArrowLeft, Bot, CalendarClock, Check, EyeOff, ExternalLink,
  FolderOpen, Loader2, Puzzle, SkipForward, ThumbsDown, X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { TaskComposer, draftValido } from "./TaskComposer";
import TextoPublicacao from "./TextoPublicacao";
import EtiquetasL1 from "./EtiquetasL1";
import ResponsavelPasta from "./ResponsavelPasta";
import SemPastaResumo from "./SemPastaResumo";
import { IGNORE_REASONS, LABEL_CONSULTOU_AUTOS } from "./motivos";
import { urlPasta, urlPublicacao } from "./l1";
import {
  BANDS, bandIndex, classificacaoDoGrupo, fmtData, idadeDias, idadeDoGrupo,
  labelIdade, prazoDoGrupo, prazoInfo, statusDoGrupo, STATUS_LABEL,
} from "./helpers";
import type { AppUser, DraftTask, GroupedRecord, PublicationRecord, TarefaAbertaL1, TaskType } from "./types";

/** Mantido exportado: a tela clássica e a auditoria usam a mesma lista. */
export const MOTIVOS_IGNORAR = IGNORE_REASONS;

/** Divisor das duas leituras do card: o que a IA entendeu, e o que vai subir. */
function SecaoTitulo({
  children, extra,
}: {
  children: React.ReactNode;
  extra?: React.ReactNode;
}) {
  return (
    <div className="mb-2.5 flex items-center gap-2 rounded-lg bg-muted px-3 py-1.5">
      <span className="text-[13px] font-extrabold uppercase tracking-wide text-foreground/80">
        {children}
      </span>
      {extra}
    </div>
  );
}

interface Props {
  group: GroupedRecord;
  drafts: DraftTask[];
  onDraftsChange: (d: DraftTask[]) => void;
  taskTypes: TaskType[];
  users: AppUser[];
  duplicatasPorSubtipo?: Record<number, TarefaAbertaL1[]>;
  consultouAutos?: boolean;
  onConsultouAutos?: (v: boolean) => void;
  onConfirm: () => void;
  onIgnore: (motivo: string, nota: string) => void;
  onSkip?: () => void;
  onBack?: () => void;
  /** Recebe a publicação da aba visível — o feedback é dela, não do grupo:
   *  um grupo pode ter publicações com classificações diferentes. */
  onFeedback?: (rec: PublicationRecord) => void;
  submitting?: boolean;
  showSkip?: boolean;
  kbdHints?: boolean;
}

export function GroupDetailCard({
  group, drafts, onDraftsChange, taskTypes, users, duplicatasPorSubtipo,
  consultouAutos, onConsultouAutos, onConfirm, onIgnore, onSkip, onBack,
  onFeedback, submitting, showSkip, kbdHints,
}: Props) {
  const [tab, setTab] = useState(0);
  const [ignorando, setIgnorando] = useState(false);
  const [motivo, setMotivo] = useState<string>("");
  const [nota, setNota] = useState("");

  const status = statusDoGrupo(group);
  const idade = idadeDoGrupo(group);
  const banda = BANDS[bandIndex(idade)];
  const prazo = prazoInfo(prazoDoGrupo(group));

  const records = useMemo(
    () => [...group.records].sort((a, b) => idadeDias(b) - idadeDias(a)),
    [group.records],
  );
  const rec = records[Math.min(tab, records.length - 1)] ?? records[0];
  const texto = rec?.description || rec?.description_preview || "";

  const classificacao = classificacaoDoGrupo(group);
  const prazoFatal = prazoDoGrupo(group);
  // O que a IA citou ao classificar — vira grifo dentro do texto.
  const trechosDaIA = useMemo(
    () => [classificacao?.justificativa, classificacao?.prazo_fundamentacao],
    [classificacao],
  );
  // pub010: ato da parte adversa que não exige nada de nós. É a célula que a
  // medição contra gabarito humano mostrou confiável (2,78% de falso-ignorar),
  // então aqui ela vira sugestão explícita — nunca decisão automática.
  const atoDaParteAdversa = useMemo(
    () =>
      group.records.some(
        (r) => r.quem_pratica_ato === "parte_adversa" && r.exige_providencia_nossa === false,
      ),
    [group.records],
  );
  const validos = drafts.filter(draftValido).length;
  const linkPasta = urlPasta(group.lawsuit_id);
  const linkPublicacao = urlPublicacao(rec?.legal_one_update_id);

  return (
    <article className="overflow-hidden rounded-2xl border bg-card/70 shadow-sm backdrop-blur">
      <header className="space-y-2 border-b px-5 py-4">
        <div className="flex flex-wrap items-center gap-2">
          {/* Selo de idade só quando é exceção: em toda publicação ele vira
              papel de parede e deixa de ser notado justamente onde importa. */}
          {idade > 7 ? (
            <span className={cn("inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold", banda.chip)}>
              <span className="h-1.5 w-1.5 rounded-full bg-current" />
              {labelIdade(idade)}
            </span>
          ) : (
            <span className="text-xs text-muted-foreground">{labelIdade(idade)}</span>
          )}
          {prazo && (
            <span
              className={cn(
                "rounded-full border px-3 py-1 text-xs font-semibold",
                prazo.estado === "vencida" && "border-red-200 bg-red-50 text-red-700",
                prazo.estado === "vence_hoje" && "border-orange-200 bg-orange-50 text-orange-700",
                prazo.estado === "no_prazo" && "border-border bg-muted/60 text-muted-foreground",
              )}
            >
              {prazo.estado === "vencida" ? "⚠ " : ""}{prazo.label}
            </span>
          )}
          <Badge
            className={cn(
              "text-[10px] font-extrabold tracking-wide",
              status === "classificado" && "bg-primary text-primary-foreground",
              status === "novo" && "bg-amber-400 text-amber-950 hover:bg-amber-400",
              status === "sem_template" && "bg-rose-100 text-rose-700 hover:bg-rose-100",
              status === "erro" && "bg-destructive text-destructive-foreground",
            )}
          >
            {STATUS_LABEL[status]}
          </Badge>

          <div className="ml-auto flex items-center gap-1.5">
            {linkPasta && (
              <a
                href={linkPasta}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 rounded-lg border bg-background px-2.5 py-1 text-[11.5px] font-semibold text-primary hover:border-primary/40"
                title="Abrir a pasta no Legal One, já na aba de Compromissos e Tarefas"
              >
                <FolderOpen className="h-3.5 w-3.5" /> Pasta no L1
                <ExternalLink className="h-3 w-3 opacity-70" />
              </a>
            )}
            {linkPublicacao && (
              <a
                href={linkPublicacao}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 rounded-lg border bg-background px-2.5 py-1 text-[11.5px] font-semibold text-muted-foreground hover:border-primary/40 hover:text-primary"
                title="Abrir esta publicação no Legal One"
              >
                Publicação <ExternalLink className="h-3 w-3 opacity-70" />
              </a>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-lg font-bold tracking-tight">
            {group.lawsuit_cnj || "sem processo vinculado"}
          </span>
          {group.lawsuit_id && (
            <span className="rounded-md bg-muted px-2 py-0.5 text-xs font-semibold text-muted-foreground">
              ID {group.lawsuit_id}
            </span>
          )}
          {/* Etiqueta da pasta: é ela que enseja direcionamento específico,
              então fica na linha do processo, antes de qualquer decisão. */}
          <EtiquetasL1 etiquetas={group.l1_etiquetas} tamanho="md" />
          {/* E de quem é a pasta. Fica na MESMA linha da etiqueta porque as
              duas respondem à mesma pergunta — que processo é este — e não
              na linha das datas, que é sobre a publicação. */}
          <ResponsavelPasta
            responsavel={group.responsavel_pasta}
            temPasta={Boolean(group.lawsuit_id)}
            tamanho="md"
            comRotulo
          />
        </div>

        {/* Fila sem pasta (pub014): aqui a pergunta não é "que providência",
            é "de que processo é isto" — o bloco mostra as pastas nossas que o
            texto cita, com link, antes de o operador ler qualquer coisa. */}
        {!group.lawsuit_id && <SemPastaResumo info={rec?.sem_pasta} />}

        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[13px] text-muted-foreground">
          <span>Publicação <b className="text-foreground">{fmtData(rec?.publication_date)}</b></span>
          <span>Captura <b className="text-foreground">{fmtData(rec?.created_at || rec?.creation_date)}</b></span>
          {records.length > 1 && <span>{records.length} publicações neste processo</span>}
        </div>
      </header>

      <div className="space-y-4 px-5 py-4">
        {records.length > 1 && (
          <div className="flex flex-wrap gap-1.5">
            {records.map((r, i) => (
              <button
                key={r.id}
                type="button"
                onClick={() => setTab(i)}
                className={cn(
                  "rounded-full border px-3 py-1 text-xs font-semibold transition-colors",
                  i === tab ? "border-foreground bg-foreground text-background" : "hover:bg-muted",
                )}
              >
                Publicação {i + 1} · {fmtData(r.publication_date)}
              </button>
            ))}
          </div>
        )}

        <div>
          <SecaoTitulo
            extra={
              records.length > 1 ? (
                <span className="text-[11px] font-medium text-muted-foreground">
                  {tab + 1} de {records.length}
                </span>
              ) : undefined
            }
          >
            Texto integral da publicação
          </SecaoTitulo>
          <TextoPublicacao texto={texto} trechos={trechosDaIA} />
        </div>

        <div>
          <SecaoTitulo>Classificação</SecaoTitulo>
          {classificacao ? (
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-2 rounded-xl border border-primary/25 bg-primary/5 px-3 py-2 text-[15px] font-bold text-primary">
                <span className="font-semibold opacity-80">{classificacao.categoria}</span>
                <span className="opacity-50">›</span>
                {classificacao.subcategoria}
              </span>
              {classificacao.polo && (
                <Badge variant="outline" className="text-[10px] uppercase">Polo {classificacao.polo}</Badge>
              )}
              {onFeedback && (
                <Button variant="ghost" size="sm" className="h-7 text-muted-foreground" onClick={() => rec && onFeedback(rec)} title="Reportar classificação errada">
                  <ThumbsDown className="h-3.5 w-3.5" />
                </Button>
              )}
              {/* Estimativa de TRIAGEM: dias úteis do default da taxonomia a
                  partir da publicação. Não é contagem oficial — não conhece
                  suspensão nem feriado local, e a decisão segue do operador. */}
              <span
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[12px]",
                  prazoFatal
                    ? prazo?.estado === "vencida"
                      ? "border-red-200 bg-red-50 text-red-700"
                      : "border-border bg-muted/50 text-foreground"
                    : "border-dashed text-muted-foreground",
                )}
                title={
                  prazoFatal
                    ? "Vencimento estimado do prazo processual, em dias úteis a partir da publicação (CPC art. 224). Estimativa de triagem, não contagem oficial."
                    : "A categoria ainda não tem prazo padrão na taxonomia."
                }
              >
                <CalendarClock className="h-3.5 w-3.5 shrink-0" />
                <span className="font-semibold">Prazo fatal estimado:</span>
                {prazoFatal ? (
                  <>
                    <b>{fmtData(prazoFatal)}</b>
                    {prazo && <span className="text-[11px]">({prazo.label})</span>}
                  </>
                ) : (
                  <span className="text-[11px]">sem prazo padrão na taxonomia</span>
                )}
              </span>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
              <Bot className="h-5 w-5 shrink-0" />
              <span className="flex-1">
                Sem classificação da IA. Você pode montar a tarefa na mão abaixo e agendar assim mesmo.
              </span>
            </div>
          )}
        </div>

        <div>
          <SecaoTitulo
            extra={
              status === "sem_template" ? (
                <span className="inline-flex items-center gap-1 text-[11px] font-medium text-muted-foreground">
                  <Puzzle className="h-3 w-3" /> sem template — monte a tarefa abaixo
                </span>
              ) : (
                drafts.filter(draftValido).length > 0 && (
                  <span className="text-[11px] font-medium text-muted-foreground">
                    {drafts.filter(draftValido).length} pronta(s) para envio
                  </span>
                )
              )
            }
          >
            Tarefas a agendar
          </SecaoTitulo>
          {atoDaParteAdversa && (
            <p className="mb-2 flex items-start gap-2 rounded-lg border border-dashed border-amber-300 bg-amber-50/60 px-3 py-2 text-[12px] text-amber-900">
              <EyeOff className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                A IA leu esta publicação como <b>ato da parte adversa, sem providência nossa</b> —
                confira o texto e, se concordar, use <b>Sem providência</b> em vez de agendar.
              </span>
            </p>
          )}
          <TaskComposer
            drafts={drafts}
            onChange={onDraftsChange}
            taskTypes={taskTypes}
            users={users}
            officeId={group.office_id}
            duplicatasPorSubtipo={duplicatasPorSubtipo}
            prazoFatal={prazoFatal}
            disabled={submitting}
          />
        </div>

        {/* Sinal do balde OPERADOR: a publicação sozinha bastou, ou foi preciso
            abrir os autos? Vale para as duas decisões (agendar e ignorar). */}
        {onConsultouAutos && (
          <label className="flex cursor-pointer items-start gap-2.5 rounded-xl border bg-muted/30 px-4 py-3">
            <Checkbox
              checked={Boolean(consultouAutos)}
              onCheckedChange={(v) => onConsultouAutos(v === true)}
              disabled={submitting}
              className="mt-0.5"
            />
            <span className="text-sm leading-snug">
              {LABEL_CONSULTOU_AUTOS.split("abrir o processo").map((parte, i, arr) => (
                <span key={i}>
                  {parte}
                  {i < arr.length - 1 && <b>abrir o processo</b>}
                </span>
              ))}
              <span className="block text-xs text-muted-foreground">
                A publicação sozinha não dizia o suficiente.
              </span>
            </span>
          </label>
        )}
      </div>

      <footer className="flex flex-wrap items-center gap-2 border-t bg-muted/30 px-5 py-4">
        <Button
          size="lg"
          variant={atoDaParteAdversa ? "outline" : "default"}
          onClick={onConfirm}
          disabled={submitting || validos === 0}
        >
          {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Check className="mr-2 h-4 w-4" />}
          Agendar {validos} tarefa{validos === 1 ? "" : "s"}
          {kbdHints && <kbd className={cn("ml-2 rounded border px-1.5 text-[10px]", atoDaParteAdversa ? "bg-background" : "border-white/30 bg-white/15")}>Enter</kbd>}
        </Button>
        {/* Mesmo tamanho do agendar: descartar é a decisão mais frequente e
            não pode ser a mais escondida. */}
        <Button
          size="lg"
          variant={atoDaParteAdversa ? "default" : "outline"}
          onClick={() => {
            setIgnorando((v) => !v);
            if (atoDaParteAdversa && !motivo) setMotivo("parte_adversa");
          }}
          disabled={submitting}
        >
          <EyeOff className="mr-2 h-4 w-4" />
          Sem providência
          {kbdHints && <kbd className={cn("ml-2 rounded border px-1.5 text-[10px]", atoDaParteAdversa ? "border-white/30 bg-white/15" : "bg-background")}>I</kbd>}
        </Button>
        <div className="flex-1" />
        {onBack && (
          <Button variant="ghost" onClick={onBack} disabled={submitting} className="text-muted-foreground">
            <ArrowLeft className="mr-1.5 h-4 w-4" />
            Voltar
            {kbdHints && <kbd className="ml-2 rounded border bg-background px-1.5 text-[10px]">←</kbd>}
          </Button>
        )}
        {showSkip && onSkip && (
          <Button variant="ghost" onClick={onSkip} disabled={submitting} className="text-muted-foreground">
            <SkipForward className="mr-1.5 h-4 w-4" />
            Pular
            {kbdHints && <kbd className="ml-2 rounded border bg-background px-1.5 text-[10px]">→</kbd>}
          </Button>
        )}
      </footer>

      {ignorando && (
        <div className="flex flex-wrap items-center gap-2 border-t bg-background px-5 py-3">
          <span className="text-xs font-bold text-muted-foreground">Por que ignorar?</span>
          {IGNORE_REASONS.map((m) => (
            <Button
              key={m.value}
              size="sm"
              variant={motivo === m.value ? "default" : "outline"}
              onClick={() => setMotivo(m.value)}
              disabled={submitting}
            >
              {m.label}
            </Button>
          ))}
          {motivo === "outro" && (
            <Textarea
              value={nota}
              onChange={(e) => setNota(e.target.value)}
              placeholder="Descreva o motivo"
              rows={2}
              className="mt-1 w-full text-sm"
            />
          )}
          <div className="flex w-full items-center gap-2 pt-1">
            <Button
              size="sm"
              variant="destructive"
              disabled={!motivo || (motivo === "outro" && !nota.trim()) || submitting}
              onClick={() => { onIgnore(motivo, nota); setIgnorando(false); setMotivo(""); setNota(""); }}
            >
              Confirmar ignorar
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setIgnorando(false)}>
              <X className="mr-1 h-3.5 w-3.5" /> cancelar
            </Button>
          </div>
        </div>
      )}
    </article>
  );
}

export default GroupDetailCard;
