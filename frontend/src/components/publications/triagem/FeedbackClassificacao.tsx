// Feedback de classificação na Triagem — colher o ERRO, não só corrigi-lo.
//
// O módulo antigo já tinha isso (thumbs-down → `POST /records/feedback`), e a
// Triagem nasceu com o botão no card mas sem ninguém do outro lado do fio: o
// `onFeedback` nunca era passado, então o botão nem aparecia. Quem trabalha na
// tela nova corrigia a classificação de cabeça e seguia — a correção morria ali,
// e a única coisa que sabemos sobre os erros do robô é o que alguém lembra de
// contar numa reunião.
//
// Duas coisas importam no que se registra aqui:
//
//   1. O PAR (o que a IA disse, o que era certo). É o dado que vira exemplo
//      few-shot no prompt; sem o "errado" junto, o exemplo não ensina nada.
//   2. A NOTA do operador — a regra que ele tem na cabeça e a máquina não
//      ("quando fala embargante, é sempre embargos"). É o campo mais valioso
//      da tela e por isso está em destaque, não escondido como opcional.
//
// A árvore de classificação vem pronta de quem chama: na fila comum é a
// taxonomia v2 do escritório; na fila SEM PASTA é a árvore plana dos 16 tipos
// do motor próprio (o `/classification-taxonomy` já troca pelo
// `office_external_id=-1`). Aqui não se decide qual é — só se desenha a que veio.

import { useEffect, useMemo, useState } from "react";
import { ChevronsUpDown, MessageSquareWarning } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList,
} from "@/components/ui/command";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/hooks/use-toast";
import { apiFetch } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import type { PublicationRecord } from "./types";

const API = "/api/v1/publications";

/** O que estava errado. Vira `error_type` e serve pra agrupar o relatório. */
const TIPOS_DE_ERRO: [string, string][] = [
  ["category", "A categoria"],
  ["subcategory", "A subcategoria"],
  ["polo", "O polo"],
  ["natureza", "A natureza do processo"],
  ["multiple", "Mais de um campo"],
];

interface Opcao {
  categoria: string;
  subcategoria: string | null;
  /** O que aparece na lista e o que a busca do cmdk casa. */
  rotulo: string;
}

/** Achata a árvore em opções selecionáveis. Categoria sem filhos (a fila sem
 *  pasta é assim) vira uma opção só, o que é exatamente o que se quer lá. */
function achatar(taxonomy: Record<string, string[]>): Opcao[] {
  const out: Opcao[] = [];
  for (const categoria of Object.keys(taxonomy).sort()) {
    const subs = taxonomy[categoria] || [];
    if (subs.length === 0) {
      out.push({ categoria, subcategoria: null, rotulo: categoria });
      continue;
    }
    for (const sub of [...subs].sort()) {
      out.push({ categoria, subcategoria: sub, rotulo: `${categoria} › ${sub}` });
    }
  }
  return out;
}

interface Props {
  /** Publicação sob feedback; `null` fecha o diálogo. */
  record: PublicationRecord | null;
  taxonomy: Record<string, string[]>;
  onOpenChange: (aberto: boolean) => void;
  /** Chamado após gravar — a tela recarrega a fila, porque a correção também
   *  é aplicada ao registro (o endpoint faz as duas coisas). */
  onRegistrado?: () => void;
}

export function FeedbackClassificacao({ record, taxonomy, onOpenChange, onRegistrado }: Props) {
  const { toast } = useToast();
  const opcoes = useMemo(() => achatar(taxonomy), [taxonomy]);

  const [tipoErro, setTipoErro] = useState("category");
  const [escolha, setEscolha] = useState<Opcao | null>(null);
  const [polo, setPolo] = useState("");
  const [nota, setNota] = useState("");
  const [buscando, setBuscando] = useState(false);
  const [enviando, setEnviando] = useState(false);

  // Abrir com a classificação ATUAL pré-selecionada seria um convite a
  // confirmar o erro sem ler. Começa vazio de propósito.
  useEffect(() => {
    if (!record) return;
    setTipoErro("category");
    setEscolha(null);
    setPolo(record.polo || "");
    setNota("");
  }, [record]);

  const enviar = async () => {
    if (!record || !escolha) return;
    setEnviando(true);
    try {
      const res = await apiFetch(`${API}/records/feedback`, {
        method: "POST",
        body: JSON.stringify({
          record_id: record.id,
          error_type: tipoErro,
          corrected_category: escolha.categoria,
          corrected_subcategory: escolha.subcategoria,
          corrected_polo: polo || null,
          user_note: nota.trim() || null,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toast({
        title: "Feedback registrado",
        description: "A correção foi aplicada à publicação e o erro ficou guardado para melhorar o classificador.",
      });
      onOpenChange(false);
      onRegistrado?.();
    } catch (e: any) {
      toast({
        title: "Não consegui registrar o feedback",
        description: e?.message,
        variant: "destructive",
      });
    } finally {
      setEnviando(false);
    }
  };

  const atual = record
    ? [record.category, record.subcategory && record.subcategory !== "-" ? record.subcategory : null]
        .filter(Boolean)
        .join(" › ")
    : "";

  return (
    <Dialog open={!!record} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <MessageSquareWarning className="h-5 w-5 text-red-500" />
            Reportar classificação errada
          </DialogTitle>
          <DialogDescription>
            A correção é aplicada à publicação e o par (o que a IA disse × o que era certo)
            fica guardado para virar exemplo no prompt do classificador.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2">
            <p className="mb-0.5 text-[11px] font-semibold uppercase tracking-wide text-red-700">
              O que a IA classificou
            </p>
            <p className="text-sm font-semibold">{atual || "—"}</p>
            {record?.polo && <p className="text-xs text-red-700/80">Polo {record.polo}</p>}
          </div>

          <div className="space-y-1">
            <Label className="text-xs">O que estava errado?</Label>
            <Select value={tipoErro} onValueChange={setTipoErro}>
              <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>
                {TIPOS_DE_ERRO.map(([v, r]) => (
                  <SelectItem key={v} value={v}>{r}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Combobox com busca, nunca Select cru: a taxonomia da fila comum
              passa de 100 pares e já estourou modal antes. */}
          <div className="space-y-1">
            <Label className="text-xs">Qual era a classificação certa?</Label>
            <Popover open={buscando} onOpenChange={setBuscando}>
              <PopoverTrigger asChild>
                <Button
                  variant="outline"
                  role="combobox"
                  className={cn(
                    "h-9 w-full justify-between text-sm font-normal",
                    !escolha && "text-muted-foreground",
                  )}
                >
                  <span className="truncate">
                    {escolha ? escolha.rotulo : "Buscar a classificação correta..."}
                  </span>
                  <ChevronsUpDown className="ml-1 h-3.5 w-3.5 shrink-0 opacity-50" />
                </Button>
              </PopoverTrigger>
              <PopoverContent className="w-[--radix-popover-trigger-width] p-0" align="start">
                <Command>
                  <CommandInput placeholder="Digite parte do nome..." className="h-9" />
                  <CommandList>
                    <CommandEmpty>Nada com esse nome nesta árvore.</CommandEmpty>
                    <CommandGroup>
                      {opcoes.map((o) => (
                        <CommandItem
                          key={o.rotulo}
                          value={o.rotulo}
                          onSelect={() => { setEscolha(o); setBuscando(false); }}
                        >
                          <span className="truncate">{o.rotulo}</span>
                        </CommandItem>
                      ))}
                    </CommandGroup>
                  </CommandList>
                </Command>
              </PopoverContent>
            </Popover>
          </div>

          <div className="space-y-1">
            <Label className="text-xs">Polo correto (opcional)</Label>
            <Select value={polo || "manter"} onValueChange={(v) => setPolo(v === "manter" ? "" : v)}>
              <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="manter">Manter como está</SelectItem>
                <SelectItem value="ativo">Ativo</SelectItem>
                <SelectItem value="passivo">Passivo</SelectItem>
                <SelectItem value="ambos">Ambos</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* O campo que mais vale: a regra que o operador tem na cabeça. */}
          <div className="space-y-1">
            <Label className="text-xs">Por que era essa? (o que a máquina não viu)</Label>
            <Textarea
              value={nota}
              onChange={(e) => setNota(e.target.value)}
              rows={3}
              className="text-sm"
              placeholder='Ex.: "fala em embargante e embargado, então é Embargos à Execução, não Cumprimento de Sentença"'
            />
            <p className="text-[11px] text-muted-foreground">
              É daqui que sai o exemplo que ensina o classificador. Uma frase basta.
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={enviando}>
            Cancelar
          </Button>
          <Button onClick={enviar} disabled={!escolha || enviando}>
            {enviando ? "Registrando..." : "Registrar e corrigir"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default FeedbackClassificacao;
