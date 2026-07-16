"""Critério de CLIENTE nos escritórios (senão o Ativos cai na fila do BB).

O roteamento escolhe o escritório por natureza/polo. Como "Banco do Brasil - Réu"
e "Ativos - Réu" têm o MESMO polo (Passivo), e o do BB tem ordem menor, um
processo do Ativos seria mandado pra fila do Banco do Brasil (provado ao vivo).

O cliente é carimbado pela porta de entrada do processo (coleta RPA = BB;
"Importar lista (Ativos)" = ATIVOS), então serve de critério confiável — mesma
lógica já aplicada nas regras de observação (bbd010).

Revision ID: bbd012_escritorio_cliente
Revises: bbd011_escritorios_ativos
Create Date: 2026-07-16
"""
import sqlalchemy as sa
from alembic import op

revision = "bbd012_escritorio_cliente"
down_revision = "bbd011_escritorios_ativos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bbd_escritorios",
        sa.Column("criterio_cliente", sa.String(length=20), nullable=True),
    )
    # Escritórios do Ativos (criados na bbd011) → ATIVOS; todo o resto é do BB
    # (o módulo nasceu só Banco do Brasil).
    op.execute(
        "UPDATE bbd_escritorios SET criterio_cliente = 'ATIVOS' "
        "WHERE escritorio_path LIKE '%/ Ativos /%' OR nome LIKE 'Ativos - %'"
    )
    op.execute(
        "UPDATE bbd_escritorios SET criterio_cliente = 'BB' WHERE criterio_cliente IS NULL"
    )


def downgrade() -> None:
    op.drop_column("bbd_escritorios", "criterio_cliente")
