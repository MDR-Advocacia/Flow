"""pub011 - aquecimento de cache em DUAS FASES, como a doc da Batches API manda.

POR QUE ISTO EXISTE
-------------------
O prompt caching entrou em 6bb7c66 aquecendo o prefixo com chamadas SINCRONAS
antes de submeter o lote. A telemetria da pub009 mostrou que isso nao funciona:
mesmo com 8/8 prefixos aquecidos com sucesso (log de 19/08), o lote gravava
milhoes de tokens em vez de ler. Resultado medido nos lotes 147-149: o cache
saiu MAIS CARO que nao cachear (US$ 152/mes contra US$ 138).

A doc da Anthropic prescreve, para a Batches API, uma receita diferente:

    - Junte as requisicoes que compartilham o prefixo.
    - Mande UM batch com uma requisicao por prefixo e bloco de cache.
    - Espere esse batch CONCLUIR.
    - So entao submeta o resto.

O detalhe que quebrava tudo: entrada de cache so fica visivel depois que a
PRIMEIRA RESPOSTA COMECA. Aquecer por fora do batch nao satisfaz isso para os
workers do batch; as centenas de requisicoes partem concorrentes, erram juntas
e gravam juntas. Esperar um batch de aquecimento CONCLUIR e' o que garante a
entrada visivel.

O QUE MUDA NO ESQUEMA
---------------------
Esperar significa que o envio deixa de ser um passo unico: o lote nasce
AQUECENDO (com o id do batch de aquecimento) e so vira ENVIADO quando o
aquecimento conclui. Como o endpoint HTTP nao pode ficar minutos bloqueado, o
estado precisa ser persistido e promovido depois - dai estas colunas.

Colunas nullable: lote antigo fica com NULL e nao e' afetado.
"""
from alembic import op
import sqlalchemy as sa


revision = "pub011_aquecimento_em_lote"
down_revision = "arb001_analise_risco_bb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Id do batch de AQUECIMENTO na Anthropic (uma requisicao por prefixo).
    # Distinto de anthropic_batch_id, que e' o lote real de classificacao.
    op.add_column(
        "publicacao_batches_classificacao",
        sa.Column("warm_batch_id", sa.String(), nullable=True),
    )
    # Quando o aquecimento foi disparado. Serve pro guarda de tempo: se o
    # aquecimento nao concluir dentro da janela, o lote segue SEM cache em vez
    # de ficar presos esperando - fila parada e' pior que lote caro.
    op.add_column(
        "publicacao_batches_classificacao",
        sa.Column("warm_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "publicacao_batches_classificacao",
        sa.Column("warm_ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Quantos prefixos distintos o aquecimento cobriu (observabilidade: com
    # este numero da' pra conferir se as gravacoes do lote real batem com o
    # esperado, que e' ~zero).
    op.add_column(
        "publicacao_batches_classificacao",
        sa.Column("warm_prefixos", sa.Integer(), nullable=True),
    )
    # O poller busca por status; indice parcial mantem barato varrer os que
    # estao aquecendo sem pesar a tabela inteira.
    op.create_index(
        "ix_pub_batch_aquecendo",
        "publicacao_batches_classificacao",
        ["status"],
        postgresql_where=sa.text("status = 'AQUECENDO'"),
    )


def downgrade() -> None:
    op.drop_index("ix_pub_batch_aquecendo", table_name="publicacao_batches_classificacao")
    op.drop_column("publicacao_batches_classificacao", "warm_prefixos")
    op.drop_column("publicacao_batches_classificacao", "warm_ended_at")
    op.drop_column("publicacao_batches_classificacao", "warm_started_at")
    op.drop_column("publicacao_batches_classificacao", "warm_batch_id")
