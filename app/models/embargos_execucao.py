"""Fluxo Embargos à Execução (emb_*).

Um card por PASTA de execução do BB Autor cuja tarefa "Protocolar Inicial -
BB Autor" foi cumprida no L1 (= ajuizamento). A partir da data do ajuizamento
o Flow espera N dias úteis e passa a consultar o tribunal (DataJud + DJEN) a
cada 5 dias úteis até achar os embargos à execução ligados a ela. Ver
docs/embargos-execucao-plano.md.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.orm import relationship

from app.db.session import Base
from app.db.types import jsonb

# ── De onde o card veio ──────────────────────────────────────────────
ORIGEM_RELATORIO = "RELATORIO"   # relatório "ROBÔ - EMBARGOS À EXECUÇÃO" do L1
ORIGEM_PLANILHA = "PLANILHA"     # legado subido pelo operador
ORIGEM_MANUAL = "MANUAL"

# ── Estado do monitoramento ──────────────────────────────────────────
ESTADO_SEM_CNJ = "SEM_CNJ"                    # sem número: não dá pra consultar o tribunal
ESTADO_AGUARDANDO_JANELA = "AGUARDANDO_JANELA"  # antes dos N dias úteis
ESTADO_MONITORANDO = "MONITORANDO"            # consulta a cada X dias úteis
ESTADO_ENCONTRADO = "ENCONTRADO"              # candidato forte — parou e avisou
ESTADO_CONFIRMADO = "CONFIRMADO"              # operador validou o vínculo
ESTADO_CONCLUIDO = "CONCLUIDO"                # tarefas disparadas no incidente — fluxo fechado
ESTADO_JA_CADASTRADO = "JA_CADASTRADO"        # a pasta já tem incidente de embargos no L1
ESTADO_SEM_EMBARGOS = "SEM_EMBARGOS"          # teto do monitoramento sem achar nada
ESTADO_ENCERRADO = "ENCERRADO"                # tirado do fluxo pelo operador

ESTADOS = (
    ESTADO_SEM_CNJ,
    ESTADO_AGUARDANDO_JANELA,
    ESTADO_MONITORANDO,
    ESTADO_ENCONTRADO,
    ESTADO_CONFIRMADO,
    ESTADO_CONCLUIDO,
    ESTADO_JA_CADASTRADO,
    ESTADO_SEM_EMBARGOS,
    ESTADO_ENCERRADO,
)

# ── Partes demandadas no portal do BB ────────────────────────────────
PARTES_PENDENTE = "PENDENTE"
PARTES_OK = "OK"
PARTES_ERRO = "ERRO"            # a coleta falhou — volta na próxima passagem (com teto)
PARTES_SEM_NPJ = "SEM_NPJ"      # pasta sem NPJ: não há o que consultar no portal
PARTES_NAO_APLICA = "NAO_APLICA"  # cliente não é o BB (Banese/Ativos)

# ── Força da evidência de um candidato ───────────────────────────────
NIVEL_CONFIRMADO_DJEN = "CONFIRMADO_DJEN"  # embargante do DJEN é parte da execução
NIVEL_PROVAVEL = "PROVAVEL"                # dependência + petição na execução no mesmo dia
NIVEL_FRACO = "FRACO"                      # só vara + classe + data
NIVEL_DESCARTADO = "DESCARTADO"            # DJEN mostra embargante que não é da execução
NIVEIS_FORTES = (NIVEL_CONFIRMADO_DJEN, NIVEL_PROVAVEL)

DECISAO_PENDENTE = "PENDENTE"
DECISAO_CONFIRMADO = "CONFIRMADO"
DECISAO_RECUSADO = "RECUSADO"

# ── Trilha de eventos ────────────────────────────────────────────────
SECAO_ENTRADA = "ENTRADA"
SECAO_PARTES = "PARTES"
SECAO_TRIBUNAL = "TRIBUNAL"
SECAO_L1 = "L1"
SECAO_AVISO = "AVISO"
SECAO_TAREFAS = "TAREFAS"
SECAO_OPERADOR = "OPERADOR"

EVT_INFO = "INFO"
EVT_AVISO = "AVISO"
EVT_ERRO = "ERRO"

# ── Tarefas do incidente (templates) ─────────────────────────────────
RESP_FIXO = "FIXO"                          # pessoa escolhida no template
RESP_ADVOGADO_CARD = "ADVOGADO_RESPONSAVEL"  # advogado responsável da execução (relatório)
DISPARO_CRIADA = "CRIADA"
DISPARO_FALHA = "FALHA"


class EmbExecucao(Base):
    __tablename__ = "emb_execucao"

    id = Column(Integer, primary_key=True)
    # Chave natural: a pasta ("Proc - 0068694"). Sem pasta (planilha só com
    # CNJ) vira "CNJ <número>" e é adotada quando a pasta aparecer.
    pasta = Column(String, nullable=False, unique=True, index=True)
    l1_task_id = Column(BigInteger, nullable=True, index=True)
    l1_task_ids = Column(jsonb(), nullable=True)
    lawsuit_id = Column(Integer, nullable=True)
    cnj = Column(String, nullable=True)
    cnj_digitos = Column(String, nullable=True, index=True)
    npj = Column(String, nullable=True, index=True)
    escritorio = Column(String, nullable=True)
    cliente = Column(String, nullable=True, index=True)
    responsavel_nome = Column(String, nullable=True)
    executante_nome = Column(String, nullable=True)
    uf = Column(String, nullable=True)
    origem = Column(String, nullable=False, server_default=ORIGEM_RELATORIO)

    data_ajuizamento = Column(Date, nullable=False)
    estado = Column(String, nullable=False, server_default=ESTADO_AGUARDANDO_JANELA, index=True)
    dias_uteis_janela = Column(Integer, nullable=False, server_default="15")
    inicio_monitoramento = Column(Date, nullable=True)
    proxima_consulta = Column(Date, nullable=True, index=True)
    ultima_consulta_em = Column(DateTime(timezone=True), nullable=True)
    consultas_feitas = Column(Integer, nullable=False, server_default="0")
    ultimo_erro = Column(Text, nullable=True)

    partes_status = Column(String, nullable=False, server_default=PARTES_PENDENTE, index=True)
    partes_tentativas = Column(Integer, nullable=False, server_default="0")
    partes_erro = Column(Text, nullable=True)
    partes_em = Column(DateTime(timezone=True), nullable=True)

    # Capa da execução no DataJud (guardada pra busca de candidatos por vara).
    tribunal_alias = Column(String, nullable=True)
    orgao_codigo = Column(Integer, nullable=True)
    orgao_nome = Column(String, nullable=True)
    datajud_ajuizamento_raw = Column(String, nullable=True)

    # Incidente de embargos já existente na pasta (ProceduralIssues do L1).
    l1_incidente_verificado_em = Column(DateTime(timezone=True), nullable=True)
    incidente_folder = Column(String, nullable=True)
    incidente_cnj = Column(String, nullable=True)
    incidente_id = Column(Integer, nullable=True)
    incidente_office_id = Column(Integer, nullable=True)

    encontrado_em = Column(DateTime(timezone=True), nullable=True)
    aviso_enviado_em = Column(DateTime(timezone=True), nullable=True)
    confirmado_candidato_id = Column(Integer, nullable=True)

    anotacao = Column(Text, nullable=True)
    decidido_por_user_id = Column(Integer, ForeignKey("legal_one_users.id"), nullable=True)
    decidido_em = Column(DateTime(timezone=True), nullable=True)

    criado_em = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_em = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    partes = relationship(
        "EmbParte", back_populates="execucao", cascade="all, delete-orphan",
        order_by="EmbParte.id",
    )
    candidatos = relationship(
        "EmbCandidato", back_populates="execucao", cascade="all, delete-orphan",
        order_by="EmbCandidato.id",
    )


class EmbParte(Base):
    __tablename__ = "emb_parte"

    id = Column(Integer, primary_key=True)
    execucao_id = Column(
        Integer, ForeignKey("emb_execucao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    origem = Column(String, nullable=False, server_default="BB")  # BB | DJEN
    polo = Column(String, nullable=True)
    nome = Column(String, nullable=False)
    cpf_cnpj = Column(String, nullable=True)
    tipo_pessoa = Column(String, nullable=True)
    relacao_bb = Column(String, nullable=True)
    demandada = Column(Boolean, nullable=False, server_default="0")
    raw = Column(jsonb(), nullable=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    execucao = relationship("EmbExecucao", back_populates="partes")


class EmbCandidato(Base):
    __tablename__ = "emb_candidato"
    __table_args__ = (
        UniqueConstraint("execucao_id", "cnj_digitos", name="uq_emb_candidato_execucao_cnj"),
    )

    id = Column(Integer, primary_key=True)
    execucao_id = Column(
        Integer, ForeignKey("emb_execucao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cnj = Column(String, nullable=False)
    cnj_digitos = Column(String, nullable=False, index=True)
    data_ajuizamento = Column(DateTime(timezone=True), nullable=True)
    classe_nome = Column(String, nullable=True)
    orgao_nome = Column(String, nullable=True)
    distribuicao_dependencia = Column(Boolean, nullable=False, server_default="0")
    peticao_mesmo_dia = Column(Boolean, nullable=False, server_default="0")
    nivel = Column(String, nullable=False, server_default=NIVEL_FRACO, index=True)

    djen_status = Column(String, nullable=True)
    djen_embargantes = Column(jsonb(), nullable=True)
    djen_embargados = Column(jsonb(), nullable=True)
    # Embargos já cadastrados no L1 (achados pelo CNJ assim que viram candidato forte).
    l1_litigation_id = Column(Integer, nullable=True)
    l1_folder = Column(String, nullable=True)
    djen_nomes_casados = Column(jsonb(), nullable=True)
    djen_trecho = Column(Text, nullable=True)
    djen_data = Column(String, nullable=True)
    djen_consultado_em = Column(DateTime(timezone=True), nullable=True)

    decisao = Column(String, nullable=False, server_default=DECISAO_PENDENTE, index=True)
    decidido_por_user_id = Column(Integer, ForeignKey("legal_one_users.id"), nullable=True)
    decidido_em = Column(DateTime(timezone=True), nullable=True)

    criado_em = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_em = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    execucao = relationship("EmbExecucao", back_populates="candidatos")


class EmbEvento(Base):
    __tablename__ = "emb_evento"

    id = Column(Integer, primary_key=True)
    execucao_id = Column(
        Integer, ForeignKey("emb_execucao.id", ondelete="CASCADE"), nullable=True, index=True
    )
    secao = Column(String, nullable=False, index=True)
    nivel = Column(String, nullable=False, server_default=EVT_INFO)
    mensagem = Column(Text, nullable=False)
    dados = Column(jsonb(), nullable=True)
    user_id = Column(Integer, ForeignKey("legal_one_users.id"), nullable=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)


class EmbTarefaTemplate(Base):
    """Tarefa que o Flow cria no incidente depois do cadastro (tela de templates)."""

    __tablename__ = "emb_tarefa_template"

    id = Column(Integer, primary_key=True)
    nome = Column(String, nullable=False)
    ativo = Column(Boolean, nullable=False, server_default=true())
    ordem = Column(Integer, nullable=False, server_default="0")
    tipo_id = Column(Integer, nullable=False)
    subtipo_id = Column(Integer, nullable=False)
    subtipo_nome = Column(String, nullable=True)
    responsavel_modo = Column(String, nullable=False, server_default=RESP_FIXO)
    responsavel_contact_id = Column(Integer, nullable=True)
    responsavel_nome = Column(String, nullable=True)
    prazo_dias_uteis = Column(Integer, nullable=False, server_default="5")
    prioridade = Column(String, nullable=False, server_default="Normal")
    descricao_template = Column(Text, nullable=False)
    observacoes_template = Column(Text, nullable=True)
    atualizado_por_user_id = Column(Integer, ForeignKey("legal_one_users.id"), nullable=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    atualizado_em = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class EmbTarefaDisparo(Base):
    """Cada tarefa enviada (ou tentada) ao L1 — cópia do que saiu, sobrevive ao template."""

    __tablename__ = "emb_tarefa_disparo"

    id = Column(Integer, primary_key=True)
    execucao_id = Column(
        Integer, ForeignKey("emb_execucao.id", ondelete="CASCADE"), nullable=False, index=True
    )
    template_id = Column(Integer, nullable=True)
    template_nome = Column(String, nullable=True)
    incidente_id = Column(Integer, nullable=True)
    subtipo_id = Column(Integer, nullable=True)
    responsavel_contact_id = Column(Integer, nullable=True)
    prazo = Column(Date, nullable=True)
    descricao = Column(Text, nullable=True)
    status = Column(String, nullable=False, index=True)
    l1_task_id = Column(BigInteger, nullable=True)
    erro = Column(Text, nullable=True)
    user_id = Column(Integer, ForeignKey("legal_one_users.id"), nullable=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
