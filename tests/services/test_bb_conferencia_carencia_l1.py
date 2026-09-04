# -*- coding: utf-8 -*-
"""A conferência não grita ERRO quando só foi cedo demais.

Medido na coleta 227 (04/09/2026): o import saiu 14:23:57, o L1 criou as 17
pastas às 14:25 — todas certas — e a conferência, que roda logo depois do
envio, checou às 14:24:38 e gravou ERRO dizendo "o Legal One NÃO criou pasta
para 17 de 17". Nada estava errado: o import do L1 é assíncrono.

Alarme que mente treina o operador a ignorar alarme — e este existe justamente
para o caso oposto (27/08: 25 pastas "restauradas" com sucesso e nenhuma criada
de verdade). Então a carência preserva o alarme e mata o falso positivo: dentro
dos primeiros minutos "ainda sem pasta" é INFO; passada a carência, volta ERRO.

O que se testa aqui é o NÍVEL DO EVENTO gravado — que é o que o operador vê.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.distribuidos_bb import (
    NIVEL_ERRO,
    NIVEL_INFO,
    BbEvento,
    BbPlanilha,
    BbProcesso,
    PROC_DISTRIBUIDO,
)
from app.services.distribuidos_bb import cadastro_conferencia as cc


@pytest.fixture(autouse=True)
def _limpa_cache_areas():
    """`resolver_office_por_path` guarda as áreas num global — sem zerar, o
    primeiro teste contamina os seguintes."""
    from app.services.distribuidos_bb import cadastro_l1

    cadastro_l1._CACHE_AREAS = None
    yield
    cadastro_l1._CACHE_AREAS = None


@pytest.fixture
def db():
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


ESCRITORIO = "MDR / BB / Réu"


class _L1Vazio:
    """L1 que responde, conhece o escritório, mas ainda não criou pasta —
    o estado real dos primeiros minutos depois do import."""

    def get_all_allocatable_areas(self):
        return [{"id": 22, "path": ESCRITORIO}]

    def _paginated_catalog_loader(self, *a, **kw):
        return []


def _cenario(db, *, subido_ha_min):
    pl = BbPlanilha(
        nome_arquivo="X.xlsx", conteudo=b"", total_processos=2,
        subido_legalone=True,
        subido_em=datetime.now(timezone.utc) - timedelta(minutes=subido_ha_min),
    )
    db.add(pl)
    db.commit()
    for i in range(2):
        db.add(BbProcesso(
            cliente="BB", fingerprint="fp:%d:%d" % (pl.id, i),
            status=PROC_DISTRIBUIDO,
            cnj="%07d-11.2026.8.05.0001" % (pl.id * 10 + i), planilha_id=pl.id,
            escritorio_path=ESCRITORIO,
        ))
    db.commit()
    return pl


def _evento(db, acao):
    return db.query(BbEvento).filter(BbEvento.acao == acao).one_or_none()


def test_dentro_da_carencia_grava_info_nao_erro(db):
    """O caso 227: conferência 41s após o envio, L1 ainda criando."""
    pl = _cenario(db, subido_ha_min=1)

    resumo = cc.conferir_duplicacao(db, pl, client=_L1Vazio())

    assert resumo["sem_pasta"] > 0, "o cenário precisa ter gente sem pasta"
    assert _evento(db, cc.ACAO_SEM_PASTA) is None, "gritou ERRO cedo demais"
    ev = _evento(db, cc.ACAO_AGUARDANDO)
    assert ev is not None and ev.nivel == NIVEL_INFO
    assert "assíncrono" in ev.mensagem


def test_passada_a_carencia_o_alarme_de_verdade_volta(db):
    """Meia hora depois, 'ninguém tem pasta' é falha real e tem que doer."""
    pl = _cenario(db, subido_ha_min=45)

    cc.conferir_duplicacao(db, pl, client=_L1Vazio())

    assert _evento(db, cc.ACAO_AGUARDANDO) is None
    ev = _evento(db, cc.ACAO_SEM_PASTA)
    assert ev is not None and ev.nivel == NIVEL_ERRO
    assert "NÃO criou pasta" in ev.mensagem


def test_planilha_sem_carimbo_de_subida_nao_ganha_carencia(db):
    """Sem `subido_em` não dá pra saber se é cedo — melhor alarme a mais."""
    pl = _cenario(db, subido_ha_min=1)
    pl.subido_em = None
    db.commit()

    cc.conferir_duplicacao(db, pl, client=_L1Vazio())

    ev = _evento(db, cc.ACAO_SEM_PASTA)
    assert ev is not None and ev.nivel == NIVEL_ERRO


def test_cedo_demais_isolado(db):
    assert cc._cedo_demais(_cenario(db, subido_ha_min=1)) is True
    assert cc._cedo_demais(_cenario(db, subido_ha_min=cc._CARENCIA_L1_MIN + 5)) is False
