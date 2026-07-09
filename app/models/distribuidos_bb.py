"""Modelos do módulo Distribuídos BB (Banco do Brasil).

Traz o RPA de processos distribuídos do BB pra dentro do Flow: coleta no
portal (via OneLog), ciência controlada, distribuição de responsáveis e
cadastro no Legal One. Tudo com LOG e auditoria por processo.

Prefixo de tabela/migration: `bbd_` / `bbd*`.

Convenções da casa (ver onerequest.py / citacoes_bm.py):
- Status como CONSTANTES string module-level + coluna String (não enum).
- Timestamps DateTime(timezone=True) com server_default=func.now().
- JSON via helper jsonb() (Postgres JSONB / SQLite JSON nos testes).
- FK de responsável → legal_one_users.id (ondelete=SET NULL).
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base
from app.db.types import jsonb


# ─────────────────────────────────────────────────────────────────────────
# Constantes de status (normalizadas, em pt-BR onde o operador vê)
# ─────────────────────────────────────────────────────────────────────────

# Ciclo de vida de um processo distribuído
PROC_COLETADO = "COLETADO"              # extraído do portal, ainda sem ciência
PROC_CIENCIA_DADA = "CIENCIA_DADA"      # ciência confirmada no BB (SIM)
PROC_DISTRIBUIDO = "DISTRIBUIDO"        # responsável/escritório definidos
PROC_CONTATOS_RESOLVIDOS = "CONTATOS_RESOLVIDOS"  # envolvidos casados no L1
PROC_CADASTRADO = "CADASTRADO"          # pasta criada no Legal One
PROC_ERRO = "ERRO"                       # falha em alguma etapa
PROC_REVISAO = "REVISAO"                 # pendência que precisa de humano

# Status de uma execução de coleta
RUN_EM_ANDAMENTO = "EM_ANDAMENTO"
RUN_CONCLUIDO = "CONCLUIDO"
RUN_ERRO = "ERRO"

# Resolução de contato do envolvido no Legal One
CONTATO_NAO_RESOLVIDO = "NAO_RESOLVIDO"
CONTATO_RESOLVIDO = "RESOLVIDO"          # já existia no L1 (achado por CPF/CNPJ)
CONTATO_CRIADO = "CRIADO"                # criado agora no L1
CONTATO_AMBIGUO = "AMBIGUO"              # múltiplos candidatos — precisa revisão

# Níveis de evento do log/auditoria
NIVEL_INFO = "INFO"
NIVEL_SUCESSO = "SUCESSO"
NIVEL_AVISO = "AVISO"
NIVEL_ERRO = "ERRO"

# Seções normalizadas do log (o operador filtra por elas — sem termos técnicos)
SECAO_COLETA = "Coleta"
SECAO_EXTRACAO = "Extração"
SECAO_CIENCIA = "Ciência"
SECAO_DISTRIBUICAO = "Distribuição"
SECAO_ENVOLVIDOS = "Envolvidos"
SECAO_CONTATOS = "Contatos"
SECAO_CADASTRO = "Cadastro"
SECAO_CONFIGURACAO = "Configuração"
SECAO_SESSAO = "Sessão"  # login/OneLog


# ─────────────────────────────────────────────────────────────────────────
# Configuração editável (tabelas que substituem os hardcodes do script)
# ─────────────────────────────────────────────────────────────────────────


class BbEscritorio(Base):
    """Escritório/fila de distribuição — a base de tudo é o escritório responsável.

    Substitui os hardcodes de `gerar_planilha.py` (mapa polo→escritório,
    responsável fixo de trabalhista, observação padrão). Cada linha é um
    destino de roteamento: por polo (Passivo/Ativo/Neutro) e/ou natureza.
    """

    __tablename__ = "bbd_escritorios"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(120), nullable=False)  # ex.: "Réu", "Autor", "Trabalhista"
    # Caminho completo no Legal One (usado no cadastro/planilha)
    escritorio_path = Column(Text, nullable=False)

    # Critérios de roteamento (o motor escolhe este escritório quando batem)
    criterio_polo = Column(String(20), nullable=True)      # Passivo | Ativo | Neutro
    criterio_natureza = Column(String(80), nullable=True)  # ex.: "Trabalhista"

    # Responsável fixo (quando a fila não é round-robin — ex.: Trabalhista)
    responsavel_fixo_user_id = Column(
        Integer, ForeignKey("legal_one_users.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    # Observação padrão gravada no processo (Cadastro / Ajuizamento / Reterceirizado)
    observacao_padrao = Column(String(40), nullable=True)

    ativo = Column(Boolean, nullable=False, server_default="true")
    ordem = Column(Integer, nullable=False, server_default="0")

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    responsaveis = relationship(
        "BbResponsavel", back_populates="escritorio",
        cascade="all, delete-orphan", order_by="BbResponsavel.ordem",
    )
    responsavel_fixo = relationship("LegalOneUser", foreign_keys=[responsavel_fixo_user_id])


class BbResponsavel(Base):
    """Fila de round-robin de responsáveis por escritório (editável na UI)."""

    __tablename__ = "bbd_responsaveis"
    __table_args__ = (
        UniqueConstraint("escritorio_id", "user_id", name="uq_bbd_resp_escritorio_user"),
    )

    id = Column(Integer, primary_key=True, index=True)
    escritorio_id = Column(
        Integer, ForeignKey("bbd_escritorios.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id = Column(
        Integer, ForeignKey("legal_one_users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    ordem = Column(Integer, nullable=False, server_default="0")
    ativo = Column(Boolean, nullable=False, server_default="true")

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    escritorio = relationship("BbEscritorio", back_populates="responsaveis")
    user = relationship("LegalOneUser", foreign_keys=[user_id])


class BbEquipeMembro(Base):
    """Equipe padrão de um responsável — vira 'Envolvidos' no cadastro/planilha.

    Substitui o `data.json` (responsável → membros com classificação).
    """

    __tablename__ = "bbd_equipe_membros"
    __table_args__ = (
        UniqueConstraint(
            "responsavel_user_id", "membro_user_id", "classificacao",
            name="uq_bbd_equipe_membro",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    responsavel_user_id = Column(
        Integer, ForeignKey("legal_one_users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    membro_user_id = Column(
        Integer, ForeignKey("legal_one_users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    classificacao = Column(String(80), nullable=False)  # ex.: "Advogado", "Assistente"
    ativo = Column(Boolean, nullable=False, server_default="true")

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    responsavel = relationship("LegalOneUser", foreign_keys=[responsavel_user_id])
    membro = relationship("LegalOneUser", foreign_keys=[membro_user_id])


class BbDistribuicaoEstado(Base):
    """Ponteiro persistido do round-robin por escritório.

    Mata o `random.shuffle` a cada run: o rodízio equilibra ENTRE execuções.
    """

    __tablename__ = "bbd_distribuicao_estado"

    escritorio_id = Column(
        Integer, ForeignKey("bbd_escritorios.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ultimo_responsavel_id = Column(
        Integer, ForeignKey("legal_one_users.id", ondelete="SET NULL"), nullable=True,
    )
    ultimo_indice = Column(Integer, nullable=False, server_default="-1")
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )


# ─────────────────────────────────────────────────────────────────────────
# Dados capturados
# ─────────────────────────────────────────────────────────────────────────


class BbRun(Base):
    """Cabeçalho de uma execução de coleta (progresso + auditoria)."""

    __tablename__ = "bbd_runs"

    id = Column(Integer, primary_key=True, index=True)
    data_inicial = Column(String(10), nullable=True)  # DD/MM/AAAA (como o operador informa)
    data_final = Column(String(10), nullable=True)

    disparado_por_user_id = Column(
        Integer, ForeignKey("legal_one_users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    # Modo seguro: se False, a ciência (SIM) NÃO é dada — só coleta.
    confirmar_ciencia = Column(Boolean, nullable=False, server_default="false")

    status = Column(String, nullable=False, server_default=RUN_EM_ANDAMENTO, index=True)

    total_coletados = Column(Integer, nullable=False, server_default="0")
    total_ciencia = Column(Integer, nullable=False, server_default="0")
    total_distribuidos = Column(Integer, nullable=False, server_default="0")
    total_cadastrados = Column(Integer, nullable=False, server_default="0")
    total_erros = Column(Integer, nullable=False, server_default="0")

    iniciado_em = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    concluido_em = Column(DateTime(timezone=True), nullable=True)
    erro = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    disparado_por = relationship("LegalOneUser", foreign_keys=[disparado_por_user_id])
    processos = relationship("BbProcesso", back_populates="run")


class BbProcesso(Base):
    """Um processo distribuído do BB (uma notificação lida no portal)."""

    __tablename__ = "bbd_processos"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_bbd_proc_fingerprint"),
    )

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("bbd_runs.id", ondelete="SET NULL"), nullable=True, index=True)

    # Identidade / dedup (fingerprint = cnj or npj)
    cnj = Column(String(40), nullable=True, index=True)
    npj = Column(String(60), nullable=True, index=True)
    notificacao_seq = Column(Integer, nullable=True)
    fingerprint = Column(String(80), nullable=False, index=True)

    # Dados capturados no portal (normalizados)
    polo = Column(String(20), nullable=True)       # Passivo | Ativo | Neutro
    posicao = Column(String(20), nullable=True)    # Réu | Autor | Interessado
    natureza = Column(String(120), nullable=True)
    acao = Column(Text, nullable=True)
    valor_causa = Column(Numeric(18, 2), nullable=True)
    data_ajuizamento = Column(String(30), nullable=True)
    situacao = Column(String(120), nullable=True)
    tramitacao = Column(String(120), nullable=True)
    advogado = Column(Text, nullable=True)
    adverso_principal = Column(Text, nullable=True)

    # Distribuição
    responsavel_user_id = Column(
        Integer, ForeignKey("legal_one_users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    escritorio_id = Column(
        Integer, ForeignKey("bbd_escritorios.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    escritorio_path = Column(Text, nullable=True)
    observacao = Column(String(40), nullable=True)  # Cadastro | Ajuizamento | Reterceirizado

    # Ciclo de vida
    status = Column(String, nullable=False, server_default=PROC_COLETADO, index=True)
    ciencia_dada_em = Column(DateTime(timezone=True), nullable=True)
    l1_lawsuit_id = Column(Integer, nullable=True, index=True)
    l1_workflow_task_id = Column(Integer, nullable=True)
    erro = Column(Text, nullable=True)

    # Auditoria bruta (capa do NPJ / HTML de origem)
    raw = Column(jsonb(), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    run = relationship("BbRun", back_populates="processos")
    responsavel = relationship("LegalOneUser", foreign_keys=[responsavel_user_id])
    escritorio = relationship("BbEscritorio", foreign_keys=[escritorio_id])
    envolvidos = relationship(
        "BbEnvolvido", back_populates="processo",
        cascade="all, delete-orphan", order_by="BbEnvolvido.id",
    )
    eventos = relationship(
        "BbEvento", back_populates="processo",
        cascade="all, delete-orphan", order_by="BbEvento.created_at",
    )


class BbEnvolvido(Base):
    """Envolvido capturado na capa do NPJ (parte, avalista, advogado…)."""

    __tablename__ = "bbd_envolvidos"

    id = Column(Integer, primary_key=True, index=True)
    processo_id = Column(
        Integer, ForeignKey("bbd_processos.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    nome = Column(Text, nullable=False)
    papel = Column(String(80), nullable=True)         # normalizado: Parte, Avalista, Advogado…
    cpf_cnpj = Column(String(20), nullable=True, index=True)
    tipo_pessoa = Column(String(2), nullable=True)     # PF | PJ

    # Resolução de contato no Legal One
    status_contato = Column(String, nullable=False, server_default=CONTATO_NAO_RESOLVIDO, index=True)
    l1_contact_id = Column(Integer, nullable=True, index=True)

    raw = Column(jsonb(), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    processo = relationship("BbProcesso", back_populates="envolvidos")


class BbEvento(Base):
    """LOG universal + auditoria: cada seção, cada dado, cada passo.

    Uma linha por acontecimento relevante. O operador filtra por `secao`,
    `nivel` e por processo. `dados` carrega o dado capturado normalizado.
    """

    __tablename__ = "bbd_eventos"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("bbd_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    processo_id = Column(
        Integer, ForeignKey("bbd_processos.id", ondelete="CASCADE"), nullable=True, index=True,
    )

    secao = Column(String(40), nullable=False, index=True)  # Coleta, Ciência, Distribuição…
    acao = Column(String(120), nullable=True)               # rótulo curto pt-BR
    nivel = Column(String(12), nullable=False, server_default=NIVEL_INFO, index=True)
    mensagem = Column(Text, nullable=False)                 # legível pro operador
    dados = Column(jsonb(), nullable=True)                  # dado capturado/normalizado

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    processo = relationship("BbProcesso", back_populates="eventos")
