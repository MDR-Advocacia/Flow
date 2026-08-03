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


# ── Pasta avulsa: duplicata entre CLIENTES é legítima ──────────────────
# O mesmo CNJ pode ter pasta do BB e do Banco Master — clientes diferentes no
# mesmo processo. A dedup do import do L1 é tenant-wide e derrubava a segunda,
# que e' justamente a que o modal "Pasta avulsa" existe pra criar.

CNJ_MASTER = "0800251-98.2026.8.14.0004"
DIGITOS_MASTER = "08002519820268140004"


def _avulso(db, cnj=CNJ_MASTER, path="MDR Advocacia / Área operacional / Banco Master / Réu"):
    p = BbProcesso(
        cnj=cnj, fingerprint=f"avulso:cnj:{DIGITOS_MASTER}", escritorio_path=path,
        planilha_status=POOL_PENDENTE_CADASTRO, status="DISTRIBUIDO",
    )
    db.add(p)
    db.commit()
    return p


def _mock_l1(monkeypatch, *, mesmo=None, outros=None, explode=False):
    from app.services.distribuidos_bb import cadastro_l1

    monkeypatch.setattr(
        "app.services.legal_one_client.LegalOneApiClient", lambda *a, **k: object(),
    )
    monkeypatch.setattr(cadastro_l1, "resolver_office_por_path", lambda c, p: 61)

    def _verificar(client, cnj, office_id):
        if explode:
            raise RuntimeError("L1 fora")
        return {
            "duplicado": bool(mesmo),
            "no_mesmo_escritorio": mesmo or [],
            "em_outros_escritorios": outros or [],
        }

    monkeypatch.setattr(cadastro_l1, "verificar_duplicado", _verificar)


def test_pasta_de_outro_cliente_libera_o_cnj(db, monkeypatch):
    """Existe a pasta do BB; a do Master precisa nascer mesmo assim."""
    from app.services.distribuidos_bb.avulso_service import _liberar_dup_de_outro_cliente

    p = _avulso(db)
    _mock_l1(monkeypatch, outros=[{"id": 67274, "folder": "Proc - 0062000", "office": 23}])
    assert _liberar_dup_de_outro_cliente(db, p) == {DIGITOS_MASTER}


def test_pasta_no_MESMO_escritorio_aborta(db, monkeypatch):
    """Duas pastas do mesmo cliente no mesmo escritório é erro de operação."""
    from app.services.distribuidos_bb.avulso_service import _liberar_dup_de_outro_cliente

    p = _avulso(db)
    _mock_l1(monkeypatch, mesmo=[{"id": 99, "folder": "Proc - 0099999", "office": 61}])
    with pytest.raises(ValueError) as exc:
        _liberar_dup_de_outro_cliente(db, p)
    assert "Proc - 0099999" in str(exc.value)


def test_sem_duplicata_nao_libera_nada(db, monkeypatch):
    from app.services.distribuidos_bb.avulso_service import _liberar_dup_de_outro_cliente

    p = _avulso(db)
    _mock_l1(monkeypatch)
    assert _liberar_dup_de_outro_cliente(db, p) == set()


def test_l1_fora_nao_libera_as_cegas(db, monkeypatch):
    """Melhor não liberar do que liberar sem saber o que existe lá."""
    from app.services.distribuidos_bb.avulso_service import _liberar_dup_de_outro_cliente

    p = _avulso(db)
    _mock_l1(monkeypatch, explode=True)
    assert _liberar_dup_de_outro_cliente(db, p) == set()


def test_processo_sem_cnj_nao_consulta_o_l1(db):
    from app.services.distribuidos_bb.avulso_service import _liberar_dup_de_outro_cliente

    p = BbProcesso(cnj=None, fingerprint="avulso:x", planilha_status=POOL_PENDENTE_CADASTRO,
                   status="DISTRIBUIDO")
    db.add(p); db.commit()
    assert _liberar_dup_de_outro_cliente(db, p) == set()
