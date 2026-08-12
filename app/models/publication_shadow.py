"""Shadow mode: a decisão que o sistema TERIA tomado, gravada em paralelo.

Serve pra responder com número — e não com estimativa — se dá pra automatizar
parte do tratamento de publicações. O sistema prevê, o operador decide sem
saber da previsão, e o placar sai do encontro dos dois.

Regra metodológica que o modelo materializa: a previsão é travada ANTES da
ação humana e carrega os sinais congelados do instante. Recalcular sinal
depois responde outra pergunta — "existe tarefa aberta da mesma família?" tem
resposta diferente hoje e amanhã.
"""

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String,
)
from sqlalchemy.sql import func

from app.db.session import Base
from app.db.types import jsonb

# Desfechos possíveis (espelham o status do registro).
SHADOW_AGENDAR = "AGENDADO"
SHADOW_IGNORAR = "IGNORADO"

# Confiança da previsão. Só ALTA é candidata a virar automação — as outras
# existem pra medir onde o sistema ainda não sabe.
CONF_ALTA = "alta"
CONF_MEDIA = "media"
CONF_BAIXA = "baixa"


class PublicacaoShadowDecisao(Base):
    __tablename__ = "publicacao_shadow_decisao"

    id = Column(Integer, primary_key=True)
    record_id = Column(
        Integer,
        ForeignKey("publicacao_registros.id", ondelete="CASCADE"),
        nullable=False, index=True, unique=True,
    )

    # ── Previsão ────────────────────────────────────────────────────────
    previsto = Column(String(20), nullable=False)
    previsto_motivo = Column(String(40), nullable=True)
    confianca = Column(String(10), nullable=False)
    # Qual regra decidiu — pra saber QUAL parte da política acerta e qual erra.
    regra = Column(String(60), nullable=True)
    sinais = Column(jsonb(), nullable=True)
    previsto_em = Column(DateTime(timezone=True), server_default=func.now(),
                         nullable=False)

    # ── Desfecho real ───────────────────────────────────────────────────
    real = Column(String(20), nullable=True)
    real_motivo = Column(String(40), nullable=True)
    real_por = Column(String(120), nullable=True)
    real_em = Column(DateTime(timezone=True), nullable=True)
    acertou = Column(Boolean, nullable=True)
