import { useCallback, useEffect, useState } from "react";
import { Check, ChevronsUpDown, Info, Loader2, Plus, Power, Trash2, UserPlus } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { useToast } from "@/hooks/use-toast";
import {
  Classificacao,
  GrupoAjuizamento,
  UsuarioL1,
  adicionarMembroGrupo,
  criarGrupoAjuizamento,
  editarGrupoAjuizamento,
  listarClassificacoes,
  listarGruposAjuizamento,
  listarUsuarios,
  removerGrupoAjuizamento,
  removerMembroGrupo,
} from "@/services/distribuidos-bb";

function UserCombo({ usuarios, value, onChange }: { usuarios: UsuarioL1[]; value: number | null; onChange: (id: number | null) => void }) {
  const [open, setOpen] = useState(false);
  const sel = usuarios.find((u) => u.id === value);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" role="combobox" className="w-full justify-between">
          <span className="truncate">{sel?.name ?? "Colaborador…"}</span>
          <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[--radix-popover-trigger-width] p-0" align="start">
        <Command>
          <CommandInput placeholder="Buscar…" />
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

export default function GruposAjuizamentoTab() {
  const { toast } = useToast();
  const [grupos, setGrupos] = useState<GrupoAjuizamento[]>([]);
  const [usuarios, setUsuarios] = useState<UsuarioL1[]>([]);
  const [classificacoes, setClassificacoes] = useState<Classificacao[]>([]);
  const [loading, setLoading] = useState(false);
  const [novoGrupo, setNovoGrupo] = useState("");

  // form de membro por grupo
  const [addPara, setAddPara] = useState<number | null>(null);
  const [novoMembro, setNovoMembro] = useState<number | null>(null);
  const [novaClassif, setNovaClassif] = useState("Advogado Ajuizamento");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [g, u, c] = await Promise.all([listarGruposAjuizamento(), listarUsuarios(), listarClassificacoes()]);
      setGrupos(g); setUsuarios(u); setClassificacoes(c);
    } catch (e) {
      toast({ title: "Erro ao carregar", description: String((e as Error).message), variant: "destructive" });
    } finally { setLoading(false); }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  const criar = async () => {
    if (!novoGrupo.trim()) return;
    try { await criarGrupoAjuizamento(novoGrupo.trim()); setNovoGrupo(""); load(); toast({ title: "Grupo criado" }); }
    catch (e) { toast({ title: "Erro", description: String((e as Error).message), variant: "destructive" }); }
  };
  const alternar = async (g: GrupoAjuizamento) => { try { await editarGrupoAjuizamento(g.id, { ativo: !g.ativo }); load(); } catch (e) { toast({ title: "Erro", description: String((e as Error).message), variant: "destructive" }); } };
  const removerGrupo = async (g: GrupoAjuizamento) => {
    if (!confirm(`Remover o grupo "${g.nome}"?`)) return;
    try { await removerGrupoAjuizamento(g.id); load(); } catch (e) { toast({ title: "Erro", description: String((e as Error).message), variant: "destructive" }); }
  };
  const addMembro = async (grupoId: number) => {
    if (!novoMembro || !novaClassif) return;
    try { await adicionarMembroGrupo({ grupo_id: grupoId, membro_user_id: novoMembro, classificacao: novaClassif }); setNovoMembro(null); setAddPara(null); load(); }
    catch (e) { toast({ title: "Erro ao adicionar", description: String((e as Error).message), variant: "destructive" }); }
  };
  const removerMembro = async (id: number) => { try { await removerMembroGrupo(id); load(); } catch (e) { toast({ title: "Erro", description: String((e as Error).message), variant: "destructive" }); } };

  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2 rounded-md border p-3 text-sm text-muted-foreground">
        <Info className="mt-0.5 h-4 w-4 shrink-0" />
        <p>Duplas (advogado + assistente) aplicadas como envolvidos quando a observação é <strong>Ajuizamento</strong>. Os grupos ativos são <strong>alternados</strong> (rodízio) entre os processos de ajuizamento.</p>
      </div>
      <div className="flex items-center gap-2">
        <Input className="w-64" placeholder="Nome do novo grupo" value={novoGrupo} onChange={(e) => setNovoGrupo(e.target.value)} onKeyDown={(e) => e.key === "Enter" && criar()} />
        <Button size="sm" onClick={criar}><Plus className="mr-2 h-4 w-4" /> Novo grupo</Button>
      </div>

      {loading && grupos.length === 0 ? (
        <div className="py-12 text-center"><Loader2 className="mx-auto h-6 w-6 animate-spin text-muted-foreground" /></div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {grupos.map((g) => (
            <Card key={g.id} className={g.ativo ? "" : "opacity-60"}>
              <CardHeader className="pb-2 flex-row items-center justify-between space-y-0">
                <CardTitle className="flex items-center gap-2 text-base">
                  {g.nome}
                  {!g.ativo && <Badge variant="outline">Inativo</Badge>}
                </CardTitle>
                <div className="flex gap-1">
                  <Button size="sm" variant="ghost" onClick={() => alternar(g)} title={g.ativo ? "Desativar" : "Ativar"}><Power className="h-3.5 w-3.5" /></Button>
                  <Button size="sm" variant="ghost" onClick={() => removerGrupo(g)} title="Remover"><Trash2 className="h-3.5 w-3.5 text-rose-500" /></Button>
                </div>
              </CardHeader>
              <CardContent>
                {g.membros.length === 0 ? (
                  <p className="mb-2 text-sm text-muted-foreground">Sem membros.</p>
                ) : (
                  <ul className="mb-2 space-y-1">
                    {g.membros.map((m) => (
                      <li key={m.id} className="flex items-center justify-between rounded-md border bg-card px-2 py-1 text-sm">
                        <span className="min-w-0 flex-1 truncate">{m.nome ?? `#${m.membro_user_id}`}</span>
                        <Badge variant="secondary" className="mx-2 shrink-0">{m.classificacao}</Badge>
                        <Button size="sm" variant="ghost" className="h-6 w-6 shrink-0 p-0" onClick={() => removerMembro(m.id)}><Trash2 className="h-3 w-3 text-rose-500" /></Button>
                      </li>
                    ))}
                  </ul>
                )}
                {addPara === g.id ? (
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
                    <div className="min-w-0 flex-1"><Label className="text-xs">Colaborador</Label><UserCombo usuarios={usuarios} value={novoMembro} onChange={setNovoMembro} /></div>
                    <div className="sm:w-48"><Label className="text-xs">Classificação</Label>
                      <select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={novaClassif} onChange={(e) => setNovaClassif(e.target.value)}>
                        {classificacoes.map((c) => <option key={c.id} value={c.nome}>{c.nome}</option>)}
                      </select>
                    </div>
                    <Button size="sm" onClick={() => addMembro(g.id)} disabled={!novoMembro}>Adicionar</Button>
                    <Button size="sm" variant="ghost" onClick={() => { setAddPara(null); setNovoMembro(null); }}>Cancelar</Button>
                  </div>
                ) : (
                  <Button size="sm" variant="outline" onClick={() => { setAddPara(g.id); setNovoMembro(null); }}><UserPlus className="mr-2 h-3.5 w-3.5" /> Adicionar membro</Button>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
