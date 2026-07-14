// Menu "Acessórios" da página Tarefas por Planilha: utilitários que preparam a
// planilha antes do agendamento. 1º acessório — "Análise de risco": recebe a
// planilha do banco (formato de agendamento, com NPJ e SEM CNJ) + a Base
// Analítica (relacional NPJ -> Nº do Processo) e devolve a MESMA planilha com o
// CNJ preenchido, pronta pra subir aqui. O join é por NPJ; ambíguos/incorretos/
// não encontrados vão pra aba "Revisar" do arquivo gerado.

import { useRef, useState } from "react";
import { FileSpreadsheet, Loader2, Sparkles, Wand2 } from "lucide-react";

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
  const analiseRef = useRef<HTMLInputElement>(null);
  const baseRef = useRef<HTMLInputElement>(null);

  const reset = () => {
    setAnaliseFile(null);
    setBaseFile(null);
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

      if (resumo) {
        const revisar = resumo.revisar_total || 0;
        toast({
          title: `Planilha gerada — ${resumo.resolvidos}/${resumo.total} CNJ preenchidos`,
          description:
            revisar > 0
              ? `${revisar} linha(s) precisam de revisão (aba "Revisar": ${resumo.ambiguo} ambíguo, ${resumo.cnj_incorreto} CNJ incorreto, ${resumo.nao_encontrado} não encontrado).`
              : "Todos os NPJs foram resolvidos. É só subir a planilha em Criar por Planilha.",
        });
      } else {
        toast({ title: "Planilha gerada", description: "Download iniciado." });
      }
      setOpenAnalise(false);
      reset();
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
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Wand2 className="h-5 w-5 text-[hsl(var(--dunatech-blue))]" />
              Análise de risco — preencher CNJ
            </DialogTitle>
            <DialogDescription>
              O banco manda a planilha de agendamento com <strong>NPJ</strong> mas sem o{" "}
              <strong>CNJ</strong>. Anexe também a <strong>Base Analítica</strong> (relacional NPJ →
              Nº do Processo) que eu devolvo a mesma planilha com o CNJ preenchido, pronta pra subir
              em <em>Criar por Planilha</em>. Ambíguos e não encontrados vão pra uma aba{" "}
              <strong>Revisar</strong>.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-2">
            <div className="space-y-1.5">
              <Label htmlFor="analise-file">
                1. Planilha de Análise de Risco <span className="text-muted-foreground">(formato de agendamento, com NPJ)</span>
              </Label>
              <Input
                id="analise-file"
                ref={analiseRef}
                type="file"
                accept=".xlsx"
                onChange={(e) => setAnaliseFile(e.target.files?.[0] ?? null)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="base-file">
                2. Base Analítica <span className="text-muted-foreground">(relacional NPJ → CNJ)</span>
              </Label>
              <Input
                id="base-file"
                ref={baseRef}
                type="file"
                accept=".xlsx"
                onChange={(e) => setBaseFile(e.target.files?.[0] ?? null)}
              />
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setOpenAnalise(false)} disabled={gerando}>
              Cancelar
            </Button>
            <Button onClick={gerar} disabled={gerando || !analiseFile || !baseFile}>
              {gerando ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <FileSpreadsheet className="mr-2 h-4 w-4" />
              )}
              Gerar planilha com CNJ
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
