// frontend/src/pages/AdminPage.tsx

import { useState, useEffect } from 'react';
import { useToast } from "@/hooks/use-toast";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Loader2, Save, Pencil, RefreshCw, AlertCircle, Copy, Shield, ShieldCheck, CheckCircle2, XCircle, Clock, Database, Building2, FileText, Link2, ChevronDown } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogClose } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { apiFetch } from "@/lib/api-client";
import { useTeams } from "@/lib/teams";
import { AdminUser, Office, PERMISSOES } from "@/components/admin/types";

// Campos que a listagem passou a devolver com o RBAC (migration usr005).
type UsuarioRBAC = AdminUser & {
  cargo_id?: number | null;
  cargo_nome?: string | null;
  excecoes?: number;
};
interface CargoRef { id: number; nome: string }
const TAMANHOS_PAGINA = [25, 50, 100];

// --- Componente de Usuários & Permissões ---
const UsersAndPermissions = () => {
    const { toast } = useToast();
    // Equipes vêm do catálogo (aba Equipes) — antes eram uma 3ª lista hardcoded
    // aqui, que vivia desalinhada do menu e causava permissão faltando.
    const EQUIPES = useTeams();
    const [editingUserId, setEditingUserId] = useState<number | null>(null);
    const [editingData, setEditingData] = useState<Partial<AdminUser>>({});
    const [tempPasswordDialog, setTempPasswordDialog] = useState<{ isOpen: boolean; password?: string; userName?: string }>({ isOpen: false });
    const [searchQuery, setSearchQuery] = useState('');
    // Sem acesso ficam ESCONDIDOS por padrão: eram 146 de 175 ativos, e
    // empurravam pra fora da tela justamente quem tem permissão.
    const [mostrarSemAcesso, setMostrarSemAcesso] = useState(false);
    const [mostrarInativos, setMostrarInativos] = useState(false);
    const [filtroCargo, setFiltroCargo] = useState<string>('todos');
    const [pagina, setPagina] = useState(0);
    const [porPagina, setPorPagina] = useState(25);

    const { data: cargos = [] } = useQuery({
        queryKey: ['admin-cargos-ref'],
        queryFn: async () => {
            const res = await apiFetch('/api/v1/admin/cargos');
            if (!res.ok) return [] as CargoRef[];
            return res.json() as Promise<CargoRef[]>;
        },
    });

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

    const temAlgumAcesso = (u: UsuarioRBAC) =>
        u.role === 'admin' ||
        PERMISSOES.some((p) => (u as any)[p.key]) ||
        (u.minha_equipe_equipes?.length ?? 0) > 0;

    const filtrados = (users as UsuarioRBAC[]).filter((u) => {
        const q = searchQuery.trim().toLowerCase();
        if (q && !u.name.toLowerCase().includes(q) && !u.email.toLowerCase().includes(q)) return false;
        if (!mostrarInativos && !u.is_active) return false;
        if (!mostrarSemAcesso && !temAlgumAcesso(u)) return false;
        if (filtroCargo !== 'todos') {
            if (filtroCargo === 'sem-cargo' && u.cargo_id) return false;
            if (filtroCargo !== 'sem-cargo' && String(u.cargo_id ?? '') !== filtroCargo) return false;
        }
        return true;
    });

    const totalPaginas = Math.max(1, Math.ceil(filtrados.length / porPagina));
    const paginaAtual = Math.min(pagina, totalPaginas - 1);
    const filteredUsers = filtrados.slice(paginaAtual * porPagina, (paginaAtual + 1) * porPagina);
    const ocultosSemAcesso = mostrarSemAcesso
        ? 0
        : (users as UsuarioRBAC[]).filter((u) => u.is_active && !temAlgumAcesso(u)).length;

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
                <div className="flex flex-wrap items-center gap-3">
                    <Input
                        placeholder="Buscar por nome ou e-mail..."
                        value={searchQuery}
                        onChange={(e) => { setSearchQuery(e.target.value); setPagina(0); }}
                        className="min-w-[220px] flex-1"
                    />
                    <Select value={filtroCargo} onValueChange={(v) => { setFiltroCargo(v); setPagina(0); }}>
                        <SelectTrigger className="w-[190px]"><SelectValue placeholder="Cargo" /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value="todos">Todos os cargos</SelectItem>
                            <SelectItem value="sem-cargo">Sem cargo</SelectItem>
                            {cargos.map((c) => (
                                <SelectItem key={c.id} value={String(c.id)}>{c.nome}</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <label className="flex cursor-pointer items-center gap-2 text-sm">
                        <Checkbox checked={mostrarSemAcesso}
                                  onCheckedChange={(c) => { setMostrarSemAcesso(!!c); setPagina(0); }} />
                        Mostrar sem acesso
                        {ocultosSemAcesso > 0 && (
                            <span className="text-xs text-muted-foreground">({ocultosSemAcesso} ocultos)</span>
                        )}
                    </label>
                    <label className="flex cursor-pointer items-center gap-2 text-sm">
                        <Checkbox checked={mostrarInativos}
                                  onCheckedChange={(c) => { setMostrarInativos(!!c); setPagina(0); }} />
                        Incluir inativos
                    </label>
                </div>
                <div className="overflow-x-auto">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Nome</TableHead>
                                <TableHead>E-mail</TableHead>
                                <TableHead>Status</TableHead>
                                <TableHead>Acesso</TableHead>
                                <TableHead>Papel</TableHead>
                                <TableHead>Cargo</TableHead>
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
                                        {/* De onde vem a permissão. A exceção é destacada de
                                            propósito: desvio individual não pode ser invisível. */}
                                        <Select
                                            value={String((user as UsuarioRBAC).cargo_id ?? '')}
                                            onValueChange={async (v) => {
                                                const res = await apiFetch(`/api/v1/admin/users/${user.id}/cargo`, {
                                                    method: 'PATCH',
                                                    headers: { 'Content-Type': 'application/json' },
                                                    body: JSON.stringify({ cargo_id: v ? Number(v) : null }),
                                                });
                                                if (res.ok) {
                                                    toast({ title: 'Cargo alterado', description: 'O acesso atual foi preservado como exceção.' });
                                                    refetchUsers();
                                                } else {
                                                    const b = await res.json().catch(() => ({}));
                                                    toast({ title: 'Erro ao trocar o cargo', description: b.detail, variant: 'destructive' });
                                                }
                                            }}
                                        >
                                            <SelectTrigger className="h-8 w-[150px] text-xs">
                                                <SelectValue placeholder="Sem cargo" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {cargos.map((c) => (
                                                    <SelectItem key={c.id} value={String(c.id)}>{c.nome}</SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                        {((user as UsuarioRBAC).excecoes ?? 0) > 0 && (
                                            <Badge variant="outline"
                                                   className="mt-1 border-amber-300 text-[10px] text-amber-800"
                                                   title="Este usuário tem permissões diferentes do cargo">
                                                +{(user as UsuarioRBAC).excecoes} exceção(ões)
                                            </Badge>
                                        )}
                                    </TableCell>
                                    <TableCell>
                                        {(() => {
                                            // Admin bypassa todos os gates no backend e o recálculo do RBAC
                                            // pula admins de propósito (as colunas can_use_* dele ficam
                                            // congeladas). Ler as colunas aqui mostrava um recorte antigo e o
                                            // checkbox "não pegava" — exibimos o acesso EFETIVO (total) e
                                            // travamos a edição.
                                            const ehAdmin = (user as unknown as { role?: string }).role === "admin";
                                            const get = (k: string) => ehAdmin || !!(user as unknown as Record<string, boolean>)[k];
                                            const ativas = PERMISSOES.filter((p) => get(p.key));
                                            return (
                                                <DropdownMenu>
                                                    <DropdownMenuTrigger asChild>
                                                        <Button variant="outline" size="sm" className="h-8 gap-1.5">
                                                            <span className="flex flex-wrap items-center gap-1">
                                                                {ehAdmin ? (
                                                                    <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                                                                        Total (admin)
                                                                    </span>
                                                                ) : ativas.length === 0 ? (
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
                                                        {ehAdmin && (
                                                            <div className="px-2 pb-1.5 text-[11px] leading-snug text-muted-foreground">
                                                                Administrador tem acesso total — os módulos não são editáveis aqui.
                                                            </div>
                                                        )}
                                                        <DropdownMenuSeparator />
                                                        {PERMISSOES.map((p) => (
                                                            <DropdownMenuCheckboxItem
                                                                key={p.key}
                                                                checked={get(p.key)}
                                                                onSelect={(e) => e.preventDefault()}
                                                                disabled={updateUserMutation.isPending || ehAdmin}
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
                                                                    checked={ehAdmin || equipes.includes(eq.key)}
                                                                    onSelect={(e) => e.preventDefault()}
                                                                    disabled={updateUserMutation.isPending || ehAdmin || !get("can_use_minha_equipe")}
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
                {/* Paginação — regra da casa: nenhuma listagem renderiza tudo.
                    Antes esta tabela desenhava os 312 usuários de uma vez. */}
                <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-3 text-xs text-muted-foreground">
                    <span>
                        {filtrados.length === 0
                            ? 'Nenhum usuário com esses filtros'
                            : `${paginaAtual * porPagina + 1}–${Math.min((paginaAtual + 1) * porPagina, filtrados.length)} de ${filtrados.length}`}
                        {' · '}Página {paginaAtual + 1} de {totalPaginas}
                    </span>
                    <div className="flex items-center gap-2">
                        <Select value={String(porPagina)}
                                onValueChange={(v) => { setPorPagina(Number(v)); setPagina(0); }}>
                            <SelectTrigger className="h-8 w-[110px] text-xs"><SelectValue /></SelectTrigger>
                            <SelectContent>
                                {TAMANHOS_PAGINA.map((n) => (
                                    <SelectItem key={n} value={String(n)}>{n} por página</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <Button size="sm" variant="outline" className="h-8 text-xs"
                                disabled={paginaAtual === 0}
                                onClick={() => setPagina((p) => Math.max(0, p - 1))}>Anterior</Button>
                        <Button size="sm" variant="outline" className="h-8 text-xs"
                                disabled={paginaAtual + 1 >= totalPaginas}
                                onClick={() => setPagina((p) => p + 1)}>Próxima</Button>
                    </div>
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


export default UsersAndPermissions;
