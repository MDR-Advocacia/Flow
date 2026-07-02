"""Balanceador: job persistido de reatribuição EM LOTE (escrita real no L1)

Revision ID: perf010_reatribuir_job
Revises: rcr005_pontos_atencao
Create Date: 2026-07-02

Status da reatribuição em lote (Balanceador de Agenda) persistido em tabela pra
o polling funcionar com múltiplos workers do uvicorn. Buckets: reatribuidas /
workflow_bloqueadas / falhas. Idempotente.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "perf010_reatribuir_job"
down_revision = "rcr005_pontos_atencao"
branch_labels = None
depends_on = None


def _has_table(table: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table)


def upgrade() -> None:
    if _has_table("balanceador_reatribuir_job"):
        return
    op.create_table(
        "balanceador_reatribuir_job",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("team", sa.String(), nullable=True, index=True),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("feito", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reatribuidas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("workflow_bloqueadas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("falhas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detalhe", JSONB(), nullable=True),
        sa.Column("criado_por_id", sa.Integer(), nullable=True),
        sa.Column("criado_por_nome", sa.String(), nullable=True),
        sa.Column("iniciado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("terminado_em", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    if _has_table("balanceador_reatribuir_job"):
        op.drop_table("balanceador_reatribuir_job")
