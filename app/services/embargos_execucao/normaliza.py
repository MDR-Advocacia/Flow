"""Normalizações do Fluxo Embargos à Execução: CNJ, pasta, NPJ, nomes e datas.

Tudo aqui é função pura — o relatório do L1, a planilha do legado, o DataJud,
o DJEN e o portal do BB escrevem o mesmo dado de jeitos diferentes, e o módulo
só funciona se todos convergirem para a mesma forma antes de comparar.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Optional

_NPJ_RE = re.compile(r"(\d{4})\s*/?\s*(\d{7})\s*-?\s*(\d{3})")

# Palavras que não identificam pessoa: preposições do nome e sufixos societários.
# "MARCIO HENRIQUE DOS SANTOS MOREIRA - ME" e "MARCIO HENRIQUE DOS SANTOS
# MOREIRA" são a mesma parte para efeito de vínculo com a execução.
_TOKENS_IGNORADOS = {
    "DA", "DE", "DI", "DO", "DU", "DAS", "DOS", "E",
    "ME", "EPP", "EIRELI", "LTDA", "SA", "S", "A", "MEI", "CIA",
}


def digitos(valor: Any) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def cnj_digitos(valor: Any) -> Optional[str]:
    d = digitos(valor)
    return d if len(d) == 20 else None


def formatar_cnj(valor: Any) -> Optional[str]:
    d = cnj_digitos(valor)
    if not d:
        return None
    return f"{d[:7]}-{d[7:9]}.{d[9:13]}.{d[13]}.{d[14:16]}.{d[16:]}"


def pasta_normalizada(valor: Any) -> Optional[str]:
    """'Proc - 0029647' como o L1 grava; colapsa espaços, nunca inventa máscara."""
    if valor is None:
        return None
    s = re.sub(r"\s+", " ", str(valor)).strip()
    return s or None


def npj_normalizado(valor: Any) -> Optional[str]:
    """'2025/0056987-000' — o sufixo é o desdobramento e é preservado."""
    if valor is None:
        return None
    m = _NPJ_RE.search(str(valor))
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2)}-{m.group(3)}"


def sem_acento(valor: Any) -> str:
    return (
        unicodedata.normalize("NFKD", str(valor or ""))
        .encode("ascii", "ignore")
        .decode()
    )


def nome_normalizado(valor: Any) -> str:
    s = sem_acento(valor).upper()
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens_nome(valor: Any) -> list[str]:
    return [t for t in nome_normalizado(valor).split() if t not in _TOKENS_IGNORADOS]


def nomes_batem(a: Any, b: Any) -> bool:
    """Mesma pessoa? Igualdade dos tokens significativos, ou o nome mais curto
    (com pelo menos 2 tokens) contido no mais longo — o diário abrevia e
    acrescenta sufixo societário, mas não troca sobrenome."""
    ta, tb = _tokens_nome(a), _tokens_nome(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    curto, longo = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(curto) < 2:
        return False
    return set(curto).issubset(set(longo))


def para_datetime(valor: Any) -> Optional[datetime]:
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        return valor
    if isinstance(valor, date):
        return datetime(valor.year, valor.month, valor.day)
    s = str(valor).strip()
    for fmt in (
        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
        "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
        "%Y%m%d%H%M%S", "%Y%m%d",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def para_data(valor: Any) -> Optional[date]:
    dt = para_datetime(valor)
    return dt.date() if dt else None
