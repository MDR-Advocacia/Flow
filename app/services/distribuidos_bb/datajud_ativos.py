"""Enriquecimento de capa via DataJud público — pré-cadastro do cliente Ativos.

A Ativos manda só a lista seca de números. Como ainda não estamos habilitados
nesses processos, a fonte automática confiável é o DataJud (grátis, por número):
traz classe, assuntos, órgão julgador, grau, tribunal, comarca (IBGE), data de
ajuizamento e movimentos. **Partes e valor da causa NÃO vêm do DataJud** — são a
única lacuna a preencher manualmente.

Reusa o roteamento CNJ→tribunal e a query por dígitos validados no projeto Lake
(`ingestion/connectors/datajud.py`): POST {base}/{alias}/_search, header
`Authorization: APIKey <chave>`, `match` em numeroProcesso (só dígitos).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

import requests

from app.core.config import settings

logger = logging.getLogger("distribuidos_bb.datajud_ativos")

# Chave pública do CNJ (publicada na Wiki) — fallback se a env vier vazia.
_PUBLIC_KEY = "cDZHYzlZa0JadVREZDJCendQbXY6SkJlTzNjLV9TRENyQk1RdnFKZGRQdw=="

# TR (2 díg.) → UF, para os aliases estaduais (api_publica_tj<uf>). Tabela CNJ.
UF_ESTADUAL = {
    "01": "ac", "02": "al", "03": "ap", "04": "am", "05": "ba", "06": "ce",
    "07": "df", "08": "es", "09": "go", "10": "ma", "11": "mt", "12": "ms",
    "13": "mg", "14": "pa", "15": "pb", "16": "pr", "17": "pe", "18": "pi",
    "19": "rj", "20": "rn", "21": "rs", "22": "ro", "23": "rr", "24": "sc",
    "25": "se", "26": "sp", "27": "to",
}


def apenas_digitos(cnj: Optional[str]) -> str:
    return re.sub(r"\D", "", cnj or "")


def formatar_cnj(d: str) -> str:
    if len(d) != 20:
        return d
    return f"{d[0:7]}-{d[7:9]}.{d[9:13]}.{d[13]}.{d[14:16]}.{d[16:20]}"


def alias_tribunal(d: str) -> Optional[str]:
    """Roteia o CNJ (segmento J + tribunal TR) pro alias do DataJud."""
    if len(d) != 20:
        return None
    j, tr = d[13], d[14:16]
    if j == "8":
        uf = UF_ESTADUAL.get(tr)
        return f"api_publica_tj{uf}" if uf else None
    if j == "4":
        return f"api_publica_trf{int(tr)}"
    if j == "5":
        return f"api_publica_trt{int(tr)}"
    if j == "6":
        return f"api_publica_tre-{UF_ESTADUAL.get(tr, tr)}"
    if j == "9":
        uf = UF_ESTADUAL.get(tr)
        return f"api_publica_tjm{uf}" if uf else None
    if j == "7":
        return "api_publica_stm"
    return None  # superiores (j 1/2/3) — raro


def _key() -> str:
    raw = (settings.datajud_api_key or _PUBLIC_KEY).strip()
    return raw if raw.lower().startswith("apikey") else f"APIKey {raw}"


def _search(alias: str, digits: str, tries: int = 4) -> list[dict[str, Any]]:
    url = f"{settings.datajud_base_url.rstrip('/')}/{alias}/_search"
    headers = {"Authorization": _key(), "Content-Type": "application/json"}
    body = {"query": {"match": {"numeroProcesso": digits}}, "size": 5}
    for i in range(tries):
        time.sleep(0.3)
        try:
            r = requests.post(url, headers=headers, json=body, timeout=settings.datajud_timeout_seconds)
        except requests.RequestException:
            if i == tries - 1:
                return []
            time.sleep(2 ** i)
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            if i < tries - 1:
                time.sleep((2 ** i) + 1)
                continue
            return []
        if r.status_code in (401, 403):
            logger.warning("DataJud recusou a chave (%s) no alias %s.", r.status_code, alias)
            return []
        if r.status_code != 200:
            return []  # alias inexistente / processo não indexado
        return r.json().get("hits", {}).get("hits", [])
    return []


def consultar_capa(cnj: str) -> Optional[dict[str, Any]]:
    """Capa enriquecida do processo ou None se não achou no DataJud.

    Campos: cnj, classe, assuntos[], assunto (1º), orgao_julgador, comarca_ibge,
    grau, tribunal, uf, data_ajuizamento, movimentos[] (data/codigo/nome).
    Partes e valor da causa NÃO vêm daqui.
    """
    d = apenas_digitos(cnj)
    if len(d) != 20:
        return None
    alias = alias_tribunal(d)
    if not alias:
        return None
    hits = _search(alias, d)
    if not hits:
        return None
    src = hits[0].get("_source", {}) or {}

    classe = src.get("classe")
    if isinstance(classe, dict):
        classe = classe.get("nome")
    assuntos = []
    for a in src.get("assuntos") or []:
        nome = a.get("nome") if isinstance(a, dict) else a
        if nome:
            assuntos.append(nome)
    orgao = src.get("orgaoJulgador")
    orgao_nome = orgao.get("nome") if isinstance(orgao, dict) else orgao
    if isinstance(orgao_nome, str):
        orgao_nome = orgao_nome.strip().strip('"').strip()
    ibge = orgao.get("codigoMunicipioIBGE") if isinstance(orgao, dict) else None
    movimentos = [
        {"data": m.get("dataHora"), "codigo": m.get("codigo"), "nome": m.get("nome")}
        for m in (src.get("movimentos") or [])
        if m.get("dataHora")
    ]
    movimentos.sort(key=lambda m: m["data"] or "", reverse=True)

    # UF só é confiável no estadual (J=8); trabalhista/federal usam região, não UF.
    uf = (UF_ESTADUAL.get(d[14:16]) or "").upper() if d[13] == "8" else None

    return {
        "cnj": formatar_cnj(d),
        "classe": classe,
        "assuntos": assuntos,
        "assunto": assuntos[0] if assuntos else None,
        "orgao_julgador": orgao_nome,
        "comarca_ibge": ibge,
        "grau": src.get("grau"),
        "tribunal": src.get("tribunal"),
        "uf": uf or None,
        "data_ajuizamento": _fmt_ajuiz(src.get("dataAjuizamento")),
        "movimentos": movimentos,
    }


def _fmt_ajuiz(v: Optional[str]) -> Optional[str]:
    """dataAjuizamento do DataJud ('20260625000000' ou ISO) → DD/MM/AAAA."""
    if not v:
        return None
    s = re.sub(r"\D", "", str(v))
    if len(s) >= 8:
        return f"{s[6:8]}/{s[4:6]}/{s[0:4]}"
    return str(v)
