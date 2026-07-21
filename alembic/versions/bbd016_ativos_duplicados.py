"""Persiste os CNJs duplicados da ingestão Ativos (antes só um contador volátil).

Cada CNJ da planilha Ativos que é pulado (já veio marcado como cadastrado, ou
repetido de lote anterior) vira uma linha rastreável: motivo, lote de origem e,
resolvidos sob demanda, id/folder da pasta no L1. É dessa lista que sai o
agendamento de tarefa em lote. Aditiva.

Revision ID: bbd016_ativos_duplicados
Revises: bbd015_vinculo_l1_link
Create Date: 2026-07-21
"""
import sqlalchemy as sa
from alembic import op

revision = "bbd016_ativos_duplicados"
down_revision = "bbd015_vinculo_l1_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bbd_ativos_duplicados",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "lote_id", sa.Integer(),
            sa.ForeignKey("bbd_ativos_lotes.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("cnj", sa.String(length=30), nullable=False),
        sa.Column("cnj_digitos", sa.String(length=20), nullable=False),
        sa.Column("motivo", sa.String(length=20), nullable=False),
        sa.Column("parte", sa.String(length=200), nullable=True),
        sa.Column("l1_lawsuit_id", sa.Integer(), nullable=True),
        sa.Column("l1_folder", sa.String(length=60), nullable=True),
        sa.Column("l1_resolvido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("lote_id", "cnj_digitos", name="uq_bbd_ativos_dup_lote_cnj"),
    )
    op.create_index("ix_bbd_ativos_duplicados_lote_id", "bbd_ativos_duplicados", ["lote_id"])
    op.create_index("ix_bbd_ativos_duplicados_cnj_digitos", "bbd_ativos_duplicados", ["cnj_digitos"])
    op.create_index("ix_bbd_ativos_duplicados_l1_lawsuit_id", "bbd_ativos_duplicados", ["l1_lawsuit_id"])


def downgrade() -> None:
    op.drop_index("ix_bbd_ativos_duplicados_l1_lawsuit_id", table_name="bbd_ativos_duplicados")
    op.drop_index("ix_bbd_ativos_duplicados_cnj_digitos", table_name="bbd_ativos_duplicados")
    op.drop_index("ix_bbd_ativos_duplicados_lote_id", table_name="bbd_ativos_duplicados")
    op.drop_table("bbd_ativos_duplicados")
