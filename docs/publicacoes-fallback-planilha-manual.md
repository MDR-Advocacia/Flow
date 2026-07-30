# Publicações — 3º fallback: upload manual da planilha do L1

**Status:** IMPLANTADO em 30/07/2026. Sem migration — não precisou de coluna
nova no banco.

## Por que existe

A captura de publicações tem três camadas, da mais automática para a mais
manual:

1. **Legal One `/Updates`** — fonte primária.
2. **DJEN/Comunica** — contingência automática (trabalho do Codex, parado em
   `feat/djen-fallback-codex`, ainda **não** na main). Depende de proxy
   brasileiro: `DJEN_PROXY=socks5h://206.42.43.192:45123`, o mesmo do Lake.
3. **Planilha do L1 (este documento)** — em duas formas:
   - **automática** (`publication_l1_report_fallback.py`): quando a busca
     agendada falha, o Flow manda o próprio L1 gerar o relatório e importa
     sozinho. É o caminho normal — o operador não faz nada;
   - **manual**: o operador exporta na tela do Legal One e sobe o arquivo.
     Continua existindo para quando nem isso funcionar.

## A contingência automática

Acionada no ponto exato em que a rodada agendada dava tudo por perdido
(`scheduled_automation_service._execute_pull_publications`, no `except` do fetch
batch). Antes: todos os escritórios viravam falha. Agora: tenta o relatório e,
se vier, o fan-out segue normal — cada escritório processa o subset dele, como
se a API tivesse respondido.

Fluxo, levantado do HAR de uma geração real:

```
GET  /processos/GenericReport/?id=789        abre o modelo salvo
POST /processos/GenericReport                dispara (302) e cria o relatório
POST /shared/ReportShared/DocumentIsLoaded   polling: 7=buscando, 8=gerando, 1=PRONTO
GET  /shared/ReportShared/GetFile/{id}       302 → blob assinado → .xlsx
```

Janela sempre **D-1 → D0**, por data de **cadastro do andamento** — mesma
semântica do `/Updates`: pega o que ENTROU no L1 na janela, não o que foi
publicado. Por isso uma janela de 2 dias traz publicação de mais de um mês
atrás, e é assim que tem que ser: publicação que o tribunal solta com atraso
não pode se perder.

**Por que o corpo do POST é um arquivo versionado** (`data/publicacoes_report_form.txt`)
e não montado lendo a página: dos 917 campos, **144 não existem no HTML** — são
injetados por JavaScript no submit, e entre eles estão os filtros e as 120
entradas de `Columns[...]`, **inclusive a coluna `Id`**. Remontar isso na mão
quebraria em produção, de madrugada, provavelmente em silêncio — gerando um
relatório sem a coluna que identifica o processo.

O guarda contra isso é o próprio importador: relatório sem `Id` é recusado com
motivo e a contingência devolve `ok=False`. Melhor não capturar do que capturar
na pasta errada.

Chave de desligamento: `PUBLICATION_REPORT_FALLBACK_ENABLED` (default `true`).

**Teste que simula queda do L1 PRECISA mockar `capturar_publicacoes`** — senão a
suíte vai no site do Legal One e gera relatório de verdade (foi assim que saiu o
relatório #13435 em 30/07/2026). O helper `_patch_contingencia` em
`tests/services/test_scheduled_pull_publications_batch.py` existe pra isso.

Validado contra o L1 real em 30/07/2026: relatório #13432 gerado em **16
segundos**, arquivo idêntico ao que o operador extraiu à mão — 1.238 linhas,
1.238 válidas, 0 ignoradas, todos os escritórios reconhecidos.

A camada 3 é a única que **não depende de rede nenhuma** — nem da API do L1,
nem do CNJ, nem de proxy. É o caminho que continua funcionando com tudo fora
do ar, que é exatamente o cenário da madrugada de 30/07/2026, quando as 13
buscas do dia falharam com HTTP 502 e ninguém foi avisado.

## Como o operador usa

Publicações → botão **"Importar planilha"**, ao lado de "Buscar".

1. Escolhe o arquivo `.xlsx` exportado do Legal One.
2. O sistema lê e mostra a prévia: quantas publicações, quantos processos, o
   período, a distribuição por escritório e as linhas ignoradas com o motivo.
   **Nessa etapa nada é gravado.**
3. Confirmando, a importação roda em background e aparece no histórico de
   buscas como qualquer outra.

## A coluna `Id` é obrigatória

É o identificador do **processo** no Legal One (`lawsuit_id`), e é o que faz
esse método ser seguro. Confirmado contra o banco de produção em 30/07/2026:

- relação `Id` ↔ `Pasta` é 1:1 (1.238 para 1.238, sem cruzamento);
- dos 1.237 IDs de uma extração real, 1.013 existiam no `lawsuit_cache` e em
  **1.013 de 1.013** o CNJ do cache era idêntico ao da planilha;
- a API do L1 resolveu os 1.238 IDs como processos reais.

Sem essa coluna seria preciso adivinhar o processo pelo CNJ — e **1.370 CNJs
da base têm mais de um `lawsuit_id`** (apensos `/001` e pastas duplicadas).
Errar aí significa criar tarefa no processo errado. Por isso a importação é
recusada, com instrução na tela, quando a coluna não vem.

## Onde encaixa no fluxo (a decisão central)

A planilha **não grava** em `publicacao_registros`. Ela é convertida para o
mesmo contrato de dicionário que a API do L1 devolve e entregue a
`create_and_run_search(prefetched_publications=...)` — o gancho que o scheduler
já usava no modo batch.

Consequência: tudo que já existe passa a valer de graça, sem lógica duplicada e
sem risco de divergir do fluxo automático:

- **Dedup em duas camadas** — por `legal_one_update_id` e por
  `(lawsuit_id, publication_date)`. Publicação já capturada pelo L1 **não
  duplica**.
- **Detecção de obsoleta** — publicação anterior à criação da pasta
  (`Data do cadastro`). No teste com a planilha real, 36 das 1.238 caíram aqui
  sozinhas.
- **A "enxugada" antes da classificação** — uma publicação por processo/dia vai
  ao agente; as demais herdam. Sem isso o custo de IA multiplica.
- **Enriquecimento pelo L1** — se a API estiver de pé, sobrescreve escritório e
  CNJ com o dado oficial; se estiver fora, falha *soft* e os valores da planilha
  permanecem. Funciona nos dois cenários.

## Idempotência (subir o mesmo arquivo duas vezes)

`legal_one_update_id` é NOT NULL e UNIQUE, e a planilha não traz o ID do
andamento — só o do processo. O ID é sintetizado: **negativo** (ID real do L1 é
positivo; o menor em produção é 1.356.393) e **determinístico** (hash de
processo + data + texto).

Subir a mesma planilha de novo gera os mesmos IDs e a dedup descarta sozinha —
validado: 1.238 registros, reimport criou **0**.

Se o ID sintético colidir com uma publicação **diferente**, ele anda até achar
espaço livre. Sem isso a linha seria tratada como duplicata exata e sumiria em
silêncio — o oposto do que esse fallback existe pra fazer.

## Mapa das colunas

Casadas por **nome normalizado** (sem acento, minúsculo), nunca por posição — o
operador monta a extração na tela do L1 e a ordem muda.

| Coluna | Vira |
|---|---|
| `Id` | **`linked_lawsuit_id`** (obrigatória) |
| `Nº do processo` | CNJ (obrigatória) |
| `Andamentos / Data/hora` | **`publication_date`** (obrigatória) |
| `Andamentos / Descrição` | texto da publicação (obrigatória) |
| `Escritório responsável` | `office_external_id`, casado pelo `path` completo |
| `Data do cadastro` | `_lawsuit_creation_date` — alimenta a detecção de obsoleta |
| `Pasta`, `Responsável principal` | exibição/rastreabilidade |
| `Andamentos / Tipo` | filtro: só `Publicação` |

As 5 colunas do fim (`SUBTIPO`, `EXECUTANTE`, `PRAZO`, `DATA DA TAREFA`,
`HORÁRIO`) vêm vazias e seguem o formato do agendamento de *Tarefas por
Planilha*. Não são usadas no ingest.

## Validação feita antes de subir

Com a planilha real de 1.238 linhas (`PUBLICAÇÕES - FLOW (2).xlsx`):

- 1.238/1.238 válidas, 0 descartes, 12/12 escritórios resolvidos pelo `path`;
- pelo endpoint HTTP: prévia não grava nada; import gravou 1.238 (1.202 novas +
  36 obsoletas), todas com processo vinculado; reimport criou 0;
- recusa com motivo: arquivo que não é planilha, planilha sem `Id`, arquivo
  vazio, arquivo acima de 25MB;
- 12 testes em `tests/services/test_publication_spreadsheet_import.py`;
- suíte completa sem regressão (conjunto de falhas idêntico ao do HEAD limpo).

## Achado colateral: `JSONB` cru quebrava a suíte

`app/models/legal_one.py` e `app/models/performance.py` declaravam colunas com
`JSONB` do dialeto Postgres. O SQLite dos testes não compila esse tipo, então
`Base.metadata.create_all` estourava e **todo teste com `db_session` falhava no
setup** — 104 erros mascarando regressões reais, exatamente o que o docstring
de `app/db/types.py` já alertava.

Convertidos para o helper `jsonb()` da casa (JSONB no Postgres, JSON no
SQLite). O DDL em Postgres é idêntico — **não exige migration**. Resultado:
104 erros → 0, e 310 testes passando contra 260.

Regra: coluna JSON nova usa `jsonb()`, nunca `JSONB` direto.

## Pendências

- Quando o DJEN do Codex entrar, revisar se a camada 2 e a 3 convivem bem
  (ambas passam pelo mesmo `create_and_run_search`, então a expectativa é que
  sim).
- O alerta de falha na captura (a busca de 30/07 falhou sem avisar ninguém)
  está no trabalho do Codex, em `publication_capture_alert_service.py` — segue
  pendente enquanto aquela branch não entra.

Relacionado: `app/services/publication_spreadsheet_import.py`,
`app/services/publication_search_service.py::create_and_run_search`.
