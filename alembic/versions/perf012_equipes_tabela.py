"""Equipes do Minha Equipe saem do código e viram tabela (CRUD no admin).

Até aqui a lista de equipes vivia hardcoded em TRÊS lugares (teams.py do
backend, teams.ts da sidebar e EQUIPES do AdminPage) — criar uma equipe exigia
deploy, e o desencontro entre as listas já causou bug de permissão (a equipe
aparecia no menu mas não no dropdown de permissões). Agora a fonte é esta
tabela; o código guarda só um fallback de bootstrap.

O seed replica EXATAMENTE as keys em uso — em especial `bb-cadastro`, cujo
rótulo hoje é "Controladoria": a key é referenciada pelo CSV de permissões em
legal_one_users.minha_equipe_equipes e por perf_pessoa.equipe, então trocá-la
revogaria acesso e orfanaria gente.

Aditiva e idempotente no seed (ON CONFLICT DO NOTHING).

Revision ID: perf012_equipes_tabela
Revises: pub006_l1_etiqueta_cache
Create Date: 2026-07-29
"""
import sqlalchemy as sa
from alembic import op

revision = "perf012_equipes_tabela"
down_revision = "pub006_l1_etiqueta_cache"
branch_labels = None
depends_on = None


# (key, label, grupo, ordem) — espelho do que estava hardcoded em 29/07/2026.
_SEED = [
    ("bb-reu", "BB Réu", "Contencioso Passivo", 10),
    ("bb-execucao", "BB Execução & Encerramento", "Contencioso Passivo", 20),
    ("bb-acordos", "BB Acordos", "Contencioso Passivo", 30),
    ("bb-estrategico", "BB Estratégico", "Contencioso Passivo", 40),
    ("master-reu", "Master Réu", "Contencioso Passivo", 50),
    ("ativos-reu", "Ativos Réu", "Contencioso Passivo", 60),
    ("trabalhista", "Trabalhista", "Contencioso Passivo", 70),
    ("bb-autor-processual", "BB Autor — Processual", "Recuperação de Crédito", 110),
    ("ativos-autor", "Ativos Autor", "Recuperação de Crédito", 120),
    ("autor-recursal", "Autor — Recursal", "Recuperação de Crédito", 130),
    ("ajuizamento", "Ajuizamento", "Recuperação de Crédito", 140),
    ("estrategico-autor", "Estratégico Autor", "Recuperação de Crédito", 150),
    ("cobranca", "Cobrança", "Recuperação de Crédito", 160),
    ("equipe-mista", "Equipe Mista", "Especializada", 210),
    ("bb-cadastro", "Controladoria", "Especializada", 220),
]


def upgrade() -> None:
    op.create_table(
        "perf_equipe",
        sa.Column("id", sa.Integer(), primary_key=True),
        # `key` é o slug usado em rota, permissão (CSV) e perf_pessoa.equipe.
        # Imutável depois de criada — o admin edita rótulo/grupo, nunca a key.
        sa.Column("key", sa.String(length=60), nullable=False, unique=True),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("grupo", sa.String(length=80), nullable=False),
        sa.Column("ordem", sa.Integer(), nullable=False, server_default="999"),
        # Exclusão é SOFT: a equipe some do menu e dos dropdowns, mas o histórico
        # (pessoas, tarefas, permissões já concedidas) continua resolvendo o nome.
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_perf_equipe_ativo", "perf_equipe", ["ativo"])

    conn = op.get_bind()
    for key, label, grupo, ordem in _SEED:
        conn.execute(
            sa.text(
                "INSERT INTO perf_equipe (key, label, grupo, ordem, ativo) "
                "VALUES (:k, :l, :g, :o, true) ON CONFLICT (key) DO NOTHING"
            ),
            {"k": key, "l": label, "g": grupo, "o": ordem},
        )


def downgrade() -> None:
    op.drop_index("ix_perf_equipe_ativo", table_name="perf_equipe")
    op.drop_table("perf_equipe")
