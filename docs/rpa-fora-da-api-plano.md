# RPA fora da API — plano (Camada 4)

**Estado em 08/09/2026, 23h:** decidido, NÃO executado. O que dava pra fazer
sem redeploy foi feito (teto de PIDs na unidade certa). A separação de
container é mudança de arquitetura e precisa de um dia útil com gente olhando.

## Por que

Em 08/09/2026 um robô (runner do Tratamento Web pendurado havia 3 dias)
esgotou os 300 PIDs do container da **API** — o mesmo processo que serve as
operadoras. A API parou de criar thread; o sintoma apareceu como "publicações
lentas, erros diversos", longe da causa. À noite a coleta do BB repetiu a
dose (220 threads de Chrome órfãos). O watchdog de PIDs agora mata o robô
pendurado, mas a lição estrutural é outra: **robô e API não podem dividir o
mesmo pool de processos.** Enquanto dividirem, todo vazamento de RPA é um
incidente da tela.

Hoje o container `api` hospeda, no mesmo cgroup:

| O que | Como nasce | Tem grupo + PID + teto? |
|---|---|---|
| uvicorn (4 workers) + 25 jobs do APScheduler no líder | processo principal | — |
| Tratamento Web (`treat-publications.js` + ~6 Chrome) | `Popen` | grupo ✔ PID ✔ teto só via reaper |
| Coleta BB (Playwright + OneLog + undetected-chrome) | **desde hoje**: processo filho supervisionado | grupo ✔ PID (evento) ✔ teto 60 min ✔ |
| cancel-legacy-task (login + cancelamento) | `Popen` | grupo ✔ teto 180 s ✔ |
| varredura-andamentos, generate-report, position-fix… | `Popen` | parcial |

## O alvo

Um container **`rpa-runner`** (mesma imagem, `target` próprio no Dockerfile,
`init: true`, `pids_limit` próprio, `shm_size: 1gb`) que roda **só robôs**.
A API deixa de executar Chrome; ela **enfileira** e o `rpa-runner` **consome**.
O molde já existe na casa: `ajus-runner` (`scripts/ajus_runner_worker.py`) —
loop standalone, sem FastAPI, lê fila no Postgres, roda o runner, grava o
resultado, encerra em SIGTERM.

```
api ──(grava pedido em tabela de fila)──▶ Postgres ◀──(reivindica, roda, grava)── rpa-runner
                                                              │
                                                              └── Chrome/node/uc só aqui
```

Efeito: robô pendurado consome PID **do rpa-runner**. A API nunca mais fica
sem thread por causa de Chrome. O watchdog de PIDs e o Vigia de Filas
continuam iguais — só mudam de container.

## Ordem de migração (um robô por vez, o mais barato primeiro)

1. **Coleta BB** — já é processo filho com comando explícito
   (`python -m app.services.distribuidos_bb.coleta_runner --run-id N`). Passo:
   o tick do agendador grava um pedido (`bbd_runs` já é a fila — status
   `EM_ANDAMENTO` + `iniciado_em` bastam) e o `rpa-runner` reivindica com
   `pg_try_advisory_lock` e chama o mesmo módulo. O supervisor
   (`coleta_supervisor.py`) vai junto, sem mudança.
2. **Tratamento Web** — `start_run` já monta `command`; vira pedido em
   `publicacao_tratamento_execucoes` (status `INICIANDO` é o "pendente de
   reivindicação"). O reaper e o `_encerrar_runner` seguem funcionando porque
   o PID gravado passa a ser do rpa-runner — que é onde o kill precisa
   acontecer.
3. **cancel-legacy-task** — já tem fila própria
   (`prazo_inicial_legacy_task_cancel_items`); só muda quem consome.
4. Os demais runners Node conforme aparecerem na fila.

## O que precisa existir antes de começar

- **Volume compartilhado** para `output/playwright/**` (status.json, logs,
  cookies): hoje a API lê o `status.json` que o runner escreve. Mesmo volume
  nos dois serviços no compose, ou o runner passa a gravar progresso no banco
  (melhor, mas é passo 2).
- **Credenciais/env** iguais nos dois containers (OneLog, L1 web, SMTP).
- **Healthcheck** do `rpa-runner` que não dependa de fork (ler `/proc`, como o
  watchdog) — o healthcheck da API morreu de `Cannot fork` no incidente.
- **Teto global em cada runner Node** (`treat-publications.js` etc.): hoje
  nenhum tem handler de SIGTERM nem orçamento de relógio próprio; o Python
  mata por fora. Vale adicionar `process.on('SIGTERM', fecharBrowserAtivo)`
  e um `setTimeout(..., TEMPO_MAXIMO).unref()` que encerra com código ≠ 0.

## Riscos

- Latência de reivindicação (poll de N segundos) — irrelevante para robôs
  que levam minutos.
- Dois consumidores rodando o mesmo pedido — `pg_try_advisory_lock` por
  pedido, igual às automações (namespace 4242) e ao motor sem pasta (4243).
- Deploy: a API e o `rpa-runner` sobem juntos no Coolify (mesmo compose); um
  redeploy no meio de uma coleta mata o filho — o reaper/`recuperar_pool_orfao`
  já cobrem, e o supervisor grava o PID no evento.

## O que NÃO fazer

- Subir `pids_limit` mais uma vez em vez de separar: só empurra a parede.
- Migrar tudo de uma vez: um robô por deploy, com o Vigia de Filas olhando.
