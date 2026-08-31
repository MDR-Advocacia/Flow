"""Transição do vínculo passa a ser EXECUTADA pelo Flow, não só marcada.

No cenário 1 o processo novo vai pra Equipe Mista e os processos antigos da
mesma parte ficam `transicao_pendente` — até aqui o painel só oferecia
"marcar como concluída", ou seja, o supervisor trocava o responsável na mão
no Legal One e vinha registrar. Agora o botão TROCA de verdade, pelo mesmo
`ModalChangeInvolvedInBatch` já validado em 108 pastas (28/08/2026, doc em
docs/legalone-trocar-responsavel-pasta.md).

Duas colunas pra que o resultado sobreviva ao refresh da página:
- `transicao_para_user_id`: pra QUEM a pasta foi (a advogada da Equipe Mista
  que ficou com o processo novo). Sem isso, depois de concluída não dá pra
  saber o destino sem garimpar evento;
- `transicao_erro`: por que falhou. A falha mantém `transicao_pendente=True`
  (o item continua na fila), mas o operador precisa ver o motivo — pasta sem
  id no L1, POST recusado, divergência na releitura.

Só adiciona colunas nullable; nenhum dado existente muda.

Revision ID: bbd020
Revises: pub011_aquecimento_em_lote
"""
from alembic import op
import sqlalchemy as sa

revision = "bbd020"
down_revision = "pub011_aquecimento_em_lote"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bbd_vinculos",
        sa.Column("transicao_para_user_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_bbd_vinculos_transicao_para_user",
        "bbd_vinculos",
        "legal_one_users",
        ["transicao_para_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("bbd_vinculos", sa.Column("transicao_erro", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("bbd_vinculos", "transicao_erro")
    op.drop_constraint("fk_bbd_vinculos_transicao_para_user", "bbd_vinculos", type_="foreignkey")
    op.drop_column("bbd_vinculos", "transicao_para_user_id")
