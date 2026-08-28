"""Resolução do alias de tribunal do DataJud a partir do número CNJ.

O DataJud expõe um índice por tribunal (ex.: `api_publica_tjsp`,
`api_publica_trf1`). O alias é derivado dos campos J (segmento) e TR
(tribunal) do CNJ `NNNNNNN-DD.AAAA.J.TR.OOOO`:

- J = dígito 14 (index 13 em base 0)
- TR = dígitos 15-16 (index 14:16)

Validado empiricamente contra a carteira Banco Master (TJAP, TJRO, TJAC,
TJTO, TJPA, TJRR, TRF1, TJRJ, TJRS, TJBA, TJMG).
"""

import re

# TR -> UF para Justiça Estadual (J=8), Eleitoral (J=6) e Militar (J=9).
_UF_POR_TR = {
    "01": "ac", "02": "al", "03": "ap", "04": "am", "05": "ba", "06": "ce",
    "07": "df", "08": "es", "09": "go", "10": "ma", "11": "mt", "12": "ms",
    "13": "mg", "14": "pa", "15": "pb", "16": "pr", "17": "pe", "18": "pi",
    "19": "rj", "20": "rn", "21": "rs", "22": "ro", "23": "rr", "24": "sc",
    # 25 = Sergipe e 26 = Sao Paulo (estavam TROCADOS aqui). O `.8.26.` do
    # TJSP e' o codigo mais comum do pais, entao a inversao mandava todo CNJ
    # paulista pro indice do DataJud de Sergipe e vice-versa — a consulta
    # respondia vazio e o processo passava como "sem citacao". Conferido
    # contra a carteira: os 130+ CNJs com TR=25 sao todos de Sergipe
    # (Aracaju/SE, Estancia/SE, Lagarto/SE...).
    "25": "se", "26": "sp", "27": "to",
}


def cnj_digits(value: str | None) -> str | None:
    """Extrai só os dígitos do CNJ. None se vazio."""
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits or None


def uf_do_cnj(cnj: str | None) -> str | None:
    """UF (sigla maiúscula) derivada do CNJ — só pra Justiça Estadual."""
    digits = cnj_digits(cnj)
    if not digits or len(digits) != 20:
        return None
    if digits[13] != "8":
        return None
    uf = _UF_POR_TR.get(digits[14:16])
    return uf.upper() if uf else None


def resolve_tribunal_alias(cnj: str | None) -> str | None:
    """Devolve o alias do índice DataJud para o CNJ, ou None se não mapear.

    Cobre os segmentos da Justiça que aparecem na carteira:
    - J=8 Estadual  -> api_publica_tj{uf}
    - J=4 Federal   -> api_publica_trf{n}
    - J=5 Trabalho  -> api_publica_trt{n}
    - J=6 Eleitoral -> api_publica_tre-{uf}
    - J=9 Militar   -> api_publica_tjm{uf}
    - J=7 STM       -> api_publica_stm
    """
    digits = cnj_digits(cnj)
    if not digits or len(digits) != 20:
        return None

    segmento = digits[13]
    tr = digits[14:16]

    if segmento == "8":
        uf = _UF_POR_TR.get(tr)
        return f"api_publica_tj{uf}" if uf else None
    if segmento == "4":
        return f"api_publica_trf{int(tr)}"
    if segmento == "5":
        return f"api_publica_trt{int(tr)}"
    if segmento == "6":
        uf = _UF_POR_TR.get(tr)
        return f"api_publica_tre-{uf}" if uf else None
    if segmento == "9":
        uf = _UF_POR_TR.get(tr)
        return f"api_publica_tjm{uf}" if uf else None
    if segmento == "7":
        return "api_publica_stm"
    return None
