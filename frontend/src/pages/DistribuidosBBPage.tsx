import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Building2,
  FileText,
  Loader2,
  ScrollText,
  Search,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
  Processo,
  getAuditoria,
  listarEventos,
  listarProcessos,
} from "@/services/distribuidos-bb";

const PAGE_SIZES = [25, 50, 100];

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

  const [aba, setAba] = useState<"processos" | "log">("processos");

  // Processos
  const [items, setItems] = useState<Processo[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [statusFiltro, setStatusFiltro] = useState<string>(searchParams.get("status") ?? "");
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
  }, [statusFiltro, busca, page, pageSize, toast]);

  const loadEventos = useCallback(async () => {
    try {
      const resp = await listarEventos({
        secao: secaoFiltro || undefined,
        nivel: nivelFiltro || undefined,
        limit: 100,
        offset: (logPage - 1) * 100,
      });
      setEventos(resp.items);
      setEventosTotal(resp.total);
    } catch (e) {
      toast({ title: "Erro ao carregar log", description: String((e as Error).message), variant: "destructive" });
    }
  }, [secaoFiltro, nivelFiltro, logPage, toast]);

  useEffect(() => {
    if (aba === "processos") loadProcessos();
  }, [aba, loadProcessos]);
  useEffect(() => {
    if (aba === "log") loadEventos();
  }, [aba, loadEventos]);

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

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <Building2 className="h-6 w-6 shrink-0 text-primary" />
            Distribuídos BB
          </h1>
          <p className="text-sm text-muted-foreground">Processos capturados no portal do Banco do Brasil e sua auditoria.</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => navigate("/distribuidos-bb/dashboard")}>
          Ver dashboard
        </Button>
      </div>

      <Tabs value={aba} onValueChange={(v) => setAba(v as "processos" | "log")}>
        <TabsList>
          <TabsTrigger value="processos">
            <FileText className="mr-1.5 h-4 w-4" /> Processos
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
                        <TableCell colSpan={9} className="py-10 text-center">
                          <Loader2 className="mx-auto h-6 w-6 animate-spin text-muted-foreground" />
                        </TableCell>
                      </TableRow>
                    ) : items.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={9} className="py-10 text-center text-muted-foreground">
                          Nenhum processo encontrado.
                        </TableCell>
                      </TableRow>
                    ) : (
                      items.map((p, idx) => (
                        <TableRow key={p.id} className={idx % 2 === 1 ? "bg-muted/20" : undefined}>
                          <TableCell>
                            <StatusBadge status={p.status} />
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

      {aba === "log" && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Select value={secaoFiltro || "__all__"} onValueChange={(v) => { setLogPage(1); setSecaoFiltro(v === "__all__" ? "" : v); }}>
              <SelectTrigger className="w-48">
                <SelectValue placeholder="Seção" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">Todas as seções</SelectItem>
                {["Coleta", "Extração", "Ciência", "Distribuição", "Envolvidos", "Contatos", "Cadastro", "Configuração", "Sessão"].map((s) => (
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
                    ["Escritório", auditoria.processo.escritorio_path ?? "—"],
                    ["Observação", auditoria.processo.observacao ?? "—"],
                    ["Cadastro L1", auditoria.processo.l1_lawsuit_id ? String(auditoria.processo.l1_lawsuit_id) : "—"],
                  ].map(([label, val]) => (
                    <div key={label} className="min-w-0">
                      <div className="text-xs text-muted-foreground">{label}</div>
                      <div className="truncate">{val}</div>
                    </div>
                  ))}
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
    </div>
  );
}
