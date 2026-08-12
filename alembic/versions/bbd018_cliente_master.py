"""Banco Master vira carteira do Cadastro (cliente MASTER).

O Master era atendido por um módulo à parte (Administração → Base Banco Master
→ Conversão L1) que apenas convertia a planilha e devolvia o xlsx pro operador
subir na mão — sem lote, sem métrica, sem "o que chegou hoje". Aqui ele entra
no mesmo motor das outras carteiras:

- `bbd_ativos_lotes` ganha `cliente` (a tabela de lote deixa de ser só da
  Ativos e passa a valer pra qualquer carteira que entre por upload);
- linha de roteamento do Master em `bbd_escritorios` (Passivo → Banco Master /
  Réu, responsável fixo, observação padrão `bmcomum`);
- config do cliente (nome/CNPJ/tipo) e o termo do agravo em `bbd_config`.

Os valores replicam exatamente o que o `conversao_l1.py` tinha hardcoded, mas
agora editáveis pela tela de Configuração — que era o ponto: nada de constante
de código pra mudar responsável ou razão social.

Revision ID: bbd018
Revises: pub008_captura_conhecimento
"""
from alembic import op
import sqlalchemy as sa

revision = "bbd018"
down_revision = "pub008_captura_conhecimento"
branch_labels = None
depends_on = None


# Espelham os hardcodes do conversao_l1.py (autor Jonilson Vilela).
ESCRITORIO_MASTER = "MDR Advocacia / Área operacional / Banco Master / Réu"
RESPONSAVEL_MASTER = "Enzo Pinto Bagatoli Carriço"
CLIENTE_NOME = "Banco Master S.A. - Em Liquidação Extrajudicial"
CLIENTE_CNPJ = "33.923.798/0001-00"

CONFIGS = [
    ("master_cliente_nome", CLIENTE_NOME,
     "Razão social do Banco Master na planilha de migração."),
    ("master_cliente_cpf_cnpj", CLIENTE_CNPJ,
     "CNPJ do Banco Master na planilha de migração."),
    ("master_cliente_tipo", "PJ",
     "Tipo de pessoa do cliente Banco Master (PF/PJ)."),
    ("master_observacao_agravo", "bmagravo",
     "Observação gravada quando o processo do Master é Agravo de Instrumento "
     "(ação = Agravo de Instrumento ou número terminado em .0000). Os demais "
     "usam a observação padrão do escritório (bmcomum)."),
]


def upgrade() -> None:
    # ── 1) o lote passa a saber de qual carteira é ────────────────────────
    op.add_column(
        "bbd_ativos_lotes",
        sa.Column("cliente", sa.String(length=20), nullable=False,
                  server_default="ATIVOS"),
    )
    op.create_index(
        "ix_bbd_ativos_lotes_cliente", "bbd_ativos_lotes", ["cliente"],
    )

    conn = op.get_bind()

    # ── 2) linha de roteamento do Master ──────────────────────────────────
    # O Master é SEMPRE Réu (polo Passivo) com responsável fixo — não há
    # rodízio, diferente de BB/Ativos. Por isso a linha já nasce com
    # `responsavel_fixo_user_id`: o motor de distribuição prefere o fixo ao
    # round-robin, então nenhuma fila de responsáveis precisa existir.
    resp_id = conn.execute(
        sa.text("SELECT id FROM legal_one_users WHERE name = :n LIMIT 1"),
        {"n": RESPONSAVEL_MASTER},
    ).scalar()
    # Sem o usuário (base nova/limpa) a linha entra assim mesmo, só sem
    # responsável — o operador aponta na tela de Configuração. Falhar a
    # migration por causa disso derrubaria o boot do container.

    ja_existe = conn.execute(
        sa.text("SELECT id FROM bbd_escritorios WHERE criterio_cliente = 'MASTER' LIMIT 1")
    ).scalar()
    if not ja_existe:
        conn.execute(
            sa.text(
                "INSERT INTO bbd_escritorios "
                "(nome, escritorio_path, criterio_cliente, criterio_polo, "
                " responsavel_fixo_user_id, observacao_padrao, ativo, ordem) "
                "VALUES (:nome, :path, 'MASTER', 'Passivo', :resp, 'bmcomum', true, 92)"
            ),
            {"nome": "Banco Master - Réu", "path": ESCRITORIO_MASTER, "resp": resp_id},
        )

    # ── 3) config do cliente (idempotente) ────────────────────────────────
    for chave, valor, descricao in CONFIGS:
        existe = conn.execute(
            sa.text("SELECT chave FROM bbd_config WHERE chave = :c"), {"c": chave},
        ).scalar()
        if not existe:
            conn.execute(
                sa.text(
                    "INSERT INTO bbd_config (chave, valor, descricao) "
                    "VALUES (:c, :v, :d)"
                ),
                {"c": chave, "v": valor, "d": descricao},
            )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM bbd_config WHERE chave IN "
                "('master_cliente_nome','master_cliente_cpf_cnpj',"
                " 'master_cliente_tipo','master_observacao_agravo')")
    )
    # Só remove a linha de roteamento se nenhum processo do Master a usa —
    # apagar com processo apontando pra ela zeraria o escritório deles.
    conn.execute(
        sa.text(
            "DELETE FROM bbd_escritorios WHERE criterio_cliente = 'MASTER' "
            "AND id NOT IN (SELECT DISTINCT escritorio_id FROM bbd_processos "
            "               WHERE escritorio_id IS NOT NULL)"
        )
    )
    op.drop_index("ix_bbd_ativos_lotes_cliente", table_name="bbd_ativos_lotes")
    op.drop_column("bbd_ativos_lotes", "cliente")
