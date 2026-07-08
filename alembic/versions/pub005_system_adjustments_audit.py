"""Publicações: ajustes automáticos do sistema na auditoria de agendamento

Revision ID: pub005_system_adjustments
Revises: perf010_reatribuir_job
Create Date: 2026-07-08

Coluna JSONB que separa, por tarefa criada no L1, o que o SISTEMA mudou
mecanicamente antes do envio (bump de data pra dia útil, defaults
obrigatórios, corte de descrição) do override HUMANO — antes os dois
ficavam misturados na comparação proposta × enviado.
Formato: {campo: {antes, depois, motivo}}.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "pub005_system_adjustments"
down_revision = "perf010_reatribuir_job"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table):
        return False
    return any(col["name"] == column for col in inspector.get_columns(table))


def upgrade() -> None:
    if _has_column("publicacao_tarefa_audit", "system_adjustments"):
        return
    op.add_column(
        "publicacao_tarefa_audit",
        sa.Column("system_adjustments", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    if not _has_column("publicacao_tarefa_audit", "system_adjustments"):
        return
    op.drop_column("publicacao_tarefa_audit", "system_adjustments")
