"""Create OneNotify BB notification intake table

Revision ID: onb001_onenotify_bb
Revises: bbd001_distribuidos_bb, bp001, bp002, con001, not001_admin_notices,
    pha005, pin006, usr003_onerequest_notifications, var003
Create Date: 2026-08-11
"""

from alembic import op
import sqlalchemy as sa


revision = "onb001_onenotify_bb"
down_revision = (
    "bbd001_distribuidos_bb",
    "bp001",
    "bp002",
    "con001",
    "not001_admin_notices",
    "pha005",
    "pin006",
    "usr003_onerequest_notifications",
    "var003",
)
branch_labels = None
depends_on = None


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table in inspector.get_table_names()


def _has_index(table: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(idx["name"] == index_name for idx in inspector.get_indexes(table))


def _create_index(name: str, table: str, columns: list[str], unique: bool = False) -> None:
    if not _has_index(table, name):
        op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    if not _has_table("onenotify_bb_notificacoes"):
        op.create_table(
            "onenotify_bb_notificacoes",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("external_group_id", sa.String(), nullable=False),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("schema_version", sa.String(), nullable=True),
            sa.Column("notify_ids", sa.JSON(), nullable=True),
            sa.Column("npj", sa.String(), nullable=True),
            sa.Column("data_notificacao", sa.String(), nullable=True),
            sa.Column("notification_date_iso", sa.String(), nullable=True),
            sa.Column("publication_date", sa.String(), nullable=True),
            sa.Column("numero_processo_cnj", sa.String(), nullable=True),
            sa.Column("cnj_publicacao", sa.String(), nullable=True),
            sa.Column("cnj_principal_notify", sa.String(), nullable=True),
            sa.Column("cnj_divergent", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("adverso_principal", sa.String(), nullable=True),
            sa.Column("polo", sa.String(), nullable=True),
            sa.Column("posicao_cliente", sa.String(), nullable=True),
            sa.Column("tipos_notificacao", sa.JSON(), nullable=True),
            sa.Column("rpa_status", sa.JSON(), nullable=True),
            sa.Column("bb_ciencia_status", sa.JSON(), nullable=True),
            sa.Column("human_status", sa.JSON(), nullable=True),
            sa.Column("flow_sync_status", sa.JSON(), nullable=True),
            sa.Column("status_legacy", sa.JSON(), nullable=True),
            sa.Column("flow_status", sa.String(), nullable=False, server_default="RECEBIDA"),
            sa.Column("action_suggested", sa.String(), nullable=True),
            sa.Column("match_strategy", sa.String(), nullable=True),
            sa.Column("match_score", sa.Float(), nullable=True),
            sa.Column("match_reason", sa.Text(), nullable=True),
            sa.Column("matched_publication_record_id", sa.Integer(), nullable=True),
            sa.Column("matched_legal_one_update_id", sa.Integer(), nullable=True),
            sa.Column("matched_publication_status", sa.String(), nullable=True),
            sa.Column("andamentos", sa.JSON(), nullable=True),
            sa.Column("documentos", sa.JSON(), nullable=True),
            sa.Column("conteudo", sa.JSON(), nullable=True),
            sa.Column("raw_payload", sa.JSON(), nullable=True),
            sa.Column("text_content", sa.Text(), nullable=True),
            sa.Column("document_summary", sa.JSON(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["matched_publication_record_id"],
                ["publicacao_registros.id"],
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("external_group_id"),
        )

    table = "onenotify_bb_notificacoes"
    _create_index("ix_onb_npj", table, ["npj"])
    _create_index("ix_onb_notification_date_iso", table, ["notification_date_iso"])
    _create_index("ix_onb_publication_date", table, ["publication_date"])
    _create_index("ix_onb_numero_processo_cnj", table, ["numero_processo_cnj"])
    _create_index("ix_onb_cnj_publicacao", table, ["cnj_publicacao"])
    _create_index("ix_onb_cnj_principal_notify", table, ["cnj_principal_notify"])
    _create_index("ix_onb_cnj_divergent", table, ["cnj_divergent"])
    _create_index("ix_onb_posicao_cliente", table, ["posicao_cliente"])
    _create_index("ix_onb_flow_status", table, ["flow_status"])
    _create_index("ix_onb_action_suggested", table, ["action_suggested"])
    _create_index("ix_onb_match_score", table, ["match_score"])
    _create_index("ix_onb_matched_publication_record_id", table, ["matched_publication_record_id"])
    _create_index("ix_onb_matched_legal_one_update_id", table, ["matched_legal_one_update_id"])
    _create_index("ix_onb_matched_publication_status", table, ["matched_publication_status"])


def downgrade() -> None:
    if _has_table("onenotify_bb_notificacoes"):
        op.drop_table("onenotify_bb_notificacoes")
