"""Execuções do MOTOR da fila sem pasta (pub014).

Uma linha por rodada — noturna (disparada pela automação) ou manual (botão
na triagem). É o que dá barra de progresso à tela e trilha ao operador:
quantas eram, quantas saíram por regra (pauta), quantas a IA identificou,
quantas ganharam ficha, quantas falharam. Padrão da casa para execução
longa: server-backed, worker que auto-completa, contagem por status.
"""
from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.db.session import Base


class PublicacaoSemPastaRun(Base):
    __tablename__ = "publicacao_sem_pasta_runs"

    id = Column(Integer, primary_key=True, index=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    # running | done | failed
    status = Column(String(16), nullable=False, default="running", server_default="running")
    # "scheduler" ou o e-mail de quem clicou.
    requested_by = Column(String(120), nullable=True)
    # Run da automação noturna que disparou (quando foi ela).
    automation_run_id = Column(Integer, nullable=True, index=True)

    total_alvo = Column(Integer, nullable=False, default=0, server_default="0")
    processados = Column(Integer, nullable=False, default=0, server_default="0")
    pautas = Column(Integer, nullable=False, default=0, server_default="0")
    classificados = Column(Integer, nullable=False, default=0, server_default="0")
    fichas = Column(Integer, nullable=False, default=0, server_default="0")
    # Tarefas criadas no L1 sem passar pela mesa (tipos do mapa de
    # agendamento automático — hoje só Embargos à Execução).
    agendados = Column(Integer, nullable=False, default=0, server_default="0")
    erros = Column(Integer, nullable=False, default=0, server_default="0")
    ultimo_erro = Column(Text, nullable=True)
