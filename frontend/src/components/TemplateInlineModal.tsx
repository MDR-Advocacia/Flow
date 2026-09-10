/**
 * TemplateInlineModal — modal contextual de template (criar / editar).
 *
 * Diferenca do modal antigo (TaskTemplatesPageLegacy): cat/sub e office
 * vem do CONTEXTO da arvore (linha clicada), nao do operador escolhendo
 * num dropdown gigante. O resto eh igual ao modal rico:
 *  - Multi-task: N tarefas por classificacao (cada bloco = 1 template).
 *  - Atribuir ao assistente do responsavel (target_role).
 *  - Squad de suporte opcional (target_squad_id) — sobrepoe o
 *    responsavel padrao.
 *  - Excecao por etiqueta opcional (excecao_etiqueta, sqd005) — processo
 *    com a etiqueta sai da regra e fica com a squad marcada com ela.
 *
 * Usado pelo OfficeTemplateTree quando o operador clica:
 *  - "+ adicionar template" → mode="create" com cat/sub pre-preenchidos
 *  - "editar" → mode="edit" com template carregado em 1 bloco; permite
 *    adicionar blocos novos pra criar templates extras na mesma cat/sub.
 */
import { useEffect, useState } from "react";
import { Loader2, Plus, X } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  SubtypePicker,
  type SubtypePickerTaskType,
} from "@/components/ui/SubtypePicker";
import UserSelector from "@/components/ui/UserSelector";
import { useToast } from "@/hooks/use-toast";
import { apiFetch } from "@/lib/api-client";

export interface TemplateInlineModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Pra criar: passa office + cat + sub. Pra editar: passa templateId. */
  mode: "create" | "edit";
  officeExternalId: number | null; // null = template global
  category: string;
  subcategory: string | null;
  templateId?: number;
  /** Chamado depois de salvar com sucesso (pai recarrega arvore). */
  onSaved: () => void;
}

interface User {
  external_id: number;
  name: string;
  email?: string;
}

interface SupportSquadOption {
  id: number;
  name: string;
  office_external_id: number | null;
}

const PRIORITIES = [
  { value: "Low", label: "Baixa" },
  { value: "Normal", label: "Normal" },
  { value: "High", label: "Alta" },
];

const DUE_REFERENCE_OPTIONS = [
  { value: "publication", label: "Data da publicação" },
  { value: "today", label: "Data atual (quando criar a tarefa)" },
];

/** Opções do seletor de exceção por etiqueta (sqd005). O valor já salvo
 *  entra mesmo que a etiqueta tenha saído do cache, pra edição não abrir
 *  em branco nem apagar a exceção sem ninguém perceber. */
const etiquetasDoSelect = (lista: string[], atual: string | null | undefined): string[] =>
  atual && !lista.includes(atual) ? [atual, ...lista] : lista;

/** Papel na equipe da etiqueta: o escolhido no template ou, se vazio, o
 *  próprio papel do template — mesma regra do backend. */
const papelNaExcecao = (b: { excecao_papel?: string; target_role_assistant?: boolean }): string =>
  b.excecao_papel || (b.target_role_assistant ? "assistente" : "principal");

/** Descrição padrão da casa (10/09/2026): subtipo — classificação — data da
 *  publicação. As variáveis são preenchidas pela publicação na hora de montar
 *  a tarefa, então a descrição não fica velha se o template mudar. */
const DESCRICAO_PADRAO = "{subtipo} — {classificacao} — publicação de {data_publicacao}";
const VARIAVEIS_DESCRICAO = [
  "subtipo", "classificacao", "data_publicacao", "cnj",
  "audiencia_data_br", "audiencia_hora", "description",
];

const BLANK_BLOCK = {
  id: undefined as number | undefined,
  task_subtype_external_id: null as number | null,
  responsible_user_external_id: null as number | null,
  priority: "Normal",
  due_business_days: 3,
  due_date_reference: "publication",
  description_template: DESCRICAO_PADRAO,
  notes_template: "",
  target_role_assistant: false,
  target_squad_id: "" as string,
  excecao_etiqueta: "" as string, // "" = sem exceção; ex.: "NERC" (sqd005)
  excecao_papel: "" as string, // "principal" | "assistente"; "" = papel do template
};

type TaskBlock = typeof BLANK_BLOCK;

const extractErrorMessage = (data: any, fallback: string): string => {
  const detail = data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d: any) => (typeof d === "string" ? d : d?.msg))
      .filter(Boolean);
    if (msgs.length > 0) return msgs.join("; ");
  }
  if (detail && typeof detail === "object" && typeof detail.msg === "string") {
    return detail.msg;
  }
  return fallback;
};

export function TemplateInlineModal({
  open,
  onOpenChange,
  mode,
  officeExternalId,
  category,
  subcategory,
  templateId,
  onSaved,
}: TemplateInlineModalProps) {
  const { toast } = useToast();

  const [taskBlocks, setTaskBlocks] = useState<TaskBlock[]>([{ ...BLANK_BLOCK }]);
  const [taskTypes, setTaskTypes] = useState<SubtypePickerTaskType[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [supportSquads, setSupportSquads] = useState<SupportSquadOption[]>([]);
  const [etiquetas, setEtiquetas] = useState<string[]>([]);
  const [loadingMeta, setLoadingMeta] = useState(false);
  const [loadingTemplate, setLoadingTemplate] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Reset form ao abrir em modo create
  useEffect(() => {
    if (!open) return;
    if (mode === "create") {
      setTaskBlocks([{ ...BLANK_BLOCK }]);
    }
  }, [open, mode]);

  // Carrega meta (subtipos + usuarios + squads de suporte) ao abrir
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoadingMeta(true);
    Promise.all([
      apiFetch("/api/v1/task-templates/meta/task-types"),
      apiFetch("/api/v1/task-templates/meta/users"),
      apiFetch("/api/v1/squads?kind=support"),
      apiFetch("/api/v1/task-templates/meta/etiquetas"),
    ])
      .then(async ([rTypes, rUsers, rSquads, rEtiquetas]) => {
        if (!rTypes.ok || !rUsers.ok)
          throw new Error("Falha carregando catálogos");
        const [types, us, sqs, ets] = await Promise.all([
          rTypes.json(),
          rUsers.json(),
          rSquads.ok ? rSquads.json() : Promise.resolve([]),
          rEtiquetas.ok ? rEtiquetas.json() : Promise.resolve([]),
        ]);
        if (cancelled) return;
        setTaskTypes(types ?? []);
        setUsers(us ?? []);
        setSupportSquads(
          (sqs ?? []).map((s: any) => ({
            id: s.id,
            name: s.name,
            office_external_id: s.office_external_id ?? null,
          })),
        );
        setEtiquetas(Array.isArray(ets) ? ets : []);
      })
      .catch((err) => {
        if (cancelled) return;
        toast({
          title: "Erro carregando catálogos",
          description: String(err?.message ?? err),
          variant: "destructive",
        });
      })
      .finally(() => !cancelled && setLoadingMeta(false));
    return () => {
      cancelled = true;
    };
  }, [open, toast]);

  // Em modo edit: carrega o template específico em 1 bloco
  useEffect(() => {
    if (!open || mode !== "edit" || !templateId) return;
    let cancelled = false;
    setLoadingTemplate(true);
    apiFetch(`/api/v1/task-templates/${templateId}`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const t = await res.json();
        if (cancelled) return;
        setTaskBlocks([
          {
            id: t.id,
            task_subtype_external_id: t.task_subtype_external_id ?? null,
            responsible_user_external_id:
              t.responsible_user_external_id ?? null,
            priority: t.priority ?? "Normal",
            due_business_days: t.due_business_days ?? 3,
            due_date_reference: t.due_date_reference ?? "publication",
            description_template: t.description_template ?? "",
            notes_template: t.notes_template ?? "",
            target_role_assistant: t.target_role === "assistente",
            target_squad_id: t.target_squad_id ? String(t.target_squad_id) : "",
            excecao_etiqueta: t.excecao_etiqueta ?? "",
            excecao_papel: t.excecao_papel ?? "",
          },
        ]);
      })
      .catch((err) => {
        if (cancelled) return;
        toast({
          title: "Falha carregando template",
          description: String(err?.message ?? err),
          variant: "destructive",
        });
      })
      .finally(() => !cancelled && setLoadingTemplate(false));
    return () => {
      cancelled = true;
    };
  }, [open, mode, templateId, toast]);

  const setBlockField = <K extends keyof TaskBlock>(
    idx: number,
    key: K,
    value: TaskBlock[K],
  ) =>
    setTaskBlocks((prev) => {
      const next = [...prev];
      next[idx] = { ...next[idx], [key]: value };
      return next;
    });

  // Adiciona bloco com base no anterior, MAS limpa subtype pra forcar
  // o operador a escolher um diferente (evita 409 de duplicate-key no backend).
  const addTaskBlock = () =>
    setTaskBlocks((prev) => [
      ...prev,
      {
        ...prev[prev.length - 1],
        id: undefined,
        task_subtype_external_id: null,
      },
    ]);

  const removeTaskBlock = (idx: number) =>
    setTaskBlocks((prev) => prev.filter((_, i) => i !== idx));

  // Subtypes em uso entre blocos do form (exclui o proprio bloco) —
  // desabilita no SubtypePicker pra evitar 409 de duplicata exata.
  const subtypesInUseForBlock = (blockIdx: number): Set<number> => {
    const used = new Set<number>();
    taskBlocks.forEach((b, i) => {
      if (i === blockIdx) return;
      if (b.task_subtype_external_id != null) {
        used.add(b.task_subtype_external_id);
      }
    });
    return used;
  };

  const subtypeName = (subtypeId: number | null): string => {
    if (subtypeId == null) return "Tarefa";
    for (const t of taskTypes) {
      const s = t.subtypes.find((x) => x.external_id === subtypeId);
      if (s) return s.name;
    }
    return "Tarefa";
  };

  const handleSubmit = async () => {
    // Valida cada bloco: subtipo eh obrigatorio
    for (let i = 0; i < taskBlocks.length; i++) {
      if (taskBlocks[i].task_subtype_external_id == null) {
        toast({
          title: `Tarefa ${i + 1}: selecione o subtipo`,
          variant: "destructive",
        });
        return;
      }
    }

    setSubmitting(true);
    try {
      const buildPayload = (block: TaskBlock) => ({
        // Nome auto-derivado do subtipo (operador nao precisa nomear).
        name: `${category}${subcategory ? " / " + subcategory : ""} — ${subtypeName(block.task_subtype_external_id)}`,
        category,
        subcategory: subcategory ?? null,
        office_external_id: officeExternalId,
        task_subtype_external_id: block.task_subtype_external_id!,
        responsible_user_external_id: block.responsible_user_external_id,
        priority: block.priority,
        due_business_days: block.due_business_days,
        due_date_reference: block.due_date_reference,
        description_template: block.description_template || null,
        notes_template: block.notes_template || null,
        is_active: true,
        target_role: block.target_role_assistant ? "assistente" : "principal",
        target_squad_id: block.target_squad_id
          ? parseInt(block.target_squad_id)
          : null,
        excecao_etiqueta: block.excecao_etiqueta || null,
        excecao_papel: block.excecao_etiqueta ? papelNaExcecao(block) : null,
      });

      let createdCount = 0;
      let updatedCount = 0;

      for (const block of taskBlocks) {
        const payload = buildPayload(block);
        const isUpdate = mode === "edit" && block.id != null;
        const url = isUpdate
          ? `/api/v1/task-templates/${block.id}`
          : "/api/v1/task-templates/";
        const method = isUpdate ? "PUT" : "POST";

        const res = await apiFetch(url, {
          method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          throw new Error(extractErrorMessage(data, "Falha ao salvar tarefa."));
        }
        if (isUpdate) updatedCount++;
        else createdCount++;
      }

      const summary =
        mode === "edit" && createdCount > 0
          ? `${updatedCount} atualizada${updatedCount !== 1 ? "s" : ""} + ${createdCount} criada${createdCount !== 1 ? "s" : ""}`
          : mode === "edit"
            ? `${updatedCount} atualizada${updatedCount !== 1 ? "s" : ""}`
            : `${createdCount} criada${createdCount !== 1 ? "s" : ""}`;

      toast({
        title: "Template salvo",
        description: `${category}${subcategory ? ` / ${subcategory}` : ""} · ${summary}`,
      });
      onSaved();
      onOpenChange(false);
    } catch (err: any) {
      toast({
        title: "Erro ao salvar",
        description: err?.message || String(err),
        variant: "destructive",
      });
    } finally {
      setSubmitting(false);
    }
  };

  const isLoading = loadingMeta || loadingTemplate;

  // Filtra squads disponiveis pelo escritorio do contexto.
  // Template global (officeExternalId == null) → mostra todas.
  const availableSquads = supportSquads.filter((s) => {
    if (officeExternalId == null) return true;
    return s.office_external_id === officeExternalId;
  });

  const newBlocksCount = taskBlocks.filter((b) => !b.id).length;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* `[&>*]:min-w-0` em todos os filhos diretos do DialogContent: o
          shadcn aplica `display: grid` no Content, e grid items default
          `min-width: auto` — entao um Button com texto longo (ex.: subtipo
          "BB Execucao e Encerramento · Impugnacao ao Cumprimento...") +
          `whitespace-nowrap` (default do Button shadcn) faz o item de
          grid estourar a max-width do modal e abrir scrollbar horizontal.
          min-w-0 nos filhos resolve. */}
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto overflow-x-hidden [&>*]:min-w-0">
        <DialogHeader>
          <DialogTitle>
            {mode === "create" ? "Adicionar template" : "Editar template"}
          </DialogTitle>
          <DialogDescription>
            <span className="font-mono text-xs">
              {category}
              {subcategory ? ` / ${subcategory}` : ""}
            </span>
            {officeExternalId == null && (
              <span className="ml-2 text-amber-600 dark:text-amber-400">
                · Template global (todos os escritórios)
              </span>
            )}
          </DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Carregando...
          </div>
        ) : (
          <div className="space-y-4 py-2">
            {/* Header de Tarefas */}
            <div className="flex items-center justify-between">
              <p className="text-sm font-medium">
                Tarefas a criar
                <span className="ml-1 text-xs text-muted-foreground">
                  ({taskBlocks.length})
                </span>
              </p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={addTaskBlock}
                className="h-7 px-2 text-xs"
              >
                <Plus className="mr-1 h-3 w-3" />
                Adicionar tarefa
              </Button>
            </div>

            {taskBlocks.map((block, idx) => {
              const usedSubtypes = subtypesInUseForBlock(idx);
              const canRemove =
                taskBlocks.length > 1 && !(mode === "edit" && block.id);
              return (
                <div
                  key={idx}
                  className="rounded-lg border bg-muted/20 p-4 space-y-4"
                >
                  {/* Block header */}
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                        Tarefa {idx + 1}
                      </p>
                      {mode === "edit" && block.id && (
                        <Badge
                          variant="outline"
                          className="text-[10px] h-4 px-1.5"
                        >
                          Editando existente
                        </Badge>
                      )}
                      {mode === "edit" && !block.id && (
                        <Badge
                          variant="secondary"
                          className="text-[10px] h-4 px-1.5"
                        >
                          Nova
                        </Badge>
                      )}
                    </div>
                    {canRemove && (
                      <button
                        type="button"
                        onClick={() => removeTaskBlock(idx)}
                        className="rounded p-0.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors"
                        title="Remover esta tarefa"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>

                  {/* Subtipo */}
                  <SubtypePicker
                    value={block.task_subtype_external_id}
                    taskTypes={taskTypes}
                    onChange={(subId) =>
                      setBlockField(idx, "task_subtype_external_id", subId)
                    }
                    disabledSubtypeIds={usedSubtypes}
                    label="Subtipo de tarefa"
                    required
                    placeholder="Selecione o subtipo"
                    searchPlaceholder="Buscar por tipo ou subtipo..."
                  />

                  {/* Responsável + assistente + squad */}
                  <div className="grid gap-1.5">
                    <Label className="text-xs">
                      Usuário responsável{" "}
                      <span className="text-muted-foreground font-normal">
                        (opcional — será exigido ao criar a tarefa)
                      </span>
                    </Label>
                    <UserSelector
                      value={
                        block.responsible_user_external_id !== null
                          ? String(block.responsible_user_external_id)
                          : null
                      }
                      users={users.map((u) => ({
                        id: u.external_id,
                        external_id: u.external_id,
                        name: u.name,
                        squads: [],
                      }))}
                      onChange={(strId) =>
                        setBlockField(
                          idx,
                          "responsible_user_external_id",
                          strId !== null ? Number(strId) : null,
                        )
                      }
                      placeholder="Selecione o usuário..."
                    />

                    <div className="flex items-start gap-2 pt-1">
                      <Checkbox
                        id={`tmpl-target-assistant-${idx}`}
                        checked={!!block.target_role_assistant}
                        onCheckedChange={(v) =>
                          setBlockField(idx, "target_role_assistant", !!v)
                        }
                      />
                      <Label
                        htmlFor={`tmpl-target-assistant-${idx}`}
                        className="text-xs font-normal leading-tight cursor-pointer"
                      >
                        Atribuir ao <strong>assistente</strong>
                        {block.target_squad_id
                          ? " da squad escolhida abaixo"
                          : " do responsável"}
                        <span className="block text-muted-foreground">
                          {block.target_squad_id
                            ? "Quando marcado, vai pro assistente da squad de suporte (round-robin). Desmarcado = vai pro líder dessa squad."
                            : "Quando marcado, vai pro assistente da squad principal do responsável."}
                        </span>
                      </Label>
                    </div>

                    <div className="grid gap-1.5 pt-1">
                      <Label className="text-xs">
                        Squad de suporte{" "}
                        <span className="text-muted-foreground font-normal">
                          (opcional — sobrepõe o responsável padrão)
                        </span>
                      </Label>
                      <Select
                        value={block.target_squad_id || "_none"}
                        onValueChange={(v) =>
                          setBlockField(
                            idx,
                            "target_squad_id",
                            v === "_none" ? "" : v,
                          )
                        }
                      >
                        <SelectTrigger className="h-8 text-sm">
                          <SelectValue placeholder="Sem squad de suporte" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="_none">
                            Sem squad de suporte (responsável padrão)
                          </SelectItem>
                          {availableSquads.map((s) => (
                            <SelectItem key={s.id} value={String(s.id)}>
                              {s.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>

                    {/* Exceção por etiqueta (sqd005): processo com essa etiqueta sai da
                        regra acima e fica com a equipe marcada com a mesma etiqueta. */}
                    <div className="grid gap-1.5 pt-1">
                      <Label className="text-xs">
                        Exceção por etiqueta{" "}
                        <span className="text-muted-foreground font-normal">(opcional)</span>
                      </Label>
                      <Select
                        value={block.excecao_etiqueta || "_none"}
                        onValueChange={(v) =>
                          setBlockField(idx, "excecao_etiqueta", v === "_none" ? "" : v)
                        }
                      >
                        <SelectTrigger className="h-8 text-sm">
                          <SelectValue placeholder="Sem exceção" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="_none">Sem exceção (segue a regra acima)</SelectItem>
                          {etiquetasDoSelect(etiquetas, block.excecao_etiqueta).map((e) => (
                            <SelectItem key={e} value={e}>
                              {e}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      {block.excecao_etiqueta && (
                        <div className="grid gap-1.5 rounded-md border border-sky-200 bg-sky-50/60 p-2">
                          <Label className="text-xs">
                            Na equipe {block.excecao_etiqueta}, esta tarefa vai para
                          </Label>
                          <Select
                            value={papelNaExcecao(block)}
                            onValueChange={(v) => setBlockField(idx, "excecao_papel", v)}
                          >
                            <SelectTrigger className="h-8 bg-background text-sm">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="principal">Advogado — o responsável da pasta</SelectItem>
                              <SelectItem value="assistente">Assistente — rodízio da squad da etiqueta</SelectItem>
                            </SelectContent>
                          </Select>
                          <p className="text-[11px] leading-snug text-muted-foreground">
                            Squad de suporte e responsável fixo acima não valem para processo{" "}
                            {block.excecao_etiqueta}. Escolha pelo tipo de trabalho: tarefa do grupo
                            de advogados é "Advogado", mesmo que a squad use o rodízio de assistentes.
                          </p>
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Prazo + Referência + Prioridade.
                      `[&>*]:min-w-0` impede que select com label longo
                      (ex.: "Data atual (quando criar a tarefa)") expanda
                      a coluna alem do 1fr e estoure o modal. Sem isso a
                      tela ganha scrollbar horizontal e o ultimo campo
                      (Prioridade) some pra fora. */}
                  <div className="grid grid-cols-3 gap-3 [&>*]:min-w-0">
                    <div className="grid gap-1.5">
                      <Label className="text-xs">Prazo (dias úteis)</Label>
                      <Input
                        type="number"
                        min={0}
                        max={365}
                        value={block.due_business_days}
                        onChange={(e) =>
                          setBlockField(
                            idx,
                            "due_business_days",
                            Math.max(
                              0,
                              Math.min(365, Number(e.target.value)),
                            ),
                          )
                        }
                        className="h-8 text-sm"
                      />
                    </div>
                    <div className="grid gap-1.5">
                      <Label className="text-xs">Contar a partir de</Label>
                      <Select
                        value={block.due_date_reference || "publication"}
                        onValueChange={(v) =>
                          setBlockField(idx, "due_date_reference", v)
                        }
                      >
                        <SelectTrigger className="h-8 text-sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {DUE_REFERENCE_OPTIONS.map((o) => (
                            <SelectItem
                              key={o.value}
                              value={o.value}
                              className="text-sm"
                            >
                              {o.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="grid gap-1.5">
                      <Label className="text-xs">Prioridade</Label>
                      <Select
                        value={block.priority}
                        onValueChange={(v) => setBlockField(idx, "priority", v)}
                      >
                        <SelectTrigger className="h-8 text-sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {PRIORITIES.map((p) => (
                            <SelectItem
                              key={p.value}
                              value={p.value}
                              className="text-sm"
                            >
                              {p.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>

                  {/* Textos */}
                  <div className="grid gap-2">
                    <p className="text-xs text-muted-foreground">
                      Variáveis:{" "}
                      {VARIAVEIS_DESCRICAO.map((v) => (
                        <code key={v} className="mr-1 rounded bg-muted px-1">{`{${v}}`}</code>
                      ))}
                    </p>
                    <div className="grid gap-1.5">
                      <div className="flex items-center justify-between gap-2">
                        <Label className="text-xs">Descrição da tarefa</Label>
                        {block.description_template !== DESCRICAO_PADRAO && (
                          <button
                            type="button"
                            className="text-[11px] text-primary underline-offset-2 hover:underline"
                            onClick={() => setBlockField(idx, "description_template", DESCRICAO_PADRAO)}
                          >
                            Usar a descrição padrão
                          </button>
                        )}
                      </div>
                      <Textarea
                        rows={2}
                        placeholder={DESCRICAO_PADRAO}
                        value={block.description_template}
                        onChange={(e) =>
                          setBlockField(
                            idx,
                            "description_template",
                            e.target.value,
                          )
                        }
                        className="text-sm"
                      />
                    </div>
                    <div className="grid gap-1.5">
                      <Label className="text-xs">Observações (notas)</Label>
                      <Textarea
                        rows={2}
                        placeholder="Opcional — aparece no campo Notas da tarefa."
                        value={block.notes_template}
                        onChange={(e) =>
                          setBlockField(idx, "notes_template", e.target.value)
                        }
                        className="text-sm"
                      />
                    </div>
                  </div>
                </div>
              );
            })}

            {/* Add another task (bottom shortcut) */}
            <button
              type="button"
              onClick={addTaskBlock}
              className="w-full rounded-lg border border-dashed border-muted-foreground/30 py-2 text-xs text-muted-foreground hover:border-primary/50 hover:text-primary transition-colors flex items-center justify-center gap-1.5"
            >
              <Plus className="h-3.5 w-3.5" />
              Adicionar outra tarefa para esta classificação
            </button>
          </div>
        )}

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            Cancelar
          </Button>
          <Button
            type="button"
            onClick={handleSubmit}
            disabled={submitting || isLoading}
          >
            {submitting
              ? "Salvando..."
              : mode === "create"
                ? `Criar template (${taskBlocks.length} tarefa${taskBlocks.length > 1 ? "s" : ""})`
                : newBlocksCount > 0
                  ? `Salvar + criar ${newBlocksCount} nova${newBlocksCount > 1 ? "s" : ""}`
                  : "Salvar alterações"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default TemplateInlineModal;
