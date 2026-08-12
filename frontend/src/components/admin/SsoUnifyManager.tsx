import { useToast } from "@/hooks/use-toast";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Loader2, Save, Pencil, RefreshCw, AlertCircle, Copy, Shield, ShieldCheck, CheckCircle2, XCircle, Clock, Database, Building2, FileText, Link2, ChevronDown } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { apiFetch } from "@/lib/api-client";

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


export default SsoUnifyManager;
