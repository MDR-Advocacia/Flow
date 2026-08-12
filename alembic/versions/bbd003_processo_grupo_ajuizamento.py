"""Distribuídos BB: grupo_ajuizamento_id no processo

Revision ID: bbd003_proc_grupo_aj
Revises: usr004_can_manage_bb
Create Date: 2026-07-09

Guarda no processo qual grupo de ajuizamento (rodízio) foi atribuído — pra que
a montagem de envolvidos seja determinística (não re-rodar o rodízio ao exibir).
Idempotente.
"""
from alembic import op
import sqlalchemy as sa


revision = "bbd003_proc_grupo_aj"
down_revision = "usr004_can_manage_bb"
branch_labels = None
depends_on = None


def _has_col(table: str, col: str) -> bool:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table(table):
        return False
    return col in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_col("bbd_processos", "grupo_ajuizamento_id"):
        op.add_column(
            "bbd_processos",
            sa.Column("grupo_ajuizamento_id", sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            "fk_bbd_proc_grupo_ajuizamento",
            "bbd_processos", "bbd_grupos_ajuizamento",
            ["grupo_ajuizamento_id"], ["id"], ondelete="SET NULL",
        )
        op.create_index("ix_bbd_processos_grupo_ajuizamento_id", "bbd_processos", ["grupo_ajuizamento_id"])


def downgrade() -> None:
    if _has_col("bbd_processos", "grupo_ajuizamento_id"):
        op.drop_index("ix_bbd_processos_grupo_ajuizamento_id", table_name="bbd_processos")
        op.drop_constraint("fk_bbd_proc_grupo_ajuizamento", "bbd_processos", type_="foreignkey")
        op.drop_column("bbd_processos", "grupo_ajuizamento_id")
