import { useCallback, useEffect, useState } from "react";
import { Info, Loader2, Pencil, Plus, Trash2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import {
  ClassificacaoFull,
  ClassificacaoPayload,
  criarClassificacao,
  editarClassificacao,
  listarClassificacoes,
  removerClassificacao,
} from "@/services/distribuidos-bb";

const TIPOS_PARTICIPANTE = ["", "Customer", "PersonInCharge", "OtherParty"];

export default function ClassificacoesTab() {
  const { toast } = useToast();
  const [itens, setItens] = useState<ClassificacaoFull[]>([]);
  const [loading, setLoading] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editando, setEditando] = useState<ClassificacaoFull | null>(null);
  const [form, setForm] = useState<ClassificacaoPayload>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItens((await listarClassificacoes()) as ClassificacaoFull[]);
    } catch (e) {
      toast({ title: "Erro ao carregar", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  const abrirNova = () => { setEditando(null); setForm({ situacao: "Outros", participante_tipo: "", ativo: true }); setDialogOpen(true); };
  const abrirEdicao = (c: ClassificacaoFull) => {
    setEditando(c);
    setForm({ nome: c.nome, situacao: c.situacao ?? "Outros", participante_tipo: c.participante_tipo ?? "", position_id_l1: c.position_id_l1 ?? undefined, ativo: c.ativo });
    setDialogOpen(true);
  };

  const salvar = async () => {
    if (!form.nome?.trim()) { toast({ title: "Informe o nome", variant: "destructive" }); return; }
    try {
      if (editando) { await editarClassificacao(editando.id, form); toast({ title: "Classificação atualizada" }); }
      else { await criarClassificacao(form); toast({ title: "Classificação criada" }); }
      setDialogOpen(false); load();
    } catch (e) {
      toast({ title: "Erro ao salvar", description: String((e as Error).message), variant: "destructive" });
    }
  };

  const remover = async (c: ClassificacaoFull) => {
    if (!confirm(`Remover a classificação "${c.nome}"?`)) return;
    try { await removerClassificacao(c.id); toast({ title: "Removida" }); load(); }
    catch (e) { toast({ title: "Erro", description: String((e as Error).message), variant: "destructive" }); }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2 rounded-md border p-3 text-sm text-muted-foreground">
        <Info className="mt-0.5 h-4 w-4 shrink-0" />
        <p>As posições/classificações dos envolvidos (Advogado, Assistente…). A <strong>Situação</strong> é a coluna da planilha; o <strong>tipo de participante</strong> e o <strong>positionId</strong> mapeiam pro cadastro via API do L1 (quando aplicável).</p>
      </div>
      <div className="flex justify-end">
        <Button size="sm" onClick={abrirNova}><Plus className="mr-2 h-4 w-4" /> Nova classificação</Button>
      </div>
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Nome</TableHead>
                <TableHead>Situação</TableHead>
                <TableHead>Tipo participante (API)</TableHead>
                <TableHead>positionId</TableHead>
                <TableHead className="text-right">Ações</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading && itens.length === 0 ? (
                <TableRow><TableCell colSpan={5} className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-muted-foreground" /></TableCell></TableRow>
              ) : itens.length === 0 ? (
                <TableRow><TableCell colSpan={5} className="py-10 text-center text-muted-foreground">Nenhuma classificação.</TableCell></TableRow>
              ) : (
                itens.map((c) => (
                  <TableRow key={c.id} className={c.ativo ? "" : "opacity-50"}>
                    <TableCell className="font-medium">{c.nome}</TableCell>
                    <TableCell>{c.situacao ?? "—"}</TableCell>
                    <TableCell>{c.participante_tipo ? <Badge variant="secondary">{c.participante_tipo}</Badge> : <span className="text-xs text-muted-foreground italic">—</span>}</TableCell>
                    <TableCell>{c.position_id_l1 ?? "—"}</TableCell>
                    <TableCell className="text-right">
                      <Button size="sm" variant="ghost" onClick={() => abrirEdicao(c)}><Pencil className="h-3.5 w-3.5" /></Button>
                      <Button size="sm" variant="ghost" onClick={() => remover(c)}><Trash2 className="h-3.5 w-3.5 text-rose-500" /></Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{editando ? "Editar classificação" : "Nova classificação"}</DialogTitle>
            <DialogDescription>Posição do envolvido + mapeamento opcional pro cadastro via API.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label className="text-xs">Nome</Label>
              <Input value={form.nome ?? ""} onChange={(e) => setForm((f) => ({ ...f, nome: e.target.value }))} placeholder="Ex.: Advogado" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Situação (planilha)</Label>
              <Input value={form.situacao ?? ""} onChange={(e) => setForm((f) => ({ ...f, situacao: e.target.value }))} placeholder="Outros" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label className="text-xs">Tipo participante (API)</Label>
                <select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={form.participante_tipo ?? ""} onChange={(e) => setForm((f) => ({ ...f, participante_tipo: e.target.value }))}>
                  {TIPOS_PARTICIPANTE.map((t) => <option key={t} value={t}>{t || "—"}</option>)}
                </select>
              </div>
              <div className="space-y-1">
                <Label className="text-xs">positionId (L1)</Label>
                <Input type="number" value={form.position_id_l1 ?? ""} onChange={(e) => setForm((f) => ({ ...f, position_id_l1: e.target.value ? Number(e.target.value) : null }))} placeholder="—" />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>Cancelar</Button>
            <Button onClick={salvar}>{editando ? "Salvar" : "Criar"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
