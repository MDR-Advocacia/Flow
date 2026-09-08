"""pub015 - guarda o PID do runner do Tratamento Web.

POR QUE ISTO EXISTE
-------------------
Incidente de 08/09/2026: o container da API ficou sem PIDs (300/300),
`RuntimeError: can't start new thread` em toda requisicao, e as operadoras
viram o modulo de publicacoes "lento e com erros diversos". Quem ocupava as
300 eram runners do Tratamento Web pendurados havia DIAS, cada um segurando
6 processos de Chrome.

O reaper de zumbi (`recover_stale_runs`) marcava a execucao como FALHA e
devolvia os itens pra fila — mas nunca matava o processo, porque o comentario
dele partia de uma premissa errada: "o processo Playwright provavelmente
morreu (OOM/restart/deploy)". Nao morreu: estava vivo e travado. O banco
dizia FALHA, o Chrome seguia comendo PID.

Sem saber o PID nao da' pra matar. Esta coluna e' o que faltava.

O QUE MUDA NO ESQUEMA
---------------------
`publicacao_tratamento_execucoes.runner_pid` (INTEGER, nullable): PID do
processo Node. Como o runner passa a nascer em sessao propria
(`start_new_session=True`), esse mesmo numero e' o PGID — matar o grupo leva
o Node e toda a arvore de Chrome junto, e nunca encosta no uvicorn.

NULL em execucao antiga (anterior a este deploy) e nas que nem chegaram a
subir processo; o reaper trata NULL como "nao sei o que matar" e segue
marcando FALHA como antes.
"""

import sqlalchemy as sa
from alembic import op

revision = "pub015"
down_revision = "pub014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "publicacao_tratamento_execucoes",
        sa.Column("runner_pid", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("publicacao_tratamento_execucoes", "runner_pid")
