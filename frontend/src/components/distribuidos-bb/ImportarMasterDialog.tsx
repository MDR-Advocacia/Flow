import { useState } from "react";
import { Loader2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import { AtivosLote, getLoteMaster, importarMaster } from "@/services/distribuidos-bb";

/**
 * Upload da Listagem de Ações Judiciais do Banco Master, com barra de progresso.
 *
 * Substitui o módulo antigo (Administração → Base Banco Master → Conversão L1),
 * que só devolvia o xlsx convertido pro operador subir na mão. Aqui a Listagem
 * já entra como processo rastreado e o cadastro no L1 é automático — é o que dá
 * o acompanhamento diário junto com as outras carteiras.
 */
export default function ImportarMasterDialog({
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
      const { lote_id } = await importarMaster(file);
      // Poll do progresso até concluir (server-backed: fechar a tela não para
      // a importação, o lote continua e pode ser reaberto pelo id).
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const l = await getLoteMaster(lote_id);
        setLote(l);
        if (l.status !== "EM_ANDAMENTO") break;
        await new Promise((r) => setTimeout(r, 1500));
      }
      toast({
        title: "Importação Banco Master concluída",
        description: "Processos criados e enviados pro cadastro no Legal One.",
      });
      onDone?.();
    } catch (e) {
      toast({
        title: "Erro na importação",
        description: String((e as Error).message),
        variant: "destructive",
      });
    } finally {
      setImportando(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={abrir}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Upload className="h-5 w-5 text-cyan-600" />
            Importar Listagem — Banco Master
          </DialogTitle>
          <DialogDescription>
            Suba a <strong>Listagem de Ações Judiciais</strong> exportada do sistema do
            cliente. Ela já traz a capa completa (partes, ação, comarca e valor da causa),
            então <strong>não consultamos o DataJud</strong>. Os processos entram como
            <strong> Réu</strong>, com o responsável e o escritório do Banco Master, e
            seguem direto pro cadastro no Legal One.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <Input
            type="file"
            accept=".xlsx,.xls"
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
                  className="h-full bg-cyan-500 transition-all"
                  style={{ width: `${lote.total ? (lote.processados / lote.total) * 100 : 0}%` }}
                />
              </div>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                <span className="text-emerald-700">{lote.criados} criado(s)</span>
                <span className="text-amber-700">{lote.duplicados} repetido(s)</span>
                {lote.invalidos > 0 && (
                  <span className="text-rose-600">{lote.invalidos} sem CNJ válido</span>
                )}
              </div>
              {lote.erro && (
                <p className="mt-2 text-xs text-rose-600">{lote.erro}</p>
              )}
            </div>
          )}
          <div className="flex justify-end gap-2">
            <Button
              variant="outline" size="sm"
              onClick={() => onOpenChange(false)} disabled={importando}
            >
              Fechar
            </Button>
            <Button size="sm" onClick={importar} disabled={!file || importando}>
              {importando
                ? <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                : <Upload className="mr-2 h-4 w-4" />}
              Importar Listagem
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
