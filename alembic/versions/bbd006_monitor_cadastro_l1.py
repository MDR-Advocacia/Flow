"""Distribuídos BB: monitor de cadastro no Legal One

Revision ID: bbd006_monitor_cadastro
Revises: bbd005_pool_planilha
Create Date: 2026-07-10

Colunas em bbd_processos pro monitor que bate na API do L1 (de 2 em 2 min a
partir da geração da planilha) procurando a pasta por CNJ+escritório:
`cadastro_confirmado_em`, `l1_verificado_em`, `l1_folder`. Idempotente.
"""
from alembic import op
import sqlalchemy as sa


revision = "bbd006_monitor_cadastro"
down_revision = "bbd005_pool_planilha"
branch_labels = None
depends_on = None


def _has_col(table: str, col: str) -> bool:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table(table):
        return False
    return col in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_col("bbd_processos", "cadastro_confirmado_em"):
        op.add_column(
            "bbd_processos",
            sa.Column("cadastro_confirmado_em", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_col("bbd_processos", "l1_verificado_em"):
        op.add_column(
            "bbd_processos",
            sa.Column("l1_verificado_em", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_col("bbd_processos", "l1_folder"):
        op.add_column(
            "bbd_processos", sa.Column("l1_folder", sa.String(length=40), nullable=True),
        )


def downgrade() -> None:
    for col in ("l1_folder", "l1_verificado_em", "cadastro_confirmado_em"):
        if _has_col("bbd_processos", col):
            op.drop_column("bbd_processos", col)
