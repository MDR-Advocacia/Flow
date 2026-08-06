"""uso001 — rollup diário de utilização do sistema.

Base do relatório de adesão pedido pelo administrativo. Rollup por
(usuário, dia, módulo) em vez de log por requisição: o auto-poll global do
Flow geraria centenas de linhas por tarde de tela aberta, sem informação
nenhuma sobre adesão de verdade.

Só passa a valer da data do deploy em diante — não há como reconstruir
navegação passada, porque nada era registrado. A parte retroativa do relatório
vem dos rastros de autoria que os módulos já gravam.
"""
from alembic import op
import sqlalchemy as sa


revision = "uso001_registro_de_uso"
down_revision = "perf014_l1_task_id_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flow_uso_diario",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("dia", sa.Date(), nullable=False),
        sa.Column("modulo", sa.String(length=60), nullable=False),
        sa.Column("requisicoes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("primeira_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultima_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "atualizado_em", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["legal_one_users.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "dia", "modulo"),
    )
    # O relatório sempre corta por período ("últimos 30 dias"), então o índice
    # por dia é o que sustenta a consulta principal.
    op.create_index("ix_flow_uso_diario_dia", "flow_uso_diario", ["dia"])


def downgrade() -> None:
    op.drop_index("ix_flow_uso_diario_dia", table_name="flow_uso_diario")
    op.drop_table("flow_uso_diario")
