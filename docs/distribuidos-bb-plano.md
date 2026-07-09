# Plano — Distribuídos BB → Cadastro no Flow

> Trazer o RPA de "processos distribuídos do Banco do Brasil" pra dentro do
> Flow: coleta na nuvem (login automatizado via OneLog), ciência controlada,
> distribuição de responsáveis no backend, e **cadastro via API do Legal One**
> como destino final (sem planilha de migração como saída principal).
>
> Status: **estudo/planejamento** (2026-07-09). Nada implementado ainda.
> Login do PAJ: **resolvido** — via OneLog (ver §3).

---

## 1. Contexto e objetivo

Hoje o cadastro de distribuídos do BB é um RPA **manual em 3 passos**, rodando
na máquina de um operador:

1. `abrir_chrome.bat` — abre Chrome com depuração remota; **humano loga** no PAJ.
2. `executar_rpa.bat` (`rpa_login_bb.py`) — conecta via CDP, consulta
   notificações por intervalo de data, extrai os campos de cada uma e **dá
   ciência** (clica "SIM"). Salva `MDR Processos distribuidos.xlsx`.
3. `executar_planilha.bat` (`gerar_planilha.py`) — distribui responsáveis
   (round-robin por polo/natureza), mapeia equipe/escritório, aplica regras de
   observação e gera `PLANILHA_MIGRACAO_COMPLETA.xlsx`, importada **manualmente**
   no Legal One.

**Objetivo:** absorver o fluxo inteiro no Flow, rodando **server-side na nuvem**
(Coolify), com:

- login automatizado (OneLog — sem humano no login);
- ciência controlada por gate de segurança + write-ahead + auditoria;
- distribuição no backend, com estado persistido e responsáveis/equipes
  configuráveis na UI (mata o `data.json` e os hardcodes do script);
- **cadastro via `POST /Lawsuits`** como destino (planilha de migração vira
  apenas fallback enquanto o cadastro via API não estiver 100%).

Decisões do operador (2026-07-09): trazer o RPA **inteiro** pra dentro do Flow;
login **automatizado** (OneLog); destino **cadastro via API L1 desde já**;
ciência **rodando na nuvem**.

---

## 2. O que já existe (não reinventar)

### 2.1 Repositório `MDR-Advocacia/Cadastro` (versão evoluída do RPA)

Já tem, em Python + Playwright:

- **`onelog_client.py`** — cliente do broker de sessão do BB (§3).
- **`browser_manager.py`** — `realizar_login_automatico()`: pega cookies do
  OneLog, injeta num Chromium (headless-capaz) e cai autenticado no PAJ.
- **`rpa_login_bb.py`** — loop de consulta/extração; ciência atrás do gate
  `BB_CONFIRMAR_NOTIFICACOES` (default `false` = modo seguro, NÃO clica "SIM");
  backup JSON write-ahead **antes** de qualquer confirmação.
- **`gerar_planilha.py`** — motor de distribuição + planilha Legal One.
- **`BACKLOG.md`** — já esboça o lado cadastro (contatos por CPF/CNPJ, capa do
  NPJ, `POST /Lawsuits`, honorário regra BB, "dashboard no Flow").

> Boa parte disso migra **quase como está** pro Flow — é Python, e o Flow já
> roda Python Playwright no Coolify (ver 2.2).

### 2.2 Infra de RPA que o Flow já tem

- **Python Playwright no Coolify** — o AJUS já usa `sync_playwright()` +
  `chromium.launch()` com sessão persistida (`config.ajus_session_path =
  /app/data/ajus-session`, montado em `/data/ajus-session/` no Coolify). Ou
  seja: **navegador headless já roda no container do Flow** — o RPA do BB entra
  no mesmo molde, com sessão própria em `/data/bb-session/`.
- **Runners Node Playwright** (`app/runners/legalone/*.js`) — padrão alternativo
  (estado em JSON, sinais de controle pause/resume). *Não* precisamos deles aqui,
  já que o código do BB é Python; ficam como referência de padrão de progresso.
- **Client do Legal One** — `app/services/legal_one_client.py` (usado por
  publications, onerequest, varredura).
- **Padrão de operação longa server-backed** — worker que auto-completa +
  endpoint de contagem por status + barra de progresso + auto-poll (memory
  `feedback_operacao_longa_server_backed`). O módulo nasce nesse padrão.

### 2.3 Motor de cadastro via API L1 (já estudado)

`project-cadastro-processos-api` + (na `main`/`feat`) `docs/cadastro-processos-plano.md`:

- `POST /Lawsuits` funciona; payload mínimo mapeado.
- Custom fields **obrigatórios** do tenant: **3687 "Número do Cliente" (NPJ,
  texto)** + **3691 "Data de Terceirização" (data)**.
- Participantes: Customer (BB) + PersonInCharge (responsável), com
  `isMainParticipant`.
- Proibidos no create: `countryId`, `costCenters`.
- **Bloqueio**: config do tenant "negociação de contrato de honorário
  obrigatória" recusa o create via API (ver §7 / Fase 0).

> **Insight central:** os distribuídos do BB não são um destino novo — são uma
> **fonte nova alimentando o motor de cadastro que já foi estudado**. O módulo
> deve **convergir** com `cadastro` (mesmo motor de `POST /Lawsuits`, mesmo
> dedup por CNJ/pasta), não duplicá-lo.

---

## 3. Login automatizado — OneLog (broker de sessão do BB)

Serviço interno: `https://api-onelog.mdradvocacia.com`, usuário `robo.cadastro`.
Resolve o login pesado do PAJ centralmente (certificado/2FA/gov.br ficam **do
lado do OneLog**) e devolve **cookies** prontos pra injetar no browser.

**Fluxo "zerocore"** (de `onelog_client.py`):

1. `POST /api/zerocore/login` `{username, password, user_agent}` → devolve
   `setor` + `status`. Se `status == "sucesso"`, já vêm `cookies`.
2. Se enfileirou: poll `GET /api/zerocore/status?setor=<setor>` até
   `concluido == true` (ou `erro == true`). Default: 150 tentativas × 2s.
3. `POST /api/zerocore/session` `{username, password, setor}` → `cookies` +
   `user_agent` finais.
4. **Marcapasso**: `POST /api/zerocore/renew` a cada ~15 min pra manter a sessão
   viva (`SESSION_TIMEOUT_SECONDS=1800`, renova antes de `1320`).

Injeção no Playwright (`browser_manager.py`): normaliza cookies (name/value/
domain/path/secure/httpOnly/sameSite/expires), cria contexto com o `user_agent`
do OneLog, `locale=pt-BR`, `timezone=America/Fortaleza`, injeta os cookies e
navega pro `URL_PORTAL_NOTIFICACOES`.

**No Flow:**

- Portar `onelog_client.py` → `app/services/distribuidos_bb/onelog_client.py`.
- Credenciais `ONELOG_USERNAME` / `ONELOG_PASSWORD` como env do serviço `api` no
  Coolify (nunca no `.env` commitado; ver CLAUDE.md — Coolify lê do painel).
- O marcapasso roda dentro do worker enquanto a coleta está ativa.
- **Rede/alcance:** confirmar que o container `api` do Flow alcança
  `api-onelog.mdradvocacia.com` (host público? rede Docker interna?). Mesma
  pegadinha do OneRequest (mesmo servidor ≠ mesma rede) — validar antes.

---

## 4. Arquitetura-alvo

```
┌─ Worker de coleta (Python Playwright, no Coolify) ─┐
│ 1. OneLog: obter cookies (zerocore) + marcapasso   │
│ 2. PAJ: consultar notificações por intervalo data  │
│ 3. extrair 11 campos + CNJ por notificação         │
│ 4. WRITE-AHEAD: persistir linha (status COLETADO)  │
│ 5. (gate) dar ciência "SIM" → status CIENCIA_DADA  │
└───────────────────────┬────────────────────────────┘
                        ▼
┌─ Backend Flow (motor) ─────────────────────────────┐
│ 6. dedup por CNJ (ou NPJ quando sem CNJ)           │
│ 7. distribuir responsável (round-robin PERSISTIDO) │
│    + equipe/escritório/regras → status DISTRIBUIDO │
│ 8. resolver contatos L1 (CPF/CNPJ) — capa do NPJ   │
└───────────────────────┬────────────────────────────┘
                        ▼
┌─ Destino ──────────────────────────────────────────┐
│ 9a. POST /Lawsuits (custom fields 3687/3691 +      │
│     participantes + honorário regra BB) → CADASTRADO│
│ 9b. (fallback) gerar planilha de migração p/ import │
│ 10. conferir disparo de workflow L1; conciliação    │
└─────────────────────────────────────────────────────┘
```

Tudo server-side, com barra de progresso e auto-poll (padrão da casa).

---

## 5. Modelo de dados

Módulo novo, convergindo com `cadastro`. Sugestão de tabelas (prefixo de
migration a definir — ver §9; provável reuso de `cad*`):

- **`bb_distribuidos`** — uma linha por notificação/processo distribuído:
  - identidade: `cnj` (nullable), `npj`, `notificacao_seq`, `fingerprint`
    (= `cnj or npj`, único, pra dedup);
  - capturados: `polo`, `natureza`, `acao`, `valor_causa`, `data_ajuizamento`,
    `situacao`, `tramitacao`, `advogado`, `adverso_principal`, `raw_html`
    (capa/auditoria);
  - distribuição: `responsavel_id` (→ LegalOneUser), `escritorio_path`,
    `observacao` (Ajuizamento/Reterceirizado/Cadastro), `equipe` (JSON);
  - ciclo: `status` (enum abaixo), `ciencia_dada_em`, `l1_lawsuit_id`,
    `l1_workflow_task_id`, timestamps, `run_id`, `erro` (texto).
- **`bb_coleta_runs`** — cabeçalho de cada execução (intervalo de datas,
  disparado_por, contadores por status, início/fim) pra progresso e auditoria.
- **`bb_distribuicao_estado`** — ponteiro persistido do round-robin por fila
  (Réu / Autor / Interessado / Trabalhista) pra equilibrar **entre execuções**
  (mata o `random.shuffle` a cada run).
- **Config de distribuição** (tabela ou `app_settings`): responsáveis por
  fila, equipes por responsável, mapa escritório por polo, dupla de ajuizamento
  — tudo editável na UI.

**Enum de status** (fio da meada da auditoria):
`COLETADO → CIENCIA_DADA → DISTRIBUIDO → CONTATOS_RESOLVIDOS → CADASTRADO`
com ramos `ERRO` e `REVISAO` (pendência humana). Nunca deleta.

---

## 6. Motor de distribuição (porta do `gerar_planilha.py`)

Regras atuais a preservar (do script), agora no backend e configuráveis:

- **Filas por posição** (mapa polo→posição: Passivo→Réu, Ativo→Autor,
  Neutro→Interessado):
  - `Trabalhista` (por natureza) → responsável fixo + escritório
    `.../Banco do Brasil / Trabalhista`.
  - `Réu` → fila de réu, escritório `.../Réu`.
  - `Autor`/`Interessado` → fila de autor, escritório `.../{posição}`.
- **Round-robin persistido** (não `random.shuffle` por run) — o ponteiro fica
  em `bb_distribuicao_estado`, então a carga equilibra ao longo do tempo.
- **Regra de observação**:
  - Autor **sem** CNJ (`0000000-00...` ou vazio) → `Ajuizamento` (+ equipe de
    ajuizamento, dupla alternada advogado/assistente);
  - Autor **com** CNJ → `Reterceirizado`;
  - Réu/Trabalhista → `Cadastro`.
- **Valor da causa**: normalizar `R$ 1.234,56` → `1234.56`.
- **Data de ajuizamento**: ignorar quando "A cadastrar"/vazio.
- **Equipes** (envolvidos): hoje vêm do `data.json` (vazio!). Passam a vir da
  config de UI, resolvendo nomes → `LegalOneUser.id`.

Saída do motor alimenta **tanto** o `POST /Lawsuits` **quanto** (fallback) a
planilha de migração — mesma estrutura de dados, dois renderizadores.

---

## 7. Destino: cadastro via API L1

Reusa o motor de `project-cadastro-processos-api`. Passos por processo:

1. **Resolver contatos** (BACKLOG do repo): buscar no L1 por **CPF/CNPJ** cada
   envolvido; criar quando não existir (não confiar no dedupe do L1). Capturar
   **a capa do NPJ** no PAJ pra pegar **todos** os envolvidos (avalista,
   devedor, representante, advogado…), não só a contraparte principal.
2. **`POST /Lawsuits`** com:
   - custom fields **3687 (NPJ)** + **3691 (Data de Terceirização)**;
   - participantes: Customer (BB, `00.000.000/0001-91`) + PersonInCharge
     (responsável distribuído) + demais envolvidos resolvidos;
   - polo/posição, natureza, valor da causa, escritório;
   - **honorário conforme regra BB autor/réu** (a definir — ver Fase 0).
3. Registrar `l1_lawsuit_id` na linha.
4. **Workflow L1**: verificar se a criação da pasta dispara o workflow
   automaticamente; se **não**, mapear acionamento por API (reusa aprendizado de
   `reference_l1_reatribuir_workflow` / tasks de Workflow). Registrar evidência.
5. **Conciliação**: volume BB × capturado × pastas criadas × workflows
   disparados — relatório de divergência sem Excel manual.

### ⚠️ Bloqueio de honorário (Fase 0 — não é código)

A config do tenant exige "negociação de contrato de honorário obrigatória", o
que **recusa o create via API**. Dois caminhos (não excludentes):

- **(a) Incluir o honorário no payload** conforme a **regra BB autor/réu** — se
  a regra estiver definida e o payload aceitar, pode destravar sem mexer na
  config. É o item 4 do BACKLOG.
- **(b) Ajuste administrativo** da config do tenant (decisão financeira).

Enquanto não destrava, o **fallback planilha de migração** mantém o fluxo
end-to-end funcional (o motor já produz os dados; só troca o renderizador).

---

## 8. Ciência — segurança operacional (inegociável)

Dar ciência ("SIM") é **irreversível** e tem **consequência jurídica** (inicia
prazo). Rodando unattended na nuvem:

- **Gate `BB_CONFIRMAR_NOTIFICACOES`** (default `false` = modo seguro): o RPA
  extrai e persiste, mas **NÃO** clica "SIM" até liberação explícita. Já existe
  no script; vira flag de config na UI, por run.
- **Write-ahead obrigatório**: persistir a linha (`COLETADO`) **antes** de
  clicar "SIM" — nunca dar ciência sem registro. Já é o comportamento atual
  (`salvar_backup_json()` roda antes do "SIM").
- **Auditoria por notificação**: `ciencia_dada_em` + `run_id` + `raw_html` da
  capa. Idempotência: dedup por fingerprint evita ciência dupla.
- **Princípio do BACKLOG**: "não confirmar recebimento no BB sem autorização
  explícita" e "não criar pasta incompleta sem status de pendência/revisão".

---

## 9. Migrations

- O destino **converge com o módulo `cadastro`**. Preferir reusar o prefixo
  **`cad*`** e as tabelas de cadastro onde fizer sentido, em vez de criar um
  módulo isolado que duplique `POST /Lawsuits` e dedup.
- **Antes de criar QUALQUER migration**: `alembic heads` (regra da casa — já
  travou o boot do Coolify com MultipleHeads). Se houver 2 heads, migration de
  `merge_heads` primeiro.

---

## 10. Fases de entrega

- **Fase 0 — destravar cadastro via API (não-código).** Definir a regra de
  honorário BB autor/réu e/ou ajustar a config do tenant. Sem isso, o destino
  final roda em fallback-planilha.
- **Fase 1 — motor de distribuição no backend.** Portar `gerar_planilha.py`:
  round-robin **persistido**, responsáveis/equipes/escritórios na UI, regras de
  observação como flags. Testável já com os xlsx que o RPA legado gera + geração
  da planilha de migração como saída. Entrega valor sem depender do RPA na nuvem.
- **Fase 2 — coleta na nuvem (OneLog + Playwright).** Portar `onelog_client.py`
  + `browser_manager.py` + loop de extração pra `app/services/distribuidos_bb/`.
  Worker server-backed + barra de progresso + marcapasso. Tabela `bb_distribuidos`
  + `bb_coleta_runs` + auditoria. Ciência atrás do gate (default seguro).
- **Fase 3 — cadastro via API.** Resolver contatos (CPF/CNPJ + capa do NPJ),
  `POST /Lawsuits` com custom fields + honorário, checar/acionar workflow,
  conciliação. Ativa quando Fase 0 destravar; até lá, fallback-planilha.
- **Fase 4 — dashboard/conciliação no Flow** (BACKLOG): status por linha
  (capturado → contato resolvido → pasta criada → workflow disparado →
  erro/revisão) + cruzamento de volumes.

---

## 11. Riscos e decisões abertas

- **Alcance de rede do OneLog** a partir do container `api` (host público vs
  rede Docker). Validar cedo — mesma pegadinha do OneRequest.
- **Estabilidade dos seletores do PAJ** (`bb-title`, `chip__desc`,
  `mi--event-note`, iframe `WIDGET_ID_1`). RPA de portal quebra quando a UI muda
  — precisa de guarda/alerta quando a extração vier vazia (não seguir dando
  ciência às cegas).
- **Honorário** (Fase 0) — bloqueio administrativo/financeiro fora do código.
- **Capa do NPJ**: mapear onde aparecem CPF/CNPJ e papéis dos envolvidos (item
  aberto no BACKLOG) — necessário pra resolver contatos corretamente.
- **Workflow L1**: confirmar se dispara sozinho no create ou precisa acionar por
  API.
- **Python vs Node runner**: recomendação é **Python** (reusa o código do repo e
  o AJUS já prova Python Playwright no Coolify). Confirmar que não há atrito com
  o pool de workers do uvicorn (usar advisory lock como no OneRequest se o
  scheduler rodar em múltiplos workers).

---

## 12. Convergências (memories relacionadas)

- `project-cadastro-processos-api` — motor de `POST /Lawsuits` (custom fields
  3687/3691, bloqueio de honorário). **Convergir, não duplicar.**
- `reference_onerequest_source_db` — padrão RPA↔Flow, advisory lock multi-worker,
  pegadinha de rede Docker.
- `feedback_operacao_longa_server_backed` — worker + barra de progresso por padrão.
- `reference_l1_folder_lookup` / dedup por CNJ+pasta.
- `reference_l1_reatribuir_workflow` — acionamento/manipulação de workflow L1.
- BACKLOG do repo `MDR-Advocacia/Cadastro` — lado cadastro (contatos, capa,
  workflow, dashboard).
