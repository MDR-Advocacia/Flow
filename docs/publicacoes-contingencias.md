# Captura de Publicações — contingências (runbook)

Documento operacional: o que existe, o que está ligado, **quais variáveis setar
para ativar** e como conferir se funcionou.

Escrito em 31/07/2026, depois de dois dias seguidos com a API do Legal One fora
do ar. Todos os números aqui foram medidos em produção, não estimados.

---

## As quatro camadas

A captura tenta as camadas em ordem e para na primeira que resolver. Só cai pra
próxima quando a anterior **falha**.

| # | Camada | Depende de | Estado |
|---|---|---|---|
| 1 | **API do L1** — `GET /Updates` | API do Legal One | sempre ligada |
| 2 | **Relatório do L1 Web** — modelo 789 | site do Legal One | **ligada** |
| 3 | **DJEN/Comunica** — consulta por OAB | proxy BR + CNJ | **desligada** |
| 4 | **Upload manual** — operador sobe a planilha | ninguém | sempre disponível |

O que justifica cada uma: a camada 2 cobre "API caiu mas o site está de pé", que
é o modo de falha mais comum — foi o de 30 e 31/07/2026. A camada 3 cobre o
Legal One inteiro fora. A 4 funciona com tudo fora.

---

## Variáveis de ambiente

### Para ATIVAR o DJEN (camada 3)

No painel do Coolify, no serviço da API:

```
DJEN_ENABLED=true
DJEN_PROXY=socks5h://206.42.43.192:45123
```

**O proxy é obrigatório.** A Comunica é pública mas bloqueia IP de datacenter
fora do Brasil: do IP da AWS responde **403**. Esse endereço é o MikroTik do
escritório, o mesmo que o Lake e o OneLog já usam. Medido: direto 403, pelo
proxy 200, IP de saída `179.190.133.4`.

Opcionais da camada 3 (têm default no código, só setar pra mudar):

| Variável | Default | Para quê |
|---|---|---|
| `DJEN_OABS` | `5553:RN` | OABs consultadas, formato `numero:UF`, vírgula separa |
| `DJEN_SOMENTE_CARTEIRA` | `true` | ignora publicação de processo que a base não conhece |
| `DJEN_REQUEST_DELAY_SECONDS` | `3.1` | espera entre chamadas à Comunica |
| `DJEN_MAX_PAGES` | `200` | teto de paginação por OAB |

### Já ligadas — não precisa setar nada

| Variável | Default | Para quê |
|---|---|---|
| `PUBLICATION_REPORT_FALLBACK_ENABLED` | `true` | camada 2. Setar `false` DESLIGA |
| `PUBLICATION_REPORT_FALLBACK_DIAS_ATRAS` | `1` | janela D-1 → D0 |
| `PUBLICATION_ALERT_EMAIL` | `ti@` + `jonilsonvilela@` | quem recebe o alerta. Setar só pra MUDAR |

O destinatário do alerta tem default **no código**, igual aos outros alertas da
casa. Se dependesse de variável, o esquecimento só apareceria na madrugada em
que o alerta fosse necessário.

---

## Os alertas

Um e-mail **por rodada**, nunca por escritório — 13 escritórios caídos mandariam
13 e-mails da mesma queda, e alerta que vira ruído ninguém lê. Como a rodada é
diária (cron `0 1 * * *`), o teto é um por dia.

| Situação | Assunto | O que fazer |
|---|---|---|
| Falhou e nenhuma contingência salvou | `Captura de Publicações` | subir a planilha à mão (camada 4) |
| Contingência salvou a rodada | `Captura de Publicações (contingência ativada)` | nada urgente — mas a API do L1 está quebrada, vale chamado |

Sem destinatário configurado, a falha vira log **ERROR**. A falta de
destinatário não pode ser mais um silêncio.

---

## Como conferir se a madrugada foi bem

```sql
-- A rodada rodou e o que aconteceu por escritório
SELECT status, count(*), to_char(min(created_at),'HH24:MI') AS primeira,
       to_char(max(created_at),'HH24:MI') AS ultima
  FROM publication_fetch_attempt
 WHERE created_at::date = CURRENT_DATE GROUP BY 1;

-- Quem capturou: 'scheduler' = API; 'scheduler-contingencia' = camada 2 ou 3
SELECT id, status, requested_by_email, total_found, total_duplicate,
       to_char(created_at,'HH24:MI:SS') AS em
  FROM publicacao_buscas
 WHERE created_at::date = CURRENT_DATE ORDER BY id;
```

No log da API, as linhas que interessam:

```
CONTINGÊNCIA ATIVA (legado, relatório #NNNNN): N escritório(s) recuperados.
Alerta de contingência ativada enviado (relatório #NNNNN).
```

**Atenção ao `last_status` da automação:** ele pode dizer `success` mesmo com
todos os escritórios falhando, porque o passo roda até o fim. Foi por isso que a
falha de 30/07 passou batida em todo lugar. Confie nas consultas acima, não nele.

---

## Armadilhas conhecidas

**Produção roda em modo legado.** `PUBLICATION_SCHEDULER_BATCH_MODE=false`. A
contingência precisa estar enganchada nos dois caminhos — na primeira versão
ficou só no batch e nunca teria disparado.

**O relatório 789 é dependência de produção.** Se alguém editar e tirar a coluna
`Id` no Legal One, a camada 2 para de funcionar. O importador recusa com motivo
em vez de importar errado, e a contingência devolve `ok=false`.

**Teste que simula queda do L1 precisa mockar `capturar_publicacoes`.** Senão a
suíte vai no site do Legal One e gera relatório de verdade — foi assim que saiu o
relatório #13435. Use o helper `_patch_contingencia`.

**`PySocks` é obrigatório pro proxy.** Já está no `requirements.txt`; se um dia
sumir, o DJEN falha com `Missing dependencies for SOCKS support`.

**Nunca resolver processo por CNJ sozinho.** 1.378 CNJs da base têm mais de um
`lawsuit_id` (apensos e pastas duplicadas). O DJEN deixa esses **sem vínculo** de
propósito: publicação na pasta errada vira tarefa no processo errado.

---

## Números medidos em produção

**Camada 2 (relatório), 31/07/2026 — funcionou sozinha de madrugada:**

- 01:00 → 02:14: 13 escritórios, 13 falhas (`Maximo de tentativas excedido`)
- 02:21:37: relatório #13470 disparado, janela 30/07 → 31/07
- 02:21:38: **593 publicações de 593 processos em 19s**
- 02:22:01: **13 escritórios recuperados**
- 02:22:03: e-mail enviado
- Resultado: **530 barradas pela dedup** (vieram do upload manual do dia
  anterior) e **10 novas**, já classificadas

**Camada 3 (DJEN), medida em 31/07/2026:**

- 1.017 comunicações em 19,3s, 11 páginas, sem truncamento
- 798 vinculadas a uma pasta
- 219 sem vínculo — 27 por CNJ ambíguo, 192 fora da carteira (ignoradas)
- 343 de 720 pares (processo, data) já existiam no Flow

**Camada 4 (upload manual), 30/07/2026:**

- 1.238 linhas, 1.238 válidas, 0 ignoradas, 12/12 escritórios reconhecidos
- 1.202 novas + 36 obsoletas detectadas sozinhas
- reimport do mesmo arquivo criou **0** registros

---

## Regra de arquitetura (não quebrar)

Nenhuma fonte alternativa grava em `publicacao_registros`. Todas convertem para
o mesmo contrato de dicionário que a API do L1 devolve e entram por
`create_and_run_search(prefetched_publications=...)`.

Com isso, dedup em duas camadas, detecção de publicação obsoleta e a "enxugada"
antes da classificação valem de graça — sem lógica duplicada e sem risco de
divergir do fluxo automático.

Idempotência sem coluna nova: `legal_one_update_id` sintético **negativo** (o id
real do L1 é positivo, o menor em produção é 1.356.393) e determinístico. Em
caso de colisão o id anda, pra publicação nunca sumir em silêncio.

---

## Arquivos

| O quê | Onde |
|---|---|
| Camada 2 — gera e baixa o relatório | `app/services/publication_l1_report_fallback.py` |
| Corpo do POST do relatório | `app/services/forms/publicacoes_report_form.txt` |
| Camada 3 — DJEN/Comunica | `app/services/djen_publication_fallback.py` |
| Camada 4 — leitor da planilha | `app/services/publication_spreadsheet_import.py` |
| Alertas | `app/services/publication_capture_alerts.py` |
| Encadeamento das camadas | `app/services/scheduled_automation_service.py::_contingencia` |

Detalhe do método por planilha: `docs/publicacoes-fallback-planilha-manual.md`.
