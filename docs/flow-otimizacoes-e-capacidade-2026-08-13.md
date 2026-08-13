# Flow — otimizações de 13/08/2026 e leitura sobre a capacidade do servidor

**Autor:** revisão técnica do Flow
**Data:** 13/08/2026
**Contexto:** resposta ao documento *"Capacidade da EC2 e governança de recursos"*
e ao *"Contrato de clientes do OneLog"*.

---

## Sumário executivo

Três mudanças entraram na `main` hoje, todas validadas contra produção sem
escrever nada:

| Commit | O que faz | Impacto principal |
| --- | --- | --- |
| `c305aa4` | Trabalho de fundo passa a rodar em **um worker só** | Corta ~63% das execuções de fundo (ver ressalva abaixo) |
| `c305aa4` | **Retenção** dos artefatos do Playwright | Libera 1,94 GB e trava o crescimento |
| `0ed4b79` | Cliente do OneLog adere ao **contrato publicado** | Sai de ~450 para ~30 consultas por espera |

A mais relevante — a do scheduler — **não estava no documento de capacidade**,
porque ela só aparece olhando o interior da aplicação, não o host.

Minha leitura sobre a decisão de infra está na
[última seção](#opinião-sobre-o-servidor): o Flow sai da lista de ofensores com
o que foi feito, mas **isso não substitui o upgrade**.

---

## 1. Trabalho de fundo rodava em quádruplo

### O problema

O Uvicorn sobe com `--workers 4`. Cada worker é um processo Python completo que
executa o `main.py` **inteiro**, inclusive o startup. Medição no container de
produção:

```
APScheduler started: 4
4 × Watchdog de publicações registrado
4 × Distribuídos BB agendado: 3 passagem(ns)/dia
4 × Tratamento Web autorun registrado
4 × Reagendamento: catch-up
```

São **19 jobs agendados, cada um registrado 4 vezes**.

O detalhe que engana: vários jobs usam `max_instances=1`. Isso **não resolve** —
essa flag só impede sobreposição *dentro de um scheduler*, e aqui existiam
quatro schedulers em quatro processos distintos. Cada um disparava a sua própria
instância, no mesmo segundo.

Fazendo a conta das travas reais: só **8 arquivos de serviço** usam lock entre
processos (`FileLock` / advisory lock). Os demais **executavam de fato em
quádruplo** a cada tique do cron.

> A linha `"outro worker já rodando — pulando"` do Distribuídos BB era o único
> job que já tinha percebido o problema — e tratado só para si.

### Por que isso importava

Num host de 4 vCPU compartilhado com outros projetos, o custo era:

- **4× chamadas ao Legal One** (fonte plausível dos `429` que vimos)
- **4× carga no Postgres**
- **4× CPU de trabalho de fundo**

E o gargalo do host, segundo o próprio diagnóstico da EC2, **é CPU** — média de
1,74 vCPU contra baseline de 1,6, com PSI marcando ~22% de espera por CPU.

### A correção

Eleição de líder por *file lock* não-bloqueante no startup:

- o primeiro worker que sobe adquire o lock e vira **LÍDER** — só ele inicia o
  APScheduler e o batch worker;
- os outros três seguem **servindo HTTP normalmente**, sem trabalho de fundo;
- o lock é segurado pela vida do processo. Se o líder morrer, o SO libera o
  `flock` e o worker que o Uvicorn respawna assume na inicialização.

**Capacidade web não muda** — continuam 4 workers atendendo requisição. O que
muda é que o trabalho de fundo acontece uma vez, não quatro.

### Um cuidado que quase passou batido

O acumulador do **relatório de utilização é por processo** — cada worker
acumula o próprio pedaço conforme atende requisições. Se eu tivesse
simplesmente pulado o `shutdown` dos secundários, **todo redeploy perderia o
acumulado dos outros três** e o relatório subcontaria justamente quem estava
trabalhando na hora.

O flush periódico se dispara sozinho dentro do `registrar()` (não depende do
scheduler), mas o de parada precisava ser preservado — e foi.

### Resultado esperado

- Redução de ~63% nas execuções de jobs de fundo

  **Correção de um número que eu tinha inflado.** Escrevi ~75% (o corte
  aritmético de 4 schedulers para 1) antes de auditar job a job. Auditados os
  21, **9 já tinham `single_worker_lock`** — e não os menores: o autorun do
  Tratamento Web, a coleta do BB e o monitor de cadastro, que são os mais
  caros, já rodavam uma vez só. Esses 9 continuavam *acordando* nos 4 workers,
  mas 3 desistiam no lock e voltavam a dormir.

  O que muda de fato: as execuções caem ~63%, e o **alívio de CPU é bem menor
  que isso**, porque o que estava sendo executado 4× de verdade eram os 12 jobs
  mais leves. O ganho real e inegociável está na linha de baixo — os jobs sem
  lock deixam de correr contra si mesmos.
- Menos `429` do Legal One
- Menos disputa no Postgres
- **Correção de comportamento**, não só de recurso: jobs sem lock deixam de
  rodar concorrentes consigo mesmos

### Como verificar depois do deploy

No log do container, deve aparecer:

```
Worker LÍDER: iniciando scheduler e trabalho de fundo.
Worker secundário: serve HTTP, sem scheduler nem batch worker.   (×3)
APScheduler started    ← uma vez só, não quatro
```

---

## 2. Retenção dos artefatos do Playwright

### O problema

`/app/output` estava com **5,8 GB**, sendo **5,21 GB** de runs do tratamento de
publicações — 164 runs guardados desde sempre, **sem nenhuma política de
descarte**, num disco a 83%.

A composição de um run mostra onde está o peso:

| Item | Tamanho | Para que serve |
| --- | ---: | --- |
| `artifacts/` | **265 MB** | 1.230 PNGs + 1.230 JSONs (uma captura por publicação) |
| `status.json` | 1,6 MB | **É o que a tela lê** — histórico e progresso |
| `runner.log` | 1,3 MB | Diagnóstico |
| `input.json` | 552 KB | Entrada do run |
| `runner.err.log` | 4 KB | Erros |

**99% do volume são capturas de diagnóstico.** Tudo o que a UI e a auditoria
realmente usam cabe em ~3,5 MB por run.

### A correção

A rotina apaga **somente `artifacts/`** de runs antigos. Status, logs e input
ficam — o histórico na tela continua completo e dá para auditar o que foi
tratado.

### A janela foi escolhida com medição, não por gosto

Minha proposta inicial era 14 dias. Simulei todas as janelas contra o disco real
e **o número me desmentiu**:

| Política | Libera | Sobra em disco |
| --- | ---: | ---: |
| 30 dias | 0,21 GB | ~5,00 GB |
| 21 dias | 0,32 GB | ~4,89 GB |
| **14 dias** | **0,39 GB** | ~4,82 GB |
| 10 dias | 0,80 GB | ~4,41 GB |
| **7 dias** ✅ | **1,94 GB** | ~3,27 GB |
| 5 dias | 3,17 GB | ~2,04 GB |
| 3 dias | 3,54 GB | ~1,67 GB |

O volume está concentrado nos **últimos dias**, porque o autorun do tratamento
passou a rodar 4×/dia sobre filas grandes. Reter 14 ou 30 dias seria escolher
uma política que quase não faz nada.

**7 dias** dá uma semana inteira para investigar qualquer tratamento e ainda
trava o crescimento — que era o ponto.

### O que se perde

Apenas a captura de tela de um item tratado há mais de uma semana. O
`status.json` guarda status e mensagem de erro de **cada item**, e nunca é
apagado.

### Agendamento

Diário às **04:20 UTC (01:20 BRT)** — fora da janela de trabalho e longe das
coletas agendadas (03h/12h/20h BRT), para não disputar I/O com elas.

---

## 3. Aderência ao contrato do OneLog

Resposta ao documento *"Contrato de clientes do OneLog"*.

### Onde já estávamos certos

- Teto de espera de **15 min** (o contrato pede ≥ 12)
- **Um `/login` por fluxo**, seguido apenas de `/status`
- Nunca pedimos credencial de novo; nunca logamos senha ou cookie

### O que estava errado

**Polling fixo em 2 segundos** — literalmente a assinatura que o documento usa
para identificar cliente desatualizado. Numa espera de 15 minutos, eram **~450
consultas**.

Agora a cadência vem do servidor, na ordem definida pelo contrato:

```
poll_after_seconds (JSON)  →  header Retry-After  →  5s (mínimo seguro)
```

Com piso de 5s (protege o servidor) e teto de 60s (evita que uma orientação
exagerada consuma a janela inteira numa espera só).

| Cenário | Consultas por espera de 15 min |
| --- | ---: |
| Antes (2s fixo) | ~450 |
| Agora, pior caso (5s) | ~180 |
| Com o exemplo do documento (30s) | **~30** |

### E o que muda comportamento de verdade

**Ignorávamos `retryable`.** Quando o worker preserva a solicitação para retry,
continuávamos martelando o status como se nada tivesse mudado. Agora:

1. O erro carrega `retryable` e `retry_after_seconds`
2. **A retentativa da coleta espera o intervalo que o servidor informou**, em vez
   do fixo — tentar antes só gera login inútil, que é o que o contrato pede para
   evitar
3. O diagnóstico ganhou o veredito **TEMPORÁRIO**, separado de PROBLEMA

Sobre o item 3, uma escolha que vale explicar: `TEMPORARIO` **não dispara
e-mail** para a TI, porque por contrato se resolve sozinho. Mas se a coleta
falhar de vez, o e-mail sai normalmente **com o veredito anexado** — o operador
continua sabendo, sem receber alarme de infra para algo que não é.

Também passamos a registrar `request_id` quando presente (observabilidade pedida
no documento).

### Ressalva honesta

Validei com respostas **simuladas** — 6 casos de cadência mais o exemplo literal
do documento, zero falhas. O OneLog em produção ainda não devolve esses campos.
Quando a versão nova do serviço subir, vale uma coleta real para confirmar que
os campos chegam com os nomes esperados. O código lida bem com a ausência deles,
então não há risco em ter subido antes.

---

## Validação

Tudo verificado **no container de produção, sem escrever nada**:

| Verificação | Resultado |
| --- | --- |
| Eleição de líder com 4 processos concorrentes | **Exatamente 1 líder** |
| Retenção em simulação contra o disco real | 121 runs elegíveis, **165/165 `status.json` preservados** |
| Cadência do OneLog (6 casos + exemplo do doc) | **0 falhas** |
| Import do app inteiro em cópia isolada | **OK** |

### O que ainda não foi verificado

- As três mudanças **não estão em produção** — dependem do redeploy
- Os testes do PR do OneNotify não rodaram (o container não tem `pytest`)
- A aderência ao contrato do OneLog foi testada contra respostas simuladas

---

## Opinião sobre o servidor

### O documento de capacidade está certo no essencial

Concordo com as duas conclusões centrais: **o OneLog não é o vilão** (com 1 vCPU
e 1,5 GiB de teto, ele não explica ocupação global de 100%), e **o problema é a
soma de recursos sem quota**. A evidência do OOM global de 12/08 — alocador
`postgres`, vítima `chrome` do OneSid — sustenta isso bem.

### Onde eu discordo

O documento coloca como P0 do Flow *"reduzir Uvicorn de 4 para 2"*, esperando
diminuir a base de 1,2–1,6 GiB. **Medi e essa é a alavanca menor.**

Os 4 workers somam ~900 MB (283 + 209 + 206 + 205), e boa parte é **compartilhada
por copy-on-write do fork** — o custo incremental de cada worker extra é bem
menor do que o número sugere. O container inteiro estava em **0,95 GB**, não nos
1,2–1,6 GiB observados.

Cortar 4→2 economizaria talvez 200–300 MB reais num host de 16 GB. Enquanto isso,
**o mesmo corte reduziria pela metade um trabalho de fundo que estava 4× maior do
que deveria** — e essa era a economia que interessava, porque o gargalo é CPU.

Por isso ataquei o scheduler em vez do número de workers: **elimina ~63% das
execuções em vez de 50%, e sem tirar capacidade web nenhuma.** E, diferente do
corte de workers, resolve junto a corrida dos 12 jobs que não tinham lock —
esse é o ganho que eu defenderia mesmo se a CPU não mudasse nada.

### Sobre reduzir workers agora

Não recomendo, ainda. Endpoints pesados nossos são síncronos e seguram um worker
inteiro — import do intake, exports, geração de planilha. Com apenas 2 workers,
**duas requisições pesadas simultâneas travam a web toda**. Se for necessário,
prefiro 4→3 e medir depois da mudança do scheduler.

### Sobre `mem_limit`

Foi recusado em 12/08, com razão na época: limite mal dimensionado mataria um
Chromium da coleta ou um worker no meio do trabalho.

O cenário mudou. Com **OOM global comprovado**, sem limite nosso o kernel pode
escolher *nosso* Uvicorn ou *nosso* Postgres como vítima da próxima vez. Com uso
real medido em 0,95 GB, um teto de **4 GB é 4× de folga** — não mata nada em
operação normal, e transforma *"somos vítima aleatória do host"* em *"temos
domínio de falha próprio"*.

Fica como recomendação, agora com número medido em vez de estimativa.

### O upgrade é inevitável

O que fizemos hoje **tira o Flow da lista de ofensores**, mas não muda a
aritmética: 4 vCPU para 57 containers, três famílias de RPA com Chromium, quatro
Postgres sem orçamento e uma instância *burstable* cujo baseline (1,6 vCPU) já é
menor que a média observada (1,74 vCPU).

Entre as duas opções do documento, **prefiro a Opção B — separar as automações**.
O raciocínio é o do próprio documento, e é bom: *um portal externo lento ou um
captcha não deveriam congelar banco, painel e deploys internos*. Hoje eles
congelam.

A Opção A (host único maior) resolve no curto prazo e é mais simples de executar,
mas mantém o acoplamento: continua sendo uma máquina onde qualquer RPA travado
degrada todo mundo. Se a escolha for A por praticidade, tudo bem — mas vale saber
que é alívio, não separação.

### Ordem que eu sugeriria

1. **Aplicar as quotas P0** nos outros projetos (OneSid é o mais urgente — dois
   RPAs com Chrome e **sem limite de CPU**)
2. **Redeploy do Flow** com o que subiu hoje
3. **Medir 3 a 5 dias** com todo mundo sob quota — é aí que se descobre a
   necessidade real, sem o ruído de quem estava sem teto
4. **Decidir o upgrade** com esse número na mão

O passo 3 importa: dimensionar a instância agora, com metade dos projetos sem
quota, é dimensionar para o caos e não para a carga real.

---

## Pendências

- [ ] Redeploy para ativar as três mudanças
- [ ] Conferir no log: **um** `Worker LÍDER` e três `Worker secundário`
- [ ] Confirmar a primeira execução da retenção (04:20 UTC)
- [ ] Coleta real quando o OneLog publicar a versão nova do contrato
- [ ] Decidir sobre `mem_limit` de 4 GB
- [ ] Decidir entre Opção A e Opção B de capacidade
