// Distribuição de leitura (pub013).
//
// A equipe de leitura se intercala entre escritórios. Sem combinar quem lê o
// quê, duas pessoas abrem a mesma publicação e uma trata o que a outra já
// estava tratando. Aqui o supervisor escolhe os leitores do turno, reparte a
// fila em partes iguais, e cada um filtra pela própria tag.
//
// A tag é sempre OPCIONAL: publicação sem tag continua visível para todos,
// que é o comportamento de quem não usa esta função.

import { useMemo, useState } from "react";
import {
  Eraser, Loader2, Shuffle, UserPlus, Users, X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { corDoNome, iniciais } from "./helpers";
import type { AppUser, DistribuicaoItem } from "./types";

interface Props {
  itens: DistribuicaoItem[];
  users: AppUser[];
  /** user_ids selecionados no filtro; "sem_tag" para as não distribuídas. */
  filtro: string[];
  onFiltro: (v: string[]) => void;
  onDistribuir: (userIds: number[], sobrescrever: boolean) => Promise<void>;
  onLimpar: () => Promise<void>;
  loading?: boolean;
  /** Nome do escopo atual, para o diálogo dizer sobre o que está falando. */
  escopo?: string;
}

export function DistribuicaoBar({
  itens, users, filtro, onFiltro, onDistribuir, onLimpar, loading, escopo,
}: Props) {
  const [dialogAberto, setDialogAberto] = useState(false);
  const [selecionados, setSelecionados] = useState<number[]>([]);
  const [sobrescrever, setSobrescrever] = useState(false);
  const [busca, setBusca] = useState("");
  const [enviando, setEnviando] = useState(false);

  const comTag = itens.filter((i) => i.user_id !== null);
  const semTag = itens.find((i) => i.user_id === null);
  const temDistribuicao = comTag.length > 0;

  const usuariosFiltrados = useMemo(() => {
    const termo = busca.trim().toLowerCase();
    const lista = users.filter((u) => u.external_id != null);
    if (!termo) return lista.slice(0, 60);
    return lista.filter((u) => (u.name || "").toLowerCase().includes(termo)).slice(0, 60);
  }, [users, busca]);

  const alternarFiltro = (valor: string) =>
    onFiltro(filtro.includes(valor) ? filtro.filter((f) => f !== valor) : [...filtro, valor]);

  const alternarSelecao = (id: number) =>
    setSelecionados((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  const confirmarDistribuicao = async () => {
    setEnviando(true);
    try {
      await onDistribuir(selecionados, sobrescrever);
      setDialogAberto(false);
      setSelecionados([]);
      setSobrescrever(false);
    } finally {
      setEnviando(false);
    }
  };

  return (
    <>
      <div className="flex flex-wrap items-center gap-2 rounded-2xl border bg-card/70 p-3 shadow-sm backdrop-blur">
        <span className="inline-flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide text-muted-foreground">
          <Users className="h-3.5 w-3.5" /> Leitura
        </span>

        {!temDistribuicao && (
          <span className="text-xs text-muted-foreground">
            Fila sem divisão — todos veem tudo.
          </span>
        )}

        {comTag.map((i) => {
          const valor = String(i.user_id);
          const ativo = filtro.includes(valor);
          return (
            <button
              key={valor}
              type="button"
              onClick={() => alternarFiltro(valor)}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border py-0.5 pl-1 pr-2.5 text-xs font-semibold transition-all",
                ativo ? "border-primary bg-primary/10 ring-2 ring-primary/20" : "bg-background hover:border-primary/40",
              )}
              title={ativo ? "Clique para tirar do filtro" : "Ver só as desta pessoa"}
            >
              <span
                className="inline-flex h-5 w-5 items-center justify-center rounded-full text-[9px] font-bold text-white"
                style={{ background: corDoNome(i.nome) }}
              >
                {iniciais(i.nome)}
              </span>
              <span className="max-w-[120px] truncate">{i.nome}</span>
              <Badge variant="secondary" className="ml-0.5 px-1.5 text-[10px]">{i.total}</Badge>
            </button>
          );
        })}

        {semTag && semTag.total > 0 && temDistribuicao && (
          <button
            type="button"
            onClick={() => alternarFiltro("sem_tag")}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold transition-all",
              filtro.includes("sem_tag")
                ? "border-primary bg-primary/10 ring-2 ring-primary/20"
                : "bg-background hover:border-primary/40",
            )}
          >
            Sem dono
            <Badge variant="secondary" className="px-1.5 text-[10px]">{semTag.total}</Badge>
          </button>
        )}

        <div className="ml-auto flex items-center gap-1.5">
          {filtro.length > 0 && (
            <Button variant="ghost" size="sm" onClick={() => onFiltro([])}>
              <X className="mr-1 h-3.5 w-3.5" /> ver todas
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => setDialogAberto(true)} disabled={loading}>
            {temDistribuicao ? <Shuffle className="mr-1.5 h-3.5 w-3.5" /> : <UserPlus className="mr-1.5 h-3.5 w-3.5" />}
            {temDistribuicao ? "Redistribuir" : "Distribuir leitura"}
          </Button>
          {temDistribuicao && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void onLimpar()}
              disabled={loading}
              title="Remove a tag de todas as publicações do escopo"
            >
              <Eraser className="mr-1.5 h-3.5 w-3.5" /> Limpar tags
            </Button>
          )}
        </div>
      </div>

      <Dialog open={dialogAberto} onOpenChange={setDialogAberto}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Distribuir a leitura</DialogTitle>
            <DialogDescription>
              A fila{escopo ? ` de ${escopo}` : ""} é repartida em partes iguais entre quem
              você escolher. Cada leitor filtra pela própria tag e não esbarra no trabalho
              do outro. Publicação sem tag continua visível para todos.
            </DialogDescription>
          </DialogHeader>

          <Input
            value={busca}
            onChange={(e) => setBusca(e.target.value)}
            placeholder="Buscar pessoa…"
            className="text-sm"
          />

          <ScrollArea className="h-[260px] rounded-lg border">
            <div className="p-1">
              {usuariosFiltrados.map((u) => (
                <label
                  key={u.external_id}
                  className="flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 hover:bg-muted"
                >
                  <Checkbox
                    checked={selecionados.includes(u.external_id)}
                    onCheckedChange={() => alternarSelecao(u.external_id)}
                  />
                  <span
                    className="inline-flex h-6 w-6 items-center justify-center rounded-full text-[9px] font-bold text-white"
                    style={{ background: corDoNome(u.name) }}
                  >
                    {iniciais(u.name)}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-sm">{u.name}</span>
                </label>
              ))}
              {usuariosFiltrados.length === 0 && (
                <p className="px-2 py-6 text-center text-sm text-muted-foreground">
                  Ninguém encontrado.
                </p>
              )}
            </div>
          </ScrollArea>

          <div className="flex items-start gap-2">
            <Checkbox
              id="sobrescrever-tags"
              checked={sobrescrever}
              onCheckedChange={(v) => setSobrescrever(v === true)}
            />
            <Label htmlFor="sobrescrever-tags" className="cursor-pointer text-sm font-normal leading-snug">
              Refazer a divisão do zero
              <span className="block text-xs text-muted-foreground">
                Sem marcar, só as publicações ainda sem dono são repartidas — quem já tem
                fila continua com ela.
              </span>
            </Label>
          </div>

          <DialogFooter>
            <Button variant="ghost" onClick={() => setDialogAberto(false)} disabled={enviando}>
              Cancelar
            </Button>
            <Button onClick={() => void confirmarDistribuicao()} disabled={!selecionados.length || enviando}>
              {enviando ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Shuffle className="mr-2 h-4 w-4" />}
              Repartir entre {selecionados.length || "…"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

export default DistribuicaoBar;
