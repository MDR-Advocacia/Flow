"""Status do enriquecimento DataJud nos processos do Ativos.

A planilha da Ativos virou a fonte primária do cadastro; o DataJud complementa de
forma ASSÍNCRONA (worker reconsulta os pendentes, pois o recém-distribuído pode
ainda não estar indexado na base pública). Duas colunas rastreiam isso.

Aditiva/inócua pro Banco do Brasil (ficam NULL).

Revision ID: bbd009_ativos_datajud_status
Revises: bbd008_rename_escritorios_bb
Create Date: 2026-07-15
"""
import sqlalchemy as sa
from alembic import op

revision = "bbd009_ativos_datajud_status"
down_revision = "bbd008_rename_escritorios_bb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bbd_processos", sa.Column("datajud_status", sa.String(length=20), nullable=True))
    op.add_column("bbd_processos", sa.Column("datajud_verificado_em", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_bbd_processos_datajud_status", "bbd_processos", ["datajud_status"])


def downgrade() -> None:
    op.drop_index("ix_bbd_processos_datajud_status", table_name="bbd_processos")
    op.drop_column("bbd_processos", "datajud_verificado_em")
    op.drop_column("bbd_processos", "datajud_status")
