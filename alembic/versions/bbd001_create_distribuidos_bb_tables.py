"""Distribuídos BB: tabelas do módulo (config editável + dados + log/auditoria)

Revision ID: bbd001_distribuidos_bb
Revises: pub005_system_adjustments
Create Date: 2026-07-09

Cria as 8 tabelas do módulo Distribuídos BB (prefixo bbd_):
  - Config editável: bbd_escritorios, bbd_responsaveis, bbd_equipe_membros,
    bbd_distribuicao_estado
  - Dados: bbd_runs, bbd_processos, bbd_envolvidos
  - Log/auditoria universal: bbd_eventos

Idempotente: cada create_table só roda se a tabela ainda não existir.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "bbd001_distribuidos_bb"
down_revision = "pub005_system_adjustments"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return insp.has_table(name)


def upgrade() -> None:
    if not _has_table("bbd_escritorios"):
        op.create_table(
            "bbd_escritorios",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("nome", sa.String(length=120), nullable=False),
            sa.Column("escritorio_path", sa.Text(), nullable=False),
            sa.Column("criterio_polo", sa.String(length=20), nullable=True),
            sa.Column("criterio_natureza", sa.String(length=80), nullable=True),
            sa.Column("responsavel_fixo_user_id", sa.Integer(), nullable=True),
            sa.Column("observacao_padrao", sa.String(length=40), nullable=True),
            sa.Column("ativo", sa.Boolean(), server_default="true", nullable=False),
            sa.Column("ordem", sa.Integer(), server_default="0", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["responsavel_fixo_user_id"], ["legal_one_users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_bbd_escritorios_id", "bbd_escritorios", ["id"])
        op.create_index("ix_bbd_escritorios_responsavel_fixo_user_id", "bbd_escritorios", ["responsavel_fixo_user_id"])

    if not _has_table("bbd_responsaveis"):
        op.create_table(
            "bbd_responsaveis",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("escritorio_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("ordem", sa.Integer(), server_default="0", nullable=False),
            sa.Column("ativo", sa.Boolean(), server_default="true", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["escritorio_id"], ["bbd_escritorios.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["legal_one_users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("escritorio_id", "user_id", name="uq_bbd_resp_escritorio_user"),
        )
        op.create_index("ix_bbd_responsaveis_id", "bbd_responsaveis", ["id"])
        op.create_index("ix_bbd_responsaveis_escritorio_id", "bbd_responsaveis", ["escritorio_id"])
        op.create_index("ix_bbd_responsaveis_user_id", "bbd_responsaveis", ["user_id"])

    if not _has_table("bbd_equipe_membros"):
        op.create_table(
            "bbd_equipe_membros",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("responsavel_user_id", sa.Integer(), nullable=False),
            sa.Column("membro_user_id", sa.Integer(), nullable=False),
            sa.Column("classificacao", sa.String(length=80), nullable=False),
            sa.Column("ativo", sa.Boolean(), server_default="true", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["responsavel_user_id"], ["legal_one_users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["membro_user_id"], ["legal_one_users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("responsavel_user_id", "membro_user_id", "classificacao", name="uq_bbd_equipe_membro"),
        )
        op.create_index("ix_bbd_equipe_membros_id", "bbd_equipe_membros", ["id"])
        op.create_index("ix_bbd_equipe_membros_responsavel_user_id", "bbd_equipe_membros", ["responsavel_user_id"])
        op.create_index("ix_bbd_equipe_membros_membro_user_id", "bbd_equipe_membros", ["membro_user_id"])

    if not _has_table("bbd_distribuicao_estado"):
        op.create_table(
            "bbd_distribuicao_estado",
            sa.Column("escritorio_id", sa.Integer(), nullable=False),
            sa.Column("ultimo_responsavel_id", sa.Integer(), nullable=True),
            sa.Column("ultimo_indice", sa.Integer(), server_default="-1", nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["escritorio_id"], ["bbd_escritorios.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["ultimo_responsavel_id"], ["legal_one_users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("escritorio_id"),
        )

    if not _has_table("bbd_runs"):
        op.create_table(
            "bbd_runs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("data_inicial", sa.String(length=10), nullable=True),
            sa.Column("data_final", sa.String(length=10), nullable=True),
            sa.Column("disparado_por_user_id", sa.Integer(), nullable=True),
            sa.Column("confirmar_ciencia", sa.Boolean(), server_default="false", nullable=False),
            sa.Column("status", sa.String(), server_default="EM_ANDAMENTO", nullable=False),
            sa.Column("total_coletados", sa.Integer(), server_default="0", nullable=False),
            sa.Column("total_ciencia", sa.Integer(), server_default="0", nullable=False),
            sa.Column("total_distribuidos", sa.Integer(), server_default="0", nullable=False),
            sa.Column("total_cadastrados", sa.Integer(), server_default="0", nullable=False),
            sa.Column("total_erros", sa.Integer(), server_default="0", nullable=False),
            sa.Column("iniciado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("concluido_em", sa.DateTime(timezone=True), nullable=True),
            sa.Column("erro", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["disparado_por_user_id"], ["legal_one_users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_bbd_runs_id", "bbd_runs", ["id"])
        op.create_index("ix_bbd_runs_disparado_por_user_id", "bbd_runs", ["disparado_por_user_id"])
        op.create_index("ix_bbd_runs_status", "bbd_runs", ["status"])

    if not _has_table("bbd_processos"):
        op.create_table(
            "bbd_processos",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("run_id", sa.Integer(), nullable=True),
            sa.Column("cnj", sa.String(length=40), nullable=True),
            sa.Column("npj", sa.String(length=60), nullable=True),
            sa.Column("notificacao_seq", sa.Integer(), nullable=True),
            sa.Column("fingerprint", sa.String(length=80), nullable=False),
            sa.Column("polo", sa.String(length=20), nullable=True),
            sa.Column("posicao", sa.String(length=20), nullable=True),
            sa.Column("natureza", sa.String(length=120), nullable=True),
            sa.Column("acao", sa.Text(), nullable=True),
            sa.Column("valor_causa", sa.Numeric(precision=18, scale=2), nullable=True),
            sa.Column("data_ajuizamento", sa.String(length=30), nullable=True),
            sa.Column("situacao", sa.String(length=120), nullable=True),
            sa.Column("tramitacao", sa.String(length=120), nullable=True),
            sa.Column("advogado", sa.Text(), nullable=True),
            sa.Column("adverso_principal", sa.Text(), nullable=True),
            sa.Column("responsavel_user_id", sa.Integer(), nullable=True),
            sa.Column("escritorio_id", sa.Integer(), nullable=True),
            sa.Column("escritorio_path", sa.Text(), nullable=True),
            sa.Column("observacao", sa.String(length=40), nullable=True),
            sa.Column("status", sa.String(), server_default="COLETADO", nullable=False),
            sa.Column("ciencia_dada_em", sa.DateTime(timezone=True), nullable=True),
            sa.Column("l1_lawsuit_id", sa.Integer(), nullable=True),
            sa.Column("l1_workflow_task_id", sa.Integer(), nullable=True),
            sa.Column("erro", sa.Text(), nullable=True),
            sa.Column("raw", JSONB(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["run_id"], ["bbd_runs.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["responsavel_user_id"], ["legal_one_users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["escritorio_id"], ["bbd_escritorios.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("fingerprint", name="uq_bbd_proc_fingerprint"),
        )
        op.create_index("ix_bbd_processos_id", "bbd_processos", ["id"])
        op.create_index("ix_bbd_processos_run_id", "bbd_processos", ["run_id"])
        op.create_index("ix_bbd_processos_cnj", "bbd_processos", ["cnj"])
        op.create_index("ix_bbd_processos_npj", "bbd_processos", ["npj"])
        op.create_index("ix_bbd_processos_fingerprint", "bbd_processos", ["fingerprint"])
        op.create_index("ix_bbd_processos_responsavel_user_id", "bbd_processos", ["responsavel_user_id"])
        op.create_index("ix_bbd_processos_escritorio_id", "bbd_processos", ["escritorio_id"])
        op.create_index("ix_bbd_processos_status", "bbd_processos", ["status"])
        op.create_index("ix_bbd_processos_l1_lawsuit_id", "bbd_processos", ["l1_lawsuit_id"])

    if not _has_table("bbd_envolvidos"):
        op.create_table(
            "bbd_envolvidos",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("processo_id", sa.Integer(), nullable=False),
            sa.Column("nome", sa.Text(), nullable=False),
            sa.Column("papel", sa.String(length=80), nullable=True),
            sa.Column("cpf_cnpj", sa.String(length=20), nullable=True),
            sa.Column("tipo_pessoa", sa.String(length=2), nullable=True),
            sa.Column("status_contato", sa.String(), server_default="NAO_RESOLVIDO", nullable=False),
            sa.Column("l1_contact_id", sa.Integer(), nullable=True),
            sa.Column("raw", JSONB(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["processo_id"], ["bbd_processos.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_bbd_envolvidos_id", "bbd_envolvidos", ["id"])
        op.create_index("ix_bbd_envolvidos_processo_id", "bbd_envolvidos", ["processo_id"])
        op.create_index("ix_bbd_envolvidos_cpf_cnpj", "bbd_envolvidos", ["cpf_cnpj"])
        op.create_index("ix_bbd_envolvidos_status_contato", "bbd_envolvidos", ["status_contato"])
        op.create_index("ix_bbd_envolvidos_l1_contact_id", "bbd_envolvidos", ["l1_contact_id"])

    if not _has_table("bbd_eventos"):
        op.create_table(
            "bbd_eventos",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("run_id", sa.Integer(), nullable=True),
            sa.Column("processo_id", sa.Integer(), nullable=True),
            sa.Column("secao", sa.String(length=40), nullable=False),
            sa.Column("acao", sa.String(length=120), nullable=True),
            sa.Column("nivel", sa.String(length=12), server_default="INFO", nullable=False),
            sa.Column("mensagem", sa.Text(), nullable=False),
            sa.Column("dados", JSONB(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["bbd_runs.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["processo_id"], ["bbd_processos.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_bbd_eventos_id", "bbd_eventos", ["id"])
        op.create_index("ix_bbd_eventos_run_id", "bbd_eventos", ["run_id"])
        op.create_index("ix_bbd_eventos_processo_id", "bbd_eventos", ["processo_id"])
        op.create_index("ix_bbd_eventos_secao", "bbd_eventos", ["secao"])
        op.create_index("ix_bbd_eventos_nivel", "bbd_eventos", ["nivel"])
        op.create_index("ix_bbd_eventos_created_at", "bbd_eventos", ["created_at"])


def downgrade() -> None:
    for tbl in (
        "bbd_eventos",
        "bbd_envolvidos",
        "bbd_processos",
        "bbd_runs",
        "bbd_distribuicao_estado",
        "bbd_equipe_membros",
        "bbd_responsaveis",
        "bbd_escritorios",
    ):
        if _has_table(tbl):
            op.drop_table(tbl)
