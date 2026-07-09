"""Normalização de dados capturados do portal BB (sem termos alienígenas).

Concentra as regras determinísticas que o `gerar_planilha.py` legado fazia
espalhado: mapa polo→posição, parsing de valor da causa, fingerprint de
dedup, limpeza de data de ajuizamento.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional

# Polo capturado no portal → posição processual (rótulo do operador)
MAPA_POLO_POSICAO = {
    "passivo": "Réu",
    "ativo": "Autor",
    "neutro": "Interessado",
}

# CNJ vazio/placeholder que o portal devolve quando ainda não há número
CNJ_PLACEHOLDER = "0000000-00.0000.0.00.0000"
_CNJ_REGEX = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")


def polo_para_posicao(polo: Optional[str]) -> Optional[str]:
    """'Passivo' → 'Réu', 'Ativo' → 'Autor', 'Neutro' → 'Interessado'."""
    if not polo:
        return None
    return MAPA_POLO_POSICAO.get(polo.strip().lower())


def normalizar_cnj(valor: Optional[str]) -> Optional[str]:
    """Extrai o CNJ no formato canônico; None se ausente/placeholder."""
    if not valor:
        return None
    texto = str(valor).strip()
    if not texto or "cadastr" in texto.lower():
        return None
    achado = _CNJ_REGEX.search(texto)
    if not achado:
        return None
    cnj = achado.group(0)
    if cnj == CNJ_PLACEHOLDER:
        return None
    return cnj


def limpar_data_ajuizamento(valor: Optional[str]) -> Optional[str]:
    """Descarta 'A cadastrar'/vazio; devolve a data como veio caso válida."""
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto or "cadastr" in texto.lower():
        return None
    return texto


def parse_valor_causa(valor: Optional[str]) -> Optional[Decimal]:
    """'R$ 1.234,56' → Decimal('1234.56'); None quando não numérico."""
    if valor is None:
        return None
    texto = str(valor).replace("R$", "").strip()
    if not texto:
        return None
    # Formato pt-BR: milhar com ponto, decimal com vírgula
    texto = texto.replace(".", "").replace(",", ".")
    try:
        return Decimal(texto)
    except (InvalidOperation, ValueError):
        return None


def fingerprint(cnj: Optional[str], npj: Optional[str]) -> str:
    """Chave de dedup: CNJ quando existe, senão NPJ, senão vazio marcado."""
    if cnj:
        return f"cnj:{cnj}"
    if npj:
        return f"npj:{npj.strip()}"
    return "sem-identidade"


def apenas_digitos(valor: Optional[str]) -> Optional[str]:
    """Mantém só dígitos (CPF/CNPJ); None se sobrar vazio."""
    if not valor:
        return None
    digitos = re.sub(r"\D", "", str(valor))
    return digitos or None


def tipo_pessoa_por_documento(cpf_cnpj: Optional[str]) -> Optional[str]:
    """11 dígitos → PF, 14 → PJ, senão None."""
    digitos = apenas_digitos(cpf_cnpj)
    if not digitos:
        return None
    if len(digitos) == 11:
        return "PF"
    if len(digitos) == 14:
        return "PJ"
    return None
