"""pub008 — captura do conhecimento tácito do operador (4 pontos).

Nasce da reflexão do operador em 07/08/2026: "a leitura pura da publicação
não é suficiente, precisa do contexto — e muita decisão fica só na cabeça
dele". Estes campos existem pra tirar da cabeça e botar no banco.

O que a medição mostrou sobre ONDE o conhecimento evapora:

  1. DATA — 72% dos agendamentos mudam a data proposta (33% adiam, 28%
     antecipam) e isso NÃO era auditado: o `override_fields` só olhava
     subtipo, responsável e escritório. É o maior vazamento do sistema, e é
     onde vive a regra de prazo que o operador descreveu (contestação 2 dias
     antes, próximo dia útil quando não há prazo explícito).
  2. AUTOS — quando o operador precisa abrir o processo pra decidir. É O
     sinal do balde OPERADOR: hoje eu adivinho por heurística de texto, com
     este campo passo a saber.
  3. AGENDOU MESMO COM TAREFA ABERTA — o inverso do chip de ignorar: diz que
     a tarefa existente NÃO cobre esta publicação. Ensina a distinção
     semântica que nenhum casamento estrutural alcança (foi exatamente a
     hipótese que o backtest derrubou).
  4. REMOVEU TAREFA PROPOSTA — o template propôs demais naquele caso;
     calibra a regra de dupla tarefa criada em 06/08.

Regra de atrito (decisão do operador): chip só aparece no DESVIO ANÔMALO —
data fora de ±3 dias, e não em toda mudança. Quem faz 267 decisões/dia não
pode ser interrogado no rotineiro.
"""
from alembic import op
import sqlalchemy as sa


revision = "pub008_captura_conhecimento"
down_revision = "pub007_shadow_decisao"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Delta de data em DIAS, gravado sempre (custo zero de atrito) — negativo
    # = operador antecipou, positivo = adiou. Auditar antes de perguntar:
    # metade do valor já vem do número, sem incomodar ninguém.
    op.add_column("publicacao_tarefa_audit",
                  sa.Column("data_delta_dias", sa.Integer(), nullable=True))
    # Motivo, só pedido no desvio grande.
    op.add_column("publicacao_tarefa_audit",
                  sa.Column("data_troca_motivo", sa.String(length=40), nullable=True))
    # "Precisei abrir o processo pra decidir" — marcado pelo operador.
    op.add_column("publicacao_tarefa_audit",
                  sa.Column("consultou_autos", sa.Boolean(), nullable=True))
    # Agendou apesar de existir tarefa da família aberta na pasta.
    op.add_column("publicacao_tarefa_audit",
                  sa.Column("agendou_com_tarefa_aberta_motivo",
                            sa.String(length=40), nullable=True))
    # Removeu um bloco de tarefa que o template propunha.
    op.add_column("publicacao_tarefa_audit",
                  sa.Column("tarefa_removida_motivo",
                            sa.String(length=40), nullable=True))

    # O estudo agrupa por motivo e por faixa de delta.
    op.create_index("ix_pub_audit_data_delta",
                    "publicacao_tarefa_audit", ["data_delta_dias"])

    # Idem no ignorar: "precisei abrir o processo" também vale quando a
    # decisão foi ignorar (o operador consultou e concluiu que não há o que
    # fazer) — sem isto o sinal ficaria capenga, só do lado do agendamento.
    op.add_column("publicacao_registros",
                  sa.Column("consultou_autos", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("publicacao_registros", "consultou_autos")
    op.drop_index("ix_pub_audit_data_delta", table_name="publicacao_tarefa_audit")
    for col in ("tarefa_removida_motivo", "agendou_com_tarefa_aberta_motivo",
                "consultou_autos", "data_troca_motivo", "data_delta_dias"):
        op.drop_column("publicacao_tarefa_audit", col)
