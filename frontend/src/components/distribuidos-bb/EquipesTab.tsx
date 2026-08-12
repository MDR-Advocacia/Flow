import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, ChevronsUpDown, Loader2, Trash2, UserPlus, Users } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { useToast } from "@/hooks/use-toast";
import {
  Classificacao,
  EquipeMembro,
  ResponsavelDistinto,
  UsuarioL1,
  adicionarMembroEquipe,
  listarClassificacoes,
  listarEquipe,
  listarResponsaveisDistintos,
  listarUsuarios,
  removerMembroEquipe,
} from "@/services/distribuidos-bb";

function UserCombo({
  usuarios,
  value,
  onChange,
  placeholder = "Selecionar colaborador…",
}: {
  usuarios: UsuarioL1[];
  value: number | null;
  onChange: (id: number | null) => void;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const selecionado = usuarios.find((u) => u.id === value);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" role="combobox" className="w-full justify-between">
          <span className="truncate">{selecionado?.name ?? placeholder}</span>
          <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[--radix-popover-trigger-width] p-0" align="start">
        <Command>
          <CommandInput placeholder="Buscar colaborador…" />
          <CommandList className="max-h-56">
            <CommandEmpty>Ninguém encontrado.</CommandEmpty>
            <CommandGroup>
              {usuarios.map((u) => (
                <CommandItem key={u.id} value={u.name} onSelect={() => { onChange(u.id === value ? null : u.id); setOpen(false); }}>
                  <span className="min-w-0 flex-1 truncate">{u.name}</span>
                  {value === u.id && <Check className="ml-2 h-4 w-4 shrink-0 text-emerald-600" />}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

export default function EquipesTab() {
  const { toast } = useToast();
  const [responsaveis, setResponsaveis] = useState<ResponsavelDistinto[]>([]);
  const [usuarios, setUsuarios] = useState<UsuarioL1[]>([]);
  const [classificacoes, setClassificacoes] = useState<Classificacao[]>([]);
  const [loading, setLoading] = useState(false);

  const [selecionado, setSelecionado] = useState<number | null>(null);
  const [equipe, setEquipe] = useState<EquipeMembro[]>([]);
  const [carregandoEquipe, setCarregandoEquipe] = useState(false);

  // form de novo membro
  const [novoMembro, setNovoMembro] = useState<number | null>(null);
  const [novaClassif, setNovaClassif] = useState<string>("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [resp, users, classif] = await Promise.all([
        listarResponsaveisDistintos(),
        listarUsuarios(),
        listarClassificacoes(),
      ]);
      setResponsaveis(resp);
      setUsuarios(users);
      setClassificacoes(classif);
      if (classif.length && !novaClassif) setNovaClassif(classif[0].nome);
    } catch (e) {
      toast({ title: "Erro ao carregar", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  const carregarEquipe = useCallback(
    async (respId: number) => {
      setSelecionado(respId);
      setCarregandoEquipe(true);
      try {
        setEquipe(await listarEquipe(respId));
      } catch (e) {
        toast({ title: "Erro ao carregar equipe", description: String((e as Error).message), variant: "destructive" });
      } finally {
        setCarregandoEquipe(false);
      }
    },
    [toast],
  );

  const respAtual = useMemo(() => responsaveis.find((r) => r.user_id === selecionado), [responsaveis, selecionado]);

  const addMembro = async () => {
    if (!selecionado || !novoMembro || !novaClassif) return;
    try {
      await adicionarMembroEquipe({ responsavel_user_id: selecionado, membro_user_id: novoMembro, classificacao: novaClassif });
      setNovoMembro(null);
      await carregarEquipe(selecionado);
      await load(); // atualiza a contagem
      toast({ title: "Membro adicionado" });
    } catch (e) {
      toast({ title: "Erro ao adicionar", description: String((e as Error).message), variant: "destructive" });
    }
  };

  const removerMembro = async (id: number) => {
    try {
      await removerMembroEquipe(id);
      if (selecionado) await carregarEquipe(selecionado);
      await load();
    } catch (e) {
      toast({ title: "Erro ao remover", description: String((e as Error).message), variant: "destructive" });
    }
  };

  if (loading && responsaveis.length === 0) {
    return (
      <div className="py-16 text-center">
        <Loader2 className="mx-auto h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
      {/* Lista de responsáveis */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <Users className="h-4 w-4" /> Responsáveis
          </CardTitle>
          <p className="text-xs text-muted-foreground">Escolha um responsável para definir a equipe de envolvidos dele.</p>
        </CardHeader>
        <CardContent className="p-0">
          {responsaveis.length === 0 ? (
            <p className="p-4 text-sm text-muted-foreground">Nenhum responsável nas filas ainda. Configure os escritórios/filas primeiro.</p>
          ) : (
            <ul className="max-h-[60vh] divide-y overflow-y-auto">
              {responsaveis.map((r) => (
                <li key={r.user_id}>
                  <button
                    type="button"
                    onClick={() => carregarEquipe(r.user_id)}
                    className={`flex w-full items-center justify-between px-3 py-2 text-left text-sm transition-colors hover:bg-muted/60 ${
                      selecionado === r.user_id ? "bg-primary/5 font-medium" : ""
                    }`}
                  >
                    <span className="truncate">{r.nome ?? `#${r.user_id}`}</span>
                    <Badge variant={r.membros > 0 ? "secondary" : "outline"} className="ml-2 shrink-0">
                      {r.membros}
                    </Badge>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* Equipe do responsável selecionado */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">
            {respAtual ? `Equipe de ${respAtual.nome}` : "Equipe / Envolvidos"}
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            Cada membro vira um envolvido do processo (na posição/classificação) sempre que um distribuído cair para este
            responsável.
          </p>
        </CardHeader>
        <CardContent>
          {!selecionado ? (
            <p className="py-8 text-center text-sm text-muted-foreground">Selecione um responsável à esquerda.</p>
          ) : carregandoEquipe ? (
            <div className="py-8 text-center">
              <Loader2 className="mx-auto h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : (
            <>
              {equipe.length === 0 ? (
                <p className="mb-3 text-sm text-muted-foreground">Sem membros ainda.</p>
              ) : (
                <ul className="mb-3 space-y-1">
                  {equipe.map((m) => (
                    <li key={m.id} className="flex items-center justify-between rounded-md border bg-card px-3 py-2 text-sm">
                      <span className="min-w-0 flex-1 truncate">{m.membro_nome ?? `#${m.membro_user_id}`}</span>
                      <Badge variant="secondary" className="mx-2 shrink-0">{m.classificacao}</Badge>
                      <Button size="sm" variant="ghost" className="h-6 w-6 shrink-0 p-0" onClick={() => removerMembro(m.id)} title="Remover">
                        <Trash2 className="h-3 w-3 text-rose-500" />
                      </Button>
                    </li>
                  ))}
                </ul>
              )}

              {/* Adicionar membro */}
              <div className="rounded-md border bg-muted/30 p-3">
                <div className="mb-2 text-xs font-medium">Adicionar membro à equipe</div>
                <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
                  <div className="min-w-0 flex-1 space-y-1">
                    <Label className="text-xs">Colaborador</Label>
                    <UserCombo usuarios={usuarios} value={novoMembro} onChange={setNovoMembro} />
                  </div>
                  <div className="space-y-1 sm:w-52">
                    <Label className="text-xs">Classificação / posição</Label>
                    <select
                      className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                      value={novaClassif}
                      onChange={(e) => setNovaClassif(e.target.value)}
                    >
                      {classificacoes.map((c) => (
                        <option key={c.id} value={c.nome}>{c.nome}</option>
                      ))}
                    </select>
                  </div>
                  <Button onClick={addMembro} disabled={!novoMembro || !novaClassif}>
                    <UserPlus className="mr-2 h-4 w-4" /> Adicionar
                  </Button>
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
