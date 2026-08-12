"""Rastro dos encerramentos executados no Legal One via Sistema de Encerramentos.

O endpoint de intake POST /api/v1/legalone/encerramento executava e respondia
sem deixar registro no Flow — o rastro ficava só no sistema de origem. Esta
tabela grava TODA chamada (sucesso, idempotente, CNJ não encontrado, conflito
e recusa do L1) para alimentar o menu "Encerramentos" da UI: a gestão enxerga
o que está sendo encerrado no Legal One via integração, por quem e com qual
desfecho, sem depender de log de container.

Revision ID: enc001_l1_intake
Revises: usr005_rbac_cargos
Create Date: 2026-07-30
"""
import sqlalchemy as sa
from alembic import op

revision = "enc001_l1_intake"
down_revision = "usr005_rbac_cargos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "encerramentos_l1_intake",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("numero_cnj", sa.String(length=40), nullable=False),
        sa.Column("lawsuit_id", sa.Integer(), nullable=True),
        # ok | ja_encerrado | nao_encontrado | conflito | erro_l1
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("data_encerramento", sa.String(length=10), nullable=True),
        sa.Column("motivo_encerramento", sa.Text(), nullable=True),
        sa.Column("operador_nome", sa.String(length=200), nullable=True),
        sa.Column("operador_email", sa.String(length=200), nullable=True),
        sa.Column("justificativa", sa.Text(), nullable=True),
        sa.Column("origem", sa.String(length=50), nullable=True),
        sa.Column("detalhe", sa.Text(), nullable=True),
    )
    op.create_index("ix_enc_l1_intake_cnj", "encerramentos_l1_intake", ["numero_cnj"])
    op.create_index("ix_enc_l1_intake_status", "encerramentos_l1_intake", ["status"])
    op.create_index("ix_enc_l1_intake_created", "encerramentos_l1_intake", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_enc_l1_intake_created", table_name="encerramentos_l1_intake")
    op.drop_index("ix_enc_l1_intake_status", table_name="encerramentos_l1_intake")
    op.drop_index("ix_enc_l1_intake_cnj", table_name="encerramentos_l1_intake")
    op.drop_table("encerramentos_l1_intake")
