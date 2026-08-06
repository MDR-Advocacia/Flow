"""Registro de utilização do sistema — base do relatório de adesão.

Guarda ROLLUP DIÁRIO, não log cru de requisição. O Flow tem auto-poll global
(telas que se atualizam sozinhas), então um registro por requisição encheria a
tabela de ruído: um supervisor com a tela aberta a tarde toda geraria centenas
de linhas sem ter feito nada além de deixar o monitor ligado. Uma linha por
(usuário, dia, módulo) responde o que o relatório precisa — com que frequência
entra e onde trabalha — sem esse ruído.

O que ESTA tabela não responde: o que a pessoa fez de fato. Isso vem dos
rastros de autoria que cada módulo já grava (quem agendou a publicação, quem
redistribuiu, quem tratou o prazo) e é cruzado no relatório. A distinção
importa: entrar e olhar é diferente de entrar e operar, e supervisor que só
consulta é uso legítimo — a maior parte do trabalho dele é leitura.
"""

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.sql import func

from app.db.session import Base


class UsoDiario(Base):
    """Uma linha por usuário/dia/módulo. Contador incrementado por upsert."""

    __tablename__ = "flow_uso_diario"

    user_id = Column(
        Integer,
        ForeignKey("legal_one_users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    dia = Column(Date, primary_key=True, index=True)
    # Nome de negócio ("Publicações", "Minha Equipe"), não o prefixo da rota:
    # o relatório é lido pelo administrativo, não por quem conhece a API.
    modulo = Column(String(60), primary_key=True)

    requisicoes = Column(Integer, nullable=False, server_default="0")
    primeira_em = Column(DateTime(timezone=True), nullable=True)
    ultima_em = Column(DateTime(timezone=True), nullable=True)

    atualizado_em = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )
