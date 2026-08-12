"""Job de agendamento de tarefa em lote sobre duplicados Ativos (server-backed).

A UI seleciona duplicados (que já existem no L1) e agenda tarefa em lote —
subtipo, responsáveis (dividindo igual ou pra uma pessoa), prazo. Um worker cria
as tarefas no L1 e a barra de progresso acompanha por polling. Aditiva.

Revision ID: bbd017_ativos_agend_jobs
Revises: bbd016_ativos_duplicados
Create Date: 2026-07-21
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "bbd017_ativos_agend_jobs"
down_revision = "bbd016_ativos_duplicados"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bbd_ativos_agend_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="EM_ANDAMENTO"),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processados", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("criados", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("falhas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pulados", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("itens", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column(
            "disparado_por_user_id", sa.Integer(),
            sa.ForeignKey("legal_one_users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("iniciado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("concluido_em", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_bbd_ativos_agend_jobs_status", "bbd_ativos_agend_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_bbd_ativos_agend_jobs_status", table_name="bbd_ativos_agend_jobs")
    op.drop_table("bbd_ativos_agend_jobs")
