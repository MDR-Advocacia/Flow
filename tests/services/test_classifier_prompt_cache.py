"""Prompt caching do classificador: onde o breakpoint fica e o canário.

Duas coisas podem transformar isso de economia em prejuízo silencioso:

  1. o breakpoint cair na mensagem do usuário (texto da publicação, diferente
     em toda requisição) — grava uma entrada por requisição e não lê nenhuma;
  2. a API recusar a configuração de cache e o lote inteiro de 600+ falhar.

Os testes abaixo travam as duas.
"""
import pytest

from app.core.config import settings
from app.services.classifier.ai_client import AnthropicClassifierClient


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "chave-de-teste", raising=False)
    return AnthropicClassifierClient(api_key="chave-de-teste")


# ─────────────────────────────── onde o breakpoint fica

def test_sem_cache_o_system_continua_string_crua(cli):
    """Comportamento histórico preservado quando a flag está desligada."""
    req = cli.build_batch_request("1", "SYSTEM", "publicação", cache=False)
    assert req["params"]["system"] == "SYSTEM"


def test_com_cache_o_breakpoint_fica_no_system(cli):
    req = cli.build_batch_request("1", "SYSTEM", "publicação", cache=True)
    system = req["params"]["system"]
    assert isinstance(system, list) and len(system) == 1
    assert system[0]["text"] == "SYSTEM"
    assert system[0]["cache_control"]["type"] == "ephemeral"


def test_a_mensagem_do_usuario_nunca_recebe_cache_control(cli):
    """A armadilha central: breakpoint no bloco variável nunca acerta."""
    req = cli.build_batch_request("1", "SYSTEM", "texto da publicação", cache=True)
    msgs = req["params"]["messages"]
    assert msgs == [{"role": "user", "content": "texto da publicação"}]
    assert "cache_control" not in str(msgs)


def test_ttl_vem_da_configuracao(cli, monkeypatch):
    monkeypatch.setattr(settings, "classifier_prompt_cache_ttl", "1h")
    req = cli.build_batch_request("1", "SYSTEM", "x", cache=True)
    assert req["params"]["system"][0]["cache_control"]["ttl"] == "1h"
    monkeypatch.setattr(settings, "classifier_prompt_cache_ttl", "5m")
    req = cli.build_batch_request("1", "SYSTEM", "x", cache=True)
    assert req["params"]["system"][0]["cache_control"]["ttl"] == "5m"


def test_prefixo_identico_gera_payload_identico(cli):
    """Requisições que compartilham o escritório têm que produzir o MESMO
    bloco de system — é o que faz o cache acertar."""
    a = cli.build_batch_request("1", "SYSTEM-DO-ESCRITORIO-23", "pub A", cache=True)
    b = cli.build_batch_request("2", "SYSTEM-DO-ESCRITORIO-23", "pub B", cache=True)
    assert a["params"]["system"] == b["params"]["system"]
    assert a["params"]["messages"] != b["params"]["messages"]


# ─────────────────────────────── canário (aquecimento)

class _RespostaFake:
    def __init__(self, status=200, payload=None, texto=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = texto

    def json(self):
        return self._payload


class _ClienteHttpFake:
    """Substitui httpx.AsyncClient capturando o payload enviado."""

    ultimo_payload = None

    def __init__(self, resposta):
        self._resposta = resposta

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        type(self).ultimo_payload = json
        return self._resposta


@pytest.mark.asyncio
async def test_aquecimento_usa_max_tokens_zero_e_marca_o_system(cli, monkeypatch):
    fake = _ClienteHttpFake(_RespostaFake(
        200, {"usage": {"cache_creation_input_tokens": 12_979,
                        "cache_read_input_tokens": 0}},
    ))
    monkeypatch.setattr("app.services.classifier.ai_client.httpx.AsyncClient", fake)

    r = await cli.aquecer_cache("SYSTEM-GRANDE")

    assert r == {"ok": True, "gravado": 12_979, "lido": 0}
    p = _ClienteHttpFake.ultimo_payload
    # max_tokens=0: lê o prompt, grava o cache, não gera saída.
    assert p["max_tokens"] == 0
    assert p["system"][0]["cache_control"]["type"] == "ephemeral"
    assert p["system"][0]["text"] == "SYSTEM-GRANDE"
    # placeholder — nunca é respondido
    assert p["messages"] == [{"role": "user", "content": "warmup"}]


@pytest.mark.asyncio
async def test_aquecimento_devolve_erro_em_vez_de_levantar(cli, monkeypatch):
    """É o canário: precisa REPORTAR a falha, não explodir, pra quem chama
    poder desligar o cache e mandar o lote pelo caminho antigo."""
    fake = _ClienteHttpFake(_RespostaFake(400, texto="ttl invalido"))
    monkeypatch.setattr("app.services.classifier.ai_client.httpx.AsyncClient", fake)

    r = await cli.aquecer_cache("SYSTEM")
    assert r["ok"] is False
    assert "400" in r["erro"]


@pytest.mark.asyncio
async def test_aquecimento_nao_levanta_nem_com_excecao_de_rede(cli, monkeypatch):
    class _Explode:
        def __call__(self, *a, **kw):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            raise RuntimeError("conexão caiu")

    monkeypatch.setattr(
        "app.services.classifier.ai_client.httpx.AsyncClient", _Explode(),
    )
    r = await cli.aquecer_cache("SYSTEM")
    assert r["ok"] is False
    assert "conexão caiu" in r["erro"]
