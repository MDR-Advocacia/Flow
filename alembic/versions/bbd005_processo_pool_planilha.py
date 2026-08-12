"""Distribuídos BB: pool de planilha no processo (NOVO / PLANILHA_GERADA)

Revision ID: bbd005_pool_planilha
Revises: bbd004_planilhas_geradas
Create Date: 2026-07-10

O operador é quem gera a planilha: o processo nasce NOVO no pool e vira
PLANILHA_GERADA quando entra numa planilha. Colunas em bbd_processos:
`planilha_status`, `planilha_id` (FK bbd_planilhas), `planilha_gerada_em`.
Idempotente.
"""
from alembic import op
import sqlalchemy as sa


revision = "bbd005_pool_planilha"
down_revision = "bbd004_planilhas_geradas"
branch_labels = None
depends_on = None


def _has_col(table: str, col: str) -> bool:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table(table):
        return False
    return col in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_col("bbd_processos", "planilha_status"):
        op.add_column(
            "bbd_processos",
            sa.Column(
                "planilha_status", sa.String(length=20),
                nullable=False, server_default="NOVO",
            ),
        )
        op.create_index(
            "ix_bbd_processos_planilha_status", "bbd_processos", ["planilha_status"],
        )
    if not _has_col("bbd_processos", "planilha_id"):
        op.add_column(
            "bbd_processos", sa.Column("planilha_id", sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            "fk_bbd_proc_planilha",
            "bbd_processos", "bbd_planilhas",
            ["planilha_id"], ["id"], ondelete="SET NULL",
        )
        op.create_index(
            "ix_bbd_processos_planilha_id", "bbd_processos", ["planilha_id"],
        )
    if not _has_col("bbd_processos", "planilha_gerada_em"):
        op.add_column(
            "bbd_processos",
            sa.Column("planilha_gerada_em", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    if _has_col("bbd_processos", "planilha_gerada_em"):
        op.drop_column("bbd_processos", "planilha_gerada_em")
    if _has_col("bbd_processos", "planilha_id"):
        op.drop_index("ix_bbd_processos_planilha_id", table_name="bbd_processos")
        op.drop_constraint("fk_bbd_proc_planilha", "bbd_processos", type_="foreignkey")
        op.drop_column("bbd_processos", "planilha_id")
    if _has_col("bbd_processos", "planilha_status"):
        op.drop_index("ix_bbd_processos_planilha_status", table_name="bbd_processos")
        op.drop_column("bbd_processos", "planilha_status")
