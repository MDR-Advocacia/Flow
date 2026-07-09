import { useCallback, useEffect, useState } from "react";
import { Info, Loader2, Save } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/hooks/use-toast";
import { ValorPadrao, atualizarValores, listarValores } from "@/services/distribuidos-bb";

export default function ValoresPadraoTab() {
  const { toast } = useToast();
  const [valores, setValores] = useState<ValorPadrao[]>([]);
  const [edicoes, setEdicoes] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [salvando, setSalvando] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const v = await listarValores();
      setValores(v);
      setEdicoes(Object.fromEntries(v.map((x) => [x.chave, x.valor ?? ""])));
    } catch (e) {
      toast({ title: "Erro ao carregar", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  const sujo = valores.some((v) => (edicoes[v.chave] ?? "") !== (v.valor ?? ""));

  const salvar = async () => {
    setSalvando(true);
    try {
      const mudados: Record<string, string> = {};
      for (const v of valores) {
        if ((edicoes[v.chave] ?? "") !== (v.valor ?? "")) mudados[v.chave] = edicoes[v.chave];
      }
      const atualizado = await atualizarValores(mudados);
      setValores(atualizado);
      setEdicoes(Object.fromEntries(atualizado.map((x) => [x.chave, x.valor ?? ""])));
      toast({ title: "Valores salvos" });
    } catch (e) {
      toast({ title: "Erro ao salvar", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setSalvando(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2 rounded-md border p-3 text-sm text-muted-foreground">
        <Info className="mt-0.5 h-4 w-4 shrink-0" />
        <p>As constantes que o robô cravava na planilha (cliente Banco do Brasil, CNPJ, tipos, origem…). Alterar aqui muda o que vai na planilha e no cadastro via API.</p>
      </div>

      {loading && valores.length === 0 ? (
        <div className="py-12 text-center"><Loader2 className="mx-auto h-6 w-6 animate-spin text-muted-foreground" /></div>
      ) : (
        <Card>
          <CardContent className="space-y-3 p-4">
            {valores.map((v) => (
              <div key={v.chave} className="grid grid-cols-1 gap-1 sm:grid-cols-[minmax(0,1fr)_2fr] sm:items-center sm:gap-3">
                <div>
                  <Label className="text-sm font-medium">{v.descricao ?? v.chave}</Label>
                  <div className="font-mono text-xs text-muted-foreground">{v.chave}</div>
                </div>
                <Input
                  value={edicoes[v.chave] ?? ""}
                  onChange={(e) => setEdicoes((prev) => ({ ...prev, [v.chave]: e.target.value }))}
                />
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <div className="flex justify-end">
        <Button onClick={salvar} disabled={!sujo || salvando}>
          {salvando ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
          Salvar alterações
        </Button>
      </div>
    </div>
  );
}
