// frontend/src/pages/AdminPage.tsx

import { useState, useEffect } from 'react';
import { useToast } from "@/hooks/use-toast";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Loader2, Save, Pencil, RefreshCw, AlertCircle, Copy, Shield, ShieldCheck, CheckCircle2, XCircle, Clock, Database, Building2, FileText, Link2, ChevronDown } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import UserSelector from "@/components/ui/UserSelector";
import { apiFetch } from "@/lib/api-client";
import { Trash2, Crown, Star } from "lucide-react";
import { Squad } from "@/components/admin/types";

// --- Componente: Gerenciamento de Squads (membros + leader/assistente) ---
//
// Squads sao agrupadas por escritorio responsavel (LegalOneOffice). Cada
// squad tem 1 leader e 1 assistente. Backend garante max 1 por papel.
// Membros sao adicionados via UserSelector com busca + checkbox criar squad.
//
interface SquadMemberDetail {
  id: number;
  is_leader: boolean;
  is_assistant: boolean;
  user: { id: number; external_id: number; name: string; is_active: boolean };
}
interface OfficeRef {
  external_id: number;
  name: string;
  path: string | null;
}
interface SquadDetail {
  id: number;
  name: string;
  is_active: boolean;
  kind?: string; // 'principal' | 'support'
  office_external_id: number | null;
  office: OfficeRef | null;
  members: SquadMemberDetail[];
}
interface L1User { external_id: number; name: string; is_active: boolean }
interface OfficeOption { external_id: number; name: string; path: string }

const SquadsManager = () => {
  const { toast } = useToast();
  const [offices, setOffices] = useState<OfficeOption[]>([]);
  const [selectedOffice, setSelectedOffice] = useState<string | null>(null);
  const [squads, setSquads] = useState<SquadDetail[]>([]);
  const [allUsers, setAllUsers] = useState<L1User[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<number | null>(null);
  const [addingTo, setAddingTo] = useState<number | null>(null);
  const [pickedUserId, setPickedUserId] = useState<string | null>(null);
  const [creatingSquad, setCreatingSquad] = useState(false);
  const [newSquadName, setNewSquadName] = useState("");
  const [newSquadKind, setNewSquadKind] = useState<"principal" | "support">("principal");
  // Renomear squad inline (CRUD completo: criar/renomear/excluir).
  const [renamingSquad, setRenamingSquad] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const usersForPicker = allUsers
    .filter((u) => u.is_active)
    .map((u) => ({
      id: u.external_id,
      external_id: u.external_id,
      name: u.name,
      squads: [],
    }));

  const fetchInitial = async () => {
    setLoading(true);
    try {
      const officesRes = await apiFetch("/api/v1/offices");
      if (officesRes.ok) {
        setOffices(await officesRes.json());
      } else {
        console.warn("SquadsManager: /offices falhou", officesRes.status);
        toast({ title: "Falha ao carregar escritórios", description: `HTTP ${officesRes.status}`, variant: "destructive" });
      }
    } catch (err: any) {
      console.error("SquadsManager: erro em /offices", err);
      toast({ title: "Erro de rede (escritórios)", description: err.message, variant: "destructive" });
    }
    try {
      let usersRes = await apiFetch("/api/v1/squads/legal-one-users");
      if (!usersRes.ok) {
        usersRes = await apiFetch("/api/v1/users/with-squads");
      }
      if (usersRes.ok) {
        setAllUsers(await usersRes.json());
      }
    } catch (err: any) {
      console.error("SquadsManager: erro em users", err);
    }
    setLoading(false);
  };

  const fetchSquads = async (officeExternalId: string) => {
    try {
      const res = await apiFetch(`/api/v1/squads?office_external_id=${officeExternalId}`);
      if (!res.ok) throw new Error("Falha ao carregar squads.");
      setSquads(await res.json());
    } catch (err: any) {
      toast({ title: "Erro", description: err.message, variant: "destructive" });
    }
  };

  useEffect(() => { fetchInitial(); }, []);
  useEffect(() => {
    if (selectedOffice) fetchSquads(selectedOffice);
    else setSquads([]);
  }, [selectedOffice]);

  const createSquad = async () => {
    if (!selectedOffice || !newSquadName.trim()) return;
    try {
      const res = await apiFetch("/api/v1/squads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: newSquadName.trim(),
          office_external_id: parseInt(selectedOffice, 10),
          kind: newSquadKind,
          members: [],
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      toast({ title: "Squad criada" });
      setCreatingSquad(false);
      setNewSquadName("");
      setNewSquadKind("principal");
      await fetchSquads(selectedOffice);
    } catch (err: any) {
      toast({ title: "Erro ao criar squad", description: err.message, variant: "destructive" });
    }
  };

  // Renomear squad (PUT parcial — só o nome; membros ficam intocados).
  const renameSquad = async (squadId: number) => {
    const nome = renameValue.trim();
    if (!nome) return;
    setSaving(squadId);
    try {
      const res = await apiFetch(`/api/v1/squads/${squadId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: nome }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      toast({ title: "Squad renomeada", description: nome });
      setRenamingSquad(null);
      if (selectedOffice) await fetchSquads(selectedOffice);
    } catch (err: any) {
      toast({ title: "Erro ao renomear", description: err.message, variant: "destructive" });
    } finally {
      setSaving(null);
    }
  };

  // Excluir squad (soft-delete no backend: desativa e preserva o histórico).
  const deleteSquad = async (squad: SquadDetail) => {
    const membros = squad.members.length
      ? ` Ela tem ${squad.members.length} membro(s).`
      : "";
    if (!confirm(`Excluir a squad "${squad.name}"?${membros} Ela será desativada (o histórico é preservado).`)) return;
    setSaving(squad.id);
    try {
      const res = await apiFetch(`/api/v1/squads/${squad.id}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) {
        throw new Error(`HTTP ${res.status}`);
      }
      toast({ title: "Squad excluída", description: squad.name });
      if (selectedOffice) await fetchSquads(selectedOffice);
    } catch (err: any) {
      toast({ title: "Erro ao excluir", description: err.message, variant: "destructive" });
    } finally {
      setSaving(null);
    }
  };

  const toggleRole = async (
    squadId: number,
    memberId: number,
    field: "is_leader" | "is_assistant",
    nextValue: boolean,
  ) => {
    setSaving(squadId);
    try {
      const res = await apiFetch(`/api/v1/squads/${squadId}/members/${memberId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [field]: nextValue }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      toast({ title: "Atualizado", description: nextValue ? "Papel definido." : "Papel removido." });
      if (selectedOffice) await fetchSquads(selectedOffice);
    } catch (err: any) {
      toast({ title: "Erro ao salvar", description: err.message, variant: "destructive" });
    } finally {
      setSaving(null);
    }
  };

  const removeMember = async (squadId: number, memberId: number) => {
    if (!confirm("Remover este membro da squad?")) return;
    setSaving(squadId);
    try {
      const res = await apiFetch(`/api/v1/squads/${squadId}/members/${memberId}`, {
        method: "DELETE",
      });
      if (!res.ok && res.status !== 204) {
        throw new Error(`HTTP ${res.status}`);
      }
      toast({ title: "Removido" });
      if (selectedOffice) await fetchSquads(selectedOffice);
    } catch (err: any) {
      toast({ title: "Erro", description: err.message, variant: "destructive" });
    } finally {
      setSaving(null);
    }
  };

  const addMember = async (squadId: number) => {
    if (!pickedUserId) return;
    setSaving(squadId);
    try {
      const res = await apiFetch(`/api/v1/squads/${squadId}/members`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: parseInt(pickedUserId, 10),
          is_leader: false,
          is_assistant: false,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      toast({ title: "Membro adicionado" });
      setAddingTo(null);
      setPickedUserId(null);
      if (selectedOffice) await fetchSquads(selectedOffice);
    } catch (err: any) {
      toast({ title: "Erro", description: err.message, variant: "destructive" });
    } finally {
      setSaving(null);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Squads — Membros e Papéis</CardTitle>
        <CardDescription>
          Filtre por escritório responsável e gerencie quem é líder e assistente de cada
          squad. O assistente recebe automaticamente as tarefas marcadas como "tarefa do
          assistente" no template.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="max-w-md">
          <Label>Escritório responsável</Label>
          <Select value={selectedOffice || ""} onValueChange={(v) => setSelectedOffice(v || null)}>
            <SelectTrigger><SelectValue placeholder="Selecione um escritório" /></SelectTrigger>
            <SelectContent>
              {offices.map((o) => (
                <SelectItem key={o.external_id} value={String(o.external_id)}>
                  {o.path || o.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {loading && <p className="text-sm text-muted-foreground">Carregando…</p>}

        {!loading && selectedOffice && (
          <div className="flex items-center gap-2 flex-wrap">
            {creatingSquad ? (
              <>
                <Input
                  placeholder="Nome da nova squad"
                  value={newSquadName}
                  onChange={(e) => setNewSquadName(e.target.value)}
                  className="max-w-sm"
                />
                <Select value={newSquadKind} onValueChange={(v) => setNewSquadKind(v as "principal" | "support")}>
                  <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="principal">Principal</SelectItem>
                    <SelectItem value="support">Suporte</SelectItem>
                  </SelectContent>
                </Select>
                <Button size="sm" onClick={createSquad} disabled={!newSquadName.trim()}>
                  Criar
                </Button>
                <Button size="sm" variant="outline" onClick={() => { setCreatingSquad(false); setNewSquadName(""); }}>
                  Cancelar
                </Button>
              </>
            ) : (
              <Button size="sm" variant="outline" onClick={() => setCreatingSquad(true)}>
                + Criar squad neste escritório
              </Button>
            )}
          </div>
        )}

        {!loading && selectedOffice && squads.length === 0 && !creatingSquad && (
          <Alert><AlertDescription>Nenhuma squad ativa nesse escritório.</AlertDescription></Alert>
        )}

        {!loading && squads.length > 0 && (
          <Accordion type="multiple" className="space-y-2">
            {squads.map((squad) => {
              const leader = squad.members.find((m) => m.is_leader);
              return (
                <AccordionItem key={squad.id} value={String(squad.id)} className="border rounded-md px-3">
                  {renamingSquad === squad.id ? (
                    // Modo renomear: input inline no lugar do header.
                    <div className="flex items-center gap-2 py-3">
                      <Input
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        className="h-8 max-w-sm"
                        autoFocus
                        onKeyDown={(e) => {
                          if (e.key === "Enter") renameSquad(squad.id);
                          if (e.key === "Escape") setRenamingSquad(null);
                        }}
                      />
                      <Button
                        size="sm"
                        onClick={() => renameSquad(squad.id)}
                        disabled={!renameValue.trim() || saving === squad.id}
                      >
                        Salvar
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => setRenamingSquad(null)}>
                        Cancelar
                      </Button>
                    </div>
                  ) : (
                    <div className="flex items-center">
                      <AccordionTrigger className="hover:no-underline flex-1">
                        <div className="flex items-center gap-3 flex-1 flex-wrap">
                          <span className="font-medium">{squad.name}</span>
                          {squad.kind === "support" && (
                            <Badge variant="secondary" className="bg-purple-100 text-purple-700 border-purple-200">Suporte</Badge>
                          )}
                          <Badge variant="secondary">{squad.members.length} {squad.members.length === 1 ? "membro" : "membros"}</Badge>
                          {leader && (
                            <Badge variant="default" className="gap-1">
                              <Crown className="h-3 w-3" /> {leader.user.name}
                            </Badge>
                          )}
                          {squad.members.filter((m) => m.is_assistant).map((m) => (
                            <Badge key={m.id} variant="outline" className="gap-1">
                              <Star className="h-3 w-3" /> {m.user.name}
                            </Badge>
                          ))}
                        </div>
                      </AccordionTrigger>
                      <div className="flex shrink-0 items-center gap-0.5 pl-2">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => {
                            setRenamingSquad(squad.id);
                            setRenameValue(squad.name);
                          }}
                          disabled={saving === squad.id}
                          aria-label="Renomear squad"
                          title="Renomear squad"
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8 text-muted-foreground hover:text-rose-600"
                          onClick={() => deleteSquad(squad)}
                          disabled={saving === squad.id}
                          aria-label="Excluir squad"
                          title="Excluir squad (desativa; histórico preservado)"
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                  )}
                  <AccordionContent>
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Membro</TableHead>
                          <TableHead className="w-32 text-center">Líder</TableHead>
                          <TableHead className="w-32 text-center">Assistente</TableHead>
                          <TableHead className="w-20"></TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {squad.members.map((m) => (
                          <TableRow key={m.id}>
                            <TableCell>{m.user.name}</TableCell>
                            <TableCell className="text-center">
                              <Checkbox
                                checked={m.is_leader}
                                onCheckedChange={(v) => toggleRole(squad.id, m.id, "is_leader", !!v)}
                                disabled={saving === squad.id}
                              />
                            </TableCell>
                            <TableCell className="text-center">
                              <Checkbox
                                checked={m.is_assistant}
                                onCheckedChange={(v) => toggleRole(squad.id, m.id, "is_assistant", !!v)}
                                disabled={saving === squad.id}
                              />
                            </TableCell>
                            <TableCell>
                              <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => removeMember(squad.id, m.id)}
                                disabled={saving === squad.id}
                                aria-label="Remover membro"
                              >
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            </TableCell>
                          </TableRow>
                        ))}
                        {squad.members.length === 0 && (
                          <TableRow>
                            <TableCell colSpan={4} className="text-center text-sm text-muted-foreground">
                              Nenhum membro cadastrado.
                            </TableCell>
                          </TableRow>
                        )}
                      </TableBody>
                    </Table>

                    {addingTo === squad.id ? (
                      <div className="mt-3 flex items-end gap-2">
                        <div className="flex-1">
                          <Label className="text-xs">Adicionar usuário</Label>
                          <UserSelector
                            users={usersForPicker}
                            value={pickedUserId}
                            onChange={setPickedUserId}
                            placeholder="Selecione um usuário..."
                          />
                        </div>
                        <Button onClick={() => addMember(squad.id)} disabled={!pickedUserId || saving === squad.id}>
                          Adicionar
                        </Button>
                        <Button variant="outline" onClick={() => { setAddingTo(null); setPickedUserId(null); }}>
                          Cancelar
                        </Button>
                      </div>
                    ) : (
                      <Button
                        variant="outline"
                        size="sm"
                        className="mt-3"
                        onClick={() => { setAddingTo(squad.id); setPickedUserId(null); }}
                      >
                        + Adicionar membro
                      </Button>
                    )}
                  </AccordionContent>
                </AccordionItem>
              );
            })}
          </Accordion>
        )}
      </CardContent>
    </Card>
  );
};



export default SquadsManager;
