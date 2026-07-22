"""Modelos do módulo "Minha Equipe" (Performance de Equipes).

Dashboard de desempenho por pessoa a partir das tarefas do Legal One. As métricas
são lidas dessas tabelas (sem re-bater a API a cada acesso). Ver
`docs/performance-equipes-plano.md`.

Tabelas (prefixo perf*):
- perf_pessoa            — roster (nome/cargo/squad/posição).
- perf_l1_tarefa         — uma linha por tarefa do L1.
- perf_subtipo_categoria — natureza de cada subtipo (operacional/profundo/ruído).
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.db.session import Base


# Cargos (CARGO na planilha) — definem o conjunto de tarefas e o benchmark.
CARGO_ADVOGADO = "Advogado(a)"
CARGO_ESTAGIARIO = "Estagiário(a)"
CARGO_ASSISTENTE = "Assistente"

# Natureza do subtipo — define QUAL métrica vale:
#   operacional -> cadência/ócio/throughput (tarefa de alta frequência, em lote);
#   profundo    -> volume/cycle time/prazo  (tarefa pesada e esparsa);
#   ruido       -> cauda longa rara (fora das métricas finas, vira "Outros").
CAT_OPERACIONAL = "operacional"
CAT_PROFUNDO = "profundo"
CAT_RUIDO = "ruido"


class PerfPessoa(Base):
    __tablename__ = "perf_pessoa"

    id = Column(Integer, primary_key=True)
    nome = Column(String, nullable=False)
    # Nome normalizado (minúsculo, sem acento, espaços colapsados) — chave de
    # join com o "Cumprido por"/"Envolvido" do L1, que varia em acento.
    nome_norm = Column(String, nullable=False, unique=True, index=True)
    cargo = Column(String, nullable=True)
    squad = Column(String, nullable=True)
    posicao = Column(String, nullable=True)
    # Setor/supervisão (slug, ex.: 'bb-reu') — agrupa abas da planilha de squads.
    equipe = Column(String, nullable=True, index=True)
    is_supervisor = Column(Boolean, nullable=False, server_default="false")
    ativo = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class PerfTarefa(Base):
    __tablename__ = "perf_l1_tarefa"

    id = Column(Integer, primary_key=True)
    l1_task_id = Column(BigInteger, nullable=True)
    pessoa_id = Column(Integer, ForeignKey("perf_pessoa.id"), nullable=True, index=True)
    cumprido_por_nome = Column(String, nullable=True)
    envolvido_nome = Column(String, nullable=True)
    escritorio = Column(String, nullable=True)
    tipo = Column(String, nullable=True)
    subtipo = Column(String, nullable=True, index=True)
    status = Column(String, nullable=True, index=True)
    cadastrado_em = Column(DateTime(timezone=True), nullable=True)
    concluido_em = Column(DateTime(timezone=True), nullable=True, index=True)
    prazo_previsto = Column(DateTime(timezone=True), nullable=True)
    pasta = Column(String, nullable=True)
    cnj = Column(String, nullable=True)
    uf = Column(String, nullable=True)
    ingested_at = Column(DateTime(timezone=True), server_default=func.now())


class PerfSubtipoCategoria(Base):
    __tablename__ = "perf_subtipo_categoria"

    subtipo = Column(String, primary_key=True)
    categoria = Column(String, nullable=False, server_default=CAT_PROFUNDO)
    volume = Column(Integer, nullable=True)
    densidade = Column(Float, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class PerfBoardTarefa(Base):
    """Curadoria do board 'Tarefas mais importantes' por time. Quando há linhas
    pra um time, o board mostra EXATAMENTE esses subtipos (na ordem); sem linhas,
    cai no default top-N por volume."""

    __tablename__ = "perf_board_tarefa"

    id = Column(Integer, primary_key=True)
    team = Column(String, nullable=False, index=True)
    subtipo = Column(String, nullable=False)
    ordem = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("team", "subtipo", name="uq_board_tarefa"),)


class PerfCancelJob(Base):
    """Job de cancelamento em lote de duplicadas (fase B). Status PERSISTIDO pra
    o polling enxergar o progresso mesmo com vários workers do uvicorn — a thread
    que cancela roda num worker; qualquer worker lê o status desta tabela."""

    __tablename__ = "perf_cancel_job"

    id = Column(String, primary_key=True)  # uuid hex
    team = Column(String, nullable=True)
    subtipo = Column(String, nullable=True)
    status = Column(String, nullable=False, server_default="running")  # running|aborting|done
    fase = Column(String, nullable=False, server_default="scanning")  # scanning|cancelling|done
    scan_total = Column(Integer, nullable=False, server_default="0")  # pastas-candidatas a varrer
    scan_feito = Column(Integer, nullable=False, server_default="0")
    total = Column(Integer, nullable=False, server_default="0")  # tarefas reais a cancelar (pós-varredura)
    feito = Column(Integer, nullable=False, server_default="0")
    cancelled = Column(Integer, nullable=False, server_default="0")
    preservadas = Column(Integer, nullable=False, server_default="0")  # já encerradas/canceladas
    falhas = Column(Integer, nullable=False, server_default="0")
    erros = Column(JSONB, nullable=True)
    iniciado_em = Column(DateTime(timezone=True), server_default=func.now())
    terminado_em = Column(DateTime(timezone=True), nullable=True)


class PerfCancelWhitelist(Base):
    """Subtipos liberados pro cancelamento AUTOMÁTICO de duplicadas (rotina da
    madrugada). Incrementável pela UI. Começa só com 'Agendar Prazos - Banco
    Master' (o desvio de fluxo conhecido) — cancelar dup de qualquer tipo seria
    arriscado (2 pendentes do mesmo tipo pode ser legítimo)."""

    __tablename__ = "perf_cancel_whitelist"

    id = Column(Integer, primary_key=True)
    subtipo = Column(String, nullable=False, unique=True)
    ativo = Column(Boolean, nullable=False, server_default="true")
    criado_em = Column(DateTime(timezone=True), server_default=func.now())
    criado_por = Column(String, nullable=True)


class PerfCancelMassaLog(Base):
    """Auditoria de cada execução da rotina de cancelamento em massa de
    duplicadas (madrugada, sobre o pool fresco). LOG TOTAL: contadores +
    breakdown por subtipo em `detalhe`."""

    __tablename__ = "perf_cancel_massa_log"

    id = Column(Integer, primary_key=True)
    iniciado_em = Column(DateTime(timezone=True), server_default=func.now())
    terminado_em = Column(DateTime(timezone=True), nullable=True)
    status = Column(String, nullable=False, server_default="running")  # running|done|erro
    dry_run = Column(Boolean, nullable=False, server_default="false")
    origem = Column(String, nullable=True)  # scheduler | manual
    total_candidatos = Column(Integer, nullable=False, server_default="0")
    cancelled = Column(Integer, nullable=False, server_default="0")
    preservadas = Column(Integer, nullable=False, server_default="0")
    falhas = Column(Integer, nullable=False, server_default="0")
    detalhe = Column(JSONB, nullable=True)


class PerfRelatorio(Base):
    """Relatório PDF gerado como JOB persistente — sobrevive à navegação/saída.

    Dispara (status=processando) → gera em background → guarda o PDF na linha
    (status=pronto). Cada usuário vê os seus (criado_por_id).
    """

    __tablename__ = "perf_relatorio"

    id = Column(Integer, primary_key=True)
    tipo = Column(String, nullable=False)  # 'setor' | 'pessoa'
    team = Column(String, nullable=True)  # slug do time (relatório de setor)
    pessoa_id = Column(Integer, nullable=True)
    label = Column(String, nullable=False)
    days = Column(Integer, nullable=False, server_default="30")
    status = Column(String, nullable=False, server_default="processando")  # processando|pronto|erro
    pdf = Column(LargeBinary, nullable=True)
    erro = Column(String, nullable=True)
    criado_por_id = Column(Integer, nullable=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())
    concluido_em = Column(DateTime(timezone=True), nullable=True)


class BalanceadorLog(Base):
    """Log de uma redistribuição executada — o que foi movido, de quem pra quem,
    quais tarefas. Gerado no 'Aplicar' e listado na aba Relatórios.

    MOCK: origem='mock' (sem escrita no L1). Na versão real, origem='l1' e o
    detalhe carrega o resultado por tarefa (reatribuída via API/Workflow).
    """

    __tablename__ = "balanceador_log"

    id = Column(Integer, primary_key=True)
    team = Column(String, nullable=True, index=True)
    criado_por_id = Column(Integer, nullable=True)
    criado_por_nome = Column(String, nullable=True)
    total_movimentos = Column(Integer, nullable=False, server_default="0")
    total_tarefas = Column(Integer, nullable=False, server_default="0")
    origem = Column(String, nullable=False, server_default="mock")
    detalhe = Column(JSONB, nullable=True)  # lista de movimentos (from/to/subtipo/qtd/tasks)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())


class BalanceadorFilaPref(Base):
    """Destinos RECORRENTES da distribuição em fila, por (origem, subtipo).

    Aprende com cada 'Distribuir' (incrementa `vezes`) e, nas próximas vezes que o
    supervisor for distribuir aquele subtipo daquela origem, sugere os habituais
    no topo da lista. Identidade do alvo por NOME (estável entre id-spaces)."""

    __tablename__ = "balanceador_fila_pref"

    id = Column(Integer, primary_key=True)
    team = Column(String, nullable=True, index=True)
    origem_pessoa_id = Column(Integer, nullable=False, index=True)
    subtipo = Column(String, nullable=False)
    alvo_id = Column(Integer, nullable=True)
    alvo_nome = Column(String, nullable=False)
    vezes = Column(Integer, nullable=False, server_default="0")
    ultimo_uso = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("origem_pessoa_id", "subtipo", "alvo_nome", name="uq_fila_pref"),
    )


class BalanceadorReatribuirJob(Base):
    """Job de reatribuição EM LOTE de tarefas (Balanceador) — ESCRITA REAL no L1.

    Espelha o padrão do perf_cancel_job: roda numa thread daemon, progresso
    PERSISTIDO nesta tabela pro polling enxergar mesmo com vários workers do
    uvicorn. Buckets do resultado:
      - reatribuidas          → PATCH /Tasks participants OK (tarefa normal);
      - workflow_bloqueadas   → API trava (modelo de procedimento do workflow),
                                exige RPA/UI — fica separado pra tratamento manual;
      - falhas                → erro de rede/HTTP inesperado.
    Suporta abort (status 'aborting', checado entre tarefas). dry_run só lê os
    participantes atuais e classifica (não grava)."""

    __tablename__ = "balanceador_reatribuir_job"

    id = Column(String, primary_key=True)  # uuid hex
    team = Column(String, nullable=True, index=True)
    status = Column(String, nullable=False, server_default="running")  # running|aborting|done
    dry_run = Column(Boolean, nullable=False, server_default="false")
    total = Column(Integer, nullable=False, server_default="0")
    feito = Column(Integer, nullable=False, server_default="0")
    reatribuidas = Column(Integer, nullable=False, server_default="0")
    workflow_bloqueadas = Column(Integer, nullable=False, server_default="0")
    falhas = Column(Integer, nullable=False, server_default="0")
    detalhe = Column(JSONB, nullable=True)  # lista por tarefa: {task_id, to_id, to_nome, reason, http}
    criado_por_id = Column(Integer, nullable=True)
    criado_por_nome = Column(String, nullable=True)
    iniciado_em = Column(DateTime(timezone=True), server_default=func.now())
    terminado_em = Column(DateTime(timezone=True), nullable=True)


# ── Reagendamentos (adiamentos de prazo) — bracket 07h/19h ──────────────────
# Detecta adiamento comparando duas fotos do MESMO dia: a foto da manhã (baseline
# em perf_prazo_manha) vs. o estado da noite (perf_l1_tarefa recém-ingerida). Onde
# a conclusão prevista da tarefa foi EMPURRADA pra frente durante o dia = adiamento.


class PerfPrazoManha(Base):
    """Baseline da MANHÃ: prazo de cada tarefa pendente às ~07h. Replace total
    todo dia (o job da manhã limpa e regrava). A noite compara contra isto."""

    __tablename__ = "perf_prazo_manha"

    l1_task_id = Column(BigInteger, primary_key=True)
    dia = Column(DateTime(timezone=True), nullable=False, index=True)  # dia do bracket (BRT, 00:00)
    prazo = Column(DateTime(timezone=True), nullable=True)
    pessoa_id = Column(Integer, nullable=True, index=True)
    pessoa_nome = Column(String, nullable=True)
    equipe = Column(String, nullable=True, index=True)
    subtipo = Column(String, nullable=True)
    pasta = Column(String, nullable=True)
    cnj = Column(String, nullable=True)
    capturado_em = Column(DateTime(timezone=True), server_default=func.now())


class PerfReagendamento(Base):
    """Um evento de ADIAMENTO detectado no bracket do dia (manhã → noite).

    Só adiamentos (prazo_para > prazo_de). Antecipações são ignoradas na
    detecção. `era_fatal_hoje` = na manhã o prazo vencia HOJE e à noite foi
    empurrado — o pior caso ("empurrou com a barriga"). Idempotente por dia: o
    job da noite apaga os eventos do dia antes de regravar."""

    __tablename__ = "perf_reagendamento"

    id = Column(Integer, primary_key=True)
    dia = Column(DateTime(timezone=True), nullable=False, index=True)  # dia do bracket (BRT, 00:00)
    l1_task_id = Column(BigInteger, nullable=True, index=True)
    pessoa_id = Column(Integer, nullable=True, index=True)
    pessoa_nome = Column(String, nullable=True)
    equipe = Column(String, nullable=True, index=True)
    subtipo = Column(String, nullable=True)
    pasta = Column(String, nullable=True)
    cnj = Column(String, nullable=True)
    prazo_de = Column(DateTime(timezone=True), nullable=True)     # prazo da manhã
    prazo_para = Column(DateTime(timezone=True), nullable=True)   # prazo da noite
    dias_adiado = Column(Integer, nullable=True)                  # diferença em dias (BRT)
    era_fatal_hoje = Column(Boolean, nullable=False, server_default="false")
    detectado_em = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("dia", "l1_task_id", name="uq_perf_reag_dia_task"),
    )
