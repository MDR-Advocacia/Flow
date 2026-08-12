// frontend/src/components/performance/RosterEditor.tsx
//
// Manutenção do roster de um time (admin): cargo, time, supervisor e ativo —
// salva na hora (PATCH). Pré-populado com a galera do time. Mover de time tira a
// pessoa da lista (passa a aparecer no dashboard do outro time).

import { useCallback, useEffect, useState } from "react";
import { Loader2, Trash2, UserPlus } from "lucide-react";

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
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import { teamLabel, useTeams } from "@/lib/teams";
import {
  type Candidato,
  type RosterPessoa,
  adicionarPessoa,
  excluirPessoa,
  getCandidatos,
  getRoster,
  updateRosterPessoa,
} from "@/services/performance";

const CARGOS = ["Advogado(a)", "Estagiário(a)", "Assistente", "Supervisor(a)"];

export default function RosterEditor({
  team,
  open,
  onClose,
  onChanged,
}: {
  team: string;
  open: boolean;
  onClose: () => void;
  onChanged?: () => void;
}) {
  const { toast } = useToast();
  // Catálogo dinâmico (admin cria equipe sem deploy) — ver lib/teams.
  const teams = useTeams();
  const [rows, setRows] = useState<RosterPessoa[]>([]);
  const [loading, setLoading] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [cand, setCand] = useState<Candidato[]>([]);
  const [candBusca, setCandBusca] = useState("");
  const [excluirAlvo, setExcluirAlvo] = useState<RosterPessoa | null>(null);

  const fetchRoster = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await getRoster(team));
    } catch (e) {
      toast({ title: "Erro ao carregar o roster", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [team, toast]);

  useEffect(() => {
    if (open) fetchRoster();
  }, [open, fetchRoster]);

  const patch = async (
    id: number,
    updates: Partial<{ cargo: string; equipe: string; is_supervisor: boolean; ativo: boolean }>,
  ) => {
    setDirty(true);
    const moveOut = updates.equipe && updates.equipe !== team;
    const prev = rows;
    if (moveOut) {
      setRows((rs) => rs.filter((r) => r.id !== id));
    } else {
      setRows((rs) => rs.map((r) => (r.id === id ? { ...r, ...updates } : r)));
    }
    try {
      await updateRosterPessoa(id, updates);
      if (moveOut) {
        toast({ title: "Movida de time", description: `Agora em ${teamLabel(updates.equipe!)}.` });
      }
    } catch (e) {
      toast({ title: "Erro ao salvar", description: String((e as Error).message), variant: "destructive" });
      setRows(prev);
    }
  };

  useEffect(() => {
    if (!addOpen) return;
    getCandidatos(team, candBusca || undefined)
      .then(setCand)
      .catch(() => undefined);
  }, [addOpen, candBusca, team]);

  const handleAdd = async (nome: string) => {
    try {
      await adicionarPessoa(nome, team);
      setAddOpen(false);
      setCandBusca("");
      setDirty(true);
      await fetchRoster();
      toast({
        title: "Pessoa adicionada",
        description: `${nome} entrou em ${teamLabel(team)}. Rode "Atualizar agora" no topo pra puxar as tarefas dela.`,
      });
    } catch (e) {
      toast({ title: "Erro ao adicionar", description: String((e as Error).message), variant: "destructive" });
    }
  };

  const handleExcluir = async () => {
    if (!excluirAlvo) return;
    const id = excluirAlvo.id;
    const prev = rows;
    setExcluirAlvo(null);
    setRows((rs) => rs.filter((r) => r.id !== id));
    setDirty(true);
    try {
      await excluirPessoa(id);
      toast({ title: "Pessoa excluída do sistema" });
    } catch (e) {
      toast({ title: "Erro ao excluir", description: String((e as Error).message), variant: "destructive" });
      setRows(prev);
    }
  };

  const close = () => {
    if (dirty) onChanged?.();
    onClose();
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && close()}>
      <DialogContent className="max-h-[90vh] max-w-4xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Ajuste de equipe — {teamLabel(team)}</DialogTitle>
        </DialogHeader>
        <p className="text-xs text-muted-foreground">
          Manutenção do roster — cargo, time, supervisor e ativo. <span className="font-medium">Salva na
          hora.</span> As tarefas seguem a pessoa: mover de time muda onde o trabalho dela aparece.
        </p>

        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-muted-foreground">{rows.length} pessoa(s) no time</span>
          <Popover open={addOpen} onOpenChange={setAddOpen}>
            <PopoverTrigger asChild>
              <Button size="sm" variant="outline" className="h-8 gap-1.5">
                <UserPlus className="h-4 w-4" /> Adicionar pessoa
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-72 p-0" align="end">
              <Command shouldFilter={false}>
                <CommandInput placeholder="Buscar pessoa no L1…" value={candBusca} onValueChange={setCandBusca} />
                <CommandList>
                  <CommandEmpty>Ninguém encontrado.</CommandEmpty>
                  <CommandGroup>
                    {cand.map((c) => (
                      <CommandItem key={c.nome} value={c.nome} onSelect={() => handleAdd(c.nome)}>
                        <span className="truncate">{c.nome}</span>
                        {c.equipe_atual && (
                          <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
                            {teamLabel(c.equipe_atual)}
                          </span>
                        )}
                      </CommandItem>
                    ))}
                  </CommandGroup>
                </CommandList>
              </Command>
            </PopoverContent>
          </Popover>
        </div>

        {loading ? (
          <p className="py-12 text-center text-sm text-muted-foreground">
            <Loader2 className="mr-1 inline h-4 w-4 animate-spin" /> Carregando…
          </p>
        ) : rows.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Ninguém neste time.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Pessoa</TableHead>
                  <TableHead>Cargo</TableHead>
                  <TableHead>Time</TableHead>
                  <TableHead className="text-center">Superv.</TableHead>
                  <TableHead className="text-center">Ativo</TableHead>
                  <TableHead className="w-8" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((p) => {
                  const cargoOpts = p.cargo && !CARGOS.includes(p.cargo) ? [...CARGOS, p.cargo] : CARGOS;
                  return (
                    <TableRow key={p.id} className={p.ativo ? "" : "opacity-50"}>
                      <TableCell>
                        <div className="text-sm font-medium">{p.nome}</div>
                        <div className="text-[10px] text-muted-foreground">
                          {p.concluido} concl · {p.pendente} pend
                        </div>
                      </TableCell>
                      <TableCell>
                        <Select value={p.cargo ?? ""} onValueChange={(v) => patch(p.id, { cargo: v })}>
                          <SelectTrigger className="h-8 w-36 text-xs">
                            <SelectValue placeholder="—" />
                          </SelectTrigger>
                          <SelectContent>
                            {cargoOpts.map((c) => (
                              <SelectItem key={c} value={c}>
                                {c}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </TableCell>
                      <TableCell>
                        <Select value={p.equipe ?? ""} onValueChange={(v) => patch(p.id, { equipe: v })}>
                          <SelectTrigger className="h-8 w-44 text-xs">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {teams.map((t) => (
                              <SelectItem key={t.key} value={t.key}>
                                {t.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </TableCell>
                      <TableCell className="text-center">
                        <Checkbox
                          checked={p.is_supervisor}
                          onCheckedChange={(c) => patch(p.id, { is_supervisor: !!c })}
                        />
                      </TableCell>
                      <TableCell className="text-center">
                        <Checkbox checked={p.ativo} onCheckedChange={(c) => patch(p.id, { ativo: !!c })} />
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          size="icon"
                          variant="ghost"
                          className="h-7 w-7 text-muted-foreground hover:text-rose-600"
                          onClick={() => setExcluirAlvo(p)}
                          title="Excluir do sistema"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}

        <AlertDialog open={excluirAlvo != null} onOpenChange={(o) => !o && setExcluirAlvo(null)}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Excluir {excluirAlvo?.nome}?</AlertDialogTitle>
              <AlertDialogDescription>
                A pessoa sai do sistema (ex.: saiu do escritório). As tarefas dela ficam sem
                responsável — o histórico não é apagado. Não dá pra desfazer.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancelar</AlertDialogCancel>
              <AlertDialogAction onClick={handleExcluir} className="bg-rose-600 hover:bg-rose-700">
                Excluir
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </DialogContent>
    </Dialog>
  );
}
