"""Vínculos da parte (processos em comum com o MDR) + Equipe Mista Especializada.

Nova função do RPA (2026-07-20): no ato da captura, pesquisar no portal BB se as
partes do processo novo têm OUTRAS ações ativas conduzidas pelo MDR. Quando têm:
- o processo novo mantém o escritório da distribuição padrão (Réu/Autor), mas o
  RESPONSÁVEL vem da fila "Equipe Mista Especializada";
- cenário 1 (antigo fora da equipe) → antigo sinalizado pra transição manual;
- cenário 2 (parte já especializada) → novo vai pro MESMO responsável.

Cria:
1) tabela `bbd_vinculos` (os processos vinculados encontrados, por processo);
2) colunas de resumo em `bbd_processos` (cenário, qtd, verificado_em);
3) o escritório-fila "Equipe Mista Especializada" — SEM critérios de roteamento
   (nunca recebe pelo rodízio por polo/natureza; é só a fila de responsáveis que
   o operador vai popular na tela Escritórios & Filas).

Revision ID: bbd014_vinculos_equipe_mista
Revises: bbd013_pasta_avulsa_cliente
Create Date: 2026-07-20
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "bbd014_vinculos_equipe_mista"
down_revision = "bbd013_pasta_avulsa_cliente"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bbd_vinculos",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("processo_id", sa.Integer(),
                  sa.ForeignKey("bbd_processos.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("envolvido_id", sa.Integer(),
                  sa.ForeignKey("bbd_envolvidos.id", ondelete="SET NULL"),
                  nullable=True, index=True),
        sa.Column("doc_parte", sa.String(length=20), nullable=True, index=True),
        sa.Column("nome_parte", sa.Text(), nullable=True),
        sa.Column("numero_pessoa", sa.Integer(), nullable=True),
        sa.Column("npj", sa.String(length=30), nullable=False, index=True),
        sa.Column("numero_processo", sa.String(length=20), nullable=True),
        sa.Column("cnj", sa.String(length=30), nullable=True, index=True),
        sa.Column("contrario_nome", sa.Text(), nullable=True),
        sa.Column("advogado_bb", sa.String(length=120), nullable=True),
        sa.Column("situacao", sa.String(length=60), nullable=True),
        sa.Column("natureza", sa.String(length=40), nullable=True),
        sa.Column("uja", sa.Integer(), nullable=True),
        sa.Column("polo", sa.String(length=10), nullable=True),
        sa.Column("posicao_banco", sa.String(length=10), nullable=True),
        sa.Column("responsavel_atual_user_id", sa.Integer(),
                  sa.ForeignKey("legal_one_users.id", ondelete="SET NULL"),
                  nullable=True, index=True),
        sa.Column("responsavel_atual_nome", sa.String(length=160), nullable=True),
        sa.Column("na_equipe_mista", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("transicao_pendente", sa.Boolean(), nullable=False,
                  server_default="false", index=True),
        sa.Column("transicao_concluida_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column("bbd_processos", sa.Column("vinculo_cenario", sa.String(length=12), nullable=True))
    op.create_index("ix_bbd_processos_vinculo_cenario", "bbd_processos", ["vinculo_cenario"])
    op.add_column("bbd_processos", sa.Column("vinculos_qtd", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("bbd_processos", sa.Column("vinculos_verificado_em", sa.DateTime(timezone=True), nullable=True))

    # Fila da equipe especializada — sem critérios (não entra no roteamento
    # padrão); o operador cadastra os responsáveis na tela. Idempotente.
    op.execute(
        """
        INSERT INTO bbd_escritorios
            (nome, escritorio_path, ativo, ordem, created_at)
        SELECT 'Equipe Mista Especializada',
               'Fila de responsáveis — o escritório do processo segue a distribuição padrão',
               true, 95, now()
        WHERE NOT EXISTS (
            SELECT 1 FROM bbd_escritorios WHERE nome = 'Equipe Mista Especializada'
        )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM bbd_escritorios WHERE nome = 'Equipe Mista Especializada'")
    op.drop_column("bbd_processos", "vinculos_verificado_em")
    op.drop_column("bbd_processos", "vinculos_qtd")
    op.drop_index("ix_bbd_processos_vinculo_cenario", table_name="bbd_processos")
    op.drop_column("bbd_processos", "vinculo_cenario")
    op.drop_table("bbd_vinculos")
