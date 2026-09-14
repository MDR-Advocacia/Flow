"""Relatório Crítico de Performance da equipe de Publicações (admin-only).

Endpoint sob demanda do supervisor: recebe um período (mínimo 5 dias),
compila as métricas de capacity, gera o diagnóstico crítico (Sonnet com
fallback) e devolve um PDF executivo renderizado server-side.

Prefixo final (registrado em main.py): /api/v1/publications/performance-report.pdf
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.core import auth as auth_security
from app.core.dependencies import get_db
from app.models.legal_one import LegalOneUser
from app.services.publications_report import (
    build_narrative,
    compute_metrics,
    html_to_pdf,
    render_html,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Publicações"])

# Período mínimo de estudo exigido pela regra de negócio.
MIN_PERIOD_DAYS = 5


def require_admin(
    current: LegalOneUser = Depends(auth_security.get_current_user),
) -> LegalOneUser:
    if getattr(current, "role", "user") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas administradores podem gerar o relatório de performance.",
        )
    return current


@router.get("/performance-report.pdf")
def performance_report_pdf(
    date_from: date = Query(..., description="Início do período (YYYY-MM-DD)."),
    date_to: date = Query(..., description="Fim do período (YYYY-MM-DD), inclusivo."),
    db: Session = Depends(get_db),
    user: LegalOneUser = Depends(require_admin),
) -> Response:
    if date_to < date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A data final deve ser igual ou posterior à inicial.",
        )
    dias = (date_to - date_from).days + 1
    if dias < MIN_PERIOD_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"O período mínimo de estudo é de {MIN_PERIOD_DAYS} dias (selecionado: {dias}).",
        )

    logger.info(
        "Relatório de performance solicitado por %s: %s a %s (%s dias).",
        getattr(user, "email", user.id), date_from, date_to, dias,
    )

    try:
        metrics = compute_metrics(db, date_from, date_to)
    except Exception:
        logger.exception("Relatório de performance: falha ao computar métricas.")
        raise HTTPException(status_code=500, detail="Falha ao computar as métricas do período.")

    if metrics["totais"]["total_decisoes"] == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Não há decisões de tratamento no período selecionado.",
        )

    narrative = build_narrative(metrics)
    html = render_html(metrics, narrative, date_from, date_to)

    try:
        pdf_bytes = html_to_pdf(html)
    except Exception:
        logger.exception("Relatório de performance: falha ao renderizar o PDF.")
        raise HTTPException(status_code=500, detail="Falha ao renderizar o PDF do relatório.")

    filename = f"relatorio-performance-publicacoes-{date_from}_{date_to}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/entradas-escritorio-report.pdf")
def entradas_escritorio_report_pdf(
    date_from: date = Query(..., description="Início do período (YYYY-MM-DD)."),
    date_to: date = Query(..., description="Fim do período (YYYY-MM-DD), inclusivo."),
    base: Literal["captura", "publicacao"] = Query(
        "captura",
        description="Dia da entrada: captura (quando o Flow viu) ou publicacao (quando o diário publicou).",
    ),
    db: Session = Depends(get_db),
    user: LegalOneUser = Depends(auth_security.require_permission("publications")),
) -> Response:
    """Relatório executivo de entradas e tratamento por escritório responsável (PDF).

    Pedido do operador em 14/09/2026: volume de entrada por escritório e volume
    tratado, só números. Mesmos critérios do Dashboard de Publicações — ver
    app/services/publications_report/entradas_escritorio.py.
    """
    from app.services.publications_report.entradas_escritorio import (
        compute_entradas_escritorio,
        render_entradas_escritorio_html,
    )

    try:
        dados = compute_entradas_escritorio(db, date_from, date_to, base)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception:
        logger.exception("Relatório de entradas por escritório: falha ao compilar os números.")
        raise HTTPException(status_code=500, detail="Falha ao compilar os números do período.")

    logger.info(
        "Relatório de entradas por escritório solicitado por %s: %s a %s (base %s).",
        getattr(user, "email", getattr(user, "id", None)), date_from, date_to, base,
    )
    try:
        pdf_bytes = html_to_pdf(render_entradas_escritorio_html(dados))
    except Exception:
        logger.exception("Relatório de entradas por escritório: falha ao renderizar o PDF.")
        raise HTTPException(status_code=500, detail="Falha ao renderizar o PDF do relatório.")

    filename = f"entradas-tratamento-por-escritorio-{date_from}_{date_to}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
