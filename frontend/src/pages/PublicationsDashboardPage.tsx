import { Fragment, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  ArrowDown,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  CornerDownRight,
  FileBarChart2,
  FileText,
  Inbox,
  ListChecks,
  Loader2,
  TrendingUp,
  Users,
} from 'lucide-react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { useAuth } from '@/hooks/useAuth';
import { useToast } from '@/hooks/use-toast';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { apiFetch } from '@/lib/api-client';
import { downloadPublicationsPerformanceReport } from '@/services/api';

// ──────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────

interface SavedFilter {
  id: number;
  name: string;
  module: string;
  filters_json: string;
  is_default: boolean;
}

interface Automation {
  id: number;
  name: string;
  next_run_at: string | null;
  is_enabled: boolean;
}

interface OverviewKpis {
  pendentes_agora: number;
  tratadas_janela: number;
  agendadas_janela: number;
  recebidas_janela: number;
  taxa_erro_pct: number;
  window_days: number;
}

interface OverviewFunnel {
  novo: number;
  classificado: number;
  agendado: number;
  ignorado: number;
  erro: number;
}

interface OverviewSeries {
  date: string; // YYYY-MM-DD
  recebidas: number;
  tratadas: number;
}

interface OverviewPayload {
  kpis: OverviewKpis;
  funnel: OverviewFunnel;
  timeseries: OverviewSeries[];
  granularity?: 'day' | 'hour';
  generated_at: string;
}

interface RhythmPayload {
  backlog: number;
  oldest_pending_age_minutes: number | null;
  last_hour_treated: number;
  avg_per_hour_7d: number;
  vs_avg_pct: number;
  treated_today: number;
  arrivals_last_hour: number;
  net_rate_per_hour: number;
  burndown_label: string;
  avg_handling_minutes: number | null;
  generated_at: string;
}

interface PipelinePayload {
  funnel_today: {
    received: number;
    treated: number;
    scheduled: number;
  };
  next_out: {
    id: number;
    cnj: string | null;
    target_status: string;
    queued_at: string | null;
  }[];
  pending_total: number;
  generated_at: string;
}

type OperatorWindow = 'dia' | 'semana' | 'mes' | 'semestre' | 'total';

interface OperatorRow {
  user_id: number;
  user_name: string | null;
  user_email: string | null;
  dia: number;
  semana: number;
  mes: number;
  semestre: number;
  total: number;
  agendado_total: number;
  ignorado_total: number;
}

interface OperatorsPayload {
  mode: 'calendar' | 'rolling';
  operators: OperatorRow[];
  team_totals: {
    dia: number;
    semana: number;
    mes: number;
    semestre: number;
    total: number;
    agendado_total: number;
    ignorado_total: number;
  };
  generated_at: string;
}

// Breakdown de UM operador por escritório responsável (dropdown da linha)
interface OperatorOfficeRow {
  office_id: number | null;
  office_name: string;
  dia: number;
  semana: number;
  mes: number;
  semestre: number;
  total: number;
  agendado_total: number;
  ignorado_total: number;
}

interface OperatorOfficesPayload {
  mode: 'calendar' | 'rolling';
  user_id: number;
  offices: OperatorOfficeRow[];
  generated_at: string;
}

const OPERATOR_WINDOWS: OperatorWindow[] = ['dia', 'semana', 'mes', 'semestre', 'total'];

const OPERATOR_WINDOW_LABELS: Record<'calendar' | 'rolling', Record<OperatorWindow, string>> = {
  calendar: { dia: 'Hoje', semana: 'Esta semana', mes: 'Este mês', semestre: 'Semestre', total: 'Total' },
  rolling: { dia: '24h', semana: '7 dias', mes: '30 dias', semestre: '180 dias', total: 'Total' },
};

// ──────────────────────────────────────────────────────────────
// Paleta DUNATECH para gráficos (valores HSL das vars do design system)
// ──────────────────────────────────────────────────────────────

const BRAND = {
  navy: 'hsl(220, 74%, 14%)',
  blue: 'hsl(217, 100%, 56%)',
  blueSoft: 'hsl(215, 95%, 72%)',
  muted: 'hsl(220, 15%, 80%)',
  success: 'hsl(140, 70%, 45%)',
  warning: 'hsl(40, 90%, 50%)',
  error: 'hsl(0, 75%, 55%)',
};

const FUNNEL_COLORS: Record<keyof OverviewFunnel, string> = {
  novo: BRAND.blue,
  classificado: BRAND.blueSoft,
  agendado: BRAND.navy,
  ignorado: BRAND.muted,
  erro: BRAND.error,
};

const FUNNEL_LABELS: Record<keyof OverviewFunnel, string> = {
  novo: 'Novas',
  classificado: 'Classificadas',
  agendado: 'Agendadas',
  ignorado: 'Dado ciência',
  erro: 'Com erro',
};

// ──────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────

const formatDateTime = (isoString: string | null) => {
  if (!isoString) return 'N/A';
  return new Intl.DateTimeFormat('pt-BR', {
    dateStyle: 'short',
    timeStyle: 'short',
    timeZone: 'America/Sao_Paulo',
  }).format(new Date(isoString));
};

const formatShortDay = (ymd: string) => {
  // ymd = "2026-04-10" → "10/04"
  const parts = ymd.split('-');
  if (parts.length !== 3) return ymd;
  return `${parts[2]}/${parts[1]}`;
};

// Idade legível a partir de minutos: 45min · 3h20min · 2d4h
const formatAge = (minutes: number): string => {
  if (minutes < 60) return `${minutes}min`;
  if (minutes < 60 * 24) {
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    return m ? `${h}h${m}min` : `${h}h`;
  }
  const d = Math.floor(minutes / (60 * 24));
  const h = Math.floor((minutes % (60 * 24)) / 60);
  return h ? `${d}d${h}h` : `${d}d`;
};

// Hora legível (America/Sao_Paulo) a partir de um ISO datetime: "14h"
const formatHour = (iso: string): string => {
  const h = new Intl.DateTimeFormat('pt-BR', {
    hour: '2-digit',
    hour12: false,
    timeZone: 'America/Sao_Paulo',
  }).format(new Date(iso));
  return `${h}h`;
};

// Iniciais pro avatar do operador: "Maria Silva" -> "MS"
const initials = (name?: string | null, email?: string | null): string => {
  const src = (name || email || '?').trim();
  const parts = src.split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return src.slice(0, 2).toUpperCase();
};

// ──────────────────────────────────────────────────────────────
// Componentes menores
// ──────────────────────────────────────────────────────────────

// Linhas do breakdown por ESCRITÓRIO RESPONSÁVEL de um operador (expandidas
// na tabela do Bloco 4). Lazy: só busca quando a linha abre; cache por
// (operador, modo). Mesmas colunas da tabela-mãe pra alinhar os números.
const OperatorOfficeRows = ({
  userId,
  mode,
  sort,
}: {
  userId: number;
  mode: 'calendar' | 'rolling';
  sort: OperatorWindow;
}) => {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['dashboard-operator-offices', userId, mode],
    queryFn: async () => {
      const res = await apiFetch(
        `/api/v1/dashboard/publications-operator-offices?user_id=${userId}&mode=${mode}`,
      );
      if (!res.ok) throw new Error('Falha ao carregar escritórios do operador');
      return (await res.json()) as OperatorOfficesPayload;
    },
    staleTime: 60_000,
  });

  if (isLoading || isError) {
    return (
      <tr className="border-b bg-muted/30">
        <td colSpan={6} className="py-2 px-2 text-xs text-muted-foreground">
          {isError ? (
            'Não consegui carregar o detalhe por escritório.'
          ) : (
            <>
              <Loader2 className="mr-1.5 inline h-3.5 w-3.5 animate-spin" />
              Carregando escritórios…
            </>
          )}
        </td>
      </tr>
    );
  }

  const offices = [...(data?.offices ?? [])].sort((a, b) => b[sort] - a[sort] || b.total - a.total);
  if (offices.length === 0) {
    return (
      <tr className="border-b bg-muted/30">
        <td colSpan={6} className="py-2 px-2 text-xs text-muted-foreground">
          Sem detalhe por escritório pra esse operador.
        </td>
      </tr>
    );
  }

  return (
    <>
      {offices.map((of) => (
        <tr key={of.office_id ?? 'sem-escritorio'} className="border-b bg-muted/30 text-xs">
          <td className="py-1.5 px-2">
            <div className="flex items-center gap-1.5 pl-7 text-muted-foreground">
              <CornerDownRight className="h-3 w-3 shrink-0" />
              <span className="max-w-[320px] truncate" title={of.office_name}>
                {of.office_name}
              </span>
            </div>
          </td>
          {(['dia', 'semana', 'mes', 'semestre'] as OperatorWindow[]).map((w) => (
            <td
              key={w}
              className={`py-1.5 px-2 text-right tabular-nums ${
                sort === w ? 'font-semibold text-[hsl(var(--dunatech-navy))]' : 'text-muted-foreground'
              }`}
            >
              {of[w]}
            </td>
          ))}
          <td className="py-1.5 px-2 text-right">
            <span className="font-semibold tabular-nums text-[hsl(var(--dunatech-navy))]">{of.total}</span>
            <span className="ml-1.5 text-[10px] text-muted-foreground">
              {of.agendado_total} ag · {of.ignorado_total} ci
            </span>
          </td>
        </tr>
      ))}
    </>
  );
};

interface KpiCardProps {
  label: string;
  value: string | number;
  caption?: string;
  icon: React.ElementType;
  tone?: 'default' | 'warning' | 'error' | 'success';
  isLoading?: boolean;
  onClick?: () => void;
}

const KpiCard = ({ label, value, caption, icon: Icon, tone = 'default', isLoading, onClick }: KpiCardProps) => {
  const toneClass =
    tone === 'warning'
      ? 'text-amber-600 bg-amber-50'
      : tone === 'error'
      ? 'text-red-600 bg-red-50'
      : tone === 'success'
      ? 'text-emerald-600 bg-emerald-50'
      : 'text-[hsl(var(--dunatech-blue))] bg-[hsl(var(--dunatech-blue)/0.08)]';

  return (
    <Card
      className={`relative overflow-hidden transition-all ${
        onClick ? 'cursor-pointer hover:shadow-md hover:-translate-y-0.5' : ''
      }`}
      onClick={onClick}
    >
      <CardContent className="p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1">
            <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {label}
            </p>
            {isLoading ? (
              <Loader2 className="h-7 w-7 animate-spin mt-1 text-muted-foreground" />
            ) : (
              <p className="text-3xl font-bold leading-none tracking-tight text-[hsl(var(--dunatech-navy))]">
                {value}
              </p>
            )}
            {caption && <p className="text-xs text-muted-foreground pt-1">{caption}</p>}
          </div>
          <div className={`rounded-xl p-2.5 ${toneClass}`}>
            <Icon className="h-5 w-5" />
          </div>
        </div>
      </CardContent>
    </Card>
  );
};

// Passo do funil compacto (Bloco 3): rótulo + número + seta pra baixo.
const FunnelStep = ({
  label,
  value,
  onClick,
  last,
}: {
  label: string;
  value: number;
  onClick?: () => void;
  last?: boolean;
}) => (
  <div>
    <div
      className={`flex items-center justify-between rounded-lg border px-3 py-2 ${
        onClick ? 'cursor-pointer hover:bg-muted transition-colors' : ''
      }`}
      onClick={onClick}
    >
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-lg font-bold tabular-nums text-[hsl(var(--dunatech-navy))]">
        {value}
      </span>
    </div>
    {!last && (
      <div className="flex justify-center py-1 text-muted-foreground/60">
        <ArrowDown className="h-3.5 w-3.5" />
      </div>
    )}
  </div>
);

// ──────────────────────────────────────────────────────────────
// Página principal
// ──────────────────────────────────────────────────────────────

const PublicationsDashboardPage = () => {
  const navigate = useNavigate();
  const { toast } = useToast();
  const { canUsePublications, user, isAdmin } = useAuth();
  const [reportOpen, setReportOpen] = useState(false);

  // Granularidade do grafico de velocidade (Bloco 2): 'day' (N dias) ou 'hour' (24h)
  const [chartGranularity, setChartGranularity] = useState<'day' | 'hour'>('day');

  // Tratamento por operador (Bloco 4): modo de janela + coluna de ordenacao
  const [operatorMode, setOperatorMode] = useState<'calendar' | 'rolling'>('calendar');
  const [operatorSort, setOperatorSort] = useState<OperatorWindow>('dia');
  // Operador expandido (dropdown com o detalhe por escritório responsável)
  const [expandedOperator, setExpandedOperator] = useState<number | null>(null);

  // Overview (KPIs + funil + serie) — a serie respeita a granularidade do grafico.
  // KPIs e funil sao snapshot/janela e nao mudam com a granularidade.
  const { data: overview, isLoading: overviewLoading } = useQuery({
    queryKey: ['dashboard-overview', 14, chartGranularity],
    queryFn: async () => {
      const res = await apiFetch(
        `/api/v1/dashboard/publications-overview?days=14&granularity=${chartGranularity}`,
      );
      if (!res.ok) throw new Error('Falha ao carregar overview');
      return (await res.json()) as OverviewPayload;
    },
    enabled: canUsePublications,
    refetchInterval: 60_000,
  });

  // Pulso operacional (ritmo, backlog, projeção) — atualiza a cada 30s
  // pra dar sensação de "vivo" sem martelar o backend.
  const { data: rhythm, isLoading: rhythmLoading } = useQuery({
    queryKey: ['dashboard-rhythm'],
    queryFn: async () => {
      const res = await apiFetch('/api/v1/dashboard/publications-rhythm');
      if (!res.ok) throw new Error('Falha ao carregar pulso operacional');
      return (await res.json()) as RhythmPayload;
    },
    enabled: canUsePublications,
    refetchInterval: 30_000,
  });

  // Pipeline de hoje (funil + proximas saidas da fila de tratamento web) — Bloco 3
  const { data: pipeline } = useQuery({
    queryKey: ['dashboard-pipeline'],
    queryFn: async () => {
      const res = await apiFetch('/api/v1/dashboard/publications-pipeline');
      if (!res.ok) throw new Error('Falha ao carregar pipeline');
      return (await res.json()) as PipelinePayload;
    },
    enabled: canUsePublications,
    refetchInterval: 30_000,
  });

  // Tratamento por operador (Bloco 4) — recarrega ao trocar o modo de janela
  const { data: operatorsData, isLoading: operatorsLoading } = useQuery({
    queryKey: ['dashboard-operators', operatorMode],
    queryFn: async () => {
      const res = await apiFetch(
        `/api/v1/dashboard/publications-operators?mode=${operatorMode}`,
      );
      if (!res.ok) throw new Error('Falha ao carregar tratamento por operador');
      return (await res.json()) as OperatorsPayload;
    },
    enabled: canUsePublications,
    refetchInterval: 60_000,
  });

  const sortedOperators = useMemo(() => {
    const ops = operatorsData?.operators ?? [];
    return [...ops].sort((a, b) => b[operatorSort] - a[operatorSort]);
  }, [operatorsData, operatorSort]);

  const { data: savedFilters = [], isLoading: filtersLoading } = useQuery({
    queryKey: ['saved-filters', 'publications'],
    queryFn: async () => {
      const res = await apiFetch('/api/v1/me/saved-filters?module=publications');
      if (!res.ok) return [];
      return res.json() as Promise<SavedFilter[]>;
    },
    enabled: canUsePublications,
  });

  const { data: automations = [], isLoading: automationsLoading } = useQuery({
    queryKey: ['automations'],
    queryFn: async () => {
      const res = await apiFetch('/api/v1/automations');
      if (!res.ok) return [];
      const data = await res.json();
      return (Array.isArray(data) ? data : data.items || []) as Automation[];
    },
  });

  const nextAutomation = automations.find((a) => a.is_enabled && a.next_run_at)
    ? automations
        .filter((a) => a.is_enabled && a.next_run_at)
        .sort(
          (a, b) => new Date(a.next_run_at || '').getTime() - new Date(b.next_run_at || '').getTime()
        )[0]
    : null;

  const handleApplyFilter = (filter: SavedFilter) => {
    try {
      const filterState =
        typeof filter.filters_json === 'string' ? JSON.parse(filter.filters_json) : filter.filters_json;
      navigate('/publications', { state: { appliedFilter: filterState } });
    } catch {
      toast({
        title: 'Erro',
        description: 'Não foi possível aplicar o filtro.',
        variant: 'destructive',
      });
    }
  };

  const kpis = overview?.kpis;
  const funnel = overview?.funnel;
  const series = overview?.timeseries ?? [];
  const windowDays = kpis?.window_days ?? 14;

  const funnelData = funnel
    ? (Object.keys(funnel) as Array<keyof OverviewFunnel>)
        .filter((k) => (funnel[k] ?? 0) > 0)
        .map((k) => ({
          name: FUNNEL_LABELS[k],
          key: k,
          value: funnel[k],
          fill: FUNNEL_COLORS[k],
        }))
    : [];

  const chartSeries = series.map((s) => ({
    ...s,
    label: chartGranularity === 'hour' ? formatHour(s.date) : formatShortDay(s.date),
  }));

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
            <Activity className="h-6 w-6 text-[hsl(var(--dunatech-blue))]" />
            Dashboard de Publicações
          </h1>
          <p className="text-sm text-muted-foreground">
            Visão operacional das publicações — últimos {windowDays} dias.
          </p>
        </div>
        {isAdmin && (
          <Button
            variant="outline"
            size="sm"
            className="gap-2"
            onClick={() => setReportOpen(true)}
          >
            <FileBarChart2 className="h-4 w-4" />
            Relatório Crítico de Performance
          </Button>
        )}
      </div>

      {isAdmin && (
        <PerformanceReportDialog open={reportOpen} onOpenChange={setReportOpen} />
      )}

      {canUsePublications && (
        <>
          {/* Bloco 1 — Pulso operacional (ritmo, backlog, projeção) */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <KpiCard
              label="Pendentes agora"
              value={rhythm?.backlog ?? 0}
              caption={
                rhythm?.oldest_pending_age_minutes != null
                  ? `Mais antiga há ${formatAge(rhythm.oldest_pending_age_minutes)}`
                  : 'Backlog vazio'
              }
              icon={Inbox}
              tone={
                rhythm && rhythm.backlog > 150
                  ? 'error'
                  : rhythm && rhythm.backlog > 50
                  ? 'warning'
                  : 'default'
              }
              isLoading={rhythmLoading}
              onClick={() => navigate('/publications?status=novo')}
            />
            <KpiCard
              label="Ritmo (última hora)"
              value={`${rhythm?.last_hour_treated ?? 0}/h`}
              caption={
                rhythm && rhythm.avg_per_hour_7d > 0
                  ? `${rhythm.vs_avg_pct >= 0 ? '↑' : '↓'} ${Math.abs(
                      rhythm.vs_avg_pct,
                    )}% vs média 7d (${rhythm.avg_per_hour_7d}/h)`
                  : 'Sem histórico ainda'
              }
              icon={TrendingUp}
              tone={rhythm && rhythm.vs_avg_pct > 0 ? 'success' : 'default'}
              isLoading={rhythmLoading}
            />
            <KpiCard
              label="Projeção do backlog"
              value={
                !rhythm
                  ? '—'
                  : rhythm.backlog === 0
                  ? 'Zerado'
                  : rhythm.net_rate_per_hour > 0
                  ? 'Caindo'
                  : 'Subindo'
              }
              caption={rhythm?.burndown_label ?? ''}
              icon={Activity}
              tone={
                !rhythm
                  ? 'default'
                  : rhythm.backlog === 0 || rhythm.net_rate_per_hour > 0
                  ? 'success'
                  : 'error'
              }
              isLoading={rhythmLoading}
            />
            <KpiCard
              label="Tratadas hoje"
              value={rhythm?.treated_today ?? 0}
              caption={
                rhythm?.avg_handling_minutes != null
                  ? `Tempo médio de tratamento: ${formatAge(rhythm.avg_handling_minutes)}`
                  : 'Você + equipe'
              }
              icon={CheckCircle2}
              tone="success"
              isLoading={rhythmLoading}
            />
          </div>

          {/* Bloco 4 — Tratamento por operador (agendadas + ciências) */}
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <div>
                  <CardTitle className="text-base flex items-center gap-2">
                    <Users className="h-4 w-4 text-[hsl(var(--dunatech-blue))]" />
                    Tratamento por operador
                  </CardTitle>
                  <CardDescription className="text-xs">
                    Agendadas + ciências por pessoa. Clique numa coluna pra ordenar; clique no
                    operador pra detalhar por escritório responsável.
                  </CardDescription>
                </div>
                <div className="inline-flex rounded-lg border p-0.5 text-xs">
                  <button
                    type="button"
                    onClick={() => setOperatorMode('calendar')}
                    className={`px-2.5 py-1 rounded-md transition-colors ${
                      operatorMode === 'calendar'
                        ? 'bg-[hsl(var(--dunatech-blue))] text-white'
                        : 'text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    Calendário
                  </button>
                  <button
                    type="button"
                    onClick={() => setOperatorMode('rolling')}
                    className={`px-2.5 py-1 rounded-md transition-colors ${
                      operatorMode === 'rolling'
                        ? 'bg-[hsl(var(--dunatech-blue))] text-white'
                        : 'text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    Janela móvel
                  </button>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              {operatorsLoading ? (
                <div className="h-[120px] flex items-center justify-center">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : sortedOperators.length === 0 ? (
                <div className="h-[120px] flex items-center justify-center text-sm text-muted-foreground">
                  Nenhum tratamento registrado ainda.
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b text-xs text-muted-foreground">
                        <th className="text-left font-medium py-2 px-2">Operador</th>
                        {OPERATOR_WINDOWS.map((w) => (
                          <th
                            key={w}
                            onClick={() => setOperatorSort(w)}
                            className={`text-right font-medium py-2 px-2 cursor-pointer select-none hover:text-foreground ${
                              operatorSort === w ? 'text-[hsl(var(--dunatech-blue))]' : ''
                            }`}
                          >
                            <span className="inline-flex items-center gap-1">
                              {OPERATOR_WINDOW_LABELS[operatorMode][w]}
                              {operatorSort === w && <ArrowDown className="h-3 w-3" />}
                            </span>
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {sortedOperators.map((o) => {
                        const isMe = user?.id === o.user_id;
                        const name = isMe
                          ? 'Você'
                          : o.user_name || o.user_email || `Operador #${o.user_id}`;
                        const expanded = expandedOperator === o.user_id;
                        return (
                          <Fragment key={o.user_id}>
                            <tr
                              className={`border-b last:border-0 ${
                                isMe ? 'bg-[hsl(var(--dunatech-blue)/0.08)]' : ''
                              }`}
                            >
                              <td className="py-2 px-2">
                                <button
                                  type="button"
                                  onClick={() =>
                                    setExpandedOperator((cur) => (cur === o.user_id ? null : o.user_id))
                                  }
                                  className="flex w-full items-center gap-2 text-left"
                                  title="Detalhar por escritório responsável"
                                >
                                  {expanded ? (
                                    <ChevronDown className="h-3.5 w-3.5 shrink-0 text-[hsl(var(--dunatech-blue))]" />
                                  ) : (
                                    <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                                  )}
                                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[hsl(var(--dunatech-blue)/0.12)] text-[11px] font-semibold text-[hsl(var(--dunatech-blue))]">
                                    {initials(o.user_name, o.user_email)}
                                  </span>
                                  <span className="truncate">{name}</span>
                                </button>
                              </td>
                              {(['dia', 'semana', 'mes', 'semestre'] as OperatorWindow[]).map((w) => (
                                <td
                                  key={w}
                                  className={`text-right py-2 px-2 tabular-nums ${
                                    operatorSort === w
                                      ? 'font-semibold text-[hsl(var(--dunatech-navy))]'
                                      : ''
                                  }`}
                                >
                                  {o[w]}
                                </td>
                              ))}
                              <td className="text-right py-2 px-2">
                                <div className="font-semibold tabular-nums text-[hsl(var(--dunatech-navy))]">
                                  {o.total}
                                </div>
                                <div className="text-[11px] text-muted-foreground">
                                  {o.agendado_total} ag · {o.ignorado_total} ci
                                </div>
                              </td>
                            </tr>
                            {expanded && (
                              <OperatorOfficeRows
                                userId={o.user_id}
                                mode={operatorMode}
                                sort={operatorSort}
                              />
                            )}
                          </Fragment>
                        );
                      })}
                    </tbody>
                    {operatorsData?.team_totals && (
                      <tfoot>
                        <tr className="border-t">
                          <td className="py-2 px-2 text-muted-foreground">Equipe (todos)</td>
                          {(['dia', 'semana', 'mes', 'semestre', 'total'] as OperatorWindow[]).map(
                            (w) => (
                              <td
                                key={w}
                                className="text-right py-2 px-2 font-semibold tabular-nums"
                              >
                                {operatorsData.team_totals[w]}
                              </td>
                            ),
                          )}
                        </tr>
                      </tfoot>
                    )}
                  </table>
                  <p className="text-[11px] text-muted-foreground mt-2">
                    ag = agendadas · ci = ciências (ignoradas). O histórico de ciências
                    começa no deploy desta versão.
                  </p>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Linha de gráficos: Velocidade (2/3) + Funil (1/3) */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {/* Velocidade de tratamento */}
            <Card className="lg:col-span-2">
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <div>
                    <CardTitle className="text-base flex items-center gap-2">
                      <TrendingUp className="h-4 w-4 text-[hsl(var(--dunatech-blue))]" />
                      Velocidade de tratamento
                    </CardTitle>
                    <CardDescription className="text-xs">
                      Publicações recebidas vs. tratadas{' '}
                      {chartGranularity === 'hour' ? 'por hora (últimas 24h)' : 'por dia'}
                    </CardDescription>
                  </div>
                  <div className="inline-flex rounded-lg border p-0.5 text-xs">
                    <button
                      type="button"
                      onClick={() => setChartGranularity('hour')}
                      className={`px-2.5 py-1 rounded-md transition-colors ${
                        chartGranularity === 'hour'
                          ? 'bg-[hsl(var(--dunatech-blue))] text-white'
                          : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      Hoje (por hora)
                    </button>
                    <button
                      type="button"
                      onClick={() => setChartGranularity('day')}
                      className={`px-2.5 py-1 rounded-md transition-colors ${
                        chartGranularity === 'day'
                          ? 'bg-[hsl(var(--dunatech-blue))] text-white'
                          : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      {windowDays} dias
                    </button>
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                {overviewLoading ? (
                  <div className="h-[240px] flex items-center justify-center">
                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                  </div>
                ) : chartSeries.length === 0 ? (
                  <div className="h-[240px] flex items-center justify-center text-sm text-muted-foreground">
                    Sem dados no período.
                  </div>
                ) : (
                  <ResponsiveContainer width="100%" height={240}>
                    <AreaChart data={chartSeries} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                      <defs>
                        <linearGradient id="gRecebidas" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor={BRAND.blueSoft} stopOpacity={0.6} />
                          <stop offset="95%" stopColor={BRAND.blueSoft} stopOpacity={0} />
                        </linearGradient>
                        <linearGradient id="gTratadas" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor={BRAND.blue} stopOpacity={0.7} />
                          <stop offset="95%" stopColor={BRAND.blue} stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="hsl(220,15%,90%)" vertical={false} />
                      <XAxis
                        dataKey="label"
                        stroke="hsl(220,15%,45%)"
                        fontSize={11}
                        tickLine={false}
                        axisLine={false}
                      />
                      <YAxis
                        stroke="hsl(220,15%,45%)"
                        fontSize={11}
                        tickLine={false}
                        axisLine={false}
                        allowDecimals={false}
                      />
                      <RTooltip
                        contentStyle={{
                          borderRadius: 10,
                          border: '1px solid hsl(220,20%,85%)',
                          fontSize: 12,
                        }}
                        labelStyle={{ color: BRAND.navy, fontWeight: 600 }}
                      />
                      <Legend
                        iconType="circle"
                        wrapperStyle={{ fontSize: 12, paddingTop: 8 }}
                      />
                      <Area
                        type="monotone"
                        dataKey="recebidas"
                        name="Recebidas"
                        stroke={BRAND.blueSoft}
                        strokeWidth={2}
                        fill="url(#gRecebidas)"
                      />
                      <Area
                        type="monotone"
                        dataKey="tratadas"
                        name="Tratadas"
                        stroke={BRAND.blue}
                        strokeWidth={2}
                        fill="url(#gTratadas)"
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                )}
              </CardContent>
            </Card>

            {/* Funil de status */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base flex items-center gap-2">
                  <FileText className="h-4 w-4 text-[hsl(var(--dunatech-blue))]" />
                  Funil atual
                </CardTitle>
                <CardDescription className="text-xs">
                  Distribuição das publicações por status
                </CardDescription>
              </CardHeader>
              <CardContent>
                {overviewLoading ? (
                  <div className="h-[240px] flex items-center justify-center">
                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                  </div>
                ) : funnelData.length === 0 ? (
                  <div className="h-[240px] flex items-center justify-center text-sm text-muted-foreground">
                    Sem publicações.
                  </div>
                ) : (
                  <ResponsiveContainer width="100%" height={240}>
                    <PieChart>
                      <RTooltip
                        contentStyle={{
                          borderRadius: 10,
                          border: '1px solid hsl(220,20%,85%)',
                          fontSize: 12,
                        }}
                      />
                      <Pie
                        data={funnelData}
                        dataKey="value"
                        nameKey="name"
                        innerRadius={50}
                        outerRadius={80}
                        paddingAngle={2}
                        stroke="none"
                      >
                        {funnelData.map((entry) => (
                          <Cell key={entry.key} fill={entry.fill} />
                        ))}
                      </Pie>
                      <Legend
                        iconType="circle"
                        verticalAlign="bottom"
                        wrapperStyle={{ fontSize: 11, paddingTop: 8 }}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                )}
              </CardContent>
            </Card>
          </div>

          {/* Bloco 3 — Pipeline de hoje (funil + próximas saídas da fila) */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base flex items-center gap-2">
                <ListChecks className="h-4 w-4 text-[hsl(var(--dunatech-blue))]" />
                Pipeline de hoje
              </CardTitle>
              <CardDescription className="text-xs">
                Do recebimento ao agendamento, e o que está na fila de tratamento web.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Funil de hoje */}
              <div>
                <FunnelStep
                  label="Recebidas hoje"
                  value={pipeline?.funnel_today.received ?? 0}
                  onClick={() => navigate('/publications')}
                />
                <FunnelStep
                  label="Tratadas hoje"
                  value={pipeline?.funnel_today.treated ?? 0}
                />
                <FunnelStep
                  label="Agendadas no Legal One"
                  value={pipeline?.funnel_today.scheduled ?? 0}
                  onClick={() => navigate('/publications?status=agendado')}
                  last
                />
              </div>
              {/* Próximas saídas da fila */}
              <div className="flex flex-col">
                <h4 className="text-sm font-semibold mb-2">
                  Próximas saídas
                  {pipeline && pipeline.pending_total > 0 && (
                    <span className="ml-1 text-xs font-normal text-muted-foreground">
                      ({pipeline.pending_total} na fila)
                    </span>
                  )}
                </h4>
                {pipeline?.next_out?.length ? (
                  <ul className="space-y-1.5 text-xs flex-1">
                    {pipeline.next_out.map((item) => (
                      <li
                        key={item.id}
                        className="flex items-center justify-between gap-2"
                      >
                        <span className="font-mono truncate">
                          {item.cnj ?? 'sem CNJ'}
                        </span>
                        <Badge variant="outline" className="text-[10px] shrink-0">
                          {item.target_status}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs text-muted-foreground flex-1">
                    Nenhuma publicação na fila de tratamento.
                  </p>
                )}
                <Button
                  variant="link"
                  size="sm"
                  className="self-start px-0 mt-2"
                  onClick={() => navigate('/publications/treatment')}
                >
                  Ver fila completa →
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* Próxima rodagem + Quick Actions lado a lado */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <Card className="lg:col-span-1">
              <CardHeader className="pb-2">
                <CardTitle className="text-base flex items-center gap-2">
                  <Clock className="h-4 w-4 text-[hsl(var(--dunatech-blue))]" />
                  Próxima rodagem automática
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {automationsLoading ? (
                  <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                ) : nextAutomation ? (
                  <>
                    <div className="text-sm font-medium">{nextAutomation.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {formatDateTime(nextAutomation.next_run_at)}
                    </div>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => navigate('/automations')}
                      className="w-full"
                    >
                      Ver agendamentos
                      <ArrowRight className="h-4 w-4 ml-2" />
                    </Button>
                  </>
                ) : (
                  <p className="text-sm text-muted-foreground">Nenhum agendamento ativo.</p>
                )}
              </CardContent>
            </Card>

            <Card className="lg:col-span-2">
              <CardHeader className="pb-2">
                <CardTitle className="text-base">Ações rápidas</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-wrap gap-2">
                <Button size="sm" onClick={() => navigate('/publications?status=novo')}>
                  <Activity className="h-4 w-4 mr-2" />
                  Classificar pendentes
                </Button>
                <Button size="sm" variant="outline" onClick={() => navigate('/publications/templates')}>
                  <FileText className="h-4 w-4 mr-2" />
                  Templates
                </Button>
                <Button size="sm" variant="outline" onClick={() => navigate('/publications')}>
                  <ArrowRight className="h-4 w-4 mr-2" />
                  Ver publicações
                </Button>
                <Button size="sm" variant="outline" onClick={() => navigate('/publications/lookup')}>
                  <FileText className="h-4 w-4 mr-2" />
                  Consultar CNJ
                </Button>
              </CardContent>
            </Card>
          </div>

          {/* Meus Filtros Salvos */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Meus filtros salvos</CardTitle>
              <CardDescription className="text-xs">
                Acesse rapidamente seus filtros de publicações favoritos.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {filtersLoading ? (
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              ) : savedFilters.length > 0 ? (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                  {savedFilters.map((filter) => (
                    <div
                      key={filter.id}
                      className="p-3 border rounded-lg cursor-pointer hover:bg-muted transition-colors"
                      onClick={() => handleApplyFilter(filter)}
                    >
                      <div className="flex items-start justify-between">
                        <div className="flex-1">
                          <h3 className="font-medium text-sm">{filter.name}</h3>
                          {filter.is_default && (
                            <Badge variant="secondary" className="mt-1 text-[10px]">
                              Padrão
                            </Badge>
                          )}
                        </div>
                        <ArrowRight className="h-4 w-4 text-muted-foreground mt-0.5" />
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-6 text-sm text-muted-foreground">
                  <p>Nenhum filtro salvo ainda.</p>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => navigate('/publications')}
                    className="mt-3"
                  >
                    Ir para Publicações
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
};

export default PublicationsDashboardPage;

// ──────────────────────────────────────────────────────────────
// Relatório Crítico de Performance (admin-only) — modal com período
// (calendário) e período mínimo de 5 dias. Baixa o PDF gerado no servidor.
// ──────────────────────────────────────────────────────────────

interface PerformanceReportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function PerformanceReportDialog({ open, onOpenChange }: PerformanceReportDialogProps) {
  const { toast } = useToast();
  const today = new Date().toISOString().slice(0, 10);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState(today);
  const [loading, setLoading] = useState(false);

  const days = useMemo(() => {
    if (!dateFrom || !dateTo) return 0;
    const a = new Date(`${dateFrom}T00:00:00`).getTime();
    const b = new Date(`${dateTo}T00:00:00`).getTime();
    if (Number.isNaN(a) || Number.isNaN(b) || b < a) return 0;
    return Math.floor((b - a) / 86_400_000) + 1;
  }, [dateFrom, dateTo]);

  const valid = Boolean(dateFrom) && Boolean(dateTo) && days >= 5;

  const handleGenerate = async () => {
    if (!valid || loading) return;
    setLoading(true);
    try {
      await downloadPublicationsPerformanceReport(dateFrom, dateTo);
      toast({
        title: 'Relatório gerado',
        description: 'O download do PDF foi iniciado.',
      });
      onOpenChange(false);
    } catch (e) {
      toast({
        title: 'Falha ao gerar relatório',
        description: e instanceof Error ? e.message : 'Erro inesperado ao gerar o relatório.',
        variant: 'destructive',
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !loading && onOpenChange(v)}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FileBarChart2 className="h-5 w-5 text-[hsl(var(--dunatech-blue))]" />
            Relatório Crítico de Performance
          </DialogTitle>
          <DialogDescription>
            Compila as métricas de capacity da equipe de tratamento de publicações no período
            escolhido e gera um PDF executivo. Período mínimo de 5 dias.
          </DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-2 gap-3 py-1">
          <div className="space-y-1.5">
            <Label htmlFor="perf-from">Início</Label>
            <Input
              id="perf-from"
              type="date"
              value={dateFrom}
              max={dateTo || today}
              onChange={(e) => setDateFrom(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="perf-to">Fim</Label>
            <Input
              id="perf-to"
              type="date"
              value={dateTo}
              min={dateFrom || undefined}
              max={today}
              onChange={(e) => setDateTo(e.target.value)}
            />
          </div>
        </div>

        {dateFrom && dateTo && days > 0 && days < 5 && (
          <p className="text-xs text-destructive">
            O período mínimo é de 5 dias (selecionado: {days}).
          </p>
        )}
        {valid && (
          <p className="text-xs text-muted-foreground">{days} dias selecionados.</p>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={loading}>
            Cancelar
          </Button>
          <Button onClick={handleGenerate} disabled={!valid || loading} className="gap-2">
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <FileBarChart2 className="h-4 w-4" />
            )}
            {loading ? 'Gerando…' : 'Gerar PDF'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
