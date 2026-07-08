import { useEffect, useRef, useState } from "react";
import { AlertCircle, CalendarClock, ExternalLink, Loader2, Pause, Play, RefreshCw, Rocket, RotateCw, Workflow } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import {
  fetchPublicationTreatmentMonitor,
  fetchPublicationTreatmentRuns,
  retryPublicationTreatmentItem,
  startPublicationTreatmentRun,
  updatePublicationTreatmentRunControl,
} from "@/services/api";
import {
  PublicationTreatmentItem,
  PublicationTreatmentMonitor,
  PublicationTreatmentRun,
} from "@/types/api";

function formatDateTime(value: string | null | undefined) {
  if (!value) return "N/A";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const [year, month, day] = value.split("-").map(Number);
    return new Intl.DateTimeFormat("pt-BR", {
      dateStyle: "short",
      timeZone: "America/Sao_Paulo",
    }).format(new Date(year, month - 1, day, 12, 0, 0));
  }
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "medium",
    timeZone: "America/Sao_Paulo",
  }).format(new Date(value));
}

function statusBadge(status: string) {
  const map: Record<string, string> = {
    EXECUTANDO: "bg-blue-100 text-blue-800",
    PAUSADO: "bg-amber-100 text-amber-800",
    CONCLUIDO: "bg-green-100 text-green-800",
    CONCLUIDO_COM_FALHAS: "bg-amber-100 text-amber-800",
    FALHA: "bg-red-100 text-red-800",
    INTERROMPIDO: "bg-slate-200 text-slate-800",
    PENDENTE: "bg-slate-100 text-slate-700",
    PROCESSANDO: "bg-blue-100 text-blue-800",
    CANCELADO: "bg-slate-200 text-slate-800",
  };
  return map[status] || "bg-slate-100 text-slate-700";
}

function targetLabel(value: string) {
  return value === "SEM_PROVIDENCIAS" ? "Sem providências" : "Tratada";
}

function queueLabel(value: string) {
  const map: Record<string, string> = {
    PENDENTE: "Pendente",
    PROCESSANDO: "Processando",
    CONCLUIDO: "Concluído",
    FALHA: "Falha",
    CANCELADO: "Cancelado",
  };
  return map[value] || value;
}

function runStatusLabel(value: string) {
  const map: Record<string, string> = {
    INICIANDO: "Iniciando",
    EXECUTANDO: "Em execução",
    PAUSADO: "Pausado",
    CONCLUIDO: "Concluído",
    CONCLUIDO_COM_FALHAS: "Concluído com falhas",
    FALHA: "Falhou",
    INTERROMPIDO: "Interrompido",
  };
  return map[value] || value;
}

const PUBLICATION_TREATMENT_REQUEST_TIMEOUT_MS = 15000;

async function withAbortTimeout<T>(
  label: string,
  request: (signal: AbortSignal) => Promise<T>,
): Promise<T> {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), PUBLICATION_TREATMENT_REQUEST_TIMEOUT_MS);

  try {
    return await request(controller.signal);
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      throw new Error(
        `${label} demorou mais que ${Math.round(PUBLICATION_TREATMENT_REQUEST_TIMEOUT_MS / 1000)}s para responder.`,
      );
    }
    throw err;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

function describeLoadFailure(label: string, err: unknown): string {
  const message = err instanceof Error ? err.message : "Erro desconhecido ao carregar dados.";
  return `${label}: ${message}`;
}

interface ItemRowProps {
  item: PublicationTreatmentItem;
  onRetry?: (item: PublicationTreatmentItem) => void;
  isRetryPending?: boolean;
  isRetryDisabled?: boolean;
}

function ItemRow({ item, onRetry, isRetryPending, isRetryDisabled }: ItemRowProps) {
  const canRetry = item.queue_status === "FALHA" && typeof onRetry === "function";
  return (
    <TableRow>
      <TableCell>{item.linked_lawsuit_cnj || "-"}</TableCell>
      <TableCell>{formatDateTime(item.publication_date)}</TableCell>
      <TableCell>{targetLabel(item.target_status)}</TableCell>
      <TableCell>
        <Badge className={statusBadge(item.queue_status)}>{queueLabel(item.queue_status)}</Badge>
      </TableCell>
      <TableCell>{item.attempt_count}</TableCell>
      <TableCell className="max-w-[360px] truncate">{item.last_error || "-"}</TableCell>
      <TableCell>
        <div className="flex items-center gap-3">
          {item.publication_link ? (
            <a
              href={item.publication_link}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-primary hover:underline"
            >
              Abrir
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          ) : (
            <span className="text-muted-foreground">-</span>
          )}
          {canRetry ? (
            <Button
              variant="outline"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={() => onRetry?.(item)}
              disabled={isRetryDisabled || isRetryPending}
            >
              {isRetryPending ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <RotateCw className="mr-1 h-3 w-3" />
              )}
              Reexecutar
            </Button>
          ) : null}
        </div>
      </TableCell>
    </TableRow>
  );
}

export default function PublicationTreatmentPage() {
  const { toast } = useToast();
  const [monitor, setMonitor] = useState<PublicationTreatmentMonitor | null>(null);
  const [runs, setRuns] = useState<PublicationTreatmentRun[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [retryingItemId, setRetryingItemId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const latestLoadIdRef = useRef(0);

  const loadData = async (showToast = false) => {
    const loadId = ++latestLoadIdRef.current;
    try {
      const [monitorResult, runsResult] = await Promise.allSettled([
        withAbortTimeout("O monitor do tratamento", (signal) => fetchPublicationTreatmentMonitor(signal)),
        withAbortTimeout("O histórico de execuções", (signal) => fetchPublicationTreatmentRuns(signal)),
      ]);

      if (loadId !== latestLoadIdRef.current) {
        return;
      }

      const issues: string[] = [];
      let monitorPayload: PublicationTreatmentMonitor | null = null;
      let hasSuccess = false;

      if (monitorResult.status === "fulfilled") {
        monitorPayload = monitorResult.value;
        setMonitor(monitorResult.value);
        hasSuccess = true;
      } else {
        issues.push(describeLoadFailure("Monitor indisponível", monitorResult.reason));
      }

      if (runsResult.status === "fulfilled") {
        setRuns(runsResult.value);
        hasSuccess = true;
      } else {
        issues.push(describeLoadFailure("Histórico indisponível", runsResult.reason));
      }

      if (!hasSuccess) {
        setMonitor(null);
        setRuns([]);
      }

      setError(issues.length ? issues.join(" ") : null);

      if (showToast) {
        if (issues.length === 0 && monitorPayload) {
          toast({
            title: "Painel atualizado",
            description: `${monitorPayload.summary.queue_count} item(ns) restante(s) na fila.`,
          });
        } else if (hasSuccess) {
          toast({
            title: "Atualização parcial",
            description: issues.join(" "),
            variant: "destructive",
          });
        } else if (issues.length) {
          toast({
            title: "Falha ao atualizar",
            description: issues.join(" "),
            variant: "destructive",
          });
        }
      }
    } catch (err) {
      if (loadId !== latestLoadIdRef.current) {
        return;
      }
      const message = err instanceof Error ? err.message : "Erro ao carregar o monitor.";
      setError(message);
      if (showToast) {
        toast({
          title: "Falha ao atualizar",
          description: message,
          variant: "destructive",
        });
      }
    } finally {
      if (loadId === latestLoadIdRef.current) {
        setIsLoading(false);
      }
    }
  };

  useEffect(() => {
    loadData();
    const intervalId = setInterval(() => {
      loadData();
    }, 5000);
    return () => clearInterval(intervalId);
  }, []);

  const handleStart = async () => {
    try {
      setIsSubmitting(true);
      const response = await startPublicationTreatmentRun();
      await loadData();
      if (response.started) {
        toast({
          title: "Tratamento iniciado",
          description: `${response.run?.total_items || 0} publicação(ões) enfileirada(s) para esta execução.`,
        });
        return;
      }
      toast({
        title: "Nada novo para iniciar",
        description:
          response.reason === "already_running"
            ? "Já existe um tratamento em execução."
            : "Nenhuma publicação pendente foi encontrada.",
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Não foi possível iniciar o tratamento.";
      toast({
        title: "Falha ao iniciar",
        description: message,
        variant: "destructive",
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleRetry = async (item: PublicationTreatmentItem) => {
    try {
      setRetryingItemId(item.id);
      setIsSubmitting(true);
      const response = await retryPublicationTreatmentItem(item.id);
      await loadData();
      if (response.started) {
        toast({
          title: "Reexecução iniciada",
          description: `Item #${item.id} foi reenfileirado para reprocessamento.`,
        });
        return;
      }
      toast({
        title: "Item reenfileirado",
        description:
          response.reason === "already_running"
            ? "Uma execução já está em andamento. O item entra no próximo ciclo."
            : "Item reenfileirado com sucesso.",
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Não foi possível reexecutar o item.";
      toast({
        title: "Falha ao reexecutar",
        description: message,
        variant: "destructive",
      });
    } finally {
      setRetryingItemId(null);
      setIsSubmitting(false);
    }
  };

  const isRunActive = Boolean(monitor?.active_run) && monitor?.active_run?.is_final === false;

  const handleControl = async (action: "pause" | "resume") => {
    if (!monitor?.active_run) return;
    try {
      setIsSubmitting(true);
      const response = await updatePublicationTreatmentRunControl(monitor.active_run.id, action);
      await loadData();
      toast({
        title: action === "pause" ? "Pausa solicitada" : "Execução retomada",
        description: `${response.message} Arquivo: ${response.control_file}`,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Não foi possível alterar a execução.";
      toast({
        title: "Falha ao alterar execução",
        description: message,
        variant: "destructive",
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const summaryCards = monitor
    ? [
        { title: "Registros elegíveis", value: monitor.summary.eligible_records },
        { title: "Na fila", value: monitor.summary.queue_count },
        { title: "Concluídos", value: monitor.summary.completed_count },
        { title: "Falhas", value: monitor.summary.failed_count },
        { title: "Tratar como tratada", value: monitor.summary.treated_target_count },
        { title: "Tratar como sem providências", value: monitor.summary.without_providence_target_count },
      ]
    : [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
              <Workflow className="h-6 w-6" />
              Tratamento de Publicações
            </h1>
            {monitor?.active_run ? (
              <Badge className={statusBadge(monitor.active_run.status)}>{runStatusLabel(monitor.active_run.status)}</Badge>
            ) : (
              <Badge variant="outline">Sem execução ativa</Badge>
            )}
            {monitor?.autorun?.enabled ? (
              <Badge variant="outline" className="gap-1 font-normal">
                <CalendarClock className="h-3.5 w-3.5" />
                Automático · próxima {formatDateTime(monitor.autorun.next_run_at)}
              </Badge>
            ) : null}
          </div>
          <p className="text-muted-foreground">
            Cada tratamento usa o `publicationId` exato retornado pela API do Legal One, então a execução fica segura
            mesmo quando há multiplicidade no mesmo processo e na mesma data.
            {monitor?.autorun?.enabled
              ? " As pendências também são tratadas automaticamente algumas vezes ao dia — o botão continua disponível pra rodadas extras."
              : ""}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            onClick={handleStart}
            disabled={isLoading || isSubmitting || !monitor || monitor?.active_run?.is_final === false}
          >
            <Rocket className="mr-2 h-4 w-4" />
            Iniciar execução
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => handleControl("pause")}
            disabled={isLoading || isSubmitting || !monitor?.active_run || monitor.control_signal === "pause"}
          >
            <Pause className="mr-2 h-4 w-4" />
            Pausar
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => handleControl("resume")}
            disabled={isLoading || isSubmitting || !monitor?.active_run || monitor.control_signal === "run"}
          >
            <Play className="mr-2 h-4 w-4" />
            Continuar
          </Button>
          <Button variant="outline" size="sm" onClick={() => loadData(true)} disabled={isLoading || isSubmitting}>
            <RefreshCw className={`mr-2 h-4 w-4 ${isLoading ? "animate-spin" : ""}`} />
            Atualizar
          </Button>
        </div>
      </div>

      {error ? (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Falha ao carregar monitor</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {summaryCards.map((card) => (
          <Card key={card.title} className="border-0 shadow-sm">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">{card.title}</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-semibold">{card.value}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle>Execução atual</CardTitle>
          <CardDescription>
            {monitor?.active_run
              ? `Última leitura ${formatDateTime(monitor.active_run.generated_at)}`
              : "Nenhuma execução ativa no momento."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {isLoading ? (
            <div className="flex min-h-24 items-center justify-center text-muted-foreground">
              <Loader2 className="mr-2 h-5 w-5 animate-spin" />
              Carregando...
            </div>
          ) : monitor?.active_run ? (
            <>
              <Progress value={monitor.progress_percentage} className="h-3" />
              <div className="grid gap-2 text-sm text-muted-foreground md:grid-cols-2 xl:grid-cols-3">
                <span>Progresso: {monitor.progress_percentage}%</span>
                <span>Processadas: {monitor.active_run.processed_items}/{monitor.active_run.total_items}</span>
                <span>Sucesso: {monitor.active_run.success_count}</span>
                <span>Falhas: {monitor.active_run.failed_count}</span>
                <span>Pausa entre lotes até: {formatDateTime(monitor.active_run.sleep_until)}</span>
                <span>Sinal atual: {monitor.control_signal === "pause" ? "Pausa solicitada" : "Execução livre"}</span>
              </div>
            </>
          ) : monitor ? (
            <Alert>
              <AlertCircle className="h-4 w-4" />
              <AlertTitle>Fila pronta para uso</AlertTitle>
              <AlertDescription>
                Assim que você iniciar uma execução, esta área passa a mostrar andamento, histórico e falhas recentes.
              </AlertDescription>
            </Alert>
          ) : (
            <div className="text-sm text-muted-foreground">
              Monitor indisponível no momento. Tente atualizar novamente quando o backend destravar.
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-6 xl:grid-cols-[1.3fr_1fr]">
        <Card className="border-0 shadow-sm">
          <CardHeader>
            <CardTitle>Falhas recentes</CardTitle>
            <CardDescription>Os links já abrem a publicação exata dentro do Legal One.</CardDescription>
          </CardHeader>
          <CardContent>
            {monitor?.recent_failures.length ? (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>CNJ</TableHead>
                      <TableHead>Disponibilização</TableHead>
                      <TableHead>Destino</TableHead>
                      <TableHead>Fila</TableHead>
                      <TableHead>Tent.</TableHead>
                      <TableHead>Erro</TableHead>
                      <TableHead></TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {monitor.recent_failures.map((item) => (
                      <ItemRow
                        key={`failure-${item.id}`}
                        item={item}
                        onRetry={handleRetry}
                        isRetryPending={retryingItemId === item.id}
                        isRetryDisabled={isRunActive || isSubmitting}
                      />
                    ))}
                  </TableBody>
                </Table>
              </div>
            ) : (
              <div className="text-sm text-muted-foreground">Nenhuma falha registrada nas últimas leituras.</div>
            )}
          </CardContent>
        </Card>

        <Card className="border-0 shadow-sm">
          <CardHeader>
            <CardTitle>Últimas execuções</CardTitle>
            <CardDescription>Histórico curto para conferência rápida.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {runs.length ? (
              runs.slice(0, 6).map((run) => (
                <div key={run.id} className="rounded-xl border border-border/70 p-4 space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <div className="font-semibold">Run #{run.id}</div>
                      <Badge variant="outline" className="text-xs font-normal">
                        {run.trigger_type === "MANUAL" ? "Manual" : "Automática"}
                      </Badge>
                    </div>
                    <Badge className={statusBadge(run.status)}>{runStatusLabel(run.status)}</Badge>
                  </div>
                  <div className="grid gap-1 text-sm text-muted-foreground">
                    <span>Início: {formatDateTime(run.started_at)}</span>
                    <span>Fim: {formatDateTime(run.finished_at)}</span>
                    <span>Sucesso/Falhas: {run.success_count}/{run.failed_count}</span>
                  </div>
                </div>
              ))
            ) : (
              <div className="text-sm text-muted-foreground">Ainda não houve execução desse módulo.</div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card className="border-0 shadow-sm">
        <CardHeader>
          <CardTitle>Fila recente</CardTitle>
          <CardDescription>Visão dos últimos itens sincronizados para tratamento.</CardDescription>
        </CardHeader>
        <CardContent>
          {monitor?.recent_items.length ? (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>CNJ</TableHead>
                    <TableHead>Disponibilização</TableHead>
                    <TableHead>Destino</TableHead>
                    <TableHead>Fila</TableHead>
                    <TableHead>Tent.</TableHead>
                    <TableHead>Erro</TableHead>
                    <TableHead></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {monitor.recent_items.map((item) => (
                    <ItemRow
                      key={item.id}
                      item={item}
                      onRetry={handleRetry}
                      isRetryPending={retryingItemId === item.id}
                      isRetryDisabled={isRunActive || isSubmitting}
                    />
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <div className="text-sm text-muted-foreground">Nenhum item de tratamento sincronizado ainda.</div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
