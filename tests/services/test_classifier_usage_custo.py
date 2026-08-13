"""Telemetria de token da classificação: extração do usage e cálculo de custo.

Estes números vão ser lidos pra decidir se o prompt caching entra, então o
cálculo precisa estar certo — inclusive a parte contraintuitiva: `input_tokens`
NÃO é a entrada total, é só o que vem depois do último breakpoint de cache.
"""
import pytest

from app.services.classifier.ai_client import AnthropicClassifierClient
from app.services.classifier.anthropic_pricing import (
    DESCONTO_BATCH,
    calcular_custo_usd,
)

extrai = AnthropicClassifierClient.extract_usage_from_batch_result


# ─────────────────────────────────── extração do usage

def test_extrai_usage_de_item_bem_sucedido():
    item = {
        "custom_id": "123",
        "result": {
            "type": "succeeded",
            "message": {
                "usage": {
                    "input_tokens": 9_484,
                    "output_tokens": 120,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        },
    }
    assert extrai(item) == {
        "input_tokens": 9_484,
        "output_tokens": 120,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }


def test_extrai_usage_com_cache_preenchido():
    """Formato que esperamos ver DEPOIS de ligar o prompt caching."""
    item = {
        "result": {
            "type": "succeeded",
            "message": {
                "usage": {
                    "input_tokens": 300,
                    "output_tokens": 120,
                    "cache_read_input_tokens": 9_484,
                    "cache_creation_input_tokens": 0,
                },
            },
        },
    }
    u = extrai(item)
    assert u["cache_read_input_tokens"] == 9_484
    assert u["input_tokens"] == 300


@pytest.mark.parametrize("item", [
    {},                                                  # vazio
    {"result": {"type": "errored"}},                     # item que falhou
    {"result": {"type": "succeeded", "message": {}}},    # sem usage
    {"result": {"type": "succeeded", "message": {"usage": None}}},
    {"result": {"type": "succeeded", "message": {"usage": "lixo"}}},
    {"result": None},
])
def test_extrai_usage_nunca_levanta(item):
    """Telemetria não pode derrubar a aplicação de um lote que classificou certo."""
    u = extrai(item)
    assert u == {
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
    }


def test_extrai_usage_tolera_campo_nulo():
    item = {"result": {"type": "succeeded", "message": {"usage": {
        "input_tokens": None, "output_tokens": 50,
    }}}}
    u = extrai(item)
    assert u["input_tokens"] == 0
    assert u["output_tokens"] == 50


# ─────────────────────────────────── cálculo de custo

def test_custo_haiku_sem_cache_bate_com_a_tabela():
    """1M de entrada + 1M de saída no Haiku 4.5, com desconto de batch.

    Preço base: entrada US$ 1,00 / saída US$ 5,00 por MTok. Com os 50% do
    Batch: 0,50 + 2,50 = US$ 3,00.
    """
    c = calcular_custo_usd(
        modelo="claude-haiku-4-5",
        input_tokens=1_000_000, output_tokens=1_000_000, batch=True,
    )
    assert c["custo_entrada_usd"] == pytest.approx(0.50)
    assert c["custo_saida_usd"] == pytest.approx(2.50)
    assert c["custo_total_usd"] == pytest.approx(3.00)
    # Sem cache envolvido, o contrafactual é o próprio custo.
    assert c["custo_sem_cache_usd"] == pytest.approx(3.00)
    assert c["economia_usd"] == pytest.approx(0.0)


def test_custo_sem_desconto_de_batch_e_o_dobro():
    kw = dict(modelo="claude-haiku-4-5", input_tokens=1_000_000)
    assert (
        calcular_custo_usd(batch=False, **kw)["custo_total_usd"]
        == pytest.approx(calcular_custo_usd(batch=True, **kw)["custo_total_usd"] / DESCONTO_BATCH)
    )


def test_id_datado_do_modelo_resolve_o_preco():
    """Em produção o model_used vem com data: claude-haiku-4-5-20251001."""
    datado = calcular_custo_usd(
        modelo="claude-haiku-4-5-20251001", input_tokens=1_000_000,
    )
    limpo = calcular_custo_usd(modelo="claude-haiku-4-5", input_tokens=1_000_000)
    assert datado["custo_total_usd"] == limpo["custo_total_usd"]


def test_modelo_desconhecido_devolve_none_em_vez_de_inventar():
    assert calcular_custo_usd(modelo="claude-inexistente-9", input_tokens=1000) is None
    assert calcular_custo_usd(modelo=None, input_tokens=1000) is None


def test_entrada_total_soma_os_tres_campos():
    """A pegadinha da API: input_tokens é só o que vem DEPOIS do breakpoint."""
    c = calcular_custo_usd(
        modelo="claude-haiku-4-5",
        input_tokens=300, cache_read_tokens=9_484, cache_creation_tokens=100,
    )
    assert c["entrada_total_tokens"] == 300 + 9_484 + 100


def test_leitura_de_cache_custa_um_decimo_da_entrada():
    lido = calcular_custo_usd(modelo="claude-haiku-4-5", cache_read_tokens=1_000_000)
    cru = calcular_custo_usd(modelo="claude-haiku-4-5", input_tokens=1_000_000)
    assert lido["custo_total_usd"] == pytest.approx(cru["custo_total_usd"] * 0.10)


def test_gravacao_1h_custa_mais_que_5m():
    kw = dict(modelo="claude-haiku-4-5", cache_creation_tokens=1_000_000)
    c5 = calcular_custo_usd(ttl_cache="5m", **kw)
    c1h = calcular_custo_usd(ttl_cache="1h", **kw)
    # 1,25x vs 2,00x da entrada base
    assert c5["custo_total_usd"] == pytest.approx(0.50 * 1.25)
    assert c1h["custo_total_usd"] == pytest.approx(0.50 * 2.00)
    assert c1h["custo_total_usd"] > c5["custo_total_usd"]


def test_cenario_real_do_lote_142_mostra_a_economia():
    """O lote #142 de produção: 582 registros, ~6,25 MTok de system reenviado.

    Cenário A (hoje): tudo como entrada crua.
    Cenário B (com cache): ~79k gravados uma vez, o resto lido a 10%.
    O teste trava a ORDEM DE GRANDEZA da economia — se alguém mexer nos
    multiplicadores e ela virar centavos ou dezenas de dólares, quebra aqui.
    """
    ENTRADA = 6_245_887
    SAIDA = 582 * 120
    GRAVADO = 78_664

    hoje = calcular_custo_usd(
        modelo="claude-haiku-4-5-20251001",
        input_tokens=ENTRADA, output_tokens=SAIDA,
    )
    com_cache = calcular_custo_usd(
        modelo="claude-haiku-4-5-20251001",
        input_tokens=0, output_tokens=SAIDA,
        cache_read_tokens=ENTRADA - GRAVADO,
        cache_creation_tokens=GRAVADO,
        ttl_cache="1h",
    )

    # As duas contas veem a MESMA entrada total — só muda como ela é cobrada.
    assert hoje["entrada_total_tokens"] == com_cache["entrada_total_tokens"] == ENTRADA

    # A economia do cache vem SÓ da entrada — a saída é cobrada igual nos dois
    # cenários, então fica separada pra ninguém confundir o ganho com o total.
    assert hoje["custo_entrada_usd"] == pytest.approx(3.12, abs=0.05)
    assert hoje["custo_saida_usd"] == pytest.approx(0.175, abs=0.01)
    assert hoje["custo_total_usd"] == pytest.approx(3.30, abs=0.05)
    assert com_cache["custo_saida_usd"] == hoje["custo_saida_usd"]
    assert com_cache["custo_total_usd"] < 0.60
    economia = hoje["custo_total_usd"] - com_cache["custo_total_usd"]
    assert 2.4 < economia < 3.0, f"economia fora da faixa esperada: {economia}"

    # E o contrafactual embutido tem que reproduzir o cenário de hoje.
    assert com_cache["custo_sem_cache_usd"] == pytest.approx(
        hoje["custo_total_usd"], abs=0.01
    )
