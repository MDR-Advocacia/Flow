"""Preços da API Anthropic e cálculo de custo a partir do `usage`.

Isolado de propósito: preço é dado externo que muda sem aviso, então fica numa
tabela editável em UM lugar, longe da lógica. O banco guarda só os TOKENS (que
são fato e não envelhecem); o custo é derivado na leitura.

Fonte: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
(tabela de preços, consultada em 13/08/2026). Valores em USD por milhão de
tokens (MTok).

Os multiplicadores de cache são fixos e valem pra todos os modelos:
  - gravação de 5 min : 1,25 × entrada base
  - gravação de 1 hora: 2,00 × entrada base
  - leitura de cache  : 0,10 × entrada base
E acumulam com o desconto de 50% da Batch API.
"""
from __future__ import annotations

from typing import Any, Optional

# USD por milhão de tokens: (entrada base, saída)
PRECOS_POR_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-fable-5": (10.00, 50.00),
}

MULT_ESCRITA_5M = 1.25
MULT_ESCRITA_1H = 2.00
MULT_LEITURA_CACHE = 0.10
DESCONTO_BATCH = 0.50

_MTOK = 1_000_000


def _precos_do_modelo(modelo: Optional[str]) -> Optional[tuple[float, float]]:
    """Resolve o preço aceitando o ID datado (claude-haiku-4-5-20251001)."""
    if not modelo:
        return None
    if modelo in PRECOS_POR_MTOK:
        return PRECOS_POR_MTOK[modelo]
    # Casa pelo prefixo mais longo — os IDs de produção vêm com data no fim.
    candidatos = [k for k in PRECOS_POR_MTOK if modelo.startswith(k)]
    if not candidatos:
        return None
    return PRECOS_POR_MTOK[max(candidatos, key=len)]


def calcular_custo_usd(
    *,
    modelo: Optional[str],
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    ttl_cache: str = "5m",
    batch: bool = True,
) -> Optional[dict[str, Any]]:
    """Custo estimado em USD a partir dos contadores de `usage`.

    Devolve None quando o modelo é desconhecido — melhor não reportar custo do
    que reportar um número inventado com o preço de outro modelo.

    Lembrete da doc que muda a leitura dos números: `input_tokens` NÃO é a
    entrada total, é só o que vem DEPOIS do último breakpoint de cache. O total
    é `input + cache_read + cache_creation`.
    """
    precos = _precos_do_modelo(modelo)
    if precos is None:
        return None
    p_in, p_out = precos
    fator = DESCONTO_BATCH if batch else 1.0
    mult_escrita = MULT_ESCRITA_1H if ttl_cache == "1h" else MULT_ESCRITA_5M

    c_in = (input_tokens / _MTOK) * p_in * fator
    c_out = (output_tokens / _MTOK) * p_out * fator
    c_leitura = (cache_read_tokens / _MTOK) * p_in * MULT_LEITURA_CACHE * fator
    c_escrita = (cache_creation_tokens / _MTOK) * p_in * mult_escrita * fator

    total_entrada = input_tokens + cache_read_tokens + cache_creation_tokens
    # Contrafactual: o que essa mesma entrada custaria SEM cache nenhum. É o
    # número que prova (ou desmente) o ganho do prompt caching.
    c_sem_cache = (total_entrada / _MTOK) * p_in * fator + c_out

    return {
        "modelo": modelo,
        "batch": batch,
        "entrada_total_tokens": total_entrada,
        "custo_entrada_usd": round(c_in, 6),
        "custo_saida_usd": round(c_out, 6),
        "custo_leitura_cache_usd": round(c_leitura, 6),
        "custo_escrita_cache_usd": round(c_escrita, 6),
        "custo_total_usd": round(c_in + c_out + c_leitura + c_escrita, 6),
        "custo_sem_cache_usd": round(c_sem_cache, 6),
        "economia_usd": round(
            c_sem_cache - (c_in + c_out + c_leitura + c_escrita), 6
        ),
    }
