import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, ChevronsUpDown, FolderPlus, Loader2, Search, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList,
} from "@/components/ui/command";
import { useToast } from "@/hooks/use-toast";
import {
  OfficeL1,
  UsuarioL1,
  buscarCapaAvulso,
  criarPastaAvulsa,
  listarOfficesL1,
  listarUsuarios,
  sugestaoAvulso,
} from "@/services/distribuidos-bb";

// Chips que pré-preenchem o cliente com os dois da casa; qualquer outro é digitado.
const CLIENTES_RAPIDOS = [
  { rotulo: "Banco do Brasil", nome: "Banco do Brasil S.A.", cnpj: "00.000.000/0001-91" },
  { rotulo: "Ativos", nome: "Ativos S.A. Securitizadora de Créditos Financeiros", cnpj: "05.437.257/0001-29" },
];
const POSICOES = ["Réu", "Autor", "Interessado"];

function OfficeCombo({
  offices, value, onChange,
}: {
  offices: OfficeL1[];
  value: string | null;
  onChange: (path: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" role="combobox" className="w-full justify-between font-normal">
          <span className="truncate">{value ?? "Selecionar escritório…"}</span>
          <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[--radix-popover-trigger-width] p-0" align="start">
        <Command>
          <CommandInput placeholder="Buscar escritório…" />
          <CommandList className="max-h-64">
            <CommandEmpty>Nenhum escritório encontrado.</CommandEmpty>
            <CommandGroup>
              {offices.map((o) => (
                <CommandItem key={o.id} value={o.path} onSelect={() => { onChange(o.path === value ? null : o.path); setOpen(false); }}>
                  <span className="min-w-0 flex-1 truncate text-xs">{o.path}</span>
                  {value === o.path && <Check className="ml-2 h-4 w-4 shrink-0 text-emerald-600" />}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

function UserCombo({
  usuarios, value, onChange, placeholder = "Selecionar responsável…",
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
        <Button variant="outline" role="combobox" className="w-full justify-between font-normal">
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

export default function PastaAvulsaDialog({
  open, onOpenChange, onCreated,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onCreated: () => void;
}) {
  const { toast } = useToast();
  const [offices, setOffices] = useState<OfficeL1[]>([]);
  const [usuarios, setUsuarios] = useState<UsuarioL1[]>([]);
  const [salvando, setSalvando] = useState(false);
  const [buscandoCapa, setBuscandoCapa] = useState(false);

  const [cnj, setCnj] = useState("");
  const [clienteNome, setClienteNome] = useState("");
  const [clienteCnpj, setClienteCnpj] = useState("");
  const [clienteTipo, setClienteTipo] = useState("PJ");
  const [posicao, setPosicao] = useState("Réu");
  const [natureza, setNatureza] = useState("Civel");
  const [acao, setAcao] = useState("");
  const [dataAjuiz, setDataAjuiz] = useState("");
  const [uf, setUf] = useState("");
  const [comarca, setComarca] = useState("");
  const [orgao, setOrgao] = useState("");
  const [valorCausa, setValorCausa] = useState("");
  const [adversoNome, setAdversoNome] = useState("");
  const [adversoDoc, setAdversoDoc] = useState("");
  const [adversoTipo, setAdversoTipo] = useState("PF");
  const [escritorioPath, setEscritorioPath] = useState<string | null>(null);
  const [responsavelId, setResponsavelId] = useState<number | null>(null);
  const [sugestaoId, setSugestaoId] = useState<number | null>(null);
  const [sugestaoNome, setSugestaoNome] = useState<string | null>(null);
  const [observacao, setObservacao] = useState("");

  useEffect(() => {
    if (!open) return;
    listarOfficesL1().then(setOffices).catch(() => setOffices([]));
    listarUsuarios().then(setUsuarios).catch(() => setUsuarios([]));
  }, [open]);

  // Sugestão do rodízio + observação da regra quando escritório/posição mudam.
  useEffect(() => {
    if (!open || !escritorioPath) return;
    sugestaoAvulso({
      escritorio_path: escritorioPath,
      cliente_cpf_cnpj: clienteCnpj || undefined,
      posicao,
      natureza: natureza || undefined,
      cnj: cnj || undefined,
    })
      .then((s) => {
        setSugestaoId(s.responsavel_sugerido_id);
        setSugestaoNome(s.responsavel_sugerido_nome);
        if (s.responsavel_sugerido_id) setResponsavelId(s.responsavel_sugerido_id);
        if (s.observacao_sugerida) setObservacao(s.observacao_sugerida);
      })
      .catch(() => { setSugestaoId(null); setSugestaoNome(null); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, escritorioPath, posicao, clienteCnpj]);

  const buscarCapa = useCallback(async () => {
    if (!cnj.trim()) return;
    setBuscandoCapa(true);
    try {
      const capa = await buscarCapaAvulso(cnj.trim());
      if (!capa.encontrado) {
        toast({ title: "Capa não encontrada", description: "O DataJud ainda não indexou este processo (comum em recém-distribuídos). Preencha manualmente.", variant: "destructive" });
        return;
      }
      // Natureza fica no catálogo do L1 (Civel/Trabalhista) — a classe vai na Ação.
      if (capa.classe) setAcao(capa.classe);
      else if (capa.assunto) setAcao(capa.assunto);
      if (capa.orgao_julgador) setOrgao(capa.orgao_julgador);
      if (capa.uf) setUf(capa.uf);
      if (capa.data_ajuizamento) setDataAjuiz(capa.data_ajuizamento);
      toast({ title: "Capa preenchida", description: "Dados do DataJud aplicados — revise e complete o que faltar." });
    } catch (e) {
      toast({ title: "DataJud indisponível", description: String(e), variant: "destructive" });
    } finally {
      setBuscandoCapa(false);
    }
  }, [cnj, toast]);

  const limpar = () => {
    setCnj(""); setClienteNome(""); setClienteCnpj(""); setClienteTipo("PJ");
    setPosicao("Réu"); setNatureza("Civel"); setAcao(""); setDataAjuiz("");
    setUf(""); setComarca(""); setOrgao(""); setValorCausa("");
    setAdversoNome(""); setAdversoDoc(""); setAdversoTipo("PF");
    setEscritorioPath(null); setResponsavelId(null); setSugestaoId(null);
    setSugestaoNome(null); setObservacao("");
  };

  const salvar = useCallback(async () => {
    if (!clienteNome.trim()) { toast({ title: "Informe o cliente", variant: "destructive" }); return; }
    if (!escritorioPath) { toast({ title: "Escolha o escritório responsável", variant: "destructive" }); return; }
    if (!responsavelId) { toast({ title: "Escolha o responsável", variant: "destructive" }); return; }
    setSalvando(true);
    try {
      const res = await criarPastaAvulsa({
        cnj: cnj.trim() || null,
        cliente_nome: clienteNome.trim(),
        cliente_cpf_cnpj: clienteCnpj.trim() || null,
        cliente_tipo: clienteTipo,
        posicao,
        natureza: natureza.trim() || null,
        acao: acao.trim() || null,
        data_ajuizamento: dataAjuiz.trim() || null,
        uf: uf.trim() || null,
        comarca: comarca.trim() || null,
        orgao: orgao.trim() || null,
        valor_causa: valorCausa ? Number(valorCausa) : null,
        adverso_nome: adversoNome.trim() || null,
        adverso_cpf_cnpj: adversoDoc.trim() || null,
        adverso_tipo: adversoTipo,
        escritorio_path: escritorioPath,
        responsavel_user_id: responsavelId,
        consumir_rodizio: responsavelId === sugestaoId && sugestaoId != null,
        observacao: observacao.trim() || null,
      });
      if (res.cadastrado) {
        toast({ title: "Pasta enviada ao Legal One", description: "Import disparado — o monitor confirma o cadastro nos próximos minutos." });
      } else {
        toast({
          title: "Pasta criada, mas o L1 falhou",
          description: `${res.erro ?? "Erro no import."} Ela ficou no pool — gere a planilha de novo pra re-tentar.`,
          variant: "destructive",
        });
      }
      limpar();
      onOpenChange(false);
      onCreated();
    } catch (e) {
      toast({ title: "Não foi possível criar a pasta", description: String(e), variant: "destructive" });
    } finally {
      setSalvando(false);
    }
  }, [cnj, clienteNome, clienteCnpj, clienteTipo, posicao, natureza, acao, dataAjuiz, uf, comarca,
      orgao, valorCausa, adversoNome, adversoDoc, adversoTipo, escritorioPath, responsavelId,
      sugestaoId, observacao, onCreated, onOpenChange, toast]);

  return (
    <Dialog open={open} onOpenChange={(o) => !salvando && onOpenChange(o)}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FolderPlus className="h-5 w-5 text-emerald-600" />
            Nova pasta avulsa
          </DialogTitle>
          <DialogDescription>
            Cria uma pasta única e já cadastra no Legal One (a planilha de migração é gerada e
            importada na hora — o workflow dispara conforme a Observação).
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* CNJ + capa */}
          <div className="space-y-1">
            <Label className="text-xs">Número CNJ (opcional para pré-judicial)</Label>
            <div className="flex gap-2">
              <Input value={cnj} onChange={(e) => setCnj(e.target.value)} placeholder="0000000-00.0000.0.00.0000" />
              <Button variant="outline" size="sm" className="shrink-0" onClick={buscarCapa} disabled={buscandoCapa || !cnj.trim()}>
                {buscandoCapa ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Search className="mr-1 h-4 w-4" />}
                Buscar capa
              </Button>
            </div>
          </div>

          {/* Cliente */}
          <div className="space-y-1 rounded-md border p-3">
            <div className="mb-1 flex items-center justify-between">
              <Label className="text-xs font-semibold">Cliente</Label>
              <div className="flex gap-1">
                {CLIENTES_RAPIDOS.map((c) => (
                  <Badge
                    key={c.rotulo}
                    variant="secondary"
                    className="cursor-pointer hover:bg-muted-foreground/20"
                    onClick={() => { setClienteNome(c.nome); setClienteCnpj(c.cnpj); setClienteTipo("PJ"); }}
                  >
                    {c.rotulo}
                  </Badge>
                ))}
              </div>
            </div>
            <div className="grid grid-cols-6 gap-2">
              <div className="col-span-3">
                <Input value={clienteNome} onChange={(e) => setClienteNome(e.target.value)} placeholder="Nome do cliente" />
              </div>
              <div className="col-span-2">
                <Input value={clienteCnpj} onChange={(e) => setClienteCnpj(e.target.value)} placeholder="CPF/CNPJ" />
              </div>
              <select className="col-span-1 h-9 rounded-md border bg-background px-2 text-sm" value={clienteTipo} onChange={(e) => setClienteTipo(e.target.value)}>
                <option value="PJ">PJ</option>
                <option value="PF">PF</option>
              </select>
            </div>
          </div>

          {/* Processo */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label className="text-xs">Posição do cliente</Label>
              <select className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={posicao} onChange={(e) => setPosicao(e.target.value)}>
                {POSICOES.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Natureza (catálogo do L1)</Label>
              <Input value={natureza} onChange={(e) => setNatureza(e.target.value)} placeholder="Civel ou Trabalhista" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Ação (classe processual)</Label>
              <Input value={acao} onChange={(e) => setAcao(e.target.value)} placeholder="Ex.: Procedimento Comum Cível" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Data de ajuizamento</Label>
              <Input value={dataAjuiz} onChange={(e) => setDataAjuiz(e.target.value)} placeholder="DD/MM/AAAA" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">UF</Label>
              <Input value={uf} onChange={(e) => setUf(e.target.value)} placeholder="Ex.: BA" maxLength={2} />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Comarca</Label>
              <Input value={comarca} onChange={(e) => setComarca(e.target.value)} placeholder="Ex.: Salvador" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Órgão / vara</Label>
              <Input value={orgao} onChange={(e) => setOrgao(e.target.value)} placeholder="Ex.: 2ª Vara Cível" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Valor da causa (R$)</Label>
              <Input type="number" min="0" step="0.01" value={valorCausa} onChange={(e) => setValorCausa(e.target.value)} placeholder="0,00" />
            </div>
          </div>

          {/* Contrário */}
          <div className="space-y-1 rounded-md border p-3">
            <Label className="text-xs font-semibold">Contrário principal</Label>
            <div className="grid grid-cols-6 gap-2">
              <div className="col-span-3">
                <Input value={adversoNome} onChange={(e) => setAdversoNome(e.target.value)} placeholder="Nome (ou deixe em branco)" />
              </div>
              <div className="col-span-2">
                <Input value={adversoDoc} onChange={(e) => setAdversoDoc(e.target.value)} placeholder="CPF/CNPJ" />
              </div>
              <select className="col-span-1 h-9 rounded-md border bg-background px-2 text-sm" value={adversoTipo} onChange={(e) => setAdversoTipo(e.target.value)}>
                <option value="PF">PF</option>
                <option value="PJ">PJ</option>
              </select>
            </div>
          </div>

          {/* Distribuição */}
          <div className="space-y-3 rounded-md border p-3">
            <div className="space-y-1">
              <Label className="text-xs font-semibold">Escritório responsável (árvore do Legal One)</Label>
              <OfficeCombo offices={offices} value={escritorioPath} onChange={setEscritorioPath} />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Responsável principal</Label>
              <UserCombo usuarios={usuarios} value={responsavelId} onChange={setResponsavelId} />
              {sugestaoNome && (
                <p className="flex items-center gap-1 text-[11px] text-muted-foreground">
                  <Sparkles className="h-3 w-3 text-amber-500" />
                  Sugerido pelo rodízio: <strong>{sugestaoNome}</strong>
                  {responsavelId === sugestaoId ? " (a fila avança ao salvar)" : " — você escolheu outro, a fila não anda"}
                </p>
              )}
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Observação (gatilho do workflow no L1)</Label>
              <Input value={observacao} onChange={(e) => setObservacao(e.target.value)} placeholder="Ex.: Cadastro, cadastroativos…" />
            </div>
          </div>

          <div className="flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => onOpenChange(false)} disabled={salvando}>
              Cancelar
            </Button>
            <Button size="sm" onClick={salvar} disabled={salvando}>
              {salvando ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <FolderPlus className="mr-2 h-4 w-4" />}
              Salvar e cadastrar no Legal One
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
