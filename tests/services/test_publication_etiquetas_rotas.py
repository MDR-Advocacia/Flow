"""Job de etiquetas: recurso e incidente têm rota própria, e pasta que falha espera.

O ACHADO (10/09/2026, auditando a rotina)
-----------------------------------------
Os mesmos 17 lawsuits falhavam em TODO tick desde a captura de 08/09. A página
`/processos/processos/edit/{id}` só existe para PROCESSO: recurso e incidente
dão 404 nela e respondem em `/processos/recursos/edit/{id}` e
`/processos/incidentes/edit/{id}` (sondado em produção — 2 incidentes e 1
recurso). Sem cache, essas pastas ficavam sem chip, sem filtro e sem a
exceção por etiqueta dos templates — e a rotina gastava ~800 GETs por dia
batendo nas mesmas 17.
"""
import pytest

from app.services import publication_etiquetas as mod

BASE = "https://l1.exemplo"
HIDDEN_NERC = (
    '<input id="selectedTagsHidden" type="hidden" value="[{&quot;Id&quot;:83,'
    '&quot;Name&quot;:&quot;NERC&quot;,&quot;ClassName&quot;:&quot;tag-color-orange&quot;,'
    '&quot;ColorId&quot;:2}]" />'
)
ERRO_404 = (404, None, "<title>Erro</title>")


class _Resposta:
    def __init__(self, status, url, text):
        self.status_code = status
        self.url = url
        self.text = text


class _Sessao:
    def __init__(self):
        self.relogins = 0

    def _invalidate_session(self):
        pass

    def _ensure_session(self):
        self.relogins += 1
        return {"sessao": "nova"}


@pytest.fixture(autouse=True)
def _sem_espera(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    mod._falhas_recentes.clear()
    yield
    mod._falhas_recentes.clear()


def _l1_falso(monkeypatch, respostas):
    """respostas: path -> (status, path_final|None, html) ou lista delas em sequência."""
    chamadas = []

    def get(url, cookies=None, timeout=None, allow_redirects=True):
        path = url.replace(BASE, "")
        chamadas.append(path)
        resposta = respostas[path]
        if isinstance(resposta, list):
            resposta = resposta.pop(0)
        status, final, html = resposta
        return _Resposta(status, BASE + (final or path), html)

    monkeypatch.setattr(mod.requests, "get", get)
    return chamadas


def test_processo_responde_na_primeira_rota(monkeypatch):
    chamadas = _l1_falso(monkeypatch, {
        "/processos/processos/edit/1": (200, None, HIDDEN_NERC),
    })

    resposta, _ = mod._get_pagina_edicao(BASE, 1, {}, _Sessao())

    assert chamadas == ["/processos/processos/edit/1"]
    assert mod._parse_etiquetas(resposta.text)[0]["name"] == "NERC"


def test_recurso_cai_na_rota_de_recurso(monkeypatch):
    chamadas = _l1_falso(monkeypatch, {
        "/processos/processos/edit/55114": ERRO_404,
        "/processos/recursos/edit/55114": (200, None, HIDDEN_NERC),
    })

    resposta, _ = mod._get_pagina_edicao(BASE, 55114, {}, _Sessao())

    assert chamadas == ["/processos/processos/edit/55114", "/processos/recursos/edit/55114"]
    assert mod._parse_etiquetas(resposta.text)[0]["id"] == 83


def test_incidente_cai_na_rota_de_incidente(monkeypatch):
    chamadas = _l1_falso(monkeypatch, {
        "/processos/processos/edit/59347": ERRO_404,
        "/processos/recursos/edit/59347": ERRO_404,
        "/processos/incidentes/edit/59347": (200, None, HIDDEN_NERC),
    })

    resposta, _ = mod._get_pagina_edicao(BASE, 59347, {}, _Sessao())

    assert chamadas[-1] == "/processos/incidentes/edit/59347"
    assert resposta.status_code == 200


def test_pasta_inexistente_devolve_404_e_nao_vira_cache(monkeypatch):
    _l1_falso(monkeypatch, {
        "/processos/processos/edit/9": ERRO_404,
        "/processos/recursos/edit/9": ERRO_404,
        "/processos/incidentes/edit/9": ERRO_404,
    })

    resposta, _ = mod._get_pagina_edicao(BASE, 9, {}, _Sessao())

    assert resposta.status_code == 404  # o job conta falha e NÃO grava []


def test_sessao_caida_reloga_uma_vez_e_repete_a_mesma_rota(monkeypatch):
    chamadas = _l1_falso(monkeypatch, {
        "/processos/processos/edit/1": [
            (200, "/login?ReturnUrl=%2fprocessos", "<form>login</form>"),
            (200, None, HIDDEN_NERC),
        ],
    })
    sessao = _Sessao()

    resposta, cookies = mod._get_pagina_edicao(BASE, 1, {"sessao": "velha"}, sessao)

    assert sessao.relogins == 1
    assert cookies == {"sessao": "nova"}
    assert chamadas == ["/processos/processos/edit/1", "/processos/processos/edit/1"]
    assert mod._parse_etiquetas(resposta.text)


def test_pasta_que_falhou_espera_antes_de_nova_tentativa():
    mod._falhas_recentes[7] = 1000.0

    assert mod._adiada(7, 1000.0 + 60)
    assert not mod._adiada(7, 1000.0 + mod._ESPERA_APOS_FALHA_S + 1)
    assert not mod._adiada(8, 1000.0)
