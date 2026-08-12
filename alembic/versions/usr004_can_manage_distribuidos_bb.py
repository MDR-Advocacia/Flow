"""Permissão can_manage_distribuidos_bb em legal_one_users

Revision ID: usr004_can_manage_bb
Revises: bbd002_config_editavel
Create Date: 2026-07-09

Libera, por usuário, o acesso ao módulo Distribuídos BB + edição das tabelas
de configuração (admin sempre passa). Idempotente.
"""
from alembic import op
import sqlalchemy as sa


revision = "usr004_can_manage_bb"
down_revision = "bbd002_config_editavel"
branch_labels = None
depends_on = None


def _has_col(table: str, col: str) -> bool:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table(table):
        return False
    return col in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_col("legal_one_users", "can_manage_distribuidos_bb"):
        op.add_column(
            "legal_one_users",
            sa.Column(
                "can_manage_distribuidos_bb",
                sa.Boolean(),
                server_default="false",
                nullable=False,
            ),
        )


def downgrade() -> None:
    if _has_col("legal_one_users", "can_manage_distribuidos_bb"):
        op.drop_column("legal_one_users", "can_manage_distribuidos_bb")
