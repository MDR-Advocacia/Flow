# Trocar o Responsável Principal de uma **Pasta de Processo** (Legal One)

> **Objetivo:** documentar o método validado para trocar o responsável
> principal de pastas de processo no Legal One — em lote ou individualmente —
> como base para a redistribuição de carteiras (Equipe Mista, remanejamento
> entre equipes, saída/entrada de colaborador).
>
> Tudo aqui foi **validado empiricamente em produção** em 2026-07-20 contra o
> tenant da MDR, na pasta-cobaia **46543** (`0001644-39.2025.4.05.8402`,
> escritório 23 — Banco do Brasil / Réu), com 4 escritas reais e rollback
> completo ao estado original.
>
> **Documento irmão:** `legalone-reatribuir-responsavel-executante-tarefa.md`
> cobre o mesmo problema para **tarefas**. Pasta e tarefa são mecanismos
> diferentes — não confundir.

---

## 1. TL;DR

| Cenário | Via API REST? | Como |
|---|---|---|
| Trocar responsável principal de **pasta de processo** | ⛔ **NÃO** | REST recusa por regra de negócio (ver §2) |
| Trocar responsável principal de **pasta de processo** | ✅ **SIM** | `POST /processos/processos/ModalChangeInvolvedInBatch` (endpoint web) |

Mesma dicotomia do cancelamento e da reatribuição de tarefas: o que a API REST
tranca, o endpoint web do Legal One libera. E **reusa exatamente a mesma
sessão** (`.ASPXAUTH`) que o projeto já mantém.

---

## 2. Por que a API REST não serve

O responsável principal da pasta aparece nos participantes como
`type: "PersonInCharge"` com `isMainParticipant: true`:

```json
{
  "type": "PersonInCharge",
  "id": 192326,              // id do participante
  "contactId": 1820,         // id do usuário — é ISSO que precisa mudar
  "contactName": "Cinthia Samylle Martins Souza da Silva",
  "positionId": 6,
  "isMainParticipant": true
}
```

Tentar mudar o `contactId` por REST:

```
PATCH /Lawsuits/46543/Participants/192326
{"contactId": 1808}
```

Retorna erro de regra de negócio (sem efeito colateral — a pasta não muda):

> **"Não será possível alterar o envolvido, pois ele é responsável principal no
> processo. Seus dados poderão ser alterados apenas na alteração da pasta de
> processo."**

Ou seja: o L1 exige que a troca passe pelo fluxo de "alteração da pasta" — que
é justamente o que o endpoint web abaixo faz.

---

## 3. O método que funciona — `ModalChangeInvolvedInBatch`

### Endpoint

```
POST https://mdradvocacia.novajus.com.br/processos/processos/ModalChangeInvolvedInBatch
Content-Type: application/json; charset=UTF-8
X-Requested-With: XMLHttpRequest
Cookie: .ASPXAUTH=<sessão web>
```

**Sem antiforgery token.** A autenticação é 100% pelo cookie `.ASPXAUTH` —
igual ao cancelamento de tarefas.

### Payload mínimo validado

```json
{
  "InvolvementStatusId": "",
  "InvolvementMainStatusId": "1",
  "InvolvedPositionId": "",
  "InvolvedPositionText": "",
  "FromInvolvedId": "",
  "FromInvolvedText": "",
  "FromUserId": "",
  "FromUserText": "",
  "ToInvolvedId": "",
  "ToInvolvedText": "",
  "ToUserId": "57818",
  "ToUserText": "Álvaro José da silva Aguiar",
  "RowsPerPage": "18",
  "TypeOfInvolvement": "0",
  "selectionViewModel": {
    "SelectAll": false,
    "SelectFirsts": false,
    "UseStringIds": false,
    "SelectedIds": ["46543"],
    "UnselectedIds": [],
    "SearchModelSerialized": "{}"
  }
}
```

### Campos que importam

| Campo | Valor | Significado |
|---|---|---|
| `InvolvementMainStatusId` | `"1"` | Alvo é o **envolvido principal** (o responsável) |
| `TypeOfInvolvement` | `"0"` | Envolvido é **usuário** (não contato externo) |
| `ToUserId` / `ToUserText` | id + nome | **PARA quem vai** — id é o `external_id` do `LegalOneUser` |
| `FromUserId` / `FromUserText` | `""` | **DE quem sai.** Vazio = troca quem estiver lá (ver §6, risco) |
| `selectionViewModel.SelectedIds` | `["46543"]` | Lista de **lawsuit ids**. Aceita N ids num POST só |
| `SearchModelSerialized` | `"{}"` | ⚠️ ver §4 |

### Resposta

```json
{"Success":true,"Message":"Alteração de envolvimento(s) na(s) pasta(s) de processo(s), recurso(s) ou incidente(s) iniciada."}
```

---

## 4. A pegadinha do `SearchModelSerialized`

Capturado do navegador, esse campo é um **blob de ~12KB** com o estado inteiro
do formulário de busca da tela de processos. Testamos as três hipóteses:

| Valor enviado | Resultado |
|---|---|
| `""` (string vazia) | ⛔ **HTTP 500** — `{"Error":true,"ErrorMessage":"Ocorreu um erro inesperado no servidor"}` |
| blob completo de 12.430 bytes | ✅ funciona |
| `"{}"` (2 bytes) | ✅ **funciona igual** |

**Conclusão:** o servidor só exige que o campo seja um **JSON deserializável**.
O conteúdo é irrelevante quando `SelectedIds` está populado.

> Isso é o que torna o método viável programaticamente: **não é preciso
> capturar/replicar a sessão de busca do navegador**. Hardcode `"{}"`.

---

## 5. Comportamento observado

- **Assíncrono no papel, síncrono na prática.** A resposta diz "iniciada", mas
  nas 4 execuções a troca já estava refletida na API REST em **menos de 5s**.
  Ainda assim: **`Success:true` não é confirmação** — confirme relendo
  `GET /Lawsuits/{id}/Participants` e checando o `contactId` do
  `type: "PersonInCharge"`.
- **Não cria participante novo.** O `id` do participante (192326) permaneceu o
  mesmo em todas as trocas; só o `contactId` mudou. Não polui o histórico de
  envolvidos.
- **`positionId` e `isMainParticipant` preservados** (6 / `true`).
- **Idempotente na direção.** Trocar A→B e depois B→A devolve exatamente o
  estado original.

### Log das execuções de validação (pasta 46543)

| # | Direção | `SearchModelSerialized` | HTTP | Refletiu? |
|---|---|---|---|---|
| 1 | Cinthia (1820) → Álvaro (57818) | blob 12KB (manual, via navegador) | 200 | ✅ |
| 2 | Álvaro → Cinthia | `""` | **500** | — |
| 3 | Álvaro → Cinthia | blob 12KB | 200 | ✅ em ~5s |
| 4 | Cinthia → Álvaro | `"{}"` | 200 | ✅ em ~4s |
| 5 | Álvaro → Cinthia (rollback) | `"{}"` | 200 | ✅ |

Estado final = estado inicial (Cinthia, participante 192326).

---

## 6. ⚠️ Riscos e travas obrigatórias antes de usar em lote

### 6.1 `FromUserId` vazio sobrescreve cegamente

Com `FromUserId: ""`, o L1 troca **quem quer que esteja** como responsável, sem
validar. Numa carga em lote a partir de um estoque montado horas antes, isso
pode **sobrescrever uma atribuição que um supervisor fez na mão** nesse
intervalo — silenciosamente.

**Duas mitigações (usar pelo menos uma):**

1. **Preencher `FromUserId`/`FromUserText`** com o responsável esperado. O L1
   só aplica onde bater. *(Não validado ainda — testar antes de confiar.)*
2. **Reler `Participants` imediatamente antes de cada POST** e abortar a pasta
   se o responsável atual divergir do esperado. Mais lento, mas determinístico.

### 6.2 Confirmação é releitura, nunca o `Success`

Já vale como regra da casa nos outros endpoints web (cancelamento, reatribuição
de tarefa) — vale aqui também.

### 6.3 Transferir pasta ≠ transferir tarefa

Trocar o responsável da **pasta** não mexe nas **tarefas** já abertas nela.
Uma redistribuição completa de carteira precisa dos **dois** movimentos:

| O quê | Endpoint | Doc |
|---|---|---|
| Pasta | `ModalChangeInvolvedInBatch` | este documento |
| Tarefa | `ModalEnvolvimentoEmLote` | `legalone-reatribuir-responsavel-executante-tarefa.md` |

### 6.4 Lote grande

`SelectedIds` aceita N ids, mas o processamento é assíncrono do lado do L1.
Não validamos o teto. Recomendação: **lotes pequenos (≤50) com verificação
entre eles**, ao invés de um POST gigante cujo resultado parcial é opaco.

---

## 7. Como chamar dentro do projeto

A sessão web já existe e é compartilhada — **não criar outra**:

```python
from app.services.prazos_iniciais.legacy_task_http_cancellation_service import (
    LegacyTaskHttpCancellationService,
)

svc = LegacyTaskHttpCancellationService()
cookies = svc._ensure_session()   # login Playwright + cache + lock entre workers
url = f"{svc._web_base_url()}/processos/processos/ModalChangeInvolvedInBatch"
resp = svc._http.post(url, data=json.dumps(payload).encode("utf-8"),
                      cookies=cookies, timeout=60,
                      headers={"X-Requested-With": "XMLHttpRequest",
                               "Accept": "*/*",
                               "Content-Type": "application/json; charset=UTF-8"})
```

Esse serviço já resolve: login headless via Playwright (`--login-only`, fluxo
OnePass), cache do cookie em `/app/data/legacy_task_http_session.json`, lock
`.lock` pra serializar o login entre os 4 workers do uvicorn, e retry com
re-login em 403.

---

## 8. Como descobrir o `ToUserId`

É o **`external_id`** da tabela `legal_one_users` — não o `id` interno do Flow:

```sql
select id, external_id, name from legal_one_users where name ilike '%Ingrid%';
-- id=100  external_id=1802  Ingrid Quirino Ribeiro
```

`external_id` é o que vai no `ToUserId`. O `ToUserText` é o nome exibido; nas
execuções ele veio com acentuação normal e não deu problema.

---

## 9. Pendências

- [ ] Validar se `FromUserId` preenchido de fato filtra (§6.1, mitigação 1).
- [ ] Descobrir o teto prático de `SelectedIds` num POST.
- [ ] Verificar se a troca dispara algum workflow/notificação no L1 (nas 4
      execuções não observamos efeito, mas não auditamos as tarefas da pasta).
- [ ] Confirmar comportamento em pastas de **recurso/incidente** — a mensagem
      de retorno os menciona, mas só testamos pasta de processo.
