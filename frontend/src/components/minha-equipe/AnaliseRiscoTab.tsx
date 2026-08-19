// Aba "Análise de Risco" do BB Réu (Minha Equipe).
// Lista as tarefas do subtipo espelhadas do L1 (via Agenda Analytics) com o
// status da verificação no portal BB. Divergência (cumprida no L1 sem análise
// no portal) é o farol que o supervisor cobra.

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, ArrowDown, ArrowUp, Loader2, RefreshCw, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import {
  AnaliseRiscoItem,
  AnaliseRiscoResponse,
  listarAnaliseRisco,
  reverificarAnaliseRisco,
  syncAnaliseRisco,
} from "@/services/analise-risco";

const PAGE_SIZES = [25, 50, 100];
const TODOS = "__todos__";

function fmtData(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleDateString("pt-BR");
}

function fmtDataHora(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime())
    ? "—"
    : `${d.toLocaleDateString("pt-BR")} ${d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}`;
}

function prazoVencido(item: AnaliseRiscoItem): boolean {
  if (item.status_l1 !== "Pendente" || !item.prazo) return false;
  return new Date(item.prazo).getTime() < Date.now();
}

function VerificacaoBadge({ item }: { item: AnaliseRiscoItem }) {
  if (item.verif_status === "SUPERADA") {
    return (
      <Badge
        variant="outline"
        className="cursor-help text-muted-foreground"
        title="Existe análise de risco mais recente deste processo — a auditoria acontece na mais nova. O que o portal respondeu pra esta ficou guardado como histórico."
      >
        Superada
      </Badge>
    );
  }
  if (item.divergente === true) {
    return (
      <Badge variant="destructive" className="gap-1">
        <AlertTriangle className="h-3 w-3" /> Divergente
      </Badge>
    );
  }
  if (item.portal_analise_feita === true) {
    return (
      <Badge className="bg-emerald-600 hover:bg-emerald-600">
        Feita{item.portal_estado ? ` · ${item.portal_estado}` : ""}
      </Badge>
    );
  }
  switch (item.verif_status) {
    case "NA_FILA":
      return <Badge variant="secondary">Na fila de verificação</Badge>;
    case "ERRO":
      return (
        <Badge
          variant="outline"
          className="cursor-help"
          title={`${item.verif_ultimo_erro || "erro não registrado"} (tentativa ${item.verif_tentativas ?? 0})`}
        >
          Erro — vai re-tentar
        </Badge>
      );
    case "VERIFICADA":
      return <Badge variant="secondary">Verificada</Badge>;
    default:
      return <Badge variant="outline">—</Badge>;
  }
}

export default function AnaliseRiscoTab({ team }: { team: string }) {
  const { toast } = useToast();
  const [data, setData] = useState<AnaliseRiscoResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);

  const [statusL1, setStatusL1] = useState<string>(TODOS);
  const [responsavel, setResponsavel] = useState<string>(TODOS);
  const [soDivergentes, setSoDivergentes] = useState(false);
  const [soVencidas, setSoVencidas] = useState(false);
  const [verifFiltro, setVerifFiltro] = useState<string>(TODOS);
  const [busca, setBusca] = useState("");
  const [buscaAplicada, setBuscaAplicada] = useState("");
  const [ordenar, setOrdenar] = useState("prazo");
  const [direcao, setDirecao] = useState<"asc" | "desc">("asc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  // KPI clicado (vira filtro); clique de novo limpa. Mexer nos filtros manuais
  // desativa o destaque do card pra não mentir sobre o que está aplicado.
  const [kpiAtivo, setKpiAtivo] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await listarAnaliseRisco({
        team,
        status_l1: statusL1 === TODOS ? undefined : statusL1,
        responsavel: responsavel === TODOS ? undefined : responsavel,
        divergente: soDivergentes ? true : undefined,
        verif_status: verifFiltro === TODOS ? undefined : verifFiltro,
        vencidas: soVencidas || undefined,
        busca: buscaAplicada || undefined,
        ordenar,
        direcao,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      });
      setData(resp);
    } catch (e) {
      toast({
        title: "Erro ao carregar Análise de Risco",
        description: String((e as Error).message),
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  }, [team, statusL1, responsavel, soDivergentes, soVencidas, verifFiltro, buscaAplicada, ordenar, direcao, page, pageSize, toast]);

  useEffect(() => {
    load();
  }, [load]);

  const reverificar = async (id: number) => {
    try {
      await reverificarAnaliseRisco(id, team);
      toast({ title: "Na fila", description: "A tarefa será re-verificada no portal no próximo ciclo." });
      await load();
    } catch (e) {
      toast({ title: "Erro ao re-enfileirar", description: String((e as Error).message), variant: "destructive" });
    }
  };

  const forcarSync = async () => {
    setSyncing(true);
    try {
      const r = await syncAnaliseRisco(team);
      toast({
        title: "Sync concluído",
        description: `${r.tarefas} tarefas do subtipo (${r.inseridas} novas, ${r.enfileiradas_verificacao} pra verificar).`,
      });
      await load();
    } catch (e) {
      toast({ title: "Erro no sync", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setSyncing(false);
    }
  };

  const kpis = data?.kpis;
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const KPI_CARDS: { key: keyof NonNullable<typeof kpis>; label: string; destaque?: boolean }[] = [
    { key: "abertas", label: "Abertas" },
    { key: "vencidas", label: "Vencidas" },
    { key: "cumpridas", label: "Cumpridas (L1)" },
    { key: "aguardando_verificacao", label: "Aguardando verificação" },
    { key: "divergentes", label: "Divergentes", destaque: true },
  ];

  const limparFiltrosKpi = () => {
    setStatusL1(TODOS);
    setSoVencidas(false);
    setVerifFiltro(TODOS);
    setSoDivergentes(false);
    setPage(1);
  };

  // Clique no card = filtro correspondente na tabela; clique de novo = limpa.
  const aplicarKpi = (key: string) => {
    if (kpiAtivo === key) {
      setKpiAtivo(null);
      limparFiltrosKpi();
      return;
    }
    limparFiltrosKpi();
    setKpiAtivo(key);
    if (key === "abertas") setStatusL1("Pendente");
    else if (key === "vencidas") setSoVencidas(true);
    else if (key === "cumpridas") setStatusL1("Cumprido");
    else if (key === "aguardando_verificacao") setVerifFiltro("NA_FILA");
    else if (key === "divergentes") setSoDivergentes(true);
  };

  // Cabeçalho ordenável: 1º clique asc, 2º inverte.
  const Ordenavel = ({ campo, children }: { campo: string; children: React.ReactNode }) => (
    <TableHead
      className="cursor-pointer select-none"
      onClick={() => {
        if (ordenar === campo) setDirecao((d) => (d === "asc" ? "desc" : "asc"));
        else {
          setOrdenar(campo);
          setDirecao("asc");
        }
        setPage(1);
      }}
      title="Clique pra ordenar"
    >
      <span className="inline-flex items-center gap-1">
        {children}
        {ordenar === campo &&
          (direcao === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
      </span>
    </TableHead>
  );

  return (
    <div className="space-y-4">
      {/* KPIs — clicáveis: filtram a tabela (clique de novo limpa) */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {KPI_CARDS.map((c) => (
          <Card
            key={c.key}
            role="button"
            onClick={() => aplicarKpi(c.key)}
            title="Clique pra filtrar a tabela"
            className={[
              "cursor-pointer transition-colors hover:bg-muted/50",
              c.destaque && (kpis?.[c.key] ?? 0) > 0 ? "border-red-500" : "",
              kpiAtivo === c.key ? "ring-2 ring-primary" : "",
            ].join(" ")}
          >
            <CardContent className="p-4">
              <div className="text-xs text-muted-foreground">{c.label}</div>
              <div className="text-2xl font-bold tabular-nums">{kpis?.[c.key] ?? 0}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Filtros */}
      <div className="flex flex-wrap items-center gap-2">
        <Select value={statusL1} onValueChange={(v) => { setStatusL1(v); setKpiAtivo(null); setPage(1); }}>
          <SelectTrigger className="w-40"><SelectValue placeholder="Status L1" /></SelectTrigger>
          <SelectContent>
            <SelectItem value={TODOS}>Todos os status</SelectItem>
            <SelectItem value="Pendente">Pendente</SelectItem>
            <SelectItem value="Cumprido">Cumprido</SelectItem>
          </SelectContent>
        </Select>
        <Select value={verifFiltro} onValueChange={(v) => { setVerifFiltro(v); setKpiAtivo(null); setPage(1); }}>
          <SelectTrigger className="w-52"><SelectValue placeholder="Verificação no portal" /></SelectTrigger>
          <SelectContent>
            <SelectItem value={TODOS}>Verificação: todas</SelectItem>
            <SelectItem value="NA_FILA">Na fila de verificação</SelectItem>
            <SelectItem value="VERIFICADA">Verificada</SelectItem>
            <SelectItem value="ERRO">Com erro (vai re-tentar)</SelectItem>
            <SelectItem value="PENDENTE">Sem verificação (tarefa aberta)</SelectItem>
            <SelectItem value="SUPERADA">Superadas (há análise mais recente)</SelectItem>
          </SelectContent>
        </Select>
        <Select value={responsavel} onValueChange={(v) => { setResponsavel(v); setPage(1); }}>
          <SelectTrigger className="w-56"><SelectValue placeholder="Responsável" /></SelectTrigger>
          <SelectContent>
            <SelectItem value={TODOS}>Todos os responsáveis</SelectItem>
            {(data?.responsaveis ?? []).map((r) => (
              <SelectItem key={r} value={r}>{r}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          variant={soDivergentes ? "destructive" : "outline"}
          size="sm"
          onClick={() => { setSoDivergentes((v) => !v); setKpiAtivo(null); setPage(1); }}
        >
          <AlertTriangle className="mr-1 h-4 w-4" /> Só divergentes
        </Button>
        <form
          className="flex items-center gap-1"
          onSubmit={(e) => { e.preventDefault(); setBuscaAplicada(busca); setPage(1); }}
        >
          <Input
            className="w-56"
            placeholder="NPJ, CNJ ou responsável"
            value={busca}
            onChange={(e) => setBusca(e.target.value)}
          />
          <Button type="submit" variant="outline" size="icon"><Search className="h-4 w-4" /></Button>
        </form>
        <div className="ml-auto flex items-center gap-2">
          {data?.last_sync_at && (
            <span className="text-xs text-muted-foreground">
              Sync: {fmtDataHora(data.last_sync_at)}
            </span>
          )}
          <Button variant="outline" size="sm" onClick={forcarSync} disabled={syncing || loading}>
            {syncing ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-1 h-4 w-4" />}
            Sincronizar
          </Button>
        </div>
      </div>

      {/* Tabela */}
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <Ordenavel campo="npj">NPJ</Ordenavel>
                <TableHead>CNJ</TableHead>
                <Ordenavel campo="responsavel">Responsável</Ordenavel>
                <Ordenavel campo="agendada_em">Agendada</Ordenavel>
                <Ordenavel campo="prazo">Prazo</Ordenavel>
                <Ordenavel campo="status_l1">Status L1</Ordenavel>
                <Ordenavel campo="verificada_em">Análise no portal</Ordenavel>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading && !data ? (
                <TableRow>
                  <TableCell colSpan={7} className="py-8 text-center">
                    <Loader2 className="mx-auto h-6 w-6 animate-spin text-muted-foreground" />
                  </TableCell>
                </TableRow>
              ) : (data?.items ?? []).length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="py-8 text-center text-muted-foreground">
                    <div>Nenhuma tarefa de Análise de Risco encontrada.</div>
                    <div className="mt-1 text-xs">
                      {(data?.subtipos_encontrados ?? []).length > 0 ? (
                        <>Subtipos do L1 que casaram: {data!.subtipos_encontrados!.join(", ")} — clique em Sincronizar.</>
                      ) : (
                        <>
                          Nenhum subtipo do espelho do L1 casa com a configuração (
                          {(data?.subtipos ?? []).join(", ")}). Confira o nome exato do
                          subtipo usado no agendamento e ajuste a chave{" "}
                          <code>analise_risco_subtipos</code>.
                        </>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ) : (
                (data?.items ?? []).map((item) => (
                  <TableRow key={item.id} className={item.divergente ? "bg-red-50 dark:bg-red-950/20" : undefined}>
                    <TableCell className="font-mono text-xs">{item.npj || "—"}</TableCell>
                    <TableCell className="font-mono text-xs">{item.cnj || "—"}</TableCell>
                    <TableCell>{item.responsavel_nome || "—"}</TableCell>
                    <TableCell>{fmtData(item.agendada_em)}</TableCell>
                    <TableCell className={prazoVencido(item) ? "font-semibold text-red-600" : undefined}>
                      {fmtData(item.prazo)}
                    </TableCell>
                    <TableCell>
                      {item.status_l1 === "Cumprido" ? (
                        <Badge variant="secondary">
                          Cumprido {item.concluida_em ? `em ${fmtData(item.concluida_em)}` : ""}
                        </Badge>
                      ) : (
                        <Badge variant="outline">{item.status_l1 || "—"}</Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1.5">
                        <VerificacaoBadge item={item} />
                        {item.status_l1 === "Cumprido" && item.verif_status !== "NA_FILA" && item.verif_status !== "SUPERADA" && (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-6 w-6"
                            title="Re-verificar no portal BB (entra na fila da esteira)"
                            onClick={() => reverificar(item.id)}
                          >
                            <RefreshCw className="h-3.5 w-3.5" />
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Paginação (padrão da casa) */}
      <div className="flex items-center justify-between text-sm">
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">Itens por página</span>
          <Select value={String(pageSize)} onValueChange={(v) => { setPageSize(Number(v)); setPage(1); }}>
            <SelectTrigger className="w-20"><SelectValue /></SelectTrigger>
            <SelectContent>
              {PAGE_SIZES.map((n) => (
                <SelectItem key={n} value={String(n)}>{n}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" disabled={page <= 1 || loading} onClick={() => setPage((p) => p - 1)}>
            Anterior
          </Button>
          <span className="text-muted-foreground">
            Página {page} de {totalPages} · {total === 0 ? 0 : (page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} de {total}
          </span>
          <Button variant="outline" size="sm" disabled={page >= totalPages || loading} onClick={() => setPage((p) => p + 1)}>
            Próxima
          </Button>
        </div>
      </div>
    </div>
  );
}
