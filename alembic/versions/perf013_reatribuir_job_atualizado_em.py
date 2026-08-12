"""perf013 — `atualizado_em` no job de reatribuição do Balanceador.

Sem esta coluna não dá pra distinguir "execução rodando devagar" de "execução
morta": a tabela só tinha `iniciado_em` e `terminado_em`, então a detecção de
zumbi usava tempo desde o INÍCIO — um proxy ruim, que erra dos dois lados.

Caso real de 04/08/2026: a execução do Bruno (541 tarefas) morreu no redeploy
às 08:30 e continuou aparecendo "Em andamento · 100%" na tela, porque o corte
era de 2h desde o início. E, pelo mesmo critério, uma execução legítima de 541
tarefas — que leva ~68 min no throttle real de 7,6s/tarefa — corria o risco de
ser morta injustamente se passasse do corte.

Com `atualizado_em` tocado a cada tarefa (o job commita por tarefa), a regra
passa a ser tempo SEM PROGRESSO, que é o sinal correto.

Backfill: linhas antigas recebem `iniciado_em` (o melhor palpite disponível) —
as concluídas não são afetadas pela detecção de qualquer forma.
"""
from alembic import op
import sqlalchemy as sa


revision = "perf013_reatribuir_atualizado_em"
down_revision = "enc002_perm_encerramentos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "balanceador_reatribuir_job",
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=True),
    )
    # Sem isto, todo job antigo ficaria com NULL e a detecção teria que tratar
    # o caso especial pra sempre.
    op.execute(
        "UPDATE balanceador_reatribuir_job "
        "SET atualizado_em = COALESCE(terminado_em, iniciado_em) "
        "WHERE atualizado_em IS NULL"
    )
    # Só as execuções vivas são varridas pela detecção de zumbi.
    op.create_index(
        "ix_balanceador_job_status_atualizado",
        "balanceador_reatribuir_job",
        ["status", "atualizado_em"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_balanceador_job_status_atualizado",
        table_name="balanceador_reatribuir_job",
    )
    op.drop_column("balanceador_reatribuir_job", "atualizado_em")
