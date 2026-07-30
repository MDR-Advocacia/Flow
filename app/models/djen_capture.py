"""Checkpoint durável da cobertura DJEN por cadernos."""

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)

from app.db.session import Base


class DjenCadernoShardCache(Base):
    """Resultado filtrado de um caderno para uma fotografia da carteira."""

    __tablename__ = "djen_caderno_shard_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_fingerprint = Column(String(64), nullable=False)
    tribunal = Column(String(32), nullable=False)
    reference_date = Column(Date, nullable=False)
    meio = Column(String(1), nullable=False)
    version = Column(String(32), nullable=True)
    archive_hash = Column(String(64), nullable=True)
    status = Column(String(24), nullable=False)
    total_comunicacoes = Column(Integer, nullable=False, default=0)
    numero_paginas = Column(Integer, nullable=False, default=0)
    matched_count = Column(Integer, nullable=False, default=0)
    download_bytes = Column(Integer, nullable=False, default=0)
    matched_payload_gzip = Column(LargeBinary, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "portfolio_fingerprint",
            "tribunal",
            "reference_date",
            "meio",
            name="uq_djen_caderno_shard_portfolio",
        ),
        Index(
            "ix_djen_caderno_shard_updated_at",
            "updated_at",
        ),
    )
