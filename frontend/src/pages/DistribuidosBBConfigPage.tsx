import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Check, ChevronsUpDown, Loader2, Plug, Plus, Power, RotateCcw, Settings, Trash2, UserPlus } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/hooks/use-toast";
import EquipesTab from "@/components/distribuidos-bb/EquipesTab";
import RegrasObservacaoTab from "@/components/distribuidos-bb/RegrasObservacaoTab";
import GruposAjuizamentoTab from "@/components/distribuidos-bb/GruposAjuizamentoTab";
import ClassificacoesTab from "@/components/distribuidos-bb/ClassificacoesTab";
import ValoresPadraoTab from "@/components/distribuidos-bb/ValoresPadraoTab";
import {
  Escritorio,
  EscritorioPayload,
  UsuarioL1,
  adicionarResponsavel,
  criarEscritorio,
  desativarEscritorio,
  editarEscritorio,
  listarEscritorios,
  listarUsuarios,
  removerResponsavel,
  rodarSeed,
  testarOnelog,
} from "@/services/distribuidos-bb";

// Combobox de usuário reutilizável (por id interno do Flow)
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
                <CommandItem
                  key={u.id}
                  value={u.name}
                  onSelect={() => {
                    onChange(u.id === value ? null : u.id);
                    setOpen(false);
                  }}
                >
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

const POLOS = ["Passivo", "Ativo", "Neutro"];
// O cliente vem da porta de entrada do processo (coleta RPA = BB; "Importar lista
// (Ativos)" = ATIVOS). Sem ele o roteamento mandaria o Ativos pra fila do BB —
// os dois têm escritório "Réu" com polo Passivo.
const CLIENTES_ESC = [
  { valor: "", rotulo: "Qualquer cliente" },
  { valor: "BB", rotulo: "Banco do Brasil" },
  { valor: "ATIVOS", rotulo: "Ativos" },
];

export default function DistribuidosBBConfigPage() {
  const navigate = useNavigate();
  const { toast } = useToast();
  const [escritorios, setEscritorios] = useState<Escritorio[]>([]);
  const [usuarios, setUsuarios] = useState<UsuarioL1[]>([]);
  const [loading, setLoading] = useState(false);

  // Dialog de novo/editar escritório
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editando, setEditando] = useState<Escritorio | null>(null);
  const [form, setForm] = useState<EscritorioPayload>({});

  // Add responsável (por escritório)
  const [addRespPara, setAddRespPara] = useState<number | null>(null);
  const [novoRespUserId, setNovoRespUserId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [escs, users] = await Promise.all([listarEscritorios(), listarUsuarios()]);
      setEscritorios(escs);
      setUsuarios(users);
    } catch (e) {
      toast({ title: "Erro ao carregar", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  const abrirNovo = () => {
    setEditando(null);
    setForm({ ativo: true, ordem: (escritorios.at(-1)?.ordem ?? 0) + 1 });
    setDialogOpen(true);
  };
  const abrirEdicao = (esc: Escritorio) => {
    setEditando(esc);
    setForm({
      nome: esc.nome,
      escritorio_path: esc.escritorio_path,
      criterio_cliente: esc.criterio_cliente,
      criterio_polo: esc.criterio_polo,
      criterio_natureza: esc.criterio_natureza,
      responsavel_fixo_user_id: esc.responsavel_fixo_user_id,
      observacao_padrao: esc.observacao_padrao,
      ativo: esc.ativo,
      ordem: esc.ordem,
    });
    setDialogOpen(true);
  };

  const salvar = async () => {
    if (!form.nome?.trim() || !form.escritorio_path?.trim()) {
      toast({ title: "Preencha nome e caminho", variant: "destructive" });
      return;
    }
    try {
      if (editando) {
        await editarEscritorio(editando.id, form);
        toast({ title: "Escritório atualizado" });
      } else {
        await criarEscritorio(form);
        toast({ title: "Escritório criado" });
      }
      setDialogOpen(false);
      load();
    } catch (e) {
      toast({ title: "Erro ao salvar", description: String((e as Error).message), variant: "destructive" });
    }
  };

  const desativar = async (esc: Escritorio) => {
    if (!confirm(`Desativar o escritório "${esc.nome}"? Ele deixa de rotear novos processos.`)) return;
    try {
      await desativarEscritorio(esc.id);
      toast({ title: "Escritório desativado" });
      load();
    } catch (e) {
      toast({ title: "Erro", description: String((e as Error).message), variant: "destructive" });
    }
  };

  const addResp = async (escritorio_id: number) => {
    if (!novoRespUserId) return;
    try {
      await adicionarResponsavel({ escritorio_id, user_id: novoRespUserId });
      setNovoRespUserId(null);
      setAddRespPara(null);
      load();
    } catch (e) {
      toast({ title: "Erro ao adicionar", description: String((e as Error).message), variant: "destructive" });
    }
  };

  const removerResp = async (id: number) => {
    try {
      await removerResponsavel(id);
      load();
    } catch (e) {
      toast({ title: "Erro ao remover", description: String((e as Error).message), variant: "destructive" });
    }
  };

  const [testando, setTestando] = useState(false);
  const testarConexao = async () => {
    setTestando(true);
    try {
      const r = await testarOnelog();
      if (r.ok) {
        toast({
          title: "OneLog conectado ✓",
          description: `Login OK como ${r.usuario ?? "robô"} — ${r.cookies} cookie(s) recebido(s).`,
        });
      } else {
        toast({
          title: r.configurado ? "OneLog respondeu com problema" : "OneLog não configurado",
          description: r.erro ?? "Falha desconhecida.",
          variant: "destructive",
        });
      }
    } catch (e) {
      toast({ title: "Erro ao testar OneLog", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setTestando(false);
    }
  };

  const seed = async () => {
    try {
      const res = await rodarSeed(false);
      toast({
        title: res.criado ? "Configuração inicial criada" : "Já existe configuração",
        description:
          res.nao_resolvidos && res.nao_resolvidos.length
            ? `${res.nao_resolvidos.length} nome(s) não casaram — ajuste manualmente.`
            : undefined,
      });
      load();
    } catch (e) {
      toast({ title: "Erro no seed", description: String((e as Error).message), variant: "destructive" });
    }
  };

  return (
    <div className="container mx-auto space-y-6 p-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold">
            <Settings className="h-6 w-6 text-[hsl(var(--dunatech-blue))]" />
            Cadastro de Processo — Configuração
          </h1>
          <p className="text-sm text-muted-foreground">
            Escritórios, filas de responsáveis e regras. Tudo o que era fixo no robô agora é editável aqui, com base no
            escritório responsável.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => navigate("/distribuidos-bb/dashboard")}>
            Voltar ao painel
          </Button>
          <Button variant="outline" size="sm" onClick={testarConexao} disabled={testando}>
            {testando ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plug className="mr-2 h-4 w-4" />}
            Testar OneLog
          </Button>
          <Button variant="outline" size="sm" onClick={seed}>
            <RotateCcw className="mr-2 h-4 w-4" />
            Padrões do robô
          </Button>
          <Button size="sm" onClick={abrirNovo}>
            <Plus className="mr-2 h-4 w-4" />
            Novo escritório
          </Button>
        </div>
      </div>

      <Tabs defaultValue="escritorios">
        <TabsList className="flex-wrap">
          <TabsTrigger value="escritorios">Escritórios &amp; Filas</TabsTrigger>
          <TabsTrigger value="equipes">Equipes / Envolvidos</TabsTrigger>
          <TabsTrigger value="regras">Regras de Observação</TabsTrigger>
          <TabsTrigger value="grupos">Grupos de Ajuizamento</TabsTrigger>
          <TabsTrigger value="classificacoes">Classificações</TabsTrigger>
          <TabsTrigger value="valores">Valores Padrão</TabsTrigger>
        </TabsList>

        <TabsContent value="equipes" className="mt-4">
          <EquipesTab />
        </TabsContent>

        <TabsContent value="regras" className="mt-4">
          <RegrasObservacaoTab />
        </TabsContent>

        <TabsContent value="grupos" className="mt-4">
          <GruposAjuizamentoTab />
        </TabsContent>

        <TabsContent value="classificacoes" className="mt-4">
          <ClassificacoesTab />
        </TabsContent>

        <TabsContent value="valores" className="mt-4">
          <ValoresPadraoTab />
        </TabsContent>

        <TabsContent value="escritorios" className="mt-4">
      {loading && escritorios.length === 0 ? (
        <div className="py-16 text-center">
          <Loader2 className="mx-auto h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : escritorios.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
            <p className="text-sm text-muted-foreground">Nenhum escritório configurado ainda.</p>
            <Button onClick={seed}>Criar a partir dos padrões do robô</Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {escritorios.map((esc) => (
            <Card key={esc.id} className={esc.ativo ? "" : "opacity-60"}>
              <CardHeader className="pb-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <CardTitle className="flex items-center gap-2 text-base">
                      {esc.nome}
                      {!esc.ativo && <Badge variant="outline">Inativo</Badge>}
                    </CardTitle>
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">{esc.escritorio_path}</p>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <Button size="sm" variant="ghost" onClick={() => abrirEdicao(esc)} title="Editar">
                      <Settings className="h-3.5 w-3.5" />
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => desativar(esc)} title="Desativar">
                      <Power className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {esc.criterio_cliente && (
                    <Badge
                      className={
                        esc.criterio_cliente === "ATIVOS"
                          ? "bg-violet-100 text-violet-700"
                          : "bg-yellow-100 text-yellow-800"
                      }
                      variant="secondary"
                    >
                      {esc.criterio_cliente === "ATIVOS" ? "Ativos" : "Banco do Brasil"}
                    </Badge>
                  )}
                  {esc.criterio_polo && <Badge variant="secondary">Polo: {esc.criterio_polo}</Badge>}
                  {esc.criterio_natureza && <Badge variant="secondary">Natureza: {esc.criterio_natureza}</Badge>}
                  {esc.observacao_padrao && <Badge variant="outline">Obs: {esc.observacao_padrao}</Badge>}
                  {esc.responsavel_fixo_nome && (
                    <Badge className="bg-indigo-100 text-indigo-700" variant="secondary">
                      Fixo: {esc.responsavel_fixo_nome}
                    </Badge>
                  )}
                </div>
              </CardHeader>
              <CardContent>
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-sm font-medium">Fila de responsáveis (rodízio)</span>
                  <span className="text-xs text-muted-foreground">{esc.responsaveis.length} na fila</span>
                </div>
                {esc.responsaveis.length === 0 ? (
                  <p className="text-xs text-muted-foreground">
                    {esc.responsavel_fixo_nome
                      ? "Usa responsável fixo (sem rodízio)."
                      : "Sem responsáveis — processos ficam sem responsável."}
                  </p>
                ) : (
                  <ul className="space-y-1">
                    {esc.responsaveis.map((r) => (
                      <li key={r.id} className="flex items-center justify-between rounded-md border bg-card px-2 py-1 text-sm">
                        <span className="truncate">{r.nome ?? `#${r.user_id}`}</span>
                        <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={() => removerResp(r.id)} title="Remover">
                          <Trash2 className="h-3 w-3 text-rose-500" />
                        </Button>
                      </li>
                    ))}
                  </ul>
                )}

                {addRespPara === esc.id ? (
                  <div className="mt-2 flex items-center gap-2">
                    <div className="min-w-0 flex-1">
                      <UserCombo usuarios={usuarios} value={novoRespUserId} onChange={setNovoRespUserId} />
                    </div>
                    <Button size="sm" onClick={() => addResp(esc.id)} disabled={!novoRespUserId}>
                      Adicionar
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => { setAddRespPara(null); setNovoRespUserId(null); }}>
                      Cancelar
                    </Button>
                  </div>
                ) : (
                  <Button size="sm" variant="outline" className="mt-2" onClick={() => { setAddRespPara(esc.id); setNovoRespUserId(null); }}>
                    <UserPlus className="mr-2 h-3.5 w-3.5" />
                    Adicionar responsável
                  </Button>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
        </TabsContent>
      </Tabs>

      {/* Dialog de novo/editar escritório */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editando ? "Editar escritório" : "Novo escritório"}</DialogTitle>
            <DialogDescription>Roteamento de processos por polo e/ou natureza, com o caminho do Legal One.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label className="text-xs">Nome</Label>
              <Input value={form.nome ?? ""} onChange={(e) => setForm((f) => ({ ...f, nome: e.target.value }))} placeholder="Ex.: Réu" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Caminho no Legal One</Label>
              <Input
                value={form.escritorio_path ?? ""}
                onChange={(e) => setForm((f) => ({ ...f, escritorio_path: e.target.value }))}
                placeholder="MDR Advocacia / Área operacional / Banco do Brasil / Réu"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Critério — Cliente</Label>
              <select
                className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                value={form.criterio_cliente ?? ""}
                onChange={(e) => setForm((f) => ({ ...f, criterio_cliente: e.target.value || null }))}
              >
                {CLIENTES_ESC.map((c) => (
                  <option key={c.valor} value={c.valor}>
                    {c.rotulo}
                  </option>
                ))}
              </select>
              <p className="text-[11px] text-muted-foreground">
                De qual cliente este escritório recebe. Banco do Brasil e Ativos têm escritórios
                com o mesmo polo — sem este critério os processos se misturariam entre as filas.
              </p>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label className="text-xs">Critério — Polo</Label>
                <select
                  className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                  value={form.criterio_polo ?? ""}
                  onChange={(e) => setForm((f) => ({ ...f, criterio_polo: e.target.value || null }))}
                >
                  <option value="">—</option>
                  {POLOS.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Critério — Natureza</Label>
                <Input
                  value={form.criterio_natureza ?? ""}
                  onChange={(e) => setForm((f) => ({ ...f, criterio_natureza: e.target.value || null }))}
                  placeholder="Ex.: Trabalhista"
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label className="text-xs">Observação padrão</Label>
                <Input
                  value={form.observacao_padrao ?? ""}
                  onChange={(e) => setForm((f) => ({ ...f, observacao_padrao: e.target.value || null }))}
                  placeholder="Cadastro / Ajuizamento…"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Responsável fixo (opcional)</Label>
                <UserCombo
                  usuarios={usuarios}
                  value={form.responsavel_fixo_user_id ?? null}
                  onChange={(id) => setForm((f) => ({ ...f, responsavel_fixo_user_id: id }))}
                  placeholder="Sem fixo (usa rodízio)"
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              Cancelar
            </Button>
            <Button onClick={salvar}>{editando ? "Salvar" : "Criar"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
