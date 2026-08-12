// Painel Administrativo → Utilização.
//
// Existe por causa de um problema concreto: adesão baixa, sem como saber quem
// estava de fato usando o sistema. O relatório separa duas coisas que costumam
// ser confundidas:
//
//  - ACESSO/NAVEGAÇÃO: com que frequência a pessoa entra e onde trabalha.
//    Começou a ser medido no deploy desta tela — não há passado para mostrar.
//  - AÇÃO EFETIVA: o que ela de fato executou (agendou, redistribuiu, subiu
//    planilha). Vem dos rastros que os módulos já gravavam, então é retroativo.
//
// A distinção importa porque supervisor trabalha LENDO. Cobrar adesão só por
// ação puniria justamente quem entra todo dia para conferir a carga da equipe.

import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, Download, Loader2, RefreshCw, Users } from "lucide-react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend,
  ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis,
} from "recharts";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import { apiFetch } from "@/lib/api-client";

interface ItemUso {
  user_id: number;
  nome: string;
  email: string;
  cargo: string;
  supervisor: boolean;
  equipes: string[];
  ultimo_acesso: string | null;
  dias_ativos: number;
  requisicoes: number;
  modulos: string[];
  acoes: number;
  acoes_por_tipo: Record<string, number>;
  situacao: string;
}

interface PontoSerie {
  dia: string;
  rotulo: string;
  acoes: number;
  pessoas: number;
  supervisores: number;
}

interface Relatorio {
  periodo_dias: number;
  desde: string;
  navegacao_disponivel: boolean;
  serie: PontoSerie[];
  ranking_tipos: { tipo: string; acoes: number }[];
  ranking_pessoas: {
    nome: string; primeiro_nome: string; acoes: number; supervisor: boolean;
  }[];
  resumo: {
    supervisores: number;
    supervisores_ativos: number;
    supervisores_pouco_ativos: number;
    supervisores_dormentes: number;
    supervisores_nunca_entraram: number;
    usuarios_avaliados: number;
  };
  total: number;
  items: ItemUso[];
}

const PERIODOS = [7, 30, 60, 90];

// Mesma família de cores dos selos de situação, pra leitura casar entre o
// gráfico e a tabela logo abaixo.
const COR_PESSOAS = "#0ea5e9";
const COR_SUPERVISORES = "#8b5cf6";
const COR_BARRA_SUP = "#8b5cf6";
const COR_BARRA_OUTROS = "#94a3b8";
const TAMANHOS = [25, 50, 100];

const CORES_SITUACAO: Record<string, string> = {
  "ativo": "bg-emerald-100 text-emerald-800 border-emerald-200",
  "pouco ativo": "bg-amber-100 text-amber-800 border-amber-200",
  "dormente": "bg-red-100 text-red-800 border-red-200",
  "nunca entrou": "bg-slate-200 text-slate-700 border-slate-300",
  "sem acesso liberado": "bg-slate-100 text-slate-500 border-slate-200",
};

function formatarData(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("pt-BR", {
    day: "2-digit", month: "2-digit", year: "numeric",
  }) + " " + d.toLocaleTimeString("pt-BR", {
    hour: "2-digit", minute: "2-digit",
  });
}

function diasAtras(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const dias = Math.floor((Date.now() - d.getTime()) / 86400000);
  if (dias <= 0) return "hoje";
  if (dias === 1) return "ontem";
  return `há ${dias} dias`;
}

export default function UtilizacaoManager() {
  const { toast } = useToast();
  const [dados, setDados] = useState<Relatorio | null>(null);
  const [carregando, setCarregando] = useState(false);
  const [dias, setDias] = useState(30);
  const [soSupervisores, setSoSupervisores] = useState(true);
  const [pagina, setPagina] = useState(1);
  const [porPagina, setPorPagina] = useState(50);
  const [expandido, setExpandido] = useState<number | null>(null);

  const carregar = useCallback(async () => {
    setCarregando(true);
    try {
      const offset = (pagina - 1) * porPagina;
      const r = await apiFetch(
        `/api/v1/admin/uso?dias=${dias}&apenas_supervisores=${soSupervisores}` +
        `&limit=${porPagina}&offset=${offset}`,
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setDados(await r.json());
    } catch (e) {
      toast({
        title: "Não foi possível carregar o relatório",
        description: e instanceof Error ? e.message : String(e),
        variant: "destructive",
      });
    } finally {
      setCarregando(false);
    }
  }, [dias, soSupervisores, pagina, porPagina, toast]);

  useEffect(() => { void carregar(); }, [carregar]);
  useEffect(() => { setPagina(1); }, [dias, soSupervisores, porPagina]);

  const totalPaginas = useMemo(
    () => (dados ? Math.max(1, Math.ceil(dados.total / porPagina)) : 1),
    [dados, porPagina],
  );

  const baixarCsv = () => {
    const url = `/api/v1/admin/uso/export?dias=${dias}` +
                `&apenas_supervisores=${soSupervisores}`;
    window.open(url, "_blank");
  };

  const r = dados?.resumo;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5" />
            Utilização do sistema
          </CardTitle>
          <CardDescription>
            Quem está entrando, com que frequência e o que executa de fato.
            Supervisor é quem tem Minha Equipe liberado.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {/* Aviso honesto: sem ele o período de transição parece queda de uso */}
          {dados && !dados.navegacao_disponivel && (
            <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
              A medição de <strong>navegação</strong> (frequência de acesso e
              módulos abertos) começou agora e ainda não acumulou dados. Até lá,
              as colunas <em>Dias com acesso</em> e <em>Módulos</em> ficam
              zeradas — o <strong>último acesso</strong> e as{" "}
              <strong>ações efetivas</strong> já são reais e retroativos.
            </div>
          )}

          <div className="flex flex-wrap items-end gap-4">
            <div className="space-y-1.5">
              <Label className="text-xs text-slate-500">Período</Label>
              <div className="flex gap-1">
                {PERIODOS.map((p) => (
                  <Button
                    key={p}
                    size="sm"
                    variant={dias === p ? "default" : "outline"}
                    onClick={() => setDias(p)}
                  >
                    {p} dias
                  </Button>
                ))}
              </div>
            </div>
            <div className="flex items-center gap-2 pb-1.5">
              <Checkbox
                id="so-sup"
                checked={soSupervisores}
                onCheckedChange={(v) => setSoSupervisores(Boolean(v))}
              />
              <Label htmlFor="so-sup" className="cursor-pointer text-sm">
                Apenas supervisores
              </Label>
            </div>
            <div className="ml-auto flex gap-2 pb-1.5">
              <Button size="sm" variant="outline" onClick={() => void carregar()}
                      disabled={carregando}>
                {carregando
                  ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                  : <RefreshCw className="mr-1.5 h-4 w-4" />}
                Atualizar
              </Button>
              <Button size="sm" variant="outline" onClick={baixarCsv}>
                <Download className="mr-1.5 h-4 w-4" />
                CSV
              </Button>
            </div>
          </div>

          {r && (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
              <Kpi rotulo="Supervisores" valor={r.supervisores} icone />
              <Kpi rotulo="Ativos" valor={r.supervisores_ativos}
                   cor="text-emerald-600" />
              <Kpi rotulo="Pouco ativos" valor={r.supervisores_pouco_ativos}
                   cor="text-amber-600" />
              <Kpi rotulo="Dormentes" valor={r.supervisores_dormentes}
                   cor="text-red-600" />
              <Kpi rotulo="Nunca entraram"
                   valor={r.supervisores_nunca_entraram} cor="text-slate-500" />
            </div>
          )}
        </CardContent>
      </Card>

      {dados && dados.serie?.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card className="lg:col-span-2">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Pessoas usando o sistema, por dia</CardTitle>
              <CardDescription>
                Quantas pessoas distintas executaram alguma ação em cada dia —
                não o volume de ações. Uma pessoa fazendo cinquenta coisas faz
                um pico bonito e não significa que o time adotou.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={240}>
                <AreaChart data={dados.serie} margin={{ left: -18, right: 8, top: 6 }}>
                  <defs>
                    <linearGradient id="gPessoas" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={COR_PESSOAS} stopOpacity={0.35} />
                      <stop offset="100%" stopColor={COR_PESSOAS} stopOpacity={0.02} />
                    </linearGradient>
                    <linearGradient id="gSup" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={COR_SUPERVISORES} stopOpacity={0.35} />
                      <stop offset="100%" stopColor={COR_SUPERVISORES} stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                  <XAxis dataKey="rotulo" fontSize={11} tickLine={false}
                         axisLine={false} interval="preserveStartEnd" minTickGap={18} />
                  <YAxis fontSize={11} tickLine={false} axisLine={false}
                         allowDecimals={false} />
                  <RTooltip
                    formatter={(v: number, nome: string) => [v, nome]}
                    labelFormatter={(l) => `Dia ${l}`}
                    contentStyle={{ fontSize: 12, borderRadius: 8 }}
                  />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Area type="monotone" dataKey="pessoas" name="Pessoas"
                        stroke={COR_PESSOAS} fill="url(#gPessoas)" strokeWidth={2} />
                  <Area type="monotone" dataKey="supervisores" name="Supervisores"
                        stroke={COR_SUPERVISORES} fill="url(#gSup)" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Quem mais executa</CardTitle>
              <CardDescription>
                Ações no período. Roxo é supervisor.
                {!soSupervisores && (
                  <> Os números não são comparáveis entre funções: agendar uma
                  publicação e redistribuir uma agenda contam 1 cada, mas não
                  são o mesmo tamanho de trabalho.</>
                )}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {dados.ranking_pessoas.length === 0 ? (
                <p className="py-10 text-center text-sm text-slate-400">
                  Ninguém executou ações no período.
                </p>
              ) : (
                <ResponsiveContainer
                  width="100%"
                  height={Math.max(200, dados.ranking_pessoas.length * 28)}
                >
                  <BarChart data={dados.ranking_pessoas} layout="vertical"
                            margin={{ left: 8, right: 30, top: 4 }}>
                    <XAxis type="number" hide />
                    <YAxis type="category" dataKey="primeiro_nome" width={78}
                           fontSize={11} tickLine={false} axisLine={false} />
                    <RTooltip
                      formatter={(v: number) => [v, "ações"]}
                      labelFormatter={(_l, p) => p?.[0]?.payload?.nome ?? ""}
                      contentStyle={{ fontSize: 12, borderRadius: 8 }}
                    />
                    <Bar dataKey="acoes" radius={[0, 4, 4, 0]}>
                      {dados.ranking_pessoas.map((d, i) => (
                        <Cell key={i}
                              fill={d.supervisor ? COR_BARRA_SUP : COR_BARRA_OUTROS} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">O que a casa usa</CardTitle>
              <CardDescription>
                Tipos de ação mais executados — costuma diferir do que se imagina.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {dados.ranking_tipos.length === 0 ? (
                <p className="py-10 text-center text-sm text-slate-400">
                  Nenhuma ação registrada no período.
                </p>
              ) : (
                <ResponsiveContainer
                  width="100%"
                  height={Math.max(200, Math.min(10, dados.ranking_tipos.length) * 28)}
                >
                  <BarChart data={dados.ranking_tipos.slice(0, 10)} layout="vertical"
                            margin={{ left: 8, right: 30, top: 4 }}>
                    <XAxis type="number" hide />
                    <YAxis type="category" dataKey="tipo" width={168}
                           fontSize={10.5} tickLine={false} axisLine={false} />
                    <RTooltip
                      formatter={(v: number) => [v, "ações"]}
                      contentStyle={{ fontSize: 12, borderRadius: 8 }}
                    />
                    <Bar dataKey="acoes" fill={COR_PESSOAS} radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      <Card>
        <CardContent className="pt-6">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Pessoa</TableHead>
                  <TableHead>Situação</TableHead>
                  <TableHead>Último acesso</TableHead>
                  <TableHead className="text-right">Dias com acesso</TableHead>
                  <TableHead className="text-right">Ações efetivas</TableHead>
                  <TableHead>Módulos / equipes</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {carregando && !dados && (
                  <TableRow>
                    <TableCell colSpan={6} className="py-10 text-center text-slate-400">
                      <Loader2 className="mx-auto h-5 w-5 animate-spin" />
                    </TableCell>
                  </TableRow>
                )}
                {dados?.items.map((i) => (
                  <>
                    <TableRow
                      key={i.user_id}
                      className={i.acoes > 0 ? "cursor-pointer" : ""}
                      onClick={() =>
                        setExpandido(expandido === i.user_id ? null : i.user_id)}
                    >
                      <TableCell>
                        <div className="flex items-center gap-2">
                          {i.supervisor && (
                            <Users className="h-3.5 w-3.5 shrink-0 text-violet-600" />
                          )}
                          <div className="min-w-0">
                            <div className="truncate font-medium">{i.nome}</div>
                            <div className="truncate text-xs text-slate-400">
                              {i.email} · {i.cargo}
                            </div>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline"
                               className={CORES_SITUACAO[i.situacao] || ""}>
                          {i.situacao}
                        </Badge>
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-sm">
                        {formatarData(i.ultimo_acesso)}
                        <div className="text-xs text-slate-400">
                          {diasAtras(i.ultimo_acesso)}
                        </div>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {i.dias_ativos || "—"}
                      </TableCell>
                      <TableCell className="text-right tabular-nums font-medium">
                        {i.acoes || "—"}
                      </TableCell>
                      <TableCell className="max-w-[22rem]">
                        <div className="flex flex-wrap gap-1">
                          {i.modulos.slice(0, 3).map((m) => (
                            <Badge key={m} variant="secondary" className="text-[10px]">
                              {m}
                            </Badge>
                          ))}
                          {i.equipes.slice(0, 3).map((e) => (
                            <Badge key={e} variant="outline"
                                   className="border-violet-200 text-[10px] text-violet-700">
                              {e}
                            </Badge>
                          ))}
                        </div>
                      </TableCell>
                    </TableRow>
                    {expandido === i.user_id && i.acoes > 0 && (
                      <TableRow key={`${i.user_id}-det`} className="bg-slate-50">
                        <TableCell colSpan={6} className="py-3">
                          <div className="text-xs font-medium text-slate-500">
                            O que {i.nome.split(" ")[0]} executou no período
                          </div>
                          <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1">
                            {Object.entries(i.acoes_por_tipo)
                              .sort((a, b) => b[1] - a[1])
                              .map(([tipo, n]) => (
                                <div key={tipo} className="text-sm">
                                  <span className="font-medium tabular-nums">{n}</span>
                                  <span className="ml-1.5 text-slate-600">{tipo}</span>
                                </div>
                              ))}
                          </div>
                        </TableCell>
                      </TableRow>
                    )}
                  </>
                ))}
                {dados && dados.items.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={6} className="py-10 text-center text-slate-400">
                      Nenhum usuário no filtro atual.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>

          {/* Paginação: regra da casa — catálogo nunca sai sem ela */}
          {dados && dados.total > 0 && (
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm">
              <div className="text-slate-500">
                {(pagina - 1) * porPagina + 1}–
                {Math.min(pagina * porPagina, dados.total)} de {dados.total}
              </div>
              <div className="flex items-center gap-3">
                <select
                  className="h-8 rounded-md border border-slate-200 bg-white px-2 text-sm"
                  value={porPagina}
                  onChange={(e) => setPorPagina(Number(e.target.value))}
                >
                  {TAMANHOS.map((t) => (
                    <option key={t} value={t}>{t} por página</option>
                  ))}
                </select>
                <Button size="sm" variant="outline" disabled={pagina <= 1}
                        onClick={() => setPagina((p) => p - 1)}>
                  Anterior
                </Button>
                <span className="text-slate-500">
                  Página {pagina} de {totalPaginas}
                </span>
                <Button size="sm" variant="outline"
                        disabled={pagina >= totalPaginas}
                        onClick={() => setPagina((p) => p + 1)}>
                  Próxima
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Kpi({ rotulo, valor, cor, icone }: {
  rotulo: string; valor: number; cor?: string; icone?: boolean;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3">
      <div className="flex items-center gap-1.5 text-xs text-slate-500">
        {icone && <Users className="h-3.5 w-3.5" />}
        {rotulo}
      </div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${cor || ""}`}>
        {valor}
      </div>
    </div>
  );
}
