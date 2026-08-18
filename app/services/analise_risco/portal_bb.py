"""Leitor da seção "Análise de Risco" do portal BB (PAJ) — HTTP puro.

Decodificado do HAR de 2026-08-18 (navegação real até a seção no processo
NPJ 2024/0116713-000). O front do PAJ (analise-risco.service.min.js) chama:

  POST {paj}/resources/app/v1/processo/analise/risco/pendencia/consultar
       body: o número do processo SEM máscara (ex.: 20240116713), como JSON cru.
       resposta: {"data": null}  -> SEM pendência (o banner amarelo "Não existe
                 nenhuma pendência de análise para este processo")
                 {"data": {...}} -> pendência ABERTA; o template renderiza
                 analise.estado.descricao (ex.: "Alçada 1") e
                 analise.possibilidadeExitoAutor.descricao (ex.: "Provável").

Semântica pro fluxo do BB Réu: a tarefa de Análise de Risco existe PORQUE havia
pendência; fazer a análise FECHA a pendência. Logo, depois da tarefa cumprida
no L1: pendência aberta = análise NÃO feita (divergente); sem pendência =
análise feita. Reusa a sessão OneLog via `vinculos_bb.montar_sessao`.

Bônus do mesmo HAR (resolver NPJ a partir do CNJ, quando a pasta não trouxer):
  GET {paj}/resources/app/portal/cadastro/processo/pesquisa-avancada/
      numero-processo/{cnj_sem_mascara}?numeroPosicaoLista=1
      -> data.processos[0].numeroProcesso (NPJ sem máscara).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import requests

from app.services.distribuidos_bb.vinculos_bb import _base, apenas_digitos


def npj_sem_mascara(valor: Optional[str]) -> Optional[str]:
    """Normaliza "2024/0116713-000" (pasta do L1) -> "20240116713" (o que o PAJ
    consome). NPJ tem 11 dígitos (4 do ano + 7 do número); a variação de 3
    dígitos no fim é descartada (a consulta é sempre no principal)."""
    digs = apenas_digitos(valor)
    if len(digs) == 14:
        digs = digs[:11]
    if len(digs) != 11:
        return None
    return digs


@dataclass
class PendenciaAnalise:
    """Resultado da consulta de pendência no portal."""

    pendencia_aberta: bool
    estado: Optional[str] = None  # ex.: "Alçada 1"
    exito: Optional[str] = None   # ex.: "Provável"
    raw: Any = None


def _descricao(no: Any, *chaves: str) -> Optional[str]:
    """Anda tolerante pelo JSON: pega no[chave]["descricao"] (ou o valor cru se
    for string) na primeira chave que existir."""
    if not isinstance(no, dict):
        return None
    for chave in chaves:
        v = no.get(chave)
        if isinstance(v, dict):
            d = v.get("descricao") or v.get("nome")
            if d:
                return str(d)
        elif isinstance(v, str) and v.strip():
            return v.strip()
    return None


def consultar_pendencia(
    sess: requests.Session,
    numero_processo: str,
    *,
    base_url: Optional[str] = None,
    timeout: int = 30,
) -> PendenciaAnalise:
    """Consulta a pendência de análise de risco do NPJ (sem máscara).

    Levanta RuntimeError em resposta não-200/formato inesperado (o worker
    trata como tentativa falha e re-tenta no próximo tick)."""
    base = _base(base_url)
    r = sess.post(
        f"{base}/resources/app/v1/processo/analise/risco/pendencia/consultar",
        data=str(int(numero_processo)),
        headers={"Content-Type": "application/json;charset=UTF-8"},
        timeout=timeout,
    )
    if r.status_code != 200:
        raise RuntimeError(f"pendencia/consultar HTTP {r.status_code}: {r.text[:200]}")
    corpo = r.json()
    if corpo.get("status") not in ("OK", None):
        raise RuntimeError(f"pendencia/consultar status={corpo.get('status')}: {str(corpo)[:200]}")
    data = corpo.get("data")

    # Sem pendência: data null/lista vazia/objeto vazio.
    itens: list = []
    if isinstance(data, list):
        itens = [x for x in data if x]
    elif isinstance(data, dict) and data:
        itens = [data]
    if not itens:
        return PendenciaAnalise(pendencia_aberta=False, raw=data)

    primeiro = itens[0]
    return PendenciaAnalise(
        pendencia_aberta=True,
        estado=_descricao(primeiro, "estado", "estadoAnalise", "situacao"),
        exito=_descricao(primeiro, "possibilidadeExitoAutor", "possibilidadeExito"),
        raw=data,
    )


def resolver_npj_por_cnj(
    sess: requests.Session,
    cnj: str,
    *,
    base_url: Optional[str] = None,
    timeout: int = 30,
) -> Optional[str]:
    """CNJ -> NPJ sem máscara, pela pesquisa avançada do PAJ. None se não achar."""
    digs = apenas_digitos(cnj)
    if not digs:
        return None
    base = _base(base_url)
    r = sess.get(
        f"{base}/resources/app/portal/cadastro/processo/pesquisa-avancada/numero-processo/{digs}",
        params={"numeroPosicaoLista": 1},
        timeout=timeout,
    )
    if r.status_code != 200 or not r.text.strip():
        return None
    processos = ((r.json().get("data") or {}).get("processos")) or []
    if not processos:
        return None
    numero = processos[0].get("numeroProcesso")
    return str(numero) if numero else None
