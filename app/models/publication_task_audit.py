"""Auditoria de agendamento de tarefas de Publicações.

Grava, por TAREFA criada no Legal One, o payload EXATO que foi enviado +
quem agendou + a proposta automática + o diff entre as duas (flag de override
humano). Motivação: o L1 registra o criador como "Sistema" (usuário da API),
então só o Flow consegue dizer QUEM (operador) agendou/modificou e SE divergiu
da sugestão automática. Append-only — nunca atualiza/deleta.
"""

from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.sql import func

from app.db.session import Base
from app.db.types import jsonb


class PublicationTaskAudit(Base):
    __tablename__ = "publicacao_tarefa_audit"

    id = Column(Integer, primary_key=True, index=True)

    lawsuit_id = Column(Integer, nullable=True, index=True)  # None no fluxo avulso
    publication_record_id = Column(Integer, nullable=True, index=True)
    subtype_id = Column(Integer, nullable=True)
    created_task_id = Column(Integer, nullable=True, index=True)

    # O que foi REALMENTE enviado ao L1 (após override do operador + squad
    # routing + defaults) e a proposta automática original, pra comparação.
    sent_payload = Column(jsonb(), nullable=True)
    proposed_payload = Column(jsonb(), nullable=True)

    # True quando o enviado divergiu da proposta em subtipo/escritório/responsável.
    override_detected = Column(Boolean, nullable=False, server_default="false", index=True)
    override_fields = Column(jsonb(), nullable=True)  # {campo: {proposto, enviado}}

    # O que o SISTEMA mudou mecanicamente antes do envio (bump de data pra
    # dia útil, defaults obrigatórios, corte de descrição) — categoria
    # separada do override humano (pub005). {campo: {antes, depois, motivo}}.
    system_adjustments = Column(jsonb(), nullable=True)

    # Por que o operador trocou o SUBTIPO proposto (pub007). Captura
    # estratégica: só ~51 casos/dia, e é o sinal que aponta template errado.
    # Nulo quando não houve troca ou o motivo não foi informado.
    subtipo_troca_motivo = Column(String(40), nullable=True)

    # ── Captura do conhecimento tácito (pub008) ────────────────────────
    # Delta da data em DIAS: negativo = o operador ANTECIPOU, positivo =
    # adiou. Gravado SEMPRE (custo zero de atrito) — 72% dos agendamentos
    # mexem na data e isso não era auditado. É onde vive a regra de prazo
    # ("contestação 2 dias antes", "próximo dia útil").
    data_delta_dias = Column(Integer, nullable=True)
    # Motivo pedido só no DESVIO ANÔMALO (fora de ±3 dias).
    data_troca_motivo = Column(String(40), nullable=True)
    # "Precisei abrir o processo pra decidir" — é O sinal do balde OPERADOR.
    consultou_autos = Column(Boolean, nullable=True)
    # Agendou apesar de haver tarefa da família aberta: a existente NÃO cobre.
    agendou_com_tarefa_aberta_motivo = Column(String(40), nullable=True)
    # Removeu bloco que o template propunha: o template propõe demais aqui.
    tarefa_removida_motivo = Column(String(40), nullable=True)
    # Quem agendou (snapshot do operador — o L1 só guarda "Sistema").
    scheduled_by_user_id = Column(Integer, nullable=True, index=True)
    scheduled_by_name = Column(String, nullable=True)
    scheduled_by_email = Column(String, nullable=True)
    scheduled_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
