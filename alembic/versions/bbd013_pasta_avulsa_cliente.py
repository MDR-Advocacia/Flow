"""Cliente por processo — pasta avulsa (modal de criação manual).

A pasta avulsa pode ser de QUALQUER cliente (não só BB/Ativos), então o processo
carrega nome/CNPJ/tipo do cliente; quando preenchidos, a planilha de migração usa
estes valores em vez da config global do cliente. A tag `cliente` ganha o valor
OUTRO para essas pastas. Aditiva (NULL nos processos existentes).

Revision ID: bbd013_pasta_avulsa_cliente
Revises: bbd012_escritorio_cliente
Create Date: 2026-07-16
"""
import sqlalchemy as sa
from alembic import op

revision = "bbd013_pasta_avulsa_cliente"
down_revision = "bbd012_escritorio_cliente"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bbd_processos", sa.Column("cliente_nome", sa.String(length=200), nullable=True))
    op.add_column("bbd_processos", sa.Column("cliente_cpf_cnpj", sa.String(length=30), nullable=True))
    op.add_column("bbd_processos", sa.Column("cliente_tipo", sa.String(length=5), nullable=True))


def downgrade() -> None:
    op.drop_column("bbd_processos", "cliente_tipo")
    op.drop_column("bbd_processos", "cliente_cpf_cnpj")
    op.drop_column("bbd_processos", "cliente_nome")
