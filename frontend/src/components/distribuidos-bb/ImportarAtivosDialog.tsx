import { useState } from "react";
import { Loader2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import { AtivosLote, getLoteAtivos, importarAtivos } from "@/services/distribuidos-bb";

/**
 * Upload da planilha da Ativos (aba PARA CADASTRO) com barra de progresso.
 * Reusado nas duas telas: Processos e Acompanhamento (dashboard).
 */
export default function ImportarAtivosDialog({
  open, onOpenChange, onDone,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onDone?: () => void;
}) {
  const { toast } = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [lote, setLote] = useState<AtivosLote | null>(null);
  const [importando, setImportando] = useState(false);

  const abrir = (o: boolean) => {
    if (importando) return;
    if (o) { setFile(null); setLote(null); }
    onOpenChange(o);
  };

  const importar = async () => {
    if (!file) return;
    setImportando(true);
    setLote(null);
    try {
      const { lote_id } = await importarAtivos(file);
      // Poll do progresso até concluir.
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const l = await getLoteAtivos(lote_id);
        setLote(l);
        if (l.status !== "EM_ANDAMENTO") break;
        await new Promise((r) => setTimeout(r, 1500));
      }
      toast({ title: "Importação Ativos concluída", description: "Processos criados a partir da planilha. O DataJud enriquece a capa em segundo plano." });
      onDone?.();
    } catch (e) {
      toast({ title: "Erro na importação", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setImportando(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={abrir}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Upload className="h-5 w-5 text-violet-600" />
            Importar lista de processos — Ativos
          </DialogTitle>
          <DialogDescription>
            Suba a planilha da Ativos. Lemos a aba <strong>PARA CADASTRO</strong> (a aba JÁ CADASTRADO
            é ignorada) e criamos os processos direto com os dados dela — CNJ, UF, data e a parte
            quando vier. A classe/assunto/órgão vêm do <strong>DataJud em segundo plano</strong>
            (reconsulta os recém-distribuídos que ainda não indexaram). Valor da causa é manual.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <Input
            type="file"
            accept=".xlsx,.xls,.csv,.txt"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            disabled={importando}
          />
          {lote && (
            <div className="rounded-md border p-3 text-sm">
              <div className="mb-2 flex items-center justify-between">
                <span className="font-medium">
                  {lote.processados} de {lote.total} processados
                </span>
                <span className="text-xs text-muted-foreground">
                  {lote.status === "EM_ANDAMENTO" ? "em andamento…" : lote.status.toLowerCase()}
                </span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full bg-violet-500 transition-all"
                  style={{ width: `${lote.total ? (lote.processados / lote.total) * 100 : 0}%` }}
                />
              </div>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                <span className="text-emerald-700">{lote.criados} criado(s)</span>
                <span className="text-amber-700">{lote.duplicados} já cadastrado / repetido</span>
                {lote.invalidos > 0 && <span className="text-rose-600">{lote.invalidos} inválido(s)</span>}
              </div>
            </div>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => onOpenChange(false)} disabled={importando}>
              Fechar
            </Button>
            <Button size="sm" onClick={importar} disabled={!file || importando}>
              {importando ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Upload className="mr-2 h-4 w-4" />}
              Importar planilha
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
