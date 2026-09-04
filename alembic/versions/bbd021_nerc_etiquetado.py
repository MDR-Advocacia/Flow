"""Marca quando a pasta recebeu a etiqueta NERC, pra não reetiquetar toda coleta.

Decisão da operação (04/09/2026): todo processo que o motor identificar com
vínculo passa a ser etiquetado como NERC no Legal One, automaticamente — tanto
o processo NOVO quanto as pastas ANTIGAS da mesma parte.

O carimbo é necessário porque a etiquetagem em lote do L1 é "Etiquetas
(Adicionar)": reenviar não quebra nada, mas geraria um POST por coleta pra
sempre. Com a data, a rotina só toca no que ainda não passou.

Por que a coluna e não uma consulta ao L1: ler etiqueta é uma requisição web
por pasta (não tem endpoint em lote), o que sairia caro só pra decidir se
precisa escrever.

Nota: nasceu apontando pra pub012 (o head de produção na hora), mas o repo
ja tinha pub013/pub014 de outra frente — re-encadeada pra pub014 antes do
commit pra nao subir com MultipleHeads e travar o boot do Coolify.

Revision ID: bbd021
Revises: pub014
"""
from alembic import op
import sqlalchemy as sa

revision = "bbd021"
down_revision = "pub014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bbd_processos",
        sa.Column("nerc_etiquetado_em", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "bbd_vinculos",
        sa.Column("nerc_etiquetado_em", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bbd_vinculos", "nerc_etiquetado_em")
    op.drop_column("bbd_processos", "nerc_etiquetado_em")
