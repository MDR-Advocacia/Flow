import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Calendar,
  CheckCircle2,
  ExternalLink,
  FileText,
  Link2,
  Loader2,
  Search,
  XCircle,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  getOneNotifyBBDetail,
  getOneNotifyBBStats,
  listOneNotifyBBNotifications,
  OneNotifyBBDetail,
  OneNotifyBBSummary,
} from "@/services/onenotify-bb";

const PAGE_SIZE = 500;

function formatPct(value?: number | null) {
  if (value === undefined || value === null) return "0%";
  return `${Math.round(value * 100)}%`;
}

function statusLabel(status?: string) {
  const labels: Record<string, string> = {
    CONCILIADA_AUTO: "Conciliada automaticamente",
    PENDENTE_FLOW: "Pendente no Flow",
    PENDENTE_DOCUMENTO: "Documento para tratamento",
    REVISAO: "Revisão manual",
    RECEBIDA: "Recebida",
    ERRO: "Erro",
  };
  return labels[status || ""] || status || "Sem status";
}

function formatDate(value?: string | null) {
  if (!value) return "-";
  const raw = String(value);
  if (/^\d{4}-\d{2}-\d{2}/.test(raw)) {
    const [year, month, day] = raw.slice(0, 10).split("-");
    return `${day}/${month}/${year}`;
  }
  return raw;
}

function field(record: Record<string, unknown> | null | undefined, key: string) {
  const value = record?.[key];
  return value === undefined || value === null || value === "" ? "-" : String(value);
}

function arrayField(record: Record<string, unknown> | null | undefined, key: string): Record<string, unknown>[] {
  const value = record?.[key];
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => !!item && typeof item === "object") : [];
}

function statusClass(status?: string) {
  if (status === "CONCILIADA_AUTO") return "bg-green-100 text-green-700";
  if (status === "PENDENTE_DOCUMENTO") return "bg-blue-100 text-blue-700";
  if (status === "REVISAO" || status === "PENDENTE_FLOW") return "bg-amber-100 text-amber-700";
  return "bg-slate-100 text-slate-700";
}

function typeLabel(item: OneNotifyBBSummary) {
  const joined = (item.tipos_notificacao || []).join(", ");
  if (/PUBLICA/i.test(joined)) return "Andamento de publicação";
  if (/DOC/i.test(joined)) return "Documento";
  return joined || "Notificação";
}

function Metric({ title, value, tone }: { title: string; value: string; tone: string }) {
  return (
    <div className={`rounded-lg border bg-white p-4 shadow-sm ${tone}`}>
      <div className="text-sm font-medium text-slate-500">{title}</div>
      <div className="mt-2 text-3xl font-bold tracking-normal text-slate-950">{value}</div>
    </div>
  );
}

function DiffGrid({ detail }: { detail: OneNotifyBBDetail }) {
  const rows = detail.diff?.rows || [];
  const notifyText = detail.text_content || "";
  const notifyTextTruncated =
    Boolean(detail.conteudo?.texto_truncado) || notifyText.trim().endsWith("...");
  return (
    <div className="overflow-hidden rounded-lg border">
      <div className="flex items-center justify-between border-b bg-slate-50 px-4 py-3">
        <div>
          <h3 className="text-base font-semibold text-slate-950">Diff textual determinístico</h3>
          <p className="text-sm text-slate-500">Normalização + blocos de texto, calculado no backend.</p>
        </div>
        <Badge className="bg-green-100 text-green-700">Score {formatPct(detail.match_score)}</Badge>
      </div>
      <details className="border-b px-4 py-3">
        <summary className="cursor-pointer text-sm font-semibold text-blue-600">
          {notifyTextTruncated ? "Texto disponível na amostra" : "Textos completos sem abreviação"}
        </summary>
        {notifyTextTruncated && (
          <div className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">
            O texto do OneNotify nesta base local veio de um recorte de comparação e está truncado.
            Em produção, o intake deve receber o texto integral capturado pela RPA.
          </div>
        )}
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md bg-slate-50 p-3 text-sm text-slate-800">
            {notifyText || "Sem texto no OneNotify."}
          </pre>
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md bg-slate-50 p-3 text-sm text-slate-800">
            {String(detail.matched_publication?.description || detail.matched_publication?.notes || "Sem publicação vinculada.")}
          </pre>
        </div>
      </details>
      <div className="grid grid-cols-[48px_1fr_48px_1fr] bg-slate-50 text-xs font-bold uppercase text-slate-500">
        <div className="border-r px-3 py-2" />
        <div className="border-r px-3 py-2">OneNotify</div>
        <div className="border-r px-3 py-2" />
        <div className="px-3 py-2">Flow</div>
      </div>
      <div className="max-h-[560px] overflow-auto">
        {rows.map((row, index) => {
          const kindClass =
            row.kind === "equal"
              ? "bg-white"
              : row.kind === "insert"
                ? "bg-green-50 text-green-800"
                : row.kind === "delete"
                  ? "bg-red-50 text-red-800"
                  : "bg-amber-50 text-amber-900";
          return (
            <div key={index} className={`grid grid-cols-[48px_1fr_48px_1fr] border-t font-mono text-sm ${kindClass}`}>
              <div className="border-r px-3 py-2 text-right text-slate-400">{row.left_line || ""}</div>
              <div className="border-r px-3 py-2 whitespace-pre-wrap">{row.left}</div>
              <div className="border-r px-3 py-2 text-right text-slate-400">{row.right_line || ""}</div>
              <div className="px-3 py-2 whitespace-pre-wrap">{row.right}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DetailModal({
  id,
  onClose,
}: {
  id: number | null;
  onClose: () => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["onenotify-bb-detail", id],
    queryFn: () => getOneNotifyBBDetail(id as number),
    enabled: !!id,
  });

  return (
    <Dialog open={!!id} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[92vh] max-w-6xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {data ? `${typeLabel(data)} - ${data.npj}` : "Notificação BB"}
          </DialogTitle>
        </DialogHeader>
        {isLoading && (
          <div className="flex items-center gap-2 py-10 text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin" />
            Carregando...
          </div>
        )}
        {data && (
          <div className="space-y-4">
            <section className="rounded-lg border p-4">
              <div className="mb-3 flex items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-bold uppercase text-slate-500">Notificação do portal do cliente</p>
                  <h2 className="text-xl font-bold text-slate-950">{typeLabel(data)}</h2>
                </div>
                <Badge className={statusClass(data.flow_status)}>{statusLabel(data.flow_status)}</Badge>
              </div>
              <div className="grid gap-3 text-sm md:grid-cols-3">
                <div><span className="font-semibold text-slate-500">NPJ</span><div className="font-mono whitespace-nowrap">{data.npj}</div></div>
                <div><span className="font-semibold text-slate-500">Data</span><div>{data.publication_date || data.notification_date_iso}</div></div>
                <div><span className="font-semibold text-slate-500">Posição do cliente</span><div>{data.posicao_cliente || "Não identificada"}</div></div>
                <div><span className="font-semibold text-slate-500">CNJ principal Notify</span><div className="font-mono whitespace-nowrap">{data.cnj_principal_notify || "-"}</div></div>
                <div><span className="font-semibold text-slate-500">CNJ da publicação</span><div className="font-mono whitespace-nowrap">{data.cnj_publicacao || "-"}</div></div>
                <div><span className="font-semibold text-slate-500">Publicação Flow</span><div>{data.matched_publication_record_id ? `#${data.matched_publication_record_id}` : "Sem vínculo"}</div></div>
              </div>
              {data.cnj_divergent && (
                <div className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-sm font-semibold text-amber-800">
                  CNJ principal do Notify difere do CNJ encontrado no texto da publicação.
                </div>
              )}
            </section>

            {data.matched_publication && (
              <section className="rounded-lg border p-4">
                <div className="mb-4 flex items-start justify-between gap-3">
                  <div>
                    <p className="text-xs font-bold uppercase text-slate-500">Publicação Legal One conciliada</p>
                    <h3 className="text-lg font-bold text-slate-950">
                      Publicação #{field(data.matched_publication, "id")} (LO: {field(data.matched_publication, "legal_one_update_id")})
                    </h3>
                  </div>
                  <Badge className="bg-green-100 text-green-700">Conciliada {formatPct(data.match_score)}</Badge>
                </div>
                <div className="grid gap-3 text-sm md:grid-cols-2">
                  <div><span className="font-semibold text-slate-500">Status</span><div>{field(data.matched_publication, "status")}</div></div>
                  <div><span className="font-semibold text-slate-500">Data da publicação</span><div>{formatDate(field(data.matched_publication, "publication_date"))}</div></div>
                  <div><span className="font-semibold text-slate-500">Data de captura</span><div>{formatDate(field(data.matched_publication, "creation_date"))}</div></div>
                  <div><span className="font-semibold text-slate-500">Processo</span><div className="font-mono whitespace-nowrap">{field(data.matched_publication, "linked_lawsuit_cnj")}</div></div>
                  <div><span className="font-semibold text-slate-500">Categoria</span><div>{field(data.matched_publication, "category")}</div></div>
                  <div><span className="font-semibold text-slate-500">Subcategoria</span><div>{field(data.matched_publication, "subcategory")}</div></div>
                </div>
                {arrayField(data.matched_publication, "classifications").length > 1 && (
                  <div className="mt-4">
                    <span className="text-sm font-semibold text-slate-500">Classificações adicionais</span>
                    <div className="mt-2 space-y-1">
                      {arrayField(data.matched_publication, "classifications").slice(1).map((classification, index) => (
                        <div key={index} className="rounded border bg-slate-50 px-2 py-1 text-xs">
                          <span className="font-medium">{field(classification, "categoria")}</span>
                          {field(classification, "subcategoria") !== "-" && (
                            <span className="text-slate-500"> / {field(classification, "subcategoria")}</span>
                          )}
                          {field(classification, "polo") !== "-" && (
                            <Badge className="ml-2 bg-rose-50 text-rose-700">{field(classification, "polo")}</Badge>
                          )}
                          {field(classification, "confianca") !== "-" && (
                            <span className="ml-2 text-slate-500">({field(classification, "confianca")})</span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                <div className="mt-4 flex flex-wrap gap-3">
                  {field(data.matched_publication, "legal_one_update_id") !== "-" && (
                    <a
                      href={`https://firm.legalone.com.br/publications?publicationId=${field(data.matched_publication, "legal_one_update_id")}&treatStatus=3`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 rounded border border-blue-300 bg-blue-50 px-2 py-1 text-xs font-medium text-blue-700 hover:underline"
                    >
                      <Link2 className="h-3 w-3" />
                      Abrir publicação no Legal One
                      <ExternalLink className="h-3 w-3" />
                    </a>
                  )}
                  {field(data.matched_publication, "linked_lawsuit_id") !== "-" && (
                    <a
                      href={`https://mdradvocacia.novajus.com.br/processos/Processos/DetailsCompromissosTarefas/${field(data.matched_publication, "linked_lawsuit_id")}?renderOnlySection=True`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 rounded border border-violet-300 bg-violet-50 px-2 py-1 text-xs font-medium text-violet-700 hover:underline"
                    >
                      <Calendar className="h-3 w-3" />
                      Abrir compromissos e tarefas no Legal One
                      <ExternalLink className="h-3 w-3" />
                    </a>
                  )}
                </div>
                {(() => {
                  const proposal =
                    data.matched_publication?.proposal &&
                    typeof data.matched_publication.proposal === "object"
                      ? (data.matched_publication.proposal as Record<string, unknown>)
                      : null;
                  const payload =
                    proposal?.payload && typeof proposal.payload === "object"
                      ? (proposal.payload as Record<string, unknown>)
                      : null;
                  const description = field(payload, "description");
                  const templateName = field(proposal, "template_name");
                  const priority = field(payload, "priority");
                  const startDate = field(payload, "startDateTime");
                  const endDate = field(payload, "endDateTime");

                  if (!proposal || (description === "-" && templateName === "-")) {
                    return null;
                  }

                  return (
                    <div className="mt-4 rounded-md border bg-slate-50 p-3">
                      <p className="text-sm font-semibold text-slate-700">Tratamento proposto pelo Flow</p>
                      {templateName !== "-" && (
                        <p className="mt-1 text-sm text-slate-800">{templateName}</p>
                      )}
                      {description !== "-" && (
                        <p className="mt-2 text-sm text-slate-600">{description}</p>
                      )}
                      <div className="mt-3 flex flex-wrap gap-2 text-xs">
                        {priority !== "-" && (
                          <Badge className="bg-amber-100 text-amber-800">Prioridade: {priority}</Badge>
                        )}
                        {startDate !== "-" && (
                          <Badge className="bg-blue-100 text-blue-800">Início: {formatDate(startDate)}</Badge>
                        )}
                        {endDate !== "-" && (
                          <Badge className="bg-blue-100 text-blue-800">Fim: {formatDate(endDate)}</Badge>
                        )}
                      </div>
                    </div>
                  );
                })()}
              </section>
            )}

            <DiffGrid detail={data} />
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default function OneNotifyBBPage() {
  const [page, setPage] = useState(0);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const stats = useQuery({ queryKey: ["onenotify-bb-stats"], queryFn: getOneNotifyBBStats });
  const list = useQuery({
    queryKey: ["onenotify-bb-list", page, q, status],
    queryFn: () =>
      listOneNotifyBBNotifications({
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        q: q || undefined,
        status: status === "all" ? undefined : status,
      }),
  });

  const totalPages = useMemo(() => {
    const total = list.data?.total || 0;
    return Math.max(1, Math.ceil(total / PAGE_SIZE));
  }, [list.data?.total]);
  const firstItem = (list.data?.offset ?? 0) + 1;
  const lastItem = Math.min((list.data?.offset ?? 0) + (list.data?.items.length ?? 0), list.data?.total ?? 0);

  return (
    <>
      <div className="space-y-6">
        <div>
          <p className="text-xs font-bold uppercase tracking-normal text-slate-500">Tratamento de Publicações</p>
          <h1 className="text-3xl font-bold tracking-normal text-slate-950">Notificações BB</h1>
        </div>

        <div className="grid gap-4 md:grid-cols-5">
          <Metric title="Notificações analisadas" value={String(stats.data?.total ?? 0)} tone="border-l-4 border-l-blue-500" />
          <Metric title="Com publicação equivalente" value={`${stats.data?.matched ?? 0} (${stats.data?.matched_pct ?? 0}%)`} tone="border-l-4 border-l-green-500" />
          <Metric title="Conciliadas automaticamente" value={`${stats.data?.auto_conciliated ?? 0} (${stats.data?.auto_conciliated_pct ?? 0}%)`} tone="border-l-4 border-l-violet-500" />
          <Metric title="CNJ divergente" value={String(stats.data?.cnj_divergent ?? 0)} tone="border-l-4 border-l-amber-500" />
          <Metric title="Sem match" value={String(stats.data?.no_match ?? 0)} tone="border-l-4 border-l-red-500" />
        </div>

        <section className="rounded-lg border bg-white shadow-sm">
          <div className="grid gap-4 border-b p-4 md:grid-cols-[1fr_260px]">
            <div>
              <label className="text-sm font-semibold text-slate-500">Buscar</label>
              <div className="relative mt-1">
                <Search className="absolute left-3 top-3 h-4 w-4 text-slate-400" />
                <Input
                  className="pl-9"
                  placeholder="NPJ, CNJ ou parte"
                  value={q}
                  onChange={(event) => {
                    setPage(0);
                    setQ(event.target.value);
                  }}
                />
              </div>
            </div>
            <div>
              <label className="text-sm font-semibold text-slate-500">Status</label>
              <Select
                value={status}
                onValueChange={(value) => {
                  setPage(0);
                  setStatus(value);
                }}
              >
                <SelectTrigger className="mt-1">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Todos</SelectItem>
                  <SelectItem value="CONCILIADA_AUTO">Conciliadas automaticamente</SelectItem>
                  <SelectItem value="PENDENTE_DOCUMENTO">Documento para tratamento</SelectItem>
                  <SelectItem value="PENDENTE_FLOW">Pendentes no Flow</SelectItem>
                  <SelectItem value="REVISAO">Revisão manual</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="flex items-center justify-between border-b p-4">
            <div>
              <h2 className="text-xl font-bold text-slate-950">Fila unificada</h2>
              <p className="text-sm text-slate-500">
                {list.data?.total
                  ? `Exibindo ${firstItem.toLocaleString("pt-BR")}-${lastItem.toLocaleString("pt-BR")} de ${(list.data.total).toLocaleString("pt-BR")} registros reais`
                  : "Nenhum registro carregado"}
              </p>
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button
                variant={status === "all" ? "default" : "outline"}
                onClick={() => {
                  setPage(0);
                  setStatus("all");
                }}
              >
                Fila completa
              </Button>
              <Button
                variant={status === "CONCILIADA_AUTO" ? "default" : "outline"}
                onClick={() => {
                  setPage(0);
                  setStatus("CONCILIADA_AUTO");
                }}
              >
                Conciliadas automaticamente
              </Button>
              <Button
                variant={status === "PENDENTE_FLOW" ? "default" : "outline"}
                onClick={() => {
                  setPage(0);
                  setStatus("PENDENTE_FLOW");
                }}
              >
                Pendentes no Flow
              </Button>
              <Button variant="outline" disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>
                Anterior
              </Button>
              <span className="text-sm text-slate-500">{page + 1} / {totalPages}</span>
              <Button variant="outline" disabled={page + 1 >= totalPages} onClick={() => setPage((p) => p + 1)}>
                Próxima
              </Button>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[1180px] text-left text-sm">
              <thead className="border-b bg-slate-50 text-xs uppercase text-slate-500">
                <tr>
                  <th className="px-4 py-3">Origem</th>
                  <th className="px-4 py-3">Data</th>
                  <th className="px-4 py-3">Posição do cliente</th>
                  <th className="px-4 py-3">NPJ</th>
                  <th className="px-4 py-3">CNJ da publicação</th>
                  <th className="px-4 py-3">Tipo</th>
                  <th className="px-4 py-3">Flow</th>
                  <th className="px-4 py-3">Visualização</th>
                </tr>
              </thead>
              <tbody>
                {list.isLoading && (
                  <tr>
                    <td colSpan={8} className="px-4 py-10 text-center text-slate-500">
                      <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
                      Carregando...
                    </td>
                  </tr>
                )}
                {!list.isLoading && (list.data?.items || []).length === 0 && (
                  <tr>
                    <td colSpan={8} className="px-4 py-10 text-center text-slate-500">Nenhuma notificação encontrada.</td>
                  </tr>
                )}
                {(list.data?.items || []).map((item) => (
                  <tr key={item.id} className="border-b last:border-b-0 hover:bg-slate-50">
                    <td className="px-4 py-3">
                      <Badge className="bg-blue-100 text-blue-900">OneNotify</Badge>
                      <div className="mt-1 text-xs text-slate-500">Notify #{item.notify_ids?.[0] || item.id}</div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="font-medium">{item.publication_date || item.notification_date_iso || "-"}</div>
                      <div className="text-xs text-slate-500">Notificado: {item.data_notificacao || "-"}</div>
                    </td>
                    <td className="px-4 py-3">
                      <Badge className="bg-blue-100 text-blue-950">{item.posicao_cliente || "Não identificado"}</Badge>
                    </td>
                    <td className="px-4 py-3 font-mono whitespace-nowrap">{item.npj || "-"}</td>
                    <td className="px-4 py-3 font-mono whitespace-nowrap">
                      {item.cnj_publicacao || "-"}
                      {item.cnj_divergent && <Badge className="ml-2 bg-amber-100 text-amber-700">Divergente</Badge>}
                    </td>
                    <td className="px-4 py-3">{typeLabel(item)}</td>
                    <td className="px-4 py-3">
                      <Badge className={statusClass(item.flow_status)}>
                        {item.flow_status === "CONCILIADA_AUTO" && <CheckCircle2 className="mr-1 h-3 w-3" />}
                        {(item.flow_status === "REVISAO" || item.flow_status === "PENDENTE_FLOW") && <AlertTriangle className="mr-1 h-3 w-3" />}
                        {item.flow_status === "PENDENTE_DOCUMENTO" && <FileText className="mr-1 h-3 w-3" />}
                        {item.flow_status === "ERRO" && <XCircle className="mr-1 h-3 w-3" />}
                        {statusLabel(item.flow_status)}
                      </Badge>
                      <div className="mt-1 text-xs text-slate-500">
                        {item.matched_publication_record_id
                          ? `Flow #${item.matched_publication_record_id} - LO #${item.matched_legal_one_update_id || "-"}`
                          : "Sem publicação vinculada"}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <Button variant="outline" size="sm" onClick={() => setSelectedId(item.id)}>
                        Ver detalhe
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
      <DetailModal id={selectedId} onClose={() => setSelectedId(null)} />
    </>
  );
}
