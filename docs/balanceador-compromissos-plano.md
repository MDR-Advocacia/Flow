# Balanceador — incluir Compromissos (`/Appointments`), não só Tarefas

**Status:** ✅ IMPLEMENTADO (2026-07-13). Leitura live + escrita + frontend prontos e
validados em prod. Investigação read-only + validação de escrita (no-op) feitas em prod.

## Resultado da implementação (2026-07-13)

- **§1 Leitura live** — `live_pessoa` agora pagina `/Tasks` E `/Appointments`
  (helper `_carregar_l1_pool`, teto 180 POR endpoint), mescla com tag
  `origem: "tarefa"|"compromisso"` em cada tarefa. Validado: Jose Alberto (cid 38)
  passou de "0 tarefas / Sem carga" pra **4 compromissos** executante-pendente.
- **§2 Snapshot** — **não precisou mudar.** O export "Agenda Analytics" que alimenta
  `perf_l1_tarefa` JÁ inclui compromissos (confirmado: IDs 367939/382308 estão lá com
  subtipo). O diagnóstico/matriz já contavam certo; só o modal LIVE divergia.
- **§3 Escrita** — `_reatribuir_uma` roteia GET/PATCH por `origem`
  (`/Appointments/{id}` p/ compromisso). O PATCH participants em `/Appointments`
  **dá 400 de validação** (o modelo revalida `startDateTime` — igual à trava de tarefa
  atrasada) → cai no **caminho web (fase 2)**, que trata compromisso e tarefa juntos.
  Validado no-op DE=PARA num compromisso real (post_reassign → `Success: true`,
  "Alteração de executante(s) de **compromisso(s) e tarefa(s)** iniciada").
- **§4 Frontend** — `origem` threadado em `resolverItens` (mapa id→origem); selinho
  roxo "Compromisso" no `DetalheSubtipoModal`.

---

## Plano original (mantido pra referência)

## Problema

No Legal One a UI mostra "Compromissos e tarefas" juntos, mas a API tem **duas
entidades separadas**:

- `/Tasks` → **Tarefas**
- `/Appointments` → **Compromissos** (audiências, prazos internos, peticionamentos etc.)

O balanceador (`live_pessoa` e cia.) consulta **só `/Tasks`** → **todo compromisso é
invisível**. Resultado: a carga de execução de qualquer pessoa é subcontada pelo
número de compromissos que ela tem.

### Evidência (prod, Jose Alberto Veloso de Carvalho, contact 38)

| papel · status 0 | `/Tasks` | `/Appointments` |
|---|---|---|
| **Executante (pendente)** | **0** | **4** |
| Responsável (pendente) | 364 | 36 |

O balanceador mostrava "0 tarefas / Sem carga" pra ele; o L1 mostrava 4 pendentes —
que são compromissos onde ele é executante (ex.: "Peticionar Habilitação",
"Contestação PRAZO INTERNO 28/07"). O operador apontou exatamente essa diferença
Tarefa × Compromisso.

## Mapa de campos — `/Tasks` vs `/Appointments`

Quase idênticos. O filtro atual do balanceador funciona igual nos dois.

| campo | `/Tasks` | `/Appointments` | nota |
|---|---|---|---|
| `participants[].{contact,isExecuter,isResponsible,isRequester}` | ✅ | ✅ | idêntico (mesma grafia `isExecuter`) |
| `statusId` | ✅ | ✅ | 0=Pendente igual |
| `endDateTime` | ✅ | ✅ | **prazo usado pelo balanceador** — existe nos dois |
| `startDateTime` | ✅ | ✅ | |
| `subTypeId` / `typeId` | ✅ | ✅ | catálogos podem ter namespaces diferentes (ver §aberto) |
| `deadLine` | ✅ | ❌ **não existe** | foi o que deu 400 no `$select`; ignorar em Appointments |
| `description` / `notes` | ✅ | ✅ | |
| `isAllDay` / `isPrivate` | ❌ | ✅ | exclusivos de compromisso; não usamos |

Filtro que vale nos dois:
`participants/any(pp: pp/contact/id eq {cid} and pp/isExecuter eq true) and statusId eq 0 and endDateTime ne null`, `$orderby=endDateTime`.

## Escopo da mudança

### 1. Leitura ao vivo — `live_pessoa` (`app/services/performance/balanceador.py`)
- Extrair o loop de paginação (`/Tasks`, `$top=30`, teto de páginas, `$count`) numa
  função reusável e chamá-la **duas vezes**: `/Tasks` e `/Appointments`.
- Mesclar os dois `raw` numa lista só antes de agregar por subtipo.
- **Marcar a origem** de cada item: `origem: "tarefa" | "compromisso"` (novo campo no
  dict de `tarefas`) — a UI mostra um selinho e a reatribuição sabe qual endpoint bater.
- `MAX_TAREFAS`/teto: hoje é 180 no `/Tasks`; decidir se o teto é por-endpoint ou
  do conjunto (provável: por-endpoint, somando os dois).
- Nome do subtipo: hoje resolve via `LegalOneTaskSubType.external_id`. **Validar** se
  os `subTypeId` de compromisso caem no mesmo catálogo; se não, fallback pelo snapshot
  (já existe fallback l1_task_id→subtipo) ou catálogo próprio.

### 2. Snapshot / diagnóstico (`perf_l1_tarefa`, ingest do "Agenda Analytics")
- Checar se o export **Agenda Analytics** que alimenta o snapshot **já inclui
  compromissos** (o diagnóstico/matriz leem do snapshot, não do L1 ao vivo).
  - Se **inclui**: diagnóstico já está certo; só o modal live diverge → resolver §1.
  - Se **não inclui**: diagnóstico também subconta → precisa incluir compromissos no
    ingest (ou puxar ao vivo, o que é caro). Ver `reference_ingest_minha_equipe`.
- Provável necessidade de uma coluna `origem` em `perf_l1_tarefa` (migration `perfNNN`)
  se o snapshot passar a misturar os dois — decidir na hora conforme o export.

### 3. Escrita — reatribuição (`app/services/performance/reatribuir_job.py` + client)
- O item resolvido do modal já carrega `l1_task_id`; passar junto a **origem**.
- No `_reatribuir_uma`: se `origem == "compromisso"`, bater `GET/PATCH` em
  `/Appointments/{id}` em vez de `/Tasks/{id}` (métodos análogos no client).
- **Validar (no-op DE=PARA em prod, como fizemos nas tasks)**: o PATCH de
  `participants` funciona em `/Appointments`? Se algum tipo travar (lock de
  Workflow/procedimento), cai no mesmo **caminho web** (fase 2) — checar se o
  `ModalEnvolvimentoEmLote` cobre compromisso ou se o CampoId/endpoint muda.
- Buckets e confirmação assíncrona (GET participants) idênticos.

### 4. Frontend (`RedistribuicaoModal.tsx`)
- Selinho "compromisso" no card/subtipo (distinguir de tarefa) — puramente informativo.
- `resolverItens` já lida com task_id; incluir `origem` no item enviado ao backend.

## Validação (antes de marcar pronto)
1. `live_pessoa` do Jose Alberto (cid 38) passa a mostrar os **4 compromissos**
   executante pendentes + as tarefas (0). Soma bate com o L1.
2. Reconferir 2-3 pessoas operacionais: total live = tarefas + compromissos, sem
   dupla contagem.
3. No-op DE=PARA em 1 compromisso real → confirmar PATCH participants OK (ou lock →
   caminho web).
4. Diagnóstico/snapshot: bater soma com o novo live.

## Pontos abertos (resolver na implementação)
- **Catálogo de subtipo** de compromisso: mesmo `LegalOneTaskSubType` ou namespace
  próprio? (typeId observado: 33; subTypeId: 1269, 903 etc.)
- **Snapshot inclui compromisso?** — depende do que o export Agenda Analytics traz.
- **PATCH participants em `/Appointments`** funciona igual? (validar com no-op).
- **Teto de páginas** somando os dois endpoints (rate limit ~1,2 req/s → dobra as
  chamadas por pessoa no modal).

## Referências
- Investigação: sessão 2026-07-13 (contact 38, /Appointments 36 pendentes, 4 executante).
- Filtro/semântica de executante: fix de 2026-07-07 (commit afee684).
- Reatribuição API+web: `docs/legalone-reatribuir-responsavel-executante-tarefa.md`.
