"""DMI é produto EXCLUSIVO do Banco do Brasil — a pasta tem que ser do BB.

Isso nunca esteve escrito no código: o resolver casava por CNJ e aceitava a
primeira pasta que o L1 devolvesse. Como o mesmo CNJ é cadastrado de propósito
para clientes diferentes, DMI foi parar em pasta do Ativos — 134 tarefas em
25/08/2026, incluindo a 449811 (`DMI - BB Defesa` na Proc - 0006903), reportada
pela operação.
"""
from sqlalchemy import text

from app.models.onerequest import OnerequestSolicitacao
from app.services.onerequest.service import OnerequestService

BB_REU, BB_AUTOR, ATIVOS_REU = 23, 22, 27


def _catalogo(db):
    """Escritórios como no L1: o filtro lê o `path`, não ids fixos."""
    db.execute(text(
        "INSERT INTO legal_one_offices (external_id, name, path) VALUES "
        "(:a, 'Réu', 'MDR Advocacia / Área operacional / Banco do Brasil / Réu'),"
        "(:b, 'Autor', 'MDR Advocacia / Área operacional / Banco do Brasil / Autor'),"
        "(:c, 'Réu', 'MDR Advocacia / Área operacional / Ativos / Réu')"
    ), {"a": BB_REU, "b": BB_AUTOR, "c": ATIVOS_REU})
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
    escolhida = svc._so_do_bb(candidatos)
    assert escolhida is not None
    assert escolhida["id"] == 83579, "pegou a pasta do Ativos de novo"


def test_sem_pasta_do_bb_nao_vincula(db_session):
    """Sem pasta do BB, o certo é NÃO agendar — a DMI fica aguardando processo.

    Agendar no vizinho de CNJ de outro cliente é pior que não agendar: cria
    tarefa na agenda de quem não é dono do processo.
    """
    _catalogo(db_session)
    svc = OnerequestService(db_session)
    assert svc._so_do_bb([
        {"id": 6904, "folder": "Proc - 0006903", "responsibleOfficeId": ATIVOS_REU},
    ]) is None


def test_aceita_qualquer_escritorio_do_bb(db_session):
    """Autor, Réu, Trabalhista, Interessado — todos são BB."""
    _catalogo(db_session)
    svc = OnerequestService(db_session)
    r = svc._so_do_bb([{"id": 1, "responsibleOfficeId": BB_AUTOR}])
    assert r and r["id"] == 1


def test_catalogo_vazio_nao_inventa(db_session):
    """Sem catálogo não dá pra afirmar que é do BB — melhor não vincular."""
    svc = OnerequestService(db_session)
    assert svc._so_do_bb([{"id": 1, "responsibleOfficeId": BB_REU}]) is None


def test_aceita_a_chave_office_do_buscar_por_npj(db_session):
    """`buscar_lawsuit_por_npj` devolve 'office'; o /Lawsuits devolve
    'responsibleOfficeId'. O filtro tem que entender as duas."""
    _catalogo(db_session)
    svc = OnerequestService(db_session)
    r = svc._so_do_bb([{"id": 83579, "office": BB_REU}])
    assert r and r["id"] == 83579


def test_npj_vem_antes_do_cnj_nas_tres_etapas():
    """O NPJ é do BB por definição; o CNJ colide entre clientes."""
    import inspect

    src = inspect.getsource(OnerequestService)
    assert "search_lawsuit_by_cnj(solicitacao.numero_processo)" not in src, (
        "voltou a resolver por CNJ sem filtrar escritório"
    )
    # as três etapas: agendamento, re-resolução e o botão Verificar L1
    assert src.count("_so_do_bb(") >= 4
