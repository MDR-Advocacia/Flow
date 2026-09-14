// Página própria de um caso do Controle de Embargos: de onde veio, a execução,
// as publicações lidas, a evidência do tribunal e a conferência no Legal One.

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Loader2, RefreshCw, RotateCcw, Search, X } from "lucide-react";
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
import { useToast } from "@/hooks/use-toast";
import { EstadoBadge, fmtData, fmtDataHora } from "@/components/minha-equipe/EmbargosExecucaoTab";
import { OrigemBadges, SituacaoCaso } from "@/components/minha-equipe/ControleEmbargosView";
import { NIVEL_LABEL } from "@/services/embargos-execucao";
import { CasoDetalhe, ORIGEM_LABEL, descartarCaso, getCaso, reabrirCaso, verificarCaso } from "@/services/embargos-controle";

function Info({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="text-sm font-medium">{children}</div>
    </div>
  );
}

export default function EmbargosCasoPage() {
  const { id, team } = useParams();
  const navigate = useNavigate();
  const { toast } = useToast();
  const casoId = Number(id);
  const time = team || "bb-cadastro";
  const [dados, setDados] = useState<CasoDetalhe | null>(null);
  const [loading, setLoading] = useState(false);
  const [acao, setAcao] = useState<string | null>(null);
  const [descartarOpen, setDescartarOpen] = useState(false);
  const [motivo, setMotivo] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setDados(await getCaso(casoId));
    } catch (e) {
      toast({ title: "Erro ao carregar o caso", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [casoId, toast]);

  useEffect(() => {
    load();
  }, [load]);

  const executar = async (nome: string, fn: () => Promise<CasoDetalhe>, ok?: (d: CasoDetalhe) => string) => {
    setAcao(nome);
    try {
      const d = await fn();
      setDados(d);
      if (ok) toast({ title: ok(d) });
    } catch (e) {
      toast({ title: "Não deu certo", description: String((e as Error).message), variant: "destructive" });
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

  const c = dados.caso;
  const exe = dados.execucao;

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <Button variant="ghost" size="sm" className="-ml-2 mb-1 gap-1" onClick={() => navigate(`/minha-equipe/${time}?aba=embargos`)}>
            <ArrowLeft className="h-4 w-4" /> Controle de Embargos
          </Button>
          <h1 className="flex flex-wrap items-center gap-2 text-2xl font-bold tracking-tight">
            <span className="font-mono">{c.cnj_embargos ?? "Embargos sem número identificado"}</span>
            <SituacaoCaso caso={c} />
          </h1>
          <p className="font-mono text-sm text-muted-foreground">
            Execução {c.pasta_execucao ?? "não identificada"} {c.cnj_execucao ? `· ${c.cnj_execucao}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {c.estado === "PENDENTE" && (
            <Button variant="outline" size="sm" disabled={!!acao}
              onClick={() => executar("verificar", () => verificarCaso(casoId),
                (d) => (d.cadastrado_agora ? "Pasta encontrada no Legal One — caso encerrado" : "Pasta dos embargos ainda não existe no Legal One"))}>
              {acao === "verificar" ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Search className="mr-1 h-4 w-4" />}
              Conferir no Legal One agora
            </Button>
          )}
          {c.estado === "DESCARTADO" ? (
            <Button variant="outline" size="sm" disabled={!!acao} onClick={() => executar("reabrir", () => reabrirCaso(casoId), () => "Caso reaberto")}>
              <RotateCcw className="mr-1 h-4 w-4" /> Reabrir
            </Button>
          ) : c.estado === "PENDENTE" ? (
            <Button variant="outline" size="sm" disabled={!!acao} onClick={() => setDescartarOpen(true)}>
              <X className="mr-1 h-4 w-4" /> Descartar
            </Button>
          ) : null}
          <Button variant="ghost" size="icon" onClick={load} disabled={loading} title="Recarregar">
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </Button>
        </div>
      </div>

      {c.falha_cadastro && c.estado === "PENDENTE" && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          Embargos identificados: o processo dos embargos foi identificado no tribunal e a intimação chegou na pasta da
          execução, porque a pasta dos embargos ainda não existe no Legal One.
        </div>
      )}

      <Card>
        <CardHeader className="pb-2"><CardTitle className="text-base">Caso</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <Info label="Origens"><OrigemBadges origens={c.origens} /></Info>
          <Info label="Primeira origem">{c.primeira_origem ? ORIGEM_LABEL[c.primeira_origem] ?? c.primeira_origem : "—"}</Info>
          <Info label="Detectado em">{fmtDataHora(c.detectado_em)}</Info>
          <Info label="Embargante">{c.embargante ?? "—"}</Info>
          <Info label="Tarefa criada no L1">{c.tarefa_l1_id ?? "—"}</Info>
          <Info label="Pasta dos embargos">{c.incidente_folder ?? "ainda não existe"}</Info>
          <Info label="Cadastrado em">{fmtDataHora(c.cadastrado_em)}</Info>
          <Info label="Última conferência no L1">{fmtDataHora(c.verificado_l1_em)}</Info>
          {c.descartado_motivo && <Info label="Motivo do descarte">{c.descartado_motivo}</Info>}
        </CardContent>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-base">Execução</CardTitle></CardHeader>
          <CardContent>
            {exe ? (
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-4">
                  <Info label="Pasta">{exe.pasta}</Info>
                  <Info label="Estado no monitor"><EstadoBadge estado={exe.estado} /></Info>
                  <Info label="Ajuizamento">{fmtData(exe.data_ajuizamento)}</Info>
                  <Info label="Vara">{exe.orgao_nome ?? "—"}</Info>
                  <Info label="Responsável">{exe.responsavel_nome ?? "—"}</Info>
                </div>
                <Button variant="outline" size="sm" onClick={() => navigate(`/minha-equipe/${time}/embargos/${exe.id}`)}>
                  Abrir a execução no monitor
                </Button>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                {c.pasta_execucao
                  ? `Execução ${c.pasta_execucao} identificada pelo Legal One, mas fora do monitor (entrou antes do corte).`
                  : "O Flow ainda não sabe de qual execução são estes embargos. Ele tenta pela vara e pelo nome do embargante uma vez por dia."}
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-base">Evidência do tribunal</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm">
            {dados.candidato ? (
              <>
                <div className="flex flex-wrap gap-2">
                  <Badge variant="outline">{NIVEL_LABEL[dados.candidato.nivel] ?? dados.candidato.nivel}</Badge>
                  <Badge variant="outline">Decisão: {dados.candidato.decisao.toLowerCase()}</Badge>
                </div>
                <div>{dados.candidato.orgao_nome} · ajuizado {fmtData(dados.candidato.data_ajuizamento)}</div>
                {dados.candidato.djen_trecho && (
                  <p className="rounded bg-muted/50 p-2 text-xs">
                    {dados.candidato.djen_data ? <strong>DJEN {fmtData(dados.candidato.djen_data)}: </strong> : null}
                    {dados.candidato.djen_trecho}
                  </p>
                )}
              </>
            ) : (
              <p className="text-muted-foreground">Não veio pelo monitor do tribunal.</p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="pb-2"><CardTitle className="text-base">Publicações ({dados.publicacoes.length})</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          {dados.publicacoes.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nenhuma publicação ligada a este caso.</p>
          ) : (
            dados.publicacoes.map((p) => (
              <div key={p.id} className="rounded border p-2 text-sm">
                <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <span>{fmtData(p.data_publicacao)}</span>
                  <Badge variant="outline">{ORIGEM_LABEL[p.origem] ?? p.origem}</Badge>
                  <span>publicação #{p.publicacao_id}</span>
                  {p.status_publicacao && <span>· {p.status_publicacao.toLowerCase()}</span>}
                </div>
                <p className="text-xs">{p.trecho ?? "—"}</p>
              </div>
            ))
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2"><CardTitle className="text-base">Linha do tempo</CardTitle></CardHeader>
        <CardContent>
          <ol className="max-h-96 space-y-2 overflow-y-auto pr-1">
            {dados.eventos.map((ev) => (
              <li key={ev.id} className="border-l-2 pl-3 text-sm"
                style={{ borderColor: ev.nivel === "ERRO" ? "#dc2626" : ev.nivel === "AVISO" ? "#f59e0b" : "#94a3b8" }}>
                <div className="text-[11px] text-muted-foreground">{fmtDataHora(ev.criado_em)} · {ev.secao.toLowerCase()}</div>
                <div>{ev.mensagem}</div>
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>

      <AlertDialog open={descartarOpen} onOpenChange={setDescartarOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Descartar este caso?</AlertDialogTitle>
            <AlertDialogDescription>Use quando não forem embargos desta carteira. Dá pra reabrir depois.</AlertDialogDescription>
          </AlertDialogHeader>
          <Input placeholder="Motivo (obrigatório)" value={motivo} onChange={(ev) => setMotivo(ev.target.value)} />
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction disabled={!motivo.trim()}
              onClick={() => executar("descartar", () => descartarCaso(casoId, motivo), () => "Caso descartado")}>
              Descartar
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
