import { type ReactNode, useCallback, useEffect, useState } from "react";
import {
  AlertTriangle, ArrowRightLeft, CheckCircle2, ChevronDown, ChevronRight, ExternalLink,
  FileSpreadsheet, Loader2, RefreshCw, Search, Undo2, Users, XCircle,
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
  exportarBaseNerc,
  listarPainelVinculos,
  transferirConjunto,
} from "@/services/distribuidos-bb";

const CENARIO_META: Record<string, { label: string; cls: string }> = {
  // O badge diz o CENÁRIO, que é permanente — não o estado da transição, que
  // muda. Dizer "transição pendente" aqui continuava mentindo depois de tudo
  // transferido; quem mostra o estado é a contagem em âmbar logo abaixo, que
  // some quando zera.
  CENARIO_1: { label: "Entrou na equipe", cls: "bg-amber-100 text-amber-800" },
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
  // Processo cuja transferência está em curso — trava só o botão clicado.
  const [transferindo, setTransferindo] = useState<number | null>(null);
  const [exportando, setExportando] = useState(false);

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

  /** Baixa a base NERC em Excel, com o MESMO recorte que estiver na tela.
   *
   *  Não é a "Gerar planilha" do topo da página — aquela monta o arquivo que o
   *  import do Legal One consome pra CRIAR pasta. Esta é relatório: a carteira
   *  como ela está, pasta a pasta. */
  const exportar = async () => {
    setExportando(true);
    try {
      const pastas = await exportarBaseNerc({
        cenario: cenarioFiltro || undefined,
        transicao: transicaoFiltro || undefined,
        busca: busca || undefined,
      });
      toast({
        title: "Planilha gerada",
        description: `${pastas} pasta(s) da base NERC — o processo novo e as pastas vinculadas de cada parte.`,
      });
    } catch (e) {
      toast({ title: "Erro ao gerar a planilha", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setExportando(false);
    }
  };

  /** Move o processo novo E as pastas antigas da parte pro MESMO responsável.
   *
   *  Um clique só, de propósito. Cada botão antigo resolvia o destino por conta
   *  própria consumindo o rodízio: no cenário 1 a fila andava duas vezes e as
   *  pastas da mesma parte acabavam com advogadas diferentes — exatamente o que
   *  a carteira especializada existe pra impedir. O destino continua sendo
   *  decisão do motor; aqui só se executa. */
  const transferirTudo = async (item: PainelVinculoItem) => {
    setTransferindo(item.processo_id);
    try {
      const r = await transferirConjunto(item.processo_id);
      const movidas = r.transferidas + r.ja_estavam;
      if (r.ok) {
        toast({
          title: r.transferidas === 0
            ? "Já estava tudo com o responsável certo"
            : `${movidas} pasta(s) agora com ${r.para}`,
          description: r.transferidas === 0
            ? `As pastas da parte já são conduzidas por ${r.para}.`
            : `Responsável trocado no Legal One e confirmado na releitura.`,
        });
      } else if (movidas === 0) {
        toast({
          title: "Não foi possível transferir",
          description: r.erro ?? r.itens.find((i) => i.erro)?.erro ?? "O Legal One recusou a troca.",
          variant: "destructive",
        });
      } else {
        toast({
          title: `${movidas} transferida(s), ${r.falhas} com problema`,
          description: "As que falharam continuam pendentes, com o motivo na linha.",
          variant: "destructive",
        });
      }
      load();
    } catch (e) {
      toast({ title: "Erro ao transferir", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setTransferindo(null);
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
        {/* Fica aqui, junto dos filtros, e não no topo da página: o que sai na
            planilha é o recorte que os filtros ao lado definem. O botão do topo
            é outra coisa — a planilha de migração que cria pasta no L1. */}
        <Button
          variant="outline"
          onClick={exportar}
          disabled={exportando || loading}
          title="Baixa a carteira NERC em Excel (processo novo + pastas vinculadas), com os filtros atuais"
        >
          {exportando
            ? <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            : <FileSpreadsheet className="mr-2 h-4 w-4" />}
          Gerar planilha (base NERC)
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
                  // O conjunto que o botão move: as pastas antigas pendentes
                  // mais o processo novo, quando ele tem pasta e ainda não está
                  // com o responsável que o motor apontou.
                  const processoEntra = !!item.l1_lawsuit_id
                    && !!item.responsavel_sugerido_nome
                    && item.responsavel_nome !== item.responsavel_sugerido_nome;
                  const totalPastas = pendentes + (processoEntra ? 1 : 0);
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
                          {/* UM botão só. Ele leva o processo desta linha E as
                              pastas antigas da mesma parte pro MESMO responsável
                              — que é a regra da carteira especializada, não
                              economia de tela. Ver `transferirTudo`. */}
                          {item.responsavel_sugerido_nome && totalPastas > 0 && (
                            <>
                            <Button
                              size="sm"
                              className="mt-2 h-7 w-full bg-emerald-600 hover:bg-emerald-700"
                              disabled={transferindo !== null}
                              title={`Passa pro mesmo responsável, de uma vez: ${
                                processoEntra ? "este processo" : "(este processo já está lá)"
                              }${pendentes > 0 ? ` e ${pendentes} pasta(s) antiga(s) da parte` : ""}. ${
                                item.cenario === "CENARIO_2"
                                  ? `Destino: ${item.responsavel_sugerido_nome}, que já conduz a parte.`
                                  : `Destino: a Equipe Mista — a vez é de ${item.responsavel_sugerido_nome}, e a fila alterna a cada transferência.`
                              }`}
                              onClick={(e) => { e.stopPropagation(); transferirTudo(item); }}
                            >
                              {transferindo === item.processo_id
                                ? <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                                : <ArrowRightLeft className="mr-1 h-3 w-3" />}
                              {/* No cenário 2 o destino é uma PESSOA (quem já conduz a
                                  parte). No 1 é a FILA — mostrar um nome fixo aqui
                                  mentiria: espiar não avança o rodízio, então todas as
                                  linhas exibiriam a mesma pessoa, mas na hora do clique
                                  a fila alterna. */}
                              Tudo da parte ({totalPastas}) → {item.cenario === "CENARIO_2"
                                ? item.responsavel_sugerido_nome.split(" ")[0]
                                : "Equipe Mista"}
                            </Button>
                            {item.cenario !== "CENARIO_2" && (
                              <div className="mt-1 text-[10px] text-muted-foreground">
                                rodízio · a vez é de {item.responsavel_sugerido_nome.split(" ")[0]}
                              </div>
                            )}
                            </>
                          )}
                        </TableCell>
                        <TableCell className="text-center font-semibold">{item.vinculos_qtd}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">{fmtData(item.criado_em)}</TableCell>
                      </TableRow>
                      {aberto && (
                        <TableRow key={`${item.processo_id}-det`} className="bg-muted/20 hover:bg-muted/20">
                          <TableCell colSpan={8} className="p-0">
                            <div className="space-y-2 px-10 py-3">
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <div>
                                  <div className="text-xs font-semibold uppercase text-muted-foreground">
                                    Processos vinculados da parte {item.vinculos[0]?.nome_parte ? `— ${item.vinculos[0].nome_parte}` : ""}
                                  </div>
                                  <div className="text-[11px] text-muted-foreground">
                                    Ações aqui mexem nas pastas ANTIGAS listadas abaixo — o processo novo tem botão próprio na linha de cima.
                                  </div>
                                </div>
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
                                          <div className="space-y-1">
                                            {/* Sem botão por pasta: quem move é o
                                                botão único da linha do processo, que
                                                leva a parte inteira pro mesmo
                                                responsável. */}
                                            <Badge variant="secondary" className="bg-amber-100 text-amber-700">
                                              aguardando transferência
                                            </Badge>
                                            {v.transicao_erro && (
                                              <div className="flex items-start gap-1 text-[11px] text-red-600">
                                                <XCircle className="mt-px h-3 w-3 shrink-0" />
                                                <span>{v.transicao_erro}</span>
                                              </div>
                                            )}
                                          </div>
                                        ) : v.transicao_concluida_em ? (
                                          <div className="flex items-center gap-2 text-xs text-emerald-700">
                                            <CheckCircle2 className="h-3.5 w-3.5" />
                                            <span>
                                              {v.transicao_para_nome ? `Transferida para ${v.transicao_para_nome}` : "Concluída"}
                                              {" "}{fmtData(v.transicao_concluida_em)}
                                            </span>
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
