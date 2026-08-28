"""Painel do módulo BB não mistura TOMBAMENTO com o fluxo diário.

Decisão do operador (27/08/2026): a migração em massa do Banco Master
(11.281 processos marcados em raw['tombamento']) afogava o painel de
cadastro. Visões operacionais escondem tombamento por padrão;
`incluir_tombamento=True` mostra tudo.

O filtro usa extração JSON (`raw['tombamento'] IS NULL`) — este arquivo
roda em SQLite de propósito: prova que a expressão existe nos dois
dialetos (Postgres jsonb `->` / SQLite JSON_EXTRACT) e que raw NULL não
explode.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.distribuidos_bb import PROC_DISTRIBUIDO, BbProcesso
from app.services.distribuidos_bb.service import DistribuidosBBService


@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _proc(db, fp, *, raw=None, cliente="BB"):
    p = BbProcesso(
        cliente=cliente, cnj="0000001-11.2026.8.05.0001", fingerprint=fp,
        status=PROC_DISTRIBUIDO, planilha_status="PENDENTE_CADASTRO", raw=raw,
    )
    db.add(p)
    db.commit()
    return p


def test_listagem_esconde_tombamento_por_padrao(db):
    _proc(db, "diario:1", raw=None)                       # raw NULL não explode
    _proc(db, "diario:2", raw={"master_listagem": {}})    # raw sem a marca
    _proc(db, "tomb:1", cliente="MASTER",
          raw={"tombamento": {"aba": "x"}, "master_listagem": {}})

    svc = DistribuidosBBService(db)
    padrao = svc.listar_processos()
    assert padrao["total"] == 2
    assert all("tomb" not in i["cliente"].lower() or i["cliente"] != "MASTER"
               for i in padrao["items"])

    com_tudo = svc.listar_processos(incluir_tombamento=True)
    assert com_tudo["total"] == 3


def test_dashboard_nao_conta_tombamento(db):
    _proc(db, "diario:1")
    _proc(db, "tomb:1", cliente="MASTER", raw={"tombamento": {"aba": "x"}})

    kpis = DistribuidosBBService(db).dashboard()["kpis"]
    assert kpis["total"] == 1
    assert kpis["distribuidos"] == 1


def test_graficos_escondem_tombamento_igual_aos_kpis(db):
    """KPI e gráfico têm que contar a MESMA coisa.

    Em 28/08/2026 só os KPIs excluíam tombamento: o card mostrava 2.221
    capturados enquanto o chip "Banco Master" mostrava 11.281 na mesma tela, e
    a série por data virava uma agulha no dia da migração — com todos os dias
    de operação rente ao eixo, o painel do dia a dia ficou ilegível.
    """
    _proc(db, "diario:1")
    _proc(db, "diario:2")
    for i in range(50):                      # migração em massa, mesmo dia
        _proc(db, f"tomb:{i}", cliente="MASTER", raw={"tombamento": {"aba": "x"}})

    d = DistribuidosBBService(db).dashboard()

    assert sum(x["total"] for x in d["por_cliente"]) == 2
    assert all(x["cliente"] != "MASTER" for x in d["por_cliente"])
    assert sum(x["total"] for x in d["por_data"]) == 2, "o pico afundaria a série"
    assert sum(x["total"] for x in d["por_natureza"]) == 2
    assert sum(x["total"] for x in d["por_posicao"]) == 2
    assert sum(x["total"] for x in d["por_estado"]) == 2
    assert d["kpis"]["total"] == sum(x["total"] for x in d["por_cliente"]),         "KPI e gráfico discordando é exatamente o bug que este teste trava"
