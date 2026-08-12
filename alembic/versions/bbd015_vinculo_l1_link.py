"""Link do processo vinculado no Legal One (pasta), pro painel de acompanhamento.

Quando o processo vinculado encontrado no portal do BB também existe na nossa
base (casado por CNJ/NPJ), guardamos o id/folder da pasta no L1 pra dar o link
direto a partir do painel Acompanhamento Réu/Autor. Aditiva.

Revision ID: bbd015_vinculo_l1_link
Revises: bbd014_vinculos_equipe_mista
Create Date: 2026-07-20
"""
import sqlalchemy as sa
from alembic import op

revision = "bbd015_vinculo_l1_link"
down_revision = "bbd014_vinculos_equipe_mista"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bbd_vinculos", sa.Column("l1_lawsuit_id", sa.Integer(), nullable=True))
    op.create_index("ix_bbd_vinculos_l1_lawsuit_id", "bbd_vinculos", ["l1_lawsuit_id"])
    op.add_column("bbd_vinculos", sa.Column("l1_folder", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("bbd_vinculos", "l1_folder")
    op.drop_index("ix_bbd_vinculos_l1_lawsuit_id", table_name="bbd_vinculos")
    op.drop_column("bbd_vinculos", "l1_lawsuit_id")
