"""pub012 - vencimento estimado do prazo em cada publicacao.

POR QUE ISTO EXISTE
-------------------
Caso 7000755-49.2024.8.22.0009 (31/08/2026): publicacao capturada em 11/07,
com ciencia dada em 13/07, so foi agendada em 28/08 — 48 dias depois, com o
prazo legal de 5 dias morto desde 17/07. O estudo que dimensionou o problema
achou 6.891 agendamentos com mais de 15 dias entre captura e decisao (18% do
historico) e 1.873 casos de "ciencia sem decisao" com gap acima de 30 dias.

Nada na listagem distinguia uma publicacao de ontem de uma de 40 dias atras:
a ordenacao era por group_key (lawsuit_id como string — arbitraria) e nao
existia nocao de prazo.

O QUE MUDA NO ESQUEMA
---------------------
`publicacao_registros.prazo_estimado` (DATE, nullable, indexada): vencimento
estimado do prazo, calculado dos defaults da taxonomia de classificacao
(default_prazo_dias/default_prazo_tipo, na subcategoria ou na categoria) a
partir da publication_date, com o calculador de dias uteis dos Prazos
Iniciais. NULL quando a categoria nao tem default configurado (hoje TODOS os
defaults estao vazios em producao — a coluna acende conforme o operador for
preenchendo a taxonomia; ate la, a regua de envelhecimento trabalha por idade
de captura, que nao depende de schema).

A coluna e preenchida na classificacao (IA, reclassificacao manual e
propagacao pra irmas) e recalculavel em massa por
scripts/backfill_prazo_estimado.py — rode-o de novo sempre que preencher
defaults novos na taxonomia.
"""

import sqlalchemy as sa
from alembic import op

revision = "pub012"
down_revision = "bbd020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "publicacao_registros",
        sa.Column("prazo_estimado", sa.Date(), nullable=True),
    )
    op.create_index(
        "ix_publicacao_registros_prazo_estimado",
        "publicacao_registros",
        ["prazo_estimado"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_publicacao_registros_prazo_estimado",
        table_name="publicacao_registros",
    )
    op.drop_column("publicacao_registros", "prazo_estimado")
