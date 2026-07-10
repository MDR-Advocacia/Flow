/**
 * ConversaoL1Tab — converte a Listagem de Acoes Judiciais (saida AJUS / RPA L1)
 * em XLSX no formato MODELO LEGAL ONE pronto pra importacao no Legal One.
 *
 * Banco Master sempre como Reu, escritorio responsavel e responsavel fixos
 * (mesma logica do script gerar_planilha.py historico).
 */

import { useRef, useState } from "react";
import { toast } from "sonner";
import {
  Download,
  FileSpreadsheet,
  Loader2,
  Sparkles,
  Upload,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { cn } from "@/lib/utils";

import { converterListagemL1 } from "@/lib/api-base-processual";

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function ConversaoL1Tab() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [loading, setLoading] = useState(false);

  const pick = () => inputRef.current?.click();

  const handleFiles = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const f = files[0];
    if (!f.name.toLowerCase().endsWith(".xlsx")) {
      toast.error("Arquivo inválido", {
        description: "Envie um arquivo .xlsx (Excel).",
      });
      return;
    }
    setFile(f);
  };

  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    handleFiles(e.dataTransfer.files);
  };

  const gerar = async () => {
    if (!file) return;
    setLoading(true);
    try {
      const { blob, filename } = await converterListagemL1(file);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      toast.success("Planilha gerada", {
        description: filename,
      });
    } catch (err: any) {
      toast.error("Falha na conversão", {
        description: err?.message ?? "Erro desconhecido",
      });
    } finally {
      setLoading(false);
    }
  };

  const limpar = () => {
    setFile(null);
    if (inputRef.current) inputRef.current.value = "";
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5" />
            Conversão para o Legal One
          </CardTitle>
          <CardDescription>
            Suba a planilha <strong>Listagem de Ações Judiciais</strong>{" "}
            exportada do AJUS (RPA) e baixe a planilha de migração já no
            formato do <strong>MODELO LEGAL ONE</strong>, com Banco Master como
            Réu, responsável e escritório preenchidos automaticamente.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <input
            ref={inputRef}
            type="file"
            accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            className="hidden"
            onChange={(e) => handleFiles(e.target.files)}
          />

          <div
            onClick={pick}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            className={cn(
              "flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-8 text-center transition cursor-pointer",
              dragOver
                ? "border-primary bg-primary/5"
                : "border-muted-foreground/25 hover:border-muted-foreground/50",
            )}
          >
            <Upload className="h-8 w-8 text-muted-foreground" />
            <p className="text-sm font-medium">
              Arraste o XLSX aqui ou clique para selecionar
            </p>
            <p className="text-xs text-muted-foreground">
              Aceita apenas .xlsx exportado do Legal One (Listagem de Ações
              Judiciais)
            </p>
          </div>

          {file && (
            <div className="flex items-center justify-between rounded-md border bg-muted/30 p-3">
              <div className="flex items-center gap-3 min-w-0">
                <FileSpreadsheet className="h-5 w-5 text-emerald-600 shrink-0" />
                <div className="min-w-0">
                  <p className="text-sm font-medium truncate" title={file.name}>
                    {file.name}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {formatBytes(file.size)}
                  </p>
                </div>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={limpar}
                disabled={loading}
                aria-label="Remover arquivo"
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          )}

          <div className="flex items-center gap-2">
            <Button
              onClick={gerar}
              disabled={!file || loading}
              className="gap-2"
            >
              {loading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Download className="h-4 w-4" />
              )}
              {loading ? "Gerando..." : "Gerar planilha de migração"}
            </Button>
          </div>

          <Alert>
            <AlertTitle className="text-sm">Como funciona</AlertTitle>
            <AlertDescription className="text-xs space-y-1">
              <p>
                · Banco Master entra sempre como <strong>Réu</strong> (CNPJ
                33.923.798/0001-00).
              </p>
              <p>
                · Responsável: <strong>Enzo Pinto Bagatoli Carriço</strong>{" "}
                · Escritório:{" "}
                <strong>MDR Advocacia / Área operacional / Banco Master / Réu</strong>.
              </p>
              <p>
                · Observação preenchida com <code>bmagravo</code> para Agravos
                de Instrumento (ou número terminando em .0000) e{" "}
                <code>bmcomum</code> para os demais.
              </p>
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>
    </div>
  );
}
