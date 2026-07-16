"""Critério de CLIENTE nas regras de observação + regra do Ativos Réu.

Sem isto as regras não distinguem cliente: a regra ativa "Réu → Cadastro" (escrita
pro Banco do Brasil, quando o módulo era só BB) casaria também com o Ativos Réu, e
não haveria como dar um termo diferente pra cada cliente.

O cliente é carimbado pela PORTA DE ENTRADA do processo (coleta RPA = BB;
"Importar lista (Ativos)" = ATIVOS), então é um critério confiável.

1) adiciona `criterio_cliente` (None = qualquer);
2) marca as regras existentes como BB — elas foram escritas pro BB e não podem
   vazar pro Ativos;
3) cria a 1ª regra do Ativos: Réu → "cadastroativos" (termo que dispara o
   workflow do Ativos no Legal One). O Ativos Autor fica pra depois (o operador
   cria na tela quando souber o termo).

Revision ID: bbd010_regra_observacao_cliente
Revises: bbd009_ativos_datajud_status
Create Date: 2026-07-16
"""
import sqlalchemy as sa
from alembic import op

revision = "bbd010_regra_observacao_cliente"
down_revision = "bbd009_ativos_datajud_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bbd_regras_observacao",
        sa.Column("criterio_cliente", sa.String(length=20), nullable=True),
    )
    # As regras que já existem são todas do Banco do Brasil (o módulo nasceu só BB).
    op.execute("UPDATE bbd_regras_observacao SET criterio_cliente = 'BB' WHERE criterio_cliente IS NULL")

    # 1ª regra do Ativos — só o Réu por enquanto. Idempotente.
    op.execute(
        """
        INSERT INTO bbd_regras_observacao
            (nome, criterio_cliente, criterio_posicao, criterio_natureza, criterio_cnj,
             texto, ativo, ordem, created_at)
        SELECT 'Ativos Réu → cadastroativos', 'ATIVOS', 'Réu', NULL, NULL,
               'cadastroativos', true, 10, now()
        WHERE NOT EXISTS (
            SELECT 1 FROM bbd_regras_observacao
             WHERE criterio_cliente = 'ATIVOS' AND criterio_posicao = 'Réu'
        )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM bbd_regras_observacao WHERE criterio_cliente = 'ATIVOS'")
    op.drop_column("bbd_regras_observacao", "criterio_cliente")
