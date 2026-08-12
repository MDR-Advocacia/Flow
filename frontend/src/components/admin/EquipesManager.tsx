// Painel Administrativo → Equipes.
//
// As equipes do Minha Equipe viviam hardcoded no código (3 listas diferentes) e
// criar uma exigia deploy. Agora saem da tabela `perf_equipe`.
//
// Duas regras que a tela deixa explícitas pro operador:
//  - o IDENTIFICADOR (slug) é imutável: ele é gravado nas pessoas e nas
//    permissões já concedidas, então renomear a key revogaria acesso;
//  - excluir é DESATIVAR: a equipe some do menu e dos dropdowns, mas o
//    histórico continua resolvendo o nome (e dá pra reativar).

import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, Pencil, Plus, RotateCcw, Trash2, Users } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import { apiFetch } from "@/lib/api-client";
import { carregarEquipes } from "@/lib/teams";

interface Equipe {
  id: number;
  key: string;
  label: string;
  grupo: string;
  ordem: number;
  ativo: boolean;
  pessoas: number;
}

// Espelha o _slug_equipe do backend — só pra PREVIEW do identificador.
function slugify(texto: string): string {
  return (texto || "")
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^A-Za-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .toLowerCase();
}

export default function EquipesManager() {
  const { toast } = useToast();
  const [equipes, setEquipes] = useState<Equipe[]>([]);
  const [loading, setLoading] = useState(false);
  const [salvando, setSalvando] = useState(false);
  const [editando, setEditando] = useState<Equipe | null>(null);
  const [criando, setCriando] = useState(false);
  const [form, setForm] = useState({ label: "", grupo: "" });

  const carregar = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiFetch("/api/v1/admin/equipes");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setEquipes(await res.json());
    } catch (e: any) {
      toast({ title: "Erro ao carregar equipes", description: e.message, variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void carregar();
  }, [carregar]);

  const grupos = useMemo(
    () => Array.from(new Set(equipes.map((e) => e.grupo))).sort(),
    [equipes],
  );
  const porGrupo = useMemo(() => {
    const m = new Map<string, Equipe[]>();
    for (const e of equipes) m.set(e.grupo, [...(m.get(e.grupo) ?? []), e]);
    return m;
  }, [equipes]);

  // Depois de qualquer escrita, recarrega TAMBÉM o catálogo global — senão a
  // sidebar e os dropdowns seguem com a lista antiga até um F5.
  const posEscrita = async () => {
    await carregar();
    await carregarEquipes(true);
  };

  const abrirCriar = () => {
    setForm({ label: "", grupo: grupos[0] ?? "" });
    setCriando(true);
  };

  const abrirEditar = (e: Equipe) => {
    setForm({ label: e.label, grupo: e.grupo });
    setEditando(e);
  };

  const salvar = async () => {
    const label = form.label.trim();
    const grupo = form.grupo.trim();
    if (!label || !grupo) {
      toast({ title: "Preencha o nome e o grupo", variant: "destructive" });
      return;
    }
    setSalvando(true);
    try {
      const editandoId = editando?.id;
      const res = await apiFetch(
        editandoId ? `/api/v1/admin/equipes/${editandoId}` : "/api/v1/admin/equipes",
        {
          method: editandoId ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ label, grupo }),
        },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      toast({
        title: editandoId ? "Equipe atualizada" : "Equipe criada",
        description: editandoId
          ? label
          : `${label} — libere o acesso em "Usuários & Permissões" e monte o time pelo roster.`,
      });
      setCriando(false);
      setEditando(null);
      await posEscrita();
    } catch (e: any) {
      toast({ title: "Não foi possível salvar", description: e.message, variant: "destructive" });
    } finally {
      setSalvando(false);
    }
  };

  const alternarAtivo = async (e: Equipe) => {
    if (e.ativo) {
      const aviso = e.pessoas
        ? `\n\nAtenção: ${e.pessoas} pessoa(s) estão vinculadas a ela — continuam no cadastro, mas a equipe sai do menu.`
        : "";
      if (!confirm(`Desativar a equipe "${e.label}"?${aviso}\n\nO histórico e as permissões são preservados, e dá pra reativar depois.`)) return;
    }
    setSalvando(true);
    try {
      const res = e.ativo
        ? await apiFetch(`/api/v1/admin/equipes/${e.id}`, { method: "DELETE" })
        : await apiFetch(`/api/v1/admin/equipes/${e.id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ label: e.label, grupo: e.grupo, ativo: true }),
          });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      toast({ title: e.ativo ? "Equipe desativada" : "Equipe reativada", description: e.label });
      await posEscrita();
    } catch (err: any) {
      toast({ title: "Erro", description: err.message, variant: "destructive" });
    } finally {
      setSalvando(false);
    }
  };

  const previewKey = slugify(form.label);

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
        <div>
          <CardTitle>Equipes do Minha Equipe</CardTitle>
          <CardDescription>
            Cada equipe vira um item no menu, uma rota e uma permissão. Criar aqui não exige deploy —
            depois libere o acesso em <b>Usuários &amp; Permissões</b> e monte o time pelo roster da
            própria tela da equipe.
          </CardDescription>
        </div>
        <Button onClick={abrirCriar} className="shrink-0 gap-1.5">
          <Plus className="h-4 w-4" /> Nova equipe
        </Button>
      </CardHeader>
      <CardContent>
        {loading && equipes.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            <Loader2 className="mr-1 inline h-4 w-4 animate-spin" /> Carregando…
          </p>
        ) : (
          <div className="space-y-6">
            {Array.from(porGrupo.entries()).map(([grupo, lista]) => (
              <div key={grupo}>
                <h4 className="mb-2 text-sm font-semibold text-muted-foreground">{grupo}</h4>
                <div className="rounded-md border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Equipe</TableHead>
                        <TableHead>Identificador</TableHead>
                        <TableHead className="text-center">Pessoas</TableHead>
                        <TableHead className="text-center">Status</TableHead>
                        <TableHead className="text-right">Ações</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {lista.map((e) => (
                        <TableRow key={e.id} className={e.ativo ? "" : "opacity-60"}>
                          <TableCell className="font-medium">{e.label}</TableCell>
                          <TableCell>
                            <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{e.key}</code>
                          </TableCell>
                          <TableCell className="text-center tabular-nums">
                            <span className="inline-flex items-center gap-1 text-sm">
                              <Users className="h-3.5 w-3.5 text-muted-foreground" /> {e.pessoas}
                            </span>
                          </TableCell>
                          <TableCell className="text-center">
                            {e.ativo ? (
                              <Badge variant="outline" className="border-emerald-300 text-emerald-700">Ativa</Badge>
                            ) : (
                              <Badge variant="outline" className="text-muted-foreground">Desativada</Badge>
                            )}
                          </TableCell>
                          <TableCell className="text-right">
                            <div className="flex justify-end gap-1">
                              <Button
                                size="sm" variant="ghost" className="h-8 gap-1 text-xs"
                                onClick={() => abrirEditar(e)} disabled={salvando}
                              >
                                <Pencil className="h-3.5 w-3.5" /> Editar
                              </Button>
                              <Button
                                size="sm" variant="ghost"
                                className={`h-8 gap-1 text-xs ${e.ativo ? "text-rose-600 hover:text-rose-700" : "text-emerald-700"}`}
                                onClick={() => alternarAtivo(e)} disabled={salvando}
                              >
                                {e.ativo ? (
                                  <><Trash2 className="h-3.5 w-3.5" /> Desativar</>
                                ) : (
                                  <><RotateCcw className="h-3.5 w-3.5" /> Reativar</>
                                )}
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>

      <Dialog
        open={criando || editando !== null}
        onOpenChange={(o) => {
          if (!o) { setCriando(false); setEditando(null); }
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{editando ? "Editar equipe" : "Nova equipe"}</DialogTitle>
            <DialogDescription>
              {editando ? (
                <>O identificador <code className="rounded bg-muted px-1 text-xs">{editando.key}</code> não
                muda — ele está gravado nas pessoas e nas permissões já concedidas.</>
              ) : (
                "O identificador é gerado a partir do nome e não pode ser alterado depois."
              )}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-1">
            <div className="space-y-1.5">
              <Label htmlFor="eq-label">Nome da equipe</Label>
              <Input
                id="eq-label" value={form.label} autoFocus
                placeholder="Ex.: Cobrança"
                onChange={(ev) => setForm((f) => ({ ...f, label: ev.target.value }))}
              />
              {!editando && previewKey && (
                <p className="text-xs text-muted-foreground">
                  Identificador: <code className="rounded bg-muted px-1">{previewKey}</code>
                </p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="eq-grupo">Grupo</Label>
              <Input
                id="eq-grupo" value={form.grupo} list="grupos-existentes"
                placeholder="Ex.: Recuperação de Crédito"
                onChange={(ev) => setForm((f) => ({ ...f, grupo: ev.target.value }))}
              />
              <datalist id="grupos-existentes">
                {grupos.map((g) => <option key={g} value={g} />)}
              </datalist>
              <p className="text-xs text-muted-foreground">
                Agrupa no menu lateral. Escolha um existente ou digite um novo.
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setCriando(false); setEditando(null); }} disabled={salvando}>
              Cancelar
            </Button>
            <Button onClick={salvar} disabled={salvando || !form.label.trim() || !form.grupo.trim()}>
              {salvando && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {editando ? "Salvar" : "Criar equipe"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
