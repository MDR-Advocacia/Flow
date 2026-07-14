"""Renomeia os escritórios do Banco do Brasil pra ficar detalhado (nome com o
cliente na frente), igual ao padrão do Ativos ("Ativos - Réu").

Ex.: nome "Réu" -> "Banco do Brasil - Réu". Cosmético: a distribuição casa por
`criterio_polo`/`criterio_natureza`, não pelo nome. Idempotente (só mexe nos que
ainda têm o nome curto e cujo path é do Banco do Brasil).

Revision ID: bbd008_rename_escritorios_bb
Revises: bbd007_processo_cliente
Create Date: 2026-07-14
"""
from alembic import op

revision = "bbd008_rename_escritorios_bb"
down_revision = "bbd007_processo_cliente"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE bbd_escritorios
           SET nome = 'Banco do Brasil - ' || nome
         WHERE escritorio_path LIKE '%Banco do Brasil%'
           AND nome NOT LIKE 'Banco do Brasil%'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE bbd_escritorios
           SET nome = regexp_replace(nome, '^Banco do Brasil - ', '')
         WHERE nome LIKE 'Banco do Brasil - %'
           AND escritorio_path LIKE '%Banco do Brasil%'
        """
    )
