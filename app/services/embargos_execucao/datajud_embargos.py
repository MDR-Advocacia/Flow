"""DataJud: capa da execução e busca dos embargos candidatos na mesma vara.

VALIDADO em 11/09/2026 com 22 pares reais (pasta da execução × incidente /001
cadastrado à mão): em 20 os embargos estavam no índice do tribunal com classe
172, MESMO `orgaoJulgador.codigo` da execução e `dataAjuizamento` posterior;
1 par era apelação e 1 ainda não estava indexado (lag). Candidatos por vara
desde a data da execução: de 1 a 56.

O DataJud não traz partes — por isso esta etapa só ACHA candidatos. Quem diz
qual é o nosso é o DJEN (djen_embargos) e, na falta dele, os sinais daqui:
distribuição "por dependência" e petição na execução no mesmo dia.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional

import httpx

from app.services.citacoes_bm.datajud import get_client
from app.services.citacoes_bm.tribunal_alias import resolve_tribunal_alias
from app.services.embargos_execucao.normaliza import cnj_digitos, para_datetime

logger = logging.getLogger(__name__)

CLASSE_EMBARGOS_EXECUCAO = 172
_COD_DISTRIBUICAO = 26
_COD_PETICAO = 85

_MAX_TENTATIVAS = 4
_BACKOFF = 1.5
_PAGINA = 100


@dataclass
class CapaExecucao:
    alias: str
    orgao_codigo: Optional[int]
    orgao_nome: Optional[str]
    classe_codigo: Optional[int]
    classe_nome: Optional[str]
    grau: Optional[str]
    data_ajuizamento: Optional[datetime]
    # Valor cru do índice: cada tribunal grava num formato e o range da busca
    # de candidatos casa com o formato do próprio tribunal.
    data_ajuizamento_raw: Optional[str]
    movimentos: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CandidatoDataJud:
    cnj_digitos: str
    data_ajuizamento: Optional[datetime]
    classe_codigo: Optional[int]
    classe_nome: Optional[str]
    orgao_nome: Optional[str]
    distribuicao_dependencia: bool


def _buscar(alias: str, payload: dict[str, Any], client=None) -> dict[str, Any]:
    client = client or get_client()
    ultimo: Optional[Exception] = None
    for tentativa in range(_MAX_TENTATIVAS):
        try:
            return client.search_processes(tribunal_alias=alias, payload=payload)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status != 429 and not (status and 500 <= status < 600):
                raise
            ultimo = exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            ultimo = exc
        if tentativa < _MAX_TENTATIVAS - 1:
            time.sleep(_BACKOFF * (2 ** tentativa))
    raise ultimo or RuntimeError("DataJud: falha sem exceção registrada.")


def _codigo(no: Any) -> Optional[int]:
    if isinstance(no, dict):
        try:
            return int(no.get("codigo"))
        except (TypeError, ValueError):
            return None
    return None


def _nome(no: Any) -> Optional[str]:
    return no.get("nome") if isinstance(no, dict) else None


def consultar_capa(cnj: str, client=None) -> Optional[CapaExecucao]:
    """Capa da execução no índice do tribunal. None = SEM_HITS (lag ou número
    fora do DataJud). Erro de transporte sobe — o chamador registra e re-tenta."""
    d = cnj_digitos(cnj)
    alias = resolve_tribunal_alias(d)
    if not d or not alias:
        raise ValueError(f"CNJ sem tribunal mapeável no DataJud: {cnj!r}")
    resp = _buscar(alias, {"query": {"match": {"numeroProcesso": d}}, "size": 10}, client)
    hits = [h.get("_source") or {} for h in (resp.get("hits", {}).get("hits") or [])]
    if not hits:
        return None
    # A execução é de 1º grau; um recurso indexa o mesmo número no G2.
    src = next((h for h in hits if h.get("grau") == "G1"), hits[0])
    raw = src.get("dataAjuizamento")
    return CapaExecucao(
        alias=alias,
        orgao_codigo=_codigo(src.get("orgaoJulgador")),
        orgao_nome=_nome(src.get("orgaoJulgador")),
        classe_codigo=_codigo(src.get("classe")),
        classe_nome=_nome(src.get("classe")),
        grau=src.get("grau"),
        data_ajuizamento=para_datetime(raw),
        data_ajuizamento_raw=str(raw) if raw else None,
        movimentos=list(src.get("movimentos") or []),
    )


def _foi_por_dependencia(movimentos: list[dict[str, Any]]) -> bool:
    for mov in movimentos or []:
        if mov.get("codigo") != _COD_DISTRIBUICAO:
            continue
        for comp in mov.get("complementosTabelados") or []:
            texto = f"{comp.get('nome') or ''} {comp.get('descricao') or ''} {comp.get('valor') or ''}"
            if "depend" in texto.lower():
                return True
    return False


def buscar_candidatos(
    capa: CapaExecucao,
    *,
    cnj_execucao: str,
    desde_raw: Optional[str] = None,
    classes: tuple[int, ...] = (CLASSE_EMBARGOS_EXECUCAO,),
    limite: int = 500,
    client=None,
) -> list[CandidatoDataJud]:
    """Embargos da mesma vara ajuizados a partir da data da execução.

    `desde_raw` usa o formato gravado pelo próprio tribunal (vem da capa)."""
    if capa.orgao_codigo is None:
        raise ValueError("Capa da execução sem código de órgão julgador.")
    desde = desde_raw or capa.data_ajuizamento_raw
    must: list[dict[str, Any]] = [
        {"match": {"orgaoJulgador.codigo": capa.orgao_codigo}},
    ]
    if len(classes) == 1:
        must.append({"match": {"classe.codigo": classes[0]}})
    else:
        must.append({"bool": {"should": [{"match": {"classe.codigo": c}} for c in classes],
                              "minimum_should_match": 1}})
    if desde:
        must.append({"range": {"dataAjuizamento": {"gte": desde}}})

    proprio = cnj_digitos(cnj_execucao)
    vistos: set[str] = set()
    saida: list[CandidatoDataJud] = []
    inicio = 0
    while inicio < limite:
        resp = _buscar(capa.alias, {
            "query": {"bool": {"must": must}},
            "size": _PAGINA,
            "from": inicio,
            "_source": ["numeroProcesso", "dataAjuizamento", "classe", "orgaoJulgador",
                        "grau", "movimentos"],
        }, client)
        hits = resp.get("hits", {}).get("hits") or []
        for h in hits:
            src = h.get("_source") or {}
            num = cnj_digitos(src.get("numeroProcesso"))
            if not num or num == proprio or num in vistos:
                continue
            vistos.add(num)
            saida.append(CandidatoDataJud(
                cnj_digitos=num,
                data_ajuizamento=para_datetime(src.get("dataAjuizamento")),
                classe_codigo=_codigo(src.get("classe")),
                classe_nome=_nome(src.get("classe")),
                orgao_nome=_nome(src.get("orgaoJulgador")),
                distribuicao_dependencia=_foi_por_dependencia(src.get("movimentos") or []),
            ))
        if len(hits) < _PAGINA:
            break
        inicio += _PAGINA
    return saida


def peticao_na_execucao_no_dia(capa: CapaExecucao, dia: Optional[date]) -> bool:
    """A execução recebeu petição no dia (ou no seguinte) em que o candidato foi
    distribuído? Na amostra, 3 de 5 embargos deixaram esse rastro."""
    if dia is None:
        return False
    janela = {dia, dia + timedelta(days=1)}
    for mov in capa.movimentos or []:
        quando = para_datetime(str(mov.get("dataHora") or "")[:19])
        if not quando or quando.date() not in janela:
            continue
        nome = (mov.get("nome") or "").lower()
        if mov.get("codigo") == _COD_PETICAO or "peti" in nome or "juntada" in nome:
            return True
    return False
