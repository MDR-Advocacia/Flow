"""DMI só vincula pasta de escritório HABILITADO — e "habilitado" não é só o BB.

Duas lições, nesta ordem:

1. DMI é produto do Banco do Brasil, e isso nunca esteve escrito no código: o
   resolver casava por CNJ e aceitava a primeira pasta que o L1 devolvesse.
   Como o mesmo CNJ é cadastrado de propósito para clientes diferentes, DMI
   foi parar em pasta do Ativos — 134 tarefas em 25/08/2026, incluindo a
   449811 (`DMI - BB Defesa` na Proc - 0006903), reportada pela operação.

2. Só que "do BB" ≠ "na subárvore Banco do Brasil". O processo com NPJ baixado
   migra pra **Recuperação de Honorários**, que fica FORA dessa subárvore e
   continua sendo do BB. Em 27/08/2026 a operadora não conseguiu agendar: a
   DMI `ANALISAR PUBLICAÇÃO EM NPJ BAIXADO` (2026/0000413638) achou a pasta
   Proc - 0053059 no escritório 63 e o filtro a recusou — a tarefa nasceu
   AVULSA, sem vínculo com a pasta.
"""
from sqlalchemy import text

from app.services.onerequest.service import OnerequestService

BB_REU, BB_AUTOR, ATIVOS_REU = 23, 22, 27
REC_HONORARIOS, REC_CREDITO = 63, 57


def _catalogo(db):
    """Escritórios como no L1: o filtro lê o `path`, não ids fixos."""
    db.execute(text(
        "INSERT INTO legal_one_offices (external_id, name, path) VALUES "
        "(:a, 'Réu', 'MDR Advocacia / Área operacional / Banco do Brasil / Réu'),"
        "(:b, 'Autor', 'MDR Advocacia / Área operacional / Banco do Brasil / Autor'),"
        "(:c, 'Réu', 'MDR Advocacia / Área operacional / Ativos / Réu'),"
        "(:d, 'Recuperação de Honorários',"
        " 'MDR Advocacia / Área operacional / Recuperação de Honorários'),"
        "(:e, 'Recuperação de Crédito',"
        " 'MDR Advocacia / Área operacional / Banco Santander / Recuperação de Crédito')"
    ), {
        "a": BB_REU, "b": BB_AUTOR, "c": ATIVOS_REU,
        "d": REC_HONORARIOS, "e": REC_CREDITO,
    })
    db.flush()


def test_escolhe_a_pasta_do_bb_e_nao_a_do_ativos(db_session):
    """O caso real: mesmo CNJ em duas pastas, uma do Ativos (antiga, vem
    primeiro do L1) e uma do BB."""
    _catalogo(db_session)
    svc = OnerequestService(db_session)
    candidatos = [
        {"id": 6904, "folder": "Proc - 0006903", "responsibleOfficeId": ATIVOS_REU},
        {"id": 83579, "folder": "Proc - 0077731", "responsibleOfficeId": BB_REU},
    ]
    escolhida = svc._so_de_escritorio_dmi(candidatos)
    assert escolhida is not None
    assert escolhida["id"] == 83579, "pegou a pasta do Ativos de novo"


def test_sem_escritorio_habilitado_nao_vincula(db_session):
    """Sem pasta habilitada, o certo é NÃO agendar — a DMI fica aguardando.

    Agendar no vizinho de CNJ de outro cliente é pior que não agendar: cria
    tarefa na agenda de quem não é dono do processo.
    """
    _catalogo(db_session)
    svc = OnerequestService(db_session)
    assert svc._so_de_escritorio_dmi([
        {"id": 6904, "folder": "Proc - 0006903", "responsibleOfficeId": ATIVOS_REU},
    ]) is None


def test_aceita_qualquer_escritorio_do_bb(db_session):
    """Autor, Réu, Trabalhista, Interessado — todos são BB."""
    _catalogo(db_session)
    svc = OnerequestService(db_session)
    r = svc._so_de_escritorio_dmi([{"id": 1, "responsibleOfficeId": BB_AUTOR}])
    assert r and r["id"] == 1


def test_aceita_recuperacao_de_honorarios(db_session):
    """O caso de 27/08/2026: NPJ baixado vive em Recuperação de Honorários.

    O escritório fica fora da subárvore "Banco do Brasil" e o processo é do
    BB do mesmo jeito — recusar a pasta fazia a tarefa nascer AVULSA.
    """
    _catalogo(db_session)
    svc = OnerequestService(db_session)
    r = svc._so_de_escritorio_dmi([
        {"id": 57843, "folder": "Proc - 0053059", "responsibleOfficeId": REC_HONORARIOS},
    ])
    assert r and r["id"] == 57843, "a pasta do NPJ baixado voltou a ser recusada"


def test_recuperacao_de_credito_nao_entra_junto(db_session):
    """Guarda do padrão `%recupera%honor%`: Recuperação de CRÉDITO (Santander)
    não pode entrar de carona na de HONORÁRIOS."""
    _catalogo(db_session)
    svc = OnerequestService(db_session)
    assert REC_CREDITO not in svc._offices_dmi()
    assert svc._so_de_escritorio_dmi([{"id": 9, "responsibleOfficeId": REC_CREDITO}]) is None


def test_catalogo_vazio_nao_inventa(db_session):
    """Sem catálogo não dá pra afirmar que a pasta é da carteira — não vincula."""
    svc = OnerequestService(db_session)
    assert svc._so_de_escritorio_dmi([{"id": 1, "responsibleOfficeId": BB_REU}]) is None


def test_aceita_a_chave_office_do_buscar_por_npj(db_session):
    """`buscar_lawsuit_por_npj` devolve 'office'; o /Lawsuits devolve
    'responsibleOfficeId'. O filtro tem que entender as duas."""
    _catalogo(db_session)
    svc = OnerequestService(db_session)
    r = svc._so_de_escritorio_dmi([{"id": 83579, "office": BB_REU}])
    assert r and r["id"] == 83579


def test_npj_vem_antes_do_cnj_nas_tres_etapas():
    """O NPJ é do BB por definição; o CNJ colide entre clientes."""
    import inspect

    src = inspect.getsource(OnerequestService)
    assert "search_lawsuit_by_cnj(solicitacao.numero_processo)" not in src, (
        "voltou a resolver por CNJ sem filtrar escritório"
    )
    # as três etapas: agendamento, re-resolução e o botão Verificar L1
    assert src.count("_so_de_escritorio_dmi(") >= 4


class _ClienteFake:
    """L1 mínimo: a MESMA pasta existe em /Lawsuits e em /Litigations."""

    def __init__(self, pasta):
        self.pasta = pasta

    def _normalize_cnj_number(self, cnj):
        return cnj

    def _cnj_variants(self, cnj):
        return [cnj]

    def _escape_odata_literal(self, v):
        return v

    def _paginated_catalog_loader(self, endpoint, params):
        return [dict(self.pasta)]


def test_a_mesma_pasta_nas_duas_entidades_conta_uma_vez(db_session):
    """Pasta vive em /Lawsuits OU /Litigations — os dois endpoints devolvem o
    MESMO registro. Sem dedupe o log dizia "2 pasta(s) achada(s)" pra uma só,
    que foi o que confundiu o diagnóstico de 27/08/2026. Mesma lição do bug da
    conferência pós-import do cadastro.
    """
    svc = OnerequestService(db_session)
    achados = svc._lawsuits_por_cnj(
        _ClienteFake({"id": 57843, "folder": "Proc - 0053059",
                      "responsibleOfficeId": REC_HONORARIOS}),
        "70336407720238220001",
    )
    assert len(achados) == 1, "a mesma pasta contou em dobro"
    assert achados[0]["id"] == 57843


class _DMI:
    """Só o que o `_office_avulso_id` lê da solicitação."""

    def __init__(self, setor, polo):
        self.setor, self.polo = setor, polo


def test_execucao_e_encerramento_sem_polo_cai_no_bb_reu(db_session):
    """O outro travamento de 27/08/2026: a 2026/0000413185 tinha responsável,
    setor e data preenchidos e mesmo assim parou em AGUARDANDO_PROCESSO —
    setor `BB Execução e Encerramento` com polo `Pendente` não derivava
    escritório nenhum pra tarefa avulsa. Escritório certo pelo operador: Réu.
    """
    _catalogo(db_session)
    svc = OnerequestService(db_session)
    assert svc._office_avulso_id(_DMI("BB Execução e Encerramento", "Pendente")) == BB_REU


def test_polo_explicito_vence_o_padrao_do_setor(db_session):
    """O padrão do setor é ÚLTIMO recurso — polo preenchido manda."""
    _catalogo(db_session)
    svc = OnerequestService(db_session)
    assert svc._office_avulso_id(_DMI("BB Encerramento", "Ativo")) == BB_AUTOR
    assert svc._office_avulso_id(_DMI("BB Autor", "Pendente")) == BB_AUTOR


def test_setor_vazio_sem_polo_nao_presume(db_session):
    """Sem setor E sem polo não há o que presumir — segue não derivável."""
    _catalogo(db_session)
    svc = OnerequestService(db_session)
    assert svc._office_avulso_id(_DMI("", "Pendente")) is None
