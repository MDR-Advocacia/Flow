// Menu "Encerramentos": rastro do que o Sistema de Encerramentos executou
// no Legal One via integração — visão de gestão (admin). Listagem paginada
// no padrão da casa (25/50/100), com contadores por desfecho e busca.

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  listarEncerramentosL1,
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
import { Archive, ChevronLeft, ChevronRight, Loader2, Search } from "lucide-react";

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
                    <TableRow key={i.id}>
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
    </div>
  );
}
