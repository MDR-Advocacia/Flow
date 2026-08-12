// Menu "Encerramentos": rastro do que o Sistema de Encerramentos executou
// no Legal One via integração — visão de gestão (admin). Listagem paginada
// no padrão da casa (25/50/100), com contadores por desfecho e busca.

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  listarEncerramentosL1,
  exportarEncerramentosExcel,
  EncerramentoL1Item,
  EncerramentoStatus,
} from "@/services/encerramentos";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import {
  Archive, ChevronLeft, ChevronRight, Copy, Download, Loader2, Search,
} from "lucide-react";

const STATUS_LABELS: Record<EncerramentoStatus, string> = {
  ok: "Encerrado",
  ja_encerrado: "Já estava encerrado",
  nao_encontrado: "CNJ não encontrado",
  conflito: "Conflito",
  erro_l1: "Erro no Legal One",
};

const STATUS_BADGES: Record<EncerramentoStatus, string> = {
  ok: "bg-emerald-100 text-emerald-800 border-emerald-200",
  ja_encerrado: "bg-sky-100 text-sky-800 border-sky-200",
  nao_encontrado: "bg-amber-100 text-amber-800 border-amber-200",
  conflito: "bg-orange-100 text-orange-800 border-orange-200",
  erro_l1: "bg-red-100 text-red-800 border-red-200",
};

function formatDateTime(value: string | null): string {
  if (!value) return "-";
  const d = new Date(value);
  return `${d.toLocaleDateString("pt-BR")} ${d.toLocaleTimeString("pt-BR", {
    hour: "2-digit",
    minute: "2-digit",
  })}`;
}

export default function EncerramentosL1Page() {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [statusFiltro, setStatusFiltro] = useState<string>("");
  const [busca, setBusca] = useState("");
  const [buscaDebounced, setBuscaDebounced] = useState("");
  const [detalhe, setDetalhe] = useState<EncerramentoL1Item | null>(null);
  const [exportando, setExportando] = useState(false);
  const { toast } = useToast();

  useEffect(() => {
    const t = setTimeout(() => {
      setBuscaDebounced(busca.trim());
      setPage(1);
    }, 350);
    return () => clearTimeout(t);
  }, [busca]);

  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: ["encerramentos-l1", page, pageSize, statusFiltro, buscaDebounced],
    queryFn: () =>
      listarEncerramentosL1({
        page,
        pageSize,
        status: statusFiltro || undefined,
        q: buscaDebounced || undefined,
      }),
    placeholderData: (prev) => prev,
  });

  const items: EncerramentoL1Item[] = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const contadores = data?.contadores;

  const cards = useMemo(
    () =>
      (Object.keys(STATUS_LABELS) as EncerramentoStatus[]).map((s) => ({
        status: s,
        label: STATUS_LABELS[s],
        n: contadores?.[s] ?? 0,
      })),
    [contadores],
  );

  async function handleExportar() {
    setExportando(true);
    try {
      await exportarEncerramentosExcel({
        status: statusFiltro || undefined,
        q: buscaDebounced || undefined,
      });
      toast({ title: "Exportação concluída", description: "A planilha foi baixada." });
    } catch (e) {
      toast({
        title: "Erro ao exportar",
        description: e instanceof Error ? e.message : "Não foi possível gerar a planilha.",
        variant: "destructive",
      });
    } finally {
      setExportando(false);
    }
  }

  function copiarDetalhe(i: EncerramentoL1Item) {
    const texto = [
      `CNJ: ${i.numero_cnj}`,
      `Lawsuit (L1): ${i.lawsuit_id ?? "-"}`,
      `Desfecho: ${STATUS_LABELS[i.status] ?? i.status}`,
      `Data do encerramento: ${i.data_encerramento ?? "-"}`,
      `Motivo (L1): ${i.motivo_encerramento ?? "-"}`,
      `Operador: ${i.operador_nome ?? "-"} (${i.operador_email ?? "-"})`,
      `Justificativa: ${i.justificativa ?? "-"}`,
      `Recebido em: ${formatDateTime(i.created_at)}`,
      "",
      `Detalhe: ${i.detalhe ?? "-"}`,
    ].join("\n");
    navigator.clipboard.writeText(texto);
    toast({ title: "Copiado", description: "Detalhes na área de transferência." });
  }

  return (
    <div className="space-y-4 p-1">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
          <Archive className="h-6 w-6" /> Encerramentos no Legal One
        </h1>
        <p className="text-muted-foreground">
          Processos encerrados automaticamente via Sistema de Encerramentos
        </p>
      </div>

      {/* Contadores por desfecho (clicáveis = filtro) */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {cards.map(({ status, label, n }) => (
          <button
            key={status}
            type="button"
            onClick={() => {
              setStatusFiltro((f) => (f === status ? "" : status));
              setPage(1);
            }}
            className={`rounded-xl border bg-card p-3 text-left transition-all hover:border-primary/40 ${
              statusFiltro === status ? "ring-2 ring-primary border-primary" : ""
            }`}
          >
            <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              {label}
            </div>
            <div className="mt-1 text-2xl font-bold">{n}</div>
          </button>
        ))}
      </div>

      <Card>
        <CardHeader className="pb-2">
          <div className="flex flex-wrap items-center gap-3">
            <CardTitle className="text-base">Registros</CardTitle>
            <div className="relative ml-auto w-72">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Buscar por CNJ ou operador..."
                value={busca}
                onChange={(e) => setBusca(e.target.value)}
                className="pl-9"
              />
            </div>
            <Button
              variant="outline"
              onClick={handleExportar}
              disabled={exportando || total === 0}
              className="gap-1.5"
              title="Baixa um .xlsx com os encerramentos do recorte atual"
            >
              {exportando ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              Exportar Excel
            </Button>
            <Select
              value={String(pageSize)}
              onValueChange={(v) => {
                setPageSize(Number(v));
                setPage(1);
              }}
            >
              <SelectTrigger className="w-24">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[25, 50, 100].map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {n}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="flex h-40 items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-primary" />
            </div>
          ) : error ? (
            <p className="py-10 text-center text-sm text-red-600">
              {(error as Error).message}
            </p>
          ) : items.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              Nenhum encerramento registrado{statusFiltro || buscaDebounced ? " neste recorte" : " ainda"}.
            </p>
          ) : (
            <div className="relative overflow-x-auto">
              {isFetching && (
                <div className="absolute left-0 right-0 top-0 z-10 h-0.5 overflow-hidden bg-primary/20">
                  <div className="h-full w-1/3 animate-pulse bg-primary" />
                </div>
              )}
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="whitespace-nowrap">Recebido em</TableHead>
                    <TableHead className="min-w-[190px]">CNJ</TableHead>
                    <TableHead>Lawsuit</TableHead>
                    <TableHead>Desfecho</TableHead>
                    <TableHead className="whitespace-nowrap">Dt. Encerramento</TableHead>
                    <TableHead className="min-w-[220px]">Motivo (L1)</TableHead>
                    <TableHead>Operador</TableHead>
                    <TableHead className="min-w-[200px]">Justificativa</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((i) => (
                    <TableRow
                      key={i.id}
                      onClick={() => setDetalhe(i)}
                      className="cursor-pointer hover:bg-muted/50"
                      title="Clique para ver os detalhes"
                    >
                      <TableCell className="whitespace-nowrap text-xs">
                        {formatDateTime(i.created_at)}
                      </TableCell>
                      <TableCell className="font-mono text-sm">{i.numero_cnj}</TableCell>
                      <TableCell className="font-mono text-xs">
                        {i.lawsuit_id ?? "-"}
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant="outline"
                          className={`whitespace-nowrap text-[10px] ${STATUS_BADGES[i.status] ?? ""}`}
                          title={i.detalhe ?? undefined}
                        >
                          {STATUS_LABELS[i.status] ?? i.status}
                        </Badge>
                        {i.detalhe && (
                          <span
                            className="mt-0.5 block max-w-[240px] truncate text-[10px] text-muted-foreground"
                            title={i.detalhe}
                          >
                            {i.detalhe}
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        {i.data_encerramento
                          ? i.data_encerramento.split("-").reverse().join("/")
                          : "-"}
                      </TableCell>
                      <TableCell className="text-xs">{i.motivo_encerramento ?? "-"}</TableCell>
                      <TableCell className="whitespace-nowrap text-xs">
                        {i.operador_nome ?? "-"}
                      </TableCell>
                      <TableCell className="max-w-[260px] truncate text-xs" title={i.justificativa ?? undefined}>
                        {i.justificativa ?? "-"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          {total > pageSize && (
            <div className="flex items-center justify-between border-t px-4 py-3 text-sm">
              <span className="text-muted-foreground">
                {total} registro(s) · página {page} de {totalPages}
              </span>
              <div className="flex gap-1">
                <Button
                  variant="outline"
                  size="icon"
                  className="h-8 w-8"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => p - 1)}
                >
                  <ChevronLeft className="h-4 w-4" />
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  className="h-8 w-8"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => p + 1)}
                >
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
      {/* Detalhes: o desfecho completo, para orientar a correção do cadastro no L1 */}
      <Dialog open={!!detalhe} onOpenChange={(o) => !o && setDetalhe(null)}>
        <DialogContent className="max-w-2xl">
          {detalhe && (
            <>
              <DialogHeader>
                <DialogTitle className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-base">{detalhe.numero_cnj}</span>
                  <Badge
                    variant="outline"
                    className={`text-[10px] ${STATUS_BADGES[detalhe.status] ?? ""}`}
                  >
                    {STATUS_LABELS[detalhe.status] ?? detalhe.status}
                  </Badge>
                </DialogTitle>
              </DialogHeader>

              <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
                <div>
                  <div className="text-xs text-muted-foreground">Lawsuit (L1)</div>
                  <div className="font-mono">{detalhe.lawsuit_id ?? "-"}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">Recebido em</div>
                  <div>{formatDateTime(detalhe.created_at)}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">Data do encerramento</div>
                  <div>
                    {detalhe.data_encerramento
                      ? detalhe.data_encerramento.split("-").reverse().join("/")
                      : "-"}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">Operador</div>
                  <div>{detalhe.operador_nome ?? "-"}</div>
                </div>
                <div className="col-span-2">
                  <div className="text-xs text-muted-foreground">Motivo gravado no Legal One</div>
                  <div>{detalhe.motivo_encerramento ?? "-"}</div>
                </div>
                <div className="col-span-2">
                  <div className="text-xs text-muted-foreground">Justificativa do encerramento</div>
                  <div>{detalhe.justificativa ?? "-"}</div>
                </div>
              </div>

              {detalhe.detalhe && (
                <div className="mt-1">
                  <div className="mb-1 text-xs font-medium text-muted-foreground">
                    Retorno do Legal One
                  </div>
                  <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted/40 p-3 text-xs">
                    {detalhe.detalhe}
                  </pre>
                </div>
              )}

              <div className="flex justify-end gap-2 pt-1">
                <Button variant="outline" size="sm" className="gap-1.5" onClick={() => copiarDetalhe(detalhe)}>
                  <Copy className="h-3.5 w-3.5" /> Copiar detalhes
                </Button>
                <Button size="sm" onClick={() => setDetalhe(null)}>Fechar</Button>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
