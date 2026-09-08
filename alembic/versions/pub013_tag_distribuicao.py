"""pub013 - tag de distribuicao de leitura entre operadores.

A equipe de leitura se intercala entre escritorios e, sem marcacao, duas
pessoas abrem a MESMA publicacao ao mesmo tempo — uma trata o que a outra ja
estava tratando. A tag e o combinado do dia: "essas sao minhas, aquelas sao
do Ricardo". Opcional por natureza; publicacao sem tag continua visivel pra
todos, que e o comportamento de hoje.

Tres colunas na propria publicacao (nao em tabela separada) porque a tag e
1:1 com o registro, e curta: ela vive um turno de trabalho e e apagada na
redistribuicao seguinte. Nome desnormalizado junto do id pra listagem e
filtro nao precisarem de join com legal_one_users.

Revision ID: pub013
Revises: pub012
"""
from alembic import op
import sqlalchemy as sa

revision = "pub013"
down_revision = "bbd021"
branch_labels = None
depends_on = None

TABELA = "publicacao_registros"


def upgrade() -> None:
    op.add_column(
        TABELA,
        sa.Column("distribuido_para_user_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        TABELA,
        sa.Column("distribuido_para_nome", sa.String(length=160), nullable=True),
    )
    op.add_column(
        TABELA,
        sa.Column("distribuido_em", sa.DateTime(timezone=True), nullable=True),
    )
    # O filtro "so as minhas" roda a cada carregamento da fila do operador —
    # indice parcial porque a esmagadora maioria das publicacoes nunca recebe
    # tag (a distribuicao e feita por turno, sobre a fila do dia).
    op.create_index(
        "ix_publicacao_registros_distribuido_para",
        TABELA,
        ["distribuido_para_user_id"],
        unique=False,
        postgresql_where=sa.text("distribuido_para_user_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_publicacao_registros_distribuido_para", table_name=TABELA)
    op.drop_column(TABELA, "distribuido_em")
    op.drop_column(TABELA, "distribuido_para_nome")
    op.drop_column(TABELA, "distribuido_para_user_id")
