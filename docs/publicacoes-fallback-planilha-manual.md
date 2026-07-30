# Publicações — 3º fallback: upload manual da planilha do L1

**Status:** planejado, **aguardando** o trabalho do DJEN entrar (branch/WIP do
Codex). NÃO começar antes: este método reusa a estrutura multi-fonte criada pela
migration `pub007_djen_source`, e mexer nos mesmos arquivos agora geraria
conflito.

## Por que existe

A captura de publicações vai ficar com três camadas, da mais automática para a
mais manual:

1. **Legal One `/Updates`** — fonte primária.
2. **DJEN/Comunica** — contingência automática, acionada quando o `/Updates`
   esgota as tentativas com HTTP 502 (o que aconteceu em 30/07/2026, quando as
   13 buscas do dia falharam). Depende de proxy brasileiro (`DJEN_PROXY`).
3. **Planilha do L1 (este documento)** — o operador exporta as publicações na
   tela do Legal One e sobe o arquivo numa aba de upload dentro do módulo de
   Publicações. É o último recurso: funciona mesmo com a API e o DJEN fora.

## A planilha (amostra real: `PUBLICAÇÕES - FLOW_30)07_BACKUP.xlsx`)

Aba única (`Folha 1`), 19 colunas. As que importam para o ingest:

| Coluna | Vira |
|---|---|
| `Escritório responsável` | `LegalOneOffice.path` → resolve o `office_external_id` |
| `Nº do processo` | CNJ (formatado) → resolve o `lawsuit_id` |
| `Pasta` | folder (fallback de resolução quando o CNJ não casa) |
| `Responsável principal` | responsável da pasta |
| `Comarca/Foro`, `UF`, `Órgão` | metadados de exibição |
| `Número do Cliente` | referência do cliente |
| `Andamentos / Data/hora` | **`publication_date`** (chave de dedup) |
| `Andamentos / Descrição` | **texto da publicação** (fingerprint de conteúdo) |
| `Andamentos / Tipo` | filtrar `= "Publicação"` |
| `Andamentos / Status da Intimação Eletrônica` | ex.: `Pendente de ciência` |
| `Andamentos / Tratamento` | ex.: `Não tratado` |
| `Data do cadastro` | **`_lawsuit_creation_date`** — alimenta a detecção de publicação obsoleta |

As 5 últimas (`SUBTIPO`, `EXECUTANTE`, `PRAZO`, `DATA DA TAREFA`, `HORÁRIO`) vêm
vazias e seguem o formato do agendamento de *Tarefas por Planilha* — a mesma
planilha serve de modelo para agendar depois. Não usar no ingest.

## Onde encaixar (o ponto crítico)

O DJEN provou o caminho certo: **a fonte alternativa entra ANTES da dedup**, não
depois. Concretamente, `fetch_publications_for_window` devolve a lista no
contrato do L1 e `create_and_run_search` segue igual — persistindo, deduplicando
e classificando.

O upload manual deve fazer o mesmo: converter as linhas da planilha para o
contrato de publicação e entregar a `create_and_run_search`. **Nunca** gravar em
`publicacao_registros` direto.

Isso garante de graça:

- **Dedup por conteúdo** — a chave é
  `(lawsuit_id, publication_date, content_fingerprint)`, agnóstica de fonte, mais
  a chave grossa `(lawsuit_id, publication_date)`. Publicação já capturada pelo
  L1 ou pelo DJEN **não duplica**.
- **Detecção de obsoleta** — publicação anterior à criação da pasta
  (`Data do cadastro`) já foi auditada na admissão.
- **A "enxugada" antes da classificação** — uma publicação por processo/dia vai
  ao agente; as demais herdam. Sem isso, o custo de IA multiplica.

## Estrutura que já existe (pub007) e deve ser reusada

A migration do DJEN criou campos **genéricos de multi-fonte** em
`publicacao_registros` — não são específicos do DJEN:

- `source_provider` (indexado) → usar algo como `PLANILHA_L1`
- `source_external_id`, `ingestion_key` → idempotência do arquivo/linha
- `source_payload` (JSON) → guardar a linha crua para auditoria
- `content_fingerprint`

E há **reconciliação**: quando o `/Updates` do L1 volta e traz uma publicação que
já existe com `source_provider` alternativo e sem `legal_one_update_id`, o
registro existente é atualizado em vez de duplicar. O upload manual precisa
entrar nesse mesmo mecanismo.

## Decisões a tomar quando for implementar

1. **Idempotência do arquivo** — subir a mesma planilha duas vezes não pode
   duplicar. Sugestão: `ingestion_key` = hash do arquivo + índice da linha
   (padrão já usado no `file_sha256` da Base Processual).
2. **Escritório** — casar por `path` completo (a convenção da casa é preferir
   `LegalOneOffice.path` a `name`).
3. **Linha que não resolve o processo** — CNJ/pasta sem correspondência no L1:
   descartar com motivo visível ou importar sem vínculo? (Hoje o módulo tem
   registros "sem pasta vinculada"; provavelmente seguir o mesmo caminho.)
4. **Preview antes de gravar** — o padrão da casa em upload é validar e mostrar
   o que vai entrar antes de efetivar (ver *Tarefas por Planilha*).
5. **Paginação na aba** — regra obrigatória do projeto para qualquer listagem.

## Pré-requisitos

- O trabalho do DJEN precisa estar **mergeado** (a `pub007` é a base).
- Confirmar antes de subir o DJEN: `DJEN_PROXY` configurado no Coolify (a
  Comunica bloqueia IP de datacenter AWS fora do Brasil).

Relacionado: [[docs/balanceador-compromissos-plano.md]] (mesmo padrão de plano
antes de implementar), `app/services/djen_publication_fallback.py`,
`app/services/publication_search_service.py::create_and_run_search`.
