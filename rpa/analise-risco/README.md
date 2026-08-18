# RPA de Análise de Risco BB Réu (servidor AWS)

Consome a fila do Flow (`/api/v1/analise-risco/intake/fila`), consulta a
pendência de análise de risco de cada NPJ no portal do BB (sessão OneLog) e
devolve os vereditos (`/intake/resultados`). Pendência aberta depois da tarefa
cumprida no L1 = análise NÃO feita = **divergente** no painel do supervisor.

Decisão de arquitetura: o acesso ao portal do BB roda AQUI (servidor AWS da
casa), não no Coolify — o Flow só guarda a fila e o resultado.

## Deploy — Coolify do servidor de testes

O RPA roda como app Docker no Coolify do servidor de testes, deployado deste
mesmo repositório:

1. **New Resource → Application → GitHub** apontando pro repo
   `MDR-Advocacia/Flow`, branch `main`.
2. **Build Pack:** Dockerfile · **Base Directory:** `/rpa/analise-risco`
   (o Dockerfile está aqui dentro).
3. **Sem domínio/porta** — é um worker de loop contínuo, não expõe HTTP.
   (Se o Coolify exigir healthcheck, desabilitar.)
4. Variáveis de ambiente no painel:

```
FLOW_API_URL=https://flow.dunatecnologia.com
ANALISE_RISCO_INTAKE_API_KEY=<mesma chave setada no Coolify do Flow>
ONELOG_API_URL=<URL do OneLog que roda no servidor de testes>
ONELOG_USERNAME=<usuario>
ONELOG_PASSWORD=<senha>
# Opcionais:
# INTERVALO_SEGUNDOS=600
# LOTE=50
# HTTPS_PROXY=<proxy BR, se o IP da AWS levar 403 do portal>
```

5. (Recomendado) **Watch Paths:** `rpa/analise-risco/**` — assim o RPA só
   rebuilda quando ESTA pasta mudar, e não a cada push do Flow.
6. Deploy. O log do container mostra cada ciclo ("Fila do Flow: N itens…").

Alternativa fora do Coolify (systemd/cron): rodar
`python3 rpa_analise_risco.py` em loop (só precisa de `pip install requests`),
ou `--once` num cron `*/10`.

## Lado do Flow (Coolify)

- Setar `ANALISE_RISCO_INTAKE_API_KEY` (a mesma do RPA; CSV pra rotação).
- `ANALISE_RISCO_VERIFICACAO_ATIVA` fica **False** (default) — é o worker
  interno de fallback via OneLog do Coolify; ligar só se o RPA externo sair
  de cena, senão os dois disputam a mesma fila.

## Contrato

- `GET /api/v1/analise-risco/intake/fila?limit=N` (header `X-AnaliseRisco-Api-Key`)
  → `{total, itens: [{id, l1_task_id, npj, cnj, verif_tentativas}]}`
- `POST /api/v1/analise-risco/intake/resultados`
  → `{"resultados": [{"id": 1, "pendencia_aberta": false, "estado": null, "exito": null}
                     | {"id": 2, "erro": "timeout"}]}`
  Item com `erro` continua na fila e re-tenta no próximo ciclo.

Endpoints do portal decodificados do HAR de 2026-08-18 (navegação real até a
seção): `POST {paj}/resources/app/v1/processo/analise/risco/pendencia/consultar`
com o NPJ sem máscara no corpo; CNJ→NPJ via
`GET {paj}/resources/app/portal/cadastro/processo/pesquisa-avancada/numero-processo/{cnj}`.
