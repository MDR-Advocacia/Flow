-- Auditoria das decisões de triagem SEGMENTADA POR POLO.
--
-- Autor (recuperação de crédito) e réu (contencioso passivo) são dois motores
-- de decisão diferentes: no autor a publicação costuma ser sobre execução,
-- penhora e custas, e o escritório é quem impulsiona o processo; no réu ela é
-- sobre defesa, prazo e recurso da parte contrária, e o escritório reage. Um
-- número médio entre os dois esconde as duas realidades — foi o erro da
-- primeira rodada desta auditoria.
--
-- O polo sai de `legal_one_offices.polo_scope`, com o caminho do escritório
-- como conferência: `polo_scope` é preenchido à mão e pode estar em "ambos".
--
-- Uso (produção):
--   scp auditoria_triagem_por_polo.sql ubuntu@<host>:/tmp/
--   sudo docker cp /tmp/auditoria_triagem_por_polo.sql <postgres>:/tmp/a.sql
--   sudo docker exec <postgres> psql -U onetask -d onetask -f /tmp/a.sql

\echo '=== 0. O polo está cadastrado? (polo_scope x caminho) ==='
SELECT coalesce(o.polo_scope, '(nulo)') AS polo_scope,
       count(*) FILTER (WHERE o.path ILIKE '%autor%')                AS caminho_diz_autor,
       count(*) FILTER (WHERE o.path ILIKE '%r_u%' OR o.path ILIKE '%réu%') AS caminho_diz_reu,
       count(*)                                                       AS escritorios
FROM legal_one_offices o
GROUP BY 1 ORDER BY 4 DESC;

\echo ''
\echo '=== 1. Volume e taxa de agendamento por polo ==='
WITH base AS (
  SELECT CASE
           WHEN o.path ILIKE '%autor%' THEN 'AUTOR (recup. de crédito)'
           WHEN o.path ILIKE '%réu%' OR o.path ILIKE '%reu%' THEN 'RÉU (contencioso passivo)'
           ELSE coalesce('outro: ' || o.polo_scope, 'sem escritório')
         END AS polo,
         r.status
  FROM publicacao_registros r
  LEFT JOIN legal_one_offices o ON o.external_id = r.linked_office_id
  WHERE r.status IN ('AGENDADO','IGNORADO') AND r.is_duplicate = false
)
SELECT polo, count(*) AS publicacoes,
       round(100.0*count(*) FILTER (WHERE status='AGENDADO')/count(*)) AS pct_agenda
FROM base GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '=== 2. PAUTA DE JULGAMENTO por polo (o caso 01 do roteiro) ==='
\echo '(a regra "pauta não gera tarefa" vale igual nos dois lados?)'
WITH base AS (
  SELECT CASE WHEN o.path ILIKE '%autor%' THEN 'AUTOR'
              WHEN o.path ILIKE '%réu%' OR o.path ILIKE '%reu%' THEN 'RÉU'
              ELSE 'outro' END AS polo,
         r.status, coalesce(r.scheduled_by_name, r.ignored_by_name) AS operador
  FROM publicacao_registros r
  LEFT JOIN legal_one_offices o ON o.external_id = r.linked_office_id
  WHERE r.subcategory ILIKE '%Inclus%Pauta%'
    AND r.status IN ('AGENDADO','IGNORADO') AND r.is_duplicate = false
)
SELECT polo, count(*) AS pubs,
       count(*) FILTER (WHERE status='AGENDADO') AS agendou,
       round(100.0*count(*) FILTER (WHERE status='AGENDADO')/count(*)) AS pct_agenda,
       count(DISTINCT operador) AS operadores
FROM base GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '=== 3. O mesmo operador muda de critério conforme o polo? ==='
WITH base AS (
  SELECT CASE WHEN o.path ILIKE '%autor%' THEN 'AUTOR'
              WHEN o.path ILIKE '%réu%' OR o.path ILIKE '%reu%' THEN 'RÉU'
              ELSE 'outro' END AS polo,
         coalesce(r.scheduled_by_name, r.ignored_by_name) AS operador, r.status
  FROM publicacao_registros r
  LEFT JOIN legal_one_offices o ON o.external_id = r.linked_office_id
  WHERE r.subcategory ILIKE '%Inclus%Pauta%'
    AND r.status IN ('AGENDADO','IGNORADO') AND r.is_duplicate = false
)
SELECT left(operador,26) AS operador, polo, count(*) AS pubs,
       round(100.0*count(*) FILTER (WHERE status='AGENDADO')/count(*)) AS pct_agenda
FROM base WHERE operador IS NOT NULL AND polo <> 'outro'
GROUP BY 1,2 HAVING count(*) >= 20 ORDER BY 1, 2;

\echo ''
\echo '=== 4. As categorias de maior volume são as MESMAS nos dois polos? ==='
WITH base AS (
  SELECT CASE WHEN o.path ILIKE '%autor%' THEN 'AUTOR'
              WHEN o.path ILIKE '%réu%' OR o.path ILIKE '%reu%' THEN 'RÉU'
              ELSE 'outro' END AS polo,
         r.category, r.subcategory, r.status
  FROM publicacao_registros r
  LEFT JOIN legal_one_offices o ON o.external_id = r.linked_office_id
  WHERE r.status IN ('AGENDADO','IGNORADO') AND r.is_duplicate = false
    AND r.category IS NOT NULL
), rank AS (
  SELECT polo, category, subcategory, count(*) AS pubs,
         round(100.0*count(*) FILTER (WHERE status='IGNORADO')/count(*)) AS pct_ignora,
         row_number() OVER (PARTITION BY polo ORDER BY count(*) DESC) AS pos
  FROM base WHERE polo <> 'outro' GROUP BY 1,2,3
)
SELECT polo, pos, left(category,26) AS categoria,
       left(coalesce(subcategory,'-'),28) AS sub, pubs, pct_ignora
FROM rank WHERE pos <= 6 ORDER BY polo, pos;

\echo ''
\echo '=== 5. Troca de subtipo por polo (onde o template erra mais) ==='
WITH base AS (
  SELECT CASE WHEN o.path ILIKE '%autor%' THEN 'AUTOR'
              WHEN o.path ILIKE '%réu%' OR o.path ILIKE '%reu%' THEN 'RÉU'
              ELSE 'outro' END AS polo,
         a.override_fields ? 'subTypeId' AS trocou
  FROM publicacao_tarefa_audit a
  JOIN publicacao_registros r ON r.id = a.publication_record_id
  LEFT JOIN legal_one_offices o ON o.external_id = r.linked_office_id
)
SELECT polo, count(*) AS tarefas,
       count(*) FILTER (WHERE trocou) AS trocas,
       round(100.0*count(*) FILTER (WHERE trocou)/count(*),1) AS pct_troca
FROM base WHERE polo <> 'outro' GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '=== 6. "Cumprir determinação": destinos diferentes por polo? ==='
WITH base AS (
  SELECT CASE WHEN o.path ILIKE '%autor%' THEN 'AUTOR'
              WHEN o.path ILIKE '%réu%' OR o.path ILIKE '%reu%' THEN 'RÉU'
              ELSE 'outro' END AS polo,
         s.name AS destino
  FROM publicacao_tarefa_audit a
  JOIN publicacao_registros r ON r.id = a.publication_record_id
  LEFT JOIN legal_one_offices o ON o.external_id = r.linked_office_id
  JOIN legal_one_task_subtypes s
    ON s.external_id = (a.override_fields->'subTypeId'->>'enviado')::int
  WHERE a.override_fields ? 'subTypeId' AND r.subcategory ILIKE '%Cumprir Determ%'
), rank AS (
  SELECT polo, destino, count(*) AS vezes,
         row_number() OVER (PARTITION BY polo ORDER BY count(*) DESC) AS pos
  FROM base WHERE polo <> 'outro' GROUP BY 1,2
)
SELECT polo, pos, left(destino,42) AS destino_escolhido, vezes
FROM rank WHERE pos <= 5 ORDER BY polo, pos;

\echo ''
\echo '=== 7. Famílias confiáveis (agenda alta + template estável) por polo ==='
WITH dec AS (
  SELECT CASE WHEN o.path ILIKE '%autor%' THEN 'AUTOR'
              WHEN o.path ILIKE '%réu%' OR o.path ILIKE '%reu%' THEN 'RÉU'
              ELSE 'outro' END AS polo,
         r.category, r.subcategory, count(*) AS total,
         count(*) FILTER (WHERE r.status='AGENDADO')::numeric/count(*) AS p_agenda
  FROM publicacao_registros r
  LEFT JOIN legal_one_offices o ON o.external_id = r.linked_office_id
  WHERE r.status IN ('AGENDADO','IGNORADO') AND r.is_duplicate=false AND r.category IS NOT NULL
  GROUP BY 1,2,3
)
SELECT polo, left(category,26) AS categoria, left(coalesce(subcategory,'-'),28) AS sub,
       total, round(100*p_agenda) AS pct_agenda
FROM dec
WHERE polo <> 'outro' AND total >= 60 AND p_agenda >= 0.90
ORDER BY polo, total DESC LIMIT 16;
