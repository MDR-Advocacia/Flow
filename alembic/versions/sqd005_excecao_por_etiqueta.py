"""sqd005 — exceção de roteamento por ETIQUETA do processo.

No Autor, as tarefas de publicação são distribuídas por rodízio entre um grupo
de assistentes e um grupo de advogados (squads de suporte nos templates). A
Equipe Mista (etiqueta NERC no L1) é exceção: processo NERC fica com o
advogado da equipe e com o assistente dele, fora do rodízio geral.

Três colunas, todas aditivas e anuláveis:

  1. task_templates.excecao_etiqueta — preenchida e o processo com essa
     etiqueta, a squad de suporte e o responsável fixo do template deixam de
     valer e a tarefa vai para a equipe marcada com a etiqueta.
  2. task_templates.excecao_papel — quem DA EQUIPE recebe: 'principal'
     (advogado = responsável da pasta) ou 'assistente' (rodízio da squad).
     Separado do target_role porque no BB Autor o grupo de advogados roda
     como 'assistente' numa squad de suporte. NULL = segue o target_role.
  3. squads.etiqueta — a squad declara a etiqueta que atende. Sem isso o
     desempate do resolvedor escolhe a squad errada quando o responsável é
     membro de mais de uma (ver app/services/excecao_etiqueta.py).

Nome da etiqueta, não id: o id da NERC já mudou no L1 (7 → 83) e o nome ficou.

Não toca dado existente: tudo nasce NULL e nenhuma tarefa muda de rota até a
configuração ser feita. `prazo_inicial_task_templates` fica de fora de
propósito — a exceção é só do motor de publicações.

Revision ID: sqd005
Revises: pub016
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "sqd005"
down_revision: Union[str, None] = "pub016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUNAS = (
    ("squads", "etiqueta", 80),
    ("task_templates", "excecao_etiqueta", 80),
    ("task_templates", "excecao_papel", 16),
)


def _existentes(tabela: str) -> set:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(tabela)}


def upgrade() -> None:
    # Guardas por inspeção: o boot do container roda `alembic upgrade head`
    # sempre, e banco que já tenha a coluna não pode derrubar o deploy.
    for tabela, coluna, tamanho in _COLUNAS:
        if coluna not in _existentes(tabela):
            op.add_column(
                tabela,
                sa.Column(coluna, sa.String(length=tamanho), nullable=True),
            )


def downgrade() -> None:
    for tabela, coluna, _ in reversed(_COLUNAS):
        if coluna in _existentes(tabela):
            op.drop_column(tabela, coluna)
