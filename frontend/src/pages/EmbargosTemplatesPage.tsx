// Templates das tarefas que o Flow cria no incidente de embargos.
// Depois que o controlador cadastra o incidente (manual no L1 e no portal do BB),
// o Flow localiza o "Proc - X/00N" e cria uma tarefa por template ativo.
// Lista curta e estável: sem paginação por decisão consciente.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Loader2, Pencil, Plus, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { SubtypePicker, type SubtypePickerTaskType } from "@/components/ui/SubtypePicker";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import UserSelector, { type SelectableUser } from "@/components/ui/UserSelector";
import { useToast } from "@/hooks/use-toast";
import {
  EmbargosTemplate,
  EmbargosTemplateIn,
  EmbargosTemplatesResponse,
  PRIORIDADE_LABEL,
  excluirTemplateEmbargos,
  getTiposTarefa,
  getUsuariosL1,
  listarTemplatesEmbargos,
  salvarTemplateEmbargos,
} from "@/services/embargos-execucao";

const VAZIO: EmbargosTemplateIn = {
  nome: "",
  ativo: true,
  ordem: 0,
  subtipo_id: 0,
  responsavel_modo: "FIXO",
  responsavel_contact_id: null,
  prazo_dias_uteis: 5,
  prioridade: "Normal",
  descricao_template: "",
  observacoes_template: null,
};

export default function EmbargosTemplatesPage() {
  const { team } = useParams();
  const navigate = useNavigate();
  const { toast } = useToast();
  const [data, setData] = useState<EmbargosTemplatesResponse | null>(null);
  const [tipos, setTipos] = useState<SubtypePickerTaskType[]>([]);
  const [usuarios, setUsuarios] = useState<SelectableUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [editando, setEditando] = useState<{ id?: number; form: EmbargosTemplateIn } | null>(null);
  const [salvando, setSalvando] = useState(false);
  const [excluir, setExcluir] = useState<EmbargosTemplate | null>(null);
  const descricaoRef = useRef<HTMLTextAreaElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await listarTemplatesEmbargos());
    } catch (e) {
      toast({ title: "Erro ao carregar os templates", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
    getTiposTarefa().then((t) => setTipos(t as SubtypePickerTaskType[])).catch(() => undefined);
    getUsuariosL1()
      .then((us) => setUsuarios(us.map((u) => ({ id: u.id, external_id: u.external_id, name: u.name, email: u.email, squads: u.squads ?? [] }))))
      .catch(() => undefined);
  }, [load]);

  const placeholders = useMemo(() => Object.entries(data?.placeholders ?? {}), [data]);
  const modos = data?.responsavel_modos ?? { FIXO: "Pessoa fixa", ADVOGADO_RESPONSAVEL: "Advogado responsável da execução" };

  const abrirNovo = () => setEditando({ form: { ...VAZIO, ordem: (data?.items.length ?? 0) + 1 } });
  const abrirEdicao = (t: EmbargosTemplate) =>
    setEditando({
      id: t.id,
      form: {
        nome: t.nome, ativo: t.ativo, ordem: t.ordem, subtipo_id: t.subtipo_id,
        responsavel_modo: t.responsavel_modo, responsavel_contact_id: t.responsavel_contact_id,
        prazo_dias_uteis: t.prazo_dias_uteis, prioridade: t.prioridade,
        descricao_template: t.descricao_template, observacoes_template: t.observacoes_template,
      },
    });

  const setForm = (patch: Partial<EmbargosTemplateIn>) =>
    setEditando((ed) => (ed ? { ...ed, form: { ...ed.form, ...patch } } : ed));

  const inserirPlaceholder = (chave: string) => {
    if (!editando) return;
    const el = descricaoRef.current;
    const texto = editando.form.descricao_template;
    const pos = el?.selectionStart ?? texto.length;
    setForm({ descricao_template: `${texto.slice(0, pos)}{${chave}}${texto.slice(pos)}` });
  };

  const salvar = async () => {
    if (!editando) return;
    setSalvando(true);
    try {
      await salvarTemplateEmbargos(editando.form, editando.id);
      toast({ title: editando.id ? "Template atualizado" : "Template criado" });
      setEditando(null);
      load();
    } catch (e) {
      toast({ title: "Não deu pra salvar", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setSalvando(false);
    }
  };

  const confirmarExclusao = async () => {
    if (!excluir) return;
    try {
      await excluirTemplateEmbargos(excluir.id);
      toast({ title: "Template excluído", description: "As tarefas já disparadas continuam no histórico de cada execução." });
      load();
    } catch (e) {
      toast({ title: "Não deu pra excluir", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setExcluir(null);
    }
  };

  const f = editando?.form;

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <Button variant="ghost" size="sm" className="-ml-2 mb-1 gap-1" onClick={() => navigate(`/minha-equipe/${team || "bb-cadastro"}?aba=embargos`)}>
            <ArrowLeft className="h-4 w-4" /> Embargos à Execução
          </Button>
          <h1 className="text-2xl font-bold tracking-tight">Templates de tarefa do incidente</h1>
          <p className="max-w-3xl text-sm text-muted-foreground">
            Depois que o incidente de embargos é cadastrado no Legal One (manual, por enquanto), o Flow localiza a pasta
            <span className="font-mono"> Proc - X/00N</span> e cria uma tarefa para cada template <strong>ativo</strong>,
            vinculada ao incidente. Tarefa já aberta do mesmo subtipo no incidente não é duplicada.
          </p>
        </div>
        <Button onClick={abrirNovo} className="gap-1"><Plus className="h-4 w-4" /> Novo template</Button>
      </div>

      <Card>
        <CardContent className="overflow-x-auto p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-12">Ordem</TableHead>
                <TableHead>Nome</TableHead>
                <TableHead>Subtipo</TableHead>
                <TableHead>Responsável</TableHead>
                <TableHead>Prazo</TableHead>
                <TableHead>Prioridade</TableHead>
                <TableHead>Situação</TableHead>
                <TableHead className="w-24" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading && !data ? (
                <TableRow>
                  <TableCell colSpan={8} className="py-8 text-center"><Loader2 className="mx-auto h-6 w-6 animate-spin text-muted-foreground" /></TableCell>
                </TableRow>
              ) : (data?.items ?? []).length === 0 ? (
                <TableRow>
                  <TableCell colSpan={8} className="py-8 text-center text-muted-foreground">
                    Nenhum template ainda. Crie as tarefas que devem nascer quando o incidente de embargos for cadastrado.
                  </TableCell>
                </TableRow>
              ) : (
                (data?.items ?? []).map((t) => (
                  <TableRow key={t.id} className={t.ativo ? "" : "text-muted-foreground"}>
                    <TableCell className="tabular-nums">{t.ordem}</TableCell>
                    <TableCell className="font-medium">{t.nome}</TableCell>
                    <TableCell className="text-xs">{t.subtipo_nome}</TableCell>
                    <TableCell className="text-xs">
                      {t.responsavel_modo === "FIXO" ? t.responsavel_nome : modos[t.responsavel_modo] ?? t.responsavel_modo}
                    </TableCell>
                    <TableCell>{t.prazo_dias_uteis} dia(s) útil(eis)</TableCell>
                    <TableCell>{PRIORIDADE_LABEL[t.prioridade] ?? t.prioridade}</TableCell>
                    <TableCell>{t.ativo ? <Badge variant="secondary">Ativo</Badge> : <Badge variant="outline">Inativo</Badge>}</TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="icon" title="Editar" onClick={() => abrirEdicao(t)}><Pencil className="h-4 w-4" /></Button>
                      <Button variant="ghost" size="icon" title="Excluir" onClick={() => setExcluir(t)}><Trash2 className="h-4 w-4" /></Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Dialog open={!!editando} onOpenChange={(v) => !v && setEditando(null)}>
        <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editando?.id ? "Editar template" : "Novo template"}</DialogTitle>
            <DialogDescription>A tarefa nasce no escritório do incidente, com prazo contado em dias úteis a partir do disparo.</DialogDescription>
          </DialogHeader>
          {f && (
            <div className="grid gap-3">
              <div className="grid grid-cols-[1fr_6rem] gap-3">
                <div className="grid gap-1"><Label>Nome</Label><Input value={f.nome} onChange={(e) => setForm({ nome: e.target.value })} placeholder="Impugnar embargos à execução" /></div>
                <div className="grid gap-1"><Label>Ordem</Label><Input type="number" value={f.ordem} onChange={(e) => setForm({ ordem: Number(e.target.value) })} /></div>
              </div>
              <SubtypePicker value={f.subtipo_id || null} taskTypes={tipos} required onChange={(id) => setForm({ subtipo_id: id })} />
              <div className="grid grid-cols-2 gap-3">
                <div className="grid gap-1">
                  <Label>Responsável</Label>
                  <Select value={f.responsavel_modo} onValueChange={(v) => setForm({ responsavel_modo: v, responsavel_contact_id: v === "FIXO" ? f.responsavel_contact_id : null })}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {Object.entries(modos).map(([k, v]) => (<SelectItem key={k} value={k}>{v}</SelectItem>))}
                    </SelectContent>
                  </Select>
                </div>
                {f.responsavel_modo === "FIXO" ? (
                  <div className="grid gap-1">
                    <Label>Pessoa</Label>
                    <UserSelector
                      users={usuarios}
                      value={f.responsavel_contact_id ? String(f.responsavel_contact_id) : null}
                      onChange={(v) => setForm({ responsavel_contact_id: v ? Number(v) : null })}
                      showEmail
                    />
                  </div>
                ) : (
                  <p className="self-end text-xs text-muted-foreground">
                    Usa o “Advogado(a) responsável” que veio no relatório do L1 para a execução.
                  </p>
                )}
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="grid gap-1">
                  <Label>Prazo (dias úteis após o disparo)</Label>
                  <Input type="number" min={0} max={60} value={f.prazo_dias_uteis} onChange={(e) => setForm({ prazo_dias_uteis: Number(e.target.value) })} />
                </div>
                <div className="grid gap-1">
                  <Label>Prioridade</Label>
                  <Select value={f.prioridade} onValueChange={(v) => setForm({ prioridade: v })}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {(data?.prioridades ?? ["Low", "Normal", "High"]).map((p) => (<SelectItem key={p} value={p}>{PRIORIDADE_LABEL[p] ?? p}</SelectItem>))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div className="grid gap-1">
                <Label>Descrição da tarefa</Label>
                <Textarea ref={descricaoRef} rows={3} value={f.descricao_template} onChange={(e) => setForm({ descricao_template: e.target.value })}
                  placeholder="Impugnar embargos {cnj_embargos} opostos por {embargante} — execução {pasta_execucao}" />
                <div className="flex flex-wrap gap-1">
                  {placeholders.map(([chave, ajuda]) => (
                    <button key={chave} type="button" title={ajuda} onClick={() => inserirPlaceholder(chave)}
                      className="rounded border px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground hover:bg-muted">
                      {`{${chave}}`}
                    </button>
                  ))}
                </div>
              </div>
              <div className="grid gap-1">
                <Label>Observações (opcional)</Label>
                <Textarea rows={2} value={f.observacoes_template ?? ""} onChange={(e) => setForm({ observacoes_template: e.target.value || null })} />
              </div>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox checked={f.ativo} onCheckedChange={(v) => setForm({ ativo: v === true })} /> Ativo (entra no disparo)
              </label>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditando(null)}>Cancelar</Button>
            <Button onClick={salvar} disabled={salvando || !f?.nome || !f?.subtipo_id || !f?.descricao_template}>
              {salvando && <Loader2 className="mr-1 h-4 w-4 animate-spin" />} Salvar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={!!excluir} onOpenChange={(v) => !v && setExcluir(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Excluir o template “{excluir?.nome}”?</AlertDialogTitle>
            <AlertDialogDescription>Os próximos disparos não criam mais essa tarefa. Para só pausar, desmarque “Ativo”.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction onClick={confirmarExclusao}>Excluir</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
