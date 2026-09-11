"""DJEN (Comunica): confirma que o candidato é o embargo DA NOSSA execução.

O DataJud acha embargos na vara, mas não tem partes. A intimação publicada no
DJEN tem: "EMBARGANTE: <nome> ... EMBARGADO: BANCO DO BRASIL". Duas leituras:

  1. EMBARGADO: tem de ser o cliente da execução (Banco do Brasil; Banese/Ativos
     nas poucas execuções desses clientes). Embargado de outro credor — ex.: a
     cooperativa Sicredi na 1ª Vara Cível de Ji-Paraná, 11/09/2026 — descarta
     na hora, mesmo que o nome do embargante bata (decisão do operador).
  2. EMBARGANTE: tem de ser uma das partes demandadas (portal do BB) ou um dos
     executados das intimações da própria execução.

Na sonda de 11/09/2026, 4 de 5 pares tinham comunicação e todos bateram.
Do servidor da AWS a Comunica só responde com `DJEN_PROXY` (saída brasileira);
sem ele cai em `DjenIndisponivel` e o candidato fica A VERIFICAR.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from app.services.djen_publication_fallback import ComunicaClient, DjenIndisponivel
from app.services.embargos_execucao.normaliza import (
    cnj_digitos,
    nome_normalizado,
    nomes_batem,
)

logger = logging.getLogger(__name__)

DJEN_CONFIRMADO = "CONFIRMADO"            # embargante é parte da execução
DJEN_NAO_BATEU = "NAO_BATEU"              # há embargante e não é ninguém da execução
DJEN_EMBARGADO_OUTRO = "EMBARGADO_OUTRO"  # o embargado não é o cliente da execução
DJEN_SEM_COMUNICACAO = "SEM_COMUNICACAO"  # nada publicado ainda — tenta de novo depois
DJEN_SEM_REFERENCIA = "SEM_REFERENCIA"    # não temos nomes da execução pra comparar
DJEN_INDISPONIVEL = "INDISPONIVEL"        # 403/sem proxy/erro — tenta de novo depois

# Fim do nome do rótulo: o próximo rótulo processual, o advogado ou o corpo do ato.
_FIM = (
    r"(?=\s*(?:Advogad|ADVOGAD|Procurador|PROCURADOR|EMBARGAD|EMBARGANT|EXEQUENT|"
    r"EXECUTAD|REQUERID|REQUERENT|Represent|REPRESENT|OAB|Processo|PROCESSO|Classe|"
    r"CLASSE|DECIS|DESPACHO|SENTEN|ATO ORDIN|Assunto|ASSUNTO|INTIMA|Intima|"
    r"FINALIDADE|Finalidade|Endere|ENDERE|CPF|CNPJ|\[|$))"
)
_ROTULO = {
    "embargante": re.compile(r"EMBARGANTES?\s*:\s*(.+?)" + _FIM, re.S),
    "embargado": re.compile(r"EMBARGAD[OA]S?\s*:\s*(.+?)" + _FIM, re.S),
    "executado": re.compile(r"EXECUTAD[OA]S?\s*:\s*(.+?)" + _FIM, re.S),
}
_BB = re.compile(r"BANCO\s+DO\s+BRASIL", re.I)

# Quem deve figurar como EMBARGADO, pelo cliente da execução.
CLIENTE_EMBARGADO = {
    "BB": re.compile(r"BANCO\s+DO\s+BRASIL", re.I),
    "BANESE": re.compile(r"BANESE|BANCO\s+DO\s+ESTADO\s+DE\s+SERGIPE", re.I),
    "ATIVOS": re.compile(r"ATIVOS\s+S", re.I),
}

# "ADVOGADO DO EMBARGANTE: fulano" / "Advogado do(a) EMBARGANTE: fulano" é o
# advogado, não a parte.
_PRECEDIDO_DE_ADVOGADO = re.compile(
    r"(?:advogad\w*|procurador\w*)\s*(?:\(a\)\s*)?(?:d[oa]s?\s*(?:\(a\)\s*)?)?$", re.I
)


def padrao_cliente(cliente: Optional[str]) -> re.Pattern:
    return CLIENTE_EMBARGADO.get((cliente or "BB").upper(), CLIENTE_EMBARGADO["BB"])


def _separar_nomes(bloco: str) -> list[str]:
    nomes = []
    for pedaco in re.split(r"[,;]", bloco or ""):
        p = re.sub(r"\s+", " ", pedaco).strip(" .-")
        if len(nome_normalizado(p)) >= 5:
            nomes.append(p)
    return nomes


def extrair_rotulo(texto: str, rotulo: str) -> list[str]:
    padrao = _ROTULO[rotulo]
    saida: list[str] = []
    texto = texto or ""
    for m in padrao.finditer(texto):
        if _PRECEDIDO_DE_ADVOGADO.search(texto[max(0, m.start() - 25): m.start()]):
            continue
        for nome in _separar_nomes(m.group(1)[:400]):
            if nome not in saida:
                saida.append(nome)
    return saida


def _destinatarios_partes(item: dict[str, Any], cliente: re.Pattern) -> list[str]:
    """Destinatários que não são advogado nem o cliente."""
    advogados = {
        nome_normalizado((a.get("advogado") or {}).get("nome"))
        for a in (item.get("destinatarioadvogados") or [])
    }
    saida = []
    for d in item.get("destinatarios") or []:
        nome = d.get("nome") or ""
        if not nome or _BB.search(nome) or cliente.search(nome) or nome_normalizado(nome) in advogados:
            continue
        saida.append(nome)
    return saida


@dataclass
class ResultadoDjen:
    status: str
    embargantes: list[str] = field(default_factory=list)
    embargados: list[str] = field(default_factory=list)
    nomes_casados: list[str] = field(default_factory=list)
    embargado_cliente: bool = False
    trecho: Optional[str] = None
    data_comunicacao: Optional[str] = None
    erro: Optional[str] = None


def comunicacoes(cnj: str, client: Optional[ComunicaClient] = None) -> list[dict[str, Any]]:
    d = cnj_digitos(cnj)
    if not d:
        return []
    client = client or ComunicaClient()
    corpo = client._get({"numeroProcesso": d, "itensPorPagina": 50, "pagina": 1})
    return list(corpo.get("items") or [])


def executados_da_execucao(cnj_execucao: str, client: Optional[ComunicaClient] = None,
                           cliente: Optional[str] = "BB") -> list[str]:
    """Nomes dos executados nas intimações da própria execução (referência
    quando o portal do BB não devolveu as partes)."""
    padrao = padrao_cliente(cliente)
    nomes: list[str] = []
    for item in comunicacoes(cnj_execucao, client):
        texto = item.get("texto") or ""
        for nome in extrair_rotulo(texto, "executado") or _destinatarios_partes(item, padrao):
            if not _BB.search(nome) and not padrao.search(nome) and nome not in nomes:
                nomes.append(nome)
    return nomes


def _trecho(texto: str, marca: str) -> str:
    i = texto.upper().find(marca)
    i = max(i, 0)
    return re.sub(r"\s+", " ", texto[max(0, i - 120): i + 380]).strip()


def avaliar_comunicacoes(
    itens: list[dict[str, Any]],
    nomes_referencia: Iterable[str],
    cliente: Optional[str] = "BB",
) -> ResultadoDjen:
    """Decide com as comunicações já baixadas (função pura, testável)."""
    referencia = [n for n in nomes_referencia if n]
    padrao = padrao_cliente(cliente)
    if not itens:
        return ResultadoDjen(status=DJEN_SEM_COMUNICACAO)

    embargantes: list[str] = []
    embargados: list[str] = []
    trecho = data = None
    for item in itens:
        texto = item.get("texto") or ""
        achados = extrair_rotulo(texto, "embargante")
        if achados and trecho is None:
            trecho = _trecho(texto, "EMBARGANT")
            data = item.get("data_disponibilizacao")
        for nome in achados:
            if nome not in embargantes:
                embargantes.append(nome)
        for nome in extrair_rotulo(texto, "embargado"):
            if nome not in embargados:
                embargados.append(nome)
        if embargados and trecho is None:
            trecho = _trecho(texto, "EMBARGAD")
            data = item.get("data_disponibilizacao")

    embargado_cliente = any(padrao.search(n) for n in embargados)
    if embargados and not embargado_cliente:
        return ResultadoDjen(
            status=DJEN_EMBARGADO_OUTRO, embargantes=embargantes, embargados=embargados,
            trecho=trecho, data_comunicacao=data,
        )

    if not embargantes:
        # Texto sem rótulo (despacho curto): cai nos destinatários que não são
        # advogado nem o cliente.
        for item in itens:
            for nome in _destinatarios_partes(item, padrao):
                if nome not in embargantes:
                    embargantes.append(nome)
        if trecho is None and itens:
            trecho = re.sub(r"\s+", " ", (itens[0].get("texto") or "")[:500]).strip() or None
            data = itens[0].get("data_disponibilizacao")

    base = dict(embargantes=embargantes, embargados=embargados, embargado_cliente=embargado_cliente,
                trecho=trecho, data_comunicacao=data)
    if not embargantes:
        return ResultadoDjen(status=DJEN_SEM_COMUNICACAO, **base)
    if not referencia:
        return ResultadoDjen(status=DJEN_SEM_REFERENCIA, **base)

    casados = [e for e in embargantes if any(nomes_batem(e, r) for r in referencia)]
    return ResultadoDjen(status=DJEN_CONFIRMADO if casados else DJEN_NAO_BATEU, nomes_casados=casados, **base)


def confirmar_candidato(
    cnj_candidato: str,
    nomes_referencia: Iterable[str],
    client: Optional[ComunicaClient] = None,
    cliente: Optional[str] = "BB",
) -> ResultadoDjen:
    try:
        itens = comunicacoes(cnj_candidato, client)
    except DjenIndisponivel as exc:
        return ResultadoDjen(status=DJEN_INDISPONIVEL, erro=str(exc)[:300])
    except Exception as exc:  # noqa: BLE001 — DJEN nunca derruba o monitor
        logger.warning("Embargos: DJEN falhou para %s: %s", cnj_candidato, exc)
        return ResultadoDjen(status=DJEN_INDISPONIVEL, erro=str(exc)[:300])
    return avaliar_comunicacoes(itens, nomes_referencia, cliente)
