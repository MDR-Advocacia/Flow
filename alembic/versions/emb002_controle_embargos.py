"""Controle de Embargos: casos de embargos à execução do BB Autor numa visão única
(emb_caso), publicações já lidas pelo controle (emb_caso_publicacao) e o vínculo
dos eventos com o caso (emb_evento.caso_id). Só aditiva.

Revision ID: emb002_controle_embargos
Revises: emb001_fluxo_embargos_execucao
"""

import sqlalchemy as sa
from alembic import op

revision = "emb002_controle_embargos"
down_revision = "emb001_fluxo_embargos_execucao"
branch_labels = None
depends_on = None


def _dt():
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "emb_caso",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cnj_embargos", sa.String(), nullable=True),
        sa.Column("cnj_embargos_digitos", sa.String(), nullable=True),
        sa.Column("execucao_id", sa.Integer(), sa.ForeignKey("emb_execucao.id", ondelete="SET NULL"), nullable=True),
        sa.Column("pasta_execucao", sa.String(), nullable=True),
        sa.Column("cnj_execucao", sa.String(), nullable=True),
        sa.Column("lawsuit_id_execucao", sa.Integer(), nullable=True),
        sa.Column("estado", sa.String(), nullable=False, server_default="PENDENTE"),
        sa.Column("falha_cadastro", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("vinculo_confirmado", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("origem_tribunal", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("origem_pub_sem_pasta", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("origem_pub_na_pasta", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("origem_pub_incidente", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("origem_l1", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("primeira_origem", sa.String(), nullable=True),
        sa.Column("embargante", sa.String(), nullable=True),
        sa.Column("candidato_id", sa.Integer(), nullable=True),
        sa.Column("tarefa_l1_id", sa.BigInteger(), nullable=True),
        sa.Column("detectado_em", _dt(), nullable=True),
        sa.Column("incidente_id", sa.Integer(), nullable=True),
        sa.Column("incidente_folder", sa.String(), nullable=True),
        sa.Column("cadastrado_em", _dt(), nullable=True),
        sa.Column("verificado_l1_em", _dt(), nullable=True),
        sa.Column("verificacoes_l1", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("vinculo_tentado_em", _dt(), nullable=True),
        sa.Column("descartado_motivo", sa.Text(), nullable=True),
        sa.Column("decidido_por_user_id", sa.Integer(), sa.ForeignKey("legal_one_users.id"), nullable=True),
        sa.Column("criado_em", _dt(), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", _dt(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_emb_caso_cnj_embargos_digitos", "emb_caso", ["cnj_embargos_digitos"], unique=True)
    op.create_index("ix_emb_caso_execucao_id", "emb_caso", ["execucao_id"])
    op.create_index("ix_emb_caso_pasta_execucao", "emb_caso", ["pasta_execucao"])
    op.create_index("ix_emb_caso_lawsuit_id_execucao", "emb_caso", ["lawsuit_id_execucao"])
    op.create_index("ix_emb_caso_estado", "emb_caso", ["estado"])
    op.create_index("ix_emb_caso_falha_cadastro", "emb_caso", ["falha_cadastro"])

    op.create_table(
        "emb_caso_publicacao",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("caso_id", sa.Integer(), sa.ForeignKey("emb_caso.id", ondelete="CASCADE"), nullable=True),
        sa.Column("publicacao_id", sa.Integer(), nullable=False),
        sa.Column("origem", sa.String(), nullable=False),
        sa.Column("data_publicacao", sa.String(), nullable=True),
        sa.Column("status_publicacao", sa.String(), nullable=True),
        sa.Column("linked_lawsuit_id", sa.Integer(), nullable=True),
        sa.Column("trecho", sa.Text(), nullable=True),
        sa.Column("criado_em", _dt(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_emb_caso_publicacao_caso_id", "emb_caso_publicacao", ["caso_id"])
    op.create_index("ix_emb_caso_publicacao_publicacao_id", "emb_caso_publicacao", ["publicacao_id"], unique=True)

    op.add_column(
        "emb_evento",
        sa.Column("caso_id", sa.Integer(), sa.ForeignKey("emb_caso.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_emb_evento_caso_id", "emb_evento", ["caso_id"])


def downgrade() -> None:
    op.drop_index("ix_emb_evento_caso_id", table_name="emb_evento")
    op.drop_column("emb_evento", "caso_id")
    op.drop_table("emb_caso_publicacao")
    op.drop_table("emb_caso")
