"""pub007 — shadow mode do tratamento de publicações + justificativa de subtipo.

DUAS coisas que existem pela mesma razão: medir automação com dado real em
vez de estimativa.

1) `publicacao_shadow_decisao` — a previsão do sistema gravada ANTES do
   operador agir, com os sinais CONGELADOS no instante da previsão. Congelar
   é o ponto: "existe tarefa aberta da mesma família" muda o tempo todo, então
   recalcular depois responderia outra pergunta e inflaria a acurácia
   artificialmente. Mesma lição do índice de escritório desatualizado (05/08).

2) `publicacao_tarefa_audit.subtipo_troca_motivo` — por que o operador trocou
   o subtipo proposto. Captura ESTRATÉGICA: medido em 06/08, trocar subtipo
   são ~51 casos/dia (atrito aceitável) enquanto trocar responsável são ~224
   (inviável — e é rotina de distribuição de carga, não dúvida). Pedir
   justificativa onde há dúvida real, nunca onde há rotina.
"""
from alembic import op
import sqlalchemy as sa

from app.db.types import jsonb


revision = "pub007_shadow_decisao"
down_revision = "pub006_ignore_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "publicacao_shadow_decisao",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("record_id", sa.Integer(), nullable=False, index=True),
        # ── previsão (travada antes da ação humana) ──
        sa.Column("previsto", sa.String(length=20), nullable=False),
        sa.Column("previsto_motivo", sa.String(length=40), nullable=True),
        sa.Column("confianca", sa.String(length=10), nullable=False),
        sa.Column("regra", sa.String(length=60), nullable=True),
        # Sinais no INSTANTE da previsão — congelados de propósito.
        sa.Column("sinais", jsonb(), nullable=True),
        sa.Column("previsto_em", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        # ── desfecho real (preenchido quando o operador age) ──
        sa.Column("real", sa.String(length=20), nullable=True),
        sa.Column("real_motivo", sa.String(length=40), nullable=True),
        sa.Column("real_por", sa.String(length=120), nullable=True),
        sa.Column("real_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acertou", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(["record_id"], ["publicacao_registros.id"],
                                ondelete="CASCADE"),
    )
    # Uma previsão por publicação — a segunda tentativa vira no-op, senão um
    # reprocessamento duplicaria linhas e distorceria o placar.
    op.create_index("ux_shadow_record", "publicacao_shadow_decisao",
                    ["record_id"], unique=True)
    # O placar corta por "já tem desfecho" e por período.
    op.create_index("ix_shadow_real_em", "publicacao_shadow_decisao",
                    ["real_em"])

    op.add_column(
        "publicacao_tarefa_audit",
        sa.Column("subtipo_troca_motivo", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("publicacao_tarefa_audit", "subtipo_troca_motivo")
    op.drop_index("ix_shadow_real_em", table_name="publicacao_shadow_decisao")
    op.drop_index("ux_shadow_record", table_name="publicacao_shadow_decisao")
    op.drop_table("publicacao_shadow_decisao")
