"""O monitor de cadastro não deixa a migração em massa sequestrar a API do L1.

Incidente 27/08/2026: o tombamento do Banco Master deixou ~5.600 processos
PENDENTE_CADASTRO. O monitor roda de 2 em 2 minutos com lote de 300, e cada
processo custa ao menos uma chamada ao L1 — ~150 req/min num tenant que
aguenta ~90. O job de PUBLICAÇÕES passou a morrer em "Máximo de tentativas
excedido" (429): 15 buscas seguidas falharam de madrugada e o operador
acordou sem publicação nenhuma.

Regra: fluxo diário primeiro; tombamento só com as sobras, até a cota.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.distribuidos_bb import POOL_PENDENTE_CADASTRO, BbProcesso
from app.services.distribuidos_bb import cadastro_monitor_worker as mw


@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _mk(db, n, *, tombamento):
    for i in range(n):
        db.add(BbProcesso(
            cliente="MASTER" if tombamento else "BB",
            cnj=None, npj=None,          # sem identificador: não chama o L1
            fingerprint=f"{'t' if tombamento else 'd'}:{i}",
            status="DISTRIBUIDO", planilha_status=POOL_PENDENTE_CADASTRO,
            raw={"tombamento": {"aba": "x"}} if tombamento else {"outra": 1},
        ))
    db.commit()


def test_tombamento_limitado_a_cota(db, monkeypatch):
    monkeypatch.setattr(mw, "_COTA_TOMBAMENTO", 40)
    _mk(db, 5, tombamento=False)
    _mk(db, 500, tombamento=True)

    r = mw.verificar_pendentes(db, limite=300)
    # 5 do dia a dia + 40 de cota: NUNCA os 300 que o L1 não aguenta.
    assert r["tombamento"] == 40
    assert r["sem_cnj_ignorados"] == 45


def test_fluxo_diario_nunca_perde_vaga(db, monkeypatch):
    """Mesmo com a fila entupida de tombamento, o dia a dia entra inteiro."""
    monkeypatch.setattr(mw, "_COTA_TOMBAMENTO", 40)
    _mk(db, 120, tombamento=False)
    _mk(db, 900, tombamento=True)

    r = mw.verificar_pendentes(db, limite=300)
    assert r["sem_cnj_ignorados"] == 160     # 120 do dia + 40 de cota
    assert r["tombamento"] == 40


def test_sem_tombamento_nada_muda(db, monkeypatch):
    monkeypatch.setattr(mw, "_COTA_TOMBAMENTO", 40)
    _mk(db, 7, tombamento=False)
    r = mw.verificar_pendentes(db, limite=300)
    assert r["tombamento"] == 0
    assert r["sem_cnj_ignorados"] == 7
