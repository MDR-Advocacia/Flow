"""Vencimento ESTIMADO do prazo de uma publicação (pub012).

O que é — e o que não é
-----------------------
Estimativa de TRIAGEM, pra régua de envelhecimento da tela de publicações
ordenar e alertar pelo que vence primeiro. NÃO é contagem oficial de prazo:
usa só feriado nacional (mesma régua do calculador dos Prazos Iniciais),
não conhece suspensão nem feriado local. A decisão jurídica continua com o
operador.

De onde vem o número
--------------------
Dos defaults da taxonomia de classificação (`classification_categories` /
`classification_subcategories`, campos `default_prazo_dias` e
`default_prazo_tipo`): o da SUBCATEGORIA vence o da categoria. Categoria sem
default => None (SEM_PRAZO) — e em 31/08/2026 TODOS os defaults estavam
vazios em produção, então a régua nasce apagada e acende conforme o operador
preencher a taxonomia. Depois de preencher, rode
`scripts/backfill_prazo_estimado.py` pra recalcular o estoque.

O termo inicial é a `publication_date` (data da publicação no tribunal, não a
captura) e a conta delega pro `calcular_prazo_seguro` dos Prazos Iniciais —
CPC art. 224: exclui o dia da publicação, dias úteis pulam fim de semana e
feriado nacional, vencimento em dia sem expediente prorroga.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.classification_taxonomy import (
    ClassificationCategory,
    ClassificationSubcategory,
)
from app.services.prazos_iniciais.prazo_calculator import calcular_prazo_seguro

logger = logging.getLogger(__name__)

_TZ_BR = ZoneInfo("America/Sao_Paulo")


def hoje_brt() -> date:
    """Data de hoje no fuso da operação (regra da casa: dia é dia em BRT)."""
    return datetime.now(_TZ_BR).date()


def estado_prazo(prazo: Optional[date], hoje: Optional[date] = None) -> str:
    """Classifica um vencimento estimado: VENCIDA / VENCE_HOJE / NO_PRAZO / SEM_PRAZO."""
    if prazo is None:
        return "SEM_PRAZO"
    hoje = hoje or hoje_brt()
    if prazo < hoje:
        return "VENCIDA"
    if prazo == hoje:
        return "VENCE_HOJE"
    return "NO_PRAZO"


def _norm(nome: Optional[str]) -> str:
    return (nome or "").strip().lower()


class PrazoEstimadoResolver:
    """Resolve (categoria, subcategoria) -> vencimento estimado.

    Carrega a taxonomia UMA vez por sessão do SQLAlchemy (cache em
    ``db.info``) — a classificação em lote passa por milhares de registros e
    não pode pagar duas queries por registro.
    """

    def __init__(self, db: Session) -> None:
        self._cat: dict[str, tuple[int, str]] = {}
        self._sub: dict[tuple[str, str], tuple[int, str]] = {}

        for cat in db.query(ClassificationCategory).all():
            if cat.default_prazo_dias:
                self._cat[_norm(cat.name)] = (
                    cat.default_prazo_dias,
                    cat.default_prazo_tipo or "util",
                )
        linhas = (
            db.query(ClassificationSubcategory, ClassificationCategory.name)
            .join(
                ClassificationCategory,
                ClassificationCategory.id == ClassificationSubcategory.category_id,
            )
            .all()
        )
        for sub, cat_name in linhas:
            if sub.default_prazo_dias:
                self._sub[(_norm(cat_name), _norm(sub.name))] = (
                    sub.default_prazo_dias,
                    sub.default_prazo_tipo or "util",
                )

    @classmethod
    def for_session(cls, db: Session) -> "PrazoEstimadoResolver":
        inst = db.info.get("_prazo_estimado_resolver")
        if inst is None:
            inst = cls(db)
            db.info["_prazo_estimado_resolver"] = inst
        return inst

    def calcular(
        self,
        publication_date: Optional[str],
        category: Optional[str],
        subcategory: Optional[str],
    ) -> Optional[date]:
        """Vencimento estimado, ou None quando não há default/data utilizável."""
        if not publication_date or not category:
            return None
        regra = self._sub.get((_norm(category), _norm(subcategory)))
        if regra is None:
            regra = self._cat.get(_norm(category))
        if regra is None:
            return None
        try:
            base = date.fromisoformat(str(publication_date)[:10])
        except (ValueError, TypeError):
            return None
        return calcular_prazo_seguro(base, regra[0], regra[1])


def atualizar_prazo_estimado(db: Session, record: Any) -> None:
    """Recalcula ``record.prazo_estimado`` a partir da classificação atual.

    Chamar SEMPRE que category/subcategory mudarem (classificação em lote,
    reclassificação manual, propagação pra irmãs). Nunca levanta: prazo
    estimado é acessório — um erro aqui não pode derrubar a classificação.
    """
    try:
        record.prazo_estimado = PrazoEstimadoResolver.for_session(db).calcular(
            record.publication_date, record.category, record.subcategory
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Falha ao calcular prazo estimado do record %s (ignorado).",
            getattr(record, "id", "?"),
        )
