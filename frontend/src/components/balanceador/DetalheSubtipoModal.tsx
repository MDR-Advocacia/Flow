// Modal de detalhe de um (colaborador, subtipo): lista as tarefas individuais
// (CNJ, pasta, prazo, situação) e permite transferir as escolhidas a dedo.

import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowRight, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import { type Situacao, type TarefaDetalhe, getDescricoes, getTarefas } from "@/services/balanceador";

const SIT: Record<Situacao, { label: string; cls: string }> = {
  atrasado: { label: "Atrasada", cls: "bg-rose-100 text-rose-700" },
  fatal_hoje: { label: "Fatal hoje", cls: "bg-amber-100 text-amber-800" },
  futuro: { label: "Futuro", cls: "bg-emerald-100 text-emerald-700" },
  sem_prazo: { label: "Sem prazo", cls: "bg-slate-100 text-slate-600" },
};

const p2 = (n: number) => String(n).padStart(2, "0");
function fmtPrazo(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${p2(d.getDate())}/${p2(d.getMonth() + 1)}/${d.getFullYear()} ${p2(d.getHours())}:${p2(d.getMinutes())}`;
}

export default function DetalheSubtipoModal({
  team,
  dias,
  fromPessoa,
  subtipo,
  alvos,
  onTransfer,
  onClose,
  tarefasIniciais,
}: {
  team: string;
  dias: number;
  fromPessoa: { id: number; nome: string };
  subtipo: string;
  alvos: { id: number; nome: string }[];
  onTransfer: (taskIds: number[], toId: number, toNome: string) => void;
  onClose: () => void;
  tarefasIniciais?: TarefaDetalhe[]; // live: já vêm prontas (com descrição), sem novo fetch
}) {
  const { toast } = useToast();
  const [tarefas, setTarefas] = useState<TarefaDetalhe[]>([]);
  const [loading, setLoading] = useState(false);
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [alvo, setAlvo] = useState<string>("");
  const [descMap, setDescMap] = useState<Record<number, string | null>>({});
  const [descLoading, setDescLoading] = useState(false);

  const outros = useMemo(() => alvos.filter((a) => a.id !== fromPessoa.id), [alvos, fromPessoa.id]);

  const load = useCallback(async () => {
    // Live: as tarefas (com descrição) já vieram do /live-pessoa — sem novo fetch.
    if (tarefasIniciais) {
      setTarefas(tarefasIniciais);
      return;
    }
    setLoading(true);
    try {
      const ts = await getTarefas(team, fromPessoa.id, subtipo, dias);
      setTarefas(ts);
      const ids = ts.map((t) => t.l1_task_id).filter((x): x is number => !!x);
      if (ids.length) {
        setDescLoading(true);
        getDescricoes(team, ids)
          .then(setDescMap)
          .catch(() => undefined)
          .finally(() => setDescLoading(false));
      }
    } catch (e) {
      toast({ title: "Erro ao carregar tarefas", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [team, fromPessoa.id, subtipo, dias, toast, tarefasIniciais]);

  useEffect(() => {
    load();
  }, [load]);

  // Radix pode deixar body com pointer-events:none ao desmontar este Dialog
  // aberto — restaura no unmount pra não travar o modal pai.
  useEffect(() => () => {
    document.body.style.pointerEvents = "";
  }, []);

  const toggle = (id: number) =>
    setSel((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });

  const allSelected = tarefas.length > 0 && sel.size === tarefas.filter((t) => t.l1_task_id).length;
  const toggleAll = () =>
    setSel(allSelected ? new Set() : new Set(tarefas.map((t) => t.l1_task_id!).filter(Boolean)));

  const transferir = () => {
    const to = outros.find((o) => String(o.id) === alvo);
    if (!to || sel.size === 0) return;
    onTransfer([...sel], to.id, to.nome);
    onClose();
  };

  return (
    // modal={false}: aninhado dentro do modal de redistribuição — não deve marcar
    // o pai como inert nem travar pointer-events (senão trava o footer/X ao fechar).
    <Dialog open modal={false} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[88vh] max-w-3xl overflow-y-auto" style={{ pointerEvents: "auto" }}>
        <DialogHeader>
          <DialogTitle className="text-base">
            {subtipo}
            <span className="ml-2 text-sm font-normal text-muted-foreground">· {fromPessoa.nome}</span>
          </DialogTitle>
        </DialogHeader>
        <p className="text-xs text-muted-foreground">
          Escolha as tarefas e o destino. Transferência troca <b>responsável + executante</b> (solicitante
          intocado). <span className="text-amber-700">Mock — não escreve no L1 ainda.</span>
        </p>

        {loading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">
            <Loader2 className="mr-1 inline h-4 w-4 animate-spin" /> Carregando…
          </p>
        ) : (
          <>
            <div className="max-h-[48vh] overflow-y-auto rounded-lg border">
              <Table>
                <TableHeader className="sticky top-0 bg-background">
                  <TableRow>
                    <TableHead className="w-8">
                      <Checkbox checked={allSelected} onCheckedChange={toggleAll} />
                    </TableHead>
                    <TableHead>Tarefa (descrição){descLoading && <span className="ml-1 text-[10px] font-normal text-muted-foreground">carregando…</span>}</TableHead>
                    <TableHead>UF</TableHead>
                    <TableHead>Prazo</TableHead>
                    <TableHead>Situação</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tarefas.map((t, i) => (
                    <TableRow key={t.l1_task_id ?? i} className={t.l1_task_id && sel.has(t.l1_task_id) ? "bg-muted/40" : ""}>
                      <TableCell>
                        <Checkbox
                          disabled={!t.l1_task_id}
                          checked={t.l1_task_id ? sel.has(t.l1_task_id) : false}
                          onCheckedChange={() => t.l1_task_id && toggle(t.l1_task_id)}
                        />
                      </TableCell>
                      <TableCell className="max-w-[440px] text-sm">
                        <div className="font-medium leading-snug">
                          {(t.l1_task_id && descMap[t.l1_task_id]) || t.descricao || t.cnj || t.pasta || "—"}
                        </div>
                        <div className="text-[10px] text-muted-foreground">
                          {[t.cnj, t.pasta].filter(Boolean).join(" · ")}
                        </div>
                      </TableCell>
                      <TableCell className="text-xs">{t.uf || "—"}</TableCell>
                      <TableCell className="whitespace-nowrap text-xs tabular-nums">{fmtPrazo(t.prazo)}</TableCell>
                      <TableCell>
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${SIT[t.situacao].cls}`}>
                          {SIT[t.situacao].label}
                        </span>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-3">
              <span className="text-xs text-muted-foreground">
                {sel.size} de {tarefas.length} selecionada(s)
              </span>
              <div className="flex items-center gap-2">
                <Select value={alvo} onValueChange={setAlvo}>
                  <SelectTrigger className="h-9 w-56 text-sm">
                    <SelectValue placeholder="Transferir para…" />
                  </SelectTrigger>
                  <SelectContent>
                    {outros.map((o) => (
                      <SelectItem key={o.id} value={String(o.id)}>
                        {o.nome}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button className="gap-1.5" disabled={sel.size === 0 || !alvo} onClick={transferir}>
                  Transferir <ArrowRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
