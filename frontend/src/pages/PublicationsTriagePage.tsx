// Triagem de Publicações — onde a publicação vira (ou não vira) tarefa.
//
// A visualização CLÁSSICA continua aqui dentro, inteira, na primeira aba: a
// nova não a substitui, oferece outro caminho. Quem prefere a tabela de
// sempre não perde nada; quem quer a fila por escritório troca de aba.
//
// Na Nova Visão o operador escolhe primeiro o ESCRITÓRIO (que é como a
// operação se organiza) e trata a fila daquele escritório, sempre da
// publicação que espera há mais tempo para a mais recente. Três layouts para
// a mesma fila, porque a forma de trabalhar varia:
//   Foco  — uma por vez, teclado, para zerar fila grande
//   Cards — varredura visual, agrupada por tempo de espera
//   Split — lista + detalhe, para comparar sem perder o contexto
//
// Nada sobe pro Legal One sem passar pelo compositor de tarefas.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft, Bot, Building2, ChevronsUpDown, Filter, LayoutGrid, Loader2,
  PanelsTopLeft, RefreshCw, Sparkles, Table2, Zap, X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList,
} from "@/components/ui/command";
import {
  Popover, PopoverContent, PopoverTrigger,
} from "@/components/ui/popover";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/hooks/use-toast";
import { apiFetch } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import PublicationsPage from "@/pages/PublicationsPage";
import OfficeHub from "@/components/publications/triagem/OfficeHub";
import GroupDetailCard from "@/components/publications/triagem/GroupDetailCard";
import EtiquetasL1 from "@/components/publications/triagem/EtiquetasL1";
import ResponsavelPasta from "@/components/publications/triagem/ResponsavelPasta";
import AuditoriaCard from "@/components/publications/triagem/AuditoriaCard";
import DistribuicaoBar from "@/components/publications/triagem/DistribuicaoBar";
import FeedbackClassificacao from "@/components/publications/triagem/FeedbackClassificacao";
import DuplicataDialog from "@/components/publications/triagem/DuplicataDialog";
import ConfirmarAgendamentoDialog, {
  confirmacaoDispensadaHoje, dispensarConfirmacaoHoje,
} from "@/components/publications/triagem/ConfirmarAgendamentoDialog";
import {
  draftParaPayload, draftValido, faltaMotivoObrigatorio, novoDraft,
} from "@/components/publications/triagem/TaskComposer";
import {
  BANDS, bandIndex, classificacaoDoGrupo, corDoNome, fmtData, idadeDoGrupo,
  iniciais, isoToBrtDate, isoToBrtTime, labelIdade, nomeCurtoEscritorio,
  pathCurto, prazoDoGrupo, prazoInfo, statusDoGrupo, STATUS_LABEL,
  addDiasUteis, hojeBrtDate,
} from "@/components/publications/triagem/helpers";
import type {
  AppUser, DistribuicaoItem, DraftTask, GroupedRecord, GroupedResponse,
  LayoutKind, OfficeSummaryResponse, PublicationRecord, TarefaAbertaL1, TaskType,
  TratadaRecente,
} from "@/components/publications/triagem/types";

const API = "/api/v1/publications";
const API_V1 = "/api/v1";
const STATUS_PENDENTES = "NOVO,CLASSIFICADO,ERRO";
const PAGE_SIZE = 50;

type Aba = "classica" | "nova";

/** Chave estável de um grupo (processo, ou registro avulso). */
const keyDoGrupo = (g: GroupedRecord) =>
  g.lawsuit_id != null ? `l${g.lawsuit_id}` : `r${g.records[0]?.id ?? "?"}`;

/** Monta os rascunhos iniciais: as propostas do template, ou uma tarefa em branco. */
function draftsIniciais(g: GroupedRecord): DraftTask[] {
  const propostas = g.proposed_tasks?.length ? g.proposed_tasks : g.proposed_task ? [g.proposed_task] : [];
  if (propostas.length) {
    return propostas.map((p) => {
      const respId =
        p.participants?.find((x: any) => x?.isResponsible)?.contact?.id ??
        p.suggested_responsible?.id ??
        null;
      return novoDraft({
        description: p.description || "",
        notes: p.notes || "",
        subTypeId: p.subTypeId ?? null,
        typeId: p.typeId ?? null,
        responsibleExternalId: respId,
        dueDate: isoToBrtDate(p.endDateTime) || hojeBrtDate(),
        dueTime: isoToBrtTime(p.endDateTime) || "23:59",
        templateName: p.template_name,
        isCustom: Boolean(p.is_custom),
        priority: p.priority || "Normal",
        responsibleOfficeId: p.responsibleOfficeId ?? g.office_id ?? null,
        // Guarda o proposto para detectar divergência depois.
        origSubTypeId: p.subTypeId ?? null,
        origDueIso: p.endDateTime ?? null,
        respSource: p.suggested_responsible?.source ?? null,
      });
    });
  }
  // Sem template: abre uma tarefa em branco com prazo sugerido (5 dias úteis
  // da publicação), pro operador ajustar em vez de digitar do zero. Sem
  // origSubTypeId/origDueIso: não há proposta da qual divergir.
  const rec = g.records[0];
  const base = rec?.publication_date ? new Date(rec.publication_date) : new Date();
  const sugerido = addDiasUteis(Number.isNaN(base.getTime()) ? new Date() : base, 5);
  const cls = classificacaoDoGrupo(g);
  return [
    novoDraft({
      description: cls
        ? `${cls.categoria} — ${cls.subcategoria}${g.lawsuit_cnj ? ` (${g.lawsuit_cnj})` : ""}`
        : "",
      notes: (rec?.description || rec?.description_preview || "").slice(0, 1500),
      dueDate: isoToBrtDate(sugerido.toISOString()) || hojeBrtDate(),
      dueTime: "23:59",
      responsibleOfficeId: g.office_id ?? null,
      isCustom: true,
    }),
  ];
}

export default function PublicationsTriagePage() {
  const { toast } = useToast();

  const [aba, setAba] = useState<Aba>(() => {
    try {
      return (localStorage.getItem("pub-triagem:aba") as Aba) || "nova";
    } catch {
      return "nova";
    }
  });

  // ── navegação ──
  const [officeId, setOfficeId] = useState<number | null>(null);
  const [layout, setLayout] = useState<LayoutKind>(() => {
    try {
      return (localStorage.getItem("pub-triagem:layout") as LayoutKind) || "focus";
    } catch {
      return "focus";
    }
  });

  // ── filtros ──
  const [category, setCategory] = useState<string>("");
  const [subcategory, setSubcategory] = useState<string>("");
  const [banda, setBanda] = useState<number | null>(null);
  const [order, setOrder] = useState<"antigas" | "urgencia">("antigas");
  const [cnjBusca, setCnjBusca] = useState("");
  const [filtroLeitor, setFiltroLeitor] = useState<string[]>([]);
  const [filtroEtiqueta, setFiltroEtiqueta] = useState<string>("");
  const [etiquetasDisponiveis, setEtiquetasDisponiveis] = useState<string[]>([]);
  const [filtroUf, setFiltroUf] = useState<string>("");
  // Rito (pub016). Vocabulário FIXO — não vem de available_*, porque as
  // opções existem mesmo quando a fila filtrada não tem nenhuma delas.
  const [filtroRito, setFiltroRito] = useState<string>("");
  const [ufsDisponiveis, setUfsDisponiveis] = useState<string[]>([]);
  const [filtroRespPasta, setFiltroRespPasta] = useState<number | null>(null);
  const [respPastaDisponiveis, setRespPastaDisponiveis] = useState<
    { id: number; nome: string; total: number }[]
  >([]);
  // Motor da fila sem pasta (escritório fictício -1): última rodada e se
  // há uma em andamento — a tela acompanha o servidor, não o contrário.
  const [runSemPasta, setRunSemPasta] = useState<any | null>(null);
  const [rodandoSemPasta, setRodandoSemPasta] = useState(false);

  // ── dados ──
  const [hub, setHub] = useState<OfficeSummaryResponse | null>(null);
  const [hubLoading, setHubLoading] = useState(true);
  const [groups, setGroups] = useState<GroupedRecord[]>([]);
  const [filaLoading, setFilaLoading] = useState(false);
  const [taxonomy, setTaxonomy] = useState<Record<string, string[]>>({});
  // Publicação sob feedback de classificação errada (null = diálogo fechado).
  const [feedbackDe, setFeedbackDe] = useState<PublicationRecord | null>(null);
  const [taskTypes, setTaskTypes] = useState<TaskType[]>([]);
  const [users, setUsers] = useState<AppUser[]>([]);
  const [distribuicao, setDistribuicao] = useState<DistribuicaoItem[]>([]);
  const [auditoria, setAuditoria] = useState<TratadaRecente[]>([]);
  const [auditoriaAberta, setAuditoriaAberta] = useState(false);
  const [auditoriaLoading, setAuditoriaLoading] = useState(false);

  // ── estado de trabalho ──
  const [cursor, setCursor] = useState(0);
  const [selKey, setSelKey] = useState<string | null>(null);
  const [draftsByKey, setDraftsByKey] = useState<Record<string, DraftTask[]>>({});
  const [consultouPorKey, setConsultouPorKey] = useState<Record<string, boolean>>({});
  const [duplicatas, setDuplicatas] = useState<Record<number, TarefaAbertaL1[]>>({});
  // Agendamento barrado por duplicata: guarda o grupo para o operador poder
  // confirmar depois de ver o que já existe (null = diálogo fechado).
  const [conflito, setConflito] = useState<
    { grupo: GroupedRecord; quantas: number; subtipos: number[] } | null
  >(null);
  // Texto integral por record — a listagem só traz 200 caracteres.
  const [textos, setTextos] = useState<Record<number, PublicationRecord>>({});
  const [submitting, setSubmitting] = useState(false);
  const [tratadas, setTratadas] = useState(0);
  const [visiveis, setVisiveis] = useState(PAGE_SIZE);
  const [confirmarPara, setConfirmarPara] = useState<GroupedRecord | null>(null);

  const trocarAba = (v: Aba) => {
    setAba(v);
    try { localStorage.setItem("pub-triagem:aba", v); } catch { /* ok */ }
  };

  const filtrosQS = useCallback(
    (extra: Record<string, string | number | undefined> = {}) => {
      const qs = new URLSearchParams();
      qs.set("status", STATUS_PENDENTES);
      // Publicação sem pasta é da fila própria (card "-1"), não da fila do
      // escritório real — assim o número do card bate com o da fila aberta.
      qs.set("separar_sem_pasta", "true");
      if (category) qs.set("category", category);
      if (subcategory) qs.set("subcategory", subcategory);
      if (cnjBusca.trim()) qs.set("cnj_search", cnjBusca.trim());
      if (filtroLeitor.length) qs.set("distribuido_para", filtroLeitor.join(","));
      if (filtroEtiqueta) qs.set("etiqueta", filtroEtiqueta);
      if (filtroUf) qs.set("uf", filtroUf);
      if (filtroRito) qs.set("rito", filtroRito);
      if (filtroRespPasta) qs.set("responsavel_pasta", String(filtroRespPasta));
      if (banda != null) {
        qs.set("idade_min_dias", String(BANDS[banda].min));
        if (BANDS[banda].max < 9999) qs.set("idade_max_dias", String(BANDS[banda].max));
      }
      Object.entries(extra).forEach(([k, v]) => {
        if (v !== undefined && v !== "") qs.set(k, String(v));
      });
      return qs;
    },
    [category, subcategory, cnjBusca, banda, filtroLeitor, filtroEtiqueta, filtroRespPasta, filtroUf, filtroRito],
  );

  /* ─── carregamentos ─── */
  const carregarHub = useCallback(async () => {
    if (aba !== "nova") return;
    setHubLoading(true);
    try {
      const res = await apiFetch(`${API}/records/office-summary?${filtrosQS().toString()}`);
      if (!res.ok) throw new Error("Falha ao carregar o resumo por escritório");
      setHub(await res.json());
    } catch (e: any) {
      toast({ title: "Erro ao carregar escritórios", description: e?.message, variant: "destructive" });
    } finally {
      setHubLoading(false);
    }
  }, [aba, filtrosQS, toast]);

  const carregarFila = useCallback(async () => {
    if (aba !== "nova" || officeId === null) return;
    setFilaLoading(true);
    try {
      const qs = filtrosQS({ linked_office_id: officeId, order: "urgencia", limit: 200, offset: 0 });
      const res = await apiFetch(`${API}/records/grouped?${qs.toString()}`);
      if (!res.ok) throw new Error("Falha ao carregar a fila");
      const data: GroupedResponse = await res.json();
      setGroups(data.groups || []);
      // O backend calcula `available_etiquetas` ignorando o próprio filtro de
      // etiqueta, então a lista não encolhe ao escolher uma.
      setEtiquetasDisponiveis(data.available_etiquetas || []);
      setRespPastaDisponiveis(data.available_responsaveis || []);
      // Como o de etiqueta, o vocabulário de UF vem calculado SEM o próprio
      // filtro de UF — a lista não encolhe ao escolher um estado.
      setUfsDisponiveis(data.available_ufs || []);
      setCursor(0);
      setVisiveis(PAGE_SIZE);
    } catch (e: any) {
      toast({ title: "Erro ao carregar a fila", description: e?.message, variant: "destructive" });
    } finally {
      setFilaLoading(false);
    }
  }, [aba, officeId, filtrosQS, toast]);

  const carregarDistribuicao = useCallback(async () => {
    if (aba !== "nova") return;
    try {
      const qs = new URLSearchParams({ status: STATUS_PENDENTES });
      if (officeId !== null) qs.set("linked_office_id", String(officeId));
      const res = await apiFetch(`${API}/records/distribuicao-summary?${qs.toString()}`);
      if (res.ok) setDistribuicao((await res.json()).itens || []);
    } catch {
      /* chips de leitura são acessórios */
    }
  }, [aba, officeId]);

  const carregarAuditoria = useCallback(async () => {
    if (aba !== "nova" || officeId === null) return;
    setAuditoriaLoading(true);
    try {
      const res = await apiFetch(
        `${API}/records/tratadas-recentes?linked_office_id=${officeId}&limit=25`,
      );
      if (res.ok) setAuditoria((await res.json()).itens || []);
    } catch {
      /* idem */
    } finally {
      setAuditoriaLoading(false);
    }
  }, [aba, officeId]);

  // catálogos: tipos e usuários uma vez só; a taxonomia recarrega quando o
  // escritório muda, porque a fila sem pasta (-1) tem árvore PRÓPRIA e plana —
  // com a árvore normal o filtro de Classificação não acharia nada lá.
  useEffect(() => {
    (async () => {
      try {
        const qsTax = officeId != null ? `?office_external_id=${officeId}` : "";
        const [tax, tt, us] = await Promise.all([
          apiFetch(`${API}/classification-taxonomy${qsTax}`),
          apiFetch(`${API_V1}/task-templates/meta/task-types`),
          apiFetch(`${API_V1}/task-templates/meta/users`),
        ]);
        if (tax.ok) {
          const j = await tax.json();
          setTaxonomy(j?.taxonomy && typeof j.taxonomy === "object" ? j.taxonomy : j || {});
        }
        if (tt.ok) setTaskTypes(await tt.json());
        if (us.ok) setUsers(await us.json());
      } catch {
        /* catálogo indisponível não impede a triagem */
      }
    })();
  }, [officeId]);

  useEffect(() => { void carregarHub(); }, [carregarHub]);
  useEffect(() => { void carregarFila(); }, [carregarFila]);
  useEffect(() => { void carregarDistribuicao(); }, [carregarDistribuicao]);
  useEffect(() => { void carregarAuditoria(); }, [carregarAuditoria]);

  /* ─── fila derivada ─── */
  const fila = useMemo(() => {
    const arr = [...groups];
    if (order === "antigas") arr.sort((a, b) => idadeDoGrupo(b) - idadeDoGrupo(a));
    else {
      arr.sort((a, b) => {
        const pa = prazoDoGrupo(a) ?? "9999-12-31";
        const pb = prazoDoGrupo(b) ?? "9999-12-31";
        return pa === pb ? idadeDoGrupo(b) - idadeDoGrupo(a) : pa < pb ? -1 : 1;
      });
    }
    return arr;
  }, [groups, order]);

  const atual = fila[Math.min(cursor, Math.max(0, fila.length - 1))] ?? null;
  const selecionado = useMemo(
    () => fila.find((g) => keyDoGrupo(g) === selKey) ?? fila[0] ?? null,
    [fila, selKey],
  );

  const draftsDe = useCallback(
    (g: GroupedRecord | null): DraftTask[] => {
      if (!g) return [];
      return draftsByKey[keyDoGrupo(g)] ?? draftsIniciais(g);
    },
    [draftsByKey],
  );

  const setDraftsDe = useCallback((g: GroupedRecord, d: DraftTask[]) => {
    setDraftsByKey((prev) => ({ ...prev, [keyDoGrupo(g)]: d }));
  }, []);

  const nomePorExternalId = useCallback(
    (id: number | null) => users.find((u) => u.external_id === id)?.name || "sem responsável",
    [users],
  );

  /* ─── checagem de tarefa já aberta no L1 ─── */
  const grupoVisivel = layout === "split" ? selecionado : atual;

  /* ─── texto integral da publicação em foco ─── */
  useEffect(() => {
    if (aba !== "nova" || !grupoVisivel) return;
    const faltando = grupoVisivel.records.filter((r) => !textos[r.id]).map((r) => r.id);
    if (!faltando.length) return;
    let cancelado = false;
    (async () => {
      for (const id of faltando) {
        try {
          const res = await apiFetch(`${API}/records/${id}`);
          if (!res.ok) continue;
          const det = await res.json();
          if (cancelado) return;
          setTextos((prev) => ({ ...prev, [id]: det }));
        } catch {
          /* sem o integral a tela ainda mostra o trecho da listagem */
        }
      }
    })();
    return () => { cancelado = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aba, grupoVisivel]);

  /**
   * Devolve o grupo com o texto integral (e as classificações completas)
   * já mesclados nos records que chegaram do detalhe.
   */
  const comTextoIntegral = useCallback(
    (g: GroupedRecord | null): GroupedRecord | null => {
      if (!g) return null;
      if (!g.records.some((r) => textos[r.id])) return g;
      return {
        ...g,
        records: g.records.map((r) => (textos[r.id] ? { ...r, ...textos[r.id] } : r)),
      };
    },
    [textos],
  );

  // Chave ESTAVEL dos subtipos escolhidos. Existe porque a checagem de
  // duplicata precisa refazer quando o operador TROCA ou ADICIONA subtipo,
  // mas nao pode refazer a cada tecla da descricao — e depender de `drafts`
  // inteiro faria isso. Recalcular uma string e barato; a chamada de rede
  // e que nao pode repetir.
  const subtiposKey = useMemo(() => {
    if (!grupoVisivel) return "";
    return draftsDe(grupoVisivel)
      .filter((d) => d.subTypeId && !d.removida)
      .map((d) => d.subTypeId as number)
      .sort((a, b) => a - b)
      .join(",");
  }, [grupoVisivel, draftsDe]);

  useEffect(() => {
    if (aba !== "nova" || !grupoVisivel?.lawsuit_id) { setDuplicatas({}); return; }
    const subtipos = draftsDe(grupoVisivel)
      .filter((d) => d.subTypeId && !d.removida)
      .map((d) => d.subTypeId as number);
    if (!subtipos.length) { setDuplicatas({}); return; }
    let cancelado = false;
    (async () => {
      try {
        const res = await apiFetch(`${API}/groups/${grupoVisivel.lawsuit_id}/check-duplicates`, {
          method: "POST",
          body: JSON.stringify({ subtype_ids: [...new Set(subtipos)] }),
        });
        if (!res.ok) return;
        const j = await res.json();
        if (!cancelado) setDuplicatas(j?.duplicates_by_subtype || {});
      } catch {
        /* sem a checagem, a tela segue: o backend ainda barra duplicata */
      }
    })();
    return () => { cancelado = true; };
    // Depende da CHAVE dos subtipos, nao de `drafts`: refaz quando o operador
    // troca/adiciona subtipo (que e quando a resposta muda) e nao refaz
    // enquanto ele digita. Antes dependia so do grupo, entao a checagem
    // rodava UMA vez com os subtipos iniciais — trocar o subtipo depois
    // deixava o aviso invisivel e o operador batia no 409 sem nunca ter
    // sido avisado. Era esse o beco.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aba, grupoVisivel?.lawsuit_id, subtiposKey]);

  /* ─── ações ─── */
  const removerDaFila = useCallback((g: GroupedRecord) => {
    const k = keyDoGrupo(g);
    setGroups((prev) => prev.filter((x) => keyDoGrupo(x) !== k));
    setDraftsByKey((prev) => { const c = { ...prev }; delete c[k]; return c; });
    setTratadas((n) => n + 1);
  }, []);

  // `forcar` chega do diálogo de duplicata: o operador VIU as tarefas em
  // aberto e decidiu agendar mesmo assim. Duplicata nunca é veto — é aviso.
  const enviarAgendamento = useCallback(async (g: GroupedRecord, forcar = false) => {
    const todos = draftsDe(g);
    const drafts = todos.filter(draftValido);
    if (!drafts.length) {
      toast({ title: "Nada a agendar", description: "Preencha descrição, subtipo e responsável.", variant: "destructive" });
      return;
    }
    const pendente = todos.find(faltaMotivoObrigatorio);
    if (pendente) {
      toast({
        title: "Falta justificar a data",
        description: "Desvio maior que 10 dias exige o motivo.",
        variant: "destructive",
      });
      return;
    }

    setSubmitting(true);
    try {
      const consultou = consultouPorKey[keyDoGrupo(g)] === true;
      // Motivo da tarefa removida pega carona no primeiro payload enviado —
      // é assim que o backend espera recebê-lo.
      const motivoRemocao = todos.find((d) => d.removida && d.removidaMotivo)?.removidaMotivo ?? null;
      const payloads = drafts.map((d, i) =>
        draftParaPayload(d, g.office_id, {
          consultouAutos: consultou,
          motivoRemocao: i === 0 ? motivoRemocao : null,
        }),
      );
      const temAberta = drafts.some((d) => d.tarefaAbertaMotivo);
      const recordIds = g.records.map((r) => r.id);
      const url = g.lawsuit_id
        ? `${API}/groups/${g.lawsuit_id}/schedule`
        : `${API}/groups/records/schedule`;
      const forcarDuplicata = temAberta || forcar;
      const body = g.lawsuit_id
        ? { payload_overrides: payloads, record_ids: recordIds, force_duplicate: forcarDuplicata }
        : { record_ids: recordIds, payload_overrides: payloads, force_duplicate: forcarDuplicata };

      const res = await apiFetch(url, { method: "POST", body: JSON.stringify(body) });
      if (res.status === 409) {
        // Duplicata NÃO barra: mostra o que já existe no L1 e devolve a
        // decisão ao operador. O toast vermelho de antes era um beco — ele
        // mandava "reenvie com force_duplicate=true", que é parâmetro de
        // API e ninguém na mesa tem como fazer.
        const j = await res.json().catch(() => ({}));
        const quantas = Number(
          String(j?.detail || "").match(/^DUPLICATE_BLOCKED:(\d+):/)?.[1] || 0,
        );
        setConflito({
          grupo: g,
          quantas,
          subtipos: [...new Set(drafts.map((d) => d.subTypeId).filter(Boolean) as number[])],
        });
        return;
      }
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j?.detail || `HTTP ${res.status}`);
      }
      toast({
        title: `${drafts.length} tarefa(s) enviada(s) ao Legal One`,
        description: g.lawsuit_cnj || "publicação sem processo vinculado",
      });
      removerDaFila(g);
      void carregarHub();
      void carregarAuditoria();
      void carregarDistribuicao();
    } catch (e: any) {
      toast({ title: "Falha ao agendar", description: e?.message, variant: "destructive" });
    } finally {
      setSubmitting(false);
      setConfirmarPara(null);
    }
  }, [draftsDe, consultouPorKey, toast, removerDaFila, carregarHub, carregarAuditoria, carregarDistribuicao]);

  /** Pede confirmação na primeira vez do dia; depois vai direto. */
  const pedirAgendamento = useCallback((g: GroupedRecord) => {
    if (confirmacaoDispensadaHoje()) { void enviarAgendamento(g); return; }
    setConfirmarPara(g);
  }, [enviarAgendamento]);

  const ignorar = useCallback(async (g: GroupedRecord, motivo: string, nota: string) => {
    setSubmitting(true);
    try {
      const consultou = consultouPorKey[keyDoGrupo(g)] === true;
      const resultados = await Promise.allSettled(
        g.records.map((r) =>
          apiFetch(`${API}/records/${r.id}`, {
            method: "PATCH",
            body: JSON.stringify({
              status: "IGNORADO",
              ignore_reason: motivo,
              ignore_reason_note: nota || null,
              consultou_autos: consultou,
            }),
          }),
        ),
      );
      const falhou = resultados.some(
        (r) => r.status === "rejected" || (r.value as Response)?.ok === false,
      );
      if (falhou) throw new Error("Algumas publicações não puderam ser ignoradas.");
      toast({ title: "Publicação ignorada", description: g.lawsuit_cnj || "sem processo" });
      removerDaFila(g);
      void carregarHub();
      void carregarAuditoria();
    } catch (e: any) {
      toast({ title: "Falha ao ignorar", description: e?.message, variant: "destructive" });
    } finally {
      setSubmitting(false);
    }
  }, [consultouPorKey, toast, removerDaFila, carregarHub, carregarAuditoria]);

  /* ─── motor da fila sem pasta ─── */
  const classificarSemPasta = useCallback(async () => {
    try {
      const res = await apiFetch(`${API}/sem-pasta/classificar`, {
        method: "POST",
        body: JSON.stringify({ limite: 500 }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const j = await res.json();
      setRodandoSemPasta(true);
      toast({
        title: "Motor da fila sem pasta iniciado",
        description: `${j.total_alvo ?? 0} publicação(ões) na rodada. Pauta sai por regra; o resto vai pra IA.`,
      });
    } catch (e: any) {
      toast({ title: "Não consegui iniciar o motor", description: e?.message, variant: "destructive" });
    }
  }, [toast]);

  useEffect(() => {
    if (officeId !== -1) return;
    let ativo = true;
    const tick = async () => {
      try {
        const res = await apiFetch(`${API}/sem-pasta/runs?limit=1`);
        if (!res.ok) return;
        const j = await res.json();
        const r = j?.runs?.[0] ?? null;
        if (!ativo) return;
        setRunSemPasta(r);
        const rodando = r?.status === "running";
        if (rodando && !rodandoSemPasta) setRodandoSemPasta(true);
        if (!rodando && rodandoSemPasta) {
          setRodandoSemPasta(false);
          // Rodada que não pegou a trava: outra já estava correndo. Dizer
          // "identificada" aqui seria mentira — e o operador clicaria de novo.
          if (r?.status === "skipped") {
            toast({
              title: "Já havia uma rodada em andamento",
              description: "Esta não rodou, para não classificar (nem agendar) a mesma publicação duas vezes. Acompanhe a que está correndo.",
            });
            return;
          }
          toast({
            title: "Fila sem pasta identificada",
            description:
              `${r?.classificados ?? 0} classificada(s), ${r?.fichas ?? 0} com ficha, ` +
              `${r?.pautas ?? 0} pauta(s) por regra, ${r?.erros ?? 0} erro(s).` +
              // Só aparece quando houve agendamento automático: com o motor
              // desligado (o default) essa contagem seria ruído.
              (r?.agendados ? ` ${r.agendados} tarefa(s) já agendada(s) automaticamente.` : ""),
          });
          void carregarFila();
          void carregarHub();
        }
      } catch {
        /* acompanhamento é conveniência; falha não interrompe a triagem */
      }
    };
    void tick();
    const id = window.setInterval(tick, 3000);
    return () => { ativo = false; window.clearInterval(id); };
  }, [officeId, rodandoSemPasta, carregarFila, carregarHub, toast]);

  const distribuir = useCallback(async (userIds: number[], sobrescrever: boolean) => {
    try {
      const res = await apiFetch(`${API}/records/distribuir-leitura`, {
        method: "POST",
        body: JSON.stringify({
          user_ids: userIds,
          sobrescrever,
          status: STATUS_PENDENTES,
          linked_office_id: officeId !== null ? String(officeId) : null,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const j = await res.json();
      toast({
        title: `${j.distribuidas} publicação(ões) repartida(s)`,
        description: (j.por_pessoa || [])
          .map((p: any) => `${p.nome}: ${p.quantidade}`)
          .join(" · "),
      });
      void carregarDistribuicao();
      void carregarFila();
    } catch (e: any) {
      toast({ title: "Falha ao distribuir", description: e?.message, variant: "destructive" });
    }
  }, [officeId, toast, carregarDistribuicao, carregarFila]);

  const limparDistribuicao = useCallback(async () => {
    try {
      const res = await apiFetch(`${API}/records/limpar-distribuicao`, {
        method: "POST",
        body: JSON.stringify({
          status: STATUS_PENDENTES,
          linked_office_id: officeId !== null ? String(officeId) : null,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const j = await res.json();
      toast({ title: `${j.limpas} tag(s) removida(s)` });
      setFiltroLeitor([]);
      void carregarDistribuicao();
      void carregarFila();
    } catch (e: any) {
      toast({ title: "Falha ao limpar", description: e?.message, variant: "destructive" });
    }
  }, [officeId, toast, carregarDistribuicao, carregarFila]);

  /* ─── teclado (Foco e Split) ─── */
  const pedirRef = useRef(pedirAgendamento);
  pedirRef.current = pedirAgendamento;
  useEffect(() => {
    if (aba !== "nova" || officeId === null) return;
    const onKey = (e: KeyboardEvent) => {
      const alvo = e.target as HTMLElement | null;
      if (alvo && /^(INPUT|TEXTAREA|SELECT)$/.test(alvo.tagName)) return;
      if (alvo?.isContentEditable) return;
      const g = layout === "split" ? selecionado : atual;
      if (!g) return;
      if (e.key === "Enter") { e.preventDefault(); pedirRef.current(g); }
      else if (e.key === "ArrowRight" && layout === "focus") setCursor((c) => Math.min(fila.length - 1, c + 1));
      else if (e.key === "ArrowLeft" && layout === "focus") setCursor((c) => Math.max(0, c - 1));
      else if (layout === "split" && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
        e.preventDefault();
        const i = fila.findIndex((x) => keyDoGrupo(x) === keyDoGrupo(g));
        const prox = e.key === "ArrowDown" ? Math.min(fila.length - 1, i + 1) : Math.max(0, i - 1);
        if (fila[prox]) setSelKey(keyDoGrupo(fila[prox]));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [aba, officeId, layout, atual, selecionado, fila]);

  const trocarLayout = (l: LayoutKind) => {
    setLayout(l);
    try { localStorage.setItem("pub-triagem:layout", l); } catch { /* ok */ }
  };

  const filtroLabel = useMemo(() => {
    const p: string[] = [];
    if (category) p.push(category);
    if (subcategory) p.push(subcategory);
    return p.length ? p.join(" › ") : null;
  }, [category, subcategory]);

  const escritorioAtual = hub?.offices.find((o) => o.office_id === officeId) ?? null;

  /* ═══════════ cabeçalho com as duas visualizações ═══════════ */
  const abas = (
    <div className="flex flex-wrap items-center gap-3">
      <div>
        <h1 className="text-xl font-bold tracking-tight">Triagem de Publicações</h1>
        <p className="text-xs text-muted-foreground">
          Ler, decidir a providência e agendar a tarefa no Legal One.
        </p>
      </div>
      <div className="ml-auto inline-flex rounded-xl bg-muted p-1">
        <button
          type="button"
          onClick={() => trocarAba("classica")}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-semibold transition-colors",
            aba === "classica" ? "bg-background shadow-sm" : "text-muted-foreground hover:text-foreground",
          )}
        >
          <Table2 className="h-3.5 w-3.5" /> Visualização clássica
        </button>
        <button
          type="button"
          onClick={() => trocarAba("nova")}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-semibold transition-colors",
            aba === "nova" ? "bg-background shadow-sm" : "text-muted-foreground hover:text-foreground",
          )}
        >
          <Sparkles className={cn("h-3.5 w-3.5", aba === "nova" && "text-primary")} /> Nova Visão
        </button>
      </div>
    </div>
  );

  if (aba === "classica") {
    return (
      <div className="space-y-4 p-4 md:p-6">
        {abas}
        {/* Só a metade de tratamento: a busca e os lotes de classificação
            moram na outra frente do módulo (/publications/classificacao). */}
        <PublicationsPage secao="tratamento" />
      </div>
    );
  }

  /* ═══════════════ HUB ═══════════════ */
  if (officeId === null) {
    return (
      <div className="space-y-5 p-4 md:p-6">
        {abas}
        <FiltroClassificacao
          taxonomy={taxonomy}
          category={category} subcategory={subcategory}
          onCategory={(v) => { setCategory(v); setSubcategory(""); }}
          onSubcategory={setSubcategory}
          cnj={cnjBusca} onCnj={setCnjBusca}
          onRefresh={() => void carregarHub()}
          loading={hubLoading}
        />
        <DistribuicaoBar
          itens={distribuicao}
          users={users}
          filtro={filtroLeitor}
          onFiltro={setFiltroLeitor}
          onDistribuir={distribuir}
          onLimpar={limparDistribuicao}
          escopo="todos os escritórios"
        />
        <OfficeHub
          data={hub}
          loading={hubLoading}
          onPick={(id) => { setOfficeId(id); setSelKey(null); setCursor(0); setAuditoriaAberta(false); }}
          filtroLabel={filtroLabel}
        />
      </div>
    );
  }

  /* ═══════════════ FILA ═══════════════ */
  const vazia = !filaLoading && fila.length === 0;
  const draftsAtuais = grupoVisivel ? draftsDe(grupoVisivel) : [];

  return (
    <div className="space-y-4 p-4 md:p-6">
      {abas}

      <div className="flex flex-wrap items-center gap-3 rounded-2xl border bg-card/70 p-3 shadow-sm backdrop-blur">
        <Button variant="outline" size="icon" onClick={() => setOfficeId(null)} title="Voltar aos escritórios">
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <Building2 className="h-3 w-3 shrink-0" />
            <span className="truncate">{pathCurto(escritorioAtual?.office_path ?? null) || `escritório ${officeId}`}</span>
          </div>
          <div className="flex items-center gap-2 text-base font-bold">
            {escritorioAtual ? nomeCurtoEscritorio(escritorioAtual) : `Escritório ${officeId}`}
            {escritorioAtual?.polo_scope && (
              <Badge variant="outline" className="text-[10px] uppercase">
                {escritorioAtual.polo_scope === "sem_pasta" ? "Fila sem pasta" : `Polo ${escritorioAtual.polo_scope}`}
              </Badge>
            )}
          </div>
        </div>
        <Badge variant="secondary" className="ml-1">
          {filaLoading ? "carregando…" : `${fila.length} na fila`}
        </Badge>
        {tratadas > 0 && (
          <Badge className="bg-emerald-100 text-emerald-800 hover:bg-emerald-100">
            {tratadas} tratada(s) nesta sessão
          </Badge>
        )}

        <div className="ml-auto flex flex-wrap items-center gap-2">
          {officeId === -1 && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => void classificarSemPasta()}
              disabled={rodandoSemPasta}
              title="Roda o motor próprio da fila sem pasta: pauta sai por regra, o resto é identificado pela IA e os tipos críticos ganham ficha de cadastro"
            >
              {rodandoSemPasta
                ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                : <Bot className="mr-1.5 h-3.5 w-3.5" />}
              {rodandoSemPasta && runSemPasta
                ? `Identificando… ${runSemPasta.processados ?? 0}/${runSemPasta.total_alvo ?? 0}`
                : "Identificar o que é (IA)"}
            </Button>
          )}
          <Select value={order} onValueChange={(v) => setOrder(v as any)}>
            <SelectTrigger className="h-9 w-[190px] text-sm"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="antigas">Mais antigas primeiro</SelectItem>
              <SelectItem value="urgencia">Menor prazo primeiro</SelectItem>
            </SelectContent>
          </Select>
          <div className="inline-flex rounded-xl bg-muted p-1">
            {([
              { k: "focus" as const, icon: Zap, label: "Foco" },
              { k: "cards" as const, icon: LayoutGrid, label: "Cards" },
              { k: "split" as const, icon: PanelsTopLeft, label: "Split" },
            ]).map(({ k, icon: Icon, label }) => (
              <button
                key={k}
                type="button"
                onClick={() => trocarLayout(k)}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-semibold transition-colors",
                  layout === k ? "bg-background shadow-sm" : "text-muted-foreground hover:text-foreground",
                )}
              >
                <Icon className="h-3.5 w-3.5" /> {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <FiltroClassificacao
        taxonomy={taxonomy}
        category={category} subcategory={subcategory}
        onCategory={(v) => { setCategory(v); setSubcategory(""); }}
        onSubcategory={setSubcategory}
        cnj={cnjBusca} onCnj={setCnjBusca}
        onRefresh={() => { void carregarFila(); void carregarHub(); }}
        loading={filaLoading}
        banda={banda} onBanda={setBanda}
        etiquetas={etiquetasDisponiveis}
        etiqueta={filtroEtiqueta}
        onEtiqueta={setFiltroEtiqueta}
        responsaveis={respPastaDisponiveis}
        responsavel={filtroRespPasta}
        onResponsavel={setFiltroRespPasta}
        ufs={ufsDisponiveis}
        uf={filtroUf}
        rito={filtroRito}
        onRito={setFiltroRito}
        onUf={setFiltroUf}
      />

      <DistribuicaoBar
        itens={distribuicao}
        users={users}
        filtro={filtroLeitor}
        onFiltro={setFiltroLeitor}
        onDistribuir={distribuir}
        onLimpar={limparDistribuicao}
        escopo={escritorioAtual ? nomeCurtoEscritorio(escritorioAtual) : undefined}
      />

      <AuditoriaCard
        itens={auditoria}
        loading={auditoriaLoading}
        aberto={auditoriaAberta}
        onOpenChange={(v) => { setAuditoriaAberta(v); if (v) void carregarAuditoria(); }}
      />

      {filaLoading && (
        <div className="flex items-center justify-center py-20 text-muted-foreground">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" /> Carregando a fila…
        </div>
      )}

      {vazia && (
        <div className="rounded-2xl border border-dashed bg-card/60 py-20 text-center">
          <div className="text-5xl">🎉</div>
          <h2 className="mt-3 text-xl font-semibold">Fila zerada</h2>
          <p className="mt-1 text-muted-foreground">
            {tratadas > 0
              ? `Você tratou ${tratadas} publicação(ões) nesta sessão.`
              : "Nada pendente neste escritório com os filtros atuais."}
          </p>
          <Button className="mt-5" onClick={() => setOfficeId(null)}>
            <ArrowLeft className="mr-2 h-4 w-4" /> Escolher outro escritório
          </Button>
        </div>
      )}

      {!filaLoading && fila.length > 0 && layout === "focus" && atual && (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_260px]">
          <div className="min-w-0 space-y-3">
            <div className="flex items-center gap-3">
              <span className="whitespace-nowrap text-sm font-semibold text-muted-foreground">
                {cursor + 1} de {fila.length}
              </span>
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                <span
                  className="block h-full rounded-full bg-primary transition-all"
                  style={{ width: `${((cursor + 1) / fila.length) * 100}%` }}
                />
              </div>
            </div>
            <GroupDetailCard
              key={keyDoGrupo(atual)}
              group={comTextoIntegral(atual) as GroupedRecord}
              drafts={draftsAtuais}
              onDraftsChange={(d) => setDraftsDe(atual, d)}
              taskTypes={taskTypes}
              users={users}
              duplicatasPorSubtipo={duplicatas}
              consultouAutos={consultouPorKey[keyDoGrupo(atual)] === true}
              onConsultouAutos={(v) =>
                setConsultouPorKey((prev) => ({ ...prev, [keyDoGrupo(atual)]: v }))
              }
              onConfirm={() => pedirAgendamento(atual)}
              onIgnore={(m, n) => void ignorar(atual, m, n)}
              onFeedback={setFeedbackDe}
              onSkip={() => setCursor((c) => Math.min(fila.length - 1, c + 1))}
              onBack={cursor > 0 ? () => setCursor((c) => Math.max(0, c - 1)) : undefined}
              submitting={submitting}
              showSkip
              kbdHints
            />
          </div>
          <aside className="hidden xl:block">
            <div className="sticky top-4 space-y-2">
              <h4 className="text-[11px] font-extrabold uppercase tracking-wide text-muted-foreground">A seguir</h4>
              {fila.slice(cursor + 1, cursor + 6).map((g, i) => {
                const idade = idadeDoGrupo(g);
                return (
                  <button
                    key={keyDoGrupo(g)}
                    type="button"
                    onClick={() => setCursor(cursor + 1 + i)}
                    className="w-full rounded-xl border bg-card/60 p-2.5 text-left transition-colors hover:border-primary/40"
                  >
                    <span className={cn("inline-flex rounded-full border px-2 py-0.5 text-[10px] font-semibold", BANDS[bandIndex(idade)].chip)}>
                      há {idade}d
                    </span>
                    <div className="mt-1 truncate text-xs font-bold">
                      {classificacaoDoGrupo(g)?.subcategoria || "Aguardando classificação"}
                    </div>
                    <div className="truncate font-mono text-[10px] text-muted-foreground">
                      {g.lawsuit_cnj || "sem processo"}
                    </div>
                    <EtiquetasL1 etiquetas={g.l1_etiquetas} className="mt-1" />
                    {g.responsavel_pasta && (
                      <ResponsavelPasta responsavel={g.responsavel_pasta} className="mt-1" />
                    )}
                  </button>
                );
              })}
              {fila.length > cursor + 6 && (
                <p className="pt-1 text-center text-[11px] text-muted-foreground">
                  + {fila.length - cursor - 6} depois dessas
                </p>
              )}
            </div>
          </aside>
        </div>
      )}

      {!filaLoading && fila.length > 0 && layout === "cards" && (
        <CardsLayout
          fila={fila}
          visiveis={visiveis}
          onMais={() => setVisiveis((v) => v + PAGE_SIZE)}
          onAbrir={(g) => {
            const i = fila.findIndex((x) => keyDoGrupo(x) === keyDoGrupo(g));
            setCursor(Math.max(0, i));
            trocarLayout("focus");
          }}
        />
      )}

      {!filaLoading && fila.length > 0 && layout === "split" && selecionado && (
        <div className="grid gap-0 overflow-hidden rounded-2xl border bg-card/70 shadow-sm backdrop-blur lg:grid-cols-[340px_minmax(0,1fr)]">
          <div className="max-h-[calc(100vh-260px)] overflow-y-auto border-b lg:border-b-0 lg:border-r">
            {fila.map((g) => {
              const idade = idadeDoGrupo(g);
              const ativo = keyDoGrupo(g) === keyDoGrupo(selecionado);
              return (
                <button
                  key={keyDoGrupo(g)}
                  type="button"
                  onClick={() => setSelKey(keyDoGrupo(g))}
                  className={cn(
                    "block w-full border-b px-4 py-3 text-left transition-colors last:border-b-0",
                    ativo ? "bg-primary/5 shadow-[inset_3px_0_0_hsl(var(--primary))]" : "hover:bg-muted/50",
                  )}
                >
                  <div className="flex items-center gap-2">
                    <span className={cn("inline-flex shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold", BANDS[bandIndex(idade)].chip)}>
                      há {idade}d
                    </span>
                    <span className="truncate text-sm font-bold">
                      {classificacaoDoGrupo(g)?.subcategoria || "Aguardando classificação"}
                    </span>
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-[11px] text-muted-foreground">
                    <span className="truncate font-mono">{g.lawsuit_cnj || "sem processo"}</span>
                    <span>· {STATUS_LABEL[statusDoGrupo(g)].toLowerCase()}</span>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1">
                    <EtiquetasL1 etiquetas={g.l1_etiquetas} />
                    {g.responsavel_pasta && (
                      <ResponsavelPasta responsavel={g.responsavel_pasta} />
                    )}
                  </div>
                </button>
              );
            })}
          </div>
          <div className="max-h-[calc(100vh-260px)] overflow-y-auto bg-background/40 p-4">
            <GroupDetailCard
              key={keyDoGrupo(selecionado)}
              group={comTextoIntegral(selecionado) as GroupedRecord}
              drafts={draftsAtuais}
              onDraftsChange={(d) => setDraftsDe(selecionado, d)}
              taskTypes={taskTypes}
              users={users}
              duplicatasPorSubtipo={duplicatas}
              consultouAutos={consultouPorKey[keyDoGrupo(selecionado)] === true}
              onConsultouAutos={(v) =>
                setConsultouPorKey((prev) => ({ ...prev, [keyDoGrupo(selecionado)]: v }))
              }
              onConfirm={() => pedirAgendamento(selecionado)}
              onIgnore={(m, n) => void ignorar(selecionado, m, n)}
              onFeedback={setFeedbackDe}
              submitting={submitting}
              kbdHints
            />
          </div>
        </div>
      )}

      {/* Feedback de classificação errada. A árvore que ele oferece é a mesma
          que o filtro usa — ou seja, os 16 tipos do motor próprio quando se
          está na fila SEM PASTA, e a taxonomia v2 do escritório na fila
          comum. Recarrega a fila ao gravar, porque o endpoint também APLICA
          a correção ao registro. */}
      {/* Duplicata: mostra o que já existe e devolve a decisão ao operador,
          em vez do beco que mandava "reenvie com force_duplicate=true". */}
      <DuplicataDialog
        conflito={
          conflito
            ? {
                lawsuitId: conflito.grupo.lawsuit_id ?? null,
                cnj: conflito.grupo.lawsuit_cnj,
                quantas: conflito.quantas,
                subtipos: conflito.subtipos,
              }
            : null
        }
        enviando={submitting}
        onCancelar={() => setConflito(null)}
        onAgendarMesmoAssim={() => {
          const g = conflito?.grupo;
          setConflito(null);
          if (g) void enviarAgendamento(g, true);
        }}
      />

      <FeedbackClassificacao
        record={feedbackDe}
        taxonomy={taxonomy}
        onOpenChange={(v) => { if (!v) setFeedbackDe(null); }}
        onRegistrado={() => { void carregarFila(); }}
      />

      <ConfirmarAgendamentoDialog
        open={confirmarPara !== null}
        onOpenChange={(v) => { if (!v) setConfirmarPara(null); }}
        tarefas={confirmarPara ? draftsDe(confirmarPara).filter(draftValido) : []}
        cnj={confirmarPara?.lawsuit_cnj ?? null}
        nomePorExternalId={nomePorExternalId}
        submitting={submitting}
        onConfirm={(naoMostrar) => {
          if (naoMostrar) dispensarConfirmacaoHoje();
          if (confirmarPara) void enviarAgendamento(confirmarPara);
        }}
      />
    </div>
  );
}

/* ─── filtro de classificação (categoria › subcategoria) + CNJ + faixas ─── */
function FiltroClassificacao({
  taxonomy, category, subcategory, onCategory, onSubcategory,
  cnj, onCnj, onRefresh, loading, banda, onBanda,
  etiquetas, etiqueta, onEtiqueta,
  responsaveis, responsavel, onResponsavel,
  ufs, uf, onUf, rito, onRito,
}: {
  taxonomy: Record<string, string[]>;
  category: string;
  subcategory: string;
  onCategory: (v: string) => void;
  onSubcategory: (v: string) => void;
  cnj: string;
  onCnj: (v: string) => void;
  onRefresh: () => void;
  loading?: boolean;
  banda?: number | null;
  onBanda?: (b: number | null) => void;
  etiquetas?: string[];
  etiqueta?: string;
  onEtiqueta?: (v: string) => void;
  responsaveis?: { id: number; nome: string; total: number }[];
  responsavel?: number | null;
  onResponsavel?: (v: number | null) => void;
  ufs?: string[];
  uf?: string;
  onUf?: (v: string) => void;
  rito?: string;
  onRito?: (v: string) => void;
}) {
  const categorias = useMemo(() => Object.keys(taxonomy).sort(), [taxonomy]);
  const subcategorias = useMemo(
    () => (category && taxonomy[category] ? [...taxonomy[category]].sort() : []),
    [taxonomy, category],
  );
  const temFiltro = Boolean(
    category || subcategory || cnj || banda != null || etiqueta || responsavel || uf,
  );
  const [buscaResp, setBuscaResp] = useState(false);
  const respEscolhido = responsaveis?.find((r) => r.id === responsavel) || null;

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-2xl border bg-card/70 p-3 shadow-sm backdrop-blur">
      <span className="inline-flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide text-muted-foreground">
        <Filter className="h-3.5 w-3.5" /> Classificação
      </span>

      <Select value={category || "__todas__"} onValueChange={(v) => onCategory(v === "__todas__" ? "" : v)}>
        <SelectTrigger className="h-9 w-[230px] text-sm">
          <SelectValue placeholder="Todas as categorias" />
        </SelectTrigger>
        <SelectContent className="max-h-[320px]">
          <SelectItem value="__todas__">Todas as categorias</SelectItem>
          {categorias.map((c) => (
            <SelectItem key={c} value={c}>{c}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={subcategory || "__todas__"}
        onValueChange={(v) => onSubcategory(v === "__todas__" ? "" : v)}
        disabled={!category}
      >
        <SelectTrigger className="h-9 w-[230px] text-sm">
          <SelectValue placeholder={category ? "Todas as subcategorias" : "escolha a categoria"} />
        </SelectTrigger>
        <SelectContent className="max-h-[320px]">
          <SelectItem value="__todas__">Todas as subcategorias</SelectItem>
          {subcategorias.map((s) => (
            <SelectItem key={s} value={s}>{s}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Input
        value={cnj}
        onChange={(e) => onCnj(e.target.value)}
        placeholder="Buscar CNJ (só dígitos serve)"
        className="h-9 w-[220px] text-sm"
      />

      {/* Etiqueta do processo: enseja direcionamento específico, então precisa
          ser filtrável — e não só visível. Filtrar por etiqueta esconde a
          publicação sem pasta e a que o enriquecimento ainda não visitou, por
          isso o filtro só entra quando pedido. */}
      {onEtiqueta && etiquetas && etiquetas.length > 0 && (
        <Select
          value={etiqueta || "__todas__"}
          onValueChange={(v) => onEtiqueta(v === "__todas__" ? "" : v)}
        >
          <SelectTrigger className="h-9 w-[190px] text-sm">
            <SelectValue placeholder="Todas as etiquetas" />
          </SelectTrigger>
          <SelectContent className="max-h-[320px]">
            <SelectItem value="__todas__">Todas as etiquetas</SelectItem>
            {etiquetas.map((e) => (
              <SelectItem key={e} value={e}>{e}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}

      {/* Responsável da pasta. Combobox com busca, não Select cru: são ~31
          nomes na fila de produção, e nome é o pior conteúdo possível pra
          varrer numa lista rolável. Mostra o volume ao lado pra o supervisor
          ver de quem é a carteira que está parando. */}
      {onResponsavel && responsaveis && responsaveis.length > 0 && (
        <Popover open={buscaResp} onOpenChange={setBuscaResp}>
          <PopoverTrigger asChild>
            <Button
              variant="outline"
              role="combobox"
              className={cn(
                "h-9 w-[230px] justify-between text-sm font-normal",
                !respEscolhido && "text-muted-foreground",
              )}
            >
              <span className="truncate">
                {respEscolhido ? respEscolhido.nome : "Responsável da pasta"}
              </span>
              <ChevronsUpDown className="ml-1 h-3.5 w-3.5 shrink-0 opacity-50" />
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-[300px] p-0" align="start">
            <Command>
              <CommandInput placeholder="Buscar responsável..." className="h-9" />
              <CommandList>
                <CommandEmpty>Ninguém com esse nome na fila.</CommandEmpty>
                <CommandGroup>
                  <CommandItem
                    value="todos os responsáveis"
                    onSelect={() => { onResponsavel(null); setBuscaResp(false); }}
                  >
                    Todos os responsáveis
                  </CommandItem>
                  {responsaveis.map((r) => (
                    <CommandItem
                      key={r.id}
                      value={r.nome}
                      onSelect={() => {
                        onResponsavel(responsavel === r.id ? null : r.id);
                        setBuscaResp(false);
                      }}
                    >
                      <span className="truncate">{r.nome}</span>
                      <span className="ml-auto pl-2 text-[11px] text-muted-foreground">
                        {r.total}
                      </span>
                    </CommandItem>
                  ))}
                </CommandGroup>
              </CommandList>
            </Command>
          </PopoverContent>
        </Popover>
      )}

      {/* Estado (UF/região derivada do CNJ). Select cru e não combobox de
          propósito: são siglas curtas, ~27 itens, que se varrem de relance —
          o padrão de busca da casa vale pra catálogo de NOMES. */}
      {onUf && ufs && ufs.length > 0 && (
        <Select value={uf || "__todas__"} onValueChange={(v) => onUf(v === "__todas__" ? "" : v)}>
          <SelectTrigger className="h-9 w-[150px] text-sm">
            <SelectValue placeholder="Todos os estados" />
          </SelectTrigger>
          <SelectContent className="max-h-[320px]">
            <SelectItem value="__todas__">Todos os estados</SelectItem>
            {ufs.map((u) => (
              <SelectItem key={u} value={u}>{u}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}

      {/* Rito (pub016). Vocabulário FIXO, não derivado da fila: as quatro
          opções existem mesmo quando o recorte atual não tem nenhuma delas,
          e sumir com a opção esconderia justamente a resposta "não tem
          nenhum juizado aqui", que é informação. "Não identificado" é opção
          de primeira classe: é a fila que o texto não resolveu e o DataJud
          ainda não completou — onde o operador precisa olhar. */}
      {onRito && (
        <Select value={rito || "__todos__"} onValueChange={(v) => onRito(v === "__todos__" ? "" : v)}>
          <SelectTrigger className="h-9 w-[185px] text-sm">
            <SelectValue placeholder="Qualquer rito" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__todos__">Qualquer rito</SelectItem>
            <SelectItem value="comum">Justiça Comum</SelectItem>
            <SelectItem value="juizado">Juizado Especial</SelectItem>
            <SelectItem value="trabalhista">Justiça do Trabalho</SelectItem>
            <SelectItem value="nao_identificado">Rito não identificado</SelectItem>
          </SelectContent>
        </Select>
      )}

      {onBanda && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">Na fila há</span>
          {BANDS.map((b, i) => (
            <button
              key={b.key}
              type="button"
              onClick={() => onBanda(banda === i ? null : i)}
              className={cn(
                "rounded-full border px-2.5 py-1 text-xs font-semibold transition-transform hover:-translate-y-px",
                b.chip,
                banda === i && "ring-2 ring-foreground ring-offset-1",
              )}
            >
              {b.short}
            </button>
          ))}
        </div>
      )}

      <div className="ml-auto flex items-center gap-2">
        {temFiltro && (
          <Button
            variant="ghost" size="sm"
            onClick={() => {
              onCategory(""); onSubcategory(""); onCnj("");
              onBanda?.(null); onEtiqueta?.(""); onResponsavel?.(null); onUf?.("");
            }}
          >
            <X className="mr-1 h-3.5 w-3.5" /> limpar
          </Button>
        )}
        <Button variant="outline" size="sm" onClick={onRefresh} disabled={loading}>
          <RefreshCw className={cn("mr-1.5 h-3.5 w-3.5", loading && "animate-spin")} /> Atualizar
        </Button>
      </div>
    </div>
  );
}

/* ─── layout Cards ─── */
function CardsLayout({
  fila, visiveis, onMais, onAbrir,
}: {
  fila: GroupedRecord[];
  visiveis: number;
  onMais: () => void;
  onAbrir: (g: GroupedRecord) => void;
}) {
  const mostrados = fila.slice(0, visiveis);
  let ultimaBanda = -1;

  return (
    <div className="space-y-3">
      {mostrados.map((g) => {
        const idade = idadeDoGrupo(g);
        const bi = bandIndex(idade);
        const cabecalho = bi !== ultimaBanda;
        if (cabecalho) ultimaBanda = bi;
        const status = statusDoGrupo(g);
        const prazo = prazoInfo(prazoDoGrupo(g));
        const rec = g.records[0];
        const cls = classificacaoDoGrupo(g);
        const propostas = g.proposed_tasks?.length
          ? g.proposed_tasks
          : g.proposed_task
            ? [g.proposed_task]
            : [];

        return (
          <div key={keyDoGrupo(g)}>
            {cabecalho && (
              <div className="sticky top-0 z-10 -mx-1 mb-2 mt-4 bg-gradient-to-b from-background via-background/95 to-transparent px-1 pb-2 pt-1 text-sm font-extrabold first:mt-0">
                <span className="inline-block h-2.5 w-2.5 rounded-full align-middle" style={{ background: BANDS[bi].hex }} />
                <span className="ml-2 align-middle">{BANDS[bi].label}</span>
                <span className="ml-2 align-middle text-xs font-semibold text-muted-foreground">
                  · {fila.filter((x) => bandIndex(idadeDoGrupo(x)) === bi).length} processo(s)
                </span>
              </div>
            )}
            <div className="grid grid-cols-[5px_minmax(0,1fr)] overflow-hidden rounded-2xl border bg-card/70 shadow-sm backdrop-blur transition-shadow hover:shadow-md">
              <span style={{ background: BANDS[bi].hex }} />
              <div className="space-y-2.5 px-4 py-3.5">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm font-bold">{g.lawsuit_cnj || "sem processo"}</span>
                  <EtiquetasL1 etiquetas={g.l1_etiquetas} />
                  <ResponsavelPasta
                    responsavel={g.responsavel_pasta}
                    temPasta={Boolean(g.lawsuit_id)}
                  />
                  <span className={cn("rounded-full border px-2 py-0.5 text-[11px] font-semibold", BANDS[bi].chip)}>
                    {labelIdade(idade)}
                  </span>
                  {prazo && (
                    <span className={cn(
                      "rounded-full border px-2 py-0.5 text-[11px] font-semibold",
                      prazo.estado === "vencida" && "border-red-200 bg-red-50 text-red-700",
                      prazo.estado === "vence_hoje" && "border-orange-200 bg-orange-50 text-orange-700",
                      prazo.estado === "no_prazo" && "border-border bg-muted/60 text-muted-foreground",
                    )}>
                      {prazo.label}
                    </span>
                  )}
                  <Badge
                    className={cn(
                      "text-[10px] font-extrabold",
                      status === "classificado" && "bg-primary text-primary-foreground",
                      status === "novo" && "bg-amber-400 text-amber-950 hover:bg-amber-400",
                      status === "sem_template" && "bg-rose-100 text-rose-700 hover:bg-rose-100",
                      status === "erro" && "bg-destructive text-destructive-foreground",
                    )}
                  >
                    {STATUS_LABEL[status]}
                  </Badge>
                  <span className="ml-auto whitespace-nowrap text-[11px] text-muted-foreground">
                    Pub. {fmtData(rec?.publication_date)} · Capt. {fmtData(rec?.created_at || rec?.creation_date)}
                  </span>
                </div>

                <p className="line-clamp-2 text-sm leading-relaxed text-muted-foreground">
                  {rec?.description_preview || rec?.description || "—"}
                </p>

                {propostas.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {propostas.map((p, i) => {
                      const nome = p.suggested_responsible?.name || null;
                      const venc = prazoInfo(isoToBrtDate(p.endDateTime) || null);
                      return (
                        <span
                          key={`${p.subTypeId}-${i}`}
                          className="inline-flex items-center gap-1.5 rounded-full border bg-background/70 py-0.5 pl-1 pr-2.5 text-[11px]"
                          title={p.template_name || "tarefa proposta"}
                        >
                          {nome && (
                            <span
                              className="inline-flex h-4 w-4 items-center justify-center rounded-full text-[7px] font-bold text-white"
                              style={{ background: corDoNome(nome) }}
                            >
                              {iniciais(nome)}
                            </span>
                          )}
                          <b className="max-w-[230px] truncate font-semibold">{p.description}</b>
                          {venc && (
                            <span className={cn("font-semibold", venc.estado === "vencida" && "text-red-600")}>
                              · {venc.label}
                            </span>
                          )}
                        </span>
                      );
                    })}
                  </div>
                )}

                <div className="flex flex-wrap items-center gap-2">
                  {cls ? (
                    <span className="rounded-full border border-primary/20 bg-primary/5 px-2.5 py-0.5 text-[11px] font-semibold text-primary">
                      {cls.categoria} › {cls.subcategoria}
                    </span>
                  ) : (
                    <span className="rounded-full border border-amber-200 bg-amber-50 px-2.5 py-0.5 text-[11px] font-semibold text-amber-700">
                      aguardando classificação
                    </span>
                  )}
                  {g.records.length > 1 && (
                    <span className="text-[11px] text-muted-foreground">{g.records.length} publicações</span>
                  )}
                  <Button size="sm" className="ml-auto" onClick={() => onAbrir(g)}>
                    Revisar e agendar
                  </Button>
                </div>
              </div>
            </div>
          </div>
        );
      })}

      {fila.length > mostrados.length ? (
        <div className="flex justify-center pt-2">
          <Button variant="outline" onClick={onMais}>
            Mostrar mais {Math.min(PAGE_SIZE, fila.length - mostrados.length)} (exibindo {mostrados.length} de {fila.length})
          </Button>
        </div>
      ) : (
        <p className="pt-2 text-center text-xs text-muted-foreground">
          Fim da fila — {fila.length} processo(s).
        </p>
      )}
    </div>
  );
}
