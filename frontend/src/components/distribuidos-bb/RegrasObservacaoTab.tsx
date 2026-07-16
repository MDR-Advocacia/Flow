import { useCallback, useEffect, useState } from "react";
import { Info, Loader2, Pencil, Plus, Power, Trash2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import {
  RegraObservacao,
  RegraObservacaoPayload,
  criarRegraObservacao,
  editarRegraObservacao,
  listarRegrasObservacao,
  removerRegraObservacao,
} from "@/services/distribuidos-bb";

const POSICOES = ["", "Réu", "Autor", "Interessado"];
// O cliente é carimbado pela porta de entrada do processo (coleta RPA = BB;
// "Importar lista (Ativos)" = ATIVOS) — por isso serve de critério da regra.
const CLIENTES = [
  { valor: "", rotulo: "Qualquer" },
  { valor: "BB", rotulo: "Banco do Brasil" },
  { valor: "ATIVOS", rotulo: "Ativos" },
];
const CNJ_OPCOES: { v: string; label: string }[] = [
  { v: "", label: "Qualquer" },
  { v: "com", label: "Com CNJ" },
  { v: "sem", label: "Sem CNJ" },
];

export default function RegrasObservacaoTab() {
  const { toast } = useToast();
  const [regras, setRegras] = useState<RegraObservacao[]>([]);
  const [loading, setLoading] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editando, setEditando] = useState<RegraObservacao | null>(null);
  const [form, setForm] = useState<RegraObservacaoPayload>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRegras(await listarRegrasObservacao());
    } catch (e) {
      toast({ title: "Erro ao carregar regras", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  const abrirNova = () => {
    setEditando(null);
    setForm({ criterio_cliente: "", criterio_posicao: "", criterio_cnj: "", ativo: true });
    setDialogOpen(true);
  };
  const abrirEdicao = (r: RegraObservacao) => {
    setEditando(r);
    setForm({
      nome: r.nome,
      criterio_cliente: r.criterio_cliente ?? "",
      criterio_posicao: r.criterio_posicao ?? "",
      criterio_natureza: r.criterio_natureza ?? "",
      criterio_cnj: r.criterio_cnj ?? "",
      texto: r.texto,
      ativo: r.ativo,
      ordem: r.ordem,
    });
    setDialogOpen(true);
  };

  const salvar = async () => {
    if (!form.nome?.trim() || !form.texto?.trim()) {
      toast({ title: "Preencha nome e texto da observação", variant: "destructive" });
      return;
    }
    try {
      if (editando) {
        await editarRegraObservacao(editando.id, form);
        toast({ title: "Regra atualizada" });
      } else {
        await criarRegraObservacao(form);
        toast({ title: "Regra criada" });
      }
      setDialogOpen(false);
      load();
    } catch (e) {
      toast({ title: "Erro ao salvar", description: String((e as Error).message), variant: "destructive" });
    }
  };

  const alternarAtivo = async (r: RegraObservacao) => {
    try {
      await editarRegraObservacao(r.id, { ativo: !r.ativo });
      load();
    } catch (e) {
      toast({ title: "Erro", description: String((e as Error).message), variant: "destructive" });
    }
  };

  const remover = async (r: RegraObservacao) => {
    if (!confirm(`Remover a regra "${r.nome}"?`)) return;
    try {
      await removerRegraObservacao(r.id);
      toast({ title: "Regra removida" });
      load();
    } catch (e) {
      toast({ title: "Erro ao remover", description: String((e as Error).message), variant: "destructive" });
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2 rounded-md border border-sky-300 bg-sky-50/60 p-3 text-sm">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-sky-600" />
        <p className="text-sky-800">
          O <strong>texto da Observação</strong> (Cadastro, Ajuizamento, Reterceirizado…) é o que <strong>ativa o
          workflow no Legal One</strong>. As regras são avaliadas de cima para baixo — a <strong>primeira que casar</strong>{" "}
          define o texto. Deixe critérios em branco para "qualquer".
        </p>
      </div>

      <div className="flex justify-end">
        <Button size="sm" onClick={abrirNova}>
          <Plus className="mr-2 h-4 w-4" /> Nova regra
        </Button>
      </div>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">#</TableHead>
                <TableHead>Nome</TableHead>
                <TableHead>Quando…</TableHead>
                <TableHead>Observação (gatilho)</TableHead>
                <TableHead>Ativa</TableHead>
                <TableHead className="text-right">Ações</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading && regras.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-10 text-center">
                    <Loader2 className="mx-auto h-5 w-5 animate-spin text-muted-foreground" />
                  </TableCell>
                </TableRow>
              ) : regras.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                    Nenhuma regra. Clique em "Nova regra".
                  </TableCell>
                </TableRow>
              ) : (
                regras.map((r) => (
                  <TableRow key={r.id} className={r.ativo ? "" : "opacity-50"}>
                    <TableCell className="text-muted-foreground">{r.ordem}</TableCell>
                    <TableCell className="font-medium">{r.nome}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {r.criterio_cliente && (
                          <Badge
                            className={
                              r.criterio_cliente === "ATIVOS"
                                ? "bg-violet-100 text-violet-700"
                                : "bg-yellow-100 text-yellow-800"
                            }
                            variant="secondary"
                          >
                            {r.criterio_cliente === "ATIVOS" ? "Ativos" : "Banco do Brasil"}
                          </Badge>
                        )}
                        {r.criterio_posicao && <Badge variant="secondary">Posição: {r.criterio_posicao}</Badge>}
                        {r.criterio_natureza && <Badge variant="secondary">Natureza: {r.criterio_natureza}</Badge>}
                        {r.criterio_cnj && <Badge variant="secondary">{r.criterio_cnj === "com" ? "Com CNJ" : "Sem CNJ"}</Badge>}
                        {!r.criterio_cliente && !r.criterio_posicao && !r.criterio_natureza && !r.criterio_cnj && (
                          <span className="text-xs text-muted-foreground italic">qualquer</span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge className="bg-indigo-100 text-indigo-700" variant="secondary">{r.texto}</Badge>
                    </TableCell>
                    <TableCell>
                      {r.ativo ? <Badge variant="default">Sim</Badge> : <Badge variant="outline">Não</Badge>}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button size="sm" variant="ghost" onClick={() => abrirEdicao(r)} title="Editar">
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => alternarAtivo(r)} title={r.ativo ? "Desativar" : "Ativar"}>
                        <Power className="h-3.5 w-3.5" />
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => remover(r)} title="Remover">
                        <Trash2 className="h-3.5 w-3.5 text-rose-500" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editando ? "Editar regra" : "Nova regra de observação"}</DialogTitle>
            <DialogDescription>
              Defina quando esta observação se aplica. O texto vira o gatilho do workflow no Legal One.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label className="text-xs">Nome da regra</Label>
              <Input value={form.nome ?? ""} onChange={(e) => setForm((f) => ({ ...f, nome: e.target.value }))} placeholder="Ex.: Autor sem CNJ → Ajuizamento" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Cliente</Label>
              <select className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                value={form.criterio_cliente ?? ""}
                onChange={(e) => setForm((f) => ({ ...f, criterio_cliente: e.target.value }))}>
                {CLIENTES.map((c) => <option key={c.valor} value={c.valor}>{c.rotulo}</option>)}
              </select>
              <p className="text-[11px] text-muted-foreground">
                O cliente vem da origem do processo: a coleta automática traz o Banco do Brasil;
                &quot;Importar lista&quot; traz o Ativos. Deixe em Qualquer só se a regra valer para os dois.
              </p>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div className="space-y-1">
                <Label className="text-xs">Posição</Label>
                <select className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                  value={form.criterio_posicao ?? ""}
                  onChange={(e) => setForm((f) => ({ ...f, criterio_posicao: e.target.value }))}>
                  {POSICOES.map((p) => <option key={p} value={p}>{p || "Qualquer"}</option>)}
                </select>
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Natureza</Label>
                <Input value={form.criterio_natureza ?? ""} onChange={(e) => setForm((f) => ({ ...f, criterio_natureza: e.target.value }))} placeholder="Qualquer" />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">CNJ</Label>
                <select className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                  value={form.criterio_cnj ?? ""}
                  onChange={(e) => setForm((f) => ({ ...f, criterio_cnj: e.target.value }))}>
                  {CNJ_OPCOES.map((o) => <option key={o.v} value={o.v}>{o.label}</option>)}
                </select>
              </div>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Observação (texto que ativa o workflow)</Label>
              <Input value={form.texto ?? ""} onChange={(e) => setForm((f) => ({ ...f, texto: e.target.value }))} placeholder="Ex.: Cadastro / Ajuizamento / Reterceirizado" />
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
