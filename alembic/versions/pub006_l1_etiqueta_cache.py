"""Cache de etiquetas (tags) do L1 por processo — alimenta os chips de
etiqueta na tela de tratamento de publicações. A API REST do L1 não expõe
etiquetas; a leitura é pelo caminho web (selectedTagsHidden da página de
edição), então cacheamos por lawsuit com TTL pra não repetir o GET pesado.

Revision ID: pub006_l1_etiqueta_cache
Revises: perf011_reagendamentos
Create Date: 2026-07-23
"""
import sqlalchemy as sa
from alembic import op

revision = "pub006_l1_etiqueta_cache"
down_revision = "perf011_reagendamentos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pub_l1_etiqueta_cache",
        sa.Column("lawsuit_id", sa.Integer(), primary_key=True),
        # lista [{"id", "name", "class_name", "color_id"}] — [] = sem etiqueta
        sa.Column("etiquetas", sa.JSON(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("pub_l1_etiqueta_cache")
