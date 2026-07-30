import { Fragment, useState, useMemo, useEffect, useRef } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import {
  Clock,
  Play,
  Trash2,
  Loader2,
  Plus,
  History,
  Eye,
  Zap,
  AlertCircle,
  Pencil,
} from 'lucide-react';
import { format } from 'date-fns';
import { ptBR } from 'date-fns/locale';

import { useToast } from '@/hooks/use-toast';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Checkbox } from '@/components/ui/checkbox';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui/scroll-area';
import { MultiSelect } from '@/components/ui/MultiSelect';
import { apiFetch } from '@/lib/api-client';

interface Automation {
  id: number;
  name: string;
  cron_expression: string;
  is_enabled: boolean;
  office_ids: number[];
  steps: string[];
  initial_lookback_days: number | null;
  overlap_hours: number | null;
  next_run_at: string | null;
  last_run_at: string | null;
  last_status?: string | null;
  latest_run_status?: string | null;
  latest_run_started_at?: string | null;
  latest_run_finished_at?: string | null;
  latest_run_progress_phase?: string | null;
  latest_run_progress_current?: number | null;
  latest_run_progress_total?: number | null;
  latest_run_progress_message?: string | null;
  latest_run_progress_updated_at?: string | null;
  created_at: string;
  updated_at?: string;
}

interface AutomationRun {
  id: number;
  automation_id: number;
  started_at: string;
  completed_at: string | null;
  status: string;
  error_message: string | null;
  steps_executed: string[];
}

interface Office {
  id: number;
  name: string;
}

const cronPresets = [
  { label: 'Diariamente 07h', value: '0 7 * * *' },
  { label: 'A cada 2 horas', value: '0 */2 * * *' },
  { label: 'A cada 6 horas', value: '0 */6 * * *' },
  { label: 'Semanalmente (segunda 07h)', value: '0 7 * * 1' },
];

const AutomationsPage = () => {
  const { toast } = useToast();
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [selectedAutomation, setSelectedAutomation] = useState<Automation | null>(null);
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);

  const defaultForm = {
    name: 'Diário 07h — Puxar + Classificar',
    cron: '0 7 * * *',
    offices: [] as string[],
    steps: ['pull_publications', 'classify'] as string[],
    enabled: true,
    initialLookbackDays: 3 as number,
    overlapHours: 1 as number,
  };

  const resetForm = () => {
    setEditingId(null);
    setNewAutomation(defaultForm);
  };

  const openEditDialog = (a: Automation) => {
    setEditingId(a.id);
    setNewAutomation({
      name: a.name,
      cron: a.cron_expression || '',
      offices: (a.office_ids || []).map(String),
      steps: a.steps || [],
      enabled: a.is_enabled,
      initialLookbackDays: a.initial_lookback_days ?? 3,
      overlapHours: a.overlap_hours ?? 1,
    });
    setIsDialogOpen(true);
  };
  const [newAutomation, setNewAutomation] = useState({
    name: 'Diário 07h — Puxar + Classificar',
    cron: '0 7 * * *',
    offices: [] as string[],
    steps: ['pull_publications', 'classify'] as string[],
    enabled: true,
    initialLookbackDays: 3 as number,
    overlapHours: 1 as number,
  });

  const { data: automations = [], isLoading, refetch } = useQuery({
    queryKey: ['automations'],
    queryFn: async () => {
      const res = await apiFetch('/api/v1/automations');
      if (!res.ok) throw new Error('Falha ao carregar agendamentos');
      const data = await res.json();
      return (Array.isArray(data) ? data : data.items || []) as Automation[];
    },
    // Enquanto alguma automação estiver com run em execução, refetch a cada 3s.
    // Assim que todas terminarem (success/failed), para de poluir a rede.
    refetchInterval: (q) => {
      const list = (q.state.data ?? []) as Automation[];
      const anyRunning = list.some((a) => a.latest_run_status === 'running');
      return anyRunning ? 3000 : false;
    },
  });

  // Tick a cada 1s enquanto houver alguma execução ativa, para a barra
  // de progresso poder atualizar o tempo decorrido.
  const anyRunning = automations.some((a) => a.latest_run_status === 'running');
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!anyRunning) return;
    const h = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(h);
  }, [anyRunning]);

  const formatElapsed = (startIso?: string | null) => {
    if (!startIso) return '';
    const diffMs = Date.now() - new Date(startIso).getTime();
    if (diffMs < 0 || !isFinite(diffMs)) return '';
    const s = Math.floor(diffMs / 1000);
    const mm = Math.floor(s / 60);
    const ss = s % 60;
    return `${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;
  };

  // Detecta transição de running → success/failed e dispara toast.
  const prevStatusRef = useRef<Record<number, string | null | undefined>>({});
  useEffect(() => {
    const prev = prevStatusRef.current;
    automations.forEach((a) => {
      const before = prev[a.id];
      const now = a.latest_run_status;
      if (before === 'running' && now && now !== 'running') {
        if (now === 'success') {
          toast({
            title: 'Execução concluída',
            description: `"${a.name}" terminou com sucesso.`,
          });
        } else if (now === 'warning') {
          toast({
            title: 'Contingência DJEN acionada',
            description: `"${a.name}" executou busca suplementar no DJEN (cobertura parcial). A reconciliação obrigatória com o Legal One está pendente.`,
          });
        } else if (now === 'failed') {
          toast({
            title: 'Execução falhou',
            description: `"${a.name}" não completou. Veja o histórico para detalhes.`,
            variant: 'destructive',
          });
        }
      }
      prev[a.id] = now;
    });
  }, [automations, toast]);

  const { data: offices = [] } = useQuery({
    queryKey: ['offices'],
    queryFn: async () => {
      const res = await apiFetch('/api/v1/offices');
      if (!res.ok) return [];
      return res.json() as Promise<Office[]>;
    },
  });

  const { data: currentRuns = [], isLoading: runsLoading } = useQuery({
    queryKey: ['automation-runs', selectedAutomation?.id],
    queryFn: async () => {
      if (!selectedAutomation) return [];
      const res = await apiFetch(`/api/v1/automations/${selectedAutomation.id}/runs`);
      if (!res.ok) return [];
      const data = await res.json();
      return (Array.isArray(data) ? data : data.items || []) as AutomationRun[];
    },
    enabled: !!selectedAutomation,
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      const payload = {
        name: newAutomation.name,
        cron_expression: newAutomation.cron,
        office_ids: newAutomation.offices.map(Number),
        steps: newAutomation.steps,
        initial_lookback_days: newAutomation.initialLookbackDays,
        overlap_hours: newAutomation.overlapHours,
      };
      const res = await apiFetch('/api/v1/automations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        let detail = `Falha ao criar agendamento (HTTP ${res.status}).`;
        try {
          const errBody = await res.json();
          if (errBody?.detail) {
            if (typeof errBody.detail === 'string') {
              detail = errBody.detail;
            } else if (Array.isArray(errBody.detail)) {
              detail = errBody.detail
                .map((e: any) => {
                  const field = Array.isArray(e.loc) ? e.loc.slice(1).join('.') : e.loc;
                  return `${field}: ${e.msg}`;
                })
                .join(' | ');
            }
          }
        } catch {
          /* ignore */
        }
        throw new Error(detail);
      }
      return res.json();
    },
    onSuccess: () => {
      toast({ title: 'Sucesso', description: 'Agendamento criado.' });
      setIsDialogOpen(false);
      resetForm();
      refetch();
    },
    onError: (err: any) => {
      toast({ title: 'Erro', description: err.message, variant: 'destructive' });
    },
  });

  const updateMutation = useMutation({
    mutationFn: async () => {
      if (!editingId) throw new Error('Sem id para atualizar');
      const payload = {
        name: newAutomation.name,
        cron_expression: newAutomation.cron,
        office_ids: newAutomation.offices.map(Number),
        steps: newAutomation.steps,
        is_enabled: newAutomation.enabled,
        initial_lookback_days: newAutomation.initialLookbackDays,
        overlap_hours: newAutomation.overlapHours,
      };
      const res = await apiFetch(`/api/v1/automations/${editingId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        let detail = `Falha ao atualizar (HTTP ${res.status}).`;
        try {
          const errBody = await res.json();
          if (errBody?.detail) {
            if (typeof errBody.detail === 'string') detail = errBody.detail;
            else if (Array.isArray(errBody.detail)) {
              detail = errBody.detail
                .map((e: any) => {
                  const field = Array.isArray(e.loc) ? e.loc.slice(1).join('.') : e.loc;
                  return `${field}: ${e.msg}`;
                })
                .join(' | ');
            }
          }
        } catch { /* ignore */ }
        throw new Error(detail);
      }
      return res.json();
    },
    onSuccess: () => {
      toast({ title: 'Sucesso', description: 'Agendamento atualizado.' });
      setIsDialogOpen(false);
      resetForm();
      refetch();
    },
    onError: (err: any) => {
      toast({ title: 'Erro', description: err.message, variant: 'destructive' });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: number) => {
      const res = await apiFetch(`/api/v1/automations/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Falha ao deletar');
      return res.json();
    },
    onSuccess: () => {
      toast({ title: 'Sucesso', description: 'Agendamento deletado.' });
      refetch();
    },
    onError: (err: any) => {
      toast({ title: 'Erro', description: err.message, variant: 'destructive' });
    },
  });

  const runMutation = useMutation({
    mutationFn: async (id: number) => {
      const res = await apiFetch(`/api/v1/automations/${id}/run`, { method: 'POST' });
      if (!res.ok) {
        let detail = `Falha ao executar (HTTP ${res.status}).`;
        try {
          const body = await res.json();
          if (body?.detail && typeof body.detail === 'string') detail = body.detail;
        } catch { /* ignore */ }
        throw new Error(detail);
      }
      return res.json();
    },
    onSuccess: () => {
      toast({
        title: 'Execução iniciada',
        description:
          'Rodando em background. Acompanhe o histórico abaixo — o status atualiza em ~1min.',
      });
      // Refetch periódico curto para o status aparecer sem F5
      setTimeout(() => refetch(), 5000);
      setTimeout(() => refetch(), 15000);
      setTimeout(() => refetch(), 45000);
    },
    onError: (err: any) => {
      toast({ title: 'Erro ao executar', description: err.message, variant: 'destructive' });
    },
  });

  const toggleMutation = useMutation({
    mutationFn: async ({ id, enabled }: { id: number; enabled: boolean }) => {
      const res = await apiFetch(`/api/v1/automations/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_enabled: !enabled }),
      });
      if (!res.ok) throw new Error('Falha ao atualizar');
      return res.json();
    },
    onSuccess: (_data, vars) => {
      toast({
        title: vars.enabled ? 'Agendamento pausado' : 'Agendamento ativado',
        description: vars.enabled
          ? 'Execuções automáticas suspensas.'
          : 'Próxima execução automática agendada.',
      });
      refetch();
    },
    onError: (err: any) => {
      toast({ title: 'Erro', description: err.message, variant: 'destructive' });
    },
  });

  const formatDateTime = (isoString: string | null) => {
    if (!isoString) return '—';
    // Sempre renderiza em horário de Brasília (independente do fuso do browser).
    return new Date(isoString).toLocaleString('pt-BR', {
      timeZone: 'America/Sao_Paulo',
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const getOfficeNames = (officeIds?: number[] | null) => {
    if (!officeIds || !Array.isArray(officeIds) || officeIds.length === 0) {
      return '—';
    }
    return officeIds
      .map((id) => {
        const o = offices.find((o) => o.id === id);
        return (o?.path || o?.name) ?? `Escritório ${id}`;
      })
      .join(', ');
  };

  if (isLoading)
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin" />
      </div>
    );

  return (
    <div className="space-y-6">
      <style>{`
        @keyframes automation-progress {
          0%   { left: -35%; }
          100% { left: 100%; }
        }
      `}</style>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
            <Clock className="h-6 w-6" />
            Agendamentos Automáticos
          </h1>
          <p className="text-muted-foreground">
            Configure e gerencie suas automações de processamento.
          </p>
        </div>
        <Button size="sm" onClick={() => { resetForm(); setIsDialogOpen(true); }}>
          <Plus className="h-4 w-4 mr-2" />
          Novo Agendamento
        </Button>
      </div>

      {automations.length === 0 ? (
        <Alert>
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Nenhum agendamento</AlertTitle>
          <AlertDescription>
            Crie um novo agendamento para começar a automatizar suas tarefas.
          </AlertDescription>
        </Alert>
      ) : (
        <Card>
          <CardContent className="pt-6">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Nome</TableHead>
                  <TableHead>Cadência</TableHead>
                  <TableHead>Escritórios</TableHead>
                  <TableHead>Próxima Execução</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Ações</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {automations.map((auto) => (
                  <Fragment key={auto.id}>
                  <TableRow>
                    <TableCell className="font-medium">
                      <div className="flex items-center gap-2">
                        {auto.latest_run_status === 'running' && (
                          <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-600" />
                        )}
                        {auto.name}
                      </div>
                    </TableCell>
                    <TableCell>
                      <code className="text-xs bg-muted px-2 py-1 rounded">
                        {auto.cron_expression}
                      </code>
                    </TableCell>
                    <TableCell className="text-sm">{getOfficeNames(auto.office_ids || [])}</TableCell>
                    <TableCell className="text-sm">{formatDateTime(auto.next_run_at)}</TableCell>
                    <TableCell>
                      <Checkbox
                        checked={auto.is_enabled}
                        onCheckedChange={() =>
                          toggleMutation.mutate({ id: auto.id, enabled: auto.is_enabled })
                        }
                        disabled={toggleMutation.isPending}
                      />
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => runMutation.mutate(auto.id)}
                          disabled={runMutation.isPending}
                          title="Rodar agora"
                        >
                          <Play className="h-4 w-4" />
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => openEditDialog(auto)}
                          title="Editar"
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => {
                            setSelectedAutomation(auto);
                            setIsHistoryOpen(true);
                          }}
                          title="Ver histórico"
                        >
                          <History className="h-4 w-4" />
                        </Button>
                        <Button
                          size="sm"
                          variant="destructive"
                          onClick={() => {
                            if (confirm('Tem certeza?')) {
                              deleteMutation.mutate(auto.id);
                            }
                          }}
                          disabled={deleteMutation.isPending}
                          title="Deletar"
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                  {auto.latest_run_status === 'running' && (() => {
                    const cur = auto.latest_run_progress_current ?? null;
                    const tot = auto.latest_run_progress_total ?? null;
                    const hasDet = cur !== null && tot !== null && tot > 0;
                    const pct = hasDet ? Math.min(100, Math.max(0, Math.round(((cur as number) / (tot as number)) * 100))) : null;
                    const phase = auto.latest_run_progress_phase || 'starting';
                    const phaseLabel =
                      phase.startsWith('pull_publications') ? 'Buscando publicações' :
                      phase.startsWith('classify:collect') ? 'Coletando pendentes' :
                      phase.startsWith('classify:submit') ? 'Submetendo batch' :
                      phase.startsWith('classify:poll') ? 'Aguardando Anthropic' :
                      phase.startsWith('classify:apply') ? 'Aplicando resultados' :
                      phase.startsWith('treat_publications:start') ? 'Iniciando tratamento web' :
                      phase.startsWith('treat_publications:wait') ? 'Tratando no Legal One' :
                      phase.startsWith('treat_publications:done') ? 'Tratamento concluído' :
                      phase.startsWith('treat_publications') ? 'Tratando publicações' :
                      phase.startsWith('classify') ? 'Classificando' :
                      phase === 'done' ? 'Finalizando' :
                      'Iniciando';
                    return (
                      <TableRow className="bg-blue-50/50 hover:bg-blue-50/50">
                        <TableCell colSpan={6} className="py-2">
                          <div className="flex items-center gap-3">
                            <span className="text-xs font-medium text-blue-700 whitespace-nowrap">
                              {phaseLabel}
                              {hasDet && <span className="ml-1 text-blue-500">· {cur}/{tot}</span>}
                            </span>
                            <div className="relative flex-1 h-2 rounded-full bg-blue-100 overflow-hidden">
                              {hasDet ? (
                                <div
                                  className="absolute inset-y-0 left-0 rounded-full bg-blue-500 transition-all duration-500"
                                  style={{ width: `${pct}%` }}
                                />
                              ) : (
                                <div
                                  className="absolute inset-y-0 w-1/3 rounded-full bg-blue-500"
                                  style={{ animation: 'automation-progress 1.4s ease-in-out infinite' }}
                                />
                              )}
                            </div>
                            <span className="text-xs text-blue-700 tabular-nums whitespace-nowrap">
                              {hasDet ? `${pct}%` : ''}
                              {hasDet ? ' · ' : ''}
                              {formatElapsed(auto.latest_run_started_at)}
                            </span>
                          </div>
                          {auto.latest_run_progress_message && (
                            <div className="text-[11px] text-blue-600/80 mt-1 pl-0 truncate">
                              {auto.latest_run_progress_message}
                            </div>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })()}
                  </Fragment>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Create/Edit Dialog */}
      <Dialog open={isDialogOpen} onOpenChange={(o) => { setIsDialogOpen(o); if (!o) resetForm(); }}>
        <DialogContent className="max-w-2xl max-h-[90vh] flex flex-col">
          <DialogHeader>
            <DialogTitle>{editingId ? 'Editar Agendamento' : 'Novo Agendamento'}</DialogTitle>
            <DialogDescription>
              {editingId
                ? 'Altere os parâmetros deste agendamento.'
                : 'Configure um novo agendamento automático para suas tarefas.'}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 overflow-y-auto flex-1 pr-1">
            {/* Nome */}
            <div>
              <Label htmlFor="name">Nome</Label>
              <Input
                id="name"
                value={newAutomation.name}
                onChange={(e) =>
                  setNewAutomation({ ...newAutomation, name: e.target.value })
                }
                placeholder="Ex: Diário 07h — Puxar + Classificar"
              />
            </div>

            {/* Cadência */}
            <div>
              <Label>Cadência</Label>
              <div className="space-y-3">
                <div className="flex gap-2 flex-wrap">
                  {cronPresets.map((preset) => (
                    <Button
                      key={preset.value}
                      size="sm"
                      variant={
                        newAutomation.cron === preset.value ? 'default' : 'outline'
                      }
                      onClick={() =>
                        setNewAutomation({ ...newAutomation, cron: preset.value })
                      }
                    >
                      {preset.label}
                    </Button>
                  ))}
                </div>
                <div>
                  <Label htmlFor="cron" className="text-xs">
                    ou insira uma expressão Cron customizada
                  </Label>
                  <Input
                    id="cron"
                    value={newAutomation.cron}
                    onChange={(e) =>
                      setNewAutomation({ ...newAutomation, cron: e.target.value })
                    }
                    placeholder="Ex: 0 7 * * *"
                    className="font-mono text-xs"
                  />
                </div>
              </div>
            </div>

            {/* Escritórios */}
            <div>
              <Label>Escritórios</Label>
              <MultiSelect
                options={offices.map((o) => ({ label: o.path || o.name, value: String(o.id) }))}
                defaultValue={newAutomation.offices}
                onValueChange={(v) =>
                  setNewAutomation({ ...newAutomation, offices: v })
                }
                placeholder="Selecione escritórios..."
              />
            </div>

            {/* Steps */}
            <div>
              <Label>Passos</Label>
              <div className="space-y-2">
                <div className="flex items-center gap-3">
                  <Checkbox
                    id="pull"
                    checked={newAutomation.steps.includes('pull_publications')}
                    onCheckedChange={(c) => {
                      const steps = newAutomation.steps.filter(
                        (s) => s !== 'pull_publications'
                      );
                      if (c) steps.push('pull_publications');
                      setNewAutomation({ ...newAutomation, steps });
                    }}
                  />
                  <Label htmlFor="pull" className="font-normal cursor-pointer">
                    Puxar publicações
                  </Label>
                </div>
                <div className="flex items-center gap-3">
                  <Checkbox
                    id="classify"
                    checked={newAutomation.steps.includes('classify')}
                    onCheckedChange={(c) => {
                      const steps = newAutomation.steps.filter((s) => s !== 'classify');
                      if (c) steps.push('classify');
                      setNewAutomation({ ...newAutomation, steps });
                    }}
                  />
                  <Label htmlFor="classify" className="font-normal cursor-pointer">
                    Classificar
                  </Label>
                </div>
                <div className="flex items-center gap-3">
                  <Checkbox
                    id="treat-publications"
                    checked={newAutomation.steps.includes('treat_publications')}
                    onCheckedChange={(c) => {
                      const steps = newAutomation.steps.filter((s) => s !== 'treat_publications');
                      if (c) steps.push('treat_publications');
                      setNewAutomation({ ...newAutomation, steps });
                    }}
                  />
                  <Label htmlFor="treat-publications" className="font-normal cursor-pointer">
                    Tratar publicações no L1 Web
                  </Label>
                </div>
              </div>
            </div>

            {/* Janela de busca */}
            <div className="space-y-3 border-t pt-4">
              <div>
                <Label className="text-sm font-semibold">Janela de busca (Legal One)</Label>
                <p className="text-xs text-muted-foreground mt-1">
                  Controla quanto tempo para trás o sistema olha no Legal One (pelo campo
                  <code className="mx-1 px-1 bg-muted rounded">creationDate</code>, a data de
                  disponibilização).
                </p>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <Label htmlFor="initialLookback">
                    Primeira rodagem — dias para trás
                  </Label>
                  <Input
                    id="initialLookback"
                    type="number"
                    min={1}
                    max={90}
                    value={newAutomation.initialLookbackDays}
                    onChange={(e) =>
                      setNewAutomation({
                        ...newAutomation,
                        initialLookbackDays: Math.max(1, Number(e.target.value) || 1),
                      })
                    }
                  />
                  <p className="text-xs text-muted-foreground mt-1">
                    Usado só na 1ª execução de cada escritório (sem cursor).
                  </p>
                </div>
                <div>
                  <Label htmlFor="overlap">
                    Overlap — horas
                  </Label>
                  <Input
                    id="overlap"
                    type="number"
                    min={0}
                    max={72}
                    value={newAutomation.overlapHours}
                    onChange={(e) =>
                      setNewAutomation({
                        ...newAutomation,
                        overlapHours: Math.max(0, Number(e.target.value) || 0),
                      })
                    }
                  />
                  <p className="text-xs text-muted-foreground mt-1">
                    Margem aplicada nas execuções seguintes para não perder nada.
                  </p>
                </div>
              </div>
            </div>

            {/* Enabled */}
            <div className="flex items-center gap-3">
              <Checkbox
                id="enabled"
                checked={newAutomation.enabled}
                onCheckedChange={(c) =>
                  setNewAutomation({ ...newAutomation, enabled: !!c })
                }
              />
              <Label htmlFor="enabled" className="font-normal cursor-pointer">
                Ativar agora
              </Label>
            </div>
          </div>

          <DialogFooter>
            <Button variant="secondary" onClick={() => { setIsDialogOpen(false); resetForm(); }}>
              Cancelar
            </Button>
            <Button
              onClick={() => (editingId ? updateMutation.mutate() : createMutation.mutate())}
              disabled={createMutation.isPending || updateMutation.isPending}
            >
              {(createMutation.isPending || updateMutation.isPending) && (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              )}
              {editingId ? 'Salvar' : 'Criar'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* History Dialog */}
      <Dialog open={isHistoryOpen} onOpenChange={setIsHistoryOpen}>
        <DialogContent className="max-w-3xl max-h-[80vh]">
          <DialogHeader>
            <DialogTitle>Histórico — {selectedAutomation?.name}</DialogTitle>
          </DialogHeader>

          {runsLoading ? (
            <div className="flex h-32 items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin" />
            </div>
          ) : currentRuns.length === 0 ? (
            <p className="text-center text-muted-foreground py-8">
              Nenhuma execução ainda.
            </p>
          ) : (
            <ScrollArea className="h-[400px] w-full pr-4">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Data de Início</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Passos</TableHead>
                    <TableHead>Erro</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {currentRuns.map((run) => (
                    <TableRow key={run.id}>
                      <TableCell className="text-sm">
                        {formatDateTime(run.started_at)}
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={
                            run.status === 'success'
                              ? 'default'
                              : run.status === 'failed'
                                ? 'destructive'
                                : 'secondary'
                          }
                          className={
                            run.status === 'warning'
                              ? 'border-amber-300 bg-amber-100 text-amber-800 hover:bg-amber-100'
                              : undefined
                          }
                        >
                          {run.status === 'warning' ? 'Contingência DJEN' : run.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-sm">
                        {Array.isArray(run.steps_executed) && run.steps_executed.length > 0
                          ? run.steps_executed
                              .map((s: any) => {
                                if (typeof s === 'string') return s;
                                if (s && typeof s === 'object') {
                                  const parts = [s.step, s.status].filter(Boolean).join(':');
                                  const extra =
                                    s.records_found != null ? ` (${s.records_found} novos)` :
                                    s.records_classified != null ? ` (${s.records_classified} classif.)` :
                                    s.treated_count != null ? ` (${s.treated_count} tratadas, ${s.failed_count || 0} falhas)` : '';
                                  return parts + extra;
                                }
                                return String(s);
                              })
                              .join(' · ')
                          : '—'}
                      </TableCell>
                      <TableCell className="text-sm max-w-xs truncate">
                        {run.error_message || '—'}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </ScrollArea>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default AutomationsPage;
