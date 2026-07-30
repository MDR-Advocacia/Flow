"""Adiciona identidade de origem para publicações DJEN.

Revision ID: pub007_djen_source
Revises: usr005_rbac_cargos
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa


revision = "pub007_djen_source"
down_revision = "usr005_rbac_cargos"
branch_labels = None
depends_on = None


def _false_literal() -> str:
    return "FALSE" if op.get_bind().dialect.name == "postgresql" else "0"


def upgrade() -> None:
    op.add_column(
        "publicacao_registros",
        sa.Column(
            "source_provider",
            sa.String(length=32),
            nullable=False,
            server_default="LEGAL_ONE",
        ),
    )
    op.add_column(
        "publicacao_registros",
        sa.Column("source_external_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "publicacao_registros",
        sa.Column("ingestion_key", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "publicacao_registros",
        sa.Column("source_payload", sa.JSON(), nullable=True),
    )
    op.add_column(
        "publicacao_registros",
        sa.Column("content_fingerprint", sa.String(length=64), nullable=True),
    )

    op.execute(
        """
        UPDATE publicacao_registros
           SET source_external_id = CAST(legal_one_update_id AS VARCHAR),
               ingestion_key = 'LEGAL_ONE:' || CAST(legal_one_update_id AS VARCHAR)
         WHERE legal_one_update_id IS NOT NULL
        """
    )
    # batch_alter_table mantém a migration verificável em SQLite e vira ALTER
    # direto no PostgreSQL.
    with op.batch_alter_table("publicacao_registros") as batch_op:
        batch_op.alter_column(
            "source_external_id",
            existing_type=sa.String(length=255),
            nullable=False,
        )
        batch_op.alter_column(
            "ingestion_key",
            existing_type=sa.String(length=255),
            nullable=False,
        )
        batch_op.alter_column(
            "legal_one_update_id",
            existing_type=sa.Integer(),
            nullable=True,
        )

    op.create_index(
        "ix_publicacao_registros_source_provider",
        "publicacao_registros",
        ["source_provider"],
        unique=False,
    )
    op.create_index(
        "ix_publicacao_registros_source_external_id",
        "publicacao_registros",
        ["source_external_id"],
        unique=False,
    )
    op.create_index(
        "uq_publicacao_registros_ingestion_key",
        "publicacao_registros",
        ["ingestion_key"],
        unique=True,
    )
    op.create_index(
        "ix_publicacao_registros_content_fingerprint",
        "publicacao_registros",
        ["content_fingerprint"],
        unique=False,
    )

    bind = op.get_bind()
    indexes = {
        item["name"]
        for item in sa.inspect(bind).get_indexes("publicacao_registros")
    }
    if "uq_pub_lawsuit_date" in indexes:
        op.drop_index(
            "uq_pub_lawsuit_date",
            table_name="publicacao_registros",
        )

    false_literal = _false_literal()
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_pub_lawsuit_date_content
        ON publicacao_registros (
            linked_lawsuit_id,
            publication_date,
            content_fingerprint
        )
        WHERE linked_lawsuit_id IS NOT NULL
          AND publication_date IS NOT NULL
          AND publication_date <> ''
          AND content_fingerprint IS NOT NULL
          AND status <> 'DESCARTADO_DUPLICADA'
          AND COALESCE(is_duplicate, {false_literal}) = {false_literal}
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    has_djen_rows = bind.execute(
        sa.text(
            "SELECT 1 FROM publicacao_registros "
            "WHERE source_provider <> 'LEGAL_ONE' "
            "OR legal_one_update_id IS NULL LIMIT 1"
        )
    ).first()
    if has_djen_rows:
        raise RuntimeError(
            "Downgrade bloqueado: existem publicações sem ID do Legal One."
        )

    false_literal = _false_literal()
    has_multiple_live_same_day = bind.execute(
        sa.text(
            f"""
            SELECT 1
              FROM publicacao_registros
             WHERE linked_lawsuit_id IS NOT NULL
               AND publication_date IS NOT NULL
               AND publication_date <> ''
               AND status <> 'DESCARTADO_DUPLICADA'
               AND COALESCE(is_duplicate, {false_literal}) = {false_literal}
             GROUP BY linked_lawsuit_id, publication_date
            HAVING COUNT(*) > 1
             LIMIT 1
            """
        )
    ).first()
    if has_multiple_live_same_day:
        raise RuntimeError(
            "Downgrade bloqueado: existem publicações distintas do mesmo "
            "processo/data que não cabem no índice legado."
        )

    op.drop_index(
        "uq_pub_lawsuit_date_content",
        table_name="publicacao_registros",
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_pub_lawsuit_date
        ON publicacao_registros (linked_lawsuit_id, publication_date)
        WHERE linked_lawsuit_id IS NOT NULL
          AND publication_date IS NOT NULL
          AND publication_date <> ''
          AND status <> 'DESCARTADO_DUPLICADA'
          AND COALESCE(is_duplicate, {false_literal}) = {false_literal}
        """
    )
    op.drop_index(
        "ix_publicacao_registros_content_fingerprint",
        table_name="publicacao_registros",
    )
    op.drop_index(
        "uq_publicacao_registros_ingestion_key",
        table_name="publicacao_registros",
    )
    op.drop_index(
        "ix_publicacao_registros_source_external_id",
        table_name="publicacao_registros",
    )
    op.drop_index(
        "ix_publicacao_registros_source_provider",
        table_name="publicacao_registros",
    )
    with op.batch_alter_table("publicacao_registros") as batch_op:
        batch_op.alter_column(
            "legal_one_update_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch_op.drop_column("content_fingerprint")
        batch_op.drop_column("source_payload")
        batch_op.drop_column("ingestion_key")
        batch_op.drop_column("source_external_id")
        batch_op.drop_column("source_provider")
