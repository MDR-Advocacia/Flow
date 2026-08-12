// Painel Administrativo → Cargos (RBAC).
//
// A permissão passa a ser POR CARGO — o modelo do Painel Financeiro. Antes cada
// um dos 312 usuários era configurado na mão, e o levantamento de 29/07 mostrou
// o estrago: 146 sem permissão nenhuma e os 29 com acesso espalhados em 16
// combinações quase todas únicas.
//
// Exceção individual continua existindo (na aba Usuários) — o cargo é a
// política, a exceção é o desvio consciente.

import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, Pencil, Plus, Trash2, Users } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import { apiFetch } from "@/lib/api-client";

interface Modulo { key: string; label: string; abbr: string }
interface EquipeCat { key: string; label: string; grupo: string }
interface ModoEquipe { key: string; label: string }
interface Cargo {
  id: number;
  nome: string;
  descricao: string | null;
  modulos: Record<string, boolean>;
  equipes_modo: string;
  equipes: string[];
  ativo: boolean;
  usuarios: number;
}

const VAZIO = {
  nome: "", descricao: "", modulos: {} as Record<string, boolean>,
  equipes_modo: "nenhuma", equipes: [] as string[],
};

export default function CargosManager() {
  const { toast } = useToast();
  const [cargos, setCargos] = useState<Cargo[]>([]);
  const [modulos, setModulos] = useState<Modulo[]>([]);
  const [equipes, setEquipes] = useState<EquipeCat[]>([]);
  const [modos, setModos] = useState<ModoEquipe[]>([]);
  const [loading, setLoading] = useState(false);
  const [salvando, setSalvando] = useState(false);
  const [editando, setEditando] = useState<Cargo | null>(null);
  const [aberto, setAberto] = useState(false);
  const [form, setForm] = useState({ ...VAZIO });

  const carregar = useCallback(async () => {
    setLoading(true);
    try {
      const [rc, rcat] = await Promise.all([
        apiFetch("/api/v1/admin/cargos"),
        apiFetch("/api/v1/admin/cargos/catalogo"),
      ]);
      if (!rc.ok) throw new Error(`HTTP ${rc.status}`);
      setCargos(await rc.json());
      if (rcat.ok) {
        const cat = await rcat.json();
        setModulos(cat.modulos ?? []);
        setEquipes(cat.equipes ?? []);
        setModos(cat.equipes_modos ?? []);
      }
    } catch (e: any) {
      toast({ title: "Erro ao carregar cargos", description: e.message, variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { void carregar(); }, [carregar]);

  const abrirNovo = () => { setEditando(null); setForm({ ...VAZIO }); setAberto(true); };
  const abrirEditar = (c: Cargo) => {
    setEditando(c);
    setForm({
      nome: c.nome, descricao: c.descricao ?? "", modulos: { ...(c.modulos || {}) },
      equipes_modo: c.equipes_modo, equipes: [...(c.equipes || [])],
    });
    setAberto(true);
  };

  const salvar = async () => {
    const nome = form.nome.trim();
    if (!nome) { toast({ title: "Informe o nome do cargo", variant: "destructive" }); return; }
    setSalvando(true);
    try {
      const res = await apiFetch(
        editando ? `/api/v1/admin/cargos/${editando.id}` : "/api/v1/admin/cargos",
        {
          method: editando ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            nome, descricao: form.descricao || null, modulos: form.modulos,
            equipes_modo: form.equipes_modo, equipes: form.equipes,
          }),
        },
      );
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b.detail || `HTTP ${res.status}`);
      }
      const body = await res.json().catch(() => ({}));
      toast({
        title: editando ? "Cargo atualizado" : "Cargo criado",
        description: editando && body?.recalculados
          ? `${body.recalculados} usuário(s) tiveram o acesso recalculado.`
          : nome,
      });
      setAberto(false);
      await carregar();
    } catch (e: any) {
      toast({ title: "Não foi possível salvar", description: e.message, variant: "destructive" });
    } finally { setSalvando(false); }
  };

  const excluir = async (c: Cargo) => {
    if (!confirm(`Excluir o cargo "${c.nome}"?`)) return;
    setSalvando(true);
    try {
      const res = await apiFetch(`/api/v1/admin/cargos/${c.id}`, { method: "DELETE" });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b.detail || `HTTP ${res.status}`);
      }
      toast({ title: "Cargo excluído", description: c.nome });
      await carregar();
    } catch (e: any) {
      toast({ title: "Não foi possível excluir", description: e.message, variant: "destructive" });
    } finally { setSalvando(false); }
  };

  const porGrupo = useMemo(() => {
    const m = new Map<string, EquipeCat[]>();
    for (const e of equipes) m.set(e.grupo, [...(m.get(e.grupo) ?? []), e]);
    return m;
  }, [equipes]);

  const toggleModulo = (k: string) =>
    setForm((f) => ({ ...f, modulos: { ...f.modulos, [k]: !f.modulos[k] } }));
  const toggleEquipe = (k: string) =>
    setForm((f) => ({
      ...f,
      equipes: f.equipes.includes(k) ? f.equipes.filter((x) => x !== k) : [...f.equipes, k],
    }));

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
        <div>
          <CardTitle>Cargos e permissões</CardTitle>
          <CardDescription>
            A permissão é do <b>cargo</b>: marque os módulos uma vez e coloque as pessoas nele.
            Ajustes individuais ficam como <b>exceção</b> na aba Usuários. Editar um cargo recalcula
            todo mundo que o herda.
          </CardDescription>
        </div>
        <Button onClick={abrirNovo} className="shrink-0 gap-1.5">
          <Plus className="h-4 w-4" /> Novo cargo
        </Button>
      </CardHeader>
      <CardContent>
        {loading && cargos.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            <Loader2 className="mr-1 inline h-4 w-4 animate-spin" /> Carregando…
          </p>
        ) : (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Cargo</TableHead>
                  <TableHead>Módulos</TableHead>
                  <TableHead>Equipes</TableHead>
                  <TableHead className="text-center">Pessoas</TableHead>
                  <TableHead className="text-right">Ações</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {cargos.map((c) => {
                  const ativos = modulos.filter((m) => c.modulos?.[m.key]);
                  const modoLabel = modos.find((m) => m.key === c.equipes_modo)?.label ?? c.equipes_modo;
                  return (
                    <TableRow key={c.id} className={c.ativo ? "" : "opacity-60"}>
                      <TableCell>
                        <div className="font-medium">{c.nome}</div>
                        {c.descricao && (
                          <div className="text-xs text-muted-foreground">{c.descricao}</div>
                        )}
                      </TableCell>
                      <TableCell>
                        {ativos.length === 0 ? (
                          <span className="text-xs text-muted-foreground">nenhum</span>
                        ) : (
                          <div className="flex flex-wrap gap-1">
                            {ativos.map((m) => (
                              <Badge key={m.key} variant="secondary" className="text-[10px]">
                                {m.abbr}
                              </Badge>
                            ))}
                          </div>
                        )}
                      </TableCell>
                      <TableCell className="text-xs">
                        {modoLabel}
                        {c.equipes_modo === "lista" && c.equipes?.length > 0 && (
                          <span className="text-muted-foreground"> ({c.equipes.length})</span>
                        )}
                      </TableCell>
                      <TableCell className="text-center">
                        <span className="inline-flex items-center gap-1 text-sm tabular-nums">
                          <Users className="h-3.5 w-3.5 text-muted-foreground" /> {c.usuarios}
                        </span>
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-1">
                          <Button size="sm" variant="ghost" className="h-8 gap-1 text-xs"
                                  onClick={() => abrirEditar(c)} disabled={salvando}>
                            <Pencil className="h-3.5 w-3.5" /> Editar
                          </Button>
                          <Button size="sm" variant="ghost"
                                  className="h-8 gap-1 text-xs text-rose-600 hover:text-rose-700"
                                  onClick={() => excluir(c)} disabled={salvando || c.usuarios > 0}
                                  title={c.usuarios > 0 ? "Mova as pessoas para outro cargo antes" : "Excluir"}>
                            <Trash2 className="h-3.5 w-3.5" /> Excluir
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>

      <Dialog open={aberto} onOpenChange={(o) => { if (!o) setAberto(false); }}>
        <DialogContent className="max-h-[88vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editando ? `Editar ${editando.nome}` : "Novo cargo"}</DialogTitle>
            <DialogDescription>
              {editando && editando.usuarios > 0
                ? `Salvar recalcula o acesso de ${editando.usuarios} pessoa(s) neste cargo.`
                : "Defina o que o cargo enxerga. Depois é só colocar pessoas nele."}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-5 py-1">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="cargo-nome">Nome</Label>
                <Input id="cargo-nome" value={form.nome} autoFocus placeholder="Ex.: Supervisor BB"
                       onChange={(e) => setForm((f) => ({ ...f, nome: e.target.value }))} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="cargo-desc">Descrição (opcional)</Label>
                <Input id="cargo-desc" value={form.descricao} placeholder="Pra que serve este cargo"
                       onChange={(e) => setForm((f) => ({ ...f, descricao: e.target.value }))} />
              </div>
            </div>

            <div className="space-y-2">
              <Label>Módulos que o cargo enxerga</Label>
              <div className="grid gap-1.5 rounded-md border p-3 sm:grid-cols-2">
                {modulos.map((m) => (
                  <label key={m.key} className="flex cursor-pointer items-center gap-2 text-sm">
                    <Checkbox checked={!!form.modulos[m.key]} onCheckedChange={() => toggleModulo(m.key)} />
                    {m.label}
                  </label>
                ))}
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="cargo-modo">Equipes do Minha Equipe</Label>
              <Select value={form.equipes_modo}
                      onValueChange={(v) => setForm((f) => ({ ...f, equipes_modo: v }))}>
                <SelectTrigger id="cargo-modo"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {modos.map((m) => (
                    <SelectItem key={m.key} value={m.key}>{m.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {form.equipes_modo === "supervisionadas" && (
                <p className="rounded-md border border-indigo-200 bg-indigo-50 p-2 text-xs text-indigo-900">
                  O sistema resolve sozinho: a pessoa enxerga as equipes em que ela é supervisora.
                  Mudou de equipe, o acesso acompanha — sem editar permissão.
                </p>
              )}
              {form.equipes_modo === "lista" && (
                <div className="max-h-48 space-y-3 overflow-y-auto rounded-md border p-3">
                  {Array.from(porGrupo.entries()).map(([grupo, lista]) => (
                    <div key={grupo}>
                      <div className="mb-1 text-xs font-semibold text-muted-foreground">{grupo}</div>
                      <div className="grid gap-1 sm:grid-cols-2">
                        {lista.map((e) => (
                          <label key={e.key} className="flex cursor-pointer items-center gap-2 text-sm">
                            <Checkbox checked={form.equipes.includes(e.key)}
                                      onCheckedChange={() => toggleEquipe(e.key)} />
                            {e.label}
                          </label>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setAberto(false)} disabled={salvando}>Cancelar</Button>
            <Button onClick={salvar} disabled={salvando || !form.nome.trim()}>
              {salvando && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {editando ? "Salvar" : "Criar cargo"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
