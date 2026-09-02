# -*- coding: utf-8 -*-
"""O funil do painel BB FECHA: capturados = distribuídos = cadastrados.

Caso de 02/09/2026: o painel mostrava 2.350 capturados x 2.339 distribuídos x
2.339 cadastrados, e ninguém sabia dizer onde os 11 tinham ido parar. Eram os
11 do cadastro direto da Ativos (status CADASTRADO, pasta pré-existente
vinculada): mais prontos que todo mundo e fora das DUAS contas — o card de
distribuídos contava só status DISTRIBUIDO, e o de cadastrados filtrava o pool
por status DISTRIBUIDO também.

Agora o funil é cumulativo (quem passou da etapa conta nela), o pool inclui
CADASTRADO, e `cadastro_direto` vira a nota de rodapé que explica o subgrupo.

De quebra: o por_data ganha a quebra por ESCRITÓRIO responsável por dia
(conjunto dia × escritório), com a MESMA chave do card "Por escritório".
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.distribuidos_bb import (
    PROC_CADASTRADO,
    PROC_DISTRIBUIDO,
    BbEscritorio,
    BbProcesso,
)
from app.services.distribuidos_bb.service import DistribuidosBBService


@pytest.fixture
def db():
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _proc(db, fp, *, status=PROC_DISTRIBUIDO, planilha="CADASTRADO_L1",
          cliente="BB", escritorio_id=None):
    p = BbProcesso(
        cliente=cliente, cnj="0000%s-11.2026.8.05.0001" % fp[-3:],
        fingerprint=fp, status=status, planilha_status=planilha,
        escritorio_id=escritorio_id,
    )
    db.add(p)
    db.commit()
    return p


def test_funil_fecha_com_cadastro_direto(db):
    for i in range(3):
        _proc(db, "dist:%03d" % i)
    _proc(db, "dire:900", status=PROC_CADASTRADO, cliente="ATIVOS")

    d = DistribuidosBBService(db).dashboard()
    k = d["kpis"]

    assert k["total"] == 4
    # cumulativo: o CADASTRADO também passou pela distribuição
    assert k["distribuidos"] == 4
    assert k["cadastro_direto"] == 1
    # o pool de planilha também conta o cadastro direto
    assert d["planilhas"]["cadastrado_l1"] == 4


def test_por_data_traz_quebra_por_escritorio(db):
    e1 = BbEscritorio(escritorio_path="MDR / A", nome="A")
    e2 = BbEscritorio(escritorio_path="MDR / B", nome="B")
    db.add_all([e1, e2])
    db.commit()
    _proc(db, "aaa:001", escritorio_id=e1.id)
    _proc(db, "aaa:002", escritorio_id=e1.id)
    _proc(db, "bbb:003", escritorio_id=e2.id)
    _proc(db, "ccc:004")                      # sem escritório

    d = DistribuidosBBService(db).dashboard()
    assert len(d["por_data"]) == 1
    dia = d["por_data"][0]
    assert dia["total"] == 4
    # chave IGUAL à do card "Por escritório responsável" (path > nome > —)
    assert dia["escritorios"]["MDR / A"] == 2
    assert dia["escritorios"]["MDR / B"] == 1
    assert dia["escritorios"]["—"] == 1
    # a quebra por cliente continua intacta
    assert dia["clientes"]["BB"] == 4
