// Aba "Embargos à Execução" da Controladoria (Minha Equipe).
// Board das execuções do BB Autor monitoradas depois do protocolo da inicial:
// janela em dias úteis → consulta ao tribunal (DataJud + DJEN) → embargos
// encontrados para conferência. Cada linha abre a página dedicada da execução.

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowDown, ArrowUp, FileUp, ListChecks, Loader2, Plus, RefreshCw, Search, Settings2, Users } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import {
  ESTADOS,
  ESTADO_HINT,
  ESTADO_LABEL,
  EmbargosListaResponse,
  EmbargosParametros,
  PARTES_LABEL,
  coletarPartesAgora,
  gerarRelatorioEmbargos,
  getPartesStatus,
  getRelatorioStatus,
  importarPlanilhaEmbargos,
  incluirEmbargosManual,
  listarEmbargos,
  salvarEmbargosParametros,
} from "@/services/embargos-execucao";

const PAGE_SIZES = [25, 50, 100];
const TODOS = "__todos__";

export function fmtData(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? `${iso}T12:00:00` : iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleDateString("pt-BR");
}

export function fmtDataHora(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime())
    ? "—"
    : `${d.toLocaleDateString("pt-BR")} ${d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}`;
}

export function EstadoBadge({ estado }: { estado: string }) {
  const cls: Record<string, string> = {
    ENCONTRADO: "bg-red-600 hover:bg-red-600 text-white",
    MONITORANDO: "bg-sky-600 hover:bg-sky-600 text-white",
    AGUARDANDO_JANELA: "bg-slate-200 text-slate-800 hover:bg-slate-200",
    SEM_CNJ: "bg-amber-200 text-amber-900 hover:bg-amber-200",
    CONFIRMADO: "bg-emerald-600 hover:bg-emerald-600 text-white",
    CONCLUIDO: "bg-emerald-800 hover:bg-emerald-800 text-white",
    JA_CADASTRADO: "bg-emerald-100 text-emerald-900 hover:bg-emerald-100",
  };
  return (
    <Badge variant={cls[estado] ? "default" : "outline"} className={`cursor-help ${cls[estado] ?? ""}`} title={ESTADO_HINT[estado]}>
      {ESTADO_LABEL[estado] ?? estado}
    </Badge>
  );
}

export default function EmbargosExecucaoTab({ team }: { team: string }) {
  const { toast } = useToast();
  const navigate = useNavigate();
  const [data, setData] = useState<EmbargosListaResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [estado, setEstado] = useState<string>(TODOS);
  const [cliente, setCliente] = useState<string>(TODOS);
  const [soPartesErro, setSoPartesErro] = useState(false);
  const [busca, setBusca] = useState("");
  const [buscaAplicada, setBuscaAplicada] = useState("");
  const [ordenar, setOrdenar] = useState("proxima_consulta");
  const [direcao, setDirecao] = useState<"asc" | "desc">("asc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [importando, setImportando] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [paramsOpen, setParamsOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const relatorioRodandoRef = useRef(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await listarEmbargos({
        estado: estado === TODOS ? undefined : estado,
        cliente: cliente === TODOS ? undefined : cliente,
        partes_status: soPartesErro ? "ERRO" : undefined,
        busca: buscaAplicada || undefined,
        ordenar,
        direcao,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      });
      setData(resp);
      relatorioRodandoRef.current = !!resp.relatorio?.running;
    } catch (e) {
      toast({ title: "Erro ao carregar Embargos à Execução", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [estado, cliente, soPartesErro, buscaAplicada, ordenar, direcao, page, pageSize, toast]);

  useEffect(() => {
    load();
  }, [load]);

  const relatorio = data?.relatorio;
  const rodando = !!relatorio?.running;
  const partesRodando = !!data?.partes?.running;

  // Coleta de partes (portal do BB): uma passagem por dia de madrugada; o botão
  // dispara na hora. Poll enquanto roda e recarrega o board ao terminar.
  useEffect(() => {
    if (!partesRodando) return;
    const id = setInterval(async () => {
      try {
        const st = await getPartesStatus();
        if (!st.running) {
          clearInterval(id);
          toast({
            title: st.ultimo?.erro ? "Coleta de partes não rodou" : "Coleta de partes concluída",
            description: st.ultimo?.erro || `${st.ultimo?.na_fila ?? 0} execução(ões) na fila consultada(s) no portal do BB.`,
            variant: st.ultimo?.erro ? "destructive" : undefined,
          });
          load();
        } else {
          setData((d) => (d ? { ...d, partes: st } : d));
        }
      } catch {
        /* ignore */
      }
    }, 10000);
    return () => clearInterval(id);
  }, [partesRodando, load, toast]);

  const coletarPartes = async () => {
    try {
      await coletarPartesAgora();
      toast({ title: "Coleta de partes disparada", description: "Abre o portal do BB no servidor — pode sair da tela." });
      load();
    } catch (e) {
      toast({ title: "Não deu pra disparar", description: String((e as Error).message), variant: "destructive" });
    }
  };

  // Poll do relatório no servidor: rápido enquanto roda; ao terminar recarrega
  // o board e mostra o desfecho (a geração não depende desta tela aberta).
  useEffect(() => {
    if (!rodando) return;
    const id = setInterval(async () => {
      try {
        const st = await getRelatorioStatus();
        if (!st.running) {
          clearInterval(id);
          const u = st.ultimo;
          if (u?.erro) {
            toast({ title: "Relatório do L1 falhou", description: u.erro, variant: "destructive" });
          } else if (u?.sem_dados) {
            toast({ title: "Relatório sem dados", description: "Nenhuma tarefa de protocolo cumprida no filtro do modelo." });
          } else if (u) {
            toast({
              title: "Relatório importado",
              description: `${u.novas ?? 0} caso(s) novo(s) · ${u.antes_do_corte ?? 0} anterior(es) ao corte ignorado(s).`,
            });
          }
          load();
        } else {
          setData((d) => (d ? { ...d, relatorio: st } : d));
        }
      } catch {
        /* ignore */
      }
    }, 5000);
    return () => clearInterval(id);
  }, [rodando, load, toast]);

  const gerar = async () => {
    try {
      await gerarRelatorioEmbargos();
      toast({ title: "Relatório disparado", description: "Gerando no Legal One — roda no servidor, pode sair da tela." });
      load();
    } catch (e) {
      toast({ title: "Não deu pra gerar", description: String((e as Error).message), variant: "destructive" });
    }
  };

  const onArquivo = async (ev: React.ChangeEvent<HTMLInputElement>) => {
    const file = ev.target.files?.[0];
    ev.target.value = "";
    if (!file) return;
    setImportando(true);
    try {
      const r = await importarPlanilhaEmbargos(file);
      toast({
        title: "Planilha importada",
        description: `${r.novas} nova(s), ${r.atualizadas} atualizada(s), ${r.sem_data} sem data, ${r.sem_identificador} sem pasta/CNJ.`,
      });
      load();
    } catch (e) {
      toast({ title: "Erro na importação", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setImportando(false);
    }
  };

  const kpis = data?.kpis;
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const aplicarEstado = (e: string) => {
    setSoPartesErro(false);
    setEstado((atual) => (atual === e ? TODOS : e));
    setPage(1);
  };

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
        {ordenar === campo && (direcao === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
      </span>
    </TableHead>
  );

  return (
    <div className="space-y-4">
      {/* Ações */}
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" onClick={gerar} disabled={rodando}>
          {rodando ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-1 h-4 w-4" />}
          Buscar protocolos no L1
        </Button>
        <Button variant="outline" size="sm" onClick={() => fileRef.current?.click()} disabled={importando}>
          {importando ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <FileUp className="mr-1 h-4 w-4" />}
          Importar planilha (legado)
        </Button>
        <input ref={fileRef} type="file" accept=".xlsx,.csv" className="hidden" onChange={onArquivo} />
        <Button variant="outline" size="sm" onClick={() => setManualOpen(true)}>
          <Plus className="mr-1 h-4 w-4" /> Incluir execução
        </Button>
        <Button variant="outline" size="sm" onClick={() => setParamsOpen(true)}>
          <Settings2 className="mr-1 h-4 w-4" /> Parâmetros
        </Button>
        <Button variant="outline" size="sm" onClick={coletarPartes} disabled={partesRodando}
          title="A coleta roda sozinha todo dia de madrugada; aqui dispara agora para as execuções que ainda não têm partes.">
          {partesRodando ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Users className="mr-1 h-4 w-4" />}
          Buscar partes no portal BB
        </Button>
        <Button variant="outline" size="sm" onClick={() => navigate(`/minha-equipe/${team}/embargos/templates`)}>
          <ListChecks className="mr-1 h-4 w-4" /> Templates de tarefa
        </Button>
        <div className="ml-auto text-xs text-muted-foreground">
          {data?.parametros?.corte_relatorio ? (
            <span title="Só protocolos concluídos a partir desta data entram sozinhos pelo relatório. Casos antigos entram por planilha.">
              Casos novos desde {fmtData(data.parametros.corte_relatorio)}
            </span>
          ) : (
            <span>Corte definido na 1ª busca do relatório</span>
          )}
          {relatorio?.ultimo?.em && <span> · Última busca: {fmtDataHora(relatorio.ultimo.em)}</span>}
        </div>
      </div>

      {partesRodando && (
        <div className="rounded-md border border-violet-300 bg-violet-50 px-3 py-2 text-xs dark:bg-violet-950/30">
          <div className="flex items-center gap-1.5 font-medium text-violet-800 dark:text-violet-200">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Lendo as partes no portal do BB
            {data?.partes?.na_fila ? ` — ${data.partes.na_fila} execução(ões) na fila` : ""}
          </div>
          <Progress className="mt-1.5 h-1.5 [&>div]:animate-pulse" value={100} />
        </div>
      )}

      {rodando && (
        <div className="rounded-md border border-sky-300 bg-sky-50 px-3 py-2 text-xs dark:bg-sky-950/30">
          <div className="flex items-center gap-1.5 font-medium text-sky-800 dark:text-sky-200">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Relatório do Legal One em andamento
            {relatorio?.fase ? ` — ${relatorio.fase}` : ""}
          </div>
          <Progress className="mt-1.5 h-1.5 [&>div]:animate-pulse" value={100} />
          <p className="mt-1 text-[10px] text-muted-foreground">Roda no servidor — pode fechar a tela.</p>
        </div>
      )}

      {/* KPIs por estado (clicáveis) */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5 xl:grid-cols-9">
        {ESTADOS.map((e) => (
          <Card
            key={e}
            role="button"
            onClick={() => aplicarEstado(e)}
            title={ESTADO_HINT[e]}
            className={[
              "cursor-pointer transition-colors hover:bg-muted/50",
              e === "ENCONTRADO" && (kpis?.por_estado?.[e] ?? 0) > 0 ? "border-red-500" : "",
              estado === e ? "ring-2 ring-primary" : "",
            ].join(" ")}
          >
            <CardContent className="p-3">
              <div className="text-[11px] leading-tight text-muted-foreground">{ESTADO_LABEL[e]}</div>
              <div className="text-2xl font-bold tabular-nums">{kpis?.por_estado?.[e] ?? 0}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Filtros */}
      <div className="flex flex-wrap items-center gap-2">
        <Select value={estado} onValueChange={(v) => { setEstado(v); setPage(1); }}>
          <SelectTrigger className="w-52"><SelectValue placeholder="Estado" /></SelectTrigger>
          <SelectContent>
            <SelectItem value={TODOS}>Todos os estados</SelectItem>
            {ESTADOS.map((e) => (
              <SelectItem key={e} value={e}>{ESTADO_LABEL[e]}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={cliente} onValueChange={(v) => { setCliente(v); setPage(1); }}>
          <SelectTrigger className="w-40"><SelectValue placeholder="Cliente" /></SelectTrigger>
          <SelectContent>
            <SelectItem value={TODOS}>Todos os clientes</SelectItem>
            <SelectItem value="BB">Banco do Brasil</SelectItem>
            <SelectItem value="BANESE">Banese</SelectItem>
            <SelectItem value="ATIVOS">Ativos</SelectItem>
          </SelectContent>
        </Select>
        <Button
          variant={soPartesErro ? "destructive" : "outline"}
          size="sm"
          onClick={() => { setSoPartesErro((v) => !v); setPage(1); }}
          title="Execuções em que o portal do BB não devolveu as partes"
        >
          Partes com erro ({kpis?.partes_erro ?? 0})
        </Button>
        <form className="flex items-center gap-1" onSubmit={(e) => { e.preventDefault(); setBuscaAplicada(busca); setPage(1); }}>
          <Input className="w-64" placeholder="Pasta, CNJ, NPJ ou responsável" value={busca} onChange={(e) => setBusca(e.target.value)} />
          <Button type="submit" variant="outline" size="icon"><Search className="h-4 w-4" /></Button>
        </form>
        <Button variant="ghost" size="icon" className="ml-auto" onClick={load} disabled={loading} title="Recarregar">
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
        </Button>
      </div>

      {/* Tabela */}
      <Card>
        <CardContent className="overflow-x-auto p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <Ordenavel campo="pasta">Pasta</Ordenavel>
                <TableHead>CNJ / NPJ</TableHead>
                <Ordenavel campo="data_ajuizamento">Ajuizamento</Ordenavel>
                <TableHead>Janela</TableHead>
                <Ordenavel campo="proxima_consulta">Próxima consulta</Ordenavel>
                <Ordenavel campo="consultas_feitas">Consultas</Ordenavel>
                <TableHead>Partes (BB)</TableHead>
                <TableHead>Candidatos</TableHead>
                <Ordenavel campo="estado">Estado</Ordenavel>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading && !data ? (
                <TableRow>
                  <TableCell colSpan={9} className="py-8 text-center">
                    <Loader2 className="mx-auto h-6 w-6 animate-spin text-muted-foreground" />
                  </TableCell>
                </TableRow>
              ) : (data?.items ?? []).length === 0 ? (
                <TableRow>
                  <TableCell colSpan={9} className="py-8 text-center text-muted-foreground">
                    Nenhuma execução no fluxo com esses filtros. Os protocolos novos entram pela busca no L1;
                    casos antigos, pela planilha.
                  </TableCell>
                </TableRow>
              ) : (
                (data?.items ?? []).map((it) => (
                  <TableRow
                    key={it.id}
                    className={`cursor-pointer ${it.estado === "ENCONTRADO" ? "bg-red-50 dark:bg-red-950/20" : ""}`}
                    onClick={() => navigate(`/minha-equipe/${team}/embargos/${it.id}`)}
                    title="Abrir a execução"
                  >
                    <TableCell className="font-mono text-xs">{it.pasta}</TableCell>
                    <TableCell className="font-mono text-xs">
                      <div>{it.cnj || "—"}</div>
                      <div className="text-muted-foreground">{it.npj || ""}</div>
                    </TableCell>
                    <TableCell>{fmtData(it.data_ajuizamento)}</TableCell>
                    <TableCell className="text-xs">
                      {it.dias_uteis_janela} úteis
                      <div className="text-muted-foreground">desde {fmtData(it.inicio_monitoramento)}</div>
                    </TableCell>
                    <TableCell>{fmtData(it.proxima_consulta)}</TableCell>
                    <TableCell className="tabular-nums">{it.consultas_feitas}</TableCell>
                    <TableCell>
                      <Badge
                        variant={it.partes_status === "ERRO" ? "destructive" : it.partes_status === "OK" ? "secondary" : "outline"}
                        title={it.partes_erro || undefined}
                      >
                        {PARTES_LABEL[it.partes_status] ?? it.partes_status}
                        {it.partes_status === "OK" ? ` · ${it.partes_demandadas ?? 0}` : ""}
                      </Badge>
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {it.candidatos ?? 0}
                      {(it.candidatos_fortes_pendentes ?? 0) > 0 && (
                        <Badge variant="destructive" className="ml-1">{it.candidatos_fortes_pendentes} forte(s)</Badge>
                      )}
                    </TableCell>
                    <TableCell><EstadoBadge estado={it.estado} /></TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Paginação */}
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
          <Button variant="outline" size="sm" disabled={page <= 1 || loading} onClick={() => setPage((p) => p - 1)}>Anterior</Button>
          <span className="text-muted-foreground">
            Página {page} de {totalPages} · {total === 0 ? 0 : (page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} de {total}
          </span>
          <Button variant="outline" size="sm" disabled={page >= totalPages || loading} onClick={() => setPage((p) => p + 1)}>Próxima</Button>
        </div>
      </div>

      <IncluirManualDialog open={manualOpen} onOpenChange={setManualOpen} onDone={load} />
      {data?.parametros && (
        <ParametrosDialog open={paramsOpen} onOpenChange={setParamsOpen} parametros={data.parametros} onDone={load} />
      )}
    </div>
  );
}

function IncluirManualDialog({ open, onOpenChange, onDone }: { open: boolean; onOpenChange: (v: boolean) => void; onDone: () => void }) {
  const { toast } = useToast();
  const [pasta, setPasta] = useState("");
  const [cnj, setCnj] = useState("");
  const [npj, setNpj] = useState("");
  const [data, setData] = useState("");
  const [salvando, setSalvando] = useState(false);

  const salvar = async () => {
    setSalvando(true);
    try {
      const r = await incluirEmbargosManual({ pasta: pasta || undefined, cnj: cnj || undefined, npj: npj || undefined, data_ajuizamento: data });
      toast({ title: r.novas ? "Execução incluída" : "Execução já estava no fluxo — dados complementados" });
      onOpenChange(false);
      setPasta(""); setCnj(""); setNpj(""); setData("");
      onDone();
    } catch (e) {
      toast({ title: "Não deu pra incluir", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setSalvando(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Incluir execução no fluxo</DialogTitle>
          <DialogDescription>Pasta ou CNJ e a data do ajuizamento. As partes e a agenda seguem o fluxo normal.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1"><Label>Pasta</Label><Input placeholder="Proc - 0068694" value={pasta} onChange={(e) => setPasta(e.target.value)} /></div>
          <div className="grid gap-1"><Label>CNJ</Label><Input placeholder="0000000-00.0000.0.00.0000" value={cnj} onChange={(e) => setCnj(e.target.value)} /></div>
          <div className="grid gap-1"><Label>NPJ</Label><Input placeholder="2025/0342918-000" value={npj} onChange={(e) => setNpj(e.target.value)} /></div>
          <div className="grid gap-1"><Label>Data do ajuizamento</Label><Input type="date" value={data} onChange={(e) => setData(e.target.value)} /></div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancelar</Button>
          <Button onClick={salvar} disabled={salvando || !data || (!pasta && !cnj)}>
            {salvando && <Loader2 className="mr-1 h-4 w-4 animate-spin" />} Incluir
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ParametrosDialog({
  open,
  onOpenChange,
  parametros,
  onDone,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  parametros: EmbargosParametros;
  onDone: () => void;
}) {
  const { toast } = useToast();
  const [corte, setCorte] = useState(parametros.corte_relatorio ?? "");
  const [janela, setJanela] = useState(String(parametros.janela_dias_uteis));
  const [intervalo, setIntervalo] = useState(String(parametros.intervalo_dias_uteis));
  const [teto, setTeto] = useState(String(parametros.teto_dias));
  const [emails, setEmails] = useState(parametros.aviso_emails.join(", "));
  const [salvando, setSalvando] = useState(false);

  useEffect(() => {
    if (!open) return;
    setCorte(parametros.corte_relatorio ?? "");
    setJanela(String(parametros.janela_dias_uteis));
    setIntervalo(String(parametros.intervalo_dias_uteis));
    setTeto(String(parametros.teto_dias));
    setEmails(parametros.aviso_emails.join(", "));
  }, [open, parametros]);

  const salvar = async () => {
    setSalvando(true);
    try {
      await salvarEmbargosParametros({
        corte_relatorio: corte || null,
        janela_dias_uteis: Number(janela),
        intervalo_dias_uteis: Number(intervalo),
        teto_dias: Number(teto),
        aviso_emails: emails.split(/[,;]/).map((s) => s.trim()).filter(Boolean),
      });
      toast({ title: "Parâmetros salvos", description: "A janela nova vale para as execuções que entrarem daqui pra frente." });
      onOpenChange(false);
      onDone();
    } catch (e) {
      toast({ title: "Não deu pra salvar", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setSalvando(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Parâmetros do fluxo</DialogTitle>
          <DialogDescription>
            Relatório do L1: “{parametros.relatorio_titulo}” (modelo {parametros.relatorio_modelo_id}).
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1">
            <Label>Casos novos a partir de</Label>
            <Input type="date" value={corte} onChange={(e) => setCorte(e.target.value)} />
            <span className="text-xs text-muted-foreground">Protocolo concluído antes desta data não entra pelo relatório.</span>
          </div>
          <div className="grid gap-1">
            <Label>Janela padrão (dias úteis após o ajuizamento)</Label>
            <Select value={janela} onValueChange={setJanela}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {parametros.janelas_permitidas.map((n) => (
                  <SelectItem key={n} value={String(n)}>{n} dias úteis</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-1">
              <Label>Consulta a cada (dias úteis)</Label>
              <Input type="number" min={1} max={30} value={intervalo} onChange={(e) => setIntervalo(e.target.value)} />
            </div>
            <div className="grid gap-1">
              <Label>Teto do monitoramento (dias)</Label>
              <Input type="number" min={30} max={1825} value={teto} onChange={(e) => setTeto(e.target.value)} />
            </div>
          </div>
          <div className="grid gap-1">
            <Label>Avisar por e-mail</Label>
            <Input placeholder="controladoria@mdradvocacia.com, ..." value={emails} onChange={(e) => setEmails(e.target.value)} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancelar</Button>
          <Button onClick={salvar} disabled={salvando}>
            {salvando && <Loader2 className="mr-1 h-4 w-4 animate-spin" />} Salvar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
