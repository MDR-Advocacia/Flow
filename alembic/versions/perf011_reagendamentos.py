"""Reagendamentos (adiamentos de prazo) — baseline da manhã + eventos do dia.

Bracket diário 07h/19h: a manhã grava o prazo de cada tarefa pendente em
perf_prazo_manha; a noite compara e grava os ADIAMENTOS (prazo empurrado pra
frente durante o dia) em perf_reagendamento. Aditiva.

Revision ID: perf011_reagendamentos
Revises: bbd017_ativos_agend_jobs
Create Date: 2026-07-22
"""
import sqlalchemy as sa
from alembic import op

revision = "perf011_reagendamentos"
down_revision = "bbd017_ativos_agend_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "perf_prazo_manha",
        sa.Column("l1_task_id", sa.BigInteger(), primary_key=True),
        sa.Column("dia", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prazo", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pessoa_id", sa.Integer(), nullable=True),
        sa.Column("pessoa_nome", sa.String(), nullable=True),
        sa.Column("equipe", sa.String(), nullable=True),
        sa.Column("subtipo", sa.String(), nullable=True),
        sa.Column("pasta", sa.String(), nullable=True),
        sa.Column("cnj", sa.String(), nullable=True),
        sa.Column("capturado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_perf_prazo_manha_dia", "perf_prazo_manha", ["dia"])
    op.create_index("ix_perf_prazo_manha_pessoa_id", "perf_prazo_manha", ["pessoa_id"])
    op.create_index("ix_perf_prazo_manha_equipe", "perf_prazo_manha", ["equipe"])

    op.create_table(
        "perf_reagendamento",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dia", sa.DateTime(timezone=True), nullable=False),
        sa.Column("l1_task_id", sa.BigInteger(), nullable=True),
        sa.Column("pessoa_id", sa.Integer(), nullable=True),
        sa.Column("pessoa_nome", sa.String(), nullable=True),
        sa.Column("equipe", sa.String(), nullable=True),
        sa.Column("subtipo", sa.String(), nullable=True),
        sa.Column("pasta", sa.String(), nullable=True),
        sa.Column("cnj", sa.String(), nullable=True),
        sa.Column("prazo_de", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prazo_para", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dias_adiado", sa.Integer(), nullable=True),
        sa.Column("era_fatal_hoje", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("detectado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("dia", "l1_task_id", name="uq_perf_reag_dia_task"),
    )
    op.create_index("ix_perf_reag_dia", "perf_reagendamento", ["dia"])
    op.create_index("ix_perf_reag_l1_task_id", "perf_reagendamento", ["l1_task_id"])
    op.create_index("ix_perf_reag_pessoa_id", "perf_reagendamento", ["pessoa_id"])
    op.create_index("ix_perf_reag_equipe", "perf_reagendamento", ["equipe"])


def downgrade() -> None:
    op.drop_table("perf_reagendamento")
    op.drop_index("ix_perf_prazo_manha_equipe", table_name="perf_prazo_manha")
    op.drop_index("ix_perf_prazo_manha_pessoa_id", table_name="perf_prazo_manha")
    op.drop_index("ix_perf_prazo_manha_dia", table_name="perf_prazo_manha")
    op.drop_table("perf_prazo_manha")
