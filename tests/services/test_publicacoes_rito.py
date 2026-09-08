"""Rito do processo como CAMPO (pub016) — comum × juizado × trabalhista.

Pedido do operador em 08/09/2026, depois de a equipe cobrar: o rito existia
só na fila SEM PASTA e precisava valer para todo mundo, aparecer na tela e
virar filtro.

O que estes testes protegem:

  1. A ORDEM das fontes. Texto primeiro (grátis, roda no nascimento da
     publicação), cache depois, DataJud por último — e o DataJud NUNCA é
     consultado no caminho da captura, senão a madrugada passaria a
     depender de uma API pública para gravar publicação.
  2. O CACHE por CNJ, que só é correto porque rito de processo não muda.
     Inclusive o caso sutil: "perguntei e não deu" é resposta que se guarda,
     senão o mesmo CNJ é reperguntado para sempre.
  3. Falha de REDE não vira "não sei" gravado — aí a resposta seria errada
     e permanente.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models as _models  # noqa: F401 - registra as tabelas
from app.db.session import Base
from app.models.publication_rito import ProcessoRito
from app.services import publication_rito as rito_mod

CNJ = "0801234-56.2026.8.20.5001"


def _sessao():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)()


# ── o texto, que é a fonte de graça ──────────────────────────────────
@pytest.mark.parametrize("texto,esperado", [
    ("JUIZADO ESPECIAL CIVEL da comarca de Natal", "juizado"),
    ("TRIBUNAL DE JUSTICA - 2a Camara Civel, Desembargador relator", "comum"),
    ("TRIBUNAL REGIONAL DO TRABALHO da 21a Regiao", "trabalhista"),
    ("Intimacao para manifestacao no prazo legal.", None),
    ("", None),
    (None, None),
])
def test_rito_pelo_texto(texto, esperado):
    assert rito_mod.rito_pelo_texto(texto)[0] == esperado


def test_turma_recursal_vence_o_cabecalho_do_tribunal():
    """Publicação de turma recursal cita "Tribunal de Justiça" no cabeçalho.
    O sinal mais específico é que manda, senão todo juizado viraria comum."""
    texto = "TRIBUNAL DE JUSTICA DO RN - Turma Recursal dos Juizados Especiais"
    assert rito_mod.rito_pelo_texto(texto)[0] == "juizado"


# ── a captura não pode depender de rede ──────────────────────────────
def test_captura_nao_consulta_datajud(monkeypatch):
    """`consultar_datajud=False` é o modo do nascimento da publicação: se
    ele tocasse a rede, a captura da madrugada passaria a depender de uma
    API pública para conseguir gravar."""
    db = _sessao()

    def _explode(*a, **k):
        raise AssertionError("a captura NAO pode consultar o DataJud")

    monkeypatch.setattr(rito_mod, "consultar_datajud_e_cachear", _explode)
    r = rito_mod.resolver_rito(db, "Intimacao sem pistas de rito", CNJ)
    assert r["rito"] is None


def test_texto_resolvido_alimenta_o_cache_do_cnj():
    """A próxima publicação do mesmo processo pode não trazer os sinais;
    o que o texto descobriu hoje vale para ela também."""
    db = _sessao()
    rito_mod.resolver_rito(db, "JUIZADO ESPECIAL CIVEL de Natal", CNJ)
    assert rito_mod.rito_em_cache(db, CNJ) == {
        "rito": "juizado", "fonte": "texto", "orgao": None,
    }


# ── o cache por CNJ ──────────────────────────────────────────────────
def test_cache_responde_sem_texto_e_sem_rede(monkeypatch):
    db = _sessao()
    rito_mod.guardar_rito(db, CNJ, "comum", "datajud", orgao="2a Vara Civel")
    monkeypatch.setattr(
        rito_mod, "consultar_datajud_e_cachear",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("nao devia perguntar")),
    )
    r = rito_mod.resolver_rito(db, "texto mudo", CNJ, consultar_datajud=True)
    assert r["rito"] == "comum" and "2a Vara Civel" in (r["evidencia"] or "")


def test_perguntei_e_nao_deu_e_resposta_que_se_guarda(monkeypatch):
    """A linha com rito=NULL existe para NÃO reperguntar. Sem ela, todo CNJ
    que o DataJud não conhece seria consultado em toda passagem, para
    sempre."""
    db = _sessao()
    chamadas = []

    class _ClienteVazio:
        def search_processes(self, alias, query):
            chamadas.append(alias)
            return {"hits": {"hits": []}}

    monkeypatch.setattr("app.services.citacoes_bm.datajud.get_client", lambda: _ClienteVazio())

    r1 = rito_mod.resolver_rito(db, "texto mudo", CNJ, consultar_datajud=True)
    r2 = rito_mod.resolver_rito(db, "texto mudo", CNJ, consultar_datajud=True)

    assert r1["rito"] is None and r2["rito"] is None
    assert len(chamadas) == 1, "reperguntou um CNJ que ja sabíamos nao ter resposta"
    assert db.query(ProcessoRito).filter_by(cnj_digitos=rito_mod.digitos(CNJ)).count() == 1


def test_falha_de_rede_nao_vira_resposta_gravada(monkeypatch):
    """Rede caindo não é "esse processo não tem rito" — gravar isso deixaria
    um erro passageiro permanente. Não cacheia, tenta de novo depois."""
    db = _sessao()

    class _ClienteQueCai:
        def search_processes(self, alias, query):
            raise RuntimeError("timeout")

    monkeypatch.setattr("app.services.citacoes_bm.datajud.get_client", lambda: _ClienteQueCai())
    r = rito_mod.resolver_rito(db, "texto mudo", CNJ, consultar_datajud=True)

    assert r["rito"] is None
    assert db.query(ProcessoRito).count() == 0, "cacheou uma falha de rede"


def test_cnj_invalido_nao_entra_no_cache():
    db = _sessao()
    rito_mod.guardar_rito(db, "12345", "comum", "texto")
    assert db.query(ProcessoRito).count() == 0
    assert rito_mod.rito_em_cache(db, "12345") is None
