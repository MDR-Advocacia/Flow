"""
Testes da terceira contingência (DJEN/Comunica).

O que precisa estar garantido:
  - CNJ ambíguo NÃO vira vínculo chutado (1.370 CNJs da base têm mais de um
    lawsuit_id — errar aqui cria tarefa no processo errado);
  - a publicação sai no contrato do L1, com id sintético negativo, pra entrar
    pelo caminho existente sem migration;
  - 403 da Comunica é reportado como geo-bloqueio/proxy, não como erro genérico
    — é o modo de falha que vai acontecer se esquecerem o DJEN_PROXY;
  - o DJEN só é acionado DEPOIS que o relatório falhou.
"""
import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.lawsuit_cache import LawsuitCache
from app.models.publication_search import PublicationRecord
from app.services import djen_publication_fallback as djen


@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _comunicacao(**kw):
    base = {
        "id": 680986671,
        "hash": "abc123",
        "numero_processo": "0000967-85.2024.5.05.0019",
        "data_disponibilizacao": "2026-07-30",
        "siglaTribunal": "TRT5",
        "tipoComunicacao": "Intimação",
        "nomeOrgao": "19ª Vara do Trabalho",
        "texto": "PODER JUDICIÁRIO ... intimação para contrarrazões.",
    }
    base.update(kw)
    return base


# ── Utilitários ────────────────────────────────────────────────────────

def test_parse_oabs_aceita_lista_e_ignora_lixo():
    assert djen.parse_oabs("5553:RN, 1234:BA") == [("5553", "RN"), ("1234", "BA")]
    assert djen.parse_oabs("5553:rn") == [("5553", "RN")]
    assert djen.parse_oabs("sem_uf") == []
    assert djen.parse_oabs(None) == []


def test_texto_limpo_remove_html_do_djen():
    t = djen.texto_limpo("<p>PODER<br/>JUDICI&Aacute;RIO</p>   <b>x</b>")
    assert "<" not in t and "&" not in t
    assert "PODER" in t and "JUDICI" in t


def test_janela_e_de_ontem_ate_hoje():
    de, ate = djen.janela_padrao()
    assert (ate - de).days == 1


# ── Resolução do processo ──────────────────────────────────────────────

def test_cnj_com_um_unico_processo_resolve(db):
    db.add(PublicationRecord(
        search_id=1, legal_one_update_id=1, linked_lawsuit_id=3903,
        linked_lawsuit_cnj="0000967-85.2024.5.05.0019", status="NOVO",
    ))
    db.commit()
    mapa, ambiguos = djen.mapa_cnj_para_processo(db)
    assert mapa["00009678520245050019"] == 3903
    assert ambiguos == set()


def test_cnj_ambiguo_fica_de_fora_do_mapa(db):
    """Apenso ou pasta duplicada: o mesmo CNJ em dois lawsuit_id."""
    for i, lid in enumerate((3903, 4152), start=1):
        db.add(PublicationRecord(
            search_id=1, legal_one_update_id=i, linked_lawsuit_id=lid,
            linked_lawsuit_cnj="0000967-85.2024.5.05.0019", status="NOVO",
        ))
    db.commit()
    mapa, ambiguos = djen.mapa_cnj_para_processo(db)
    assert "00009678520245050019" not in mapa
    assert "00009678520245050019" in ambiguos


def test_lawsuit_cache_complementa_o_mapa(db):
    db.add(LawsuitCache(
        lawsuit_id=5000,
        payload={"id": 5000, "identifierNumber": "0000967-85.2024.5.05.0019",
                 "responsibleOfficeId": 61},
    ))
    db.commit()
    mapa, _ = djen.mapa_cnj_para_processo(db)
    assert mapa["00009678520245050019"] == 5000


# ── Adaptação ──────────────────────────────────────────────────────────

def test_adaptar_devolve_o_contrato_do_legal_one():
    pub = djen.adaptar(_comunicacao(), lawsuit_id=3903, office_id=61)
    assert pub["date"] == "2026-07-30T00:00:00Z"
    assert pub["originType"] == "OfficialJournalsCrawler"
    assert pub["typeId"] == 5
    assert pub["relationships"] == [{"linkType": "Litigation", "linkId": 3903}]
    assert pub["_responsible_office_id"] == 61
    assert pub["_cnj"] == "0000967-85.2024.5.05.0019"
    assert "PODER JUDICIÁRIO" in pub["description"]


def test_adaptar_sem_processo_nao_inventa_vinculo():
    pub = djen.adaptar(_comunicacao(), lawsuit_id=None, office_id=None)
    assert pub["relationships"] == []


def test_adaptar_descarta_sem_texto_ou_sem_data():
    assert djen.adaptar(_comunicacao(texto=""), lawsuit_id=1, office_id=1) is None
    assert djen.adaptar(
        _comunicacao(data_disponibilizacao=""), lawsuit_id=1, office_id=1
    ) is None


# ── Cliente HTTP ───────────────────────────────────────────────────────

class _R:
    def __init__(self, status=200, payload=None, text="", headers=None):
        self.status_code = status
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("sem json")
        return self._payload


def _cliente(respostas, monkeypatch):
    monkeypatch.setattr(djen.time, "sleep", lambda *_: None)
    seq = list(respostas)

    class _S:
        def __init__(self):
            self.proxies = {}

        def get(self, *a, **kw):
            return seq.pop(0)

    return djen.ComunicaClient(proxy="socks5h://x:1", session=_S(), delay=0)


def test_403_e_reportado_como_geobloqueio(monkeypatch):
    """Sem proxy BR a Comunica recusa — o erro tem que dizer isso."""
    c = _cliente([_R(status=403, text="Forbidden")], monkeypatch)
    with pytest.raises(djen.DjenIndisponivel) as exc:
        c._get({})
    assert "geo" in str(exc.value).lower() or "DJEN_PROXY" in str(exc.value)


def test_429_respeita_retry_after_e_tenta_de_novo(monkeypatch):
    c = _cliente([
        _R(status=429, headers={"Retry-After": "1"}),
        _R(status=200, payload={"status": "success", "items": []}),
    ], monkeypatch)
    assert c._get({})["status"] == "success"


def test_5xx_faz_backoff_e_tenta_de_novo(monkeypatch):
    c = _cliente([
        _R(status=502), _R(status=200, payload={"status": "success", "items": []}),
    ], monkeypatch)
    assert c._get({})["status"] == "success"


def test_http_200_com_status_semantico_ruim_e_erro(monkeypatch):
    c = _cliente([_R(status=200, payload={"status": "erro", "items": []})], monkeypatch)
    with pytest.raises(djen.DjenIndisponivel):
        c._get({})


def test_paginacao_para_quando_a_pagina_vem_incompleta(monkeypatch):
    itens = [_comunicacao(id=i) for i in range(djen.ITENS_POR_PAGINA)]
    c = _cliente([
        _R(status=200, payload={"status": "success", "count": 150, "items": itens}),
        _R(status=200, payload={"status": "success", "count": 150, "items": [_comunicacao(id=999)]}),
    ], monkeypatch)
    achados, meta = c.buscar_por_oabs(
        oabs=[("5553", "RN")],
        data_de=datetime.date(2026, 7, 30), data_ate=datetime.date(2026, 7, 31),
    )
    assert len(achados) == djen.ITENS_POR_PAGINA + 1
    assert meta["paginas"] == 2


def test_teto_oficial_de_10k_e_sinalizado(monkeypatch):
    c = _cliente([
        _R(status=200, payload={"status": "success", "count": 10_000, "items": [_comunicacao()]}),
    ], monkeypatch)
    _, meta = c.buscar_por_oabs(
        oabs=[("5553", "RN")],
        data_de=datetime.date(2026, 7, 30), data_ate=datetime.date(2026, 7, 31),
    )
    assert meta["truncadas"] == ["5553/RN"]


# ── Ponta a ponta ──────────────────────────────────────────────────────

def test_captura_vincula_o_que_da_e_deixa_o_ambiguo_sem_pasta(db, monkeypatch):
    # CNJ A: um processo só  → vincula
    db.add(PublicationRecord(
        search_id=1, legal_one_update_id=1, linked_lawsuit_id=3903,
        linked_lawsuit_cnj="0000967-85.2024.5.05.0019", status="NOVO",
    ))
    # CNJ B: dois processos → ambíguo, entra sem vínculo
    for i, lid in enumerate((7000, 7001), start=2):
        db.add(PublicationRecord(
            search_id=1, legal_one_update_id=i, linked_lawsuit_id=lid,
            linked_lawsuit_cnj="0133887-11.2018.8.06.0001", status="NOVO",
        ))
    db.commit()

    brutos = [
        _comunicacao(),
        _comunicacao(id=2, hash="def", numero_processo="0133887-11.2018.8.06.0001"),
    ]

    class _FakeCliente:
        proxy = "socks5h://x:1"

        def buscar_por_oabs(self, **kw):
            return brutos, {"paginas": 1, "brutos": len(brutos)}

    monkeypatch.setattr(djen, "ComunicaClient", lambda *a, **k: _FakeCliente())
    r = djen.capturar_publicacoes(db)

    assert r["ok"] is True
    assert r["total"] == 2
    assert r["vinculadas"] == 1
    assert r["sem_vinculo"] == 1
    assert r["ambiguas"] == 1
    # Todas com id sintético negativo — entram sem migration.
    assert all(p["id"] < 0 for p in r["publicacoes"])
    # E o ambíguo NÃO ganhou pasta chutada.
    sem = [p for p in r["publicacoes"] if not p["relationships"]]
    assert len(sem) == 1


def test_captura_sem_oab_nao_tenta_nada(db, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "djen_oabs", "")
    r = djen.capturar_publicacoes(db)
    assert r["ok"] is False and r["motivo"] == "sem_oab_configurada"


def test_djen_indisponivel_devolve_motivo_sem_explodir(db, monkeypatch):
    class _Quebrado:
        proxy = ""

        def buscar_por_oabs(self, **kw):
            raise djen.DjenIndisponivel("403 geo-bloqueio")

    monkeypatch.setattr(djen, "ComunicaClient", lambda *a, **k: _Quebrado())
    r = djen.capturar_publicacoes(db)
    assert r["ok"] is False
    assert r["motivo"] == "djen_indisponivel"
    assert "403" in r["detalhe"]


def test_publicacao_de_processo_fora_da_carteira_e_ignorada(db, monkeypatch):
    """A consulta por OAB traz um superconjunto: 192 de 1.017 em 31/07/2026.

    O /Updates do L1 — a fonte primária — também não traria essas. Importá-las
    encheria a fila de avulsas com publicação que ninguém aqui trata.
    """
    db.add(PublicationRecord(
        search_id=1, legal_one_update_id=1, linked_lawsuit_id=3903,
        linked_lawsuit_cnj="0000967-85.2024.5.05.0019", status="NOVO",
    ))
    db.commit()

    brutos = [
        _comunicacao(),  # da carteira
        _comunicacao(id=2, hash="zzz", numero_processo="9999999-99.2099.8.99.9999"),
    ]

    class _Fake:
        proxy = "socks5h://x:1"

        def buscar_por_oabs(self, **kw):
            return brutos, {"paginas": 1, "brutos": len(brutos)}

    monkeypatch.setattr(djen, "ComunicaClient", lambda *a, **k: _Fake())
    r = djen.capturar_publicacoes(db)
    assert r["total"] == 1
    assert r["fora_carteira"] == 1
    assert r["vinculadas"] == 1


def test_filtro_de_carteira_pode_ser_desligado(db, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "djen_somente_carteira", False)
    brutos = [_comunicacao(numero_processo="9999999-99.2099.8.99.9999")]

    class _Fake:
        proxy = "socks5h://x:1"

        def buscar_por_oabs(self, **kw):
            return brutos, {"paginas": 1, "brutos": 1}

    monkeypatch.setattr(djen, "ComunicaClient", lambda *a, **k: _Fake())
    r = djen.capturar_publicacoes(db)
    assert r["total"] == 1 and r["sem_vinculo"] == 1 and r["fora_carteira"] == 0
