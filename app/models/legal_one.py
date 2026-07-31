from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text, DateTime, JSON
from app.db.types import jsonb
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base

class LegalOneTaskSubType(Base):
    __tablename__ = "legal_one_task_subtypes"

    id = Column(Integer, primary_key=True, index=True)
    external_id = Column(Integer, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    parent_type_external_id = Column(
        Integer,
        ForeignKey("legal_one_task_types.external_id"),
        index=True,
        nullable=False,
    )

    parent_type = relationship("LegalOneTaskType", back_populates="subtypes")


class LegalOneTaskType(Base):
    __tablename__ = "legal_one_task_types"

    id = Column(Integer, primary_key=True, index=True)
    external_id = Column(Integer, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)

    subtypes = relationship(
        "LegalOneTaskSubType",
        back_populates="parent_type",
        cascade="all, delete-orphan",
    )


class LegalOneOffice(Base):
    __tablename__ = "legal_one_offices"

    id = Column(Integer, primary_key=True, index=True)
    external_id = Column(Integer, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    path = Column(String)
    is_active = Column(Boolean, default=True)
    # Polo do processo que esse escritorio atende. Determina qual arvore
    # da taxonomia v2 (ativo/passivo) e oferecida em UI de templates e
    # injetada no prompt do classificador. 'ambos' = sem filtro (default).
    # Migration: tax002.
    polo_scope = Column(
        String(16), nullable=False, default="ambos", server_default="ambos",
    )


class LegalOneUser(Base):
    __tablename__ = "legal_one_users"

    id = Column(Integer, primary_key=True, index=True)
    external_id = Column(Integer, unique=True, index=True, nullable=False)
    name = Column(String, index=True, nullable=False)
    email = Column(String, index=True, unique=True)
    is_active = Column(Boolean, default=True, nullable=False)
    hashed_password = Column(String, nullable=True)
    must_change_password = Column(Boolean, default=False, nullable=False)

    # Permissions and roles
    role = Column(String, default="user", nullable=False)  # 'admin' | 'user'
    can_schedule_batch = Column(Boolean, default=False, nullable=False)
    can_use_publications = Column(Boolean, default=False, server_default="false", nullable=False)
    can_use_prazos_iniciais = Column(Boolean, default=False, nullable=False)
    notify_onerequest_errors = Column(Boolean, default=False, nullable=False)
    can_use_onerequest = Column(Boolean, default=False, server_default="false", nullable=False)
    can_use_encerramentos = Column(Boolean, default=False, server_default="false", nullable=False)
    # Minha Equipe: libera o menu + CSV das equipes que o usuário pode ver (ex.: "bb-reu").
    can_use_minha_equipe = Column(Boolean, default=False, server_default="false", nullable=False)
    minha_equipe_equipes = Column(String, nullable=True)
    # Distribuídos BB: libera o módulo + edição das tabelas de configuração.
    can_manage_distribuidos_bb = Column(Boolean, default=False, server_default="false", nullable=False)
    default_office_id = Column(Integer, ForeignKey("legal_one_offices.external_id"), nullable=True)
    # Carimbo do último login via SSO (Microsoft Entra). NULL = nunca entrou
    # por SSO. Usado pro selo "Entra ID" no admin de usuários.
    last_sso_at = Column(DateTime(timezone=True), nullable=True)

    # ── RBAC (migration usr005) ───────────────────────────────────────────
    # A política vem do CARGO; a exceção individual entra por cima ({chave:
    # bool} — true concede, false revoga). As colunas booleanas acima viram
    # CACHE do resultado, escrito só por app/services/permissoes.py.
    cargo_id = Column(Integer, ForeignKey("flow_cargo.id", ondelete="SET NULL"),
                      nullable=True, index=True)
    modulos_extra = Column(jsonb(), nullable=True)
    equipes_extra = Column(jsonb(), nullable=True)

    # Relationships
    squad_members = relationship("SquadMember", back_populates="user")
    saved_filters = relationship("SavedFilter", back_populates="user", cascade="all, delete-orphan")
    default_office = relationship("LegalOneOffice", foreign_keys=[default_office_id])


class FlowCargo(Base):
    """Cargo/papel do RBAC — a política de acesso é POR CARGO (ver
    app/services/permissoes.py). Espelha o modelo do Painel Financeiro."""

    __tablename__ = "flow_cargo"

    id = Column(Integer, primary_key=True)
    nome = Column(String(80), nullable=False, unique=True)
    descricao = Column(String(200), nullable=True)
    modulos = Column(jsonb(), nullable=False, server_default="{}")
    # nenhuma | lista | todas | supervisionadas
    equipes_modo = Column(String(20), nullable=False, server_default="nenhuma")
    equipes = Column(jsonb(), nullable=False, server_default="[]")
    ativo = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SavedFilter(Base):
    """
    Filtros salvos por usuário para reutilização rápida.
    Exemplo: "Pendentes do escritório SP", "Classificação Contrato"
    """
    __tablename__ = "saved_filters"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("legal_one_users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    module = Column(String, nullable=False)  # e.g., "publications", "scheduler"
    filters_json = Column(JSON, nullable=False)  # Armazena os parâmetros do filtro
    is_default = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    # Relationships
    user = relationship("LegalOneUser", back_populates="saved_filters")


class EncerramentoL1Intake(Base):
    """Rastro de cada encerramento pedido pelo Sistema de Encerramentos.

    Alimenta o menu "Encerramentos" da UI: o que foi encerrado no Legal One
    via integracao, por quem e com qual desfecho (ok | ja_encerrado |
    nao_encontrado | conflito | erro_l1)."""

    __tablename__ = "encerramentos_l1_intake"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    numero_cnj = Column(String(40), nullable=False, index=True)
    lawsuit_id = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, index=True)
    data_encerramento = Column(String(10), nullable=True)
    motivo_encerramento = Column(Text, nullable=True)
    operador_nome = Column(String(200), nullable=True)
    operador_email = Column(String(200), nullable=True)
    justificativa = Column(Text, nullable=True)
    origem = Column(String(50), nullable=True)
    detalhe = Column(Text, nullable=True)
