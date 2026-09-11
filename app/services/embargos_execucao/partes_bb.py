"""Partes demandadas da execução, lidas no portal do BB pelo NPJ.

Reusa o `PortalBBColetor.extrair_envolvidos` do Distribuídos BB (capa do NPJ →
aba "Pessoas do Processo"), provado em produção sob Xvfb não-headless — o PAJ
sobe vazio em headless. Na execução o BB é o autor; demandados são o polo
oposto ao do banco (identificado pelo CNPJ 00.000.000/0001-91).

Armadilha herdada: `extrair_envolvidos` devolve [] tanto quando a capa não
carrega quanto quando não há ninguém. Lista vazia aqui é ERRO (re-tenta), nunca
"execução sem partes".
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.services.distribuidos_bb.normalizacao import (
    polo_envolvido_normalizado,
    tipo_pessoa_por_documento,
)
from app.services.distribuidos_bb.vinculos_bb import normalizar_documento
from app.services.embargos_execucao.normaliza import nome_normalizado

logger = logging.getLogger(__name__)

CNPJ_BB = "00000000000191"


def _eh_bb(env: dict[str, Any]) -> bool:
    doc = normalizar_documento(env.get("cpf_cnpj"))
    return doc == CNPJ_BB or "BANCO DO BRASIL" in nome_normalizado(env.get("nome"))


def separar_demandadas(envolvidos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normaliza os envolvidos do portal e marca quem é demandado.

    Polo do banco achado → demandados = o outro polo (Ativo↔Passivo).
    Banco não achado → na execução do BB Autor, demandado = Passivo.
    Interessados (advogado adverso etc.) nunca são demandados."""
    linhas = []
    polo_bb: Optional[str] = None
    for env in envolvidos or []:
        polo = polo_envolvido_normalizado(env.get("polo"))
        doc = normalizar_documento(env.get("cpf_cnpj"))
        linha = {
            "polo": polo,
            "nome": (env.get("nome") or "").strip(),
            "cpf_cnpj": doc,
            "tipo_pessoa": tipo_pessoa_por_documento(doc),
            "relacao_bb": (env.get("relacao") or "").strip() or None,
            "eh_bb": _eh_bb(env),
            "raw": env,
        }
        if linha["eh_bb"] and polo in ("Ativo", "Passivo"):
            polo_bb = polo
        linhas.append(linha)

    alvo = {"Ativo": "Passivo", "Passivo": "Ativo"}.get(polo_bb or "", "Passivo")
    for linha in linhas:
        linha["demandada"] = bool(
            linha["nome"] and not linha["eh_bb"] and linha["polo"] == alvo
        )
    return linhas


def coletar(npjs: list[str], coletor=None) -> dict[str, list[dict[str, Any]] | Exception]:
    """Abre UMA sessão do portal e lê as partes de cada NPJ.

    Devolve {npj: linhas} ou {npj: exceção}. O coletor é injetável nos testes."""
    from app.services.distribuidos_bb.portal import PortalBBColetor

    saida: dict[str, list[dict[str, Any]] | Exception] = {}
    with (coletor or PortalBBColetor()) as portal:
        for npj in npjs:
            try:
                envolvidos = portal.extrair_envolvidos(npj)
                if not envolvidos:
                    raise RuntimeError(
                        "capa do NPJ não carregou ou veio sem 'Pessoas do Processo'"
                    )
                saida[npj] = separar_demandadas(envolvidos)
            except Exception as exc:  # noqa: BLE001 — um NPJ não derruba os outros
                logger.warning("Embargos: partes do NPJ %s falharam: %s", npj, exc)
                saida[npj] = exc
    return saida
