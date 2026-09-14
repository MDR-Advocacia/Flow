# -*- coding: utf-8 -*-
"""Relatório de Entradas e Tratamento por Escritório (Publicações, 14/09/2026).

Os números têm que bater com o Dashboard: entrada = publicação não duplicada no
dia da captura (Brasília) ou da publicação; tratada = agendada ou com ciência
dentro do período; pendente = NOVO/CLASSIFICADO/ERRO agora; escritório = ramo
do path ("Banco do Brasil / Réu").
"""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.legal_one import LegalOneOffice
from app.models.publication_search import (
    RECORD_STATUS_CLASSIFIED,
    RECORD_STATUS_IGNORED,
    RECORD_STATUS_NEW,
    RECORD_STATUS_SCHEDULED,
    SEARCH_STATUS_COMPLETED,
    PublicationRecord,
    PublicationSearch,
)
from app.services.publications_report import entradas_escritorio as rel

BRT = timezone(timedelta(hours=-3))
AGORA = datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc)   # 12:00 em Brasília
BB = "MDR Advocacia / Área operacional / Banco do Brasil / Réu"
MASTER = "MDR Advocacia / Área operacional / Banco Master / Réu"
_uid = iter(range(500_000, 600_000))


def _brt(dia, hora, minuto=0):
    # Grava em UTC: o SQLite dos testes descarta o fuso (o Postgres guarda timestamptz).
    return datetime(2026, 9, dia, hora, minuto, tzinfo=BRT).astimezone(timezone.utc)


@pytest.fixture
def busca(db_session):
    db_session.add_all([
        LegalOneOffice(external_id=23, name="Réu", path=BB),
        LegalOneOffice(external_id=61, name="Réu", path=MASTER),
    ])
    b = PublicationSearch(date_from="2026-09-01", status=SEARCH_STATUS_COMPLETED)
    db_session.add(b)
    db_session.commit()
    return b


def _pub(db, busca, *, office=None, criada, status=RECORD_STATUS_NEW, dup=False,
         agendada=None, ciencia=None, publicada=None):
    r = PublicationRecord(
        search_id=busca.id, legal_one_update_id=next(_uid), status=status, is_duplicate=dup,
        linked_office_id=office, created_at=criada, publication_date=publicada,
        scheduled_at=agendada, ignored_at=ciencia,
    )
    db.add(r)
    db.flush()
    return r


@pytest.fixture
def cenario(db_session, busca):
    # Banco do Brasil / Réu
    _pub(db_session, busca, office=23, criada=_brt(11, 10), status=RECORD_STATUS_SCHEDULED, agendada=_brt(11, 14))
    _pub(db_session, busca, office=23, criada=_brt(11, 11), status=RECORD_STATUS_CLASSIFIED)
    _pub(db_session, busca, office=23, criada=_brt(12, 9), status=RECORD_STATUS_IGNORED, ciencia=_brt(12, 10))
    _pub(db_session, busca, office=23, criada=_brt(11, 10), dup=True,
         status=RECORD_STATUS_SCHEDULED, agendada=_brt(11, 15))                      # duplicada: fora de tudo
    _pub(db_session, busca, office=23, criada=_brt(5, 10), status=RECORD_STATUS_IGNORED,
         ciencia=_brt(10, 9))                                                          # entrou antes, tratada no período
    _pub(db_session, busca, office=23, criada=_brt(1, 10))                             # pendente antiga
    _pub(db_session, busca, office=23, criada=_brt(13, 0, 30))                         # entrou depois do período
    # Banco Master / Réu: 23:30 de 10/09 em Brasília já é 11/09 em UTC
    _pub(db_session, busca, office=61, criada=_brt(10, 23, 30))
    # Sem escritório: sem pasta e escritório que não existe no catálogo
    _pub(db_session, busca, office=None, criada=_brt(12, 8))
    _pub(db_session, busca, office=999, criada=_brt(12, 8))
    db_session.commit()
    return rel.compute_entradas_escritorio(db_session, date(2026, 9, 10), date(2026, 9, 12), "captura", agora=AGORA)


@pytest.mark.parametrize("path, esperado", [
    (BB, "Banco do Brasil / Réu"),
    ("MDR Advocacia / Área operacional / Recuperação de Honorários", "Recuperação de Honorários"),
    ("MDR Advocacia", "Sem escritório"),
    (None, "Sem escritório"),
])
def test_rotulo_do_escritorio_e_o_mesmo_do_dashboard(path, esperado):
    assert rel.rotulo_escritorio(path) == esperado


def test_totais_do_periodo(cenario):
    t = cenario["totais"]
    assert t["entradas"] == 6 and t["media_dia"] == 2.0
    assert (t["pico_n"], t["pico_dia"]) == (3, "2026-09-12")
    assert (t["tratadas"], t["agendadas"], t["ciencias"]) == (3, 1, 2)
    assert t["taxa_tratamento"] == 50
    assert t["pendentes"] == 6 and t["pendente_mais_antiga_dias"] == 13


def test_linha_por_escritorio(cenario):
    por_nome = {x["escritorio"]: x for x in cenario["escritorios"]}
    assert [x["escritorio"] for x in cenario["escritorios"]] == [
        "Banco do Brasil / Réu", "Sem escritório", "Banco Master / Réu",
    ]
    bb = por_nome["Banco do Brasil / Réu"]
    assert (bb["entradas"], bb["pct_entradas"], bb["pico_n"], bb["pico_dia"]) == (3, 50, 2, "2026-09-11")
    assert (bb["tratadas"], bb["agendadas"], bb["ciencias"], bb["taxa_tratamento"]) == (3, 1, 2, 100)
    assert bb["pendentes"] == 3 and bb["pendente_mais_antiga_dias"] == 13
    assert por_nome["Sem escritório"]["entradas"] == 2 and por_nome["Sem escritório"]["pendentes"] == 2
    master = por_nome["Banco Master / Réu"]
    assert (master["entradas"], master["pico_dia"], master["taxa_tratamento"]) == (1, "2026-09-10", 0)


def test_serie_diaria_em_horario_de_brasilia(cenario):
    assert cenario["serie"] == [
        {"dia": "2026-09-10", "entradas": 1, "tratadas": 1},
        {"dia": "2026-09-11", "entradas": 2, "tratadas": 1},
        {"dia": "2026-09-12", "entradas": 3, "tratadas": 1},
    ]


def test_base_publicacao_conta_pela_data_do_diario(db_session, busca):
    _pub(db_session, busca, office=23, criada=_brt(14, 3), publicada="2026-09-11T00:00:00Z")
    _pub(db_session, busca, office=23, criada=_brt(11, 3), publicada="2026-09-02T00:00:00Z")
    _pub(db_session, busca, office=23, criada=_brt(11, 3), publicada=None)
    db_session.commit()

    dados = rel.compute_entradas_escritorio(db_session, date(2026, 9, 10), date(2026, 9, 12), "publicacao", agora=AGORA)

    assert dados["totais"]["entradas"] == 1
    assert dados["serie"][1] == {"dia": "2026-09-11", "entradas": 1, "tratadas": 0}


@pytest.mark.parametrize("ini, fim, base", [
    (date(2026, 9, 12), date(2026, 9, 10), "captura"),
    (date(2025, 1, 1), date(2026, 9, 10), "captura"),
    (date(2026, 9, 10), date(2026, 9, 12), "outra"),
])
def test_periodo_ou_base_invalidos(db_session, ini, fim, base):
    with pytest.raises(ValueError):
        rel.compute_entradas_escritorio(db_session, ini, fim, base)


def test_html_executivo_tem_os_numeros_e_escapa_nomes(db_session, busca, cenario):
    db_session.add(LegalOneOffice(external_id=70, name="X", path="MDR Advocacia / Área operacional / <b>Cliente</b> / Réu"))
    _pub(db_session, busca, office=70, criada=_brt(12, 9))
    db_session.commit()
    dados = rel.compute_entradas_escritorio(db_session, date(2026, 9, 10), date(2026, 9, 12), "captura", agora=AGORA)

    html = rel.render_entradas_escritorio_html(dados)

    for trecho in ("Entradas e Tratamento", "Banco do Brasil / Réu", "Sem escritório", "Tratadas ÷ entradas",
                   "Pendentes agora", "<svg", "10/09/2026 a 12/09/2026", "&lt;b&gt;Cliente&lt;/b&gt; / Réu"):
        assert trecho in html
    assert "None" not in html and "<b>Cliente" not in html


def test_endpoint_devolve_o_pdf(db_session, cenario, monkeypatch):
    from app.api.v1.endpoints import publications_performance as ep

    monkeypatch.setattr(ep, "html_to_pdf", lambda html: b"%PDF-teste " + html[:20].encode())
    resp = ep.entradas_escritorio_report_pdf(
        date_from=date(2026, 9, 10), date_to=date(2026, 9, 12), base="captura",
        db=db_session, user=SimpleNamespace(id=1, email="ti@mdradvocacia.com"),
    )
    assert resp.media_type == "application/pdf" and resp.body.startswith(b"%PDF-teste")
    assert "entradas-tratamento-por-escritorio-2026-09-10_2026-09-12.pdf" in resp.headers["content-disposition"]


def test_endpoint_recusa_periodo_invertido(db_session):
    from app.api.v1.endpoints import publications_performance as ep

    with pytest.raises(HTTPException) as erro:
        ep.entradas_escritorio_report_pdf(
            date_from=date(2026, 9, 12), date_to=date(2026, 9, 10), base="captura",
            db=db_session, user=SimpleNamespace(id=1, email="x"),
        )
    assert erro.value.status_code == 422
