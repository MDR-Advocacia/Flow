"""Análise de Risco BB Réu (arb_*).

Espelho persistente das tarefas do subtipo "Análise de Risco" agendadas pro
BB Réu, alimentado pela tabela `perf_l1_tarefa` (ingestão diária do Agenda
Analytics do L1) — o espelho do Minha Equipe é REPLACE diário, então esta
tabela é o histórico que sobrevive e onde a esteira de verificação no portal
do BB grava o resultado (a tarefa foi cumprida no L1, mas a análise foi FEITA
no portal?). Ver cards do módulo em docs/ e o plano da esteira.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)

from app.db.session import Base

# Estados da esteira de verificação no portal BB.
VERIF_PENDENTE = "PENDENTE"      # tarefa ainda aberta no L1 — nada a verificar
VERIF_NA_FILA = "NA_FILA"        # cumprida no L1 — aguardando verificação no portal
VERIF_VERIFICADA = "VERIFICADA"  # portal consultado, resultado gravado
VERIF_ERRO = "ERRO"              # falha na última tentativa — a esteira re-tenta
# Existe análise MAIS RECENTE do mesmo processo: esta saiu da auditoria. A
# pendência no portal é o estado ATUAL do processo — comparar com tarefa antiga
# gera falso divergente (caso real: análise cumprida em 02/2026 acusada como
# divergente porque a pendência aberta era da análise nova de 08/2026).
VERIF_SUPERADA = "SUPERADA"


class AnaliseRiscoTarefa(Base):
    __tablename__ = "arb_analise_risco_tarefa"

    id = Column(Integer, primary_key=True)
    # Chave natural: id da tarefa no Legal One (vem do espelho do Agenda Analytics).
    l1_task_id = Column(BigInteger, nullable=False, unique=True, index=True)

    # Capturado do espelho perf_l1_tarefa (atualizado a cada sync).
    subtipo = Column(String, nullable=True)
    responsavel_nome = Column(String, nullable=True, index=True)
    cumprida_por_nome = Column(String, nullable=True)
    npj = Column(String, nullable=True, index=True)  # coluna "pasta" do relatório
    cnj = Column(String, nullable=True)
    agendada_em = Column(DateTime(timezone=True), nullable=True)
    prazo = Column(DateTime(timezone=True), nullable=True)
    status_l1 = Column(String, nullable=True, index=True)  # Pendente | Cumprido
    concluida_em = Column(DateTime(timezone=True), nullable=True)

    # Esteira de verificação no portal BB (preenchida pelo worker do card 3).
    verif_status = Column(String, nullable=False, server_default=VERIF_PENDENTE, index=True)
    portal_analise_feita = Column(Boolean, nullable=True)
    portal_estado = Column(String, nullable=True)   # ex.: "Alçada 1"
    portal_exito = Column(String, nullable=True)    # ex.: "Provável"
    portal_verificado_em = Column(DateTime(timezone=True), nullable=True)
    verif_tentativas = Column(Integer, nullable=False, server_default="0")
    verif_ultimo_erro = Column(Text, nullable=True)
    # Cumprida no L1 SEM análise registrada no portal — o farol do supervisor.
    divergente = Column(Boolean, nullable=True, index=True)

    # Tratamento da divergência pelo supervisor (card 4).
    trat_status = Column(String, nullable=True)  # COBRADA | REAGENDADA | FALSO_POSITIVO
    trat_anotacao = Column(Text, nullable=True)
    trat_por_user_id = Column(Integer, ForeignKey("legal_one_users.id"), nullable=True)
    trat_em = Column(DateTime(timezone=True), nullable=True)

    criado_em = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_em = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
