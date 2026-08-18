"""
Schema Pydantic para validar a resposta JSON do classificador de
publicações e capturar inconsistências semânticas que `repair_classification`
e `validate_classification` não pegam (eles cuidam só do par
categoria/subcategoria contra a taxonomia).

O que ESTE módulo valida:
  - Cross-field: `audiencia_data`/`audiencia_hora`/`audiencia_link` só
    podem vir preenchidos quando categoria e' de audiencia ("Audiências" v2
    ou "Audiência Agendada" v1 legacy).
  - `prazo_dias` exige `prazo_tipo` e `prazo_fundamentacao` preenchidos.
  - `prazo_dias` ausente exige `prazo_tipo` e `prazo_fundamentacao` null.
  - `confianca` ∈ {alta, media, baixa}.
  - `polo` ∈ {ativo, passivo, ambos}.
  - Datas em ISO `YYYY-MM-DD` e horas em `HH:MM`.

Quando inconsistente: o validador NÃO levanta — limpa os campos
incoerentes (ex.: zera `audiencia_data` se categoria não é "Audiência
Agendada"), e devolve uma lista de `warnings` que o caller pode logar.
Esse modo "permissivo" foi escolhido porque queremos APROVEITAR a
classificação principal (categoria/subcategoria) mesmo quando a IA
alucinou um campo extra — só sanitizamos o lixo antes de gravar.

Quando o erro é estrutural (categoria ausente, payload não-dict),
levanta `ResponseSchemaError`.

Uso típico no orquestrador:

    from app.services.classifier.response_schema import (
        validate_response,
        ResponseSchemaError,
    )

    try:
        clean = validate_response(result)
    except ResponseSchemaError as exc:
        # marca pra revisão humana; não persiste classificação
        ...
    else:
        if clean.warnings:
            logger.warning("Schema warnings #%s: %s", rec.id, clean.warnings)
        rec.category = clean.categoria
        rec.subcategory = clean.subcategoria
        # ... (campos limpos pelo validator, sem alucinações cruzadas)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


class ResponseSchemaError(ValueError):
    """Erro estrutural na resposta — não dá pra aproveitar nem a categoria."""


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")
_VALID_POLOS = {"ativo", "passivo", "ambos"}
_VALID_CONFIANCA = {"alta", "media", "baixa"}
_VALID_PRAZO_TIPO = {"util", "corrido"}
# Categorias que ATIVAM extracao de audiencia (data/hora/link).
# Aceita v2 ("Audiências" — atual) e v1 ("Audiência Agendada" —
# preservado pra back-compat com publicacoes ja classificadas em v1).
# Bug raiz observado em 2026-05-08: validator hardcoded apenas com v1,
# descartava data/hora/link de toda audiencia classificada em v2 com
# warning "categoria != Audiência Agendada".
_AUDIENCIA_CATEGORIES = {"Audiências", "Audiência Agendada"}
# Sistemas que costumam aparecer em publicacoes de pesquisa patrimonial
# e bloqueio. Campo opcional `sistema_mencionado` (taxonomy v2): IA
# preenche quando o texto cita um deles. Mantido como enum fechado pra
# evitar inflar a arvore principal com uma cat por sistema.
_VALID_SISTEMAS = {"SISBAJUD", "RENAJUD", "INFOJUD", "SNIPER", "CCS", "CNIB", "OUTRO"}

# pub010 — de quem é o ato. O `juizo` aparece SEPARADO em dois porque medir
# ensinou: como balde único ele era 44% do volume com 35% de pureza (moeda ao
# ar), e tê-lo tratado como descartável derrubou a primeira versão da regra
# (falso-ignorar de 46,6%). A separação é hipótese ainda não validada —
# nenhuma regra a usa hoje; ela existe pra o próximo lote poder medi-la.
_VALID_QUEM_PRATICA = {
    "nos",               # nós temos que fazer algo
    "parte_adversa",     # quem tem que agir é a parte contrária
    "juizo_expediente",  # conclusos / mero expediente — ninguém age agora
    "juizo_determina",   # o juízo determinou algo que alguém deve cumprir
    "indeterminado",     # o texto não permite dizer
}


@dataclass
class CleanClassification:
    """Resultado validado e sanitizado pronto pra persistir."""

    categoria: str
    subcategoria: str
    polo: Optional[str]
    audiencia_data: Optional[str]
    audiencia_hora: Optional[str]
    audiencia_link: Optional[str]
    prazo_dias: Optional[int]
    prazo_tipo: Optional[str]
    prazo_fundamentacao: Optional[str]
    confianca: Optional[str]
    justificativa: str
    natureza_processo: Optional[str]
    # Campo opcional da taxonomy v2. Indica qual sistema de pesquisa/
    # bloqueio patrimonial o texto cita (SISBAJUD, RENAJUD, INFOJUD,
    # SNIPER, CCS, CNIB, OUTRO). Null quando nao aplicavel ou ausente.
    sistema_mencionado: Optional[str] = None
    # pub010 — de quem é o ato. Campo separado da `categoria` de propósito:
    # a categoria diz o ASSUNTO, este diz QUEM AGE, e a medição mostrou que
    # 83% dos erros do shadow vinham de nunca perguntarmos o segundo.
    quem_pratica_ato: Optional[str] = None
    exige_providencia_nossa: Optional[bool] = None
    warnings: list[str] = field(default_factory=list)


def _coerce_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        return s or None
    return str(value).strip() or None


def _coerce_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        # bool é subclass de int em Python; trata como int normal seria
        # surpresa pra IA que retorna `false` ao invés de null.
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def validate_response(payload: Any) -> CleanClassification:
    """
    Valida e sanitiza a resposta da IA. Devolve CleanClassification.

    Levanta ResponseSchemaError em casos irrecuperáveis:
      - payload não é dict
      - sem `categoria` (mesmo após coerção)

    Caso contrário, qualquer inconsistência cross-field vira warning e
    o campo problemático é zerado (ex.: audiencia_data limpa se
    categoria nao for de audiencia).
    """
    if not isinstance(payload, dict):
        raise ResponseSchemaError(
            f"payload não é objeto JSON ({type(payload).__name__})"
        )

    warnings: list[str] = []

    categoria = _coerce_str(payload.get("categoria"))
    if not categoria:
        raise ResponseSchemaError("campo 'categoria' ausente ou vazio")

    subcategoria = _coerce_str(payload.get("subcategoria")) or "-"
    polo_raw = (_coerce_str(payload.get("polo")) or "").lower()
    polo: Optional[str]
    if polo_raw in _VALID_POLOS:
        polo = polo_raw
    else:
        if polo_raw:
            warnings.append(f"polo inválido '{polo_raw}' — descartado")
        polo = None

    audiencia_data = _coerce_str(payload.get("audiencia_data"))
    audiencia_hora = _coerce_str(payload.get("audiencia_hora"))
    audiencia_link = _coerce_str(payload.get("audiencia_link"))

    # Cross-field: campos de audiência só fazem sentido na categoria certa.
    is_audiencia = categoria in _AUDIENCIA_CATEGORIES
    if not is_audiencia:
        if audiencia_data:
            warnings.append(
                f"audiencia_data='{audiencia_data}' descartada "
                f"(categoria='{categoria}' nao e categoria de audiencia)"
            )
            audiencia_data = None
        if audiencia_hora:
            warnings.append(
                f"audiencia_hora='{audiencia_hora}' descartada "
                f"(categoria='{categoria}' nao e categoria de audiencia)"
            )
            audiencia_hora = None
        if audiencia_link:
            warnings.append(
                f"audiencia_link descartado "
                f"(categoria='{categoria}' nao e categoria de audiencia)"
            )
            audiencia_link = None

    # Formato de data/hora — só aceita ISO; senão zera com warning.
    if audiencia_data and not _DATE_RE.match(audiencia_data):
        warnings.append(
            f"audiencia_data='{audiencia_data}' fora do padrão YYYY-MM-DD — zerada"
        )
        audiencia_data = None
    if audiencia_hora and not _TIME_RE.match(audiencia_hora):
        warnings.append(
            f"audiencia_hora='{audiencia_hora}' fora do padrão HH:MM — zerada"
        )
        audiencia_hora = None

    # Prazo — trio audiência atrelado.
    prazo_dias = _coerce_int(payload.get("prazo_dias"))
    prazo_tipo = (_coerce_str(payload.get("prazo_tipo")) or "").lower() or None
    prazo_fundamentacao = _coerce_str(payload.get("prazo_fundamentacao"))

    if prazo_dias is not None:
        if prazo_dias <= 0 or prazo_dias > 365:
            warnings.append(
                f"prazo_dias={prazo_dias} fora do range 1..365 — zerado todo o trio"
            )
            prazo_dias = None
            prazo_tipo = None
            prazo_fundamentacao = None
        else:
            if prazo_tipo not in _VALID_PRAZO_TIPO:
                if prazo_tipo:
                    warnings.append(
                        f"prazo_tipo='{prazo_tipo}' inválido — assumindo 'util'"
                    )
                else:
                    warnings.append(
                        "prazo_tipo ausente com prazo_dias preenchido — assumindo 'util'"
                    )
                prazo_tipo = "util"
            if not prazo_fundamentacao:
                warnings.append(
                    "prazo_fundamentacao ausente com prazo_dias preenchido — operador deverá completar"
                )
    else:
        if prazo_tipo:
            warnings.append(
                f"prazo_tipo='{prazo_tipo}' descartado (prazo_dias é null)"
            )
            prazo_tipo = None
        if prazo_fundamentacao:
            warnings.append(
                "prazo_fundamentacao descartada (prazo_dias é null)"
            )
            prazo_fundamentacao = None

    confianca = (_coerce_str(payload.get("confianca")) or "").lower() or None
    if confianca and confianca not in _VALID_CONFIANCA:
        warnings.append(
            f"confianca='{confianca}' inválida — assumindo 'baixa'"
        )
        confianca = "baixa"

    justificativa = _coerce_str(payload.get("justificativa")) or ""

    natureza_processo = _coerce_str(payload.get("natureza_processo"))

    # sistema_mencionado (taxonomy v2): aceita uppercase fechado.
    # Tolerante a variacoes (lowercase, com acento) — normaliza pra
    # uppercase e descarta com warning se nao bater no enum.
    sistema_raw = _coerce_str(payload.get("sistema_mencionado"))
    sistema_mencionado: Optional[str]
    if sistema_raw:
        candidate = sistema_raw.upper().strip()
        if candidate in _VALID_SISTEMAS:
            sistema_mencionado = candidate
        else:
            warnings.append(
                f"sistema_mencionado='{sistema_raw}' fora do enum — descartado"
            )
            sistema_mencionado = None
    else:
        sistema_mencionado = None

    # pub010 — quem pratica o ato. Fechado no enum: valor fora dele vira None
    # e a `r6` não dispara. Preferir NÃO decidir a decidir com vocabulário que
    # não medimos — a regra só ignora publicação, e ignorar errado perde prazo.
    quem_raw = (_coerce_str(payload.get("quem_pratica_ato")) or "").lower()
    quem_pratica_ato: Optional[str]
    if quem_raw in _VALID_QUEM_PRATICA:
        quem_pratica_ato = quem_raw
    else:
        if quem_raw:
            warnings.append(
                f"quem_pratica_ato='{quem_raw}' fora do enum — descartado"
            )
        quem_pratica_ato = None

    # Só aceita booleano de verdade. String "true"/"sim" não é coagida: um
    # false inventado por coerção frouxa mandaria a publicação pro descarte.
    exige_raw = payload.get("exige_providencia_nossa")
    exige_providencia_nossa: Optional[bool]
    if isinstance(exige_raw, bool):
        exige_providencia_nossa = exige_raw
    else:
        if exige_raw is not None:
            warnings.append(
                f"exige_providencia_nossa não-booleano ({exige_raw!r}) — descartado"
            )
        exige_providencia_nossa = None

    return CleanClassification(
        categoria=categoria,
        subcategoria=subcategoria,
        polo=polo,
        audiencia_data=audiencia_data,
        audiencia_hora=audiencia_hora,
        audiencia_link=audiencia_link,
        prazo_dias=prazo_dias,
        prazo_tipo=prazo_tipo,
        prazo_fundamentacao=prazo_fundamentacao,
        confianca=confianca,
        justificativa=justificativa,
        natureza_processo=natureza_processo,
        sistema_mencionado=sistema_mencionado,
        quem_pratica_ato=quem_pratica_ato,
        exige_providencia_nossa=exige_providencia_nossa,
        warnings=warnings,
    )
