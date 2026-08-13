"""Registra o usage (tokens) dos lotes de classificacao de publicacoes.

Hoje ninguem grava input_tokens/output_tokens em lugar nenhum do classificador,
entao nao ha como saber quanto a classificacao custa — nem como PROVAR ganho
depois de ligar o prompt caching. Estas colunas guardam os contadores crus que
a Anthropic devolve por item do batch, somados por lote.

Guardamos TOKEN, nao custo: token e' fato e nao envelhece; preco muda e fica
numa tabela editavel (app/services/classifier/anthropic_pricing.py).

`usage_itens_contados` existe pra distinguir soma completa de soma parcial —
item que falhou pode nao trazer usage, e sem esse contador a soma pareceria
menor sem explicacao.

Revision ID: pub009_usage_classificacao
Revises: onb001_onenotify_bb
"""
import sqlalchemy as sa
from alembic import op

revision = "pub009_usage_classificacao"
down_revision = "onb001_onenotify_bb"
branch_labels = None
depends_on = None

TABELA = "publicacao_batches_classificacao"

# BigInteger: um lote de 600 publicacoes com prefixo de ~10k tokens ja' passa
# de 6 milhoes de tokens de entrada. Integer aguentaria, mas o acumulado de
# relatorio futuro (soma de meses) nao.
COLUNAS = (
    ("usage_input_tokens", sa.BigInteger()),
    ("usage_output_tokens", sa.BigInteger()),
    ("usage_cache_read_tokens", sa.BigInteger()),
    ("usage_cache_creation_tokens", sa.BigInteger()),
    ("usage_itens_contados", sa.Integer()),
)


def upgrade() -> None:
    for nome, tipo in COLUNAS:
        op.add_column(TABELA, sa.Column(nome, tipo, nullable=True))


def downgrade() -> None:
    for nome, _ in reversed(COLUNAS):
        op.drop_column(TABELA, nome)
