// Página dedicada de uma execução no Fluxo Embargos à Execução.
// Tudo o que o Flow sabe dela: agenda, partes do portal BB, embargos
// candidatos com a evidência (DJEN/DataJud), decisões e a linha do tempo.

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Check, ListChecks, Loader2, RefreshCw, Save, Search, Send, Users, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/hooks/use-toast";
import { EstadoBadge, fmtData, fmtDataHora } from "@/components/minha-equipe/EmbargosExecucaoTab";
import {
  EmbargosCandidato,
  EmbargosDetalhe,
  EmbargosPrevia,
  NIVEL_LABEL,
  PARTES_LABEL,
  PRIORIDADE_LABEL,
  ajustarEmbargos,
  consultarEmbargosAgora,
  decidirCandidatoEmbargos,
  dispararTarefasEmbargos,
  encerrarEmbargos,
  getEmbargosDetalhe,
  previaTarefasEmbargos,
  reabrirEmbargos,
  recoletarPartesEmbargos,
} from "@/services/embargos-execucao";

const JANELAS = [15, 20, 25];

function NivelBadge({ c }: { c: EmbargosCandidato }) {
  const cls: Record<string, string> = {
    CONFIRMADO_DJEN: "bg-red-600 hover:bg-red-600 text-white",
    PROVAVEL: "bg-amber-500 hover:bg-amber-500 text-white",
    DESCARTADO: "bg-slate-100 text-slate-500 hover:bg-slate-100 line-through",
  };
  return (
    <Badge variant={cls[c.nivel] ? "default" : "outline"} className={cls[c.nivel] ?? ""}>
      {c.nivel === "DESCARTADO" && c.djen_status === "EMBARGADO_OUTRO" ? NIVEL_LABEL.DESCARTADO_EMBARGADO : NIVEL_LABEL[c.nivel] ?? c.nivel}
    </Badge>
  );
}

function Info({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="text-sm font-medium">{children}</div>
    </div>
  );
}

export default function EmbargosExecucaoDetalhePage() {
  const { id, team } = useParams();
  const navigate = useNavigate();
  const { toast } = useToast();
  const execId = Number(id);
  const [dados, setDados] = useState<EmbargosDetalhe | null>(null);
  const [loading, setLoading] = useState(false);
  const [acao, setAcao] = useState<string | null>(null);
  const [anotacao, setAnotacao] = useState("");
  const [encerrarOpen, setEncerrarOpen] = useState(false);
  const [motivo, setMotivo] = useState("");
  const [trechoAberto, setTrechoAberto] = useState<number | null>(null);
  const [previa, setPrevia] = useState<EmbargosPrevia | null>(null);
  const [carregandoPrevia, setCarregandoPrevia] = useState(false);
  const [confirmarDisparo, setConfirmarDisparo] = useState(false);
  const [mostrarDescartados, setMostrarDescartados] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const voltar = `/minha-equipe/${team || "bb-cadastro"}?aba=embargos`;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await getEmbargosDetalhe(execId);
      setDados(d);
      setAnotacao(d.execucao.anotacao ?? "");
      return d;
    } catch (e) {
      toast({ title: "Erro ao carregar a execução", description: String((e as Error).message), variant: "destructive" });
      return null;
    } finally {
      setLoading(false);
    }
  }, [execId, toast]);

  useEffect(() => {
    load();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [load]);

  const executar = async (nome: string, fn: () => Promise<unknown>, okMsg?: string) => {
    setAcao(nome);
    try {
      const r = await fn();
      if (r && typeof r === "object" && "execucao" in (r as object)) setDados(r as EmbargosDetalhe);
      else await load();
      if (okMsg) toast({ title: okMsg });
    } catch (e) {
      toast({ title: "Não deu certo", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setAcao(null);
    }
  };

  const consultarAgora = async () => {
    const antes = dados?.execucao.ultima_consulta_em ?? null;
    await executar("consultar", () => consultarEmbargosAgora(execId), "Consulta ao tribunal em andamento");
    // A consulta roda no servidor: acompanha até a trilha registrar o resultado.
    let voltas = 0;
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      voltas += 1;
      const d = await getEmbargosDetalhe(execId).catch(() => null);
      if (d) setDados(d);
      if ((d && d.execucao.ultima_consulta_em !== antes) || voltas > 40) {
        if (pollRef.current) clearInterval(pollRef.current);
      }
    }, 4000);
  };

  const carregarPrevia = async () => {
    setCarregandoPrevia(true);
    try {
      const p = await previaTarefasEmbargos(execId);
      setPrevia(p);
      if (p.incidente) load();
    } catch (err) {
      toast({ title: "Não deu pra montar a prévia", description: String((err as Error).message), variant: "destructive" });
    } finally {
      setCarregandoPrevia(false);
    }
  };

  const disparar = async () => {
    setConfirmarDisparo(false);
    setAcao("disparo");
    try {
      const d = await dispararTarefasEmbargos(execId);
      setDados(d);
      const r = d.resultado_disparo;
      toast({
        title: r && r.falhas ? "Disparo com falhas" : "Tarefas criadas no Legal One",
        description: r ? `${r.criadas} criada(s), ${r.falhas} com falha, ${r.puladas} já existia(m).` : undefined,
        variant: r && r.falhas ? "destructive" : undefined,
      });
      setPrevia(await previaTarefasEmbargos(execId).catch(() => null));
    } catch (err) {
      toast({ title: "Não deu pra disparar", description: String((err as Error).message), variant: "destructive" });
    } finally {
      setAcao(null);
    }
  };

  if (!dados) {
    return (
      <div className="flex items-center gap-2 p-6 text-muted-foreground">
        {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : null} Carregando…
      </div>
    );
  }

  const e = dados.execucao;
  const podeConsultar = e.estado === "AGUARDANDO_JANELA" || e.estado === "MONITORANDO";
  const encerrado = ["ENCERRADO", "SEM_EMBARGOS", "CONFIRMADO", "CONCLUIDO", "JA_CADASTRADO"].includes(e.estado);
  const mostrarTarefas = ["CONFIRMADO", "JA_CADASTRADO", "CONCLUIDO"].includes(e.estado) || dados.disparos.length > 0;
  const demandadas = dados.partes.filter((p) => p.demandada);
  const descartados = dados.candidatos.filter((c) => c.nivel === "DESCARTADO");
  const candidatosVisiveis = mostrarDescartados ? dados.candidatos : dados.candidatos.filter((c) => c.nivel !== "DESCARTADO");

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <Button variant="ghost" size="sm" className="-ml-2 mb-1 gap-1" onClick={() => navigate(voltar)}>
            <ArrowLeft className="h-4 w-4" /> Embargos à Execução
          </Button>
          <h1 className="flex flex-wrap items-center gap-2 text-2xl font-bold tracking-tight">
            <span className="font-mono">{e.pasta}</span> <EstadoBadge estado={e.estado} />
          </h1>
          <p className="font-mono text-sm text-muted-foreground">
            {e.cnj || "sem CNJ"} {e.npj ? `· NPJ ${e.npj}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {podeConsultar && (
            <Button variant="outline" size="sm" onClick={consultarAgora} disabled={!!acao}>
              {acao === "consultar" ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Search className="mr-1 h-4 w-4" />}
              Consultar tribunal agora
            </Button>
          )}
          {e.npj && (
            <Button variant="outline" size="sm" disabled={!!acao}
              onClick={() => executar("partes", () => recoletarPartesEmbargos(execId), "Coleta das partes disparada no portal do BB — atualize em alguns minutos")}>
              <Users className="mr-1 h-4 w-4" /> Coletar partes de novo
            </Button>
          )}
          {encerrado ? (
            <Button variant="outline" size="sm" disabled={!!acao}
              onClick={() => executar("reabrir", () => reabrirEmbargos(execId), "Execução devolvida à agenda")}>
              <RefreshCw className="mr-1 h-4 w-4" /> Reabrir
            </Button>
          ) : (
            <Button variant="outline" size="sm" disabled={!!acao} onClick={() => setEncerrarOpen(true)}>
              <X className="mr-1 h-4 w-4" /> Tirar do fluxo
            </Button>
          )}
          <Button variant="ghost" size="icon" onClick={load} disabled={loading} title="Recarregar">
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </Button>
        </div>
      </div>

      {(e.ultimo_erro || (e.partes_status === "ERRO" && e.partes_erro)) && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          {e.ultimo_erro && <div>Última consulta: {e.ultimo_erro}</div>}
          {e.partes_status === "ERRO" && e.partes_erro && (
            <div>Portal do BB ({e.partes_tentativas} tentativa(s)): {e.partes_erro}</div>
          )}
        </div>
      )}

      {/* Agenda e capa */}
      <Card>
        <CardHeader className="pb-2"><CardTitle className="text-base">Agenda do monitoramento</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <Info label="Ajuizamento">{fmtData(e.data_ajuizamento)}</Info>
          <Info label="Janela">
            <Select
              value={String(e.dias_uteis_janela)}
              onValueChange={(v) => executar("janela", () => ajustarEmbargos(execId, { dias_uteis_janela: Number(v) }), "Janela atualizada")}
              disabled={!!acao || encerrado}
            >
              <SelectTrigger className="h-8 w-36"><SelectValue /></SelectTrigger>
              <SelectContent>
                {JANELAS.map((n) => (
                  <SelectItem key={n} value={String(n)}>{n} dias úteis</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Info>
          <Info label="Início do monitoramento">{fmtData(e.inicio_monitoramento)}</Info>
          <Info label="Próxima consulta">{fmtData(e.proxima_consulta)}</Info>
          <Info label="Consultas feitas">{e.consultas_feitas} {e.ultima_consulta_em ? `· última ${fmtDataHora(e.ultima_consulta_em)}` : ""}</Info>
          <Info label="Vara (DataJud)">{e.orgao_nome ? `${e.orgao_nome}${e.tribunal ? ` · ${e.tribunal}` : ""}` : "—"}</Info>
          <Info label="Escritório">{e.escritorio || e.cliente || "—"}</Info>
          <Info label="Responsável">{e.responsavel_nome || "—"}</Info>
          <Info label="Entrou por">{e.origem === "RELATORIO" ? "Relatório do L1" : e.origem === "PLANILHA" ? "Planilha" : "Inclusão manual"} · {fmtData(e.criado_em)}</Info>
          <Info label="Tarefas de protocolo (L1)">{dados.l1_task_ids.length ? dados.l1_task_ids.join(", ") : "—"}</Info>
          {e.encontrado_em && <Info label="Embargos encontrados em">{fmtDataHora(e.encontrado_em)} {e.aviso_enviado_em ? "· aviso registrado (ver trilha)" : ""}</Info>}
          {e.incidente_folder && <Info label="Incidente no L1">{e.incidente_folder} · {e.incidente_cnj || "sem CNJ"}</Info>}
        </CardContent>
      </Card>

      {/* Candidatos */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Embargos à execução na vara ({dados.candidatos.length - descartados.length})</CardTitle>
          <p className="text-xs text-muted-foreground">
            Processos da classe Embargos à Execução na mesma vara, ajuizados depois da execução. O DJEN confirma
            quando o embargante é uma das partes demandadas; sem publicação ainda, a dependência + petição no mesmo dia
            na execução é o sinal provável. Embargado que não é o cliente da execução é descartado automaticamente.
          </p>
          {descartados.length > 0 && (
            <button className="text-left text-xs text-sky-700 underline" onClick={() => setMostrarDescartados((v) => !v)}>
              {mostrarDescartados ? "Esconder" : "Mostrar"} {descartados.length} descartado(s) automaticamente
            </button>
          )}
        </CardHeader>
        <CardContent className="overflow-x-auto p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>CNJ dos embargos</TableHead>
                <TableHead>Ajuizamento</TableHead>
                <TableHead>Evidência</TableHead>
                <TableHead>Partes no DJEN</TableHead>
                <TableHead>Decisão</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {candidatosVisiveis.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="py-6 text-center text-muted-foreground">
                    {!e.consultas_feitas ? "O tribunal ainda não foi consultado."
                      : descartados.length ? `Nenhum candidato em aberto — ${descartados.length} descartado(s) automaticamente.`
                      : "Nenhum embargo na vara desde a execução."}
                  </TableCell>
                </TableRow>
              ) : (
                candidatosVisiveis.map((c) => (
                  <Fragment key={c.id}>
                    <TableRow className={c.decisao === "CONFIRMADO" ? "bg-emerald-50 dark:bg-emerald-950/20" : ""}>
                      <TableCell className="font-mono text-xs">{c.cnj}</TableCell>
                      <TableCell>{fmtData(c.data_ajuizamento)}</TableCell>
                      <TableCell className="space-y-1">
                        <NivelBadge c={c} />
                        {(c.l1_folder || c.l1_litigation_id) && (
                          <Badge className="bg-emerald-100 text-emerald-900 hover:bg-emerald-100">
                            Já no Legal One · {c.l1_folder ?? `id ${c.l1_litigation_id}`}
                          </Badge>
                        )}
                        <div className="flex flex-wrap gap-1">
                          {c.distribuicao_dependencia && <Badge variant="outline">por dependência</Badge>}
                          {c.peticao_mesmo_dia && <Badge variant="outline">petição na execução no dia</Badge>}
                          {c.djen_status && <Badge variant="outline" title={c.djen_consultado_em ? `DJEN consultado em ${fmtDataHora(c.djen_consultado_em)}` : undefined}>DJEN: {c.djen_status.toLowerCase().replace(/_/g, " ")}</Badge>}
                        </div>
                      </TableCell>
                      <TableCell className="text-xs">
                        <div><span className="text-muted-foreground">Embargante: </span>{(c.djen_embargantes ?? []).length ? (c.djen_embargantes ?? []).join(", ") : "—"}</div>
                        {(c.djen_embargados ?? []).length > 0 && (
                          <div className={c.djen_status === "EMBARGADO_OUTRO" ? "text-red-700" : "text-muted-foreground"}>
                            Embargado: {(c.djen_embargados ?? []).join(", ")}
                          </div>
                        )}
                        {c.djen_trecho && (
                          <button className="ml-1 text-sky-700 underline" onClick={() => setTrechoAberto(trechoAberto === c.id ? null : c.id)}>
                            {trechoAberto === c.id ? "ocultar" : "ver trecho"}
                          </button>
                        )}
                      </TableCell>
                      <TableCell>
                        {c.decisao === "PENDENTE" ? (
                          <div className="flex gap-1">
                            <Button size="sm" className="h-7 gap-1" disabled={!!acao}
                              onClick={() => executar("decisao", () => decidirCandidatoEmbargos(execId, c.id, "CONFIRMADO"), "Vínculo confirmado — monitoramento encerrado")}>
                              <Check className="h-3.5 w-3.5" /> É desta execução
                            </Button>
                            <Button size="sm" variant="outline" className="h-7 gap-1" disabled={!!acao}
                              onClick={() => executar("decisao", () => decidirCandidatoEmbargos(execId, c.id, "RECUSADO"), "Candidato recusado")}>
                              <X className="h-3.5 w-3.5" /> Não é
                            </Button>
                          </div>
                        ) : (
                          <Badge variant={c.decisao === "CONFIRMADO" ? "default" : "secondary"}>
                            {c.decisao === "CONFIRMADO" ? "Confirmado" : "Recusado"} {c.decidido_em ? `· ${fmtData(c.decidido_em)}` : ""}
                          </Badge>
                        )}
                      </TableCell>
                    </TableRow>
                    {trechoAberto === c.id && c.djen_trecho && (
                      <TableRow>
                        <TableCell colSpan={5} className="bg-muted/40 text-xs">
                          {c.djen_data ? <strong>DJEN {fmtData(c.djen_data)}: </strong> : null}
                          {c.djen_trecho}
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {mostrarTarefas && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base"><ListChecks className="h-4 w-4" /> Incidente e tarefas</CardTitle>
            <p className="text-xs text-muted-foreground">
              O cadastro do incidente é manual no Legal One e no portal do BB. Depois de cadastrar, localize o incidente
              aqui e dispare as tarefas dos <button className="text-sky-700 underline" onClick={() => navigate(`/minha-equipe/${team || "bb-cadastro"}/embargos/templates`)}>templates</button>.
              {e.estado === "JA_CADASTRADO" && " O incidente já existia antes do fluxo — confira se as tarefas não foram criadas à mão (tarefa aberta do mesmo subtipo não é duplicada)."}
            </p>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="outline" size="sm" onClick={carregarPrevia} disabled={carregandoPrevia || !!acao}>
                {carregandoPrevia ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Search className="mr-1 h-4 w-4" />}
                Localizar incidente e ver prévia
              </Button>
              <Button size="sm" onClick={() => setConfirmarDisparo(true)}
                disabled={!previa?.incidente || !previa.tarefas.some((t) => !t.ja_disparada && !t.erro) || !!acao}>
                <Send className="mr-1 h-4 w-4" /> Disparar tarefas
              </Button>
              {(previa?.incidente || e.incidente_folder) && (
                <span className="text-sm">
                  Incidente: <span className="font-mono">{previa?.incidente?.folder || e.incidente_folder}</span>
                  {(previa?.incidente?.cnj || e.incidente_cnj) ? ` · ${previa?.incidente?.cnj || e.incidente_cnj}` : ""}
                </span>
              )}
              {previa && !previa.incidente && (
                <span className="text-sm text-amber-700">Nenhum incidente de embargos na pasta {e.pasta} ainda — cadastre no Legal One.</span>
              )}
            </div>
            {previa && previa.tarefas.length === 0 && (
              <p className="text-sm text-muted-foreground">Nenhum template ativo — configure na tela de templates.</p>
            )}
            {previa && previa.tarefas.length > 0 && (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Tarefa</TableHead>
                      <TableHead>Responsável</TableHead>
                      <TableHead>Prazo</TableHead>
                      <TableHead>Descrição</TableHead>
                      <TableHead>Situação</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {previa.tarefas.map((t) => (
                      <TableRow key={t.template_id}>
                        <TableCell className="text-xs"><div className="font-medium">{t.nome}</div><div className="text-muted-foreground">{t.subtipo_nome}</div></TableCell>
                        <TableCell className="text-xs">{t.responsavel_nome || <span className="text-red-600">{t.erro}</span>}</TableCell>
                        <TableCell>{fmtData(t.prazo)} · {PRIORIDADE_LABEL[t.prioridade] ?? t.prioridade}</TableCell>
                        <TableCell className="max-w-md text-xs">{t.descricao}</TableCell>
                        <TableCell>
                          {t.ja_disparada ? <Badge variant="secondary">Criada · {t.l1_task_id}</Badge>
                            : t.erro ? <Badge variant="destructive">Não sai</Badge> : <Badge variant="outline">Vai ser criada</Badge>}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
            {dados.disparos.length > 0 && (
              <div className="overflow-x-auto">
                <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Histórico de disparos</div>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Quando</TableHead>
                      <TableHead>Template</TableHead>
                      <TableHead>Tarefa no L1</TableHead>
                      <TableHead>Prazo</TableHead>
                      <TableHead>Resultado</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {dados.disparos.map((d) => (
                      <TableRow key={d.id}>
                        <TableCell className="text-xs">{fmtDataHora(d.criado_em)}</TableCell>
                        <TableCell className="text-xs">{d.template_nome}</TableCell>
                        <TableCell className="font-mono text-xs">{d.l1_task_id ?? "—"}</TableCell>
                        <TableCell>{fmtData(d.prazo)}</TableCell>
                        <TableCell className="text-xs">
                          {d.status === "CRIADA" ? <Badge variant="secondary">Criada</Badge> : <Badge variant="destructive">Falhou</Badge>}
                          {d.erro && <div className="mt-0.5 text-muted-foreground">{d.erro}</div>}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        {/* Partes */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">
              Partes {demandadas.length ? `· ${demandadas.length} demandada(s)` : ""}
            </CardTitle>
            <p className="text-xs text-muted-foreground">
              Portal do BB: {PARTES_LABEL[e.partes_status] ?? e.partes_status}
              {e.partes_em ? ` · ${fmtDataHora(e.partes_em)}` : ""}
            </p>
          </CardHeader>
          <CardContent className="overflow-x-auto p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Nome</TableHead>
                  <TableHead>CPF/CNPJ</TableHead>
                  <TableHead>Polo</TableHead>
                  <TableHead>Fonte</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {dados.partes.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={4} className="py-6 text-center text-muted-foreground">Sem partes lidas ainda.</TableCell>
                  </TableRow>
                ) : (
                  dados.partes.map((p) => (
                    <TableRow key={p.id} className={p.demandada ? "" : "text-muted-foreground"}>
                      <TableCell>
                        {p.nome} {p.demandada && <Badge variant="secondary" className="ml-1">demandada</Badge>}
                      </TableCell>
                      <TableCell className="font-mono text-xs">{p.cpf_cnpj || "—"}</TableCell>
                      <TableCell>{p.polo || "—"}</TableCell>
                      <TableCell>{p.origem === "DJEN" ? "Intimações (DJEN)" : "Portal BB"}</TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        {/* Anotação + trilha */}
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-base">Anotação e linha do tempo</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <div className="flex gap-2">
              <Textarea value={anotacao} onChange={(ev) => setAnotacao(ev.target.value)} placeholder="Anotação da Controladoria" rows={2} />
              <Button variant="outline" size="icon" title="Salvar anotação" disabled={!!acao}
                onClick={() => executar("anotacao", () => ajustarEmbargos(execId, { anotacao }), "Anotação salva")}>
                <Save className="h-4 w-4" />
              </Button>
            </div>
            <ol className="max-h-96 space-y-2 overflow-y-auto pr-1">
              {dados.eventos.map((ev) => (
                <li key={ev.id} className="border-l-2 pl-3 text-sm" style={{ borderColor: ev.nivel === "ERRO" ? "#dc2626" : ev.nivel === "AVISO" ? "#f59e0b" : "#94a3b8" }}>
                  <div className="text-[11px] text-muted-foreground">{fmtDataHora(ev.criado_em)} · {ev.secao.toLowerCase()}</div>
                  <div>{ev.mensagem}</div>
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>
      </div>

      <AlertDialog open={confirmarDisparo} onOpenChange={setConfirmarDisparo}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Criar as tarefas no Legal One?</AlertDialogTitle>
            <AlertDialogDescription>
              {previa?.tarefas.filter((t) => !t.ja_disparada && !t.erro).length ?? 0} tarefa(s) no incidente {previa?.incidente?.folder}.
              As que já foram criadas não se repetem.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction onClick={disparar}>Criar tarefas</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={encerrarOpen} onOpenChange={setEncerrarOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Tirar a execução do fluxo?</AlertDialogTitle>
            <AlertDialogDescription>O Flow para de consultar o tribunal. Dá pra reabrir depois.</AlertDialogDescription>
          </AlertDialogHeader>
          <Input placeholder="Motivo (opcional)" value={motivo} onChange={(ev) => setMotivo(ev.target.value)} />
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => executar("encerrar", () => encerrarEmbargos(execId, motivo || undefined), "Execução tirada do fluxo")}
            >
              Tirar do fluxo
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
