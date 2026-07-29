"""RBAC por CARGO (com exceção por usuário) — modelo do Painel Financeiro.

Hoje a permissão é uma coluna booleana POR USUÁRIO (7 flags + CSV de equipes),
cada um dos 312 configurado na mão. Levantamento de 29/07/2026: dos 175 ativos,
146 não têm permissão nenhuma e os 29 restantes se espalham em 16 combinações
quase todas únicas — não existe padrão, é tudo caso a caso.

Desenho (decidido com o operador):
  - `flow_cargo` guarda a política: `modulos` {chave: bool} + as equipes
    (`equipes_modo` = nenhuma | lista | todas | supervisionadas).
    O modo `supervisionadas` resolve DINAMICAMENTE as equipes em que a pessoa é
    supervisora (perf_pessoa.is_supervisor) — muda de equipe, o acesso segue,
    sem ninguém lembrar de editar permissão.
  - Exceção por usuário: `modulos_extra`/`equipes_extra` {chave: bool}, onde
    true CONCEDE e false REVOGA sobre o cargo.

As COLUNAS booleanas continuam existindo e continuam sendo o que os ~30 gates do
backend leem — viram cache materializado do resultado (cargo + exceção), escrito
por `app/services/permissoes.py`. Assim o RBAC entra sem tocar em 30 call sites.

Esta migration NÃO muda o acesso de ninguém: ela escolhe o cargo que casa exato
e joga toda diferença em exceção. Recalcular depois tem que dar zero diffs — é
o teste que roda na validação local.

Revision ID: usr005_rbac_cargos
Revises: perf012_equipes_tabela
Create Date: 2026-07-29
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "usr005_rbac_cargos"
down_revision = "perf012_equipes_tabela"
branch_labels = None
depends_on = None

# Chaves dos módulos = nomes das próprias colunas booleanas. Manter igual evita
# um mapa de tradução entre o JSON do cargo e o cache materializado.
MODULOS = [
    "can_schedule_batch",
    "can_use_publications",
    "can_use_prazos_iniciais",
    "can_use_onerequest",
    "can_use_minha_equipe",
    "can_manage_distribuidos_bb",
    "notify_onerequest_errors",
]


def upgrade() -> None:
    op.create_table(
        "flow_cargo",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nome", sa.String(length=80), nullable=False, unique=True),
        sa.Column("descricao", sa.String(length=200), nullable=True),
        sa.Column(
            "modulos", postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        # nenhuma | lista | todas | supervisionadas
        sa.Column("equipes_modo", sa.String(length=20), nullable=False, server_default="nenhuma"),
        sa.Column(
            "equipes", postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )

    op.add_column("legal_one_users", sa.Column("cargo_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_legal_one_users_cargo", "legal_one_users", "flow_cargo",
        ["cargo_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_legal_one_users_cargo_id", "legal_one_users", ["cargo_id"])
    op.add_column(
        "legal_one_users",
        sa.Column("modulos_extra", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "legal_one_users",
        sa.Column("equipes_extra", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    conn = op.get_bind()
    todos_true = {k: True for k in MODULOS}
    todos_false = {k: False for k in MODULOS}
    so_pub = {**todos_false, "can_use_publications": True}
    # Supervisor: enxerga o Minha Equipe e, dinamicamente, as equipes que
    # supervisiona. Os demais módulos ficam por exceção/edição posterior.
    supervisor = {**todos_false, "can_use_minha_equipe": True}

    import json as _json

    def cria(nome, descricao, modulos, modo, equipes=None):
        return conn.execute(
            sa.text(
                "INSERT INTO flow_cargo (nome, descricao, modulos, equipes_modo, equipes, ativo) "
                "VALUES (:n, :d, CAST(:m AS jsonb), :mo, CAST(:e AS jsonb), true) RETURNING id"
            ),
            {"n": nome, "d": descricao, "m": _json.dumps(modulos), "mo": modo,
             "e": _json.dumps(equipes or [])},
        ).scalar()

    id_admin = cria("Administrador", "Acesso total ao sistema.", todos_true, "todas")
    id_super = cria("Supervisor", "Minha Equipe, restrito às equipes que a pessoa supervisiona.",
                    supervisor, "supervisionadas")
    id_pub = cria("Publicações", "Apenas o módulo de Publicações.", so_pub, "nenhuma")
    id_sem = cria("Sem acesso", "Usuário cadastrado sem acesso a módulo nenhum.",
                  todos_false, "nenhuma")

    # ── Atribuição preservando EXATAMENTE o acesso atual ──────────────────
    cols = ", ".join(MODULOS)
    usuarios = conn.execute(
        sa.text(f"SELECT id, role, {cols}, minha_equipe_equipes FROM legal_one_users")
    ).fetchall()

    for u in usuarios:
        atual = {k: bool(getattr(u, k)) for k in MODULOS}
        equipes_atuais = [
            e.strip() for e in (u.minha_equipe_equipes or "").split(",") if e.strip()
        ]

        # Admin bypassa todos os gates do backend (auth.require_permission e
        # os gates do Minha Equipe), então os flags dele são inócuos — o cargo
        # Administrador reflete a realidade sem alterar acesso efetivo.
        if (u.role or "user") == "admin":
            cargo_id, cargo_mods, cargo_equipes = id_admin, todos_true, equipes_atuais
        elif not any(atual.values()) and not equipes_atuais:
            cargo_id, cargo_mods, cargo_equipes = id_sem, todos_false, []
        elif atual == so_pub and not equipes_atuais:
            cargo_id, cargo_mods, cargo_equipes = id_pub, so_pub, []
        else:
            # Caso único: entra em "Sem acesso" e TUDO vira exceção. O operador
            # reagrupa em cargos pela tela, com calma, sem risco na virada.
            cargo_id, cargo_mods, cargo_equipes = id_sem, todos_false, []

        extra_mod = {k: v for k, v in atual.items() if v != cargo_mods.get(k, False)}
        extra_eq = {e: True for e in equipes_atuais if e not in cargo_equipes}

        conn.execute(
            sa.text(
                "UPDATE legal_one_users SET cargo_id = :c, "
                "modulos_extra = CAST(:m AS jsonb), equipes_extra = CAST(:e AS jsonb) "
                "WHERE id = :id"
            ),
            {
                "c": cargo_id, "id": u.id,
                "m": _json.dumps(extra_mod) if extra_mod else None,
                "e": _json.dumps(extra_eq) if extra_eq else None,
            },
        )


def downgrade() -> None:
    op.drop_column("legal_one_users", "equipes_extra")
    op.drop_column("legal_one_users", "modulos_extra")
    op.drop_index("ix_legal_one_users_cargo_id", table_name="legal_one_users")
    op.drop_constraint("fk_legal_one_users_cargo", "legal_one_users", type_="foreignkey")
    op.drop_column("legal_one_users", "cargo_id")
    op.drop_table("flow_cargo")
