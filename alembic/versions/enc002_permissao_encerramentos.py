"""Permissão de módulo para o Encerramentos (padrão dos demais módulos).

O menu "Encerramentos no Legal One" tinha nascido gated por `isAdmin`, fora do
padrão da casa: todo módulo do Flow tem sua flag `can_use_*` (cache
materializado do RBAC por cargo + exceção, escrito por
`app/services/permissoes.py`). Esta migration cria a coluna e a torna parte do
catálogo de módulos — assim o acesso passa a ser concedido pelo cargo ou por
exceção individual no Painel Administrativo, como em Publicações, Prazos
Iniciais e OneRequest.

Ninguém ganha ou perde acesso aqui: a coluna nasce `false` para todos e os
admins seguem bypassando todos os gates. Quem já usava o menu (admins)
continua enxergando.

Revision ID: enc002_perm_encerramentos
Revises: enc001_l1_intake
Create Date: 2026-07-30
"""
import sqlalchemy as sa
from alembic import op

revision = "enc002_perm_encerramentos"
down_revision = "enc001_l1_intake"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "legal_one_users",
        sa.Column(
            "can_use_encerramentos",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("legal_one_users", "can_use_encerramentos")
