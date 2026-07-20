import { type ReactNode, useCallback, useEffect, useState } from "react";
import {
  AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, ExternalLink, Loader2,
  RefreshCw, Search, Undo2, Users,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import {
  PainelVinculoItem,
  PainelVinculos,
  listarPainelVinculos,
  marcarTransicaoVinculo,
} from "@/services/distribuidos-bb";

const CENARIO_META: Record<string, { label: string; cls: string }> = {
  CENARIO_1: { label: "Novo na equipe — transição pendente", cls: "bg-amber-100 text-amber-800" },
  CENARIO_2: { label: "Parte já especializada", cls: "bg-emerald-100 text-emerald-700" },
};

const POSICAO_CLS: Record<string, string> = {
  Autor: "bg-violet-100 text-violet-700",
  "Réu": "bg-sky-100 text-sky-700",
};

function fmtCnj(cnj: string | null): string {
  if (!cnj) return "—";
  const d = cnj.replace(/\D/g, "");
  if (d.length !== 20) return cnj;
  return `${d.slice(0, 7)}-${d.slice(7, 9)}.${d.slice(9, 13)}.${d.slice(13, 14)}.${d.slice(14, 16)}.${d.slice(16)}`;
}

function fmtValor(v: number | null): string {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function fmtData(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

const L1_URL = "https://mdradvocacia.novajus.com.br/processos/Processos/details";

/** Abre a pasta no Legal One. Sem id (processo ainda não cadastrado / vinculado
 *  antigo que não está na nossa base) mostra o texto puro, sem link morto. */
function LinkL1({
  lawsuitId, folder, children, title,
}: {
  lawsuitId: number | null;
  folder?: string | null;
  children: React.ReactNode;
  title?: string;
}) {
  if (!lawsuitId) return <>{children}</>;
  return (
    <a
      href={`${L1_URL}/${lawsuitId}`}
      target="_blank"
      rel="noreferrer"
      onClick={(e) => e.stopPropagation()}
      title={title ?? (folder ? `Abrir no Legal One — ${folder}` : "Abrir no Legal One")}
      className="inline-flex items-center gap-1 text-[hsl(var(--dunatech-blue))] hover:underline"
    >
      {children}
      <ExternalLink className="h-3 w-3 shrink-0 opacity-70" />
    </a>
  );
}

function KpiCard({
  icone, valor, rotulo, ativo, alerta, onClick,
}: {
  icone: ReactNode;
  valor: number | undefined;
  rotulo: string;
  ativo: boolean;
  alerta?: boolean;
  onClick: () => void;
}) {
  return (
    <Card
      role="button"
      tabIndex={0}
      aria-pressed={ativo}
      onClick={onClick}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(); } }}
      className={`cursor-pointer transition hover:bg-muted/40 hover:shadow-sm ${
        ativo ? "ring-2 ring-primary" : alerta ? "border-amber-300" : ""
      }`}
    >
      <CardContent className="flex items-center gap-3 p-4">
        {icone}
        <div>
          <div className="text-2xl font-bold">{valor ?? "—"}</div>
          <div className="text-xs text-muted-foreground">{rotulo}</div>
        </div>
      </CardContent>
    </Card>
  );
}

export default function AcompanhamentoVinculosTab() {
  const { toast } = useToast();
  const [data, setData] = useState<PainelVinculos | null>(null);
  const [loading, setLoading] = useState(false);
  const [cenarioFiltro, setCenarioFiltro] = useState("");
  const [transicaoFiltro, setTransicaoFiltro] = useState("");
  const [buscaInput, setBuscaInput] = useState("");
  const [busca, setBusca] = useState("");
  const [abertos, setAbertos] = useState<Set<number>>(new Set());
  const [marcando, setMarcando] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await listarPainelVinculos({
        cenario: cenarioFiltro || undefined,
        transicao: transicaoFiltro || undefined,
        busca: busca || undefined,
        limit: 100,
      });
      setData(resp);
    } catch (e) {
      toast({ title: "Erro ao carregar o painel", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [cenarioFiltro, transicaoFiltro, busca, toast]);

  useEffect(() => { load(); }, [load]);

  const toggle = (id: number) => {
    setAbertos((prev) => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  };

  const marcar = async (vinculoId: number, concluida: boolean) => {
    setMarcando(vinculoId);
    try {
      await marcarTransicaoVinculo(vinculoId, concluida);
      toast({
        title: concluida ? "Transição concluída" : "Transição reaberta",
        description: concluida
          ? "O processo antigo foi marcado como transferido pra equipe especializada."
          : "O processo voltou pra lista de transições pendentes.",
      });
      load();
    } catch (e) {
      toast({ title: "Erro", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setMarcando(null);
    }
  };

  const kpis = data?.kpis;

  return (
    <div className="space-y-4">
      {/* KPIs — cada card é um atalho pro recorte que ele conta. Clicar aplica o
          filtro na lista abaixo; clicar no card já ativo limpa (funciona como toggle). */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <KpiCard
          icone={<Users className="h-8 w-8 text-indigo-500" />}
          valor={kpis?.total}
          rotulo="Processos com vínculo"
          ativo={!cenarioFiltro && !transicaoFiltro}
          onClick={() => { setCenarioFiltro(""); setTransicaoFiltro(""); }}
        />
        <KpiCard
          icone={<AlertTriangle className="h-8 w-8 text-amber-500" />}
          valor={kpis?.cenario_1}
          rotulo="Cenário 1 — novos na equipe"
          ativo={cenarioFiltro === "CENARIO_1" && !transicaoFiltro}
          onClick={() => {
            setTransicaoFiltro("");
            setCenarioFiltro((v) => (v === "CENARIO_1" ? "" : "CENARIO_1"));
          }}
        />
        <KpiCard
          icone={<CheckCircle2 className="h-8 w-8 text-emerald-500" />}
          valor={kpis?.cenario_2}
          rotulo="Cenário 2 — já especializados"
          ativo={cenarioFiltro === "CENARIO_2" && !transicaoFiltro}
          onClick={() => {
            setTransicaoFiltro("");
            setCenarioFiltro((v) => (v === "CENARIO_2" ? "" : "CENARIO_2"));
          }}
        />
        <KpiCard
          icone={<Undo2 className="h-8 w-8 text-rose-500" />}
          valor={kpis?.transicoes_pendentes}
          rotulo="Transições pendentes (supervisor)"
          ativo={transicaoFiltro === "pendente"}
          alerta={!!kpis && kpis.transicoes_pendentes > 0}
          onClick={() => {
            setCenarioFiltro("");
            setTransicaoFiltro((v) => (v === "pendente" ? "" : "pendente"));
          }}
        />
      </div>

      {/* Filtros */}
      <div className="flex flex-wrap items-center gap-2">
        <Select value={cenarioFiltro || "all"} onValueChange={(v) => setCenarioFiltro(v === "all" ? "" : v)}>
          <SelectTrigger className="w-[240px]"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Todos os cenários</SelectItem>
            <SelectItem value="CENARIO_1">Cenário 1 — transição pendente</SelectItem>
            <SelectItem value="CENARIO_2">Cenário 2 — já especializado</SelectItem>
          </SelectContent>
        </Select>
        <Select value={transicaoFiltro || "all"} onValueChange={(v) => setTransicaoFiltro(v === "all" ? "" : v)}>
          <SelectTrigger className="w-[220px]"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Transições: todas</SelectItem>
            <SelectItem value="pendente">Só com transição pendente</SelectItem>
          </SelectContent>
        </Select>
        <div className="relative">
          <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            className="w-[260px] pl-8"
            placeholder="CNJ, NPJ ou adverso"
            value={buscaInput}
            onChange={(e) => setBuscaInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") setBusca(buscaInput); }}
          />
        </div>
        <Button variant="outline" size="icon" onClick={load} disabled={loading}>
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
        </Button>
      </div>

      {/* Tabela */}
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" />
                <TableHead>Processo novo</TableHead>
                <TableHead>Posição</TableHead>
                <TableHead>Adverso / Parte</TableHead>
                <TableHead>Responsável (equipe)</TableHead>
                <TableHead>Cenário</TableHead>
                <TableHead className="text-center">Vínculos</TableHead>
                <TableHead>Capturado em</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading && !data ? (
                <TableRow><TableCell colSpan={8} className="py-10 text-center">
                  <Loader2 className="mx-auto h-6 w-6 animate-spin text-muted-foreground" />
                </TableCell></TableRow>
              ) : !data || data.items.length === 0 ? (
                <TableRow><TableCell colSpan={8} className="py-10 text-center text-sm text-muted-foreground">
                  Nenhum processo com vínculo encontrado.
                </TableCell></TableRow>
              ) : (
                data.items.map((item: PainelVinculoItem) => {
                  const aberto = abertos.has(item.processo_id);
                  const cm = CENARIO_META[item.cenario] ?? { label: item.cenario, cls: "bg-slate-100 text-slate-700" };
                  const pendentes = item.vinculos.filter((v) => v.transicao_pendente).length;
                  return (
                    <>
                      <TableRow
                        key={item.processo_id}
                        className="cursor-pointer hover:bg-muted/40"
                        onClick={() => toggle(item.processo_id)}
                      >
                        <TableCell>
                          {aberto ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        </TableCell>
                        <TableCell>
                          <div className="font-mono text-xs">
                            <LinkL1 lawsuitId={item.l1_lawsuit_id} folder={item.l1_folder}>
                              {fmtCnj(item.cnj)}
                            </LinkL1>
                          </div>
                          <div className="font-mono text-[11px] text-muted-foreground">
                            {item.npj ?? "—"}
                            {item.l1_folder && <span className="ml-1.5">· {item.l1_folder}</span>}
                          </div>
                        </TableCell>
                        <TableCell>
                          {item.posicao ? (
                            <Badge variant="secondary" className={POSICAO_CLS[item.posicao] ?? "bg-slate-100 text-slate-700"}>
                              {item.posicao}
                            </Badge>
                          ) : "—"}
                        </TableCell>
                        <TableCell className="max-w-[240px] truncate text-sm">{item.adverso_principal ?? "—"}</TableCell>
                        <TableCell className="text-sm">{item.responsavel_nome ?? "—"}</TableCell>
                        <TableCell>
                          <Badge variant="secondary" className={cm.cls}>{cm.label}</Badge>
                          {pendentes > 0 && (
                            <div className="mt-1 text-[11px] font-medium text-amber-600">
                              {pendentes} transição(ões) pendente(s)
                            </div>
                          )}
                        </TableCell>
                        <TableCell className="text-center font-semibold">{item.vinculos_qtd}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">{fmtData(item.criado_em)}</TableCell>
                      </TableRow>
                      {aberto && (
                        <TableRow key={`${item.processo_id}-det`} className="bg-muted/20 hover:bg-muted/20">
                          <TableCell colSpan={8} className="p-0">
                            <div className="space-y-2 px-10 py-3">
                              <div className="text-xs font-semibold uppercase text-muted-foreground">
                                Processos vinculados da parte {item.vinculos[0]?.nome_parte ? `— ${item.vinculos[0].nome_parte}` : ""}
                              </div>
                              <Table>
                                <TableHeader>
                                  <TableRow>
                                    <TableHead>NPJ</TableHead>
                                    <TableHead>CNJ</TableHead>
                                    <TableHead>Situação</TableHead>
                                    <TableHead>Polo (banco)</TableHead>
                                    <TableHead>Responsável atual</TableHead>
                                    <TableHead>Transição</TableHead>
                                  </TableRow>
                                </TableHeader>
                                <TableBody>
                                  {item.vinculos.map((v) => (
                                    <TableRow key={v.id}>
                                      <TableCell className="font-mono text-xs">
                                        <LinkL1 lawsuitId={v.l1_lawsuit_id} folder={v.l1_folder}>
                                          {v.npj}
                                        </LinkL1>
                                        {v.l1_folder && (
                                          <div className="text-[10px] font-sans text-muted-foreground">{v.l1_folder}</div>
                                        )}
                                      </TableCell>
                                      <TableCell className="font-mono text-xs">
                                        <LinkL1 lawsuitId={v.l1_lawsuit_id} folder={v.l1_folder}>
                                          {fmtCnj(v.cnj)}
                                        </LinkL1>
                                      </TableCell>
                                      <TableCell className="text-sm">{v.situacao ?? "—"}</TableCell>
                                      <TableCell>
                                        {v.posicao_banco ? (
                                          <Badge variant="secondary" className={POSICAO_CLS[v.posicao_banco] ?? "bg-slate-100 text-slate-700"}>
                                            BB {v.posicao_banco}
                                          </Badge>
                                        ) : "—"}
                                      </TableCell>
                                      <TableCell className="text-sm">
                                        {v.responsavel_atual_nome ?? <span className="text-muted-foreground">não identificado</span>}
                                        {v.na_equipe_mista && (
                                          <Badge variant="secondary" className="ml-2 bg-emerald-100 text-emerald-700">equipe</Badge>
                                        )}
                                      </TableCell>
                                      <TableCell>
                                        {v.transicao_pendente ? (
                                          <Button
                                            size="sm" variant="outline"
                                            className="h-7 border-amber-300 text-amber-700 hover:bg-amber-50"
                                            disabled={marcando === v.id}
                                            onClick={(e) => { e.stopPropagation(); marcar(v.id, true); }}
                                          >
                                            {marcando === v.id
                                              ? <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                                              : <CheckCircle2 className="mr-1 h-3 w-3" />}
                                            Marcar transição concluída
                                          </Button>
                                        ) : v.transicao_concluida_em ? (
                                          <div className="flex items-center gap-2 text-xs text-emerald-700">
                                            <CheckCircle2 className="h-3.5 w-3.5" />
                                            Concluída {fmtData(v.transicao_concluida_em)}
                                            <Button
                                              size="sm" variant="ghost" className="h-6 px-1 text-muted-foreground"
                                              disabled={marcando === v.id}
                                              onClick={(e) => { e.stopPropagation(); marcar(v.id, false); }}
                                            >
                                              <Undo2 className="h-3 w-3" />
                                            </Button>
                                          </div>
                                        ) : (
                                          <span className="text-xs text-muted-foreground">—</span>
                                        )}
                                      </TableCell>
                                    </TableRow>
                                  ))}
                                </TableBody>
                              </Table>
                            </div>
                          </TableCell>
                        </TableRow>
                      )}
                    </>
                  );
                })
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
