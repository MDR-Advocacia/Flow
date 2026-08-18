"""Análise de Risco BB Réu: tabela arb_analise_risco_tarefa (espelho persistente
das tarefas do subtipo + campos da esteira de verificação no portal BB e do
tratamento de divergência pelo supervisor).

Revision ID: arb001_analise_risco_bb
Revises: onb001_onenotify_bb
"""

import sqlalchemy as sa
from alembic import op

revision = "arb001_analise_risco_bb"
down_revision = "onb001_onenotify_bb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "arb_analise_risco_tarefa",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("l1_task_id", sa.BigInteger(), nullable=False),
        sa.Column("subtipo", sa.String(), nullable=True),
        sa.Column("responsavel_nome", sa.String(), nullable=True),
        sa.Column("cumprida_por_nome", sa.String(), nullable=True),
        sa.Column("npj", sa.String(), nullable=True),
        sa.Column("cnj", sa.String(), nullable=True),
        sa.Column("agendada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prazo", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_l1", sa.String(), nullable=True),
        sa.Column("concluida_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verif_status", sa.String(), nullable=False, server_default="PENDENTE"),
        sa.Column("portal_analise_feita", sa.Boolean(), nullable=True),
        sa.Column("portal_estado", sa.String(), nullable=True),
        sa.Column("portal_exito", sa.String(), nullable=True),
        sa.Column("portal_verificado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verif_tentativas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verif_ultimo_erro", sa.Text(), nullable=True),
        sa.Column("divergente", sa.Boolean(), nullable=True),
        sa.Column("trat_status", sa.String(), nullable=True),
        sa.Column("trat_anotacao", sa.Text(), nullable=True),
        sa.Column(
            "trat_por_user_id",
            sa.Integer(),
            sa.ForeignKey("legal_one_users.id"),
            nullable=True,
        ),
        sa.Column("trat_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    t = "arb_analise_risco_tarefa"
    op.create_index(f"ix_{t}_l1_task_id", t, ["l1_task_id"], unique=True)
    op.create_index(f"ix_{t}_responsavel_nome", t, ["responsavel_nome"])
    op.create_index(f"ix_{t}_npj", t, ["npj"])
    op.create_index(f"ix_{t}_status_l1", t, ["status_l1"])
    op.create_index(f"ix_{t}_verif_status", t, ["verif_status"])
    op.create_index(f"ix_{t}_divergente", t, ["divergente"])


def downgrade() -> None:
    op.drop_table("arb_analise_risco_tarefa")
