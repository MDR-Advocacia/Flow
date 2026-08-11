from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base


ONB_STATUS_RECEBIDA = "RECEBIDA"
ONB_STATUS_CONCILIADA_AUTO = "CONCILIADA_AUTO"
ONB_STATUS_PENDENTE_FLOW = "PENDENTE_FLOW"
ONB_STATUS_PENDENTE_DOCUMENTO = "PENDENTE_DOCUMENTO"
ONB_STATUS_REVISAO = "REVISAO"
ONB_STATUS_ERRO = "ERRO"

ONB_ACTION_SEM_TRATAMENTO_NOTIFY = "SEM_TRATAMENTO_NOTIFY"
ONB_ACTION_TRATAR_DOCUMENTO_FLOW = "TRATAR_DOCUMENTO_FLOW"
ONB_ACTION_REVISAR_MANUALMENTE = "REVISAR_MANUALMENTE"


class OneNotifyBBNotification(Base):
    __tablename__ = "onenotify_bb_notificacoes"

    id = Column(Integer, primary_key=True, index=True)
    external_group_id = Column(String, nullable=False, unique=True, index=True)
    source = Column(String, nullable=False, default="ONENOTIFY_BB", index=True)
    schema_version = Column(String, nullable=True)

    notify_ids = Column(JSON, nullable=True)
    npj = Column(String, nullable=True, index=True)
    data_notificacao = Column(String, nullable=True, index=True)
    notification_date_iso = Column(String, nullable=True, index=True)
    publication_date = Column(String, nullable=True, index=True)

    numero_processo_cnj = Column(String, nullable=True, index=True)
    cnj_publicacao = Column(String, nullable=True, index=True)
    cnj_principal_notify = Column(String, nullable=True, index=True)
    cnj_divergent = Column(Boolean, nullable=False, default=False, index=True)

    adverso_principal = Column(String, nullable=True)
    polo = Column(String, nullable=True)
    posicao_cliente = Column(String, nullable=True, index=True)
    tipos_notificacao = Column(JSON, nullable=True)

    rpa_status = Column(JSON, nullable=True)
    bb_ciencia_status = Column(JSON, nullable=True)
    human_status = Column(JSON, nullable=True)
    flow_sync_status = Column(JSON, nullable=True)
    status_legacy = Column(JSON, nullable=True)

    flow_status = Column(String, nullable=False, default=ONB_STATUS_RECEBIDA, index=True)
    action_suggested = Column(String, nullable=True, index=True)
    match_strategy = Column(String, nullable=True)
    match_score = Column(Float, nullable=True, index=True)
    match_reason = Column(Text, nullable=True)

    matched_publication_record_id = Column(
        Integer,
        ForeignKey("publicacao_registros.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    matched_legal_one_update_id = Column(Integer, nullable=True, index=True)
    matched_publication_status = Column(String, nullable=True, index=True)

    andamentos = Column(JSON, nullable=True)
    documentos = Column(JSON, nullable=True)
    conteudo = Column(JSON, nullable=True)
    raw_payload = Column(JSON, nullable=True)
    text_content = Column(Text, nullable=True)
    document_summary = Column(JSON, nullable=True)

    last_error = Column(Text, nullable=True)
    received_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    reconciled_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    matched_publication = relationship("PublicationRecord")
