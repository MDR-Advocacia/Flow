"""Distribuídos BB: coluna cliente no processo (BB / ATIVOS)

Revision ID: bbd007_processo_cliente
Revises: bbd006_monitor_cadastro
Create Date: 2026-07-13

Mesma interface, motores de ingestão diferentes por cliente. Adiciona
`cliente` em bbd_processos (default BB; backfill dos existentes = BB, que são
todos do Banco do Brasil). Idempotente.
"""
from alembic import op
import sqlalchemy as sa


revision = "bbd007_processo_cliente"
down_revision = "bbd006_monitor_cadastro"
branch_labels = None
depends_on = None


def _has_col(table: str, col: str) -> bool:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table(table):
        return False
    return col in {c["name"] for c in insp.get_columns(table)}


def _has_table(table: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table)


def upgrade() -> None:
    if not _has_col("bbd_processos", "cliente"):
        op.add_column(
            "bbd_processos",
            sa.Column("cliente", sa.String(length=20), nullable=False, server_default="BB"),
        )
        op.create_index("ix_bbd_processos_cliente", "bbd_processos", ["cliente"])
        op.execute("UPDATE bbd_processos SET cliente = 'BB' WHERE cliente IS NULL")

    if not _has_table("bbd_ativos_lotes"):
        op.create_table(
            "bbd_ativos_lotes",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("nome_arquivo", sa.String(length=200), nullable=True),
            sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("processados", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("encontrados", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("nao_encontrados", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("criados", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("duplicados", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("invalidos", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="EM_ANDAMENTO"),
            sa.Column("erro", sa.Text(), nullable=True),
            sa.Column("disparado_por_user_id", sa.Integer(), nullable=True),
            sa.Column("iniciado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("concluido_em", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["disparado_por_user_id"], ["legal_one_users.id"], ondelete="SET NULL"),
        )
        op.create_index("ix_bbd_ativos_lotes_status", "bbd_ativos_lotes", ["status"])


def downgrade() -> None:
    if _has_table("bbd_ativos_lotes"):
        op.drop_table("bbd_ativos_lotes")
    if _has_col("bbd_processos", "cliente"):
        op.drop_index("ix_bbd_processos_cliente", table_name="bbd_processos")
        op.drop_column("bbd_processos", "cliente")
