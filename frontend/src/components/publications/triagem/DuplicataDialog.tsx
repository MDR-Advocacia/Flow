// Duplicata no Legal One não é veto — é aviso.
//
// O backend recusa com 409 quando já existe tarefa pendente do mesmo subtipo
// no processo, e a Triagem tratava isso como erro terminal: um toast vermelho
// dizendo "reenvie com force_duplicate=true". Isso é parâmetro de API — não
// existe forma de o operador fazer isso pela tela. Na prática ele ficava
// impedido de agendar, sem nem saber QUAL tarefa estava no caminho.
//
// Aqui a decisão volta para quem tem contexto: mostra as tarefas que já
// existem, com o #id clicável para conferir no L1, e oferece as duas saídas.
// Agendar de novo é legítimo com frequência — a tarefa aberta pode ser de
// outra providência, pode estar parada, pode ser de outro prazo do mesmo
// processo. Quem sabe disso é a pessoa, não a regra.
//
// O motivo é opcional de propósito: exigir justificativa para destravar o
// trabalho transformaria o aviso em pedágio. Quando escolhido, ele viaja no
// payload (`_agendou_com_tarefa_aberta_motivo`) e fica na auditoria.

import { useEffect, useState } from "react";
import { AlertTriangle, ExternalLink, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { OPEN_TASK_REASONS } from "./motivos";
import { ChipsMotivo } from "./TaskComposer";
import { LinhaTarefa, buscarTarefasDoProcesso, type TarefaL1 } from "./TarefasNoL1";

const L1_TAREFAS = "https://mdradvocacia.novajus.com.br/processos/Processos/DetailsCompromissosTarefas";

interface Props {
  /** null = fechado. */
  conflito: { lawsuitId: number | null; cnj?: string | null; quantas: number; subtipos: number[] } | null;
  onCancelar: () => void;
  /** Recebe o motivo escolhido (ou null) e reenvia com force_duplicate. */
  onAgendarMesmoAssim: (motivo: string | null) => void;
  enviando?: boolean;
}

export function DuplicataDialog({ conflito, onCancelar, onAgendarMesmoAssim, enviando }: Props) {
  const [tarefas, setTarefas] = useState<TarefaL1[] | null>(null);
  const [carregando, setCarregando] = useState(false);
  const [motivo, setMotivo] = useState<string | null>(null);

  useEffect(() => {
    setMotivo(null);
    setTarefas(null);
    if (!conflito?.lawsuitId) return;
    let vivo = true;
    setCarregando(true);
    buscarTarefasDoProcesso(conflito.lawsuitId)
      .then((r) => {
        if (!vivo) return;
        const pend = r?.pending || [];
        // As do MESMO subtipo primeiro — são elas que barraram o envio — e as
        // outras pendentes logo abaixo: o operador decide melhor vendo o
        // processo inteiro, não só o que a regra apontou.
        const alvo = new Set(conflito.subtipos);
        const ordenadas = [
          ...pend.filter((t) => t.subtype_id != null && alvo.has(t.subtype_id)),
          ...pend.filter((t) => t.subtype_id == null || !alvo.has(t.subtype_id)),
        ];
        setTarefas(ordenadas);
      })
      .finally(() => { if (vivo) setCarregando(false); });
    return () => { vivo = false; };
  }, [conflito]);

  const n = conflito?.quantas || tarefas?.length || 0;

  return (
    <Dialog open={!!conflito} onOpenChange={(v) => { if (!v) onCancelar(); }}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-amber-500" />
            Já existe tarefa em aberto neste processo
          </DialogTitle>
          <DialogDescription>
            {n > 0
              ? `O Legal One tem ${n} tarefa(s) pendente(s) do mesmo subtipo. `
              : "O Legal One acusou tarefa pendente do mesmo subtipo. "}
            Confira abaixo e decida: pode ser a mesma providência já tratada, ou
            um prazo diferente que precisa da tarefa nova mesmo assim.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          {carregando && (
            <div className="flex items-center gap-2 py-3 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Consultando as tarefas do processo...
            </div>
          )}

          {!carregando && tarefas && tarefas.length > 0 && (
            <ul className="max-h-64 space-y-1.5 overflow-y-auto">
              {tarefas.map((t) => (
                <LinhaTarefa key={t.task_id ?? Math.random()} t={t} tom="pendente" />
              ))}
            </ul>
          )}

          {!carregando && (!tarefas || tarefas.length === 0) && (
            <p className="text-xs text-muted-foreground">
              Não consegui listar as tarefas agora.{" "}
              {conflito?.lawsuitId && (
                <a
                  href={`${L1_TAREFAS}/${conflito.lawsuitId}?renderOnlySection=True`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-0.5 text-primary underline"
                >
                  Ver no Legal One <ExternalLink className="h-3 w-3" />
                </a>
              )}
            </p>
          )}

          <div>
            <p className="mb-1 text-xs font-medium">
              Por que agendar mesmo assim?{" "}
              <span className="font-normal text-muted-foreground">(opcional, fica na auditoria)</span>
            </p>
            <ChipsMotivo
              titulo=""
              opcoes={OPEN_TASK_REASONS}
              valor={motivo}
              onPick={setMotivo}
              disabled={enviando}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onCancelar} disabled={enviando}>
            Não agendar agora
          </Button>
          <Button onClick={() => onAgendarMesmoAssim(motivo)} disabled={enviando}>
            {enviando ? "Enviando..." : "Agendar mesmo assim"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default DuplicataDialog;
