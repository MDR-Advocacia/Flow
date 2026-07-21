// Agendamento de tarefa em lote sobre os duplicados Ativos selecionados.
//
// Reusa o motor de criação de tarefa das Publicações (SubtypePicker + create_task
// no backend) e a divisão igual por contagem do Balanceador. Fluxo:
//   1. operador escolhe subtipo, prazo (default hoje+1), prioridade, escritório,
//      descrição/obs e um ou mais responsáveis (dividir igual OU tudo pra um);
//   2. PREVIEW (dry-run leve) mostra quantas tarefas e pra quem — sem tocar no L1;
//   3. ao confirmar, dispara o worker (server-backed) e a barra acompanha a
//      criação real via polling. Anti-dup: pula pasta que já tem tarefa aberta
//      do mesmo subtipo.

import { useCallback, useEffect, useMemo, useState } from "react";
import { CalendarPlus, CheckCircle2, Loader2, Users2 } from "lucide-react";

import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { SubtypePicker, type SubtypePickerTaskType } from "@/components/ui/SubtypePicker";
import { Progress } from "@/components/ui/progress";
import {
  type AgendJobStatus, type AgendPreview, type AgendUser, type DuplicadoAtivos,
  type OfficeL1,
  dispararAgendamento, getTaskTypesMeta, getUsersMeta, listarOfficesL1,
  previewAgendamento, statusAgendamento,
} from "@/services/distribuidos-bb";
import { useToast } from "@/hooks/use-toast";

function amanhaISO(): string {
  // Default do prazo: hoje + 1 dia, 18:00 (local). Formato YYYY-MM-DD pro <input date>.
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10);
}

export default function AgendarTarefaDuplicadosDialog({
  open, onOpenChange, duplicados, onDone,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  duplicados: DuplicadoAtivos[];
  onDone?: () => void;
}) {
  const { toast } = useToast();
  const dupIds = useMemo(() => duplicados.map((d) => d.id), [duplicados]);

  // catálogos
  const [taskTypes, setTaskTypes] = useState<SubtypePickerTaskType[]>([]);
  const [users, setUsers] = useState<AgendUser[]>([]);
  const [offices, setOffices] = useState<OfficeL1[]>([]);

  // form
  const [subtypeId, setSubtypeId] = useState<number | null>(null);
  const [typeId, setTypeId] = useState<number | null>(null);
  const [subtipoNome, setSubtipoNome] = useState<string>("");
  const [data, setData] = useState<string>(amanhaISO());
  const [hora, setHora] = useState<string>("18:00");
  const [prioridade, setPrioridade] = useState<string>("Normal");
  const [officeId, setOfficeId] = useState<string>("");
  const [descricao, setDescricao] = useState<string>("");
  const [obs, setObs] = useState<string>("");
  const [respSel, setRespSel] = useState<number[]>([]);
  const [dividirIgual, setDividirIgual] = useState(true);
  const [respOpen, setRespOpen] = useState(false);

  // preview + job
  const [preview, setPreview] = useState<AgendPreview | null>(null);
  const [job, setJob] = useState<AgendJobStatus | null>(null);
  const [disparando, setDisparando] = useState(false);

  useEffect(() => {
    if (!open) return;
    // reset ao reabrir
    setJob(null); setPreview(null); setDisparando(false);
    Promise.all([getTaskTypesMeta(), getUsersMeta(), listarOfficesL1()])
      .then(([tt, us, of]) => { setTaskTypes(tt); setUsers(us); setOffices(of); })
      .catch((e) => toast({ title: "Erro ao carregar catálogos", description: String((e as Error).message), variant: "destructive" }));
  }, [open, toast]);

  // Recalcula o preview quando muda a seleção de responsáveis ou o modo.
  const recalcPreview = useCallback(async () => {
    if (!respSel.length || !dupIds.length) { setPreview(null); return; }
    try {
      setPreview(await previewAgendamento({
        duplicado_ids: dupIds, responsavel_ids: respSel, dividir_igual: dividirIgual,
      }));
    } catch { /* silencioso — preview é auxiliar */ }
  }, [respSel, dividirIgual, dupIds]);
  useEffect(() => { if (open) recalcPreview(); }, [open, recalcPreview]);

  const toggleResp = (id: number) => {
    setRespSel((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]);
    if (!dividirIgual) setDividirIgual(true); // ao marcar 2º, faz sentido dividir
  };

  const podeDisparar = subtypeId && typeId && data && respSel.length > 0 && officeId;

  const dataIso = () => `${data}T${hora || "18:00"}:00`;

  const baseConfig = () => ({
    duplicado_ids: dupIds,
    responsavel_ids: respSel,
    dividir_igual: respSel.length > 1 ? dividirIgual : false,
    subtype_id: subtypeId!, type_id: typeId!, subtipo_nome: subtipoNome,
    data_iso: dataIso(), publish_date_iso: dataIso(),
    office_external_id: officeId ? Number(officeId) : undefined,
    prioridade, descricao: descricao || undefined, observacoes: obs || undefined,
  });

  const acompanhar = (jobId: number) => {
    const tick = async () => {
      try {
        const st = await statusAgendamento(jobId);
        setJob(st);
        if (st.status === "EM_ANDAMENTO") setTimeout(tick, 1500);
        else if (st.status === "CONCLUIDO") {
          toast({
            title: st.dry_run ? "Simulação concluída" : "Agendamento concluído",
            description: st.dry_run
              ? `${st.total} tarefa(s) seriam criadas.`
              : `${st.criados} criada(s) · ${st.pulados} pulada(s) · ${st.falhas} falha(s).`,
          });
          if (!st.dry_run) onDone?.();
        }
      } catch { /* para o polling em erro de rede */ }
    };
    tick();
  };

  const disparar = async (dryRun: boolean) => {
    if (!podeDisparar) return;
    setDisparando(true); setJob(null);
    try {
      const r = await dispararAgendamento({ ...baseConfig(), dry_run: dryRun });
      acompanhar(r.job_id);
    } catch (e) {
      toast({ title: "Erro ao disparar", description: String((e as Error).message), variant: "destructive" });
    } finally {
      setDisparando(false);
    }
  };

  const rodando = job?.status === "EM_ANDAMENTO";
  const pct = job && job.total ? Math.round((job.processados / job.total) * 100) : 0;

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!rodando) onOpenChange(o); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Agendar tarefa em lote</DialogTitle>
          <DialogDescription>
            {duplicados.length} pasta(s) selecionada(s). Cria uma tarefa em cada, dividindo os
            responsáveis igual ou pra uma pessoa. O prazo padrão é amanhã.
          </DialogDescription>
        </DialogHeader>

        {/* Resultado do job (progresso) tem prioridade visual */}
        {job ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-sm">
              {rodando ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4 text-emerald-600" />}
              <span>
                {job.dry_run ? "Simulação" : "Criação"} — {job.processados}/{job.total}
                {job.dry_run ? " (nada é escrito no L1)" : ""}
              </span>
            </div>
            <Progress value={pct} />
            {!job.dry_run && (
              <div className="flex gap-4 text-xs">
                <span className="text-emerald-700">{job.criados} criada(s)</span>
                <span className="text-amber-700">{job.pulados} pulada(s) — já tinham tarefa</span>
                {job.falhas > 0 && <span className="text-rose-600">{job.falhas} falha(s)</span>}
              </div>
            )}
            {job.falhas > 0 && (
              <div className="max-h-32 overflow-y-auto rounded border p-2 text-xs">
                {job.itens.filter((i) => i.status === "falha").slice(0, 20).map((i, k) => (
                  <div key={k} className="text-rose-600">{i.cnj}: {i.erro}</div>
                ))}
              </div>
            )}
            {job.status === "CONCLUIDO" && job.dry_run && (
              <p className="text-xs text-muted-foreground">
                Revise o plano acima. Se estiver certo, clique em <span className="font-medium">Criar de verdade</span>.
              </p>
            )}
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            <div className="md:col-span-2">
              <SubtypePicker
                value={subtypeId}
                taskTypes={taskTypes}
                required
                onChange={(sid, parent) => {
                  setSubtypeId(sid);
                  setTypeId(parent?.external_id ?? null);
                  const s = parent?.subtypes.find((x) => x.external_id === sid);
                  setSubtipoNome(s?.name ?? "");
                }}
              />
            </div>
            <div className="grid gap-1.5">
              <Label className="text-xs font-medium">Prazo (data) *</Label>
              <Input type="date" value={data} onChange={(e) => setData(e.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label className="text-xs font-medium">Horário</Label>
              <Input type="time" value={hora} onChange={(e) => setHora(e.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label className="text-xs font-medium">Escritório responsável *</Label>
              <Select value={officeId} onValueChange={setOfficeId}>
                <SelectTrigger><SelectValue placeholder="Selecione" /></SelectTrigger>
                <SelectContent>
                  {offices.map((o) => (
                    <SelectItem key={o.external_id} value={String(o.external_id)}>{o.path || o.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label className="text-xs font-medium">Prioridade</Label>
              <Select value={prioridade} onValueChange={setPrioridade}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="Low">Baixa</SelectItem>
                  <SelectItem value="Normal">Normal</SelectItem>
                  <SelectItem value="High">Alta</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Responsáveis */}
            <div className="md:col-span-2 grid gap-1.5">
              <Label className="text-xs font-medium">Responsáveis *</Label>
              <Popover open={respOpen} onOpenChange={setRespOpen}>
                <PopoverTrigger asChild>
                  <Button variant="outline" role="combobox" className="justify-start font-normal">
                    <Users2 className="mr-2 h-4 w-4" />
                    {respSel.length === 0 ? "Selecione um ou mais" : `${respSel.length} selecionado(s)`}
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-[380px] p-0" align="start">
                  <Command>
                    <CommandInput placeholder="Buscar por nome ou email…" />
                    <CommandList>
                      <CommandEmpty>Ninguém encontrado.</CommandEmpty>
                      <CommandGroup>
                        {users.map((u) => (
                          <CommandItem key={u.id} value={`${u.name} ${u.email ?? ""}`} onSelect={() => toggleResp(u.id)}>
                            <Checkbox checked={respSel.includes(u.id)} className="mr-2" />
                            <span className="truncate">{u.name}</span>
                            {u.email && <span className="ml-2 truncate text-xs text-muted-foreground">{u.email}</span>}
                          </CommandItem>
                        ))}
                      </CommandGroup>
                    </CommandList>
                  </Command>
                </PopoverContent>
              </Popover>
              {respSel.length > 1 && (
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Checkbox checked={dividirIgual} onCheckedChange={(v) => setDividirIgual(!!v)} />
                  Dividir igual entre os {respSel.length} (round-robin). Desmarcado = tudo pro 1º.
                </label>
              )}
            </div>

            <div className="md:col-span-2 grid gap-1.5">
              <Label className="text-xs font-medium">Descrição</Label>
              <Input value={descricao} maxLength={250} placeholder="(opcional — usa o nome do subtipo se vazio)"
                onChange={(e) => setDescricao(e.target.value)} />
            </div>
            <div className="md:col-span-2 grid gap-1.5">
              <Label className="text-xs font-medium">Observações</Label>
              <Textarea value={obs} rows={2} onChange={(e) => setObs(e.target.value)} />
            </div>

            {/* Preview da divisão */}
            {preview && preview.por_responsavel.length > 0 && (
              <div className="md:col-span-2 rounded-md border bg-muted/30 p-3 text-xs">
                <div className="mb-1 font-medium">
                  {preview.total_pastas} tarefa(s) serão criadas
                  {preview.sem_pasta > 0 && ` · ${preview.sem_pasta} sem pasta L1 (ignorada(s))`}
                </div>
                {preview.por_responsavel.map((r) => (
                  <div key={r.responsavel_id} className="flex justify-between">
                    <span>{r.responsavel_nome}</span><span className="font-semibold">{r.total}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          {job?.status === "CONCLUIDO" && !job.dry_run ? (
            <Button onClick={() => onOpenChange(false)}>Fechar</Button>
          ) : (
            <>
              <Button variant="outline" disabled={rodando} onClick={() => onOpenChange(false)}>Cancelar</Button>
              <Button variant="outline" disabled={!podeDisparar || disparando || rodando} onClick={() => disparar(true)}>
                {disparando ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : null}
                Simular (dry-run)
              </Button>
              <Button disabled={!podeDisparar || disparando || rodando} onClick={() => disparar(false)}>
                <CalendarPlus className="mr-1.5 h-4 w-4" /> Criar de verdade
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
