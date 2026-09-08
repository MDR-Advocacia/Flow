// Confirmação antes de remeter ao Legal One.
//
// Agendar cria tarefa de verdade na agenda de alguém — é irreversível pela
// tela. O aviso existe para que ninguém descubra isso no primeiro clique.
//
// Some pelo resto do dia quando a pessoa marca a caixa: quem trata 50
// publicações por turno não precisa confirmar 50 vezes o que já entendeu na
// primeira. A dispensa é por dia e por navegador, então o dia seguinte
// pergunta de novo — e um turno diferente, em outra máquina, também.

import { useState } from "react";
import { AlertTriangle, Loader2, Send } from "lucide-react";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { fmtData } from "./helpers";
import type { DraftTask } from "./types";

const CHAVE = "pub-triagem:pular-confirmacao-ate";

/** A dispensa vale até a virada do dia (guardada como AAAA-MM-DD). */
export function confirmacaoDispensadaHoje(): boolean {
  try {
    return localStorage.getItem(CHAVE) === new Date().toISOString().slice(0, 10);
  } catch {
    return false;
  }
}

export function dispensarConfirmacaoHoje(): void {
  try {
    localStorage.setItem(CHAVE, new Date().toISOString().slice(0, 10));
  } catch {
    /* navegador sem storage: o aviso volta a aparecer, que é o lado seguro */
  }
}

interface Props {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  tarefas: DraftTask[];
  cnj: string | null;
  nomePorExternalId: (id: number | null) => string;
  submitting?: boolean;
  onConfirm: (naoMostrarHoje: boolean) => void;
}

export function ConfirmarAgendamentoDialog({
  open, onOpenChange, tarefas, cnj, nomePorExternalId, submitting, onConfirm,
}: Props) {
  const [naoMostrar, setNaoMostrar] = useState(false);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-amber-500" />
            Confirmar envio ao Legal One
          </DialogTitle>
          <DialogDescription>
            Ao confirmar, {tarefas.length === 1 ? "a tarefa vai" : `as ${tarefas.length} tarefas vão`}{" "}
            <b className="text-foreground">direto para o Legal One</b> e aparecem na agenda
            do responsável. Não dá para desfazer por esta tela.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2 rounded-xl border bg-muted/30 p-3">
          <p className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">
            {cnj ? `Processo ${cnj}` : "Publicação sem processo vinculado"}
          </p>
          {tarefas.map((t, i) => (
            <div key={t.uid} className="text-sm">
              <span className="font-semibold">{i + 1}. {t.description}</span>
              <div className="text-xs text-muted-foreground">
                {nomePorExternalId(t.responsibleExternalId)} · conclusão prevista {fmtData(t.dueDate)}
              </div>
            </div>
          ))}
        </div>

        <div className="flex items-center gap-2">
          <Checkbox
            id="nao-mostrar-hoje"
            checked={naoMostrar}
            onCheckedChange={(v) => setNaoMostrar(v === true)}
          />
          <Label htmlFor="nao-mostrar-hoje" className="cursor-pointer text-sm font-normal">
            Não mostrar este aviso de novo hoje
          </Label>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={submitting}>
            Cancelar
          </Button>
          <Button onClick={() => onConfirm(naoMostrar)} disabled={submitting}>
            {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Send className="mr-2 h-4 w-4" />}
            Enviar ao Legal One
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default ConfirmarAgendamentoDialog;
