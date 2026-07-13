import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Building2,
  CheckCircle2,
  Download,
  ExternalLink,
  FileSpreadsheet,
  FileText,
  History,
  Loader2,
  RefreshCw,
  ScrollText,
  Search,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import {
  Auditoria,
  Evento,
  PlanilhaDetalhe,
  PlanilhaHist,
  Processo,
  baixarPlanilhaArquivada,
  cadastrarPlanilhaL1,
  gerarPlanilhaNoHistorico,
  getAuditoria,
  getPlanilhaDetalhe,
  listarEventos,
  listarPlanilhas,
  listarProcessos,
  marcarPlanilhaSubida,
  verificarCadastroAgora,
} from "@/services/distribuidos-bb";

const PAGE_SIZES = [25, 50, 100];
const PLANILHAS_PAGE_SIZE = 25;

function fmtBytes(n: number): string {
  if (!n) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

const ORIGEM_META: Record<string, { label: string; cls: string }> = {
  AUTOMATICA: { label: "Automática", cls: "bg-sky-100 text-sky-700" },
  MANUAL: { label: "Manual", cls: "bg-slate-100 text-slate-700" },
};

const POOL_META: Record<string, { label: string; cls: string }> = {
  NOVO: { label: "Novo", cls: "bg-amber-100 text-amber-700" },
  PENDENTE_CADASTRO: { label: "Pendente cadastro", cls: "bg-sky-100 text-sky-700" },
  CADASTRADO_L1: { label: "Cadastro confirmado no L1", cls: "bg-emerald-100 text-emerald-700" },
};

const STATUS_META: Record<string, { label: string; cls: string }> = {
  COLETADO: { label: "Aguardando ciência", cls: "bg-slate-100 text-slate-700" },
  CIENCIA_DADA: { label: "Ciência dada", cls: "bg-indigo-100 text-indigo-700" },
  DISTRIBUIDO: { label: "Distribuído", cls: "bg-sky-100 text-sky-700" },
  CONTATOS_RESOLVIDOS: { label: "Contatos resolvidos", cls: "bg-teal-100 text-teal-700" },
  CADASTRADO: { label: "Cadastrado no L1", cls: "bg-emerald-100 text-emerald-700" },
  ERRO: { label: "Erro", cls: "bg-rose-100 text-rose-700" },
  REVISAO: { label: "Revisão", cls: "bg-amber-100 text-amber-700" },
};

const NIVEL_META: Record<string, string> = {
  INFO: "bg-slate-100 text-slate-700",
  SUCESSO: "bg-emerald-100 text-emerald-700",
  AVISO: "bg-amber-100 text-amber-700",
  ERRO: "bg-rose-100 text-rose-700",
};

const POOL_FILTROS = [
  { value: "", label: "Todo o pool" },
  { value: "PENDENTE_CADASTRO", label: "Pendente cadastro" },
  { value: "NOVO", label: "Novo" },
  { value: "CADASTRADO_L1", label: "Cadastrado no L1" },
];

const STATUS_FILTROS = [
  { value: "", label: "Todos os status" },
  { value: "COLETADO", label: "Aguardando ciência" },
  { value: "DISTRIBUIDO", label: "Distribuído" },
  { value: "CADASTRADO", label: "Cadastrado no L1" },
  { value: "ERRO", label: "Erro" },
  { value: "REVISAO", label: "Revisão" },
];

function StatusBadge({ status }: { status: string }) {
  const meta = STATUS_META[status] ?? { label: status, cls: "bg-slate-100 text-slate-700" };
  return <Badge className={`${meta.cls} hover:${meta.cls}`} variant="secondary">{meta.label}</Badge>;
}

function fmtData(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

function fmtValor(v: number | null): string {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

export default function DistribuidosBBPage() {
  const navigate = useNavigate();
  const { toast } = useToast();
  const [searchParams] = useSearchParams();

  const [aba, setAba] = useState<"processos" | "log" | "planilhas">("processos");
  const [baixando, setBaixando] = useState(false);

  const gerarPlanilha = async () => {
    setBaixando(true);
    try {
      const pl = await gerarPlanilhaNoHistorico();
      await baixarPlanilhaArquivada(pl.id, pl.nome_arquivo);
      toast({
        title: "Planilha gerada",
        description: `${pl.total_processos} processo(s) do pool exportado(s) e marcado(s) como "Planilha gerada". Suba no Legal One.`,
      });
      setAba("planilhas");
      loadPlanilhas();
      if (aba === "processos") loadProcessos();
    } catch (e) {
      toast({ title: "Nada para gerar", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setBaixando(false);
    }
  };

  // Processos
  const [items, setItems] = useState<Processo[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [statusFiltro, setStatusFiltro] = useState<string>(searchParams.get("status") ?? "");
  const [poolFiltro, setPoolFiltro] = useState<string>("");
  const [cadastroDe, setCadastroDe] = useState<string>("");
  const [cadastroAte, setCadastroAte] = useState<string>("");
  const [buscaInput, setBuscaInput] = useState("");
  const [busca, setBusca] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  // Log
  const [eventos, setEventos] = useState<Evento[]>([]);
  const [eventosTotal, setEventosTotal] = useState(0);
  const [secaoFiltro, setSecaoFiltro] = useState<string>("");
  const [nivelFiltro, setNivelFiltro] = useState<string>("");
  const [logPage, setLogPage] = useState(1);
  const [logBuscaInput, setLogBuscaInput] = useState("");
  const [logBusca, setLogBusca] = useState("");

  // Planilhas
  const [planilhas, setPlanilhas] = useState<PlanilhaHist[]>([]);
  const [planilhasTotal, setPlanilhasTotal] = useState(0);
  const [planilhasPendentes, setPlanilhasPendentes] = useState(0);
  const [planilhasPage, setPlanilhasPage] = useState(1);
  const [planilhasLoading, setPlanilhasLoading] = useState(false);
  const [soPendentes, setSoPendentes] = useState(false);

  // Detalhe da planilha (tela de visualização)
  const [detalhe, setDetalhe] = useState<PlanilhaDetalhe | null>(null);
  const [detalheOpen, setDetalheOpen] = useState(false);
  const [detalheLoading, setDetalheLoading] = useState(false);
  const [verificando, setVerificando] = useState(false);
  const [cadastrandoL1, setCadastrandoL1] = useState(false);

  const abrirDetalhe = async (id: number) => {
    setDetalheOpen(true);
    setDetalheLoading(true);
    setDetalhe(null);
    try {
      setDetalhe(await getPlanilhaDetalhe(id));
    } catch (e) {
      toast({ title: "Erro ao abrir planilha", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setDetalheLoading(false);
    }
  };

  const verificarCadastro = async () => {
    setVerificando(true);
    try {
      const r = await verificarCadastroAgora();
      toast({
        title: "Monitor de cadastro executado",
        description: `${r.verificados} verificado(s) no L1 · ${r.confirmados} confirmado(s) agora.`,
      });
      if (detalhe) setDetalhe(await getPlanilhaDetalhe(detalhe.planilha.id));
      loadPlanilhas();
    } catch (e) {
      toast({ title: "Erro ao verificar", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setVerificando(false);
    }
  };

  const cadastrarNoL1 = async (dryRun: boolean) => {
    if (!detalhe) return;
    if (!dryRun) {
      const ok = window.confirm(
        `CADASTRO REAL no Legal One: vai criar as pastas dos ${detalhe.progresso.total} processo(s) desta planilha e disparar o workflow. Ação irreversível. Confirmar?`,
      );
      if (!ok) return;
    }
    setCadastrandoL1(true);
    try {
      const rel = await cadastrarPlanilhaL1(detalhe.planilha.id, dryRun);
      const st = rel.status_import;
      toast({
        title: dryRun ? "Simulação (dry-run) concluída" : "Import enviado ao Legal One",
        description: dryRun
          ? `${st?.revisingLitigationsCount ?? 0} em revisão · ${st?.importingLitigationsErrorsCount ?? 0} erro(s). Nada foi cadastrado (dry-run).`
          : `${rel.resultado}. O monitor confirma os cadastros de 2 em 2 min.`,
      });
      setDetalhe(await getPlanilhaDetalhe(detalhe.planilha.id));
      loadPlanilhas();
    } catch (e) {
      toast({ title: "Erro no import", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setCadastrandoL1(false);
    }
  };

  // Auditoria
  const [auditoria, setAuditoria] = useState<Auditoria | null>(null);
  const [auditLoading, setAuditLoading] = useState(false);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const firstRow = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const lastRow = Math.min(total, page * pageSize);

  const loadProcessos = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await listarProcessos({
        status: statusFiltro || undefined,
        planilhaStatus: poolFiltro || undefined,
        cadastroDe: cadastroDe || undefined,
        cadastroAte: cadastroAte || undefined,
        busca: busca || undefined,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      });
      setItems(resp.items);
      setTotal(resp.total);
    } catch (e) {
      toast({ title: "Erro ao carregar processos", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [statusFiltro, poolFiltro, cadastroDe, cadastroAte, busca, page, pageSize, toast]);

  const loadEventos = useCallback(async () => {
    try {
      const resp = await listarEventos({
        secao: secaoFiltro || undefined,
        nivel: nivelFiltro || undefined,
        busca: logBusca || undefined,
        limit: 100,
        offset: (logPage - 1) * 100,
      });
      setEventos(resp.items);
      setEventosTotal(resp.total);
    } catch (e) {
      toast({ title: "Erro ao carregar log", description: String((e as Error).message), variant: "destructive" });
    }
  }, [secaoFiltro, nivelFiltro, logBusca, logPage, toast]);

  const loadPlanilhas = useCallback(async () => {
    setPlanilhasLoading(true);
    try {
      const resp = await listarPlanilhas({
        apenasPendentes: soPendentes,
        limit: PLANILHAS_PAGE_SIZE,
        offset: (planilhasPage - 1) * PLANILHAS_PAGE_SIZE,
      });
      setPlanilhas(resp.items);
      setPlanilhasTotal(resp.total);
      setPlanilhasPendentes(resp.pendentes);
    } catch (e) {
      toast({ title: "Erro ao carregar planilhas", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setPlanilhasLoading(false);
    }
  }, [soPendentes, planilhasPage, toast]);

  const alternarSubido = async (pl: PlanilhaHist, valor: boolean) => {
    try {
      const atualizada = await marcarPlanilhaSubida(pl.id, valor);
      setPlanilhas((prev) => prev.map((x) => (x.id === pl.id ? atualizada : x)));
      setPlanilhasPendentes((n) => Math.max(0, n + (valor ? -1 : 1)));
    } catch (e) {
      toast({ title: "Erro ao marcar planilha", description: String((e as Error).message), variant: "destructive" });
    }
  };

  const baixarDoHistorico = async (pl: PlanilhaHist) => {
    try {
      await baixarPlanilhaArquivada(pl.id, pl.nome_arquivo);
    } catch (e) {
      toast({ title: "Erro ao baixar", description: String((e as Error).message), variant: "destructive" });
    }
  };

  const cadastrarDaLista = async (pl: PlanilhaHist) => {
    const ok = window.confirm(
      `Cadastrar no Legal One os processos NOVOS da planilha "${pl.nome_arquivo}"? Cria as pastas e dispara o workflow (ação irreversível). Os que já existem no L1 são ignorados.`,
    );
    if (!ok) return;
    setCadastrandoL1(true);
    try {
      const rel = await cadastrarPlanilhaL1(pl.id, false);
      toast({
        title: "Import enviado ao Legal One",
        description: `${rel.resultado} O monitor confirma os cadastros de 2 em 2 min.`,
      });
      loadPlanilhas();
    } catch (e) {
      toast({ title: "Erro no import", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setCadastrandoL1(false);
    }
  };

  useEffect(() => {
    if (aba === "processos") loadProcessos();
  }, [aba, loadProcessos]);
  useEffect(() => {
    if (aba === "log") loadEventos();
  }, [aba, loadEventos]);
  useEffect(() => {
    if (aba === "planilhas") loadPlanilhas();
  }, [aba, loadPlanilhas]);

  const abrirAuditoria = async (proc: Processo) => {
    setAuditLoading(true);
    setAuditoria({ processo: proc, envolvidos: [], eventos: [] });
    try {
      setAuditoria(await getAuditoria(proc.id));
    } catch (e) {
      toast({ title: "Erro na auditoria", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setAuditLoading(false);
    }
  };

  const logTotalPages = Math.max(1, Math.ceil(eventosTotal / 100));
  const planilhasTotalPages = Math.max(1, Math.ceil(planilhasTotal / PLANILHAS_PAGE_SIZE));

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <Building2 className="h-6 w-6 shrink-0 text-primary" />
            Cadastro de Processo
          </h1>
          <p className="text-sm text-muted-foreground">Processos capturados no portal do Banco do Brasil e sua auditoria.</p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={gerarPlanilha} disabled={baixando}>
            {baixando ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <FileSpreadsheet className="mr-2 h-4 w-4" />}
            Gerar planilha
          </Button>
          <Button variant="outline" size="sm" onClick={() => navigate("/distribuidos-bb/dashboard")}>
            Ver dashboard
          </Button>
        </div>
      </div>

      <Tabs value={aba} onValueChange={(v) => setAba(v as "processos" | "log" | "planilhas")}>
        <TabsList>
          <TabsTrigger value="processos">
            <FileText className="mr-1.5 h-4 w-4" /> Processos
          </TabsTrigger>
          <TabsTrigger value="planilhas">
            <History className="mr-1.5 h-4 w-4" /> Planilhas
            {planilhasPendentes > 0 && (
              <Badge className="ml-1.5 bg-amber-100 text-amber-700 hover:bg-amber-100" variant="secondary">
                {planilhasPendentes}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="log">
            <ScrollText className="mr-1.5 h-4 w-4" /> Log de tudo
          </TabsTrigger>
        </TabsList>
      </Tabs>

      {aba === "processos" && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Select
              value={statusFiltro || "__all__"}
              onValueChange={(v) => {
                setPage(1);
                setStatusFiltro(v === "__all__" ? "" : v);
              }}
            >
              <SelectTrigger className="w-52">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {STATUS_FILTROS.map((s) => (
                  <SelectItem key={s.value || "__all__"} value={s.value || "__all__"}>
                    {s.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={poolFiltro || "__all__"}
              onValueChange={(v) => {
                setPage(1);
                setPoolFiltro(v === "__all__" ? "" : v);
              }}
            >
              <SelectTrigger className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {POOL_FILTROS.map((s) => (
                  <SelectItem key={s.value || "__all__"} value={s.value || "__all__"}>
                    {s.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-muted-foreground">Cadastro de</span>
              <Input
                type="date"
                className="w-[150px]"
                value={cadastroDe}
                onChange={(e) => {
                  setPage(1);
                  setCadastroDe(e.target.value);
                }}
              />
              <span className="text-xs text-muted-foreground">até</span>
              <Input
                type="date"
                className="w-[150px]"
                value={cadastroAte}
                onChange={(e) => {
                  setPage(1);
                  setCadastroAte(e.target.value);
                }}
              />
              {(cadastroDe || cadastroAte) && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setPage(1);
                    setCadastroDe("");
                    setCadastroAte("");
                  }}
                >
                  Limpar
                </Button>
              )}
            </div>
            <div className="relative w-full lg:w-80">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                className="w-full pl-8"
                placeholder="CNJ, NPJ ou adverso"
                value={buscaInput}
                onChange={(e) => setBuscaInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    setPage(1);
                    setBusca(buscaInput.trim());
                  }
                }}
              />
            </div>
          </div>

          <Card>
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Status</TableHead>
                      <TableHead>Pool</TableHead>
                      <TableHead>CNJ / NPJ</TableHead>
                      <TableHead>Posição</TableHead>
                      <TableHead>Natureza</TableHead>
                      <TableHead className="min-w-[180px]">Adverso principal</TableHead>
                      <TableHead>Responsável</TableHead>
                      <TableHead>Observação</TableHead>
                      <TableHead className="text-right">Valor</TableHead>
                      <TableHead className="text-right">Auditoria</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {loading && items.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={10} className="py-10 text-center">
                          <Loader2 className="mx-auto h-6 w-6 animate-spin text-muted-foreground" />
                        </TableCell>
                      </TableRow>
                    ) : items.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={10} className="py-10 text-center text-muted-foreground">
                          Nenhum processo encontrado.
                        </TableCell>
                      </TableRow>
                    ) : (
                      items.map((p, idx) => (
                        <TableRow key={p.id} className={idx % 2 === 1 ? "bg-muted/20" : undefined}>
                          <TableCell>
                            <StatusBadge status={p.status} />
                          </TableCell>
                          <TableCell>
                            {(() => {
                              const pm = POOL_META[p.planilha_status] ?? { label: p.planilha_status, cls: "bg-slate-100 text-slate-700" };
                              return <Badge className={`${pm.cls} hover:${pm.cls}`} variant="secondary">{pm.label}</Badge>;
                            })()}
                          </TableCell>
                          <TableCell className="font-mono text-xs">
                            <div>{p.cnj ?? <span className="text-muted-foreground">sem CNJ</span>}</div>
                            <div className="text-muted-foreground">{p.npj ?? "—"}</div>
                          </TableCell>
                          <TableCell>{p.posicao ?? "—"}</TableCell>
                          <TableCell>{p.natureza ?? "—"}</TableCell>
                          <TableCell className="max-w-[240px] truncate">{p.adverso_principal ?? "—"}</TableCell>
                          <TableCell>{p.responsavel_nome ?? <span className="text-amber-600">sem responsável</span>}</TableCell>
                          <TableCell>{p.observacao ?? "—"}</TableCell>
                          <TableCell className="text-right">{fmtValor(p.valor_causa)}</TableCell>
                          <TableCell className="text-right">
                            <Button size="sm" variant="outline" onClick={() => abrirAuditoria(p)}>
                              Ver
                            </Button>
                          </TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-3 border-t p-3 text-sm">
                <div className="flex items-center gap-2">
                  <span className="text-muted-foreground">Por página:</span>
                  <Select
                    value={String(pageSize)}
                    onValueChange={(v) => {
                      setPage(1);
                      setPageSize(Number(v));
                    }}
                  >
                    <SelectTrigger className="w-20">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {PAGE_SIZES.map((s) => (
                        <SelectItem key={s} value={String(s)}>
                          {s}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="text-muted-foreground">
                  {firstRow}–{lastRow} de {total} · Página {page} de {totalPages}
                </div>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" disabled={page <= 1 || loading} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                    Anterior
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page >= totalPages || loading}
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  >
                    Próxima
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </>
      )}

      {aba === "planilhas" && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Checkbox
                id="so-pendentes"
                checked={soPendentes}
                onCheckedChange={(v) => {
                  setPlanilhasPage(1);
                  setSoPendentes(v === true);
                }}
              />
              <label htmlFor="so-pendentes" className="text-sm">
                Só as que ainda não subi no Legal One
              </label>
            </div>
            <div className="text-sm text-muted-foreground">
              {planilhasPendentes} pendente(s) de envio ao Legal One
            </div>
          </div>

          <Card>
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-44">Gerada em</TableHead>
                      <TableHead>Arquivo</TableHead>
                      <TableHead className="w-28">Origem</TableHead>
                      <TableHead className="w-24 text-right">Processos</TableHead>
                      <TableHead className="w-24 text-right">Tamanho</TableHead>
                      <TableHead className="min-w-[200px]">Subido no Legal One</TableHead>
                      <TableHead className="min-w-[240px] text-right">Ações</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {planilhasLoading && planilhas.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={7} className="py-10 text-center">
                          <Loader2 className="mx-auto h-6 w-6 animate-spin text-muted-foreground" />
                        </TableCell>
                      </TableRow>
                    ) : planilhas.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={7} className="py-10 text-center text-muted-foreground">
                          Nenhuma planilha gerada ainda. Elas aparecem aqui automaticamente após cada
                          coleta (agendada ou manual).
                        </TableCell>
                      </TableRow>
                    ) : (
                      planilhas.map((pl, idx) => {
                        const om = ORIGEM_META[pl.origem] ?? { label: pl.origem, cls: "bg-slate-100 text-slate-700" };
                        return (
                          <TableRow key={pl.id} className={idx % 2 === 1 ? "bg-muted/20" : undefined}>
                            <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                              {fmtData(pl.created_at)}
                            </TableCell>
                            <TableCell className="max-w-[280px] truncate font-mono text-xs" title={pl.nome_arquivo}>
                              {pl.nome_arquivo}
                            </TableCell>
                            <TableCell>
                              <Badge className={`${om.cls} hover:${om.cls}`} variant="secondary">{om.label}</Badge>
                            </TableCell>
                            <TableCell className="text-right">{pl.total_processos}</TableCell>
                            <TableCell className="text-right text-muted-foreground">{fmtBytes(pl.tamanho_bytes)}</TableCell>
                            <TableCell>
                              <div className="flex items-center gap-2">
                                <Checkbox
                                  id={`subido-${pl.id}`}
                                  checked={pl.subido_legalone}
                                  onCheckedChange={(v) => alternarSubido(pl, v === true)}
                                />
                                {pl.subido_legalone ? (
                                  <span className="flex items-center gap-1 text-xs text-emerald-700">
                                    <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                                    {pl.subido_em ? fmtData(pl.subido_em) : "subido"}
                                    {pl.subido_por ? ` · ${pl.subido_por}` : ""}
                                  </span>
                                ) : (
                                  <label htmlFor={`subido-${pl.id}`} className="cursor-pointer text-xs text-amber-600">
                                    marcar como subido
                                  </label>
                                )}
                              </div>
                            </TableCell>
                            <TableCell className="text-right">
                              <div className="flex justify-end gap-1.5">
                                <Button
                                  size="sm"
                                  onClick={() => cadastrarDaLista(pl)}
                                  disabled={cadastrandoL1}
                                >
                                  Cadastrar
                                </Button>
                                <Button size="sm" variant="outline" onClick={() => abrirDetalhe(pl.id)}>
                                  Ver
                                </Button>
                                <Button size="sm" variant="outline" onClick={() => baixarDoHistorico(pl)}>
                                  <Download className="mr-1.5 h-3.5 w-3.5" /> Baixar
                                </Button>
                              </div>
                            </TableCell>
                          </TableRow>
                        );
                      })
                    )}
                  </TableBody>
                </Table>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-3 border-t p-3 text-sm">
                <div className="text-muted-foreground">
                  {planilhasTotal === 0 ? 0 : (planilhasPage - 1) * PLANILHAS_PAGE_SIZE + 1}–
                  {Math.min(planilhasTotal, planilhasPage * PLANILHAS_PAGE_SIZE)} de {planilhasTotal} · Página{" "}
                  {planilhasPage} de {planilhasTotalPages}
                </div>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={planilhasPage <= 1 || planilhasLoading}
                    onClick={() => setPlanilhasPage((p) => Math.max(1, p - 1))}
                  >
                    Anterior
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={planilhasPage >= planilhasTotalPages || planilhasLoading}
                    onClick={() => setPlanilhasPage((p) => Math.min(planilhasTotalPages, p + 1))}
                  >
                    Próxima
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </>
      )}

      {aba === "log" && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative w-full lg:w-96">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                className="w-full pl-8"
                placeholder="Auditar por CNJ, NPJ ou adverso — histórico do processo"
                value={logBuscaInput}
                onChange={(e) => setLogBuscaInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    setLogPage(1);
                    setLogBusca(logBuscaInput.trim());
                  }
                }}
              />
            </div>
            {logBusca && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setLogBuscaInput("");
                  setLogBusca("");
                  setLogPage(1);
                }}
              >
                Limpar auditoria
              </Button>
            )}
            <Select value={secaoFiltro || "__all__"} onValueChange={(v) => { setLogPage(1); setSecaoFiltro(v === "__all__" ? "" : v); }}>
              <SelectTrigger className="w-48">
                <SelectValue placeholder="Seção" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">Todas as seções</SelectItem>
                {["Coleta", "Extração", "Ciência", "Distribuição", "Envolvidos", "Contatos", "Cadastro", "Planilha", "Configuração", "Sessão"].map((s) => (
                  <SelectItem key={s} value={s}>{s}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={nivelFiltro || "__all__"} onValueChange={(v) => { setLogPage(1); setNivelFiltro(v === "__all__" ? "" : v); }}>
              <SelectTrigger className="w-40">
                <SelectValue placeholder="Nível" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">Todos os níveis</SelectItem>
                {["INFO", "SUCESSO", "AVISO", "ERRO"].map((n) => (
                  <SelectItem key={n} value={n}>{n}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {logBusca && (
            <p className="-mt-1 text-xs text-muted-foreground">
              Auditoria de <span className="font-mono">{logBusca}</span> — todo o histórico do(s) processo(s) que casam, em ordem cronológica inversa.
            </p>
          )}

          <Card>
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-40">Quando</TableHead>
                      <TableHead className="w-32">Seção</TableHead>
                      <TableHead className="w-24">Nível</TableHead>
                      <TableHead>Mensagem</TableHead>
                      <TableHead className="w-20 text-right">Processo</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {eventos.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={5} className="py-10 text-center text-muted-foreground">
                          Nenhum evento registrado.
                        </TableCell>
                      </TableRow>
                    ) : (
                      eventos.map((ev, idx) => (
                        <TableRow key={ev.id} className={idx % 2 === 1 ? "bg-muted/20" : undefined}>
                          <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{fmtData(ev.created_at)}</TableCell>
                          <TableCell>
                            <Badge variant="outline">{ev.secao}</Badge>
                          </TableCell>
                          <TableCell>
                            <Badge className={NIVEL_META[ev.nivel] ?? ""} variant="secondary">
                              {ev.nivel}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            {ev.acao && <span className="font-medium">{ev.acao}: </span>}
                            {ev.mensagem}
                          </TableCell>
                          <TableCell className="text-right text-xs text-muted-foreground">{ev.processo_id ?? "—"}</TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </div>
              <div className="flex items-center justify-between gap-3 border-t p-3 text-sm">
                <span className="text-muted-foreground">{eventosTotal} eventos</span>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" disabled={logPage <= 1} onClick={() => setLogPage((p) => Math.max(1, p - 1))}>
                    Anterior
                  </Button>
                  <Button variant="outline" size="sm" disabled={logPage >= logTotalPages} onClick={() => setLogPage((p) => p + 1)}>
                    Próxima
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </>
      )}

      {/* Dialog de auditoria */}
      <Dialog open={!!auditoria} onOpenChange={(o) => !o && setAuditoria(null)}>
        <DialogContent className="max-h-[92vh] max-w-4xl overflow-y-auto overflow-x-hidden">
          {auditoria && (
            <>
              <DialogHeader>
                <DialogTitle className="font-mono text-base">
                  {auditoria.processo.cnj ?? auditoria.processo.npj ?? `Processo ${auditoria.processo.id}`}
                </DialogTitle>
                <DialogDescription>{auditoria.processo.adverso_principal ?? "Sem adverso principal"}</DialogDescription>
              </DialogHeader>

              {/* Dados capturados */}
              <div>
                <h3 className="mb-2 text-sm font-semibold">Dados capturados</h3>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-3">
                  {[
                    ["Status", STATUS_META[auditoria.processo.status]?.label ?? auditoria.processo.status],
                    ["Posição", auditoria.processo.posicao ?? "—"],
                    ["Polo", auditoria.processo.polo ?? "—"],
                    ["Natureza", auditoria.processo.natureza ?? "—"],
                    ["Ação", auditoria.processo.acao ?? "—"],
                    ["Valor da causa", fmtValor(auditoria.processo.valor_causa)],
                    ["Data ajuizamento", auditoria.processo.data_ajuizamento ?? "—"],
                    ["Situação", auditoria.processo.situacao ?? "—"],
                    ["Responsável", auditoria.processo.responsavel_nome ?? "—"],
                    ["Observação", auditoria.processo.observacao ?? "—"],
                  ].map(([label, val]) => (
                    <div key={label} className="min-w-0">
                      <div className="text-xs text-muted-foreground">{label}</div>
                      <div className="truncate">{val}</div>
                    </div>
                  ))}
                </div>
                {/* Escritório responsável (path completo) + link da pasta no L1 */}
                <div className="mt-2 grid gap-x-4 gap-y-2 text-sm sm:grid-cols-2">
                  <div className="min-w-0">
                    <div className="text-xs text-muted-foreground">Escritório responsável</div>
                    <div className="break-words">{auditoria.processo.escritorio_path ?? "—"}</div>
                  </div>
                  <div className="min-w-0">
                    <div className="text-xs text-muted-foreground">Cadastro no Legal One</div>
                    {auditoria.processo.l1_lawsuit_id ? (
                      <a
                        href={`https://mdradvocacia.novajus.com.br/processos/Processos/details/${auditoria.processo.l1_lawsuit_id}`}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 font-medium text-primary hover:underline"
                      >
                        {auditoria.processo.l1_folder ?? `id ${auditoria.processo.l1_lawsuit_id}`}
                        <ExternalLink className="h-3.5 w-3.5 shrink-0" />
                      </a>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </div>
                </div>
              </div>

              {/* Envolvidos */}
              <div>
                <h3 className="mb-2 text-sm font-semibold">Envolvidos ({auditoria.envolvidos.length})</h3>
                {auditoria.envolvidos.length === 0 ? (
                  <p className="text-sm text-muted-foreground">Nenhum envolvido capturado ainda (virá da capa do NPJ).</p>
                ) : (
                  <div className="overflow-x-auto rounded-md border">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Nome</TableHead>
                          <TableHead>Papel</TableHead>
                          <TableHead>CPF/CNPJ</TableHead>
                          <TableHead>Contato no L1</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {auditoria.envolvidos.map((e) => (
                          <TableRow key={e.id}>
                            <TableCell>{e.nome}</TableCell>
                            <TableCell>{e.papel ?? "—"}</TableCell>
                            <TableCell className="font-mono text-xs">{e.cpf_cnpj ?? "—"}</TableCell>
                            <TableCell>
                              <Badge variant="outline">{e.status_contato}</Badge>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </div>

              {/* Envolvidos de equipe (derivados da config: equipe + ajuizamento) */}
              <div>
                <h3 className="mb-2 text-sm font-semibold">
                  Equipe / Envolvidos ({auditoria.envolvidos_equipe?.length ?? 0})
                </h3>
                {(auditoria.envolvidos_equipe?.length ?? 0) === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    Nenhum — configure a equipe do responsável (e os grupos de ajuizamento) na tela de Configuração.
                  </p>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {auditoria.envolvidos_equipe.map((e, i) => (
                      <div key={i} className="flex items-center gap-2 rounded-md border bg-card px-2.5 py-1.5 text-sm">
                        <span>{e.nome ?? `#${e.membro_user_id}`}</span>
                        <Badge variant="secondary">{e.classificacao}</Badge>
                        {e.origem === "ajuizamento" && (
                          <Badge className="bg-purple-100 text-purple-700" variant="secondary">ajuizamento</Badge>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Linha do tempo */}
              <div>
                <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold">
                  Linha do tempo {auditLoading && <Loader2 className="h-3 w-3 animate-spin" />}
                </h3>
                <ol className="space-y-2">
                  {auditoria.eventos.map((ev) => (
                    <li key={ev.id} className="flex gap-3 rounded-md border bg-card p-2 text-sm">
                      <Badge className={NIVEL_META[ev.nivel] ?? ""} variant="secondary">
                        {ev.secao}
                      </Badge>
                      <div className="min-w-0 flex-1">
                        <div>
                          {ev.acao && <span className="font-medium">{ev.acao}: </span>}
                          {ev.mensagem}
                        </div>
                        <div className="text-xs text-muted-foreground">{fmtData(ev.created_at)}</div>
                      </div>
                    </li>
                  ))}
                  {auditoria.eventos.length === 0 && !auditLoading && (
                    <li className="text-sm text-muted-foreground">Sem eventos.</li>
                  )}
                </ol>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* Detalhe da planilha (tela de visualização) */}
      <Dialog
        open={detalheOpen}
        onOpenChange={(o) => {
          if (!o) {
            setDetalheOpen(false);
            setDetalhe(null);
          }
        }}
      >
        <DialogContent className="max-h-[85vh] max-w-4xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <FileSpreadsheet className="h-5 w-5 text-[hsl(var(--dunatech-blue))]" />
              <span className="truncate font-mono text-sm">
                {detalhe ? detalhe.planilha.nome_arquivo : "Planilha"}
              </span>
            </DialogTitle>
            <DialogDescription>
              Processos desta planilha e o status de cadastro no Legal One — o monitor confirma de 2 em 2 min.
            </DialogDescription>
          </DialogHeader>
          {detalheLoading || !detalhe ? (
            <div className="py-10 text-center">
              <Loader2 className="mx-auto h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : (
            <div className="space-y-4">
              <div className="rounded-md border p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-sm font-medium">
                    {detalhe.progresso.cadastrados} de {detalhe.progresso.total} cadastrados no Legal One
                  </span>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => cadastrarNoL1(true)}
                      disabled={cadastrandoL1}
                      title="Sobe e parseia no L1 sem criar pasta (validação)"
                    >
                      {cadastrandoL1 ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                      Simular import
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => cadastrarNoL1(false)}
                      disabled={cadastrandoL1}
                    >
                      {cadastrandoL1 ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                      Cadastrar no L1
                    </Button>
                    <Button size="sm" variant="outline" onClick={verificarCadastro} disabled={verificando}>
                      {verificando ? (
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      ) : (
                        <RefreshCw className="mr-2 h-4 w-4" />
                      )}
                      Verificar agora
                    </Button>
                  </div>
                </div>
                <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full bg-emerald-500 transition-all"
                    style={{
                      width: `${
                        detalhe.progresso.total
                          ? (detalhe.progresso.cadastrados / detalhe.progresso.total) * 100
                          : 0
                      }%`,
                    }}
                  />
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {detalhe.progresso.pendentes} pendente(s) de cadastro · gerada {fmtData(detalhe.planilha.created_at)}
                </div>
              </div>

              <div className="overflow-x-auto rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>CNJ / NPJ</TableHead>
                      <TableHead>Responsável</TableHead>
                      <TableHead>Status cadastro</TableHead>
                      <TableHead>Pasta L1</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {detalhe.processos.map((p, idx) => {
                      const pm = POOL_META[p.planilha_status] ?? {
                        label: p.planilha_status,
                        cls: "bg-slate-100 text-slate-700",
                      };
                      return (
                        <TableRow key={p.id} className={idx % 2 === 1 ? "bg-muted/20" : undefined}>
                          <TableCell className="font-mono text-xs">
                            <div>{p.cnj ?? <span className="text-muted-foreground">sem CNJ</span>}</div>
                            <div className="text-muted-foreground">{p.npj ?? "—"}</div>
                          </TableCell>
                          <TableCell className="text-sm">{p.responsavel_nome ?? "—"}</TableCell>
                          <TableCell>
                            <Badge className={`${pm.cls} hover:${pm.cls}`} variant="secondary">
                              {pm.label}
                            </Badge>
                          </TableCell>
                          <TableCell className="font-mono text-xs">{p.l1_folder ?? "—"}</TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
