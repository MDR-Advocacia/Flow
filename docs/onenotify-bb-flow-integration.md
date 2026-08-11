# Integração OneNotify BB no Flow

Este módulo recebe notificações do portal jurídico do Banco do Brasil vindas do OneNotify, persiste o payload no Flow e tenta conciliá-las com publicações Legal One já existentes. A conciliação é determinística, sem IA.

## Objetivo

- Mostrar no Flow que uma publicação Legal One também foi notificada pelo cliente no portal do BB.
- Evitar retrabalho humano quando a notificação BB é redundante com uma publicação já classificada/tratada no módulo de Publicações.
- Separar notificações com documentos para tratamento próprio no Flow, inclusive quando o PDF é imagem e não possui texto extraível.
- Preservar NPJ, CNJ principal do Notify e CNJ extraído do texto da publicação, porque recursos, apensos e incidentes podem aparecer vinculados ao mesmo NPJ no Banco do Brasil.

## Rotas

### Intake público do OneNotify

`POST /api/v1/onenotify-bb/intake`

Autenticação por header:

```http
X-Onenotify-Api-Key: <valor de ONENOTIFY_BB_INTAKE_API_KEY>
```

O endpoint aceita um payload único ou um lote:

```json
{
  "items": [
    {
      "schema_version": "onenotify.flow-intake.v1",
      "external_group_id": "onenotify:128077:0",
      "ids": [128077],
      "npj": "2026/0217541-000",
      "numero_processo_cnj": "7008928-76.2026.8.22.0014",
      "cnj_publicacao": "7008928-76.2026.8.22.0014",
      "data_notificacao": "09/07/2026",
      "tipos_notificacao": ["PUBLICACAO DJ/DO"],
      "conteudo": {
        "fontes_texto": [
          {
            "tipo": "andamento",
            "data": "06/07/2026",
            "titulo": "PUBLICACAO DJ/DO",
            "texto": "Texto completo capturado no OneNotify..."
          }
        ],
        "tem_documentos": false,
        "total_documentos": 0,
        "total_documentos_ocr_required": 0
      },
      "documentos": {
        "schema_version": "onenotify.documents.v1",
        "items": []
      }
    }
  ]
}
```

### Leitura protegida no módulo de publicações

- `GET /api/v1/publications/onenotify-bb/stats`
- `GET /api/v1/publications/onenotify-bb/notifications?limit=250&offset=0`
- `GET /api/v1/publications/onenotify-bb/notifications/{id}`

As rotas de leitura exigem JWT e permissão `publications`, como o restante do módulo.

## Regra de Conciliação

O backend usa:

- data civil da publicação (`YYYY-MM-DD`), aceitando registros do Flow com timestamp ISO;
- CNJ da publicação extraído por regex do texto do OneNotify ou recebido no campo `cnj_publicacao`;
- comparação textual determinística por normalização + `difflib.SequenceMatcher` + contenção de tokens;
- corte mínimo de `0.80` para conciliação automática.

Status gerados:

- `CONCILIADA_AUTO`: publicação BB redundante com publicação Legal One; não exige tratamento no Notify.
- `PENDENTE_DOCUMENTO`: sem match de publicação e com documento; precisa virar demanda do Flow.
- `PENDENTE_FLOW`: sem match e sem documento; precisa revisão humana no Flow.
- `REVISAO`: achou publicação parecida, mas não passou na regra automática.

## CNJ Principal vs CNJ da Publicação

O Banco do Brasil mostra a notificação pelo NPJ, mas o texto pode trazer CNJ de recurso, incidente, apenso ou processo secundário. Por isso o Flow guarda:

- `npj`: número interno do Banco do Brasil;
- `numero_processo_cnj` / `cnj_principal_notify`: CNJ principal que o Notify tinha no cadastro;
- `cnj_publicacao`: CNJ encontrado no texto da publicação;
- `cnj_divergent`: `true` quando o principal difere do CNJ publicado.

Esse alerta não bloqueia a conciliação por si só. Ele serve para revisão operacional e para futura busca de pasta no Legal One pelo CNJ correto.

## Documentos e PDFs Imagem

Quando a notificação traz documentos, o payload deve informar:

- metadados do arquivo;
- link/rota de visualização do OneNotify, quando disponível;
- texto extraído, quando houver;
- `ocr_required: true` quando o PDF é imagem;
- texto parcial, se existir.

Documentos sem texto capturável não devem ser descartados. Eles entram como `PENDENTE_DOCUMENTO` e o Flow pode exibir legenda de "PDF imagem / OCR necessário".

## Impacto no Módulo de Publicações

`PublicationRecord` agora pode vir enriquecido com:

```json
"onenotify_bb_notifications": [
  {
    "id": 12,
    "npj": "2026/0213800-000",
    "data_notificacao": "09/07/2026",
    "notification_date_iso": "2026-07-09",
    "flow_status": "CONCILIADA_AUTO",
    "match_score": 1.0,
    "cnj_publicacao": "0801497-60.2026.8.20.5114",
    "cnj_principal_notify": "0801497-60.2026.8.20.5114",
    "cnj_divergent": false,
    "posicao_cliente": "Banco citado no texto"
  }
]
```

A tela de Publicações exibe "Notificado pelo cliente em ..." na coluna de datas e no modal do registro quando esse vínculo existe.

## Teste Local com Dados Reais

A amostra foi montada com:

- 2.179 linhas reais do relatório OneNotify x Flow;
- 1.292 publicações reais exportadas em leitura da produção do Flow;
- nenhum write em produção.

Nota sobre visualização textual: o CSV usado para esta amostra local traz
`notify_excerpt`, não o payload integral do OneNotify. Por isso alguns registros
da demonstração exibem o texto do OneNotify truncado, embora a publicação do
Flow esteja completa. O contrato de produção exige que o OneNotify envie o texto
integral capturado pela RPA em `conteudo.fontes_texto[].texto`.

Comando usado:

```bash
DATABASE_URL=sqlite:///./data/onenotify_bb_real_sample_20260811_v2.db \
  .venv-flow/bin/python scripts/onenotify_bb_import_real_sample.py \
  --create-schema \
  --comparison-csv /Users/rildonpimentelpereira/Documents/Codex/2026-06-25/um/work/notify-flow-compare/notify_flow_publication_match_report.csv \
  --flow-records-csv /Users/rildonpimentelpereira/Documents/Codex/2026-06-25/um/work/flow-real-sample/publicacao_registros_matched_sample.csv
```

Resultado recalculado pelo backend do Flow:

- total analisado: `2.179`
- com publicação equivalente: `1.372` (`63,0%`)
- conciliadas automaticamente: `1.359` (`62,4%`)
- CNJ principal diferente do texto: `158`
- sem match: `807`

O estudo anterior apontava 1.374 matches. A regra nova recusou 2 casos que eram falsos positivos úteis para o backlog: o Flow tinha publicação do CNJ principal, mas o texto do Notify era de recurso/agravo com CNJ incidental diferente.

## Validações

```bash
.venv-flow/bin/python -m pytest tests/services/test_onenotify_bb_service.py -q
npm --prefix frontend run build
.venv-flow/bin/python -m compileall app scripts
```

Observações:

- O build do frontend mantém avisos já existentes de chunk grande e um aviso CSS legado.
- O `conftest` global de testes usa SQLite e ainda encontra modelos antigos com `JSONB`; por isso os testes novos usam fixture isolada com apenas as tabelas da integração.
