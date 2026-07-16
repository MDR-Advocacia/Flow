"""Cria os escritórios do cliente Ativos (Réu e Autor) de verdade.

Antes eles nasciam SÓ na primeira importação de lista (get-or-create preguiçoso
dentro da ingestão), então não apareciam na tela "Escritórios & Filas" e o
operador não tinha onde montar a fila de responsáveis ANTES de importar — que é
justamente a ordem natural do trabalho. Os do Banco do Brasil sempre vieram do
seed; os do Ativos passam a vir daqui.

Os paths conferem com os escritórios reais do Legal One (os mesmos que o módulo
de Publicações enxerga): Ativos/Réu = office 27, Ativos/Autor = office 26.

Filas nascem VAZIAS de propósito — o operador cadastra os responsáveis na tela.
Idempotente (não duplica se já existir).

Revision ID: bbd011_escritorios_ativos
Revises: bbd010_regra_observacao_cliente
Create Date: 2026-07-16
"""
from alembic import op

revision = "bbd011_escritorios_ativos"
down_revision = "bbd010_regra_observacao_cliente"
branch_labels = None
depends_on = None

_BASE = "MDR Advocacia / Área operacional / Ativos"


def upgrade() -> None:
    for nome, sufixo, polo, ordem in (
        ("Ativos - Réu", "Réu", "Passivo", 90),
        ("Ativos - Autor", "Autor", "Ativo", 91),
    ):
        op.execute(
            f"""
            INSERT INTO bbd_escritorios
                (nome, escritorio_path, criterio_polo, ativo, ordem, created_at)
            SELECT '{nome}', '{_BASE} / {sufixo}', '{polo}', true, {ordem}, now()
            WHERE NOT EXISTS (
                SELECT 1 FROM bbd_escritorios WHERE nome = '{nome}'
            )
            """
        )


def downgrade() -> None:
    op.execute("DELETE FROM bbd_escritorios WHERE nome IN ('Ativos - Réu', 'Ativos - Autor')")
