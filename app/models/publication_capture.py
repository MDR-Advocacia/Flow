"""
Models para rastreamento confiável de captura de publicações do Legal One.

- OfficePublicationCursor: watermark por escritório (até onde já capturamos com sucesso).
- PublicationFetchAttempt: histórico de tentativas de fetch por janela, para retry e dead-letter.
- PublicationCaptureAlert: outbox durável dos alertas de falha/degradação.
"""
from datetime import datetime
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    JSON,
    String,
    Text,
    func,
)

from app.db.session import Base


# Status possíveis
CURSOR_STATUS_OK = "ok"
CURSOR_STATUS_FAILED = "failed"
CURSOR_STATUS_DEAD_LETTER = "dead_letter"

ATTEMPT_STATUS_PENDING = "pending"
ATTEMPT_STATUS_SUCCESS = "success"
ATTEMPT_STATUS_FAILED = "failed"
ATTEMPT_STATUS_DEAD_LETTER = "dead_letter"

PUBLICATION_ALERT_STATUS_PENDING = "pending"
PUBLICATION_ALERT_STATUS_PROCESSING = "processing"
PUBLICATION_ALERT_STATUS_SENT = "sent"
PUBLICATION_ALERT_STATUS_DEAD_LETTER = "dead_letter"

# Backoff em minutos: 1, 5, 30, 60. A quinta falha encerra em dead_letter;
# assim todos os intervalos declarados são efetivamente utilizados.
RETRY_BACKOFF_MINUTES = [1, 5, 30, 60]
MAX_CONSECUTIVE_FAILURES_BEFORE_DEAD_LETTER = len(RETRY_BACKOFF_MINUTES) + 1


class OfficePublicationCursor(Base):
    __tablename__ = "office_publication_cursor"

    office_id = Column(Integer, primary_key=True)
    last_successful_date = Column(DateTime(timezone=True), nullable=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    last_status = Column(String, nullable=True)
    last_error = Column(Text, nullable=True)
    consecutive_failures = Column(Integer, nullable=False, default=0)
    # O cursor do L1 não avança enquanto a reconciliação estiver pendente.
    # Cadernos parciais são refeitos; após cobertura integral, só o /Updates
    # continua sendo consultado.
    djen_reconciliation_pending = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )
    djen_covered_from = Column(DateTime(timezone=True), nullable=True)
    djen_covered_to = Column(DateTime(timezone=True), nullable=True)
    djen_fallback_at = Column(DateTime(timezone=True), nullable=True)
    djen_coverage_complete = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )
    djen_coverage_metadata = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class PublicationFetchAttempt(Base):
    __tablename__ = "publication_fetch_attempt"

    id = Column(Integer, primary_key=True, autoincrement=True)
    office_id = Column(Integer, nullable=False, index=True)
    window_from = Column(DateTime(timezone=True), nullable=False)
    window_to = Column(DateTime(timezone=True), nullable=False)
    status = Column(String, nullable=False)
    attempt_n = Column(Integer, nullable=False, default=1)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    records_found = Column(Integer, nullable=True)
    automation_id = Column(Integer, nullable=True, index=True)
    # Gravado na mesma transação da falha. Se o processo reiniciar antes de
    # criar o outbox, o sweep repara qualquer linha required ainda sem vínculo.
    alert_required = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )
    alert_outbox_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


Index("ix_pfa_status_next_retry", PublicationFetchAttempt.status, PublicationFetchAttempt.next_retry_at)
Index(
    "ix_pfa_alert_repair",
    PublicationFetchAttempt.alert_required,
    PublicationFetchAttempt.alert_outbox_id,
    PublicationFetchAttempt.created_at,
)


class PublicationCaptureAlert(Base):
    """Outbox de alertas da captura, com entrega SMTP ao menos uma vez.

    ``idempotency_key`` é fornecida pelo chamador e identifica semanticamente
    um único aviso (por exemplo, ``scheduled-failure:run:185``). A transição
    ``pending -> processing -> sent`` permite que vários workers executem o
    sweep sem enviar simultaneamente a mesma linha. Um lock abandonado volta a
    ser elegível depois do lease configurado no serviço.
    """

    __tablename__ = "publication_capture_alert"

    id = Column(Integer, primary_key=True, autoincrement=True)
    idempotency_key = Column(String(255), nullable=False, unique=True, index=True)
    alert_type = Column(String(64), nullable=False)
    status = Column(
        String(32),
        nullable=False,
        default=PUBLICATION_ALERT_STATUS_PENDING,
        server_default=PUBLICATION_ALERT_STATUS_PENDING,
    )

    # Contrato persistido do mail_service.send_failure_report.
    recipients = Column(JSON, nullable=False)
    failed_items = Column(JSON, nullable=False)
    batch_source = Column(String(255), nullable=False)
    system_name = Column(String(64), nullable=False, default="Flow", server_default="Flow")
    alert_context = Column(JSON, nullable=True)

    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    # Zero = repetir até entregar. Um limite positivo pode ser usado em
    # eventos que aceitem dead-letter explícito.
    max_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


Index(
    "ix_pca_status_next_retry",
    PublicationCaptureAlert.status,
    PublicationCaptureAlert.next_retry_at,
)
