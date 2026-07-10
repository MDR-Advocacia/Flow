import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  Building2,
  CheckCircle2,
  CloudDownload,
  Download,
  FileSpreadsheet,
  Inbox,
  Layers,
  ListChecks,
  Loader2,
  type LucideIcon,
  RefreshCw,
  Settings,
  ShieldAlert,
  ShieldCheck,
  UserX,
  Users,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import {
  DashboardData,
  RunResumo,
  baixarPlanilhaArquivada,
  gerarPlanilhaNoHistorico,
  dispararColeta,
  getDashboard,
  getRun,
  rodarSeed,
} from "@/services/distribuidos-bb";

function fmtDataCurta(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

function Kpi({
  label,
  value,
  icon: Icon,
  tone,
  onClick,
}: {
  label: string;
  value: number;
  icon: LucideIcon;
  tone: string;
  onClick?: () => void;
}) {
  return (
    <Card
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      className={onClick ? "cursor-pointer transition-all hover:-translate-y-0.5 hover:shadow-md" : ""}
    >
      <CardContent className="flex items-center gap-3 p-4">
        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full ${tone}`}>
          <Icon className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <div className="text-2xl font-bold leading-none">{value}</div>
          <div className="truncate text-xs text-muted-foreground">{label}</div>
        </div>
      </CardContent>
    </Card>
  );
}

// O backend fala DD/MM/AAAA; o <input type="date"> nativo fala ISO (AAAA-MM-DD).
// Convertemos nos dois sentidos pra manter o payload/back intactos e ganhar o calendário.
function brParaIso(br: string): string {
  const m = br.trim().match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  return m ? `${m[3]}-${m[2]}-${m[1]}` : "";
}
function isoParaBr(iso: string): string {
  const m = iso.trim().match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : "";
}

export default function DistribuidosBBDashboardPage() {
  const navigate = useNavigate();
  const { toast } = useToast();
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(false);

  // Coleta
  const [coletaOpen, setColetaOpen] = useState(false);
  const [dataIni, setDataIni] = useState("");
  const [dataFim, setDataFim] = useState("");
  const [confirmarCiencia, setConfirmarCiencia] = useState(false);
  const [coletarEnvolvidos, setColetarEnvolvidos] = useState(true);
  const [disparando, setDisparando] = useState(false);
  const [runAtivo, setRunAtivo] = useState<RunResumo | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [gerandoPlanilha, setGerandoPlanilha] = useState(false);

  const gerarPlanilhaDoPool = useCallback(async () => {
    setGerandoPlanilha(true);
    try {
      const pl = await gerarPlanilhaNoHistorico();
      await baixarPlanilhaArquivada(pl.id, pl.nome_arquivo);
      toast({
        title: "Planilha gerada",
        description: `${pl.total_processos} processo(s) do pool exportado(s) e marcado(s) como "Planilha gerada".`,
      });
      setData(await getDashboard());
    } catch (e) {
      toast({ title: "Nada para gerar", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setGerandoPlanilha(false);
    }
  }, [toast]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await getDashboard());
    } catch (e) {
      toast({ title: "Erro ao carregar", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  // Auto-poll do progresso da coleta enquanto EM_ANDAMENTO
  useEffect(() => {
    if (!runAtivo || runAtivo.status !== "EM_ANDAMENTO") {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return;
    }
    pollRef.current = setInterval(async () => {
      try {
        const r = await getRun(runAtivo.id);
        setRunAtivo(r);
        if (r.status !== "EM_ANDAMENTO") {
          load();
          toast({
            title: r.status === "CONCLUIDO" ? "Coleta concluída" : "Coleta encerrada com erro",
            description: `${r.total_coletados} capturados · ${r.total_ciencia} com ciência · ${r.total_distribuidos} distribuídos · ${r.total_erros} erro(s).`,
            variant: r.status === "CONCLUIDO" ? undefined : "destructive",
          });
        }
      } catch {
        /* mantém tentando no próximo tick */
      }
    }, 3000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [runAtivo, load, toast]);

  const iniciarColeta = async () => {
    setDisparando(true);
    try {
      const resp = await dispararColeta({
        data_inicial: dataIni.trim() || undefined,
        data_final: dataFim.trim() || undefined,
        confirmar_ciencia: confirmarCiencia,
        coletar_envolvidos: coletarEnvolvidos,
      });
      setColetaOpen(false);
      if (resp.aviso_ciencia) {
        toast({ title: "Atenção", description: resp.aviso_ciencia });
      }
      setRunAtivo({
        id: resp.run_id,
        data_inicial: dataIni || null,
        data_final: dataFim || null,
        status: resp.status,
        confirmar_ciencia: confirmarCiencia,
        total_coletados: 0,
        total_ciencia: 0,
        total_distribuidos: 0,
        total_cadastrados: 0,
        total_erros: 0,
        iniciado_em: null,
        concluido_em: null,
      });
    } catch (e) {
      toast({ title: "Não foi possível iniciar a coleta", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setDisparando(false);
    }
  };

  const k = data?.kpis;

  const semConfig = (data?.por_escritorio?.length ?? 0) === 0 && (k?.total ?? 0) === 0;

  const criarConfigInicial = async () => {
    try {
      const res = await rodarSeed(false);
      if (res.criado) {
        toast({
          title: "Configuração inicial criada",
          description:
            res.nao_resolvidos && res.nao_resolvidos.length > 0
              ? `${res.nao_resolvidos.length} nome(s) não casaram com o Legal One — ajuste na Configuração.`
              : "Escritórios e filas do BB criados a partir dos padrões do robô.",
        });
      } else {
        toast({ title: "Já existe configuração", description: "Nada a criar." });
      }
      load();
    } catch (e) {
      toast({ title: "Erro no seed", description: String((e as Error).message), variant: "destructive" });
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
            <Building2 className="h-6 w-6 text-[hsl(var(--dunatech-blue))]" />
            Cadastro de Processo — Acompanhamento
          </h1>
          <p className="text-sm text-muted-foreground">
            Processos distribuídos do Banco do Brasil: coleta, ciência, distribuição e cadastro no Legal One.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => setColetaOpen(true)}>
            <CloudDownload className="mr-2 h-4 w-4" />
            Nova coleta
          </Button>
          <Button variant="outline" size="sm" onClick={() => navigate("/distribuidos-bb")}>
            <ListChecks className="mr-2 h-4 w-4" />
            Processos
          </Button>
          <Button variant="outline" size="sm" onClick={() => navigate("/distribuidos-bb/config")}>
            <Settings className="mr-2 h-4 w-4" />
            Configuração
          </Button>
          <Button variant="outline" size="icon" onClick={load} disabled={loading}>
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </Button>
        </div>
      </div>

      {semConfig && (
        <Card className="border-amber-300 bg-amber-50/60">
          <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-sm">
              <div className="font-semibold text-amber-800">Módulo ainda sem configuração</div>
              <div className="text-amber-700">
                Crie os escritórios e filas do BB a partir dos padrões do robô legado (você ajusta depois na tela de
                Configuração).
              </div>
            </div>
            <Button onClick={criarConfigInicial} className="shrink-0">
              Criar configuração inicial
            </Button>
          </CardContent>
        </Card>
      )}

      {runAtivo && (
        <Card className="border-sky-300 bg-sky-50/50">
          <CardContent className="flex flex-wrap items-center gap-4 p-4">
            {runAtivo.status === "EM_ANDAMENTO" ? (
              <Loader2 className="h-5 w-5 animate-spin text-sky-600" />
            ) : runAtivo.status === "CONCLUIDO" ? (
              <CheckCircle2 className="h-5 w-5 text-emerald-600" />
            ) : (
              <AlertTriangle className="h-5 w-5 text-rose-600" />
            )}
            <div className="text-sm">
              <span className="font-semibold">Coleta #{runAtivo.id}</span> ·{" "}
              {runAtivo.status === "EM_ANDAMENTO" ? "em andamento" : runAtivo.status.toLowerCase()}
            </div>
            <div className="flex flex-wrap gap-4 text-sm text-muted-foreground">
              <span>{runAtivo.total_coletados} capturados</span>
              <span>{runAtivo.total_ciencia} com ciência</span>
              <span>{runAtivo.total_distribuidos} distribuídos</span>
              {runAtivo.total_erros > 0 && <span className="text-rose-600">{runAtivo.total_erros} erro(s)</span>}
            </div>
            {runAtivo.status !== "EM_ANDAMENTO" && (
              <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setRunAtivo(null)}>
                Fechar
              </Button>
            )}
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Kpi label="Total capturados" value={k?.total ?? 0} icon={Inbox} tone="bg-blue-100 text-blue-700" onClick={() => navigate("/distribuidos-bb")} />
        <Kpi label="Aguardando ciência" value={k?.coletados ?? 0} icon={ShieldCheck} tone="bg-slate-100 text-slate-700" onClick={() => navigate("/distribuidos-bb?status=COLETADO")} />
        <Kpi label="Distribuídos" value={k?.distribuidos ?? 0} icon={Users} tone="bg-sky-100 text-sky-700" onClick={() => navigate("/distribuidos-bb?status=DISTRIBUIDO")} />
        <Kpi label="Cadastrados no L1" value={k?.cadastrados ?? 0} icon={CheckCircle2} tone="bg-emerald-100 text-emerald-700" onClick={() => navigate("/distribuidos-bb?status=CADASTRADO")} />
        <Kpi label="Sem responsável" value={k?.sem_responsavel ?? 0} icon={UserX} tone="bg-amber-100 text-amber-700" />
        <Kpi label="Erros / revisão" value={(k?.erros ?? 0) + (k?.revisao ?? 0)} icon={AlertTriangle} tone="bg-rose-100 text-rose-700" onClick={() => navigate("/distribuidos-bb?status=ERRO")} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Por escritório responsável</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {(data?.por_escritorio ?? []).length === 0 ? (
              <div className="py-6 text-center text-sm text-muted-foreground">Sem processos ainda.</div>
            ) : (
              data!.por_escritorio.map((row) => (
                <button
                  key={row.escritorio}
                  type="button"
                  onClick={() => navigate("/distribuidos-bb")}
                  className="flex items-center justify-between rounded-md border bg-card px-3 py-2 text-sm transition-colors hover:bg-muted/50"
                >
                  <span className="flex items-center gap-2">
                    <Building2 className="h-4 w-4 text-muted-foreground" />
                    {row.escritorio}
                  </span>
                  <span className="font-semibold">{row.total}</span>
                </button>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3 flex-row items-center justify-between space-y-0">
            <CardTitle className="text-base">Envolvidos com contato pendente</CardTitle>
            <Download className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">{k?.envolvidos_pendentes ?? 0}</div>
            <p className="mt-1 text-sm text-muted-foreground">
              Envolvidos capturados na capa do NPJ que ainda não foram casados por CPF/CNPJ no Legal One.
            </p>
            {data?.ultima_run && (
              <div className="mt-4 rounded-md border bg-muted/30 p-3 text-xs text-muted-foreground">
                Última coleta: {data.ultima_run.total_coletados} capturados ·{" "}
                {data.ultima_run.confirmar_ciencia ? "ciência confirmada" : "modo seguro (sem ciência)"} ·{" "}
                {data.ultima_run.status}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Pool + planilhas pendentes de subir no Legal One */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-3 flex-row items-center justify-between space-y-0">
            <CardTitle className="flex items-center gap-2 text-base">
              <Layers className="h-4 w-4 text-amber-600" />
              Pool aguardando planilha
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">{data?.planilhas?.pool_novos ?? 0}</div>
            <p className="mt-1 text-sm text-muted-foreground">
              Processos novos distribuídos que ainda não entraram em nenhuma planilha. Gere quando
              quiser — eles viram <strong>"Planilha gerada"</strong> e a próxima coleta traz os novos.
            </p>
            <Button
              className="mt-4"
              size="sm"
              onClick={gerarPlanilhaDoPool}
              disabled={gerandoPlanilha || (data?.planilhas?.pool_novos ?? 0) === 0}
            >
              {gerandoPlanilha ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <FileSpreadsheet className="mr-2 h-4 w-4" />
              )}
              Gerar planilha do pool
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3 flex-row items-center justify-between space-y-0">
            <CardTitle className="flex items-center gap-2 text-base">
              <CloudDownload className="h-4 w-4 text-[hsl(var(--dunatech-blue))]" />
              Planilhas pendentes de subir no Legal One
            </CardTitle>
            <Button variant="ghost" size="sm" onClick={() => navigate("/distribuidos-bb")}>
              Ver todas
            </Button>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">{data?.planilhas?.pendentes ?? 0}</div>
            <p className="mt-1 text-sm text-muted-foreground">
              Planilhas já geradas que o operador ainda não marcou como subidas no Legal One.
            </p>
            <div className="mt-3 flex flex-col gap-2">
              {(data?.planilhas?.recentes_pendentes ?? []).length === 0 ? (
                <div className="rounded-md border bg-muted/30 p-3 text-xs text-muted-foreground">
                  Nenhuma planilha pendente de envio.
                </div>
              ) : (
                data!.planilhas!.recentes_pendentes.map((pl) => (
                  <div
                    key={pl.id}
                    className="flex items-center justify-between gap-2 rounded-md border bg-card px-3 py-2 text-sm"
                  >
                    <div className="min-w-0">
                      <div className="truncate font-mono text-xs">{pl.nome_arquivo}</div>
                      <div className="text-xs text-muted-foreground">
                        {pl.total_processos} processo(s) · {fmtDataCurta(pl.created_at)}
                      </div>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      className="shrink-0"
                      onClick={() => baixarPlanilhaArquivada(pl.id, pl.nome_arquivo)}
                    >
                      <Download className="mr-1.5 h-3.5 w-3.5" /> Baixar
                    </Button>
                  </div>
                ))
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Dialog de nova coleta */}
      <Dialog open={coletaOpen} onOpenChange={setColetaOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <CloudDownload className="h-5 w-5 text-[hsl(var(--dunatech-blue))]" />
              Nova coleta no portal BB
            </DialogTitle>
            <DialogDescription>
              O robô entra no portal (via OneLog), lê as notificações do intervalo e as registra. Datas em branco = hoje.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label className="text-xs">Data inicial</Label>
                <Input
                  type="date"
                  value={brParaIso(dataIni)}
                  onChange={(e) => setDataIni(isoParaBr(e.target.value))}
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Data final</Label>
                <Input
                  type="date"
                  value={brParaIso(dataFim)}
                  onChange={(e) => setDataFim(isoParaBr(e.target.value))}
                />
              </div>
            </div>
            <p className="-mt-1 text-xs text-muted-foreground">
              Clique no campo pra abrir o calendário. Deixe em branco = hoje.
            </p>

            <div className="flex items-start gap-2 rounded-md border p-3">
              <Checkbox
                id="coletar-envolvidos"
                checked={coletarEnvolvidos}
                onCheckedChange={(v) => setColetarEnvolvidos(v === true)}
                className="mt-0.5"
              />
              <div className="text-sm">
                <Label htmlFor="coletar-envolvidos" className="font-medium">
                  Capturar envolvidos (Pessoas do Processo)
                </Label>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Abre a capa do NPJ de cada processo e traz as partes com CPF/CNPJ, MCI e relação com o BB. Deixa a
                  coleta mais lenta.
                </p>
              </div>
            </div>

            <div className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50/60 p-3">
              <Checkbox
                id="confirmar-ciencia"
                checked={confirmarCiencia}
                onCheckedChange={(v) => setConfirmarCiencia(v === true)}
                className="mt-0.5"
              />
              <div className="text-sm">
                <Label htmlFor="confirmar-ciencia" className="flex items-center gap-1.5 font-medium text-amber-800">
                  <ShieldAlert className="h-4 w-4" />
                  Dar ciência (clicar SIM no BB)
                </Label>
                <p className="mt-0.5 text-xs text-amber-700">
                  Ação <strong>irreversível</strong> que inicia prazos. Só acontece se a trava global de segurança também
                  estiver ligada no servidor; caso contrário, roda em modo seguro (apenas coleta).
                </p>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setColetaOpen(false)}>
              Cancelar
            </Button>
            <Button onClick={iniciarColeta} disabled={disparando}>
              {disparando && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Iniciar coleta
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
