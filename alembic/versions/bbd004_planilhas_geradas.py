"""Distribuídos BB: histórico de planilhas geradas

Revision ID: bbd004_planilhas_geradas
Revises: bbd003_proc_grupo_aj
Create Date: 2026-07-10

Tabela `bbd_planilhas`: guarda cada planilha de migração gerada (o xlsx inteiro
em `conteudo`), com origem (automática/manual), total de processos, e a
marcação do operador `subido_legalone` (já importei no L1 ou não). Idempotente.
"""
from alembic import op
import sqlalchemy as sa


revision = "bbd004_planilhas_geradas"
down_revision = "bbd003_proc_grupo_aj"
branch_labels = None
depends_on = None


def _has_table(table: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table)


def upgrade() -> None:
    if _has_table("bbd_planilhas"):
        return
    op.create_table(
        "bbd_planilhas",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("nome_arquivo", sa.String(length=200), nullable=False),
        sa.Column("conteudo", sa.LargeBinary(), nullable=False),
        sa.Column("total_processos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tamanho_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("origem", sa.String(length=20), nullable=False, server_default="MANUAL"),
        sa.Column("status_origem", sa.String(length=20), nullable=True),
        sa.Column("subido_legalone", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("subido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subido_por_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["bbd_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["subido_por_user_id"], ["legal_one_users.id"], ondelete="SET NULL",
        ),
    )
    op.create_index("ix_bbd_planilhas_run_id", "bbd_planilhas", ["run_id"])
    op.create_index("ix_bbd_planilhas_subido_legalone", "bbd_planilhas", ["subido_legalone"])
    op.create_index("ix_bbd_planilhas_created_at", "bbd_planilhas", ["created_at"])


def downgrade() -> None:
    if _has_table("bbd_planilhas"):
        op.drop_table("bbd_planilhas")
