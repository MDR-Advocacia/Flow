"""
Template de tarefa para o motor de agendamento automático de publicações.

Cada template define, para uma combinação de (category, subcategory, office),
qual tipo/subtipo de tarefa será criado no Legal One, qual usuário será
responsável, prioridade, prazo em dias úteis e o corpo da descrição/notas.

O motor de proposta usa estes templates para pré-montar o payload que o
operador revisa e confirma antes de enviar ao Legal One.
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base


class TaskTemplate(Base):
    __tablename__ = "task_templates"
    # Múltiplos templates por (category, subcategory, office) são permitidos,
    # cada um gera uma tarefa diferente no agendamento.
    __table_args__ = ()

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)

    # Critério de casamento (classificação + escritório responsável)
    # office_external_id = NULL → template "global": aplica a publicações sem escritório vinculado
    category = Column(String, nullable=False, index=True)
    subcategory = Column(String, nullable=True, index=True)
    office_external_id = Column(
        Integer,
        ForeignKey("legal_one_offices.external_id"),
        nullable=True,   # NULL = template para publicações sem processo/escritório
        index=True,
    )

    # Configuração da tarefa gerada
    task_subtype_external_id = Column(
        Integer,
        ForeignKey("legal_one_task_subtypes.external_id"),
        nullable=False,
        index=True,
    )
    # Nullable: templates podem ser criados sem responsável nominal. Quando o
    # operador for de fato criar a tarefa a partir desse template, o modal da
    # tela de classificação exige que o responsável seja preenchido (trava
    # client-side em CreateTaskByProcessPage). Assim a definição do
    # responsável fica para o momento da criação, tipicamente resolvendo-o
    # como o responsável principal da pasta do processo.
    responsible_user_external_id = Column(
        Integer,
        ForeignKey("legal_one_users.external_id"),
        nullable=True,
        index=True,
    )

    priority = Column(String, nullable=False, default="Normal")  # Low, Normal, High
    due_business_days = Column(Integer, nullable=False, default=3)

    # Decide pra quem vai a tarefa criada no L1: 'principal' usa o
    # `responsible_user_external_id` direto; 'assistente' resolve via
    # `Squad.is_assistant` da squad do responsavel (ver
    # app/services/squad_assistant_resolver.py). Migration: sqd001.
    target_role = Column(
        String(16), nullable=False, default="principal", server_default="principal",
    )
    # Quando setado, a tarefa vai pra essa squad de suporte (kind='support')
    # em vez do responsavel/assistente da squad principal. Combinado com
    # target_role. Migration: sqd004.
    target_squad_id = Column(
        Integer, ForeignKey("squads.id"), nullable=True, index=True,
    )
    # Exceção por ETIQUETA do processo (sqd005). Preenchida e o processo com
    # essa etiqueta no L1: a squad de suporte e o responsável fixo deixam de
    # valer — advogado vai pro responsável da pasta; assistente pro rodízio da
    # squad marcada com a mesma etiqueta em que esse responsável está. Caso de
    # origem: Equipe Mista (NERC), que acumula os dois polos. Ver
    # app/services/excecao_etiqueta.py.
    excecao_etiqueta = Column(String(80), nullable=True)
    # Quem DA EQUIPE da etiqueta recebe: 'principal' (advogado = responsável
    # da pasta) ou 'assistente' (rodízio da squad marcada). Separado do
    # target_role de propósito: no BB Autor o grupo de advogados roda como
    # 'assistente' numa squad de suporte. NULL = segue o target_role.
    excecao_papel = Column(String(16), nullable=True)
    due_date_reference = Column(
        String, nullable=False, default="publication",
        doc='Referência para cálculo do prazo: "publication" (data da publicação) ou "today" (data atual)',
    )  # "publication" | "today"

    # Templates de texto (podem usar placeholders como {cnj}, {publication_date})
    description_template = Column(Text, nullable=True)
    notes_template = Column(Text, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False, index=True)

    # Versionamento da taxonomia (v1 / v2). Templates v1 sao os criados
    # antes do redesenho de polo. v2 sao os criados/revisados na taxonomia
    # nova. Engine de proposta de tarefa so usa templates v2 ativos.
    # Migration: tax003.
    taxonomy_version = Column(
        String(8), nullable=False, default="v1", server_default="v1",
    )
    # Snapshot textual "<categoria_v1> / <subcategoria_v1>" mostrado no
    # banner amarelo do modal de edicao pra que o operador saiba a qual
    # classificacao o template referenciava antes de escolher o equivalente
    # na arvore nova.
    legacy_label = Column(Text, nullable=True)
    # Quando true, o template fica dormente: nao casa com publicacao
    # nenhuma. Operador limpa a flag salvando o template no modal com a
    # classificacao v2 selecionada.
    needs_taxonomy_review = Column(
        Boolean, nullable=False, default=False, server_default="false",
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    # Relationships
    office = relationship(
        "LegalOneOffice",
        primaryjoin="TaskTemplate.office_external_id == LegalOneOffice.external_id",
        foreign_keys=[office_external_id],
    )
    task_subtype = relationship(
        "LegalOneTaskSubType",
        primaryjoin="TaskTemplate.task_subtype_external_id == LegalOneTaskSubType.external_id",
        foreign_keys=[task_subtype_external_id],
    )
    responsible_user = relationship(
        "LegalOneUser",
        primaryjoin="TaskTemplate.responsible_user_external_id == LegalOneUser.external_id",
        foreign_keys=[responsible_user_external_id],
    )
