// Seção "Balanceamento de agenda" — vive DENTRO da página Minha Equipe (seção
// recolhível). Diagnóstico de carga por colaborador + redistribuição.
// MOCK (2026-06-29): leitura real do pool; escrita simulada.

import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, ArrowLeftRight, CalendarClock, CalendarPlus, CalendarRange, Clock, HelpCircle, Loader2, Newspaper, Star, X } from "lucide-react";
import { type DateRange } from "react-day-picker";

import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import { type Colaborador, type EntradaDia, type FaixaData, getDiagnosticoCompleto, getEntradas, listarExecucoes } from "@/services/balanceador";
import ExecucoesDialog from "@/components/balanceador/ExecucoesDialog";
import RedistribuicaoModal from "@/components/balanceador/RedistribuicaoModal";

// Data local → YYYY-MM-DD (sem UTC shift — usa o fuso do navegador).
const toISO = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const fmtBR = (d: Date) => `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}/${d.getFullYear()}`;
const addDays = (d: Date, n: number) => {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
};

function cargoBadge(cargo: string | null): string {
  const c = (cargo || "").toLowerCase();
  if (c.includes("superv")) return "bg-indigo-100 text-indigo-700";
  if (c.includes("advog")) return "bg-violet-100 text-violet-700";
  if (c.includes("estag")) return "bg-sky-100 text-sky-700";
  if (c.includes("assist")) return "bg-amber-100 text-amber-700";
  return "bg-slate-100 text-slate-700";
}

function Bar({ a, f, fut }: { a: number; f: number; fut: number }) {
  const tot = a + f + fut || 1;
  return (
    <div className="flex h-2 w-28 overflow-hidden rounded-full bg-muted">
      <div className="bg-rose-500" style={{ width: `${(a / tot) * 100}%` }} />
      <div className="bg-amber-400" style={{ width: `${(f / tot) * 100}%` }} />
      <div className="bg-emerald-400" style={{ width: `${(fut / tot) * 100}%` }} />
    </div>
  );
}

export default function BalanceadorSection({
  team,
  onAplicado,
  snapshotVersion,
}: {
  team: string;
  onAplicado?: () => void;
  /** Instante do último snapshot ingerido. Quando muda, o diagnóstico que
   *  estava aberto precisa ser relido — ele mantém estado próprio e não é
   *  atualizado pelo `load` da página pai. */
  snapshotVersion?: string | null;
}) {
  const { toast } = useToast();
  const [data, setData] = useState<Colaborador[]>([]);
  const [loading, setLoading] = useState(false);
  const [sel, setSel] = useState<Set<number>>(new Set());
  // DOIS recortes de data INDEPENDENTES (a confusão anterior era ter um só):
  //  - tableRange: filtra a TABELA de baixo (análise do estoque por dia). Default
  //    vazio = todas as pendentes.
  //  - redistRange: faixa da REDISTRIBUIÇÃO, escolhida no modal do botão. Default
  //    hoje → hoje+30.
  const [tableRange, setTableRange] = useState<DateRange | undefined>(undefined);
  const [tableCalOpen, setTableCalOpen] = useState(false);
  // Recorte por data de CADASTRO (quando a tarefa CHEGOU) — o acompanhamento
  // que a supervisão faz do que entrou de novo. Independente e combinável com
  // o recorte de conclusão: "cadastrada ontem pra semana que vem" = os dois.
  const [tableCadRange, setTableCadRange] = useState<DateRange | undefined>(undefined);
  const [tableCadOpen, setTableCadOpen] = useState(false);
  // Prévia "o que chegou" (cadastros/dia, últimos 7 dias) — carrega ao abrir
  // o popover do filtro de chegada, não no boot da seção.
  const [entradas, setEntradas] = useState<EntradaDia[] | null>(null);
  const [redistRange, setRedistRange] = useState<DateRange | undefined>(() => {
    const hoje = new Date();
    return { from: hoje, to: addDays(hoje, 30) };
  });
  // Vencidas (prazo < hoje) NÃO entram por padrão: com o calendário fixo, a
  // faixa escolhida é o recorte exato (decisão do operador 2026-07-29).
  const [redistAtrasadas, setRedistAtrasadas] = useState(false);
  // Faixa de CADASTRO na REDISTRIBUIÇÃO (opcional, além da faixa de conclusão).
  const [redistPorCadastro, setRedistPorCadastro] = useState(false);
  const [redistCadRange, setRedistCadRange] = useState<DateRange | undefined>(undefined);
  const [faixaModalOpen, setFaixaModalOpen] = useState(false);
  // Limite do recorte de origem (data do agendamento mais antigo registrado).
  const [publicacoesDesde, setPublicacoesDesde] = useState<string | null>(null);
  // Recorte de origem NA REDISTRIBUIÇÃO — opção B: age só no que vai ser
  // movido, nunca na carga exibida. Desligado por padrão.
  const [redistSoPub, setRedistSoPub] = useState(false);
  const [cargo, setCargo] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [execucoesOpen, setExecucoesOpen] = useState(false);
  const [temRodando, setTemRodando] = useState(false);

  // Faixa da TABELA (pro getDiagnostico) — cada janela é opcional e as duas
  // se combinam (interseção). Conclusão exige o par completo; cadastro idem.
  const tableFaixa = useMemo(() => {
    const f: { inicio?: string; fim?: string; cadInicio?: string; cadFim?: string } = {};
    if (tableRange?.from && tableRange?.to) {
      f.inicio = toISO(tableRange.from);
      f.fim = toISO(tableRange.to);
    }
    if (tableCadRange?.from && tableCadRange?.to) {
      f.cadInicio = toISO(tableCadRange.from);
      f.cadFim = toISO(tableCadRange.to);
    }
    return Object.keys(f).length ? f : null;
  }, [tableRange, tableCadRange]);

  // Sinaliza no botão "Execuções" se há redistribuição rodando em 2º plano
  // (poll leve de 20s — o acompanhamento fino é dentro do painel).
  useEffect(() => {
    let vivo = true;
    const check = () =>
      listarExecucoes(team, 1, 0)
        .then((r) => vivo && setTemRodando(r.items.some((j) => j.status !== "done")))
        .catch(() => undefined);
    check();
    const t = setInterval(check, 20_000);
    return () => {
      vivo = false;
      clearInterval(t);
    };
  }, [team]);

  useEffect(() => {
    if (!tableCadOpen || entradas !== null) return;
    getEntradas(team, 7).then(setEntradas).catch(() => setEntradas([]));
  }, [tableCadOpen, entradas, team]);

  // Troca de time OU de snapshot invalida a prévia (recarrega na próxima
  // abertura). Sem isto, a tabela principal ficava nova mas o popover "o que
  // chegou" continuava exibindo os números do snapshot anterior.
  useEffect(() => { setEntradas(null); }, [team, snapshotVersion]);

  const load = useCallback(async () => {
    setLoading(true);
    setSel(new Set());
    try {
      const dg = await getDiagnosticoCompleto(team, tableFaixa);
      setData(dg.colaboradores);
      setPublicacoesDesde(dg.publicacoes_desde);
    } catch (e) {
      toast({ title: "Erro ao carregar o diagnóstico", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [team, tableFaixa, toast]);

  useEffect(() => {
    load();
  }, [load, snapshotVersion]);

  const cargos = useMemo(
    () => Array.from(new Set(data.map((d) => d.cargo).filter(Boolean))) as string[],
    [data],
  );
  const dataView = useMemo(() => (cargo ? data.filter((d) => d.cargo === cargo) : data), [data, cargo]);
  const totais = useMemo(
    () =>
      dataView.reduce(
        (s, d) => ({
          atrasado: s.atrasado + d.atrasado,
          fatal: s.fatal + d.fatal_hoje,
          futuro: s.futuro + d.futuro,
          pub: s.pub + (d.total_pub ?? 0),
        }),
        { atrasado: 0, fatal: 0, futuro: 0, pub: 0 },
      ),
    [dataView],
  );

  // Parêntese de ORIGEM: quanto daquele número veio de Publicações. Discreto e
  // sempre na MESMA cor da legenda/card — é o que faz o olho ligar as três
  // coisas sem precisar de explicação. Só aparece quando há o que mostrar.
  const Pub = ({ n }: { n?: number }) =>
    n && n > 0 ? <span className="ml-1 text-[11px] font-medium text-violet-600">({n})</span> : null;

  const toggle = (id: number) =>
    setSel((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const selecionados = useMemo(
    () => data.filter((d) => sel.has(d.id)).map((d) => ({ id: d.id, nome: d.nome })),
    [data, sel],
  );

  // Faixa da REDISTRIBUIÇÃO (escolhida no modal do botão Redistribuir).
  const redistFaixa: FaixaData | null = useMemo(() => {
    if (!redistRange?.from || !redistRange?.to) return null;
    const base: FaixaData = {
      inicio: toISO(redistRange.from),
      fim: toISO(redistRange.to),
      incluirAtrasadas: redistAtrasadas,
      apenasPublicacoes: redistSoPub,
    };
    if (redistPorCadastro && redistCadRange?.from && redistCadRange?.to) {
      base.cadInicio = toISO(redistCadRange.from);
      base.cadFim = toISO(redistCadRange.to);
    }
    return base;
  }, [redistRange, redistAtrasadas, redistSoPub, redistPorCadastro, redistCadRange]);
  const rangeLabel = (r: DateRange | undefined, vazio: string) =>
    r?.from ? (r.to ? `${fmtBR(r.from)} – ${fmtBR(r.to)}` : `${fmtBR(r.from)} – …`) : vazio;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          Carga pendente de cada colaborador. Filtre a tabela por data pra analisar o estoque de um dia
          (ou dias anteriores/futuros); selecione quem rebalancear e clique em Redistribuir — a faixa da
          redistribuição é escolhida lá.
          <span className="ml-1 text-emerald-700">Leitura e escrita ao vivo no L1.</span>
        </p>
        <Button size="sm" variant="outline" className="relative h-7 gap-1.5 text-xs" onClick={() => setExecucoesOpen(true)}>
          <Activity className="h-3.5 w-3.5" /> Execuções
          {temRodando && (
            <span className="absolute -right-1 -top-1 flex h-2.5 w-2.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-400 opacity-75" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-blue-500" />
            </span>
          )}
        </Button>
      </div>

      {/* KPIs do time */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div className="rounded-lg border bg-rose-50/50 p-3">
          <div className="flex items-center gap-1.5 text-[11px] text-rose-700"><AlertTriangle className="h-3.5 w-3.5" /> Atrasadas</div>
          <div className="text-2xl font-bold text-rose-700">{totais.atrasado}</div>
        </div>
        <div className="rounded-lg border bg-amber-50/50 p-3">
          <div className="flex items-center gap-1.5 text-[11px] text-amber-800"><CalendarClock className="h-3.5 w-3.5" /> Fatais hoje</div>
          <div className="text-2xl font-bold text-amber-800">{totais.fatal}</div>
        </div>
        <div className="rounded-lg border bg-emerald-50/50 p-3">
          <div className="flex items-center gap-1.5 text-[11px] text-emerald-700"><Clock className="h-3.5 w-3.5" /> Futuras</div>
          <div className="text-2xl font-bold text-emerald-700">{totais.futuro}</div>
        </div>
        {/* Origem Publicações — informativo. NÃO é um quarto balde: é um
            recorte que atravessa os outros três (uma tarefa atrasada de
            Publicações conta nos dois lugares). */}
        <div
          className="rounded-lg border bg-violet-50/50 p-3"
          title={
            publicacoesDesde
              ? `Tarefas criadas pelo módulo de Publicações. Considera agendamentos registrados a partir de ${fmtBR(new Date(publicacoesDesde + "T12:00:00"))} — tarefas de Publicações anteriores a essa data existem na fila, mas não são identificáveis.`
              : "Tarefas criadas pelo módulo de Publicações."
          }
        >
          <div className="flex items-center gap-1.5 text-[11px] text-violet-700">
            <Newspaper className="h-3.5 w-3.5" /> De Publicações
          </div>
          <div className="text-2xl font-bold text-violet-700">{totais.pub}</div>
        </div>
      </div>

      {/* controles de redistribuição */}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-muted/30 px-3 py-2">
        <span className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
          {sel.size > 0 ? `${sel.size} colaborador(es) selecionado(s)` : "Selecione colaboradores na tabela"}
          {sel.size > 0 && (
            <button
              type="button"
              onClick={() => setSel(new Set())}
              className="inline-flex items-center gap-0.5 rounded-full border px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              title="Desmarca todos os colaboradores selecionados"
            >
              <X className="h-3 w-3" /> Limpar seleção
            </button>
          )}
        </span>
        <Button
          size="sm"
          className="gap-1.5"
          disabled={sel.size === 0}
          title={sel.size === 0 ? "Selecione colaboradores na tabela" : "Escolher a faixa e redistribuir"}
          onClick={() => setFaixaModalOpen(true)}
        >
          <ArrowLeftRight className="h-4 w-4" /> Redistribuir
        </Button>
      </div>

      {/* filtro de DATA da tabela — análise do estoque pendente por conclusão
          prevista (hoje, dias anteriores, futuros). Independente da faixa da
          redistribuição. */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium text-muted-foreground">Estoque por data:</span>
        <Popover open={tableCalOpen} onOpenChange={setTableCalOpen}>
          <PopoverTrigger asChild>
            <Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs font-normal"
              title="Filtra a tabela pela DATA DE CONCLUSÃO PREVISTA das pendentes.">
              <CalendarRange className="h-3.5 w-3.5" /> {rangeLabel(tableRange, "Todas as pendentes")}
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-auto p-0" align="start">
            <div className="border-b px-3 py-2 text-[11px] text-muted-foreground">
              Filtra por <b>data de conclusão prevista</b> — veja o que cai num dia ou numa faixa.
            </div>
            <Calendar mode="range" numberOfMonths={2} selected={tableRange}
              onSelect={setTableRange} defaultMonth={tableRange?.from ?? new Date()} />
          </PopoverContent>
        </Popover>
        <span className="ml-2 font-medium text-muted-foreground">Chegada (cadastro):</span>
        <Popover open={tableCadOpen} onOpenChange={setTableCadOpen}>
          <PopoverTrigger asChild>
            <Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs font-normal"
              title="Filtra a tabela pela DATA DE CADASTRO da tarefa — o que CHEGOU no período.">
              <CalendarPlus className="h-3.5 w-3.5 text-sky-600" /> {rangeLabel(tableCadRange, "Qualquer data")}
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-auto p-0" align="start">
            <div className="border-b px-3 py-2 text-[11px] text-muted-foreground">
              Filtra por <b>data de cadastro</b> — o que <b>chegou</b> no período, independente do prazo.
            </div>
            {/* Prévia do que chegou: o número que a supervisão acompanha de
                cabeça, servido pronto ao abrir o filtro. */}
            <div className="border-b px-3 py-2">
              <div className="mb-1 text-[11px] font-semibold text-muted-foreground">
                O que chegou nos últimos 7 dias
              </div>
              {entradas === null ? (
                <div className="py-1 text-[11px] text-muted-foreground">
                  <Loader2 className="mr-1 inline h-3 w-3 animate-spin" /> carregando…
                </div>
              ) : entradas.length === 0 ? (
                <div className="py-1 text-[11px] text-muted-foreground">Sem cadastros no período.</div>
              ) : (
                <div className="space-y-0.5">
                  {entradas.map((e) => {
                    const d = new Date(e.dia + "T12:00:00");
                    const max = Math.max(...entradas.map((x) => x.cadastradas), 1);
                    return (
                      <div key={e.dia} className="flex items-center gap-2 text-[11px] tabular-nums">
                        <span className="w-10 text-muted-foreground">{fmtBR(d).slice(0, 5)}</span>
                        <div className="h-1.5 w-24 overflow-hidden rounded-full bg-muted">
                          <div className="h-full bg-sky-500" style={{ width: `${(e.cadastradas / max) * 100}%` }} />
                        </div>
                        <span className="font-medium">{e.cadastradas}</span>
                        <span className="text-muted-foreground">
                          chegaram · {e.ainda_pendentes} ainda pendentes
                        </span>
                      </div>
                    );
                  })}
                  <div className="pt-1 text-[10px] text-muted-foreground">
                    O dia de hoje aparece parcial — a foto é do início da manhã.
                  </div>
                </div>
              )}
            </div>
            <Calendar mode="range" numberOfMonths={2} selected={tableCadRange}
              onSelect={setTableCadRange} defaultMonth={tableCadRange?.from ?? new Date()} />
          </PopoverContent>
        </Popover>
        <Popover>
          <PopoverTrigger asChild>
            <button type="button"
              className="inline-flex h-6 w-6 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              title="Como funcionam os dois filtros de data?">
              <HelpCircle className="h-4 w-4" />
            </button>
          </PopoverTrigger>
          <PopoverContent className="w-96 text-xs leading-relaxed" align="start">
            <p className="mb-2 font-semibold">Os dois filtros de data, sem mistério</p>
            <p className="mb-2">
              Toda tarefa tem <b>duas datas</b>: o dia em que ela foi{" "}
              <b className="text-sky-700">cadastrada</b> (quando ela <i>chegou</i> pra
              equipe) e o dia em que ela deve ser{" "}
              <b>concluída</b> (o prazo dela).
            </p>
            <p className="mb-2">
              <b>Estoque por data</b> olha o <b>prazo</b>: "o que vence hoje?",
              "o que já venceu?", "o que cai semana que vem?".
            </p>
            <p className="mb-2">
              <b className="text-sky-700">Chegada (cadastro)</b> olha a <b>entrada</b>:
              "o que chegou ontem?", "quanto entrou essa semana?" — é o
              acompanhamento do que é <i>novo</i>, independente do prazo.
            </p>
            <p className="mb-2 rounded-md bg-muted/60 p-2">
              <b>Os dois juntos</b> respondem a pergunta completa:{" "}
              <i>"do que chegou ontem, o que precisa ser feito até semana que
              vem?"</i> — marque a chegada como ontem e o estoque como hoje até
              +7 dias. A tabela mostra só a interseção.
            </p>
            <p className="text-muted-foreground">
              Tarefa <b>concluída não aparece</b> em nenhum dos dois — o
              balanceador só olha pendentes. E a tabela reflete a foto da
              manhã; a redistribuição em si sempre confere ao vivo no L1.
            </p>
          </PopoverContent>
        </Popover>
        {tableFaixa && (
          <button type="button"
            onClick={() => { setTableRange(undefined); setTableCadRange(undefined); }}
            className="inline-flex items-center gap-0.5 rounded-full border px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">
            <X className="h-3 w-3" /> Limpar filtros
          </button>
        )}
      </div>

      {/* filtro por cargo */}
      {cargos.length > 1 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-xs font-medium text-muted-foreground">Cargo:</span>
          {[null, ...cargos].map((c) => {
            const active = cargo === c;
            return (
              <button
                key={c ?? "todos"}
                type="button"
                onClick={() => setCargo(c)}
                className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                  active ? "border-transparent bg-foreground text-background" : "bg-background text-muted-foreground hover:bg-muted"
                }`}
              >
                {c ?? "Todos"}
              </button>
            );
          })}
        </div>
      )}

      {loading ? (
        <p className="py-10 text-center text-sm text-muted-foreground">
          <Loader2 className="mr-1 inline h-4 w-4 animate-spin" /> Carregando…
        </p>
      ) : (
        <>
        <div className="flex items-center gap-1.5 px-0.5 text-[11px] text-muted-foreground">
          <span className="font-semibold text-violet-600">( )</span>
          <span>
            = <span className="font-semibold text-violet-600">origem Publicações</span>, incluído no número ao lado
          </span>
        </div>
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" />
                <TableHead>Colaborador</TableHead>
                <TableHead className="text-right">Atrasadas</TableHead>
                <TableHead className="text-right">Fatais hoje</TableHead>
                <TableHead className="text-right">Futuras</TableHead>
                <TableHead className="text-right">Total</TableHead>
                <TableHead>Mix</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {dataView.map((d) => (
                <TableRow
                  key={d.id}
                  className={`cursor-pointer ${sel.has(d.id) ? "bg-muted/50" : ""}`}
                  onClick={() => toggle(d.id)}
                >
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <Checkbox checked={sel.has(d.id)} onCheckedChange={() => toggle(d.id)} />
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1.5 text-sm font-medium">
                      {d.is_supervisor && <Star className="h-3.5 w-3.5 fill-indigo-400 text-indigo-400" />}
                      {d.nome}
                    </div>
                    {d.cargo && (
                      <span className={`mt-0.5 inline-block rounded-full px-1.5 py-0.5 text-[10px] font-medium ${cargoBadge(d.cargo)}`}>
                        {d.cargo}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {d.atrasado > 0 ? (
                      <><span className="font-semibold text-rose-700">{d.atrasado}</span><Pub n={d.atrasado_pub} /></>
                    ) : "—"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {d.fatal_hoje > 0 ? (
                      <><span className="font-semibold text-amber-800">{d.fatal_hoje}</span><Pub n={d.fatal_hoje_pub} /></>
                    ) : "—"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {d.futuro > 0 ? (<>{d.futuro}<Pub n={d.futuro_pub} /></>) : "—"}
                  </TableCell>
                  <TableCell className="text-right font-semibold tabular-nums">
                    {d.total}<Pub n={d.total_pub} />
                  </TableCell>
                  <TableCell><Bar a={d.atrasado} f={d.fatal_hoje} fut={d.futuro} /></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        </>
      )}

      {/* Modal da FAIXA da redistribuição — abre ao clicar Redistribuir. Deixa
          claro que este recorte é SÓ da redistribuição (o da tabela é outro). */}
      <Dialog open={faixaModalOpen} onOpenChange={setFaixaModalOpen}>
        {/* max-h + rolagem interna: com o calendário de cadastro aberto o
            conteúdo passa da altura da tela e cortava em cima e embaixo —
            os botões do rodapé sumiam junto. */}
        <DialogContent className="max-h-[92vh] max-w-fit overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Faixa da redistribuição</DialogTitle>
            <DialogDescription>
              Quais tarefas entram no rebalanceamento de <b>{selecionados.length}</b> colaborador(es), pela{" "}
              <b>data de conclusão prevista</b>. Entra <b>só o que cai nas datas escolhidas</b>. Não afeta a tabela.
            </DialogDescription>
          </DialogHeader>
          <Calendar mode="range" numberOfMonths={2} selected={redistRange}
            onSelect={setRedistRange} defaultMonth={redistRange?.from} />
          <label className="flex cursor-pointer items-start gap-2 rounded-md border p-2.5 text-sm hover:bg-muted/40">
            <Checkbox
              className="mt-0.5"
              checked={redistAtrasadas}
              onCheckedChange={(c) => setRedistAtrasadas(!!c)}
            />
            <span>
              Puxar também as <b>atrasadas</b>
              <span className="block text-xs text-muted-foreground">
                Inclui as vencidas (conclusão prevista anterior a hoje), mesmo fora da faixa.
              </span>
            </span>
          </label>
          <label className="flex cursor-pointer items-start gap-2 rounded-md border p-2.5 text-sm hover:bg-muted/40">
            <Checkbox
              className="mt-0.5"
              checked={redistSoPub}
              onCheckedChange={(c) => setRedistSoPub(!!c)}
            />
            <span>
              Apenas as de <b className="text-violet-700">Publicações</b>
              <span className="block text-xs text-muted-foreground">
                Move só o que foi agendado pelo módulo de Publicações. A carga da
                tabela não muda — quem está sobrecarregado continua sendo medido
                pela fila inteira.
                {publicacoesDesde && (
                  <> Considera agendamentos a partir de{" "}
                    {fmtBR(new Date(publicacoesDesde + "T12:00:00"))}.</>
                )}
              </span>
            </span>
          </label>
          <label className="flex cursor-pointer items-start gap-2 rounded-md border p-2.5 text-sm hover:bg-muted/40">
            <Checkbox
              className="mt-0.5"
              checked={redistPorCadastro}
              onCheckedChange={(c) => setRedistPorCadastro(!!c)}
            />
            <span className="flex-1">
              Só o que <b className="text-sky-700">chegou</b> num período{" "}
              <span className="text-muted-foreground">(data de cadastro)</span>
              <span className="block text-xs text-muted-foreground">
                Move só as tarefas <b>cadastradas</b> na faixa escolhida — ex.:
                marque ontem pra redistribuir o que entrou ontem. Combina com a
                faixa de conclusão acima (vale a interseção das duas).
              </span>
              {redistPorCadastro && (
                <span className="mt-1.5 block" onClick={(e) => e.preventDefault()}>
                  <Calendar mode="range" numberOfMonths={1} selected={redistCadRange}
                    onSelect={setRedistCadRange} defaultMonth={redistCadRange?.from ?? new Date()} />
                </span>
              )}
            </span>
          </label>
          <div className="text-xs text-muted-foreground">
            Faixa: <span className="font-medium text-foreground">{rangeLabel(redistRange, "escolha início e fim")}</span>
            {redistAtrasadas && <span className="font-medium text-amber-700"> + vencidas</span>}
            {redistSoPub && <span className="font-medium text-violet-700"> · só Publicações</span>}
            {redistPorCadastro && (
              <span className="font-medium text-sky-700">
                {" "}· chegadas em {rangeLabel(redistCadRange, "(escolha a faixa)")}
              </span>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFaixaModalOpen(false)}>Cancelar</Button>
            <Button
              className="gap-1.5"
              disabled={!redistFaixa || (redistPorCadastro && !(redistCadRange?.from && redistCadRange?.to))}
              title={redistPorCadastro && !(redistCadRange?.from && redistCadRange?.to)
                ? "Escolha a faixa de cadastro (ou desmarque a opção)" : undefined}
              onClick={() => { setFaixaModalOpen(false); setModalOpen(true); }}
            >
              <ArrowLeftRight className="h-4 w-4" /> Redistribuir
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {modalOpen && redistFaixa && (
        <RedistribuicaoModal
          team={team}
          pessoas={selecionados}
          faixa={redistFaixa}
          onClose={() => setModalOpen(false)}
          onAplicado={() => {
            // Refresh AUTOMÁTICO da tabela de diagnóstico (Atrasadas/Fatais/
            // Futuras por pessoa): relê o snapshot, que o job já espelhou antes
            // de marcar 'done'. Sem isto o operador precisava dar F5. Também
            // borbulha pro pai (KPIs do topo do Minha Equipe).
            load();
            onAplicado?.();
          }}
        />
      )}

      {execucoesOpen && <ExecucoesDialog team={team} onClose={() => setExecucoesOpen(false)} />}
    </div>
  );
}
