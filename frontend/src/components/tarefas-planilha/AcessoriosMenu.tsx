// Menu "Acessórios" da página Tarefas por Planilha: utilitários que preparam a
// planilha antes do agendamento. 1º acessório — "Análise de risco": recebe a
// planilha do banco (formato de agendamento, com NPJ e SEM CNJ) + a Base
// Analítica (relacional NPJ -> Nº do Processo) e devolve a MESMA planilha com o
// CNJ preenchido, pronta pra subir aqui. O join é por NPJ; ambíguos/incorretos/
// não encontrados vão pra aba "Revisar" do arquivo gerado.

import { useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  FileSpreadsheet,
  Info,
  Loader2,
  Sparkles,
  Wand2,
} from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/hooks/use-toast";
import { apiFetch } from "@/lib/api-client";

interface Resumo {
  total: number;
  resolvidos: number;
  ja_tinha_cnj: number;
  ambiguo: number;
  cnj_incorreto: number;
  nao_encontrado: number;
  revisar_total: number;
}

export default function AcessoriosMenu() {
  const { toast } = useToast();
  const [openAnalise, setOpenAnalise] = useState(false);
  const [analiseFile, setAnaliseFile] = useState<File | null>(null);
  const [baseFile, setBaseFile] = useState<File | null>(null);
  const [gerando, setGerando] = useState(false);
  const [resultado, setResultado] = useState<Resumo | null>(null);
  const analiseRef = useRef<HTMLInputElement>(null);
  const baseRef = useRef<HTMLInputElement>(null);

  const reset = () => {
    setAnaliseFile(null);
    setBaseFile(null);
    setResultado(null);
    if (analiseRef.current) analiseRef.current.value = "";
    if (baseRef.current) baseRef.current.value = "";
  };

  const gerar = async () => {
    if (!analiseFile || !baseFile) {
      toast({
        title: "Faltam planilhas",
        description: "Anexe as duas: a de Análise de Risco e a Base Analítica.",
        variant: "destructive",
      });
      return;
    }
    setGerando(true);
    setResultado(null);
    try {
      const fd = new FormData();
      fd.append("analise", analiseFile);
      fd.append("base", baseFile);
      const resp = await apiFetch("/api/v1/tasks/acessorios/analise-risco", {
        method: "POST",
        body: fd,
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || "Falha ao gerar a planilha.");
      }
      // resumo vem no header; o arquivo no corpo
      let resumo: Resumo | null = null;
      try {
        const raw = resp.headers.get("X-Analise-Resumo");
        if (raw) resumo = JSON.parse(raw) as Resumo;
      } catch {
        /* header opcional */
      }
      const blob = await resp.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "analise_risco_com_cnj.xlsx";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);

      setResultado(resumo);
      toast({
        title: resumo
          ? `Planilha gerada — ${resumo.resolvidos}/${resumo.total} CNJ preenchidos`
          : "Planilha gerada",
        description: "Download iniciado. Confira o resumo antes de subir.",
      });
    } catch (e) {
      toast({
        title: "Erro ao gerar a planilha",
        description: e instanceof Error ? e.message : "Erro inesperado.",
        variant: "destructive",
      });
    } finally {
      setGerando(false);
    }
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm">
            <Sparkles className="mr-2 h-4 w-4" />
            Acessórios
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56">
          <DropdownMenuLabel>Preparar planilha</DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => setOpenAnalise(true)}>
            <Wand2 className="mr-2 h-4 w-4" />
            Análise de risco
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog
        open={openAnalise}
        onOpenChange={(o) => {
          setOpenAnalise(o);
          if (!o) reset();
        }}
      >
        <DialogContent className="sm:max-w-lg max-h-[88vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Wand2 className="h-5 w-5 text-[hsl(var(--dunatech-blue))]" />
              Análise de risco — preencher CNJ
            </DialogTitle>
            <DialogDescription>
              O banco manda a planilha de agendamento com <strong>NPJ</strong> mas sem o{" "}
              <strong>CNJ</strong>. Anexe também a <strong>Base Analítica</strong> que eu devolvo a
              mesma planilha com o CNJ preenchido, pronta pra subir em <em>Criar por Planilha</em>.
            </DialogDescription>
          </DialogHeader>

          {/* Como funciona */}
          <div className="rounded-lg border bg-muted/40 p-3 text-xs">
            <div className="mb-1.5 flex items-center gap-1.5 font-semibold text-foreground">
              <Info className="h-3.5 w-3.5 text-[hsl(var(--dunatech-blue))]" />
              Como funciona
            </div>
            <ol className="list-decimal space-y-1 pl-4 text-muted-foreground">
              <li>
                Anexe a <strong>planilha do banco</strong> (formato de agendamento, com a coluna{" "}
                <strong>NPJ</strong> e a <strong>CNJ vazia</strong>).
              </li>
              <li>
                Anexe a <strong>Base Analítica</strong> — o relacional <strong>NPJ → Nº do
                Processo</strong>.
              </li>
              <li>
                Eu caso linha a linha pelo <strong>NPJ</strong> e preencho o <strong>CNJ</strong>,
                preferindo os que a base marca como <strong>"CNJ CORRETO"</strong>.
              </li>
              <li>
                Baixa a <strong>mesma planilha</strong> com o CNJ preenchido, mais as abas{" "}
                <strong>Revisar</strong> (o que precisou de atenção, com os CNJs candidatos) e{" "}
                <strong>Resumo</strong>.
              </li>
            </ol>
            <p className="mt-2 text-muted-foreground">
              NPJ com <strong>mais de um processo</strong>, com <strong>CNJ incorreto</strong> na
              base, ou <strong>fora da base</strong> ficam <strong>sem CNJ</strong> na aba
              Agendamentos e vão pra <strong>Revisar</strong> — pra você decidir, sem risco de
              cadastrar tarefa no processo errado.
            </p>
          </div>

          <div className="space-y-4 py-1">
            <div className="space-y-1.5">
              <Label htmlFor="analise-file">
                1. Planilha de Análise de Risco{" "}
                <span className="text-muted-foreground">(formato de agendamento, com NPJ)</span>
              </Label>
              <Input
                id="analise-file"
                ref={analiseRef}
                type="file"
                accept=".xlsx"
                onChange={(e) => {
                  setAnaliseFile(e.target.files?.[0] ?? null);
                  setResultado(null);
                }}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="base-file">
                2. Base Analítica{" "}
                <span className="text-muted-foreground">(relacional NPJ → CNJ)</span>
              </Label>
              <Input
                id="base-file"
                ref={baseRef}
                type="file"
                accept=".xlsx"
                onChange={(e) => {
                  setBaseFile(e.target.files?.[0] ?? null);
                  setResultado(null);
                }}
              />
            </div>
          </div>

          {/* Resultado + avisos */}
          {resultado && (
            <div className="space-y-2">
              <Alert className="border-emerald-200 bg-emerald-50 text-emerald-900">
                <CheckCircle2 className="h-4 w-4 !text-emerald-600" />
                <AlertTitle>
                  {resultado.resolvidos}/{resultado.total} CNJ preenchidos
                </AlertTitle>
                <AlertDescription className="text-emerald-800">
                  Download iniciado (arquivo <strong>analise_risco_com_cnj.xlsx</strong>).
                  {resultado.ja_tinha_cnj > 0 && ` ${resultado.ja_tinha_cnj} já tinham CNJ.`}
                </AlertDescription>
              </Alert>

              {resultado.cnj_incorreto > 0 && (
                <Alert className="border-amber-300 bg-amber-50 text-amber-900">
                  <AlertTriangle className="h-4 w-4 !text-amber-600" />
                  <AlertTitle>
                    ⚠️ {resultado.cnj_incorreto} processo(s) com <strong>CNJ INCORRETO</strong> na base
                  </AlertTitle>
                  <AlertDescription className="text-amber-800">
                    A Base Analítica marcou esse(s) CNJ como reprovado na validação, então{" "}
                    <strong>deixei em branco</strong> na aba Agendamentos pra não subir um número
                    inválido. Veja a aba <strong>Revisar</strong>: o CNJ candidato está lá — confira
                    e, se estiver certo, cole manualmente antes de agendar.
                  </AlertDescription>
                </Alert>
              )}

              {(resultado.ambiguo > 0 || resultado.nao_encontrado > 0) && (
                <Alert className="border-slate-300 bg-slate-50 text-slate-800">
                  <Info className="h-4 w-4 !text-slate-500" />
                  <AlertTitle>Outras linhas pra revisar</AlertTitle>
                  <AlertDescription className="text-slate-700">
                    {resultado.ambiguo > 0 && (
                      <div>
                        <strong>{resultado.ambiguo}</strong> ambíguo(s) — o NPJ tem mais de um CNJ na
                        base.
                      </div>
                    )}
                    {resultado.nao_encontrado > 0 && (
                      <div>
                        <strong>{resultado.nao_encontrado}</strong> não encontrado(s) — NPJ fora da
                        Base Analítica.
                      </div>
                    )}
                    Todas ficaram sem CNJ e estão listadas na aba <strong>Revisar</strong>.
                  </AlertDescription>
                </Alert>
              )}
            </div>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={() => setOpenAnalise(false)} disabled={gerando}>
              {resultado ? "Fechar" : "Cancelar"}
            </Button>
            <Button onClick={gerar} disabled={gerando || !analiseFile || !baseFile}>
              {gerando ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <FileSpreadsheet className="mr-2 h-4 w-4" />
              )}
              {resultado ? "Gerar novamente" : "Gerar planilha com CNJ"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
