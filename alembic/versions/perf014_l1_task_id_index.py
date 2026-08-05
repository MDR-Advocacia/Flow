"""perf014 — índice em `perf_l1_tarefa.l1_task_id`.

O recorte "origem Publicações" do Balanceador casa o snapshot com a auditoria
de agendamento por esse campo (`pub.created_task_id = t.l1_task_id`), e o
`live_pessoa` também consulta por ele (`l1_task_id = ANY(:ids)`). A tabela não
tinha índice nenhum aí — as buscas caíam em varredura.

Medido em produção antes do índice (253 mil linhas): o diagnóstico saía de
11 ms para 22 ms com o join. Aceitável hoje, mas o snapshot só cresce, e essa
query roda a cada abertura da tela.

Não é único de propósito: o snapshot é reingerido por replace e, durante a
troca, a mesma tarefa pode aparecer transitoriamente mais de uma vez.
"""
from alembic import op


revision = "perf014_l1_task_id_index"
down_revision = "perf013_reatribuir_atualizado_em"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_perf_tarefa_l1_task_id", "perf_l1_tarefa", ["l1_task_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_perf_tarefa_l1_task_id", table_name="perf_l1_tarefa")
