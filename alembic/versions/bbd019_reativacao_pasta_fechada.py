"""Duplicata em pasta FECHADA vira fila de reativação, não silêncio.

Quando a ingestão (BB, Ativos ou Master) encontrava pasta do mesmo cliente pro
CNJ, marcava o processo como CADASTRADO_L1 e seguia — sem olhar o status da
pasta. Na carteira do Banco Master isso é a maioria: de 8.756 pastas, 6.494
estão arquivadas e 215 baixadas (77%). Ou seja, o cliente reenvia processo que
voltou a andar e o Flow registrava "já resolvido": pasta fechada, sem tarefa,
sem ninguém saber.

Três colunas no processo:
- `l1_status_id`: status da pasta no momento em que a duplicata foi detectada;
- `reativacao_status`: fila PENDENTE → REATIVADO / FALHOU / DISPENSADA;
- `reativado_em`: quando a pasta voltou pra Ativo.

Só adiciona colunas nullable — nenhum dado existente muda. Os processos já
marcados CADASTRADO_L1 antes disso ficam com `l1_status_id` NULL (não dá pra
saber retroativamente qual era o status na época); a próxima passagem do
monitor os preenche.

Revision ID: bbd019
Revises: bbd018
"""
from alembic import op
import sqlalchemy as sa

revision = "bbd019"
down_revision = "bbd018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bbd_processos",
        sa.Column("l1_status_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "bbd_processos",
        sa.Column("reativacao_status", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "bbd_processos",
        sa.Column("reativado_em", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_bbd_processos_l1_status_id", "bbd_processos", ["l1_status_id"],
    )
    # A fila de reativação é lida por status a cada abertura do painel, e a
    # esmagadora maioria das linhas tem NULL aqui — índice parcial mantém ele
    # pequeno e ainda serve a consulta (`WHERE reativacao_status = 'PENDENTE'`).
    op.create_index(
        "ix_bbd_processos_reativacao_status", "bbd_processos",
        ["reativacao_status"],
        postgresql_where=sa.text("reativacao_status IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_bbd_processos_reativacao_status", table_name="bbd_processos")
    op.drop_index("ix_bbd_processos_l1_status_id", table_name="bbd_processos")
    op.drop_column("bbd_processos", "reativado_em")
    op.drop_column("bbd_processos", "reativacao_status")
    op.drop_column("bbd_processos", "l1_status_id")
