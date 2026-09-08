from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, Field

from app.core.dependencies import get_db
from app.models.legal_one import LegalOneOffice
from app.services.office_lawsuit_index_service import (
    OfficeLawsuitIndexService,
    FULL_SYNC_TTL,
)

router = APIRouter()

# --- Pydantic Schema for Office Data ---
class OfficeResponse(BaseModel):
    id: int
    name: str
    path: str
    external_id: int
    # Polo do processo que esse escritorio atende (taxonomy v2).
    # 'ativo' / 'passivo' / 'ambos'. Default 'ambos' (preserva
    # comportamento atual). Migration: tax002.
    polo_scope: str = "ambos"

    class Config:
        orm_mode = True


class OfficePoloScopePayload(BaseModel):
    """Payload do Admin de Escritorios pra setar/atualizar polo_scope."""

    polo_scope: str = Field(..., pattern="^(ativo|passivo|ambos)$")


@router.get("/offices", response_model=List[OfficeResponse], summary="Listar todos os escritórios ativos", tags=["Offices"])
def get_all_offices(
    include_virtual: bool = Query(
        False,
        description=(
            "Inclui escritórios VIRTUAIS da casa (external_id negativo, ex.: -1 "
            "'Publicações sem pasta'). Default false: eles não existem no Legal "
            "One e não podem ir num payload de tarefa nem numa automação."
        ),
    ),
    db: Session = Depends(get_db),
):
    """
    Retorna os escritórios ativos.

    Escritórios VIRTUAIS (external_id < 0) ficam de FORA por padrão. Eles são
    da casa, não do Legal One: existem só para dar área de templates e card de
    fila a publicações sem processo vinculado (pub014). Esta lista alimenta
    ~10 telas, entre elas Automações e criação de tarefa — mandar um id que o
    L1 não conhece viraria erro na hora de agendar ou uma automação varrendo
    um escritório inexistente. Quem precisa deles (a UI de Templates) pede
    explicitamente com `include_virtual=true`.
    """
    query = db.query(LegalOneOffice).filter(LegalOneOffice.is_active == True)  # noqa: E712
    if not include_virtual:
        query = query.filter(LegalOneOffice.external_id >= 0)
    return query.order_by(LegalOneOffice.path).all()


@router.patch(
    "/offices/{external_id}/polo-scope",
    response_model=OfficeResponse,
    summary="Atualizar polo_scope do escritório",
    tags=["Offices"],
)
def update_office_polo_scope(
    external_id: int,
    payload: OfficePoloScopePayload,
    db: Session = Depends(get_db),
):
    """Define a qual polo do processo esse escritorio atende.

    Determina qual arvore da taxonomy v2 (ativo / passivo) e oferecida no
    modal de templates daquele escritorio e injetada no prompt do
    classificador. 'ambos' = sem filtro. Templates ja existentes nao sao
    afetados pelo PATCH; o operador re-aponta cada um pelo painel
    "Templates Pendentes de Revisao" usando POST /task-templates/{id}/migrate."""
    office = (
        db.query(LegalOneOffice)
        .filter(LegalOneOffice.external_id == external_id)
        .first()
    )
    if office is None:
        raise HTTPException(404, f"Escritorio external_id={external_id} nao encontrado.")
    office.polo_scope = payload.polo_scope
    db.commit()
    db.refresh(office)
    return office


# ─── Índice de processos por escritório ─────────────────

class LawsuitIndexStatus(BaseModel):
    office_id: int
    total_ids: int
    in_progress: bool
    progress_pct: int
    last_full_sync_at: Optional[str]
    last_incremental_at: Optional[str]
    last_sync_status: Optional[str]
    last_sync_error: Optional[str]
    supports_incremental: bool
    is_fresh: bool


def _serialize_status(svc: OfficeLawsuitIndexService, office_id: int) -> LawsuitIndexStatus:
    state = svc.get_sync_state(office_id)
    if state is None:
        return LawsuitIndexStatus(
            office_id=office_id,
            total_ids=0,
            in_progress=False,
            progress_pct=0,
            last_full_sync_at=None,
            last_incremental_at=None,
            last_sync_status=None,
            last_sync_error=None,
            supports_incremental=True,
            is_fresh=False,
        )
    return LawsuitIndexStatus(
        office_id=office_id,
        total_ids=state.total_ids or 0,
        in_progress=bool(state.in_progress),
        progress_pct=state.progress_pct or 0,
        last_full_sync_at=state.last_full_sync_at.isoformat() if state.last_full_sync_at else None,
        last_incremental_at=state.last_incremental_at.isoformat() if state.last_incremental_at else None,
        last_sync_status=state.last_sync_status,
        last_sync_error=state.last_sync_error,
        supports_incremental=bool(state.supports_incremental),
        is_fresh=svc.is_fresh(office_id),
    )


@router.get(
    "/offices/{office_id}/lawsuit-index",
    response_model=LawsuitIndexStatus,
    tags=["Offices"],
)
def get_lawsuit_index_status(office_id: int, db: Session = Depends(get_db)):
    svc = OfficeLawsuitIndexService(db)
    return _serialize_status(svc, office_id)


@router.post(
    "/offices/{office_id}/lawsuit-index/sync",
    response_model=LawsuitIndexStatus,
    tags=["Offices"],
)
def trigger_lawsuit_index_sync(
    office_id: int,
    force_full: bool = False,
    db: Session = Depends(get_db),
):
    """
    Dispara sync do índice de processos do escritório em background.
    Se já estiver rodando, apenas retorna o estado atual.
    """
    svc = OfficeLawsuitIndexService(db)
    svc.ensure_sync(office_id, force_full=force_full)
    return _serialize_status(svc, office_id)