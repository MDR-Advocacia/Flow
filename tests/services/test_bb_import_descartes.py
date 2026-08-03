"""
Testes do descarte de linhas no import do Legal One (Distribuídos BB).

Nasceram de um caso real: em 31/07/2026 o processo 0801099-88.2026.8.14.0003
saiu na planilha 57, o L1 recusou a linha por congestionamento da PRÓPRIA
infraestrutura dele (`ServiceBusy`, código 50002 — a mensagem diz "wait 10
seconds and try again"), e o Flow tratou isso como erro definitivo. O processo
ficou "Pendente cadastro" com a coluna `erro` NULL: nenhum registro do motivo,
nenhum alerta. Reenviado à mão em 03/08, o L1 aceitou de primeira.

O que precisa estar garantido:
  - erro TRANSITÓRIO do L1 não descarta a linha — ela é reenviada;
  - erro REAL descarta, mas o motivo é gravado no processo;
  - duplicata continua se comportando como antes.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.distribuidos_bb import (
    POOL_CADASTRADO_L1,
    POOL_PENDENTE_CADASTRO,
    BbEvento,
    BbProcesso,
)
from app.services.distribuidos_bb.cadastro_descartes import registrar_descartes
from app.services.distribuidos_bb.import_l1_service import (
    _linhas_novas,
    classificar_linha,
)


# A mensagem EXATA que o L1 devolveu em 31/07/2026.
ERRO_REAL_DO_INCIDENTE = (
    "The request was terminated because the namespace MATTER-TRACKER-PRODUCTION "
    "is being throttled. Error code : 50002. Please wait 10 seconds and try again. "
    "To know more visit https://aka.ms/sbResourceMgrExceptions ... (ServiceBusy)"
)

CNJ = "0801099-88.2026.8.14.0003"


@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


# ── Classificação ──────────────────────────────────────────────────────

def test_throttling_do_l1_nao_descarta_a_linha():
    """O caso do incidente: erro transitório tem que ser reenviado."""
    cadastrar, motivo = classificar_linha(
        {"errors": [{"message": ERRO_REAL_DO_INCIDENTE}], "identifierNumber": CNJ},
        set(),
    )
    assert cadastrar is True
    assert motivo == ""


@pytest.mark.parametrize("msg", [
    "The namespace is being throttled",
    "ServiceBusy",
    "ServerBusy: try later",
    "Error code : 50002",
    "The service is temporarily unavailable",
    "Request timed out",
    "HTTP 503 from downstream",
])
def test_variantes_de_erro_transitorio_sao_reenviadas(msg):
    cadastrar, _ = classificar_linha({"errors": [{"message": msg}]}, set())
    assert cadastrar is True


def test_erro_de_validacao_descarta_e_explica():
    cadastrar, motivo = classificar_linha(
        {"errorMessage": "Comarca não encontrada", "identifierNumber": CNJ}, set(),
    )
    assert cadastrar is False
    assert "Comarca não encontrada" in motivo


def test_duplicata_com_cnj_continua_fora():
    cadastrar, motivo = classificar_linha(
        {"duplicated": True, "identifierNumber": CNJ}, set(),
    )
    assert cadastrar is False
    assert "duplicata" in motivo.lower() or "já existente" in motivo


def test_duplicata_sem_cnj_continua_entrando():
    """BB Autor/pré-judicial: o L1 acusa dup só pelo nome do autor."""
    cadastrar, _ = classificar_linha({"duplicated": True, "identifierNumber": ""}, set())
    assert cadastrar is True


def test_cnj_liberado_resgata_a_duplicata():
    digitos = "08010998820268140003"
    cadastrar, _ = classificar_linha(
        {"duplicated": True, "identifierNumber": CNJ}, {digitos},
    )
    assert cadastrar is True


# ── Separação ──────────────────────────────────────────────────────────

def test_linhas_novas_devolve_os_descartes_com_motivo():
    novas, descartadas = _linhas_novas([
        {"id": 1, "identifierNumber": "A"},
        {"id": 2, "errors": [{"message": ERRO_REAL_DO_INCIDENTE}], "identifierNumber": "B"},
        {"id": 3, "errorMessage": "campo obrigatório", "identifierNumber": "C"},
        {"id": 4, "duplicated": True, "identifierNumber": "D"},
    ])
    assert [x["id"] for x in novas] == [1, 2], "a transitória (2) tem que entrar"
    assert [x["id"] for x in descartadas] == [3, 4]
    assert all(d["motivo"] for d in descartadas), "todo descarte precisa de motivo"


# ── Registro do motivo no processo ─────────────────────────────────────

def _processo(db, cnj=CNJ, planilha_id=57):
    p = BbProcesso(
        cnj=cnj, npj="2026/0234199-000", planilha_id=planilha_id,
        planilha_status=POOL_PENDENTE_CADASTRO, status="DISTRIBUIDO",
        fingerprint=f"fp-{cnj}-{planilha_id}",
    )
    db.add(p)
    db.commit()
    return p


def test_motivo_e_gravado_no_processo(db):
    """Era o buraco: 917 processos na base, ZERO com motivo registrado."""
    p = _processo(db)
    rel = {"descartadas": [{"id": 122305, "cnj": CNJ, "motivo": "O Legal One recusou a linha: X"}]}

    assert registrar_descartes(db, rel, planilha_id=57) == 1
    db.refresh(p)
    assert p.erro and "recusou" in p.erro
    # E o operador precisa ver isso no histórico, não só numa coluna.
    ev = db.query(BbEvento).filter(BbEvento.processo_id == p.id).all()
    assert any(e.acao == "Não cadastrado" for e in ev)


def test_casa_o_processo_pelo_cnj_mesmo_com_mascara_diferente(db):
    p = _processo(db, cnj=CNJ)
    rel = {"descartadas": [{"id": 1, "cnj": "08010998820268140003", "motivo": "erro"}]}
    assert registrar_descartes(db, rel, planilha_id=57) == 1
    db.refresh(p)
    assert p.erro


def test_nao_toca_em_processo_de_outra_planilha(db):
    p = _processo(db, planilha_id=99)
    rel = {"descartadas": [{"id": 1, "cnj": CNJ, "motivo": "erro"}]}
    assert registrar_descartes(db, rel, planilha_id=57) == 0
    db.refresh(p)
    assert p.erro is None


def test_nao_toca_em_processo_ja_cadastrado(db):
    p = _processo(db)
    p.planilha_status = POOL_CADASTRADO_L1
    db.commit()
    rel = {"descartadas": [{"id": 1, "cnj": CNJ, "motivo": "erro"}]}
    assert registrar_descartes(db, rel, planilha_id=57) == 0


def test_sem_descartes_nao_faz_nada(db):
    assert registrar_descartes(db, {"novos": 3}, planilha_id=57) == 0
    assert registrar_descartes(db, {}, planilha_id=57) == 0


def test_linha_sem_processo_correspondente_nao_quebra(db):
    """Resíduo de import antigo no staging do L1."""
    rel = {"descartadas": [{"id": 1, "cnj": "9999999-99.2099.8.99.9999", "motivo": "x"}]}
    assert registrar_descartes(db, rel, planilha_id=57) == 0


def test_falha_ao_registrar_nunca_derruba_o_cadastro(db):
    """O cadastro dos que deram certo não pode cair por causa do log."""
    rel = {"descartadas": [{"id": 1, "cnj": CNJ, "motivo": "erro"}]}
    db.close()  # sessão inutilizável de propósito
    assert registrar_descartes(db, rel, planilha_id=57) == 0
