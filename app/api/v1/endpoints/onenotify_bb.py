"""Integração OneNotify BB dentro do módulo de publicações do Flow.

O intake público recebe payloads do OneNotify por API key. As rotas de leitura
ficam protegidas por JWT + permissão de publicações, como o restante do módulo.
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core import auth
from app.core.config import settings
from app.core.dependencies import get_db
from app.models.legal_one import LegalOneUser
from app.services.onenotify_bb_service import OneNotifyBBService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/publications/onenotify-bb", tags=["OneNotify BB"])
intake_router = APIRouter(prefix="/onenotify-bb", tags=["OneNotify BB (Intake)"])


def _get_service(db: Session = Depends(get_db)) -> OneNotifyBBService:
    return OneNotifyBBService(db=db)


def _validate_intake_api_key(
    x_onenotify_api_key: Optional[str] = Header(
        default=None, alias="X-Onenotify-Api-Key"
    ),
) -> str:
    valid_keys = settings.onenotify_bb_intake_api_keys
    if not valid_keys:
        logger.error("ONENOTIFY_BB_INTAKE_API_KEY não configurada — intake rejeitado.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Endpoint de intake do OneNotify BB não configurado.",
        )
    if not x_onenotify_api_key or x_onenotify_api_key not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key inválida ou ausente no header X-Onenotify-Api-Key.",
        )
    return x_onenotify_api_key


@intake_router.get("/health")
def intake_health(_: str = Depends(_validate_intake_api_key)):
    return {
        "status": "ok",
        "service": "flow-onenotify-bb-intake",
        "schema_version": "onenotify.flow-intake.v1",
    }


@intake_router.post("/intake")
def intake_notifications(
    payload: dict[str, Any],
    _: str = Depends(_validate_intake_api_key),
    service: OneNotifyBBService = Depends(_get_service),
):
    try:
        return service.ingest(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/stats")
def stats(
    service: OneNotifyBBService = Depends(_get_service),
    _: LegalOneUser = Depends(auth.require_permission("publications")),
):
    return service.stats()


@router.get("/notifications")
def list_notifications(
    status: Optional[str] = Query(default=None),
    action: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: OneNotifyBBService = Depends(_get_service),
    _: LegalOneUser = Depends(auth.require_permission("publications")),
):
    return service.list_notifications(
        status=status,
        action=action,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.get("/notifications/{notification_id}")
def notification_detail(
    notification_id: int,
    service: OneNotifyBBService = Depends(_get_service),
    _: LegalOneUser = Depends(auth.require_permission("publications")),
):
    detail = service.get_detail(notification_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Notificação não encontrada.")
    return detail
