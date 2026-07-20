// frontend/src/pages/AdminPage.tsx

import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { TaxonomyToggleCard } from "@/components/TaxonomyToggleCard";
import { TemplateDrivenTaxonomyCard } from "@/components/TemplateDrivenTaxonomyCard";
import { useToast } from "@/hooks/use-toast";
import { useAuth } from "@/hooks/useAuth";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Loader2, Save, Pencil, RefreshCw, AlertCircle, Copy, Shield, ShieldCheck, CheckCircle2, XCircle, Clock, Database, Building2, FileText, Link2, ChevronDown } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from "@/components/ui/dialog";
import { MultiSelect } from "@/components/ui/MultiSelect";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { BaseProcessualPage } from "@/pages/BaseProcessualPage";
import UserSelector from "@/components/ui/UserSelector";
import { apiFetch } from "@/lib/api-client";
import { Trash2, Crown, Star } from "lucide-react";
import TaxonomiaAdminTab from "@/components/TaxonomiaAdminTab";
import { AdminNoticesManager } from "@/components/AdminNoticesManager";
import { UserFeedbackManager } from "@/components/UserFeedbackManager";

// Permissões de módulo exibidas no dropdown condensado da tabela de usuários.
const PERMISSOES = [
  { key: "can_schedule_batch", label: "LegalOne", abbr: "L1" },
  { key: "can_use_publications", label: "Publicações", abbr: "Pub" },
  { key: "can_use_prazos_iniciais", label: "Prazos Iniciais", abbr: "PI" },
  { key: "can_use_onerequest", label: "OneRequest", abbr: "OR" },
  { key: "can_use_minha_equipe", label: "Minha Equipe", abbr: "ME" },
  { key: "can_manage_distribuidos_bb", label: "Cadastro de Processo", abbr: "CP" },
  { key: "notify_onerequest_errors", label: "Notificação OneRequest", abbr: "Notif" },
] as const;

// Equipes disponíveis no "Minha Equipe" — filhas da permissão can_use_minha_equipe
// (a árvore: liberar o menu + marcar quais equipes o usuário enxerga).
const EQUIPES = [
  { key: "bb-reu", label: "BB Réu" },
  { key: "bb-execucao", label: "BB Execução & Encerramento" },
  { key: "bb-acordos", label: "BB Acordos" },
  { key: "bb-estrategico", label: "BB Estratégico" },
  { key: "master-reu", label: "Master Réu" },
  { key: "ativos-reu", label: "Ativos Réu" },
  { key: "trabalhista", label: "Trabalhista" },
  { key: "bb-autor-processual", label: "BB Autor — Processual" },
  { key: "ativos-autor", label: "Ativos Autor" },
  { key: "autor-recursal", label: "Autor — Recursal" },
  { key: "ajuizamento", label: "Ajuizamento" },
  { key: "estrategico-autor", label: "Estratégico Autor" },
  { key: "equipe-mista", label: "Equipe Mista" },
  { key: "bb-cadastro", label: "Controladoria" },
] as const;

// --- Tipos de Dados ---
interface Sector { id: number; name: string; }
interface Squad { id: number; name: string; }
interface AdminUser {
  id: number;
  name: string;
  email: string;
  external_id: number;
  is_active: boolean;
  role: string;
  can_schedule_batch: boolean;
  can_use_publications: boolean;
  can_use_prazos_iniciais: boolean;
  can_use_onerequest: boolean;
  can_use_minha_equipe: boolean;
  minha_equipe_equipes: string[];
  can_manage_distribuidos_bb: boolean;
  notify_onerequest_errors: boolean;
  default_office_id: number | null;
  has_password: boolean;
  is_sso: boolean;
  must_change_password: boolean;
}
interface Office {
  id: number;
  name: string;
}

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


// --- Tipos do cache-status ---
interface OfficeIndexStatus {
    office_id: number;
    office_name: string;
    total_ids: number;
    in_progress: boolean;
    progress_pct: number;
    status: string | null;
    error: string | null;
    is_fresh: boolean;
    last_sync: string | null;
}

interface CacheStatusResponse {
    metadata: { offices: number; users: number; task_types: number };
    office_index: { offices: OfficeIndexStatus[]; total_indexed: number; any_in_progress: boolean };
    lawsuit_cache: { total: number; fresh: number; stale: number; ttl_hours: number };
}

// --- Componente para Sincronização ---
const SyncManager = () => {
    const { toast } = useToast();
    const [isSyncing, setIsSyncing] = useState(false);
    const [isCacheWarming, setIsCacheWarming] = useState(false);
    const [polling, setPolling] = useState(false);

    // Polling do status de cache
    const { data: cacheStatus, refetch: refetchStatus } = useQuery<CacheStatusResponse>({
        queryKey: ['admin-cache-status'],
        queryFn: async () => {
            const res = await apiFetch('/api/v1/admin/cache-status');
            if (!res.ok) throw new Error('Falha ao carregar status');
            return res.json();
        },
        refetchInterval: polling ? 3000 : false,
    });

    // Controla polling: liga quando algo está in_progress, desliga quando termina
    useEffect(() => {
        if (cacheStatus?.office_index?.any_in_progress) {
            setPolling(true);
        } else if (polling && cacheStatus && !cacheStatus.office_index.any_in_progress) {
            // Acabou de terminar — mais um fetch e desliga
            setPolling(false);
            setIsCacheWarming(false);
        }
    }, [cacheStatus]);

    const handleSync = async () => {
        setIsSyncing(true);
        toast({
            title: "Sincronização Iniciada",
            description: "O processo foi iniciado em segundo plano e pode levar alguns minutos.",
        });
        try {
            const response = await apiFetch('/api/v1/admin/sync-metadata', { method: 'POST' });
            if (response.status !== 202) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Falha ao disparar a sincronização.');
            }
        } catch (error: any) {
            toast({ title: "Erro ao Iniciar Sincronização", description: error.message, variant: "destructive" });
        } finally {
            setTimeout(() => {
                setIsSyncing(false);
                refetchStatus();
            }, 3000);
        }
    };

    const handleCacheWarm = async () => {
        setIsCacheWarming(true);
        setPolling(true);
        try {
            const response = await apiFetch('/api/v1/admin/sync-caches', { method: 'POST' });
            if (response.status !== 202) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Falha ao disparar a pré-carga de caches.');
            }
            const data = await response.json();
            toast({
                title: "Pré-carga Disparada",
                description: `Sincronizando ${data.offices || '?'} escritórios...`,
            });
            // Primeiro refetch após 1s para capturar o in_progress
            setTimeout(() => refetchStatus(), 1000);
        } catch (error: any) {
            toast({ title: "Erro ao Iniciar Pré-carga", description: error.message, variant: "destructive" });
            setIsCacheWarming(false);
            setPolling(false);
        }
    };

    const formatDate = (iso: string | null) => {
        if (!iso) return '—';
        try {
            const d = new Date(iso);
            return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
        } catch { return iso; }
    };

    const meta = cacheStatus?.metadata;
    const officeIdx = cacheStatus?.office_index;
    const lawCache = cacheStatus?.lawsuit_cache;

    return (
        <div className="space-y-4">
            {/* Card: Metadados */}
            <Card>
                <CardHeader>
                    <CardTitle>Sincronização de Metadados</CardTitle>
                    <CardDescription>
                        Dados do sistema sincronizados com o Legal One: escritórios, usuários e tipos de tarefas.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    {meta && (
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                            <div className="flex items-center gap-3 rounded-lg border p-3">
                                <Building2 className="h-5 w-5 text-blue-600" />
                                <div>
                                    <p className="text-2xl font-bold">{meta.offices}</p>
                                    <p className="text-xs text-muted-foreground">Escritórios ativos</p>
                                </div>
                            </div>
                            <div className="flex items-center gap-3 rounded-lg border p-3">
                                <Shield className="h-5 w-5 text-green-600" />
                                <div>
                                    <p className="text-2xl font-bold">{meta.users}</p>
                                    <p className="text-xs text-muted-foreground">Usuários ativos</p>
                                </div>
                            </div>
                            <div className="flex items-center gap-3 rounded-lg border p-3">
                                <FileText className="h-5 w-5 text-purple-600" />
                                <div>
                                    <p className="text-2xl font-bold">{meta.task_types}</p>
                                    <p className="text-xs text-muted-foreground">Tipos de tarefa</p>
                                </div>
                            </div>
                        </div>
                    )}
                    <Button onClick={handleSync} disabled={isSyncing} size="sm">
                        {isSyncing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                        {isSyncing ? "Sincronizando..." : "Sincronizar Metadados"}
                    </Button>
                </CardContent>
            </Card>

            {/* Card: Polo dos Escritórios (Taxonomy v2 — fase 10) */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        Polo dos Escritórios
                        <Badge variant="outline" className="text-xs">Taxonomy v2</Badge>
                    </CardTitle>
                    <CardDescription>
                        Configure a qual polo do processo cada escritório atende
                        (ativo / passivo / ambos). Determina qual árvore aparece nos
                        templates desse escritório e é injetada no prompt do
                        classificador quando a v2 estiver ativa.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <Button asChild size="sm" variant="outline">
                        <Link to="/admin/offices/polo-scope">
                            Configurar polo por escritório
                        </Link>
                    </Button>
                </CardContent>
            </Card>

            {/* Card: Toggle Taxonomy v1 <-> v2 (fase 11) */}
            <TaxonomyToggleCard />

            {/* Card: Modo arvore enxuta - template-driven (fase 13) */}
            <TemplateDrivenTaxonomyCard />

            {/* Card: Cache de Escritórios (Índice) */}
            <Card>
                <CardHeader>
                    <div className="flex items-center justify-between">
                        <div>
                            <CardTitle>Índice de Processos por Escritório</CardTitle>
                            <CardDescription>
                                Mapeia cada escritório aos seus processos no Legal One. Usado para filtrar publicações.
                            </CardDescription>
                        </div>
                        {officeIdx && (
                            <div className="text-right">
                                <p className="text-2xl font-bold">{officeIdx.total_indexed.toLocaleString('pt-BR')}</p>
                                <p className="text-xs text-muted-foreground">processos indexados</p>
                            </div>
                        )}
                    </div>
                </CardHeader>
                <CardContent className="space-y-4">
                    {officeIdx && officeIdx.offices.length > 0 ? (
                        <div className="space-y-3">
                            {officeIdx.offices.map((office) => (
                                <div key={office.office_id} className="rounded-lg border p-3 space-y-2">
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="flex items-start gap-2 min-w-0 flex-1">
                                            <Building2 className="h-4 w-4 text-muted-foreground shrink-0 mt-0.5" />
                                            <div className="min-w-0 flex-1">
                                                <span className="font-medium text-sm break-words" title={office.office_name}>{office.office_name}</span>
                                                <span className="text-xs text-muted-foreground ml-1">({office.total_ids.toLocaleString('pt-BR')} processos)</span>
                                            </div>
                                        </div>
                                        <div className="flex items-center gap-2 shrink-0">
                                            {office.in_progress ? (
                                                <Badge variant="default" className="bg-blue-600">
                                                    <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                                                    Sincronizando {office.progress_pct}%
                                                </Badge>
                                            ) : office.status === 'success' ? (
                                                <Badge variant={office.is_fresh ? "default" : "secondary"} className={office.is_fresh ? "bg-green-600" : ""}>
                                                    <CheckCircle2 className="mr-1 h-3 w-3" />
                                                    {office.is_fresh ? "Atualizado" : "Desatualizado"}
                                                </Badge>
                                            ) : office.status === 'error' ? (
                                                <Badge variant="destructive">
                                                    <XCircle className="mr-1 h-3 w-3" />
                                                    Erro
                                                </Badge>
                                            ) : (
                                                <Badge variant="secondary">
                                                    <Clock className="mr-1 h-3 w-3" />
                                                    Nunca sincronizado
                                                </Badge>
                                            )}
                                        </div>
                                    </div>
                                    {office.in_progress && (
                                        <Progress value={office.progress_pct} className="h-2" />
                                    )}
                                    {office.error && (
                                        <p className="text-xs text-destructive truncate" title={office.error}>
                                            Erro: {office.error}
                                        </p>
                                    )}
                                    {office.last_sync && !office.in_progress && (
                                        <p className="text-xs text-muted-foreground">
                                            Último sync: {formatDate(office.last_sync)}
                                        </p>
                                    )}
                                </div>
                            ))}
                        </div>
                    ) : officeIdx ? (
                        <p className="text-sm text-muted-foreground">Nenhum escritório sincronizado ainda. Clique em "Pré-carregar Caches" para iniciar.</p>
                    ) : (
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                            <Loader2 className="h-4 w-4 animate-spin" />
                            Carregando status...
                        </div>
                    )}

                    <div className="flex items-center gap-3 pt-2 border-t">
                        <Button onClick={handleCacheWarm} disabled={isCacheWarming || (officeIdx?.any_in_progress ?? false)} size="sm" variant="outline">
                            {isCacheWarming || officeIdx?.any_in_progress ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            ) : (
                                <RefreshCw className="mr-2 h-4 w-4" />
                            )}
                            {isCacheWarming || officeIdx?.any_in_progress ? "Sincronizando..." : "Pré-carregar Caches"}
                        </Button>
                        <Button onClick={() => refetchStatus()} variant="ghost" size="sm">
                            <RefreshCw className="mr-1 h-3 w-3" />
                            Atualizar status
                        </Button>
                    </div>
                </CardContent>
            </Card>

            {/* Card: Cache de Dados de Processos */}
            <Card>
                <CardHeader>
                    <CardTitle>Cache de Dados de Processos</CardTitle>
                    <CardDescription>
                        Armazena localmente CNJ, data de criação e escritório responsável de cada processo.
                        Validade: {lawCache ? lawCache.ttl_hours : 24}h por processo.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    {lawCache ? (
                        <div className="space-y-3">
                            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                                <div className="flex items-center gap-3 rounded-lg border p-3">
                                    <Database className="h-5 w-5 text-blue-600" />
                                    <div>
                                        <p className="text-2xl font-bold">{lawCache.total.toLocaleString('pt-BR')}</p>
                                        <p className="text-xs text-muted-foreground">Total em cache</p>
                                    </div>
                                </div>
                                <div className="flex items-center gap-3 rounded-lg border p-3">
                                    <CheckCircle2 className="h-5 w-5 text-green-600" />
                                    <div>
                                        <p className="text-2xl font-bold">{lawCache.fresh.toLocaleString('pt-BR')}</p>
                                        <p className="text-xs text-muted-foreground">Atualizados (&lt;{lawCache.ttl_hours}h)</p>
                                    </div>
                                </div>
                                <div className="flex items-center gap-3 rounded-lg border p-3">
                                    <Clock className="h-5 w-5 text-amber-600" />
                                    <div>
                                        <p className="text-2xl font-bold">{lawCache.stale.toLocaleString('pt-BR')}</p>
                                        <p className="text-xs text-muted-foreground">Expirados</p>
                                    </div>
                                </div>
                            </div>
                            {lawCache.total > 0 && (
                                <div className="space-y-1">
                                    <div className="flex justify-between text-xs text-muted-foreground">
                                        <span>Cobertura do cache</span>
                                        <span>{Math.round((lawCache.fresh / Math.max(lawCache.total, 1)) * 100)}% atualizado</span>
                                    </div>
                                    <Progress value={Math.round((lawCache.fresh / Math.max(lawCache.total, 1)) * 100)} className="h-2" />
                                </div>
                            )}
                        </div>
                    ) : (
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                            <Loader2 className="h-4 w-4 animate-spin" />
                            Carregando status...
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}

// --- Componente de Usuários & Permissões ---
const UsersAndPermissions = () => {
    const { toast } = useToast();
    const [editingUserId, setEditingUserId] = useState<number | null>(null);
    const [editingData, setEditingData] = useState<Partial<AdminUser>>({});
    const [tempPasswordDialog, setTempPasswordDialog] = useState<{ isOpen: boolean; password?: string; userName?: string }>({ isOpen: false });
    const [searchQuery, setSearchQuery] = useState('');

    const { data: users = [], isLoading: usersLoading, refetch: refetchUsers } = useQuery({
        queryKey: ['admin-users'],
        queryFn: async () => {
            const res = await apiFetch('/api/v1/admin/users');
            if (!res.ok) throw new Error('Falha ao carregar usuários');
            return res.json() as Promise<AdminUser[]>;
        },
    });

    const { data: offices = [], isLoading: officesLoading } = useQuery({
        queryKey: ['offices'],
        queryFn: async () => {
            const res = await apiFetch('/api/v1/offices');
            if (!res.ok) throw new Error('Falha ao carregar escritórios');
            return res.json() as Promise<Office[]>;
        },
    });

    const updateUserMutation = useMutation({
        mutationFn: async (data: { userId: number; updates: Partial<AdminUser> }) => {
            const res = await apiFetch(`/api/v1/admin/users/${data.userId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data.updates),
            });
            if (!res.ok) throw new Error('Falha ao atualizar usuário');
            return res.json();
        },
        onSuccess: () => {
            toast({ title: 'Sucesso', description: 'Usuário atualizado.' });
            setEditingUserId(null);
            refetchUsers();
        },
        onError: (err: any) => {
            toast({ title: 'Erro', description: err.message, variant: 'destructive' });
        },
    });

    const activateUserMutation = useMutation({
        mutationFn: async (userId: number) => {
            const res = await apiFetch(`/api/v1/admin/users/${userId}/activate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
            });
            if (!res.ok) throw new Error('Falha ao ativar usuário');
            return res.json();
        },
        onSuccess: (data) => {
            setTempPasswordDialog({ isOpen: true, password: data.temp_password, userName: data.name });
            refetchUsers();
        },
        onError: (err: any) => {
            toast({ title: 'Erro', description: err.message, variant: 'destructive' });
        },
    });

    const resetPasswordMutation = useMutation({
        mutationFn: async (userId: number) => {
            const res = await apiFetch(`/api/v1/admin/users/${userId}/reset-password`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
            });
            if (!res.ok) throw new Error('Falha ao resetar senha');
            return res.json();
        },
        onSuccess: (data) => {
            setTempPasswordDialog({ isOpen: true, password: data.temp_password, userName: data.name });
            refetchUsers();
        },
        onError: (err: any) => {
            toast({ title: 'Erro', description: err.message, variant: 'destructive' });
        },
    });

    const deactivateUserMutation = useMutation({
        mutationFn: async (userId: number) => {
            const res = await apiFetch(`/api/v1/admin/users/${userId}/deactivate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
            });
            if (!res.ok) throw new Error('Falha ao desativar usuário');
            return res.json();
        },
        onSuccess: () => {
            toast({ title: 'Sucesso', description: 'Usuário desativado.' });
            refetchUsers();
        },
        onError: (err: any) => {
            toast({ title: 'Erro', description: err.message, variant: 'destructive' });
        },
    });

    const handleEditClick = (user: AdminUser) => {
        setEditingUserId(user.id);
        setEditingData({ ...user });
    };

    const handleSave = (userId: number) => {
        // Edit-mode agora cuida SÓ de papel + escritório; as permissões são
        // togladas direto no dropdown (salvam na hora), pra não haver conflito
        // (senão Salvar enviaria o snapshot antigo e reverteria o toggle).
        updateUserMutation.mutate({
            userId,
            updates: { role: editingData.role, default_office_id: editingData.default_office_id },
        });
    };

    const filteredUsers = users.filter(u =>
        u.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        u.email.toLowerCase().includes(searchQuery.toLowerCase())
    );

    const getOfficeName = (id: number | null) => {
        if (!id) return '—';
        return offices.find(o => o.id === id)?.name || 'Desconhecido';
    };

    const copyToClipboard = (text: string) => {
        navigator.clipboard.writeText(text);
        toast({ title: 'Copiado!', description: 'Senha copiada para a área de transferência.' });
    };

    if (usersLoading || officesLoading) return <Loader2 className="h-8 w-8 animate-spin" />;

    return (
        <Card>
            <CardHeader>
                <CardTitle>Usuários & Permissões</CardTitle>
                <CardDescription>Gerencie papéis, permissões e acesso dos usuários.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
                <Input
                    placeholder="Buscar por nome ou e-mail..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="w-full"
                />
                <div className="overflow-x-auto">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Nome</TableHead>
                                <TableHead>E-mail</TableHead>
                                <TableHead>Status</TableHead>
                                <TableHead>Acesso</TableHead>
                                <TableHead>Papel</TableHead>
                                <TableHead>Permissões</TableHead>
                                <TableHead>Escritório</TableHead>
                                <TableHead>Ações</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {filteredUsers.map((user) => (
                                <TableRow key={user.id}>
                                    <TableCell className="font-medium text-sm">{user.name}</TableCell>
                                    <TableCell className="font-mono text-sm">{user.email}</TableCell>
                                    <TableCell>
                                        <Badge variant={user.is_active ? "default" : "secondary"}>
                                            {user.is_active ? "Ativo" : "Inativo"}
                                        </Badge>
                                    </TableCell>
                                    <TableCell>
                                        {user.is_sso ? (
                                            <Badge variant="outline" className="border-blue-300 bg-blue-50 text-blue-700">
                                                Entra ID
                                            </Badge>
                                        ) : (
                                            <Badge variant={user.has_password ? "outline" : "destructive"}>
                                                {user.has_password ? "Configurado" : "Sem senha"}
                                            </Badge>
                                        )}
                                    </TableCell>
                                    <TableCell>
                                        {editingUserId === user.id ? (
                                            <Select value={editingData.role || ''} onValueChange={(v) => setEditingData({ ...editingData, role: v })}>
                                                <SelectTrigger className="w-24"><SelectValue /></SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="admin">Admin</SelectItem>
                                                    <SelectItem value="user">User</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        ) : (
                                            <span className="text-sm">{user.role}</span>
                                        )}
                                    </TableCell>
                                    <TableCell>
                                        {(() => {
                                            const get = (k: string) => !!(user as unknown as Record<string, boolean>)[k];
                                            const ativas = PERMISSOES.filter((p) => get(p.key));
                                            return (
                                                <DropdownMenu>
                                                    <DropdownMenuTrigger asChild>
                                                        <Button variant="outline" size="sm" className="h-8 gap-1.5">
                                                            <span className="flex flex-wrap items-center gap-1">
                                                                {ativas.length === 0 ? (
                                                                    <span className="text-xs text-muted-foreground">Nenhuma</span>
                                                                ) : (
                                                                    ativas.map((p) => (
                                                                        <span
                                                                            key={p.key}
                                                                            className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary"
                                                                        >
                                                                            {p.abbr}
                                                                        </span>
                                                                    ))
                                                                )}
                                                            </span>
                                                            <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-60" />
                                                        </Button>
                                                    </DropdownMenuTrigger>
                                                    {/* Lista cresceu (permissões + equipes): teto na altura disponível
                                                        da viewport + scroll interno, senão o menu estoura a tela. */}
                                                    <DropdownMenuContent
                                                        align="start"
                                                        collisionPadding={12}
                                                        className="max-h-[min(70vh,var(--radix-dropdown-menu-content-available-height))] w-56 overflow-y-auto"
                                                    >
                                                        <DropdownMenuLabel>Permissões de acesso</DropdownMenuLabel>
                                                        <DropdownMenuSeparator />
                                                        {PERMISSOES.map((p) => (
                                                            <DropdownMenuCheckboxItem
                                                                key={p.key}
                                                                checked={get(p.key)}
                                                                onSelect={(e) => e.preventDefault()}
                                                                disabled={updateUserMutation.isPending}
                                                                onCheckedChange={(c) =>
                                                                    updateUserMutation.mutate({
                                                                        userId: user.id,
                                                                        updates: { [p.key]: !!c } as Partial<AdminUser>,
                                                                    })
                                                                }
                                                            >
                                                                {p.label}
                                                            </DropdownMenuCheckboxItem>
                                                        ))}
                                                        <DropdownMenuSeparator />
                                                        <DropdownMenuLabel className="text-[11px] font-normal text-muted-foreground">
                                                            Equipes do Minha Equipe
                                                        </DropdownMenuLabel>
                                                        {EQUIPES.map((eq) => {
                                                            const equipes = (user as AdminUser).minha_equipe_equipes ?? [];
                                                            return (
                                                                <DropdownMenuCheckboxItem
                                                                    key={eq.key}
                                                                    checked={equipes.includes(eq.key)}
                                                                    onSelect={(e) => e.preventDefault()}
                                                                    disabled={updateUserMutation.isPending || !get("can_use_minha_equipe")}
                                                                    className="pl-7"
                                                                    onCheckedChange={(c) => {
                                                                        const next = c
                                                                            ? Array.from(new Set([...equipes, eq.key]))
                                                                            : equipes.filter((k) => k !== eq.key);
                                                                        updateUserMutation.mutate({
                                                                            userId: user.id,
                                                                            updates: { minha_equipe_equipes: next } as Partial<AdminUser>,
                                                                        });
                                                                    }}
                                                                >
                                                                    {eq.label}
                                                                </DropdownMenuCheckboxItem>
                                                            );
                                                        })}
                                                    </DropdownMenuContent>
                                                </DropdownMenu>
                                            );
                                        })()}
                                    </TableCell>
                                    <TableCell>
                                        {editingUserId === user.id ? (
                                            <Select value={editingData.default_office_id ? String(editingData.default_office_id) : '__none__'} onValueChange={(v) => setEditingData({ ...editingData, default_office_id: v === '__none__' ? null : parseInt(v) })}>
                                                <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="__none__">Nenhum</SelectItem>
                                                    {offices.map((o) => (
                                                        <SelectItem key={o.id} value={String(o.id)}>{o.name}</SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        ) : (
                                            <span className="text-sm">{getOfficeName(user.default_office_id)}</span>
                                        )}
                                    </TableCell>
                                    <TableCell className="space-y-1">
                                        {editingUserId === user.id ? (
                                            <div className="flex gap-1">
                                                <Button
                                                    size="sm"
                                                    variant="default"
                                                    onClick={() => handleSave(user.id)}
                                                    disabled={updateUserMutation.isPending}
                                                >
                                                    <Save className="h-3 w-3" />
                                                </Button>
                                                <Button
                                                    size="sm"
                                                    variant="secondary"
                                                    onClick={() => setEditingUserId(null)}
                                                >
                                                    ✕
                                                </Button>
                                            </div>
                                        ) : (
                                            <div className="flex flex-col gap-1">
                                                {!user.has_password && (
                                                    <Button
                                                        size="sm"
                                                        variant="default"
                                                        onClick={() => activateUserMutation.mutate(user.id)}
                                                        disabled={activateUserMutation.isPending}
                                                    >
                                                        <Shield className="h-3 w-3 mr-1" />
                                                        Ativar
                                                    </Button>
                                                )}
                                                {user.has_password && (
                                                    <Button
                                                        size="sm"
                                                        variant="outline"
                                                        onClick={() => resetPasswordMutation.mutate(user.id)}
                                                        disabled={resetPasswordMutation.isPending}
                                                    >
                                                        Resetar
                                                    </Button>
                                                )}
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    onClick={() => handleEditClick(user)}
                                                >
                                                    <Pencil className="h-3 w-3" />
                                                </Button>
                                                <Button
                                                    size="sm"
                                                    variant={user.is_active ? "outline" : "default"}
                                                    onClick={() => user.is_active ? deactivateUserMutation.mutate(user.id) : null}
                                                    disabled={deactivateUserMutation.isPending || !user.is_active}
                                                >
                                                    {user.is_active ? "Desativar" : "Inativo"}
                                                </Button>
                                            </div>
                                        )}
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            </CardContent>

            <Dialog open={tempPasswordDialog.isOpen} onOpenChange={(open) => setTempPasswordDialog({ isOpen: open })}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Senha Gerada para {tempPasswordDialog.userName}</DialogTitle>
                    </DialogHeader>
                    <Alert className="bg-blue-50 border-blue-200">
                        <AlertCircle className="h-4 w-4 text-blue-600" />
                        <AlertDescription className="text-blue-800">
                            Esta senha só será exibida uma vez. Copie-a com segurança e repasse ao usuário.
                        </AlertDescription>
                    </Alert>
                    <div className="flex gap-2 items-center bg-muted p-3 rounded font-mono text-sm">
                        <span className="flex-1 break-all">{tempPasswordDialog.password}</span>
                        <Button
                            size="sm"
                            variant="outline"
                            onClick={() => copyToClipboard(tempPasswordDialog.password || '')}
                        >
                            <Copy className="h-4 w-4" />
                        </Button>
                    </div>
                    <DialogFooter>
                        <Button onClick={() => setTempPasswordDialog({ isOpen: false })}>Fechar</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </Card>
    );
};

// --- Componente: Unificação de Contas SSO (Microsoft Entra ID) ---
//
// Pessoas que logaram pela 1a vez via Entra e ainda nao estao vinculadas a um
// usuario do Legal One (external_id NULL). Como o e-mail da Microsoft costuma
// divergir do cadastrado no Legal One, o backend sugere candidatos por
// similaridade de nome (pg_trgm/unaccent). Unificar reescreve o e-mail do
// usuario do Legal One para o e-mail da Microsoft, mantendo permissoes/dados.
//
interface SsoCandidate {
    id: number;
    name: string;
    email: string;
    external_id: number | null;
    role: string;
    is_active: boolean;
    similarity: number;
}
interface SsoPending {
    id: number;
    name: string;
    email: string;
    candidates: SsoCandidate[];
}

const SsoUnifyManager = () => {
    const { toast } = useToast();

    const { data: pendings = [], isLoading, refetch } = useQuery({
        queryKey: ['admin-sso-pending'],
        queryFn: async () => {
            const res = await apiFetch('/api/v1/admin/sso/pending');
            if (!res.ok) throw new Error('Falha ao carregar contas pendentes');
            return res.json() as Promise<SsoPending[]>;
        },
    });

    const unifyMutation = useMutation({
        mutationFn: async (vars: { pendingId: number; targetId: number }) => {
            const res = await apiFetch('/api/v1/admin/sso/unify', {
                method: 'POST',
                body: JSON.stringify({ pending_user_id: vars.pendingId, target_user_id: vars.targetId }),
            });
            if (!res.ok) {
                const body = await res.json().catch(() => ({}));
                throw new Error(body.detail || `HTTP ${res.status}`);
            }
            return res.json();
        },
        onSuccess: (data) => {
            toast({
                title: 'Contas unificadas',
                description: `${data.user?.name} agora entra com ${data.user?.email}.`,
            });
            refetch();
        },
        onError: (err: any) => {
            toast({ title: 'Erro ao unificar', description: err.message, variant: 'destructive' });
        },
    });

    if (isLoading) return <Loader2 className="h-8 w-8 animate-spin" />;

    return (
        <Card>
            <CardHeader>
                <div className="flex items-start justify-between gap-3">
                    <div>
                        <CardTitle className="flex items-center gap-2">
                            <Link2 className="h-5 w-5" />
                            Unificar Contas (Entra ID)
                        </CardTitle>
                        <CardDescription>
                            Quem entrou pela primeira vez via Microsoft e ainda não está vinculado
                            a um usuário do Legal One. Como o e-mail da Microsoft costuma divergir
                            do cadastrado no Legal One, sugerimos vínculos por <strong>similaridade
                            de nome</strong>. Ao unificar, o e-mail da Microsoft passa a ser o
                            e-mail daquele usuário do Legal One — mantendo permissões e dados.
                        </CardDescription>
                    </div>
                    <Button variant="ghost" size="sm" onClick={() => refetch()} className="shrink-0">
                        <RefreshCw className="mr-1 h-3 w-3" /> Atualizar
                    </Button>
                </div>
            </CardHeader>
            <CardContent className="space-y-4">
                {pendings.length === 0 ? (
                    <Alert>
                        <CheckCircle2 className="h-4 w-4" />
                        <AlertDescription>
                            Nenhuma conta pendente de vínculo no momento.
                        </AlertDescription>
                    </Alert>
                ) : (
                    pendings.map((p) => (
                        <div key={p.id} className="rounded-lg border p-4 space-y-3">
                            <div className="flex items-center gap-3 flex-wrap">
                                <Badge variant="secondary" className="gap-1">
                                    <Clock className="h-3 w-3" /> Pendente
                                </Badge>
                                <span className="font-medium">{p.name}</span>
                                <span className="font-mono text-sm text-muted-foreground">{p.email}</span>
                            </div>

                            {p.candidates.length === 0 ? (
                                <Alert variant="destructive">
                                    <AlertCircle className="h-4 w-4" />
                                    <AlertDescription>
                                        Nenhum usuário do Legal One parecido. Confira o nome ou cadastre manualmente.
                                    </AlertDescription>
                                </Alert>
                            ) : (
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Candidato (Legal One)</TableHead>
                                            <TableHead>E-mail atual</TableHead>
                                            <TableHead className="w-20 text-center">Papel</TableHead>
                                            <TableHead className="w-28 text-center">Semelhança</TableHead>
                                            <TableHead className="w-28"></TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {p.candidates.map((c) => {
                                            const pct = Math.round(c.similarity * 100);
                                            return (
                                                <TableRow key={c.id}>
                                                    <TableCell className="font-medium text-sm">
                                                        {c.name}
                                                        {!c.is_active && (
                                                            <Badge variant="secondary" className="ml-2">Inativo</Badge>
                                                        )}
                                                    </TableCell>
                                                    <TableCell className="font-mono text-xs text-muted-foreground break-all">
                                                        {c.email}
                                                    </TableCell>
                                                    <TableCell className="text-center text-sm">{c.role}</TableCell>
                                                    <TableCell className="text-center">
                                                        <Badge
                                                            variant={pct >= 60 ? 'default' : pct >= 40 ? 'secondary' : 'outline'}
                                                            className={pct >= 60 ? 'bg-green-600' : ''}
                                                        >
                                                            {pct}%
                                                        </Badge>
                                                    </TableCell>
                                                    <TableCell className="text-right">
                                                        <Button
                                                            size="sm"
                                                            onClick={() => {
                                                                if (confirm(
                                                                    `Unificar "${p.name}" (${p.email}) com "${c.name}"?\n\n` +
                                                                    `O e-mail ${c.email} será substituído por ${p.email}. ` +
                                                                    `A conta pendente será removida.`
                                                                )) {
                                                                    unifyMutation.mutate({ pendingId: p.id, targetId: c.id });
                                                                }
                                                            }}
                                                            disabled={unifyMutation.isPending}
                                                        >
                                                            <Link2 className="mr-1 h-3 w-3" /> Unificar
                                                        </Button>
                                                    </TableCell>
                                                </TableRow>
                                            );
                                        })}
                                    </TableBody>
                                </Table>
                            )}
                        </div>
                    ))
                )}
            </CardContent>
        </Card>
    );
};

// --- Componente Principal da Página (Renderizando todos) ---
const AdminPage = () => {
    const { isAdmin } = useAuth();

    if (!isAdmin) {
        return (
            <div className="space-y-6">
                <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4" />
                    <AlertTitle>Acesso Negado</AlertTitle>
                    <AlertDescription>Você não tem permissão para acessar esta página.</AlertDescription>
                </Alert>
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
                        <ShieldCheck className="h-6 w-6" />
                        Painel Administrativo
                    </h1>
                    <p className="text-muted-foreground">
                        Gerencie as configurações e associações do sistema.
                    </p>
                </div>
            </div>

            <Tabs defaultValue="sync" className="w-full">
                <TabsList>
                    <TabsTrigger value="sync">Sincronização</TabsTrigger>
                    <TabsTrigger value="squads">Squads</TabsTrigger>
                    <TabsTrigger value="taxonomy">Taxonomia</TabsTrigger>
                    <TabsTrigger value="users">Usuários & Permissões</TabsTrigger>
                    <TabsTrigger value="sso">Contas SSO</TabsTrigger>
                    <TabsTrigger value="notices">Avisos</TabsTrigger>
                    <TabsTrigger value="feedback">Feedback</TabsTrigger>
                    <TabsTrigger value="base-processual">Base Banco Master</TabsTrigger>
                </TabsList>
                <TabsContent value="sync" className="space-y-6">
                    <SyncManager />
                </TabsContent>
                <TabsContent value="squads" className="space-y-6">
                    <SquadsManager />
                </TabsContent>
                <TabsContent value="taxonomy" className="space-y-6">
                    <TaxonomiaAdminTab />
                </TabsContent>
                <TabsContent value="users" className="space-y-6">
                    <UsersAndPermissions />
                </TabsContent>
                <TabsContent value="sso" className="space-y-6">
                    <SsoUnifyManager />
                </TabsContent>
                <TabsContent value="notices" className="space-y-6">
                    <AdminNoticesManager />
                </TabsContent>
                <TabsContent value="feedback" className="space-y-6">
                    <UserFeedbackManager />
                </TabsContent>
            <TabsContent value="base-processual" className="space-y-6">
                    <BaseProcessualPage />
                </TabsContent>
            </Tabs>
        </div>
    )
}

export default AdminPage;
