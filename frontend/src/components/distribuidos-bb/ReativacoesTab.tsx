// Fila de pastas FECHADAS que o cliente reenviou.
//
// Por que esta tela existe: quando a ingestão (BB, Ativos ou Master) encontra
// pasta do mesmo cliente pro CNJ, ela não recadastra — certo, evita duplicar.
// O que faltava era olhar o STATUS dessa pasta. Baixada/arquivada e ativa
// davam no mesmo "já cadastrado", e o processo saía da fila sem tarefa e sem
// alerta. Na carteira do Banco Master isso é a regra: 6.709 de 8.756 pastas
// estão fechadas, então processo reenviado quase sempre é processo que voltou
// a andar.

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ExternalLink, Loader2, RefreshCw, RotateCcw, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import {
  type ReativacaoItem, dispensarReativacoes, listarReativacoes,
} from "@/services/distribuidos-bb";
import AgendarTarefaDuplicadosDialog from "./AgendarTarefaDuplicadosDialog";

const CLIENTES = [
  { value: "", label: "Todas as carteiras" },
  { value: "MASTER", label: "Banco Master" },
  { value: "BB", label: "Banco do Brasil" },
  { value: "ATIVOS", label: "Ativos" },
];

const PAGE_SIZES = [25, 50, 100];

export default function ReativacoesTab() {
  const { toast } = useToast();
  const [items, setItems] = useState<ReativacaoItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [cliente, setCliente] = useState("");
  const [limit, setLimit] = useState(50);
  const [offset, setOffset] = useState(0);
  const [sel, setSel] = useState<number[]>([]);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [dispensando, setDispensando] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await listarReativacoes({ cliente: cliente || undefined, limit, offset });
      setItems(r.items);
      setTotal(r.total);
      setSel([]);
    } catch (e) {
      toast({
        title: "Erro ao carregar a fila",
        description: String((e as Error).message),
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  }, [cliente, limit, offset, toast]);

  useEffect(() => { load(); }, [load]);

  const todosMarcados = items.length > 0 && sel.length === items.length;
  const toggleTodos = () => setSel(todosMarcados ? [] : items.map((i) => i.id));
  const toggle = (id: number) =>
    setSel((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  const dispensar = async () => {
    if (!sel.length) return;
    setDispensando(true);
    try {
      const r = await dispensarReativacoes(sel);
      toast({
        title: "Tirados da fila",
        description: `${r.dispensados} processo(s) saíram sem reativar. As pastas seguem fechadas.`,
      });
      load();
    } catch (e) {
      toast({
        title: "Erro ao dispensar",
        description: String((e as Error).message),
        variant: "destructive",
      });
    } finally {
      setDispensando(false);
    }
  };

  const paginaAtual = Math.floor(offset / limit) + 1;
  const totalPaginas = Math.max(1, Math.ceil(total / limit));
  const inicio = total === 0 ? 0 : offset + 1;
  const fim = Math.min(offset + items.length, total);

  const linkL1 = (id: number | null) =>
    id ? `https://mdradvocacia.novajus.com.br/processos/processos/edit/${id}` : undefined;

  const selecionados = useMemo(() => sel, [sel]);

  return (
    <div className="space-y-4">
      {/* O alerta é o ponto da tela: antes disso o caso era invisível. */}
      {total > 0 && (
        <div className="flex items-start gap-3 rounded-lg border-2 border-amber-300 bg-amber-50 p-4 dark:border-amber-700 dark:bg-amber-950/40">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
          <div className="space-y-1 text-sm">
            <p className="font-medium text-amber-900 dark:text-amber-200">
              {total} processo(s) que o cliente reenviou já têm pasta no Legal One — mas ela está
              baixada ou arquivada.
            </p>
            <p className="text-amber-800 dark:text-amber-300">
              Não criamos pasta nova (isso duplicaria o processo). Se o processo voltou a andar, a
              pasta precisa ser <strong>reativada</strong> antes de receber trabalho — tarefa em
              pasta arquivada não aparece pra ninguém no dia a dia. Se algum realmente acabou, use
              <strong> Dispensar</strong> pra tirar da fila.
            </p>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Select value={cliente || "__all"} onValueChange={(v) => { setCliente(v === "__all" ? "" : v); setOffset(0); }}>
          <SelectTrigger className="w-56"><SelectValue /></SelectTrigger>
          <SelectContent>
            {CLIENTES.map((c) => (
              <SelectItem key={c.value || "__all"} value={c.value || "__all"}>{c.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          Atualizar
        </Button>

        <div className="ml-auto flex items-center gap-2">
          <Button
            variant="outline" size="sm"
            onClick={dispensar}
            disabled={!selecionados.length || dispensando}
          >
            {dispensando ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <XCircle className="mr-2 h-4 w-4" />}
            Dispensar ({selecionados.length})
          </Button>
          <Button
            size="sm"
            onClick={() => setDialogOpen(true)}
            disabled={!selecionados.length}
          >
            <RotateCcw className="mr-2 h-4 w-4" />
            Reativar e agendar ({selecionados.length})
          </Button>
        </div>
      </div>

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10">
                <Checkbox checked={todosMarcados} onCheckedChange={toggleTodos} aria-label="Selecionar todos" />
              </TableHead>
              <TableHead>Processo</TableHead>
              <TableHead>Carteira</TableHead>
              <TableHead>Status da pasta</TableHead>
              <TableHead>Parte contrária</TableHead>
              <TableHead>Responsável</TableHead>
              <TableHead className="w-20">Pasta</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading && items.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="py-8 text-center text-sm text-muted-foreground">
                  <Loader2 className="mx-auto h-5 w-5 animate-spin" />
                </TableCell>
              </TableRow>
            )}
            {!loading && items.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="py-8 text-center text-sm text-muted-foreground">
                  Nenhuma pasta fechada aguardando decisão. Quando o cliente reenviar um processo
                  que já foi baixado ou arquivado, ele aparece aqui.
                </TableCell>
              </TableRow>
            )}
            {items.map((it) => (
              <TableRow key={it.id} className={sel.includes(it.id) ? "bg-muted/50" : undefined}>
                <TableCell>
                  <Checkbox checked={sel.includes(it.id)} onCheckedChange={() => toggle(it.id)} />
                </TableCell>
                <TableCell className="font-mono text-xs">
                  {it.cnj || "—"}
                  {it.l1_folder && (
                    <div className="text-[11px] text-muted-foreground">{it.l1_folder}</div>
                  )}
                </TableCell>
                <TableCell className="text-xs">{it.cliente}</TableCell>
                <TableCell>
                  <Badge variant="outline" className="border-amber-400 text-amber-700 dark:text-amber-300">
                    {it.l1_status}
                  </Badge>
                </TableCell>
                <TableCell className="max-w-[260px] truncate text-xs" title={it.adverso || ""}>
                  {it.adverso || "—"}
                </TableCell>
                <TableCell className="text-xs">{it.responsavel_nome || "— sem responsável"}</TableCell>
                <TableCell>
                  {linkL1(it.l1_lawsuit_id) && (
                    <a
                      href={linkL1(it.l1_lawsuit_id)}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center text-xs text-sky-600 hover:underline"
                    >
                      abrir <ExternalLink className="ml-1 h-3 w-3" />
                    </a>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Paginação (padrão da casa — catálogo grande nunca sai sem ela) */}
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="text-muted-foreground">
          {inicio}–{fim} de {total} resultado(s)
        </span>
        <div className="flex items-center gap-2">
          <Select value={String(limit)} onValueChange={(v) => { setLimit(Number(v)); setOffset(0); }}>
            <SelectTrigger className="w-24"><SelectValue /></SelectTrigger>
            <SelectContent>
              {PAGE_SIZES.map((n) => <SelectItem key={n} value={String(n)}>{n}/pág.</SelectItem>)}
            </SelectContent>
          </Select>
          <Button
            variant="outline" size="sm"
            onClick={() => setOffset(Math.max(0, offset - limit))}
            disabled={offset === 0 || loading}
          >
            Anterior
          </Button>
          <span className="text-muted-foreground">Página {paginaAtual} de {totalPaginas}</span>
          <Button
            variant="outline" size="sm"
            onClick={() => setOffset(offset + limit)}
            disabled={offset + limit >= total || loading}
          >
            Próxima
          </Button>
        </div>
      </div>

      <AgendarTarefaDuplicadosDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        duplicados={[]}
        modo="reativacao"
        processoIds={selecionados}
        onDone={load}
      />
    </div>
  );
}
