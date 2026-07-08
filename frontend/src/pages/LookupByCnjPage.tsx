import { useState } from "react";
import {
  Search,
  Loader2,
  AlertCircle,
  ExternalLink,
  ChevronDown,
  ChevronRight,
  ClipboardCheck,
  Clock,
  Bot,
  Tag,
  Calendar,
  CheckCircle2,
  XCircle,
  ArrowRight,
  User,
  Building2,
  FileText,
} from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useToast } from "@/hooks/use-toast";
import { apiFetch } from "@/lib/api-client";

/* ─── Types ───────────────────────────────────────────────────────── */

type TreatmentInfo = {
  id: number;
  queue_status: string;
  target_status: string | null;
  source_record_status: string | null;
  attempt_count: number;
  last_run_id: number | null;
  last_error: string | null;
  treated_at: string | null;
  last_attempt_at: string | null;
  created_at: string | null;
  updated_at: string | null;
};

type RecordDetail = {
  id: number;
  search_id: number;
  legal_one_update_id: number;
  description_preview?: string;
  description?: string;
  notes?: string;
  publication_date: string | null;
  creation_date: string | null;
  linked_lawsuit_id: number | null;
  linked_lawsuit_cnj: string | null;
  linked_office_id: number | null;
  status: string;
  is_duplicate?: boolean;
  scheduled_by_name?: string | null;
  scheduled_by_email?: string | null;
  scheduled_at?: string | null;
  ignored_by_name?: string | null;
  ignored_by_email?: string | null;
  ignored_at?: string | null;
  category: string | null;
  subcategory: string | null;
  polo: string | null;
  audiencia_data: string | null;
  audiencia_hora: string | null;
  audiencia_link: string | null;
  classifications: any;
  treatment: TreatmentInfo | null;
  created_at: string | null;
  updated_at: string | null;
  requested_by_email: string | null;
  has_proposal: boolean;
  proposal: any;
  proposals_count: number;
  raw_relationships?: any;
};

type SearchInfo = {
  id: number;
  status: string;
  date_from: string;
  date_to: string | null;
  office_filter: string | null;
  requested_by_email: string | null;
  created_at: string | null;
  finished_at: string | null;
  total_found: number;
  total_new: number;
  total_duplicate: number;
};

type TimelineEvent = {
  timestamp: string;
  event: string;
  label: string;
  detail: string | null;
  user: string | null;
  record_id: number | null;
};

type LawsuitInfo = {
  id: number;
  cnj: string | null;
  creation_date: string | null;
  responsible_office_id: number | null;
  responsible_office_name: string | null;
};

type OverrideField = { proposto: unknown; enviado: unknown };

type SystemAdjustment = { antes: unknown; depois: unknown; motivo?: string };

type TaskAudit = {
  id: number;
  publication_record_id: number | null;
  created_task_id: number | null;
  subtype_id: number | null;
  override_detected: boolean;
  override_fields: Record<string, OverrideField> | null;
  system_adjustments?: Record<string, SystemAdjustment> | null;
  scheduled_by_name: string | null;
  scheduled_by_email: string | null;
  scheduled_at: string | null;
  sent_payload: Record<string, unknown> | null;
  proposed_payload: Record<string, unknown> | null;
  l1_task_url: string | null;
  template_id?: number | null;
  template_name?: string | null;
};

type LookupResponse = {
  cnj_input: string;
  cnj_normalized: string;
  cnj_display: string | null;
  lawsuit_id: number | null;
  lawsuit_info: LawsuitInfo | null;
  found: boolean;
  totals: {
    records: number;
    duplicates: number;
    by_status: Record<string, number>;
    by_category: Record<string, number>;
    by_queue_status: Record<string, number>;
  };
  timeline: TimelineEvent[];
  searches: SearchInfo[];
  records: RecordDetail[];
  task_audits?: TaskAudit[];
  task_audit_labels?: AuditLabels;
};

type AuditLabels = {
  subtypes: Record<string, string>;
  types: Record<string, string>;
  contacts: Record<string, string>;
  offices: Record<string, string>;
  statuses: Record<string, string>;
};

/* ─── Helpers ─────────────────────────────────────────────────────── */

function formatDate(value: string | null | undefined) {
  if (!value) return "—";
  if (/^\d{4}-\d{2}-\d{2}/.test(value)) {
    const d = new Date(value);
    if (!isNaN(d.getTime())) {
      return new Intl.DateTimeFormat("pt-BR", {
        dateStyle: "short",
        timeZone: "America/Sao_Paulo",
      }).format(d);
    }
  }
  return value;
}

function formatDateTime(value: string | null | undefined) {
  if (!value) return "—";
  const d = new Date(value);
  if (isNaN(d.getTime())) return value;
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: "America/Sao_Paulo",
  }).format(d);
}

function statusColor(status: string): string {
  const map: Record<string, string> = {
    NOVO: "bg-slate-100 text-slate-700",
    CLASSIFICADO: "bg-blue-100 text-blue-800",
    AGENDADO: "bg-green-100 text-green-800",
    IGNORADO: "bg-amber-100 text-amber-800",
    ERRO: "bg-red-100 text-red-800",
    DESCARTADO_DUPLICADA: "bg-purple-100 text-purple-800",
    DESCARTADO_OBSOLETA: "bg-orange-100 text-orange-800",
    PENDENTE: "bg-slate-100 text-slate-700",
    PROCESSANDO: "bg-blue-100 text-blue-800",
    CONCLUIDO: "bg-green-100 text-green-800",
    FALHA: "bg-red-100 text-red-800",
    CANCELADO: "bg-slate-200 text-slate-800",
  };
  return map[status] || "bg-slate-100 text-slate-700";
}

function eventIcon(event: string) {
  switch (event) {
    case "captura":
      return <Bot className="h-4 w-4 text-blue-500" />;
    case "classificacao":
      return <Tag className="h-4 w-4 text-indigo-500" />;
    case "status_change":
      return <ArrowRight className="h-4 w-4 text-amber-500" />;
    case "rpa_enfileirada":
      return <Clock className="h-4 w-4 text-slate-500" />;
    case "rpa_concluida":
      return <CheckCircle2 className="h-4 w-4 text-green-500" />;
    case "rpa_erro":
      return <XCircle className="h-4 w-4 text-red-500" />;
    case "tarefa_criada":
      return <ClipboardCheck className="h-4 w-4 text-emerald-600" />;
    default:
      return <Calendar className="h-4 w-4 text-slate-400" />;
  }
}

const OVERRIDE_FIELD_LABELS: Record<string, string> = {
  subTypeId: "Tipo de tarefa (subtipo)",
  responsibleOfficeId: "Escritório responsável",
  responsavel_contact_id: "Responsável (contact id)",
};

const SYSTEM_FIELD_LABELS: Record<string, string> = {
  endDateTime: "Data de conclusão",
  startDateTime: "Data de início",
  description: "Descrição",
  status: "Status",
  responsibleOfficeId: "Escritório responsável",
  originOfficeId: "Escritório de origem",
  publishDate: "Data de publicação",
};

function fmtVal(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function labelFor(map: Record<string, string> | undefined, id: unknown): string {
  if (id === null || id === undefined || id === "") return "—";
  const name = map?.[String(id)];
  return name ? `${name} (#${id})` : `#${id}`;
}

function translateDiffValue(field: string, value: unknown, labels?: AuditLabels): string {
  if (value === null || value === undefined || value === "") return "—";
  if (!labels) return fmtVal(value);
  if (field === "subTypeId") return labelFor(labels.subtypes, value);
  if (field === "responsavel_contact_id") return labelFor(labels.contacts, value);
  if (field === "responsibleOfficeId" || field === "originOfficeId") return labelFor(labels.offices, value);
  if (field === "endDateTime" || field === "startDateTime" || field === "publishDate") return formatDateTime(String(value));
  if (field === "status" && typeof value === "object") {
    return labelFor(labels.statuses, (value as { id?: unknown }).id);
  }
  return fmtVal(value);
}

function PayloadFicha({
  title,
  payload,
  labels,
  accent,
}: {
  title: string;
  payload: Record<string, unknown> | null;
  labels?: AuditLabels;
  accent?: boolean;
}) {
  const titleClass = `text-xs font-semibold mb-1 ${accent ? "text-emerald-700" : "text-muted-foreground"}`;
  if (!payload) {
    return (
      <div>
        <div className={titleClass}>{title}</div>
        <div className="text-xs text-muted-foreground bg-white p-2 rounded border">— sem payload registrado —</div>
      </div>
    );
  }
  const participants = Array.isArray(payload.participants)
    ? (payload.participants as Array<Record<string, unknown>>)
    : [];
  const participantLine = (p: Record<string, unknown>) => {
    const cid = (p.contact as { id?: unknown } | undefined)?.id;
    const papeis: string[] = [];
    if (p.isResponsible) papeis.push("responsável");
    if (p.isExecuter) papeis.push("executante");
    if (p.isRequester) papeis.push("solicitante");
    return `${labelFor(labels?.contacts, cid)}${papeis.length ? ` — ${papeis.join(", ")}` : ""}`;
  };
  const rows: Array<[string, string]> = [];
  if (payload.typeId != null) rows.push(["Tipo de tarefa", labelFor(labels?.types, payload.typeId)]);
  if (payload.subTypeId != null) rows.push(["Subtipo", labelFor(labels?.subtypes, payload.subTypeId)]);
  participants.forEach((p, i) =>
    rows.push([participants.length > 1 ? `Envolvido ${i + 1}` : "Envolvido", participantLine(p)]),
  );
  if (payload.responsibleOfficeId != null)
    rows.push(["Escritório responsável", labelFor(labels?.offices, payload.responsibleOfficeId)]);
  if (payload.originOfficeId != null)
    rows.push(["Escritório de origem", labelFor(labels?.offices, payload.originOfficeId)]);
  if (payload.startDateTime) rows.push(["Início", formatDateTime(String(payload.startDateTime))]);
  if (payload.endDateTime) rows.push(["Conclusão", formatDateTime(String(payload.endDateTime))]);
  if (payload.publishDate) rows.push(["Data de publicação", formatDateTime(String(payload.publishDate))]);
  if (payload.priority) rows.push(["Prioridade", String(payload.priority)]);
  const statusVal = payload.status as { id?: unknown } | undefined;
  if (statusVal && statusVal.id != null) rows.push(["Status", labelFor(labels?.statuses, statusVal.id)]);

  return (
    <div>
      <div className={titleClass}>{title}</div>
      <div className="bg-white rounded border divide-y">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-start gap-2 px-2 py-1.5 text-xs">
            <span className="text-muted-foreground w-40 shrink-0">{k}</span>
            <span className="font-medium break-words min-w-0">{v}</span>
          </div>
        ))}
        {typeof payload.description === "string" && payload.description && (
          <div className="px-2 py-1.5 text-xs">
            <span className="text-muted-foreground">Descrição</span>
            <p className="mt-0.5 whitespace-pre-wrap">{payload.description}</p>
          </div>
        )}
        {typeof payload.notes === "string" && payload.notes && (
          <details className="px-2 py-1.5 text-xs">
            <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
              Notas (texto da publicação)
            </summary>
            <pre className="mt-1 whitespace-pre-wrap font-sans max-h-48 overflow-y-auto">{String(payload.notes)}</pre>
          </details>
        )}
        <details className="px-2 py-1.5 text-xs">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">Ver JSON bruto</summary>
          <pre className="mt-1 whitespace-pre-wrap font-mono text-[11px] max-h-72 overflow-y-auto">
            {JSON.stringify(payload, null, 2)}
          </pre>
        </details>
      </div>
    </div>
  );
}

/* ─── Components ──────────────────────────────────────────────────── */

function LawsuitHeader({ info, cnj, lawsuitId }: { info: LawsuitInfo | null; cnj: string; lawsuitId: number | null }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-lg flex items-center gap-2">
          <FileText className="h-5 w-5" />
          Processo {cnj}
        </CardTitle>
        {lawsuitId && (
          <CardDescription>
            Legal One ID: <code className="text-xs">{lawsuitId}</code>
          </CardDescription>
        )}
      </CardHeader>
      <CardContent>
        {info ? (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-sm">
            <div className="flex items-center gap-2">
              <Calendar className="h-4 w-4 text-muted-foreground" />
              <div>
                <div className="text-muted-foreground text-xs">Criação da pasta</div>
                <div className="font-medium">{formatDate(info.creation_date)}</div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Building2 className="h-4 w-4 text-muted-foreground" />
              <div>
                <div className="text-muted-foreground text-xs">Escritório responsável</div>
                <div className="font-medium">{info.responsible_office_name || `ID ${info.responsible_office_id}` || "—"}</div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <ExternalLink className="h-4 w-4 text-muted-foreground" />
              <div>
                <div className="text-muted-foreground text-xs">Legal One</div>
                <a
                  href={`https://firm.legalone.com.br/lawsuits/${info.id}`}
                  target="_blank"
                  rel="noreferrer"
                  className="text-blue-600 hover:underline font-medium"
                >
                  Abrir processo
                </a>
              </div>
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            {lawsuitId
              ? "Não foi possível carregar dados do processo no Legal One."
              : "Nenhum lawsuit_id vinculado nos registros."}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function StatsCards({ totals }: { totals: LookupResponse["totals"] }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      <Card className="p-4">
        <div className="text-xs text-muted-foreground">Publicações</div>
        <div className="text-2xl font-bold">{totals.records}</div>
      </Card>
      <Card className="p-4">
        <div className="text-xs text-muted-foreground">Duplicatas</div>
        <div className="text-2xl font-bold">{totals.duplicates}</div>
      </Card>
      <Card className="p-4">
        <div className="text-xs text-muted-foreground">Na fila RPA</div>
        <div className="text-2xl font-bold">
          {Object.values(totals.by_queue_status).reduce((a, b) => a + b, 0)}
        </div>
      </Card>
      <Card className="p-4">
        <div className="text-xs text-muted-foreground">Classificações</div>
        <div className="text-2xl font-bold">
          {Object.values(totals.by_category).reduce((a, b) => a + b, 0)}
        </div>
      </Card>
    </div>
  );
}

function StatusSummary({ totals }: { totals: LookupResponse["totals"] }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">Por status</CardTitle>
        </CardHeader>
        <CardContent className="space-y-1.5 text-sm">
          {Object.entries(totals.by_status).map(([k, v]) => (
            <div key={k} className="flex items-center justify-between">
              <Badge variant="outline" className={statusColor(k)}>{k}</Badge>
              <span className="font-medium">{v}</span>
            </div>
          ))}
          {Object.keys(totals.by_status).length === 0 && (
            <div className="text-muted-foreground">—</div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">Por classificação</CardTitle>
        </CardHeader>
        <CardContent className="space-y-1.5 text-sm">
          {Object.entries(totals.by_category).map(([k, v]) => (
            <div key={k} className="flex items-center justify-between gap-2">
              <span className="truncate" title={k}>{k}</span>
              <span className="font-medium shrink-0">{v}</span>
            </div>
          ))}
          {Object.keys(totals.by_category).length === 0 && (
            <div className="text-muted-foreground">Nenhuma classificada</div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">Fila RPA</CardTitle>
        </CardHeader>
        <CardContent className="space-y-1.5 text-sm">
          {Object.entries(totals.by_queue_status).map(([k, v]) => (
            <div key={k} className="flex items-center justify-between">
              <Badge variant="outline" className={statusColor(k)}>{k}</Badge>
              <span className="font-medium">{v}</span>
            </div>
          ))}
          {Object.keys(totals.by_queue_status).length === 0 && (
            <div className="text-muted-foreground">Nenhuma enfileirada</div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Timeline({ events }: { events: TimelineEvent[] }) {
  if (events.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <Clock className="h-4 w-4" />
          Timeline de eventos
        </CardTitle>
        <CardDescription>Histórico cronológico de tudo que aconteceu com as publicações deste processo.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="relative pl-6 space-y-0">
          {/* Vertical line */}
          <div className="absolute left-[11px] top-2 bottom-2 w-px bg-border" />

          {events.map((ev, i) => (
            <div key={i} className="relative flex items-start gap-3 pb-4 last:pb-0">
              {/* Dot on timeline */}
              <div className="absolute -left-6 mt-0.5 flex h-6 w-6 items-center justify-center rounded-full bg-background border">
                {eventIcon(ev.event)}
              </div>

              <div className="flex-1 min-w-0 ml-2">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium">{ev.label}</span>
                  <span className="text-xs text-muted-foreground">
                    {formatDateTime(ev.timestamp)}
                  </span>
                  {ev.record_id && (
                    <span className="text-xs text-muted-foreground font-mono">
                      pub #{ev.record_id}
                    </span>
                  )}
                </div>
                {ev.detail && (
                  <p className="text-xs text-muted-foreground mt-0.5">{ev.detail}</p>
                )}
                {ev.user && (
                  <div className="flex items-center gap-1 text-xs text-muted-foreground mt-0.5">
                    <User className="h-3 w-3" />
                    {ev.user}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function TaskAuditCard({ audit, labels }: { audit: TaskAudit; labels?: AuditLabels }) {
  const [showPayloads, setShowPayloads] = useState(false);
  const overrides = Object.entries(audit.override_fields || {});
  const adjustments = Object.entries(audit.system_adjustments || {});

  return (
    <div className="border rounded-lg p-4 space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <ClipboardCheck className="h-4 w-4 text-emerald-600 shrink-0" />
        <span className="font-semibold text-sm">
          Tarefa {audit.created_task_id ? `#${audit.created_task_id}` : "—"}
        </span>
        {audit.subtype_id != null && (
          <span className="text-xs text-muted-foreground">{labelFor(labels?.subtypes, audit.subtype_id)}</span>
        )}
        {audit.template_id ? (
          <a
            href={`/publications/templates?template_id=${audit.template_id}`}
            target="_blank"
            rel="noreferrer"
            className="text-xs inline-flex items-center gap-1 text-blue-600 hover:underline"
            title="Abrir a configuração do template que gerou esta proposta"
          >
            template: {audit.template_name || `#${audit.template_id}`}
            <ExternalLink className="h-3 w-3" />
          </a>
        ) : null}
        {audit.override_detected ? (
          <Badge variant="outline" className="bg-amber-100 text-amber-800">
            override do operador
          </Badge>
        ) : (
          <Badge variant="outline" className="bg-green-100 text-green-800">
            conforme proposta
          </Badge>
        )}
        {adjustments.length > 0 && (
          <Badge variant="outline" className="bg-sky-100 text-sky-800">
            ajuste automático do sistema
          </Badge>
        )}
        {audit.l1_task_url && (
          <a
            href={audit.l1_task_url}
            target="_blank"
            rel="noreferrer"
            className="text-xs inline-flex items-center gap-1 text-blue-600 hover:underline ml-auto"
          >
            Abrir no Legal One <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </div>

      <div className="text-sm text-muted-foreground flex flex-wrap gap-x-4 gap-y-1">
        <span className="flex items-center gap-1">
          <User className="h-3 w-3" />
          {audit.scheduled_by_name || audit.scheduled_by_email || "operador não registrado"}
        </span>
        <span>{formatDateTime(audit.scheduled_at)}</span>
      </div>

      {overrides.length > 0 && (
        <div className="text-xs bg-amber-50 border border-amber-200 rounded p-2 space-y-1">
          <div className="font-semibold text-amber-800">O que o operador mudou em relação à proposta automática:</div>
          {overrides.map(([field, diff]) => (
            <div key={field} className="flex items-center gap-2 flex-wrap">
              <span className="text-muted-foreground">{OVERRIDE_FIELD_LABELS[field] || field}:</span>
              <code className="bg-white px-1 rounded border">{translateDiffValue(field, diff.proposto, labels)}</code>
              <ArrowRight className="h-3 w-3 text-amber-600" />
              <code className="bg-white px-1 rounded border font-semibold">{translateDiffValue(field, diff.enviado, labels)}</code>
            </div>
          ))}
        </div>
      )}

      {adjustments.length > 0 && (
        <div className="text-xs bg-sky-50 border border-sky-200 rounded p-2 space-y-1.5">
          <div className="font-semibold text-sky-800">
            O que o sistema ajustou automaticamente antes de enviar (não foi o operador):
          </div>
          {adjustments.map(([field, adj]) => (
            <div key={field} className="space-y-0.5">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-muted-foreground">{SYSTEM_FIELD_LABELS[field] || field}:</span>
                <code className="bg-white px-1 rounded border">{translateDiffValue(field, adj.antes, labels)}</code>
                <ArrowRight className="h-3 w-3 text-sky-600" />
                <code className="bg-white px-1 rounded border font-semibold">{translateDiffValue(field, adj.depois, labels)}</code>
              </div>
              {adj.motivo && <div className="text-sky-700 pl-1">↳ {adj.motivo}</div>}
            </div>
          ))}
        </div>
      )}

      <button
        onClick={() => setShowPayloads(!showPayloads)}
        className="text-xs text-muted-foreground hover:text-foreground font-semibold uppercase tracking-wide flex items-center gap-1"
      >
        {showPayloads ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        Tarefa enviada × proposta automática (detalhe traduzido)
      </button>
      {showPayloads && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <PayloadFicha
            title="ENVIADO AO LEGAL ONE (efetivo)"
            payload={audit.sent_payload}
            labels={labels}
            accent
          />
          <PayloadFicha
            title="PROPOSTA AUTOMÁTICA"
            payload={audit.proposed_payload}
            labels={labels}
          />
        </div>
      )}
    </div>
  );
}

function RecordCard({ record }: { record: RecordDetail }) {
  const [expanded, setExpanded] = useState(false);

  const cls = Array.isArray(record.classifications) && record.classifications.length > 0
    ? record.classifications[0]
    : null;

  // Templates que geraram as propostas — link direto pra configuração
  // (conferir/corrigir a regra da classificação sem caçar na listagem).
  const proposalTemplates: Array<{ template_id: number; template_name?: string | null }> = (
    Array.isArray(record.raw_relationships?._proposed_tasks)
      ? record.raw_relationships._proposed_tasks
      : record.proposal
        ? [record.proposal]
        : []
  ).filter((p: any) => p && p.template_id);

  return (
    <div className="border rounded-lg overflow-hidden">
      {/* Header — always visible */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full text-left p-4 hover:bg-slate-50 transition-colors"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap mb-1">
              {expanded ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}
              <Badge variant="outline" className={statusColor(record.status)}>{record.status}</Badge>
              {record.is_duplicate && (
                <Badge variant="outline" className={statusColor("DESCARTADO_DUPLICADA")}>duplicata</Badge>
              )}
              {record.polo && <Badge variant="outline">polo: {record.polo}</Badge>}
              {record.category && (
                <span className="text-xs font-medium text-blue-700">
                  {record.category}{record.subcategory && record.subcategory !== "-" ? ` / ${record.subcategory}` : ""}
                </span>
              )}
            </div>
            <div className="text-sm text-muted-foreground flex flex-wrap gap-x-3 gap-y-1">
              <span>Publicação: <strong className="text-foreground">{formatDate(record.publication_date)}</strong></span>
              <span>Capturada: {formatDateTime(record.created_at)}</span>
              <span>Busca #{record.search_id}</span>
              {record.requested_by_email && (
                <span className="flex items-center gap-1"><User className="h-3 w-3" />{record.requested_by_email}</span>
              )}
              {record.status === "AGENDADO" && (record.scheduled_by_name || record.scheduled_by_email) && (
                <span className="flex items-center gap-1 text-green-700">
                  <User className="h-3 w-3" />
                  agendou: {record.scheduled_by_name || record.scheduled_by_email}
                </span>
              )}
              {record.status === "IGNORADO" && (record.ignored_by_name || record.ignored_by_email) && (
                <span className="flex items-center gap-1 text-amber-700">
                  <User className="h-3 w-3" />
                  ciência: {record.ignored_by_name || record.ignored_by_email}
                </span>
              )}
            </div>
          </div>
          {record.legal_one_update_id && (
            <a
              href={`https://firm.legalone.com.br/publications?publicationId=${record.legal_one_update_id}&treatStatus=3`}
              target="_blank"
              rel="noreferrer"
              className="text-xs inline-flex items-center gap-1 text-blue-600 hover:underline shrink-0"
              onClick={(e) => e.stopPropagation()}
            >
              Legal One <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </div>
      </button>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t bg-slate-50/50 p-4 space-y-4">
          {/* Classificação IA */}
          <div>
            <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Classificação IA</h4>
            {record.category ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
                <div>
                  <span className="text-muted-foreground">Categoria:</span>{" "}
                  <strong>{record.category}</strong>
                  {record.subcategory && record.subcategory !== "-" && <> / {record.subcategory}</>}
                </div>
                {record.polo && (
                  <div>
                    <span className="text-muted-foreground">Polo:</span> <strong>{record.polo}</strong>
                  </div>
                )}
                {(record.audiencia_data || record.audiencia_hora) && (
                  <div>
                    <span className="text-muted-foreground">Audiência:</span>{" "}
                    <strong>{record.audiencia_data || "?"} {record.audiencia_hora || ""}</strong>
                    {record.audiencia_link && (
                      <a href={record.audiencia_link} target="_blank" rel="noreferrer"
                        className="ml-2 text-blue-600 hover:underline text-xs">
                        link da videoconferência
                      </a>
                    )}
                  </div>
                )}
                {cls?.confianca != null && (
                  <div>
                    <span className="text-muted-foreground">Confiança:</span>{" "}
                    <strong>{typeof cls.confianca === "number" ? `${(cls.confianca * 100).toFixed(0)}%` : cls.confianca}</strong>
                  </div>
                )}
                {cls?.justificativa && (
                  <div className="sm:col-span-2">
                    <span className="text-muted-foreground">Justificativa IA:</span>
                    <p className="mt-1 text-xs bg-white p-2 rounded border">{cls.justificativa}</p>
                  </div>
                )}
                {proposalTemplates.length > 0 && (
                  <div className="sm:col-span-2 flex flex-wrap items-center gap-x-3 gap-y-1">
                    <span className="text-muted-foreground">
                      Template{proposalTemplates.length > 1 ? "s" : ""} do agendamento:
                    </span>
                    {proposalTemplates.map((p: any) => (
                      <a
                        key={p.template_id}
                        href={`/publications/templates?template_id=${p.template_id}`}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 text-blue-600 hover:underline"
                        title="Abrir a configuração deste template"
                      >
                        {p.template_name || `#${p.template_id}`}
                        <ExternalLink className="h-3 w-3" />
                      </a>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">Não classificada</p>
            )}
          </div>

          {/* Proposta de tarefa */}
          {record.has_proposal && record.proposal && (
            <div>
              <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Proposta de Tarefa</h4>
              <div className="text-sm bg-white p-2 rounded border">
                <pre className="whitespace-pre-wrap font-sans text-xs">
                  {typeof record.proposal === "object" ? JSON.stringify(record.proposal, null, 2) : String(record.proposal)}
                </pre>
              </div>
            </div>
          )}

          {/* Tratamento humano — quem agendou / deu ciência */}
          {(record.scheduled_by_name || record.scheduled_by_email || record.ignored_by_name || record.ignored_by_email) && (
            <div>
              <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Tratamento humano</h4>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
                {(record.scheduled_by_name || record.scheduled_by_email) && (
                  <div className="flex items-center gap-1.5">
                    <User className="h-3.5 w-3.5 text-green-600" />
                    <span className="text-muted-foreground">Agendado por:</span>{" "}
                    <strong>{record.scheduled_by_name || record.scheduled_by_email}</strong>
                    {record.scheduled_at && (
                      <span className="text-xs text-muted-foreground">em {formatDateTime(record.scheduled_at)}</span>
                    )}
                  </div>
                )}
                {(record.ignored_by_name || record.ignored_by_email) && (
                  <div className="flex items-center gap-1.5">
                    <User className="h-3.5 w-3.5 text-amber-600" />
                    <span className="text-muted-foreground">Ciência dada por:</span>{" "}
                    <strong>{record.ignored_by_name || record.ignored_by_email}</strong>
                    {record.ignored_at && (
                      <span className="text-xs text-muted-foreground">em {formatDateTime(record.ignored_at)}</span>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Fila RPA */}
          <div>
            <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Fila RPA</h4>
            {record.treatment ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
                <div>
                  <span className="text-muted-foreground">Status fila:</span>{" "}
                  <Badge variant="outline" className={statusColor(record.treatment.queue_status)}>
                    {record.treatment.queue_status}
                  </Badge>
                </div>
                <div>
                  <span className="text-muted-foreground">Alvo:</span>{" "}
                  <strong>{record.treatment.target_status || "—"}</strong>
                </div>
                <div>
                  <span className="text-muted-foreground">Tentativas:</span>{" "}
                  <strong>{record.treatment.attempt_count}</strong>
                </div>
                {record.treatment.treated_at && (
                  <div>
                    <span className="text-muted-foreground">Tratada em:</span>{" "}
                    <strong>{formatDateTime(record.treatment.treated_at)}</strong>
                  </div>
                )}
                {record.treatment.last_error && (
                  <div className="sm:col-span-2 text-red-600 text-xs bg-red-50 p-2 rounded">
                    Erro: {record.treatment.last_error}
                  </div>
                )}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">Não enfileirada</p>
            )}
          </div>

          {/* Texto da publicação */}
          {record.description && (
            <details className="text-xs">
              <summary className="cursor-pointer text-muted-foreground hover:text-foreground font-semibold uppercase tracking-wide">
                Texto completo da publicação
              </summary>
              <pre className="mt-2 whitespace-pre-wrap font-sans text-xs bg-white p-3 rounded border max-h-64 overflow-y-auto">
                {record.description}
              </pre>
            </details>
          )}

          {/* Metadados técnicos */}
          <div className="text-xs text-muted-foreground border-t pt-2 flex flex-wrap gap-x-4 gap-y-1">
            <span>ID interno: {record.id}</span>
            <span>update_id: {record.legal_one_update_id}</span>
            <span>lawsuit_id: {record.linked_lawsuit_id || "—"}</span>
            <span>office_id: {record.linked_office_id || "—"}</span>
            <span>criado: {formatDateTime(record.created_at)}</span>
            {record.updated_at && <span>atualizado: {formatDateTime(record.updated_at)}</span>}
          </div>
        </div>
      )}
    </div>
  );
}

/* ─── Page ────────────────────────────────────────────────────────── */

const LookupByCnjPage = () => {
  const { toast } = useToast();
  const [cnj, setCnj] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<LookupResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const doSearch = async () => {
    if (!cnj.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await apiFetch(
        `/api/v1/publications/lookup-by-cnj?cnj=${encodeURIComponent(cnj.trim())}`,
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${res.status}`);
      }
      const data = (await res.json()) as LookupResponse;
      setResult(data);
      if (!data.found) {
        toast({
          title: "Nenhuma publicação encontrada",
          description: `Nada indexado no sistema para o CNJ ${data.cnj_normalized}.`,
        });
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      toast({ title: "Erro na busca", description: msg, variant: "destructive" });
    } finally {
      setLoading(false);
    }
  };

  const onSubmit = (ev: React.FormEvent) => {
    ev.preventDefault();
    doSearch();
  };

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Auditoria de Processo</h1>
        <p className="text-sm text-muted-foreground">
          Consulta completa: publicações capturadas, classificações, agendamentos, tratamento RPA e dados do processo no Legal One.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Número do processo (CNJ)</CardTitle>
          <CardDescription>
            Pode colar com ou sem formatação (0000000-00.0000.0.00.0000 ou só dígitos).
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="flex gap-2">
            <Input
              value={cnj}
              onChange={(e) => setCnj(e.target.value)}
              placeholder="Ex: 1234567-89.2024.8.26.0100"
              autoFocus
            />
            <Button type="submit" disabled={loading || !cnj.trim()}>
              {loading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Search className="h-4 w-4" />
              )}
              <span className="ml-2">Buscar</span>
            </Button>
          </form>
        </CardContent>
      </Card>

      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Falha na consulta</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {result && result.found && (
        <>
          {/* Header do processo com dados do Legal One */}
          <LawsuitHeader
            info={result.lawsuit_info}
            cnj={result.cnj_display || result.cnj_normalized}
            lawsuitId={result.lawsuit_id}
          />

          {/* Números resumidos */}
          <StatsCards totals={result.totals} />

          {/* Timeline de eventos */}
          <Timeline events={result.timeline} />

          {/* Tarefas efetivamente criadas no Legal One (auditoria de agendamento) */}
          {(result.task_audits?.length ?? 0) > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base flex items-center gap-2">
                  <ClipboardCheck className="h-4 w-4 text-emerald-600" />
                  Tarefas criadas no Legal One ({result.task_audits!.length})
                </CardTitle>
                <CardDescription>
                  O que foi <strong>efetivamente enviado</strong> ao Legal One — não a proposta. Cada tarefa mostra o
                  operador que agendou, o antes/depois quando ele alterou subtipo, escritório ou responsável, e — em
                  azul — o que o <strong>sistema</strong> ajustou sozinho (ex.: data vencida movida pro próximo dia útil).
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {result.task_audits!.map((a) => (
                  <TaskAuditCard key={a.id} audit={a} labels={result.task_audit_labels} />
                ))}
              </CardContent>
            </Card>
          )}

          {/* Resumos por status / classificação / RPA */}
          <StatusSummary totals={result.totals} />

          {/* Buscas que capturaram este processo */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Buscas que capturaram publicações deste processo</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {result.searches.map((s) => (
                <div key={s.id} className="flex items-center gap-3 text-sm border rounded p-2">
                  <span className="font-mono text-xs">#{s.id}</span>
                  <Badge variant="outline" className={statusColor(s.status)}>{s.status}</Badge>
                  <span className="text-muted-foreground">
                    {s.date_from} → {s.date_to || "—"}
                  </span>
                  <span className="text-xs">{s.total_found} encontradas · {s.total_new} novas</span>
                  {s.requested_by_email && (
                    <span className="text-xs flex items-center gap-1 text-muted-foreground">
                      <User className="h-3 w-3" />{s.requested_by_email}
                    </span>
                  )}
                  <span className="text-xs text-muted-foreground ml-auto">{formatDateTime(s.created_at)}</span>
                </div>
              ))}
            </CardContent>
          </Card>

          {/* Publicações — cards expansíveis */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                Publicações ({result.records.length})
              </CardTitle>
              <CardDescription>
                Clique em cada publicação para expandir os detalhes completos (classificação IA, justificativa, fila RPA, texto).
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {result.records.map((r) => (
                <RecordCard key={r.id} record={r} />
              ))}
            </CardContent>
          </Card>
        </>
      )}

      {result && !result.found && (
        <Alert>
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Nenhum registro</AlertTitle>
          <AlertDescription>
            O sistema não tem nenhuma publicação indexada para o CNJ{" "}
            <code>{result.cnj_normalized}</code>. Pode ser que o robô ainda não tenha passado
            pelo período onde esse processo teve publicação, ou que o processo não esteja
            no escritório buscado.
          </AlertDescription>
        </Alert>
      )}
    </div>
  );
};

export default LookupByCnjPage;
