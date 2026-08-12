// Aba "Duplicados" do Cadastro de Processo (cliente Ativos).
//
// Lista os CNJs que a Ativos remandou e que JÁ existem no L1 — então não foram
// recadastrados (antes isso era só um contador volátil na barra de progresso).
// Aqui o operador vê QUAIS voltaram, de qual lote, por qual motivo, resolve a
// pasta no L1 (link direto) e seleciona um ou mais pra AGENDAR TAREFA em lote.

import { type ReactNode, useCallback, useEffect, useState } from "react";
import {
  AlertTriangle, CalendarPlus, CheckCircle2, Copy, ExternalLink, Layers,
  Link2, Loader2, RefreshCw, Search,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  type DuplicadoAtivos, type DuplicadosResp,
  listarDuplicadosAtivos, resolverPastasDuplicados,
} from "@/services/distribuidos-bb";
import { useToast } from "@/hooks/use-toast";
import AgendarTarefaDuplicadosDialog from "@/components/distribuidos-bb/AgendarTarefaDuplicadosDialog";

const PAGE = 50;

function KpiCard({
  icone, valor, rotulo, ativo, alerta, onClick,
}: {
  icone: ReactNode; valor: number | undefined; rotulo: string;
  ativo: boolean; alerta?: boolean; onClick: () => void;
}) {
  return (
    <Card
      role="button" tabIndex={0} aria-pressed={ativo} onClick={onClick}
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

export default function DuplicadosAtivosTab() {
  const { toast } = useToast();
  const [data, setData] = useState<DuplicadosResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [resolvendo, setResolvendo] = useState(false);
  const [motivoFiltro, setMotivoFiltro] = useState("");
  const [pastaFiltro, setPastaFiltro] = useState<"" | "com" | "sem">("");
  const [buscaInput, setBuscaInput] = useState("");
  const [busca, setBusca] = useState("");
  const [page, setPage] = useState(1);
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [agendarOpen, setAgendarOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await listarDuplicadosAtivos({
        motivo: motivoFiltro || undefined,
        comPasta: pastaFiltro === "" ? undefined : pastaFiltro === "com",
        busca: busca || undefined,
        limit: PAGE,
        offset: (page - 1) * PAGE,
      });
      setData(resp);
    } catch (e) {
      toast({ title: "Erro ao carregar os duplicados", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [motivoFiltro, pastaFiltro, busca, page, toast]);

  useEffect(() => { load(); }, [load]);

  const kpis = data?.kpis;
  const itens = data?.items ?? [];
  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE));

  // Seleção: só faz sentido agendar em quem tem pasta resolvida.
  const selecionaveis = itens.filter((d) => d.l1_lawsuit_id);
  const selecionados = itens.filter((d) => sel.has(d.id));
  const todosMarcados = selecionaveis.length > 0 && selecionaveis.every((d) => sel.has(d.id));

  const toggle = (d: DuplicadoAtivos) => {
    if (!d.l1_lawsuit_id) return;
    setSel((prev) => {
      const n = new Set(prev);
      if (n.has(d.id)) n.delete(d.id); else n.add(d.id);
      return n;
    });
  };
  const toggleTodos = () => {
    setSel((prev) => {
      const n = new Set(prev);
      if (todosMarcados) selecionaveis.forEach((d) => n.delete(d.id));
      else selecionaveis.forEach((d) => n.add(d.id));
      return n;
    });
  };

  const resolver = async (escopo: "pagina" | "selecao") => {
    const ids = escopo === "selecao"
      ? selecionados.filter((d) => !d.l1_lawsuit_id).map((d) => d.id)
      : itens.filter((d) => !d.l1_lawsuit_id).map((d) => d.id);
    if (!ids.length) {
      toast({ title: "Nada a resolver", description: "Todas as pastas visíveis já estão resolvidas." });
      return;
    }
    setResolvendo(true);
    try {
      const r = await resolverPastasDuplicados({ ids });
      toast({
        title: "Pastas resolvidas",
        description: `${r.resolvidos} encontrada(s) no L1 · ${r.nao_encontrados} sem correspondência.`,
      });
      load();
    } catch (e) {
      toast({ title: "Erro ao resolver pastas", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setResolvendo(false);
    }
  };

  const setFiltro = (fn: () => void) => { fn(); setPage(1); };

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Processos que a Ativos remandou e que <span className="font-medium text-foreground">já existem no Legal One</span> —
        não foram recadastrados. Resolva a pasta pra abrir no L1 e, se precisar, agende tarefa em lote.
      </p>

      {/* KPIs clicáveis (viram filtro; clicar no ativo limpa) */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <KpiCard
          icone={<Layers className="h-8 w-8 text-indigo-500" />}
          valor={kpis?.total} rotulo="Duplicados no total"
          ativo={!motivoFiltro && !pastaFiltro}
          onClick={() => setFiltro(() => { setMotivoFiltro(""); setPastaFiltro(""); })}
        />
        <KpiCard
          icone={<AlertTriangle className="h-8 w-8 text-amber-500" />}
          valor={kpis?.ja_cadastrado} rotulo="Marcados na planilha"
          ativo={motivoFiltro === "JA_CADASTRADO"}
          onClick={() => setFiltro(() => { setPastaFiltro(""); setMotivoFiltro((v) => v === "JA_CADASTRADO" ? "" : "JA_CADASTRADO"); })}
        />
        <KpiCard
          icone={<Copy className="h-8 w-8 text-sky-500" />}
          valor={kpis?.repetido_lote} rotulo="Repetidos de outro lote"
          ativo={motivoFiltro === "REPETIDO_LOTE"}
          onClick={() => setFiltro(() => { setPastaFiltro(""); setMotivoFiltro((v) => v === "REPETIDO_LOTE" ? "" : "REPETIDO_LOTE"); })}
        />
        <KpiCard
          icone={<CheckCircle2 className="h-8 w-8 text-emerald-500" />}
          valor={kpis?.com_pasta} rotulo="Com pasta L1 resolvida"
          ativo={pastaFiltro === "com"}
          onClick={() => setFiltro(() => { setMotivoFiltro(""); setPastaFiltro((v) => v === "com" ? "" : "com"); })}
        />
      </div>

      {/* Filtros + ações */}
      <div className="flex flex-wrap items-center gap-2">
        <Select value={motivoFiltro || "all"} onValueChange={(v) => setFiltro(() => setMotivoFiltro(v === "all" ? "" : v))}>
          <SelectTrigger className="w-[240px]"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Todos os motivos</SelectItem>
            <SelectItem value="JA_CADASTRADO">Marcado como cadastrado na planilha</SelectItem>
            <SelectItem value="REPETIDO_LOTE">Repetido de importação anterior</SelectItem>
          </SelectContent>
        </Select>
        <Select value={pastaFiltro || "all"} onValueChange={(v) => setFiltro(() => setPastaFiltro(v === "all" ? "" : (v as "com" | "sem")))}>
          <SelectTrigger className="w-[200px]"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Pasta L1: todas</SelectItem>
            <SelectItem value="com">Só com pasta resolvida</SelectItem>
            <SelectItem value="sem">Só sem pasta</SelectItem>
          </SelectContent>
        </Select>
        <div className="relative">
          <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            className="w-[240px] pl-8" placeholder="CNJ ou parte"
            value={buscaInput} onChange={(e) => setBuscaInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") setFiltro(() => setBusca(buscaInput)); }}
          />
        </div>
        <Button variant="outline" size="icon" onClick={load} disabled={loading}>
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
        </Button>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => resolver("pagina")} disabled={resolvendo}>
            {resolvendo ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Link2 className="mr-1.5 h-4 w-4" />}
            Resolver pastas desta página
          </Button>
          <Button
            size="sm"
            disabled={selecionados.length === 0}
            onClick={() => setAgendarOpen(true)}
          >
            <CalendarPlus className="mr-1.5 h-4 w-4" />
            Agendar tarefa{selecionados.length ? ` (${selecionados.length})` : ""}
          </Button>
        </div>
      </div>

      {/* Tabela */}
      <div className="rounded-md border">
        <table className="w-full text-sm">
          <thead className="border-b bg-muted/40 text-xs text-muted-foreground">
            <tr>
              <th className="w-10 px-2 py-2">
                <Checkbox checked={todosMarcados} onCheckedChange={toggleTodos}
                  disabled={selecionaveis.length === 0} aria-label="Selecionar todos com pasta" />
              </th>
              <th className="px-2 py-2 text-left">CNJ</th>
              <th className="px-2 py-2 text-left">Parte</th>
              <th className="px-2 py-2 text-left">Motivo</th>
              <th className="px-2 py-2 text-left">Lote</th>
              <th className="px-2 py-2 text-left">Pasta L1</th>
            </tr>
          </thead>
          <tbody>
            {itens.map((d) => (
              <tr key={d.id} className="border-b last:border-0 hover:bg-muted/20">
                <td className="px-2 py-2">
                  <Checkbox
                    checked={sel.has(d.id)} onCheckedChange={() => toggle(d)}
                    disabled={!d.l1_lawsuit_id}
                    aria-label={d.l1_lawsuit_id ? `Selecionar ${d.cnj}` : "Resolva a pasta primeiro"}
                  />
                </td>
                <td className="px-2 py-2 font-mono text-xs">{d.cnj}</td>
                <td className="px-2 py-2">{d.parte || <span className="text-muted-foreground">—</span>}</td>
                <td className="px-2 py-2">
                  <Badge variant="secondary" className={
                    d.motivo === "JA_CADASTRADO"
                      ? "bg-amber-100 text-amber-700 hover:bg-amber-100"
                      : "bg-sky-100 text-sky-700 hover:bg-sky-100"
                  }>
                    {d.motivo_label}
                  </Badge>
                </td>
                <td className="px-2 py-2 text-muted-foreground">#{d.lote_id}</td>
                <td className="px-2 py-2">
                  {d.l1_url ? (
                    <a href={d.l1_url} target="_blank" rel="noreferrer"
                      className="inline-flex items-center gap-1 text-primary hover:underline">
                      {d.l1_folder || `#${d.l1_lawsuit_id}`} <ExternalLink className="h-3 w-3" />
                    </a>
                  ) : (
                    <span className="text-xs text-muted-foreground">não resolvida</span>
                  )}
                </td>
              </tr>
            ))}
            {!itens.length && (
              <tr><td colSpan={6} className="px-2 py-10 text-center text-sm text-muted-foreground">
                {loading ? "Carregando…" : "Nenhum duplicado neste recorte."}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Paginação */}
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>
          {data?.total ?? 0} duplicado(s)
          {selecionados.length > 0 && ` · ${selecionados.length} selecionado(s)`}
        </span>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" disabled={page <= 1 || loading} onClick={() => setPage((p) => p - 1)}>Anterior</Button>
          <span>Página {page} de {totalPages}</span>
          <Button variant="outline" size="sm" disabled={page >= totalPages || loading} onClick={() => setPage((p) => p + 1)}>Próxima</Button>
        </div>
      </div>

      <AgendarTarefaDuplicadosDialog
        open={agendarOpen}
        onOpenChange={setAgendarOpen}
        duplicados={selecionados}
        onDone={() => { setSel(new Set()); load(); }}
      />
    </div>
  );
}
