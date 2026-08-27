"""Falha de batch de classificação nunca chega muda ao operador.

Caso real 27/08/2026: o lote #155 (1.039 publicações) morreu em
`httpx.WriteTimeout` no meio do upload para a Anthropic. Como `str()` dessas
exceções de rede é VAZIO, o painel mostrou "Falha" com o motivo em branco e o
log registrou "Lote 155: falha ao promover ()" — ninguém tinha como saber que
fora timeout de escrita.
"""
import httpx
import pytest

from app.services.publication_batch_classifier import _erro_legivel


@pytest.mark.parametrize("exc", [
    httpx.WriteTimeout(""),      # a do incidente
    httpx.ReadTimeout(""),
    httpx.ConnectError(""),
])
def test_excecao_muda_vira_nome_da_classe(exc):
    msg = _erro_legivel(exc)
    assert msg == exc.__class__.__name__
    assert msg.strip(), "mensagem vazia é o bug que este teste existe pra impedir"


def test_excecao_com_texto_mantem_o_texto_e_ganha_a_classe():
    msg = _erro_legivel(ValueError("Lista de requisições vazia."))
    assert msg == "ValueError: Lista de requisições vazia."


def test_espaco_em_branco_conta_como_vazio():
    assert _erro_legivel(httpx.WriteTimeout("   ")) == "WriteTimeout"
