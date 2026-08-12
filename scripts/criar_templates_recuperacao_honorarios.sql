-- Templates da carteira Recuperação de Honorários (escritório L1 63).
--
-- Carteira nova, resumida e específica: tudo que cair nela vai para a Hellen
-- (external_id 1809) com prazo no próximo dia útil. Um template CURINGA por
-- categoria — `subcategory` NULL casa com todas as subcategorias da categoria
-- (ver TaskTemplate matching: subcategory.in_(candidatas) | subcategory.is_(None)),
-- então 13 linhas cobrem a árvore inteira sem precisar enumerar subcategoria.
--
-- Por que 13 e não 24: o escritório 63 está marcado como `polo_scope = 'ativo'`
-- (a MDR é a credora dos próprios honorários), e o classificador só oferece à
-- IA as categorias 'ativo' + 'ambos'. Template em categoria 'passivo' nunca
-- casaria — seria peso morto no catálogo.
--
-- Subtipo 816 "Analisar demanda" é TEMPORÁRIO: a carteira ainda não tem família
-- própria no L1. Como o nome do subtipo não descreve o ato, a descrição de cada
-- template carrega o que a tarefa realmente é. Quando a família for cadastrada,
-- trocar `task_subtype_external_id` por categoria.
--
-- Idempotente: não recria template ativo já existente pra mesma
-- (categoria, curinga, escritório).

INSERT INTO task_templates (
    name, category, subcategory, office_external_id,
    task_subtype_external_id, responsible_user_external_id,
    priority, due_business_days, due_date_reference,
    description_template, target_role, taxonomy_version,
    needs_taxonomy_review, is_active, created_at
)
SELECT
    v.categoria || ' — Recuperação de Honorários',
    v.categoria,
    NULL,
    63,
    816,
    1809,
    'Normal',
    1,
    'today',
    v.descricao,
    'principal',
    'v2',
    false,
    true,
    now()
FROM (VALUES
    ('Citação, Intimação e Localização',
     'Recuperação de Honorários — citação, intimação ou localização do devedor. Processo {cnj}, publicado em {publication_date}.'),
    ('Manifestação do Credor / Exequente',
     'Recuperação de Honorários — manifestação da MDR como exequente. Processo {cnj}, publicado em {publication_date}.'),
    ('Manifestação do Devedor / Executado',
     'Recuperação de Honorários — analisar manifestação do executado e responder. Processo {cnj}, publicado em {publication_date}.'),
    ('Pesquisa Patrimonial e Bloqueio',
     'Recuperação de Honorários — pesquisa patrimonial e bloqueio (Sisbajud, Renajud, Infojud). Processo {cnj}, publicado em {publication_date}.'),
    ('Penhora, Garantia e Expropriação',
     'Recuperação de Honorários — penhora, garantia ou expropriação de bem do devedor. Processo {cnj}, publicado em {publication_date}.'),
    ('Acordo, Pagamento e Depósito',
     'Recuperação de Honorários — acordo, pagamento ou depósito; conferir valor e providenciar levantamento. Processo {cnj}, publicado em {publication_date}.'),
    ('Defesa do Devedor e Incidentes',
     'Recuperação de Honorários — impugnação, embargos ou incidente do devedor; preparar resposta. Processo {cnj}, publicado em {publication_date}.'),
    ('Decisão, Sentença e Extinção',
     'Recuperação de Honorários — decisão, sentença ou extinção; avaliar o resultado e o próximo passo da cobrança. Processo {cnj}, publicado em {publication_date}.'),
    ('Recursos',
     'Recuperação de Honorários — recurso; avaliar cabimento, prazo e necessidade de contrarrazões. Processo {cnj}, publicado em {publication_date}.'),
    ('Para Análise — Recuperação de Crédito',
     'Recuperação de Honorários — triagem: publicação sem providência evidente, definir o encaminhamento. Processo {cnj}, publicado em {publication_date}.'),
    ('Audiências',
     'Recuperação de Honorários — audiência designada; confirmar data, horário e quem comparece. Processo {cnj}, publicado em {publication_date}.'),
    ('Assembleia de Credores',
     'Recuperação de Honorários — assembleia de credores; conferir a habilitação do crédito da MDR. Processo {cnj}, publicado em {publication_date}.'),
    ('Recuperação Judicial',
     'Recuperação de Honorários — recuperação judicial do devedor; acompanhar o crédito habilitado. Processo {cnj}, publicado em {publication_date}.')
) AS v(categoria, descricao)
WHERE NOT EXISTS (
    SELECT 1 FROM task_templates t
     WHERE t.office_external_id = 63
       AND t.category = v.categoria
       AND t.subcategory IS NULL
       AND t.is_active
);
