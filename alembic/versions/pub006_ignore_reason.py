"""pub006 — motivo estruturado da ciência (ignore) de publicação.

Estudo de 06/08/2026: 12.773 publicações ignoradas, ZERO com motivo
registrado — o critério vivia só na cabeça da equipe, e a taxa de ignore
varia de 20% a 41% entre operadores DO MESMO escritório. O motivo vira
campo escolhido na hora da ciência (chips no modal), transformando cada
ignore em dado de treino/auditoria de custo zero.

`ignore_reason` é slug curto ('ja_agendado', 'parte_adversa',
'informativa', 'classificacao_incorreta', 'outro');
`ignore_reason_note` é texto livre opcional (obrigatório só no 'outro',
regra da UI). Nullable porque o histórico não tem como ser reconstituído
e a ciência automática (tratamento web) não escolhe motivo.
"""
from alembic import op
import sqlalchemy as sa


revision = "pub006_ignore_reason"
down_revision = "uso001_registro_de_uso"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "publicacao_registros",
        sa.Column("ignore_reason", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "publicacao_registros",
        sa.Column("ignore_reason_note", sa.Text(), nullable=True),
    )
    # O estudo agrupa por motivo; sem índice vira varredura da tabela inteira
    # (68k registros e crescendo ~600/dia).
    op.create_index(
        "ix_publicacao_registros_ignore_reason",
        "publicacao_registros", ["ignore_reason"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_publicacao_registros_ignore_reason",
        table_name="publicacao_registros",
    )
    op.drop_column("publicacao_registros", "ignore_reason_note")
    op.drop_column("publicacao_registros", "ignore_reason")
