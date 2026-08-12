"""Distribuídos BB: tabelas de config editável (regras da planilha antiga)

Revision ID: bbd002_config_editavel
Revises: bbd001_distribuidos_bb
Create Date: 2026-07-09

Tabela as regras que estavam hardcoded no gerar_planilha.py, pra edição na
tela administrativa do módulo:
  - bbd_classificacoes (catálogo de posição/classificação de envolvido)
  - bbd_regras_observacao (condição → texto da Observação)
  - bbd_grupos_ajuizamento + bbd_grupo_ajuizamento_membros (duplas alternadas)
  - bbd_config (valores padrão key/value: cliente BB, tipos, etc.)

Idempotente.
"""
from alembic import op
import sqlalchemy as sa


revision = "bbd002_config_editavel"
down_revision = "bbd001_distribuidos_bb"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("bbd_classificacoes"):
        op.create_table(
            "bbd_classificacoes",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("nome", sa.String(length=80), nullable=False),
            sa.Column("situacao", sa.String(length=40), server_default="Outros", nullable=True),
            sa.Column("participante_tipo", sa.String(length=20), nullable=True),
            sa.Column("position_id_l1", sa.Integer(), nullable=True),
            sa.Column("ativo", sa.Boolean(), server_default="true", nullable=False),
            sa.Column("ordem", sa.Integer(), server_default="0", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_bbd_classificacoes_id", "bbd_classificacoes", ["id"])

    if not _has_table("bbd_regras_observacao"):
        op.create_table(
            "bbd_regras_observacao",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("nome", sa.String(length=120), nullable=False),
            sa.Column("criterio_posicao", sa.String(length=20), nullable=True),
            sa.Column("criterio_natureza", sa.String(length=80), nullable=True),
            sa.Column("criterio_cnj", sa.String(length=10), nullable=True),
            sa.Column("texto", sa.String(length=120), nullable=False),
            sa.Column("ativo", sa.Boolean(), server_default="true", nullable=False),
            sa.Column("ordem", sa.Integer(), server_default="0", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_bbd_regras_observacao_id", "bbd_regras_observacao", ["id"])

    if not _has_table("bbd_grupos_ajuizamento"):
        op.create_table(
            "bbd_grupos_ajuizamento",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("nome", sa.String(length=120), nullable=False),
            sa.Column("ativo", sa.Boolean(), server_default="true", nullable=False),
            sa.Column("ordem", sa.Integer(), server_default="0", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_bbd_grupos_ajuizamento_id", "bbd_grupos_ajuizamento", ["id"])

    if not _has_table("bbd_grupo_ajuizamento_membros"):
        op.create_table(
            "bbd_grupo_ajuizamento_membros",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("grupo_id", sa.Integer(), nullable=False),
            sa.Column("membro_user_id", sa.Integer(), nullable=False),
            sa.Column("classificacao", sa.String(length=80), nullable=False),
            sa.Column("ordem", sa.Integer(), server_default="0", nullable=False),
            sa.Column("ativo", sa.Boolean(), server_default="true", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["grupo_id"], ["bbd_grupos_ajuizamento.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["membro_user_id"], ["legal_one_users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_bbd_grupo_ajuizamento_membros_id", "bbd_grupo_ajuizamento_membros", ["id"])
        op.create_index("ix_bbd_grupo_ajuizamento_membros_grupo_id", "bbd_grupo_ajuizamento_membros", ["grupo_id"])
        op.create_index("ix_bbd_grupo_ajuizamento_membros_membro_user_id", "bbd_grupo_ajuizamento_membros", ["membro_user_id"])

    if not _has_table("bbd_config"):
        op.create_table(
            "bbd_config",
            sa.Column("chave", sa.String(length=60), nullable=False),
            sa.Column("valor", sa.Text(), nullable=True),
            sa.Column("descricao", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("chave"),
        )


def downgrade() -> None:
    for tbl in (
        "bbd_config",
        "bbd_grupo_ajuizamento_membros",
        "bbd_grupos_ajuizamento",
        "bbd_regras_observacao",
        "bbd_classificacoes",
    ):
        if _has_table(tbl):
            op.drop_table(tbl)
