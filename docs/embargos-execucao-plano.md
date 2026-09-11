# Fluxo Embargos à Execução — plano do módulo

Aberto em 11/09/2026. Status: **fases 1 e 2 implementadas na worktree
`fluxo-embargos-execucao`, não commitadas** (ver §9). Fases 3 e 4 pendentes.

## 1. O fluxo pedido pelo operador

1. A tarefa **"Ativos e BB - Recuperação de Crédito / Protocolar Inicial - BB Autor"**
   é concluída no L1 (= a execução foi ajuizada).
2. O Flow roda um **relatório próprio** (enxuto, mais rápido que o Agenda Analytics
   do Minha Equipe — HAR a ser enviado pelo operador) e as novas conclusões
   entram sozinhas no **board da Controladoria** (Minha Equipe).
3. No mesmo dia, o Flow consulta o **portal do BB pelo NPJ** e traz o documento
   (CPF/CNPJ) e o nome das partes cadastradas como **demandadas**.
4. O painel registra a **data do ajuizamento** (= conclusão da tarefa). Passados
   **15, 20 ou 25 dias úteis** (ajustável), começa a consulta ao **tribunal**,
   repetida a cada **5 dias úteis**, até achar um processo de **embargos à
   execução** ajuizado depois da execução e ligado a ela.
5. Achou → para de monitorar, **avisa a Controladoria e o operador registrado**
   e destaca no board para verificação.
6. Verificado o vínculo, o controlador **cadastra o incidente no Legal One e no
   portal do BB** e **dispara as tarefas** — tudo pelo Flow. Fluxo fechado: as
   intimações seguintes chegam direto ao operador pela pasta.

## 2. O que foi medido antes de desenhar (produção, 11/09/2026, só leitura)

### 2.1 Gatilho
- Subtipo **852 "Protocolar Inicial - BB Autor"**: 501 tarefas cumpridas entre
  mai/2025 e jul/2026, ~50/mês em 2026, 99% com CNJ; escritório BB Autor (22).
- **Nenhuma tarefa 852 foi criada desde 24/07/2026** — conferido direto na API do
  L1 (`/Tasks?$filter=subTypeId eq 852`), não só no relatório. ⚠️ Confirmar com o
  operador se o protocolo passou a usar outro subtipo ou se a tarefa deixou de ser
  lançada.
- "Verificar Ajuizamento - BB Autor" **não é substituta**: nas 488 pastas que têm
  as duas, ela vem sempre ANTES do protocolo (é da criação da pasta, sem CNJ).
- Tribunais das execuções (set/2025→jul/2026): TJRO 151 · TJPA 95 · TJRN 52 ·
  TJAC 27 · TJAM 24 · TJSE 11 · TJRR 9 · TJAP 8.

### 2.2 Como o incidente vive hoje no L1
Os embargos já cadastrados à mão são **ProceduralIssues** (incidentes), pasta
filha da execução:

```
folder "Proc - 0068694/001"  title "EMBARGOS À EXECUÇÃO"  type Judicial
relatedLitigationType Lawsuit  relatedLitigationId 74387 (pasta mãe)
identifierNumber = CNJ dos embargos  distributionDate  actionTypeId 67
responsibleOfficeId 22 (BB Autor)  stateId/cityId da comarca
```

A API pública tem `POST /ProceduralIssues` (+ `/proceduralIssues/{id}/participants`)
→ cadastro por REST é viável. Risco conhecido: `POST /Lawsuits` **não dispara o
workflow** do L1 (ver Distribuídos BB); aqui não importa tanto, porque as tarefas
serão disparadas pelo próprio Flow.

Tarefas manuais que já existem em volta desse caso:
- 966 "Monitorar Embargos à Execução - BB Autor" (34 cumpridas) — é o que o módulo
  automatiza;
- 1295 "Impugnação aos Embargos à Execução - Ativos e BB Autor" (70);
- 1404 "Verificar novo Embargo - BB Autor" — nasce da fila de publicações sem
  pasta (motor `publication_sem_pasta_motor`, agendamento automático desligado).

### 2.3 Dá pra achar os embargos com dado público? Sim.
Sonda com **22 pares reais** (pasta da execução × incidente `/001` com os embargos):

| Resultado | Pares |
|---|---|
| Achado no **DataJud**: classe **172**, **mesma vara** (órgão julgador), ajuizado depois da execução | **20** |
| O `/001` era apelação, não embargos | 1 |
| Embargos de agosto ainda não indexados no DataJud (lag) | 1 |

Candidatos por vara na mesma busca (todos os embargos da vara desde a data da
execução): de 1 a 56. Numa passagem semanal que só olha os **novos** desde a
última consulta, cai para poucos.

**DataJud não tem partes.** Para dizer QUAL candidato é o nosso:
- **DJEN (comunicaapi) por número do candidato**: o texto traz
  `EMBARGANTE: <nome> … EMBARGADO: BANCO DO BRASIL`, e a execução traz os
  `EXECUTADO: …` → casa com as partes demandadas vindas do portal BB.
  4 de 5 pares tinham comunicação e todos bateram.
- Sinais de apoio (não decisivos): movimento de distribuição "por dependência"
  (3 de 5) e movimento "Petição" na execução no mesmo dia da distribuição dos
  embargos (3 de 5).

Consequência de desenho: **não precisa de robô por tribunal** (PJe/e-SAJ/eproc,
com captcha). DataJud acha, DJEN confirma, o operador valida.

⚠️ DJEN a partir do servidor exige proxy BR (`DJEN_PROXY`) — **não está setado em
produção** (a sonda rodou de uma máquina no Brasil).

## 3. Desenho

### 3.1 Estados do card (board)

```
NOVO ──► PARTES_OK ──► AGUARDANDO_JANELA ──► MONITORANDO ──► ENCONTRADO
                                                  ▲               │
                                                  └── recusado ───┤
                                                                  ▼
                        FECHADO ◄── TAREFAS_OK ◄── CADASTRADO ◄── CONFIRMADO
laterais: SEM_EMBARGOS (teto atingido / pasta encerrada) · ERRO_PARTES · ERRO_CADASTRO
```

- `ENCONTRADO` guarda o nível da evidência: **confirmado pelo DJEN** (nome do
  embargante bate) ou **só DataJud** (vara+classe+data, a verificar).
- Operador recusa o candidato → volta a `MONITORANDO` e o CNJ recusado nunca mais
  é oferecido para aquela execução.

### 3.2 Etapa 1 — Entrada (depende do HAR do relatório)
- Job diário baixa o relatório dedicado; upsert por `l1_task_id` (nunca apaga).
- `data_ajuizamento` = conclusão da tarefa; resolve `lawsuit_id`, pasta, CNJ e NPJ
  (title da pasta / custom field 3687).
- Pasta que já tem incidente de embargos (`ProceduralIssues` com
  `relatedLitigationId` = pasta) entra direto como `FECHADO` — não monitora o que
  já está cadastrado.

### 3.3 Etapa 2 — Partes demandadas no portal BB (mesmo dia)
- Reusa a sessão OneLog + `vinculos_bb` (undetected-chromedriver, JSON de dentro
  da SPA — Playwright/requests tomam 403 do WAF) ou `PortalBBColetor.extrair_envolvidos`.
- Lado do BB pelo CNPJ `00000000000191`; demandados = polo oposto (lógica de
  `vinculos_service.pesquisar_e_decidir`). Documento com zero à esquerda
  recomposto (`normalizar_documento`).
- Roda em processo filho com teto (padrão `coleta_supervisor`). **Falha ≠ lista
  vazia**: erro vira `ERRO_PARTES` e volta na próxima passagem.

### 3.4 Etapa 3 — Monitoramento no tribunal
- Janela inicial: `data_ajuizamento + N dias úteis` (N = 15/20/25, default global
  + ajuste por card), `prazo_calculator.add_business_days`. Depois, a cada 5 dias
  úteis. (Só feriados nacionais — sem recesso local.)
- **Passo A — DataJud**: capa da execução (código do órgão julgador, data)
  guardada no card; busca `classe.codigo = 172` + `orgaoJulgador.codigo` +
  `dataAjuizamento >= data da execução`, **uma consulta por vara** cobrindo todas
  as execuções monitoradas nela. Candidato já visto não volta.
- **Passo B — DJEN** por número do candidato: embargante ∈ demandados (nome
  normalizado; CPF/CNPJ quando o texto trouxer) e BB como embargado.
- **Teto** (regra da casa: "até achar" sempre com teto): proposta 12 meses de
  monitoramento ou pasta encerrada/arquivada → `SEM_EMBARGOS`, visível no board.
- Achou → para, **e-mail** para a Controladoria + responsável do card
  (`mail_service`, sem repetir aviso) e card no topo do board.

### 3.5 Etapa 4 — Verificação, cadastro e tarefas (controlador, pelo Flow)
- **L1**: `POST /ProceduralIssues` com os campos do §2.2 (pasta mãe, title,
  CNJ, data de distribuição, actionTypeId 67, escritório 22, UF/cidade herdados)
  + participantes. Validar primeiro numa cobaia; conferir se `folder` `/00N` sai
  sozinho (auto-suggest) ou precisa ser calculado.
- **Portal BB**: escrita NOVA (hoje o Flow só lê o portal) — precisa de HAR da tela
  de cadastro do incidente. Tratamento igual à ciência: dupla trava
  (setting global + confirmação do operador) e write-ahead antes do clique.
- **Tarefas**: `criar_tarefa_na_pasta` na pasta do incidente (já cancela/reenvia
  tarefa sem vínculo). Subtipos, responsável e prazo: a definir com o operador.

### 3.6 Encontro com a fila de publicações sem pasta
A publicação de embargos que chega sem pasta (motor `publication_sem_pasta_motor`,
ficha com `cnj_execucao` e `embargante`) passa a ser **segunda fonte**: se o CNJ
de origem ou vara+nome casar com uma execução monitorada, vira evidência no card
e **não** gera a tarefa 1404 em duplicidade.

## 4. Dados (prefixo `emb*`, migration `emb001`)
- `emb_execucao` — o card: tarefa, pasta/lawsuit, CNJ, NPJ, data de ajuizamento,
  estado, N dias úteis, próxima consulta, órgão julgador/tribunal, responsável,
  incidente criado.
- `emb_parte` — demandados vindos do BB (nome, CPF/CNPJ, PF/PJ, raw).
- `emb_consulta` — cada passagem (DataJud/DJEN/BB): quando, resultado, erro.
- `emb_candidato` — CNJ candidato, fonte, evidências, decisão do operador.
- `emb_evento` — trilha de auditoria (quem decidiu, cadastrou, disparou).

`down_revision` pelo head do **git** (`alembic heads` + `git ls-files`) na hora de criar.

## 5. Tela
- Aba **"Embargos à Execução"** no time **Controladoria** (`bb-cadastro`) do Minha
  Equipe, no molde da aba Análise de Risco da BB Réu: cards de KPI por estado que
  filtram, tabela paginada (25/50/100), busca.
- **Página dedicada por execução**: partes, linha do tempo das consultas,
  candidatos com evidência (trecho do DJEN), botões Confirmar/Recusar, Cadastrar
  no L1, Cadastrar no BB, Disparar tarefas.
- Configuração: N padrão, intervalo, teto, destinatários do aviso.

## 6. Operação
- Jobs APScheduler com `single_worker_lock` (próxima chave livre a conferir;
  hoje 826100001–008 em uso).
- Portal BB em processo filho supervisionado; DataJud com backoff de 429.
- Vigia de Filas: card parado em `NOVO`/`ERRO_PARTES` há mais de 1 dia útil,
  `MONITORANDO` com consulta atrasada, `ENCONTRADO` sem decisão há 3 dias úteis.

## 7. Fases
1. **Entrada + board + partes BB** — depende do HAR do relatório.
2. **Monitor DataJud + confirmação DJEN** — depende do proxy BR em produção.
3. **Cadastro do incidente no L1 + tarefas** — depende da definição das tarefas.
4. **Cadastro no portal BB** — depende do HAR da tela do portal.

## 8. Pendências com o operador
1. ~~HAR do relatório dedicado~~ — recebido: modelo **799** "ROBÔ - EMBARGOS À EXECUÇÃO"
   (filtros: subtipo 852 + Cumprido; colunas ajustadas com `Id` e `Data/hora conclusão efetiva`).
2. Subtipo 852 sem tarefa nova desde 24/07 — "o fluxo é novo" (operador); o relatório só
   importa conclusões a partir da data de corte.
3. "Documento da parte" = CPF/CNPJ + nome (premissa deste plano) ou também
   arquivo/PDF de documento pessoal?
4. Quais tarefas nascem ao cadastrar o incidente (subtipo, responsável, prazo)?
5. HAR da tela de cadastro do incidente no portal BB.
6. Teto do monitoramento (proposta: 12 meses) e quem recebe o aviso.

## 9. Implementação — fases 1 e 2 (11/09/2026)

**Decisão do operador:** casos antigos do relatório NÃO entram sozinhos. O modelo 799
traz todo o subtipo 852 desde 05/2025 (526 linhas); só entra tarefa com conclusão efetiva
a partir de `embargos_execucao_corte_relatorio` (vazio → a 1ª passagem grava o dia).
Legado entra por planilha, que não passa pelo corte.

| Peça | Onde |
|---|---|
| Tabelas `emb_execucao` (card por pasta), `emb_parte`, `emb_candidato`, `emb_evento` | `app/models/embargos_execucao.py`, migration `emb001_fluxo_embargos_execucao` (down `sqd005`) |
| Relatório 799: dispara o runner `generate-report.js`, poll da lista + `DocumentIsLoaded` (4 = sem dados), download, importação com corte | `relatorio_l1.py`, `importacao.py` (colunas por título) |
| Planilha do legado / inclusão manual | `POST /embargos-execucao/importar`, `/manual` |
| Partes demandadas no portal BB (processo filho, teto 30 min; falha de sessão não gasta tentativa) | `partes_bb.py`, `partes_runner.py` |
| Monitor: incidente já no L1 → capa DataJud → embargos da vara → DJEN → nível → aviso | `monitor.py`, `datajud_embargos.py`, `djen_embargos.py`, `aviso.py` |
| Jobs: relatório **1×/dia 7h20**, partes **1×/dia às 3h** (+ botão "Buscar partes no portal BB"), monitor 6h–20h (locks 826100009–011) | `worker.py`, registrado no `main.py` |
| Templates das tarefas do incidente (tela própria, mutável) + disparo pelo Flow no incidente localizado no L1 | `tarefas.py`, `EmbargosTemplatesPage.tsx`, seção "Incidente e tarefas" da página da execução |
| Aba "Embargos à Execução" na Controladoria + página da execução | `EmbargosExecucaoTab.tsx`, `EmbargosExecucaoDetalhePage.tsx` |

**Níveis de evidência do candidato:** `CONFIRMADO_DJEN` (embargante é parte demandada ou
executado da execução) · `PROVAVEL` (dependência + petição na execução no mesmo dia, só
quando o DJEN já conferiu sem conclusão ou está fora do ar) · `FRACO` · `DESCARTADO`
(DJEN mostra embargante de outro processo). Forte → card `ENCONTRADO`, para e avisa.

**Teste real (DataJud + DJEN de verdade, 4 execuções com embargos já cadastrados):**
3 encontradas e confirmadas pelo nome do embargante; a 4ª (embargos sem intimação
publicada) seguiu monitorando sem alarme. Na vara com 44 embargos saiu 1 "provável"
falso → PROVAVEL passou a exigir conferência do DJEN e a conferência prioriza
dependência + mais recentes.

**Antes de ligar em produção:**
- `DJEN_PROXY=socks5h://206.42.43.192:45123` no Coolify do Flow (o mesmo do Lake, que já
  roda com ele). Testado de dentro do container `api` de produção em 11/09: sem proxy 403,
  com proxy 200. PySocks já está na imagem. Não liga o fallback de publicações
  (`DJEN_ENABLED` segue false).
- Destinatários do aviso na tela de Parâmetros (e SMTP `MAIL_*` já existente).
- Templates de tarefa cadastrados na tela própria antes do primeiro disparo.
- Kill-switches: `EMBARGOS_EXECUCAO_RELATORIO_ATIVO`, `_PARTES_ATIVO`, `_MONITOR_ATIVO` (default true).

**Decisões de 11/09 (2ª rodada):**
- Relatório uma vez por dia (7h20). Partes uma passagem por dia de madrugada (3h) — cada
  execução é lida uma vez; só volta se o portal falhar (até 5 tentativas).
- **Cadastro do incidente no L1 e no portal do BB: MANUAL** por enquanto. O Flow localiza o
  incidente (`ProceduralIssues` com `startswith(folder,'Proc - X/')`) e dispara as tarefas
  dos templates, vinculadas ao incidente (linkType Litigation = id do incidente, como a
  tarefa 476538 → 98774). Tarefa aberta do mesmo subtipo no incidente não é duplicada;
  disparo repetido não recria o que já saiu. Todas criadas → card `CONCLUIDO`.
- **Embargado precisa ser o cliente da execução** (BB; Banese/Ativos pelo escritório): o DJEN
  traz "EMBARGADO:"; outro credor → candidato DESCARTADO na hora, mesmo com o nome do
  embargante batendo. Descartados não contam no board e ficam recolhidos na página.
- **Candidato forte → busca do CNJ no L1** antes de avisar (`search_lawsuit_by_cnj`, com
  fallback em /Litigations): já cadastrado (apenso/incidente ou outra pasta) → card
  `JA_CADASTRADO`, sem aviso. Incidente pode ter title NULO no L1 ("Proc - 0068696/001") —
  reconhecido também por `actionTypeId` 67 ou pelo CNJ do candidato.
- Teste no Docker local com 6 execuções reais: 9 candidatos descartados por embargado de outro
  credor (cooperativa, Bradesco, agência de fomento, municípios); 4 execuções saíram como
  "Já cadastrado no L1"; 2 seguem monitorando.
- **Futuro (segurar):** motor de monitoramento EXTERNO por API — o Flow envia os casos a
  monitorar e recebe o resultado. O monitor interno (DataJud + DJEN) fica até lá.
