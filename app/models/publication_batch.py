"""
Modelo para rastrear envios à Message Batches API da Anthropic.

Cada linha representa um lote enviado ao endpoint /v1/messages/batches
para classificação assíncrona de publicações em volume.
"""

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base


# Status do lote no nosso sistema
PUB_BATCH_STATUS_SUBMITTED = "ENVIADO"          # criado na Anthropic, aguardando processamento
PUB_BATCH_STATUS_IN_PROGRESS = "EM_PROCESSAMENTO"  # Anthropic está processando
PUB_BATCH_STATUS_READY = "PRONTO"               # resultados disponíveis para download
PUB_BATCH_STATUS_APPLIED = "APLICADO"           # resultados baixados e aplicados no banco
PUB_BATCH_STATUS_FAILED = "FALHA"               # erro no envio ou processamento
PUB_BATCH_STATUS_CANCELLED = "CANCELADO"        # cancelado pelo usuário

# Status da Anthropic (message_batches API)
ANTHROPIC_STATUS_IN_PROGRESS = "in_progress"
ANTHROPIC_STATUS_ENDED = "ended"
ANTHROPIC_STATUS_CANCELING = "canceling"
ANTHROPIC_STATUS_CANCELED = "canceled"


class PublicationBatchClassification(Base):
    """
    Rastreia um lote enviado à Message Batches API da Anthropic.

    Fluxo típico:
      1. submit_batch() cria registro com status ENVIADO + anthropic_batch_id
      2. check_batch_status() atualiza para EM_PROCESSAMENTO / PRONTO
      3. apply_batch_results() baixa resultados e atualiza PublicationRecord
    """

    __tablename__ = "publicacao_batches_classificacao"

    id = Column(Integer, primary_key=True, index=True)

    # ID do lote na Anthropic (ex: "msgbatch_01HkLFnP...")
    anthropic_batch_id = Column(String, nullable=True, index=True)

    # Status interno do lote
    status = Column(
        String,
        nullable=False,
        default=PUB_BATCH_STATUS_SUBMITTED,
        index=True,
    )

    # Status da Anthropic (in_progress, ended, etc.)
    anthropic_status = Column(String, nullable=True)

    # Quantidade de registros enviados
    total_records = Column(Integer, nullable=False, default=0)

    # Contadores de resultado
    succeeded_count = Column(Integer, default=0)
    errored_count = Column(Integer, default=0)
    expired_count = Column(Integer, default=0)
    canceled_count = Column(Integer, default=0)

    # IDs dos PublicationRecord incluídos neste batch (JSON array)
    record_ids = Column(JSON, nullable=True)

    # Metadata opcional (custom_id → record_id mapping, etc)
    batch_metadata = Column(JSON, nullable=True)

    # Modelo usado (ex: claude-haiku-4-5-20251001)
    model_used = Column(String, nullable=True)

    # Email do usuário que disparou
    requested_by_email = Column(String, nullable=True, index=True)

    # URL para download dos resultados (após ended)
    results_url = Column(Text, nullable=True)

    # Mensagem de erro (quando aplicável)
    error_message = Column(Text, nullable=True)

    # Detalhes dos erros por item (JSON: {record_id: error_reason})
    error_details = Column(JSON, nullable=True)

    # ── Usage (tokens) somado dos itens do lote — migration pub009 ────────
    # Guardamos TOKEN, não custo: token é fato e não envelhece, preço muda.
    # O custo sai de app/services/classifier/anthropic_pricing.py na leitura.
    #
    # Atenção ao ler: `usage_input_tokens` NÃO é a entrada total. Por definição
    # da API é só o que vem DEPOIS do último breakpoint de cache. A entrada
    # total é input + cache_read + cache_creation. Sem prompt caching ligado,
    # os dois campos de cache ficam em 0 e o input é a entrada inteira.
    usage_input_tokens = Column(BigInteger, nullable=True)
    usage_output_tokens = Column(BigInteger, nullable=True)
    usage_cache_read_tokens = Column(BigInteger, nullable=True)
    usage_cache_creation_tokens = Column(BigInteger, nullable=True)
    # Quantos itens de fato trouxeram usage — distingue soma completa de
    # parcial (item que falhou não traz usage).
    usage_itens_contados = Column(Integer, nullable=True)

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    applied_at = Column(DateTime(timezone=True), nullable=True)
