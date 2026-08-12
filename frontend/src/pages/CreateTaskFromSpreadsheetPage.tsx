import { useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, Download, Eye, EyeOff, File, HelpCircle, ListChecks, Loader2, Pause, Play, Square, Upload } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/hooks/use-toast";
import { apiFetch } from "@/lib/api-client";
import {
  cancelBatchExecution,
  downloadBatchErrorReport,
  pauseBatchExecution,
  resumeBatchExecution,
} from "@/services/api";
import AcessoriosMenu from "@/components/tarefas-planilha/AcessoriosMenu";
import BatchExecutionsPage from "./BatchExecutionsPage";


interface PreviewRow {
  row_id: number;
  process_number?: string | null;
  is_valid: boolean;
  errors: string[];
  warnings: string[];
  data: Record<string, any>;
}

interface PreviewSummary {
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  duplicate_rows_in_file: number;
  duplicate_rows_in_history: number;
}

interface PreviewResponse {
  filename: string;
  headers: string[];
  summary: PreviewSummary;
  rows: PreviewRow[];
}

interface BatchStatusResponse {
  id: number;
  status: string;
  total_items: number;
  processed_items: number;
  remaining_items: number;
  success_count: number;
  failure_count: number;
  percentage: number;
  source_filename?: string | null;
  requested_by_email?: string | null;
  can_pause: boolean;
  can_resume: boolean;
  can_cancel: boolean;
}


const CreateTaskFromSpreadsheetPage = () => {
  const { toast } = useToast();
  const [tab, setTab] = useState("criar");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isDownloadingTemplate, setIsDownloadingTemplate] = useState(false);
  const [isControlLoading, setIsControlLoading] = useState<"pause" | "resume" | "cancel" | "report" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [batchId, setBatchId] = useState<number | null>(null);
  const [batchStatus, setBatchStatus] = useState<BatchStatusResponse | null>(null);
  const [isMonitoring, setIsMonitoring] = useState(false);
  const [showBatchDialog, setShowBatchDialog] = useState(false);

  const previewProblems = preview?.rows.filter((row) => !row.is_valid) ?? [];

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    const isSpreadsheet =
      file.type === "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" ||
      file.name.toLowerCase().endsWith(".xlsx");

    if (!isSpreadsheet) {
      setError("Formato de arquivo invalido. Por favor, selecione um arquivo .xlsx.");
      setSelectedFile(null);
      setPreview(null);
      return;
    }

    setSelectedFile(file);
    setPreview(null);
    setError(null);
  };

  const handleTemplateDownload = async () => {
    setIsDownloadingTemplate(true);
    try {
      const response = await apiFetch("/api/v1/tasks/spreadsheet-template");
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || "Falha ao baixar o modelo.");
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "modelo_agendamento_planilha.xlsx";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      toast({
        title: "Erro ao baixar modelo",
        description: err instanceof Error ? err.message : "Erro inesperado.",
        variant: "destructive",
      });
    } finally {
      setIsDownloadingTemplate(false);
    }
  };

  const handlePreview = async () => {
    if (!selectedFile) {
      setError("Nenhum arquivo selecionado.");
      return;
    }

    setIsPreviewing(true);
    setError(null);

    const formData = new FormData();
    formData.append("file", selectedFile);

    try {
      const response = await apiFetch("/api/v1/tasks/preview-spreadsheet", {
        method: "POST",
        body: formData,
      });
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "Falha ao validar a planilha.");
      }

      const data: PreviewResponse = await response.json();
      setPreview(data);
      toast({
        title: "Preview gerado",
        description: `${data.summary.valid_rows} linhas prontas e ${data.summary.invalid_rows} com alerta ou erro.`,
      });
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "Erro desconhecido.";
      setError(errorMessage);
      toast({
        title: "Erro no preview",
        description: errorMessage,
        variant: "destructive",
      });
    } finally {
      setIsPreviewing(false);
    }
  };

  const handleSubmit = async () => {
    if (!selectedFile) {
      setError("Nenhum arquivo selecionado.");
      return;
    }

    setIsSubmitting(true);
    setError(null);

    const formData = new FormData();
    formData.append("file", selectedFile);

    try {
      const response = await apiFetch("/api/v1/tasks/batch-create-from-spreadsheet", {
        method: "POST",
        body: formData,
      });
      if (response.status !== 202) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "Ocorreu uma falha ao enviar o arquivo.");
      }

      const data = await response.json();
      if (!data.batch_id) {
        throw new Error("O backend nao retornou o identificador do lote.");
      }

      setBatchId(data.batch_id);
      setBatchStatus(null);
      setIsMonitoring(true);
      setShowBatchDialog(true);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "Erro desconhecido.";
      setError(errorMessage);
      toast({
        title: "Erro no envio",
        description: errorMessage,
        variant: "destructive",
      });
      setIsSubmitting(false);
    }
  };

  const handleControlAction = async (action: "pause" | "resume" | "cancel" | "report") => {
    if (!batchId) return;

    setIsControlLoading(action);
    try {
      if (action === "pause") {
        await pauseBatchExecution(batchId);
      } else if (action === "resume") {
        await resumeBatchExecution(batchId);
      } else if (action === "cancel") {
        await cancelBatchExecution(batchId);
      } else if (action === "report") {
        const blob = await downloadBatchErrorReport(batchId);
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `lote_${batchId}_erros.csv`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
      }
    } catch (err) {
      toast({
        title: "Falha na acao do lote",
        description: err instanceof Error ? err.message : "Erro inesperado.",
        variant: "destructive",
      });
    } finally {
      setIsControlLoading(null);
    }
  };

  useEffect(() => {
    let intervalId: NodeJS.Timeout;

    if (isMonitoring && batchId) {
      intervalId = setInterval(async () => {
        try {
          const response = await apiFetch(`/api/v1/tasks/batch/status/${batchId}`);
          if (!response.ok) return;

          const data: BatchStatusResponse = await response.json();
          setBatchStatus(data);

          if (
            data.status === "CONCLUIDO" ||
            data.status === "CONCLUIDO_COM_FALHAS" ||
            data.status === "CANCELADO"
          ) {
            clearInterval(intervalId);
            setIsMonitoring(false);
            setIsSubmitting(false);

            toast({
              title:
                data.status === "CANCELADO"
                  ? "Lote cancelado"
                  : data.status === "CONCLUIDO_COM_FALHAS"
                    ? "Lote finalizado com falhas"
                    : "Lote finalizado com sucesso",
              description: `${data.success_count} sucesso(s) e ${data.failure_count} falha(s).`,
              variant: data.failure_count > 0 || data.status === "CANCELADO" ? "destructive" : undefined,
            });
          }
        } catch (err) {
          console.error("Erro ao buscar status do lote:", err);
        }
      }, 1500);
    }

    return () => clearInterval(intervalId);
  }, [batchId, isMonitoring, toast]);

  return (
    <div className="space-y-6">
      {/* Chip de lote rodando com o progresso OCULTO — clica pra reabrir o
          controle. O lote é server-backed; o polling da página segue vivo. */}
      {isMonitoring && !showBatchDialog && (
        <button
          type="button"
          onClick={() => setShowBatchDialog(true)}
          className="flex w-full items-center gap-2 rounded-lg border border-blue-200 bg-blue-50/60 px-3 py-2 text-left text-sm text-blue-800 transition-colors hover:bg-blue-100/60"
        >
          <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
          <span className="font-medium">Lote em processamento</span>
          <span className="tabular-nums">
            {batchStatus?.processed_items ?? 0}/{batchStatus?.total_items ?? 0}
          </span>
          <span className="ml-auto flex items-center gap-1 text-xs font-medium">
            <Eye className="h-3.5 w-3.5" /> Ver progresso
          </span>
        </button>
      )}

      <div className="flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
            <Upload className="h-6 w-6" />
            Tarefas por Planilha
          </h1>
          <p className="text-muted-foreground">
            Crie tarefas em lote a partir de uma planilha e acompanhe as execuções.
          </p>
        </div>
        {tab === "criar" && (
        <div className="flex gap-2">
          <AcessoriosMenu />
          <Dialog>
            <DialogTrigger asChild>
              <Button variant="outline" size="sm">
                <HelpCircle className="mr-2 h-4 w-4" />
                Como preencher
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-2xl max-h-[80vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>Como preencher a planilha de agendamento</DialogTitle>
                <DialogDescription>
                  Cada linha vira uma tarefa agendada no Legal One, vinculada ao processo ou pasta.
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-4 text-sm">
                <section className="space-y-1">
                  <h4 className="font-semibold">Identificação do processo: CNJ ou PASTA</h4>
                  <p className="text-muted-foreground">
                    Cada linha precisa de <strong>um</strong> identificador — preencha{" "}
                    <strong>CNJ</strong> <em>ou</em> <strong>PASTA</strong> (não os dois):
                  </p>
                  <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
                    <li>
                      <strong>CNJ</strong> — processos judiciais com número (ex.:{" "}
                      <code>0000000-00.2026.8.00.0000</code>).
                    </li>
                    <li>
                      <strong>PASTA</strong> — processos <strong>sem CNJ</strong> (ex.: cobrança
                      pré-judicial). Cole o número da pasta <strong>exatamente como aparece no Legal
                      One</strong>, com o prefixo (ex.: <code>Proc - 0069519</code>). Só o número
                      (<code>69519</code>) não funciona.
                    </li>
                  </ul>
                  <p className="text-muted-foreground">
                    Pode misturar linhas por CNJ e por PASTA no mesmo arquivo.
                  </p>
                </section>

                <section className="space-y-1">
                  <h4 className="font-semibold">Colunas obrigatórias</h4>
                  <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
                    <li>
                      <strong>ESCRITORIO</strong> — escritório de origem, caminho completo (ex.:{" "}
                      MDR Advocacia / Filial BA / Cível).
                    </li>
                    <li>
                      <strong>CNJ ou PASTA</strong> — identificador do processo (ver acima).
                    </li>
                    <li>
                      <strong>PUBLISH_DATE</strong> — data de publicação/ciência (dd/mm/aaaa).
                    </li>
                    <li>
                      <strong>SUBTIPO</strong> — subtipo da tarefa, exatamente como cadastrado no L1.
                    </li>
                    <li>
                      <strong>EXECUTANTE</strong> — responsável/executante, pelo nome cadastrado no L1.
                    </li>
                    <li>
                      <strong>DATA_TAREFA</strong> — data do prazo/compromisso (dd/mm/aaaa).
                    </li>
                  </ul>
                </section>

                <section className="space-y-1">
                  <h4 className="font-semibold">Colunas opcionais</h4>
                  <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
                    <li><strong>PRAZO</strong> — data que entra na descrição da tarefa (dd/mm/aaaa).</li>
                    <li><strong>HORARIO</strong> — hora da tarefa (HH:MM). Em branco, assume 23:59.</li>
                    <li><strong>OBSERVACAO</strong> — vai para as anotações da tarefa.</li>
                    <li><strong>DESCRICAO</strong> — complemento da descrição.</li>
                  </ul>
                </section>

                <section className="space-y-1">
                  <h4 className="font-semibold">Antes de enviar</h4>
                  <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
                    <li>Use <strong>Validar Planilha</strong> para conferir erros sem criar nada.</li>
                    <li>Agendamentos idênticos já criados são bloqueados (anti-duplicidade).</li>
                    <li>Baixe o <strong>Modelo</strong> para já começar no formato certo.</li>
                  </ul>
                </section>
              </div>
            </DialogContent>
          </Dialog>
          <Button variant="outline" size="sm" onClick={handleTemplateDownload} disabled={isDownloadingTemplate}>
            {isDownloadingTemplate ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Download className="mr-2 h-4 w-4" />}
            Baixar Modelo
          </Button>
        </div>
        )}
      </div>

      <Tabs value={tab} onValueChange={setTab} className="w-full">
        <TabsList>
          <TabsTrigger value="criar" className="gap-2">
            <Upload className="h-4 w-4" />
            Criar por Planilha
          </TabsTrigger>
          <TabsTrigger value="acompanhamento" className="gap-2">
            <ListChecks className="h-4 w-4" />
            Acompanhamento de Lotes
          </TabsTrigger>
        </TabsList>

        <TabsContent value="criar" className="mt-4 space-y-6">
          <Card className="max-w-3xl mx-auto glass-card border-0 animate-fade-in">
        <CardHeader>
          <CardTitle>Upload da Planilha</CardTitle>
          <CardDescription>
            Cada linha precisa de ESCRITORIO, PUBLISH_DATE, SUBTIPO, EXECUTANTE, DATA_TAREFA e o
            identificador do processo: <strong>CNJ ou PASTA</strong> (use PASTA para processos sem
            CNJ, como cobrança pré-judicial). Veja "Como preencher".
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="grid w-full items-center gap-1.5">
            <Label htmlFor="spreadsheet-file">Arquivo Excel</Label>
            <Input
              id="spreadsheet-file"
              type="file"
              accept=".xlsx"
              onChange={handleFileChange}
              disabled={isSubmitting || isPreviewing}
              className="file:text-primary file:font-medium"
            />
          </div>

          {selectedFile && (
            <div className="flex items-center p-3 rounded-md bg-muted/50">
              <File className="w-5 h-5 mr-3 text-primary" />
              <span className="text-sm font-medium">{selectedFile.name}</span>
            </div>
          )}

          {error && (
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertTitle>Erro</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          <div className="flex flex-col sm:flex-row gap-3">
            <Button onClick={handlePreview} disabled={!selectedFile || isPreviewing || isSubmitting} variant="outline" className="flex-1">
              {isPreviewing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-2 h-4 w-4" />}
              {isPreviewing ? "Validando..." : "Validar Planilha"}
            </Button>
            <Button onClick={handleSubmit} disabled={!selectedFile || isSubmitting} className="flex-1 glass-button text-white">
              {isSubmitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Upload className="mr-2 h-4 w-4" />}
              {isSubmitting ? "Processando..." : "Enviar e Processar"}
            </Button>
          </div>
        </CardContent>
      </Card>

      {preview && (
        <Card className="max-w-5xl mx-auto border-0">
          <CardHeader>
            <CardTitle>Resumo do Preview</CardTitle>
            <CardDescription>
              {preview.filename} foi validada antes do envio. Linhas com alerta ainda podem ser processadas, mas merecem revisao.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              <Badge variant="outline" className="justify-center py-3">Total: {preview.summary.total_rows}</Badge>
              <Badge className="justify-center py-3 bg-green-100 text-green-800">Validas: {preview.summary.valid_rows}</Badge>
              <Badge variant="destructive" className="justify-center py-3">Invalidas: {preview.summary.invalid_rows}</Badge>
              <Badge variant="outline" className="justify-center py-3">Dup. planilha: {preview.summary.duplicate_rows_in_file}</Badge>
              <Badge variant="outline" className="justify-center py-3">Dup. historico: {preview.summary.duplicate_rows_in_history}</Badge>
            </div>

            {previewProblems.length > 0 ? (
              <div className="space-y-3">
                <h3 className="text-sm font-semibold">Principais linhas com problema</h3>
                <ScrollArea className="h-72 rounded-md border p-4">
                  <div className="space-y-4">
                    {previewProblems.slice(0, 20).map((row) => (
                      <div key={row.row_id} className="rounded-md border border-red-100 bg-red-50/40 p-3">
                        <div className="flex items-center justify-between gap-3">
                          <span className="font-medium text-sm">
                            Linha {row.row_id} {row.process_number ? `- ${row.process_number}` : ""}
                          </span>
                          <Badge variant="destructive">Revisar</Badge>
                        </div>
                        {row.errors.map((item) => (
                          <p key={item} className="text-sm text-red-700 mt-2">{item}</p>
                        ))}
                        {row.warnings.map((item) => (
                          <p key={item} className="text-sm text-amber-700 mt-2">{item}</p>
                        ))}
                      </div>
                    ))}
                  </div>
                </ScrollArea>
              </div>
            ) : (
              <Alert>
                <CheckCircle2 className="h-4 w-4" />
                <AlertTitle>Preview limpo</AlertTitle>
                <AlertDescription>Nao encontramos erros locais nem duplicidades conhecidas nessa planilha.</AlertDescription>
              </Alert>
            )}
          </CardContent>
        </Card>
      )}
        </TabsContent>

        <TabsContent value="acompanhamento" className="mt-4">
          <BatchExecutionsPage embedded />
        </TabsContent>
      </Tabs>

      <AlertDialog
        open={showBatchDialog}
        onOpenChange={(open) => {
          if (!isMonitoring) {
            setShowBatchDialog(open);
          }
        }}
      >
        <AlertDialogContent className="sm:max-w-lg">
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              {batchStatus?.status === "CONCLUIDO" ? (
                <CheckCircle2 className="h-6 w-6 text-green-500" />
              ) : (
                <Loader2 className={`h-6 w-6 text-blue-500 ${isMonitoring ? "animate-spin" : ""}`} />
              )}
              Controle do Lote
            </AlertDialogTitle>
            <AlertDialogDescription>
              Use os controles abaixo para acompanhar, pausar, retomar ou cancelar o processamento.
            </AlertDialogDescription>
          </AlertDialogHeader>

          <div className="space-y-5 py-2">
            <Progress value={batchStatus?.percentage ?? 0} className="w-full h-4" />

            <div className="grid grid-cols-2 gap-3 text-sm">
              <div className="rounded-md bg-muted/40 p-3">
                <p className="text-muted-foreground">Status</p>
                <p className="font-semibold">{batchStatus?.status ?? "Aguardando..."}</p>
              </div>
              <div className="rounded-md bg-muted/40 p-3">
                <p className="text-muted-foreground">Progresso</p>
                <p className="font-semibold">
                  {batchStatus?.processed_items ?? 0} / {batchStatus?.total_items ?? 0}
                </p>
              </div>
              <div className="rounded-md bg-muted/40 p-3">
                <p className="text-muted-foreground">Sucessos</p>
                <p className="font-semibold text-green-700">{batchStatus?.success_count ?? 0}</p>
              </div>
              <div className="rounded-md bg-muted/40 p-3">
                <p className="text-muted-foreground">Falhas</p>
                <p className="font-semibold text-red-700">{batchStatus?.failure_count ?? 0}</p>
              </div>
            </div>

            <div className="flex flex-wrap gap-3">
              <Button
                variant="outline"
                onClick={() => handleControlAction("pause")}
                disabled={!batchStatus?.can_pause || isControlLoading !== null}
              >
                {isControlLoading === "pause" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Pause className="mr-2 h-4 w-4" />}
                Pausar
              </Button>
              <Button
                variant="outline"
                onClick={() => handleControlAction("resume")}
                disabled={!batchStatus?.can_resume || isControlLoading !== null}
              >
                {isControlLoading === "resume" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />}
                Retomar
              </Button>
              <Button
                variant="destructive"
                onClick={() => handleControlAction("cancel")}
                disabled={!batchStatus?.can_cancel || isControlLoading !== null}
              >
                {isControlLoading === "cancel" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Square className="mr-2 h-4 w-4" />}
                Cancelar
              </Button>
              <Button
                variant="secondary"
                onClick={() => handleControlAction("report")}
                disabled={(batchStatus?.failure_count ?? 0) === 0 || isControlLoading !== null}
              >
                {isControlLoading === "report" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Download className="mr-2 h-4 w-4" />}
                Baixar Erros
              </Button>
            </div>
          </div>
          {isMonitoring ? (
            <AlertDialogFooter className="sm:justify-between sm:gap-2">
              <p className="self-center text-[11px] text-muted-foreground">
                O lote roda no servidor — ocultar não interrompe nada.
              </p>
              <Button variant="outline" onClick={() => setShowBatchDialog(false)}>
                <EyeOff className="mr-2 h-4 w-4" /> Ocultar progresso
              </Button>
            </AlertDialogFooter>
          ) : (
            batchStatus && (
              <AlertDialogFooter>
                <Button variant="outline" onClick={() => setShowBatchDialog(false)}>
                  Fechar
                </Button>
              </AlertDialogFooter>
            )
          )}
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};

export default CreateTaskFromSpreadsheetPage;
