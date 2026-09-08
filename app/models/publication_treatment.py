"""
Modelos para a fila de tratamento de publicacoes no Legal One.

O fluxo separa:
- o status de negocio da publicacao (AGENDADO / IGNORADO)
- o status tecnico do tratamento no modulo de Publicacoes do Legal One
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base

TREATMENT_TARGET_TREATED = "TRATADA"
TREATMENT_TARGET_WITHOUT_PROVIDENCE = "SEM_PROVIDENCIAS"
VALID_TREATMENT_TARGETS = {
    TREATMENT_TARGET_TREATED,
    TREATMENT_TARGET_WITHOUT_PROVIDENCE,
}

QUEUE_STATUS_PENDING = "PENDENTE"
QUEUE_STATUS_PROCESSING = "PROCESSANDO"
QUEUE_STATUS_COMPLETED = "CONCLUIDO"
QUEUE_STATUS_FAILED = "FALHA"
QUEUE_STATUS_CANCELLED = "CANCELADO"
VALID_QUEUE_STATUSES = {
    QUEUE_STATUS_PENDING,
    QUEUE_STATUS_PROCESSING,
    QUEUE_STATUS_COMPLETED,
    QUEUE_STATUS_FAILED,
    QUEUE_STATUS_CANCELLED,
}

RUN_STATUS_STARTING = "INICIANDO"
RUN_STATUS_RUNNING = "EXECUTANDO"
RUN_STATUS_PAUSED = "PAUSADO"
RUN_STATUS_COMPLETED = "CONCLUIDO"
RUN_STATUS_COMPLETED_WITH_ERRORS = "CONCLUIDO_COM_FALHAS"
RUN_STATUS_FAILED = "FALHA"
RUN_STATUS_STOPPED = "INTERROMPIDO"
ACTIVE_RUN_STATUSES = {
    RUN_STATUS_STARTING,
    RUN_STATUS_RUNNING,
    RUN_STATUS_PAUSED,
}
FINAL_RUN_STATUSES = {
    RUN_STATUS_COMPLETED,
    RUN_STATUS_COMPLETED_WITH_ERRORS,
    RUN_STATUS_FAILED,
    RUN_STATUS_STOPPED,
}

RUN_TRIGGER_MANUAL = "MANUAL"
RUN_TRIGGER_AUTOMATION = "AUTOMACAO"


class PublicationTreatmentRun(Base):
    __tablename__ = "publicacao_tratamento_execucoes"

    id = Column(Integer, primary_key=True, index=True)
    status = Column(String, nullable=False, default=RUN_STATUS_STARTING, index=True)
    trigger_type = Column(String, nullable=False, default=RUN_TRIGGER_MANUAL)
    triggered_by_email = Column(String, nullable=True, index=True)
    automation_id = Column(Integer, ForeignKey("scheduled_automations.id"), nullable=True, index=True)

    input_file_path = Column(String, nullable=True)
    status_file_path = Column(String, nullable=True)
    control_file_path = Column(String, nullable=True)
    log_file_path = Column(String, nullable=True)
    error_log_file_path = Column(String, nullable=True)

    # PID do processo Node do runner (pub015). Sem ele o reaper de zumbi
    # marcava a execucao como FALHA e ia embora, deixando o runner VIVO e
    # travado segurando a arvore de Chrome — foi assim que o container ficou
    # sem PIDs em 08/09/2026. O processo nasce em sessao propria
    # (start_new_session), entao este pid tambem e o PGID: matar o grupo leva
    # o Node e todos os Chrome de uma vez, sem risco de encostar na API.
    runner_pid = Column(Integer, nullable=True)

    total_items = Column(Integer, nullable=False, default=0)
    processed_items = Column(Integer, nullable=False, default=0)
    success_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    retry_pending_count = Column(Integer, nullable=False, default=0)

    batch_size = Column(Integer, nullable=True)
    total_batches = Column(Integer, nullable=True)
    current_batch = Column(Integer, nullable=True)
    max_attempts = Column(Integer, nullable=True)

    generated_at = Column(DateTime(timezone=True), nullable=True)
    sleep_until = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)

    automation = relationship("ScheduledAutomation", foreign_keys=[automation_id])


class PublicationTreatmentItem(Base):
    __tablename__ = "publicacao_tratamento_itens"

    id = Column(Integer, primary_key=True, index=True)
    publication_record_id = Column(
        Integer,
        ForeignKey("publicacao_registros.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    legal_one_update_id = Column(Integer, nullable=False, unique=True, index=True)

    linked_lawsuit_id = Column(Integer, nullable=True, index=True)
    linked_lawsuit_cnj = Column(String, nullable=True, index=True)
    linked_office_id = Column(Integer, nullable=True, index=True)
    publication_date = Column(String, nullable=True, index=True)

    source_record_status = Column(String, nullable=False, index=True)
    target_status = Column(String, nullable=False, index=True)
    queue_status = Column(String, nullable=False, default=QUEUE_STATUS_PENDING, index=True)

    attempt_count = Column(Integer, nullable=False, default=0)
    last_run_id = Column(
        Integer,
        ForeignKey("publicacao_tratamento_execucoes.id"),
        nullable=True,
        index=True,
    )
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    treated_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    last_response = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    record = relationship("PublicationRecord", back_populates="treatment_item")
    last_run = relationship("PublicationTreatmentRun", foreign_keys=[last_run_id])
