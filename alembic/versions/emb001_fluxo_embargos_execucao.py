"""Fluxo Embargos à Execução: cards das execuções monitoradas (emb_execucao),
partes demandadas (emb_parte), embargos candidatos (emb_candidato) e trilha
de eventos (emb_evento), templates das tarefas do incidente
(emb_tarefa_template) e histórico dos disparos (emb_tarefa_disparo).

Revision ID: emb001_fluxo_embargos_execucao
Revises: sqd005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "emb001_fluxo_embargos_execucao"
down_revision = "sqd005"
branch_labels = None
depends_on = None


def _json():
    return postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def _agora():
    return sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "emb_execucao",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pasta", sa.String(), nullable=False),
        sa.Column("l1_task_id", sa.BigInteger(), nullable=True),
        sa.Column("l1_task_ids", _json(), nullable=True),
        sa.Column("lawsuit_id", sa.Integer(), nullable=True),
        sa.Column("cnj", sa.String(), nullable=True),
        sa.Column("cnj_digitos", sa.String(), nullable=True),
        sa.Column("npj", sa.String(), nullable=True),
        sa.Column("escritorio", sa.String(), nullable=True),
        sa.Column("cliente", sa.String(), nullable=True),
        sa.Column("responsavel_nome", sa.String(), nullable=True),
        sa.Column("executante_nome", sa.String(), nullable=True),
        sa.Column("uf", sa.String(), nullable=True),
        sa.Column("origem", sa.String(), nullable=False, server_default="RELATORIO"),
        sa.Column("data_ajuizamento", sa.Date(), nullable=False),
        sa.Column("estado", sa.String(), nullable=False, server_default="AGUARDANDO_JANELA"),
        sa.Column("dias_uteis_janela", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("inicio_monitoramento", sa.Date(), nullable=True),
        sa.Column("proxima_consulta", sa.Date(), nullable=True),
        sa.Column("ultima_consulta_em", _agora(), nullable=True),
        sa.Column("consultas_feitas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ultimo_erro", sa.Text(), nullable=True),
        sa.Column("partes_status", sa.String(), nullable=False, server_default="PENDENTE"),
        sa.Column("partes_tentativas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("partes_erro", sa.Text(), nullable=True),
        sa.Column("partes_em", _agora(), nullable=True),
        sa.Column("tribunal_alias", sa.String(), nullable=True),
        sa.Column("orgao_codigo", sa.Integer(), nullable=True),
        sa.Column("orgao_nome", sa.String(), nullable=True),
        sa.Column("datajud_ajuizamento_raw", sa.String(), nullable=True),
        sa.Column("l1_incidente_verificado_em", _agora(), nullable=True),
        sa.Column("incidente_folder", sa.String(), nullable=True),
        sa.Column("incidente_cnj", sa.String(), nullable=True),
        sa.Column("incidente_id", sa.Integer(), nullable=True),
        sa.Column("incidente_office_id", sa.Integer(), nullable=True),
        sa.Column("encontrado_em", _agora(), nullable=True),
        sa.Column("aviso_enviado_em", _agora(), nullable=True),
        sa.Column("confirmado_candidato_id", sa.Integer(), nullable=True),
        sa.Column("anotacao", sa.Text(), nullable=True),
        sa.Column("decidido_por_user_id", sa.Integer(), sa.ForeignKey("legal_one_users.id"), nullable=True),
        sa.Column("decidido_em", _agora(), nullable=True),
        sa.Column("criado_em", _agora(), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", _agora(), server_default=sa.func.now(), nullable=False),
    )
    t = "emb_execucao"
    op.create_index(f"ix_{t}_pasta", t, ["pasta"], unique=True)
    op.create_index(f"ix_{t}_l1_task_id", t, ["l1_task_id"])
    op.create_index(f"ix_{t}_cnj_digitos", t, ["cnj_digitos"])
    op.create_index(f"ix_{t}_npj", t, ["npj"])
    op.create_index(f"ix_{t}_cliente", t, ["cliente"])
    op.create_index(f"ix_{t}_estado", t, ["estado"])
    op.create_index(f"ix_{t}_proxima_consulta", t, ["proxima_consulta"])
    op.create_index(f"ix_{t}_partes_status", t, ["partes_status"])

    op.create_table(
        "emb_parte",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "execucao_id", sa.Integer(),
            sa.ForeignKey("emb_execucao.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("origem", sa.String(), nullable=False, server_default="BB"),
        sa.Column("polo", sa.String(), nullable=True),
        sa.Column("nome", sa.String(), nullable=False),
        sa.Column("cpf_cnpj", sa.String(), nullable=True),
        sa.Column("tipo_pessoa", sa.String(), nullable=True),
        sa.Column("relacao_bb", sa.String(), nullable=True),
        sa.Column("demandada", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("raw", _json(), nullable=True),
        sa.Column("criado_em", _agora(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_emb_parte_execucao_id", "emb_parte", ["execucao_id"])

    op.create_table(
        "emb_candidato",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "execucao_id", sa.Integer(),
            sa.ForeignKey("emb_execucao.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("cnj", sa.String(), nullable=False),
        sa.Column("cnj_digitos", sa.String(), nullable=False),
        sa.Column("data_ajuizamento", _agora(), nullable=True),
        sa.Column("classe_nome", sa.String(), nullable=True),
        sa.Column("orgao_nome", sa.String(), nullable=True),
        sa.Column("distribuicao_dependencia", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("peticao_mesmo_dia", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("nivel", sa.String(), nullable=False, server_default="FRACO"),
        sa.Column("djen_status", sa.String(), nullable=True),
        sa.Column("djen_embargantes", _json(), nullable=True),
        sa.Column("djen_embargados", _json(), nullable=True),
        sa.Column("l1_litigation_id", sa.Integer(), nullable=True),
        sa.Column("l1_folder", sa.String(), nullable=True),
        sa.Column("djen_nomes_casados", _json(), nullable=True),
        sa.Column("djen_trecho", sa.Text(), nullable=True),
        sa.Column("djen_data", sa.String(), nullable=True),
        sa.Column("djen_consultado_em", _agora(), nullable=True),
        sa.Column("decisao", sa.String(), nullable=False, server_default="PENDENTE"),
        sa.Column("decidido_por_user_id", sa.Integer(), sa.ForeignKey("legal_one_users.id"), nullable=True),
        sa.Column("decidido_em", _agora(), nullable=True),
        sa.Column("criado_em", _agora(), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", _agora(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("execucao_id", "cnj_digitos", name="uq_emb_candidato_execucao_cnj"),
    )
    op.create_index("ix_emb_candidato_execucao_id", "emb_candidato", ["execucao_id"])
    op.create_index("ix_emb_candidato_cnj_digitos", "emb_candidato", ["cnj_digitos"])
    op.create_index("ix_emb_candidato_nivel", "emb_candidato", ["nivel"])
    op.create_index("ix_emb_candidato_decisao", "emb_candidato", ["decisao"])

    op.create_table(
        "emb_evento",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "execucao_id", sa.Integer(),
            sa.ForeignKey("emb_execucao.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("secao", sa.String(), nullable=False),
        sa.Column("nivel", sa.String(), nullable=False, server_default="INFO"),
        sa.Column("mensagem", sa.Text(), nullable=False),
        sa.Column("dados", _json(), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("legal_one_users.id"), nullable=True),
        sa.Column("criado_em", _agora(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_emb_evento_execucao_id", "emb_evento", ["execucao_id"])
    op.create_index("ix_emb_evento_secao", "emb_evento", ["secao"])
    op.create_index("ix_emb_evento_criado_em", "emb_evento", ["criado_em"])

    op.create_table(
        "emb_tarefa_template",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nome", sa.String(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ordem", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tipo_id", sa.Integer(), nullable=False),
        sa.Column("subtipo_id", sa.Integer(), nullable=False),
        sa.Column("subtipo_nome", sa.String(), nullable=True),
        sa.Column("responsavel_modo", sa.String(), nullable=False, server_default="FIXO"),
        sa.Column("responsavel_contact_id", sa.Integer(), nullable=True),
        sa.Column("responsavel_nome", sa.String(), nullable=True),
        sa.Column("prazo_dias_uteis", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("prioridade", sa.String(), nullable=False, server_default="Normal"),
        sa.Column("descricao_template", sa.Text(), nullable=False),
        sa.Column("observacoes_template", sa.Text(), nullable=True),
        sa.Column("atualizado_por_user_id", sa.Integer(), sa.ForeignKey("legal_one_users.id"), nullable=True),
        sa.Column("criado_em", _agora(), server_default=sa.func.now(), nullable=False),
        sa.Column("atualizado_em", _agora(), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "emb_tarefa_disparo",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "execucao_id", sa.Integer(),
            sa.ForeignKey("emb_execucao.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("template_nome", sa.String(), nullable=True),
        sa.Column("incidente_id", sa.Integer(), nullable=True),
        sa.Column("subtipo_id", sa.Integer(), nullable=True),
        sa.Column("responsavel_contact_id", sa.Integer(), nullable=True),
        sa.Column("prazo", sa.Date(), nullable=True),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("l1_task_id", sa.BigInteger(), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("legal_one_users.id"), nullable=True),
        sa.Column("criado_em", _agora(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_emb_tarefa_disparo_execucao_id", "emb_tarefa_disparo", ["execucao_id"])
    op.create_index("ix_emb_tarefa_disparo_status", "emb_tarefa_disparo", ["status"])


def downgrade() -> None:
    op.drop_table("emb_tarefa_disparo")
    op.drop_table("emb_tarefa_template")
    op.drop_table("emb_evento")
    op.drop_table("emb_candidato")
    op.drop_table("emb_parte")
    op.drop_table("emb_execucao")
