// frontend/src/pages/AdminPage.tsx

import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { TaxonomyToggleCard } from "@/components/TaxonomyToggleCard";
import { TemplateDrivenTaxonomyCard } from "@/components/TemplateDrivenTaxonomyCard";
import { useToast } from "@/hooks/use-toast";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Loader2, Save, Pencil, RefreshCw, AlertCircle, Copy, Shield, ShieldCheck, CheckCircle2, XCircle, Clock, Database, Building2, FileText, Link2, ChevronDown } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { apiFetch } from "@/lib/api-client";
import { MOSTRAR_LEGADO } from "@/components/admin/types";

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

            {MOSTRAR_LEGADO && (
              <>
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
              </>
            )}
        </div>
    );
}


export default SyncManager;
