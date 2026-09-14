// Controle de Embargos — visão única da Controladoria.
// Uma linha por caso execução + embargos, venha do monitor do tribunal ou das
// Publicações (com e sem pasta). Etapas: em vigilância → sem pasta incidental
// (pendência) → cadastrado no Legal One (fim). Cada linha abre a página própria.

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, Loader2, RefreshCw, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import { EstadoBadge, fmtData, fmtDataHora } from "@/components/minha-equipe/EmbargosExecucaoTab";
import {
  ControleCaso,
  ControleResponse,
  ControleVigilancia,
  Etapa,
  IGNORADA_LABEL,
  ORIGEM_HINT,
  ORIGEM_LABEL,
  getControleStatus,
  listarControle,
  sincronizarControle,
} from "@/services/embargos-controle";

const PAGE_SIZES = [25, 50, 100];
const TODAS = "__todas__";

export function OrigemBadges({ origens }: { origens: string[] }) {
  return (
    <div className="flex flex-wrap gap-1">
      {origens.map((o) => (
        <Badge
          key={o}
          variant="outline"
          title={ORIGEM_HINT[o]}
          className={o === "PUB_NA_PASTA" ? "cursor-help border-red-400 text-red-700" : "cursor-help"}
        >
          {ORIGEM_LABEL[o] ?? o}
        </Badge>
      ))}
    </div>
  );
}

export function SituacaoCaso({ caso }: { caso: ControleCaso }) {
  if (caso.estado === "CADASTRADO") {
    return <Badge className="bg-emerald-600 hover:bg-emerald-600 text-white">Cadastrado · {caso.incidente_folder ?? `id ${caso.incidente_id}`}</Badge>;
  }
  if (caso.estado === "DESCARTADO") {
    return <Badge variant="outline" title={caso.descartado_motivo ?? undefined}>Descartado</Badge>;
  }
  if (caso.falha_cadastro) {
    return (
      <Badge variant="destructive" className="gap-1" title="A publicação caiu na pasta da execução: falta a pasta incidental.">
        <AlertTriangle className="h-3 w-3" /> Falha de cadastro
      </Badge>
    );
  }
  if (!caso.cnj_embargos && caso.origens.includes("PUB_NA_PASTA")) {
    return (
      <Badge variant="outline" className="cursor-help border-amber-500 text-amber-700"
        title="A publicação na pasta da execução fala em embargos à execução, mas o processo apartado não foi identificado.">
        A verificar
      </Badge>
    );
  }
  return <Badge className="bg-amber-500 hover:bg-amber-500 text-white">Sem pasta incidental</Badge>;
}

export default function ControleEmbargosView({ team }: { team: string }) {
  const { toast } = useToast();
  const navigate = useNavigate();
  const [etapa, setEtapa] = useState<Etapa>("pendente");
  const [origem, setOrigem] = useState<string>(TODAS);
  const [soFalha, setSoFalha] = useState(false);
  const [aVerificar, setAVerificar] = useState(false);
  const [semExecucao, setSemExecucao] = useState(false);
  const [busca, setBusca] = useState("");
  const [buscaAplicada, setBuscaAplicada] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [data, setData] = useState<ControleResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(
        await listarControle({
          etapa,
          origem: origem === TODAS ? undefined : origem,
          so_falha: soFalha,
          a_verificar: aVerificar,
          sem_execucao: semExecucao,
          busca: buscaAplicada || undefined,
          limit: pageSize,
          offset: (page - 1) * pageSize,
        }),
      );
    } catch (e) {
      toast({ title: "Erro ao carregar o Controle de Embargos", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [etapa, origem, soFalha, aVerificar, semExecucao, buscaAplicada, page, pageSize, toast]);

  useEffect(() => {
    load();
  }, [load]);

  const rodando = !!data?.status?.running;
  useEffect(() => {
    if (!rodando) return;
    const id = setInterval(async () => {
      try {
        const st = await getControleStatus();
        if (!st.running) {
          clearInterval(id);
          if (st.ultimo?.erro) {
            toast({ title: "Sincronização com problema", description: st.ultimo.erro, variant: "destructive" });
          } else {
            toast({ title: "Controle sincronizado" });
          }
          load();
        }
      } catch {
        /* ignore */
      }
    }, 5000);
    return () => clearInterval(id);
  }, [rodando, load, toast]);

  const sincronizar = async () => {
    try {
      await sincronizarControle();
      toast({ title: "Sincronização disparada", description: "Lê as Publicações e confere no Legal One — roda no servidor." });
      load();
    } catch (e) {
      toast({ title: "Não deu pra sincronizar", description: String((e as Error).message), variant: "destructive" });
    }
  };

  const k = data?.kpis;
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const escolherEtapa = (e: Etapa, filtro?: "falha" | "verificar") => {
    setEtapa(e);
    setSoFalha(filtro === "falha");
    setAVerificar(filtro === "verificar");
    setSemExecucao(false);
    setOrigem(TODAS);
    setPage(1);
  };

  const filtroAtivo = soFalha ? "falha" : aVerificar ? "verificar" : undefined;
  const CARDS: { etapa: Etapa; filtro?: "falha" | "verificar"; label: string; valor: number; hint: string; cls?: string }[] = [
    { etapa: "vigilancia", label: "Em vigilância", valor: k?.vigilancia ?? 0, hint: "Execuções ajuizadas sem embargos encontrados ainda (monitor do tribunal)." },
    { etapa: "pendente", label: "Sem pasta incidental", valor: k?.pendente ?? 0, hint: "Embargos detectados que ainda não têm pasta no Legal One — a pendência da Controladoria.", cls: (k?.pendente ?? 0) > 0 ? "border-amber-500" : "" },
    { etapa: "pendente", filtro: "falha", label: "… falha de cadastro", valor: k?.pendente_falha_cadastro ?? 0, hint: "Existe processo de embargos apartado e a publicação caiu na pasta da execução: falta a pasta incidental.", cls: (k?.pendente_falha_cadastro ?? 0) > 0 ? "border-red-500" : "" },
    { etapa: "pendente", filtro: "verificar", label: "… a verificar", valor: k?.pendente_a_verificar ?? 0, hint: "A publicação na pasta da execução fala em embargos à execução, mas o processo apartado não foi identificado." },
    { etapa: "cadastrado", label: "Cadastrados no Legal One", valor: k?.cadastrado ?? 0, hint: "A pasta dos embargos existe — trabalho feito." },
    { etapa: "descartado", label: "Descartados", valor: k?.descartado ?? 0, hint: "Não eram embargos desta carteira." },
  ];
  const ignoradas = Object.entries(k?.publicacoes_ignoradas ?? {});

  const itens = data?.items ?? [];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {CARDS.map((c) => (
          <Card
            key={c.label}
            role="button"
            title={c.hint}
            onClick={() => escolherEtapa(c.etapa, c.filtro)}
            className={[
              "cursor-pointer transition-colors hover:bg-muted/50",
              c.cls ?? "",
              etapa === c.etapa && filtroAtivo === c.filtro ? "ring-2 ring-primary" : "",
            ].join(" ")}
          >
            <CardContent className="p-3">
              <div className="text-[11px] leading-tight text-muted-foreground">{c.label}</div>
              <div className="text-2xl font-bold tabular-nums">{c.valor}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      {ignoradas.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Fora do controle (publicações lidas e registradas):{" "}
          {ignoradas.map(([o, n]) => `${n} ${IGNORADA_LABEL[o] ?? o}`).join(" · ")}.
        </p>
      )}

      {rodando && (
        <div className="rounded-md border border-sky-300 bg-sky-50 px-3 py-2 text-xs dark:bg-sky-950/30">
          <div className="flex items-center gap-1.5 font-medium text-sky-800 dark:text-sky-200">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Sincronizando: lendo Publicações e conferindo no Legal One
          </div>
          <Progress className="mt-1.5 h-1.5 [&>div]:animate-pulse" value={100} />
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {etapa !== "vigilancia" && (
          <>
            <Select value={origem} onValueChange={(v) => { setOrigem(v); setPage(1); }}>
              <SelectTrigger className="w-64"><SelectValue placeholder="Origem" /></SelectTrigger>
              <SelectContent>
                <SelectItem value={TODAS}>Todas as origens</SelectItem>
                {Object.entries(ORIGEM_LABEL).map(([o, l]) => (
                  <SelectItem key={o} value={o}>
                    {l}{etapa === "pendente" && k ? ` (${k.pendente_por_origem?.[o] ?? 0})` : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {etapa === "pendente" && (
              <Button
                variant={semExecucao ? "default" : "outline"}
                size="sm"
                onClick={() => { setSemExecucao((v) => !v); setPage(1); }}
                title="Casos em que o Flow ainda não sabe de qual execução são os embargos"
              >
                Sem execução identificada ({k?.pendente_sem_execucao ?? 0})
              </Button>
            )}
          </>
        )}
        <form className="flex items-center gap-1" onSubmit={(e) => { e.preventDefault(); setBuscaAplicada(busca); setPage(1); }}>
          <Input className="w-64" placeholder="CNJ, pasta ou embargante" value={busca} onChange={(e) => setBusca(e.target.value)} />
          <Button type="submit" variant="outline" size="icon"><Search className="h-4 w-4" /></Button>
        </form>
        <div className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
          {data?.status?.ultimo?.em && <span>Sincronizado {fmtDataHora(data.status.ultimo.em)}</span>}
          <Button variant="outline" size="sm" onClick={sincronizar} disabled={rodando}>
            {rodando ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-1 h-4 w-4" />}
            Sincronizar agora
          </Button>
        </div>
      </div>

      <Card>
        <CardContent className="overflow-x-auto p-0">
          {etapa === "vigilancia" ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Execução</TableHead>
                  <TableHead>Ajuizamento</TableHead>
                  <TableHead>Próxima consulta</TableHead>
                  <TableHead>Consultas</TableHead>
                  <TableHead>Responsável</TableHead>
                  <TableHead>Estado</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {itens.length === 0 ? (
                  <TableRow><TableCell colSpan={6} className="py-8 text-center text-muted-foreground">{loading ? "Carregando…" : "Nenhuma execução em vigilância."}</TableCell></TableRow>
                ) : (
                  (itens as ControleVigilancia[]).map((it) => (
                    <TableRow key={`e${it.id}`} className="cursor-pointer" onClick={() => navigate(`/minha-equipe/${team}/embargos/${it.execucao_id}`)}>
                      <TableCell className="font-mono text-xs"><div>{it.pasta_execucao}</div><div className="text-muted-foreground">{it.cnj_execucao ?? "—"}</div></TableCell>
                      <TableCell>{fmtData(it.data_ajuizamento)}</TableCell>
                      <TableCell>{fmtData(it.proxima_consulta)}</TableCell>
                      <TableCell className="tabular-nums">{it.consultas_feitas}</TableCell>
                      <TableCell className="text-xs">{it.responsavel_nome ?? "—"}</TableCell>
                      <TableCell><EstadoBadge estado={it.estado} /></TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Embargos</TableHead>
                  <TableHead>Execução</TableHead>
                  <TableHead>Origem</TableHead>
                  <TableHead>Embargante</TableHead>
                  <TableHead>Detectado</TableHead>
                  <TableHead>Tarefa L1</TableHead>
                  <TableHead>Situação</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {itens.length === 0 ? (
                  <TableRow><TableCell colSpan={7} className="py-8 text-center text-muted-foreground">{loading ? "Carregando…" : "Nenhum caso com esses filtros."}</TableCell></TableRow>
                ) : (
                  (itens as ControleCaso[]).map((c) => (
                    <TableRow
                      key={`c${c.id}`}
                      className={`cursor-pointer ${c.falha_cadastro && c.estado === "PENDENTE" ? "bg-red-50 dark:bg-red-950/20" : ""}`}
                      onClick={() => navigate(`/minha-equipe/${team}/embargos/caso/${c.id}`)}
                    >
                      <TableCell className="font-mono text-xs">{c.cnj_embargos ?? <span className="text-muted-foreground">número não identificado</span>}</TableCell>
                      <TableCell className="font-mono text-xs">
                        <div>{c.pasta_execucao ?? "—"}</div>
                        <div className="text-muted-foreground">{c.cnj_execucao ?? (c.execucao_id ? "" : "execução não identificada")}</div>
                      </TableCell>
                      <TableCell><OrigemBadges origens={c.origens} /></TableCell>
                      <TableCell className="max-w-xs text-xs">{c.embargante ?? "—"}</TableCell>
                      <TableCell>{fmtData(c.detectado_em)}</TableCell>
                      <TableCell className="font-mono text-xs">{c.tarefa_l1_id ?? "—"}</TableCell>
                      <TableCell><SituacaoCaso caso={c} /></TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <div className="flex items-center justify-between text-sm">
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">Itens por página</span>
          <Select value={String(pageSize)} onValueChange={(v) => { setPageSize(Number(v)); setPage(1); }}>
            <SelectTrigger className="w-20"><SelectValue /></SelectTrigger>
            <SelectContent>
              {PAGE_SIZES.map((n) => (<SelectItem key={n} value={String(n)}>{n}</SelectItem>))}
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
    </div>
  );
}
