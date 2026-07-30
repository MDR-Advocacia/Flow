"""Persistência de reconciliação L1 e outbox dos alertas de captura.

Revision ID: pub008_capture_resilience
Revises: pub007_djen_source
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa


revision = "pub008_capture_resilience"
down_revision = "pub007_djen_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "office_publication_cursor",
        sa.Column(
            "djen_reconciliation_pending",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "office_publication_cursor",
        sa.Column("djen_covered_from", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "office_publication_cursor",
        sa.Column("djen_covered_to", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "office_publication_cursor",
        sa.Column("djen_fallback_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "office_publication_cursor",
        sa.Column(
            "djen_coverage_complete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "office_publication_cursor",
        sa.Column("djen_coverage_metadata", sa.JSON(), nullable=True),
    )

    op.create_table(
        "djen_caderno_shard_cache",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "portfolio_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("tribunal", sa.String(length=32), nullable=False),
        sa.Column("reference_date", sa.Date(), nullable=False),
        sa.Column("meio", sa.String(length=1), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=True),
        sa.Column("archive_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "total_comunicacoes",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "numero_paginas",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "matched_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "download_bytes",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("matched_payload_gzip", sa.LargeBinary(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "portfolio_fingerprint",
            "tribunal",
            "reference_date",
            "meio",
            name="uq_djen_caderno_shard_portfolio",
        ),
    )
    op.create_index(
        "ix_djen_caderno_shard_updated_at",
        "djen_caderno_shard_cache",
        ["updated_at"],
        unique=False,
    )

    op.add_column(
        "publication_fetch_attempt",
        sa.Column(
            "alert_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "publication_fetch_attempt",
        sa.Column("alert_outbox_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_publication_fetch_attempt_alert_outbox_id",
        "publication_fetch_attempt",
        ["alert_outbox_id"],
        unique=False,
    )
    op.create_index(
        "ix_pfa_alert_repair",
        "publication_fetch_attempt",
        ["alert_required", "alert_outbox_id", "created_at"],
        unique=False,
    )

    op.add_column(
        "publicacao_buscas",
        sa.Column(
            "l1_reconciliation_status",
            sa.String(length=24),
            nullable=False,
            server_default="NAO_NECESSARIA",
        ),
    )
    op.add_column(
        "publicacao_buscas",
        sa.Column(
            "l1_reconciliation_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "publicacao_buscas",
        sa.Column(
            "l1_reconciliation_next_retry_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "publicacao_buscas",
        sa.Column(
            "l1_reconciliation_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "publicacao_buscas",
        sa.Column(
            "l1_reconciliation_completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "publicacao_buscas",
        sa.Column("l1_reconciliation_last_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "publicacao_buscas",
        sa.Column("l1_reconciliation_payload", sa.JSON(), nullable=True),
    )
    op.add_column(
        "publicacao_buscas",
        sa.Column("l1_reconciliation_run_token", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "publicacao_buscas",
        sa.Column("l1_reconciliation_result_search_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "publicacao_buscas",
        sa.Column("l1_alert_required_attempt", sa.Integer(), nullable=True),
    )
    op.add_column(
        "publicacao_buscas",
        sa.Column(
            "l1_alert_required_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "publicacao_buscas",
        sa.Column("l1_alert_outbox_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_publicacao_buscas_l1_reconciliation_status",
        "publicacao_buscas",
        ["l1_reconciliation_status"],
        unique=False,
    )
    op.create_index(
        "ix_publicacao_buscas_l1_reconciliation_next_retry_at",
        "publicacao_buscas",
        ["l1_reconciliation_next_retry_at"],
        unique=False,
    )
    op.create_index(
        "ix_publicacao_buscas_l1_reconciliation_run_token",
        "publicacao_buscas",
        ["l1_reconciliation_run_token"],
        unique=False,
    )
    op.create_index(
        "ix_publicacao_buscas_l1_reconciliation_result_search_id",
        "publicacao_buscas",
        ["l1_reconciliation_result_search_id"],
        unique=False,
    )
    op.create_index(
        "ix_pub_search_l1_reconciliation_due",
        "publicacao_buscas",
        ["l1_reconciliation_status", "l1_reconciliation_next_retry_at"],
        unique=False,
    )
    op.create_index(
        "ix_publicacao_buscas_l1_alert_outbox_id",
        "publicacao_buscas",
        ["l1_alert_outbox_id"],
        unique=False,
    )
    op.create_index(
        "ix_pub_search_l1_alert_repair",
        "publicacao_buscas",
        ["l1_alert_required_at", "l1_alert_outbox_id"],
        unique=False,
    )

    op.create_table(
        "publication_capture_alert",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("alert_type", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("recipients", sa.JSON(), nullable=False),
        sa.Column("failed_items", sa.JSON(), nullable=False),
        sa.Column("batch_source", sa.String(length=255), nullable=False),
        sa.Column(
            "system_name",
            sa.String(length=64),
            nullable=False,
            server_default="Flow",
        ),
        sa.Column("alert_context", sa.JSON(), nullable=True),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        # Zero significa retentar até o SMTP confirmar.
        sa.Column(
            "max_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_publication_capture_alert_idempotency_key",
        "publication_capture_alert",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_pca_status_next_retry",
        "publication_capture_alert",
        ["status", "next_retry_at"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    missing_scheduled_alert = bind.execute(
        sa.text(
            "SELECT 1 FROM publication_fetch_attempt "
            "WHERE alert_required = "
            + ("TRUE" if bind.dialect.name == "postgresql" else "1")
            + " AND alert_outbox_id IS NULL LIMIT 1"
        )
    ).first()
    if missing_scheduled_alert:
        raise RuntimeError(
            "Downgrade bloqueado: existem falhas agendadas sem alerta vinculado."
        )

    missing_manual_alert = bind.execute(
        sa.text(
            "SELECT 1 FROM publicacao_buscas "
            "WHERE l1_alert_required_attempt IS NOT NULL "
            "AND l1_alert_outbox_id IS NULL LIMIT 1"
        )
    ).first()
    if missing_manual_alert:
        raise RuntimeError(
            "Downgrade bloqueado: existem buscas manuais sem alerta vinculado."
        )

    active_alert = bind.execute(
        sa.text(
            "SELECT 1 FROM publication_capture_alert "
            "WHERE status IN ('pending', 'processing') LIMIT 1"
        )
    ).first()
    if active_alert:
        raise RuntimeError(
            "Downgrade bloqueado: existem alertas de captura ainda não entregues."
        )

    active_manual_reconciliation = bind.execute(
        sa.text(
            "SELECT 1 FROM publicacao_buscas "
            "WHERE l1_reconciliation_status IN ('PENDENTE', 'EXECUTANDO') "
            "LIMIT 1"
        )
    ).first()
    if active_manual_reconciliation:
        raise RuntimeError(
            "Downgrade bloqueado: existem reconciliações manuais do Legal One "
            "pendentes ou em execução."
        )

    active_scheduled_reconciliation = bind.execute(
        sa.text(
            "SELECT 1 FROM office_publication_cursor "
            "WHERE djen_reconciliation_pending = "
            + ("TRUE" if bind.dialect.name == "postgresql" else "1")
            + " LIMIT 1"
        )
    ).first()
    if active_scheduled_reconciliation:
        raise RuntimeError(
            "Downgrade bloqueado: existem escritórios com reconciliação DJEN "
            "pendente."
        )

    op.drop_index(
        "ix_pca_status_next_retry",
        table_name="publication_capture_alert",
    )
    op.drop_index(
        "ix_publication_capture_alert_idempotency_key",
        table_name="publication_capture_alert",
    )
    op.drop_table("publication_capture_alert")

    op.drop_index(
        "ix_djen_caderno_shard_updated_at",
        table_name="djen_caderno_shard_cache",
    )
    op.drop_table("djen_caderno_shard_cache")

    op.drop_index(
        "ix_pub_search_l1_reconciliation_due",
        table_name="publicacao_buscas",
    )
    op.drop_index(
        "ix_pub_search_l1_alert_repair",
        table_name="publicacao_buscas",
    )
    op.drop_index(
        "ix_publicacao_buscas_l1_alert_outbox_id",
        table_name="publicacao_buscas",
    )
    op.drop_index(
        "ix_publicacao_buscas_l1_reconciliation_result_search_id",
        table_name="publicacao_buscas",
    )
    op.drop_index(
        "ix_publicacao_buscas_l1_reconciliation_run_token",
        table_name="publicacao_buscas",
    )
    op.drop_index(
        "ix_publicacao_buscas_l1_reconciliation_next_retry_at",
        table_name="publicacao_buscas",
    )
    op.drop_index(
        "ix_publicacao_buscas_l1_reconciliation_status",
        table_name="publicacao_buscas",
    )
    with op.batch_alter_table("publicacao_buscas") as batch_op:
        batch_op.drop_column("l1_alert_outbox_id")
        batch_op.drop_column("l1_alert_required_at")
        batch_op.drop_column("l1_alert_required_attempt")
        batch_op.drop_column("l1_reconciliation_result_search_id")
        batch_op.drop_column("l1_reconciliation_run_token")
        batch_op.drop_column("l1_reconciliation_payload")
        batch_op.drop_column("l1_reconciliation_last_error")
        batch_op.drop_column("l1_reconciliation_completed_at")
        batch_op.drop_column("l1_reconciliation_started_at")
        batch_op.drop_column("l1_reconciliation_next_retry_at")
        batch_op.drop_column("l1_reconciliation_attempts")
        batch_op.drop_column("l1_reconciliation_status")

    op.drop_index(
        "ix_pfa_alert_repair",
        table_name="publication_fetch_attempt",
    )
    op.drop_index(
        "ix_publication_fetch_attempt_alert_outbox_id",
        table_name="publication_fetch_attempt",
    )
    with op.batch_alter_table("publication_fetch_attempt") as batch_op:
        batch_op.drop_column("alert_outbox_id")
        batch_op.drop_column("alert_required")

    with op.batch_alter_table("office_publication_cursor") as batch_op:
        batch_op.drop_column("djen_coverage_metadata")
        batch_op.drop_column("djen_coverage_complete")
        batch_op.drop_column("djen_fallback_at")
        batch_op.drop_column("djen_covered_to")
        batch_op.drop_column("djen_covered_from")
        batch_op.drop_column("djen_reconciliation_pending")
