# app/api/v1/endpoints/squads.py

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List

from app.core.dependencies import get_db
from app.models import legal_one as legal_one_models
from app.api.v1 import schemas
from app.services.squad_service import SquadService

router = APIRouter()

def get_squad_service(db: Session = Depends(get_db)) -> SquadService:
    """
    Dependência para injetar o SquadService nos endpoints.
    """
    return SquadService(db)

@router.post("", response_model=schemas.Squad, status_code=201)
def create_squad(
    squad_data: schemas.SquadCreateSchema,
    service: SquadService = Depends(get_squad_service)
):
    """
    Cria um novo squad.
    """
    try:
        squad = service.create_squad(squad_data)
        return squad
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

from typing import Optional

@router.get("", response_model=List[schemas.Squad])
def get_squads(
    office_external_id: Optional[int] = None,
    kind: Optional[str] = None,
    service: SquadService = Depends(get_squad_service)
):
    """
    Endpoint para buscar todos os squads e membros ATIVOS.
    Pode ser filtrado por `office_external_id` e/ou `kind` ('principal' | 'support').
    """
    squads = service.get_all_squads(office_external_id=office_external_id, kind=kind)
    
    # Filtra membros inativos (com base no status do usuário do Legal One) na resposta
    active_squads_data = []
    for squad in squads:
        active_members = [
            member for member in squad.members if member.user and member.user.is_active
        ]
        squad.members = active_members
        active_squads_data.append(squad)

    return active_squads_data

@router.put("/{squad_id}", response_model=schemas.Squad)
def update_squad(
    squad_id: int,
    squad_data: schemas.SquadUpdateSchema,
    service: SquadService = Depends(get_squad_service)
):
    """
    Atualiza um squad existente (nome e/ou membros).
    """
    try:
        updated_squad = service.update_squad(squad_id, squad_data)
        if not updated_squad:
            raise HTTPException(status_code=404, detail=f"Squad com ID {squad_id} não encontrado.")
        return updated_squad
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/{squad_id}", status_code=204)
def deactivate_squad(
    squad_id: int,
    service: SquadService = Depends(get_squad_service)
):
    """
    Desativa um squad, marcando-o como inativo.
    """
    deactivated_squad = service.deactivate_squad(squad_id)
    if not deactivated_squad:
        raise HTTPException(status_code=404, detail=f"Squad com ID {squad_id} não encontrado.")
    return None # Retorna 204 No Content

@router.get("/legal-one-users", response_model=List[schemas.LegalOneUser])
def get_legal_one_users(db: Session = Depends(get_db)):
    """
    Endpoint para buscar todos os usuários do Legal One para
    popular os dropdowns de associação no frontend.
    """
    users = db.query(legal_one_models.LegalOneUser).order_by(legal_one_models.LegalOneUser.name).all()
    if not users:
        raise HTTPException(status_code=404, detail="Nenhum usuário do Legal One encontrado.")
    return users


# ─── Membros da squad (CRUD granular pra UI Admin) ───────────────────

@router.post(
    "/{squad_id}/members",
    response_model=schemas.SquadMember,
    status_code=201,
    summary="Adiciona um user a uma squad com papeis (is_leader/is_assistant).",
)
def add_squad_member(
    squad_id: int,
    body: schemas.SquadMemberAddRequest,
    service: SquadService = Depends(get_squad_service),
):
    try:
        member = service.add_member(
            squad_id,
            user_id=body.user_id,
            is_leader=body.is_leader,
            is_assistant=body.is_assistant,
        )
        return member
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete(
    "/{squad_id}/members/{member_id}",
    status_code=204,
    summary="Remove um user de uma squad.",
)
def remove_squad_member(
    squad_id: int,
    member_id: int,
    service: SquadService = Depends(get_squad_service),
):
    ok = service.remove_member(squad_id, member_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Membro {member_id} nao encontrado na squad {squad_id}.",
        )
    return None


@router.patch(
    "/{squad_id}/members/{member_id}",
    response_model=schemas.SquadMember,
    summary="Alterna papeis (is_leader/is_assistant) de um membro. Garante max 1 por papel.",
)
def update_squad_member_roles(
    squad_id: int,
    member_id: int,
    body: schemas.SquadMemberRoleUpdate,
    service: SquadService = Depends(get_squad_service),
):
    member = service.update_member_roles(
        squad_id,
        member_id,
        is_leader=body.is_leader,
        is_assistant=body.is_assistant,
    )
    if member is None:
        raise HTTPException(
            status_code=404,
            detail=f"Membro {member_id} nao encontrado na squad {squad_id}.",
        )
    return member


# ─── Resolucao do assistente (usado pelo frontend) ─────────────────

@router.get(
    "/assistant-of/{user_external_id}",
    response_model=schemas.AssistantResolution,
    summary="PREVIEW do assistente — nao avanca a fila do round-robin.",
)
def get_assistant_of_user(
    user_external_id: int,
    task_subtype_external_id: Optional[int] = None,
    office_external_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Lookup somente leitura. Quando uma squad tem multiplos assistentes,
    este endpoint retorna o proximo da rotacao, mas NAO incrementa o
    `last_assigned_at`. Use o POST /claim quando for criar a tarefa de
    verdade — ai a fila avanca."""
    from app.services.squad_assistant_resolver import resolve_assistant
    try:
        result = resolve_assistant(
            db,
            responsible_user_external_id=user_external_id,
            task_subtype_external_id=task_subtype_external_id,
            office_external_id=office_external_id,
            commit=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return schemas.AssistantResolution(
        user_external_id=result.user_external_id,
        squad_id=result.squad_id,
        squad_name=result.squad_name,
        fallback_reason=result.fallback_reason,
    )


# ─── Resolver unificado (cobre target_squad_id + target_role) ─────

class ResolveTargetRequest(BaseModel):
    target_role: str = "principal"  # 'principal' | 'assistente'
    responsible_user_external_id: Optional[int] = None
    target_squad_id: Optional[int] = None
    office_external_id: Optional[int] = None
    task_subtype_external_id: Optional[int] = None
    # Excecao por etiqueta (sqd005): com o processo e o template, a regra do
    # template e' aplicada antes do rodizio. A tela classica resolve por aqui
    # antes de enviar a tarefa; sem estes campos a excecao so' valeria na
    # Triagem, que resolve no servidor. Opcionais: chamada antiga segue igual.
    lawsuit_id: Optional[int] = None
    template_id: Optional[int] = None


def _resolver_destino(
    db: Session, body: "ResolveTargetRequest", *, commit: bool,
) -> schemas.AssistantResolution:
    """Preview e claim resolvem pelo MESMO caminho, com a excecao por etiqueta
    (sqd005) antes da regra comum. A unica diferenca entre os dois e' o
    `commit`, que avanca (claim) ou nao (preview) o rodizio."""
    from app.services.excecao_etiqueta import aplicar_excecao_etiqueta
    from app.services.squad_assistant_resolver import resolve_target

    excecao = aplicar_excecao_etiqueta(
        db,
        template_id=body.template_id,
        lawsuit_id=body.lawsuit_id,
        target_role=body.target_role,
        commit=commit,
    )
    if excecao is not None:
        result, motivo = excecao
    else:
        if body.target_role == "principal" and body.target_squad_id is None:
            # Sem squad de suporte e papel=principal: precisa do responsible_user_external_id
            if not body.responsible_user_external_id:
                raise HTTPException(
                    status_code=422,
                    detail="responsible_user_external_id obrigatorio quando target_squad_id nao informado.",
                )
        result = resolve_target(
            db,
            target_role=body.target_role,
            responsible_user_external_id=body.responsible_user_external_id or 0,
            target_squad_id=body.target_squad_id,
            office_external_id=body.office_external_id,
            task_subtype_external_id=body.task_subtype_external_id,
            commit=commit,
        )
        motivo = None
    return schemas.AssistantResolution(
        user_external_id=result.user_external_id,
        squad_id=result.squad_id,
        squad_name=result.squad_name,
        fallback_reason=result.fallback_reason,
        motivo=motivo,
    )


@router.post(
    "/resolve-target/preview",
    response_model=schemas.AssistantResolution,
    summary="PREVIEW de quem recebe a tarefa (cobre 4 cenarios) — nao avanca fila.",
)
def resolve_target_preview(
    body: ResolveTargetRequest,
    db: Session = Depends(get_db),
):
    try:
        return _resolver_destino(db, body, commit=False)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/resolve-target/claim",
    response_model=schemas.AssistantResolution,
    summary="Resolve E avanca a fila do round-robin (claim definitivo).",
)
def resolve_target_claim(
    body: ResolveTargetRequest,
    db: Session = Depends(get_db),
):
    try:
        resposta = _resolver_destino(db, body, commit=True)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return resposta


@router.post(
    "/assistant-of/{user_external_id}/claim",
    response_model=schemas.AssistantResolution,
    summary="Resolve E avanca a fila do round-robin (assistente recebe a tarefa).",
)
def claim_assistant_of_user(
    user_external_id: int,
    task_subtype_external_id: Optional[int] = None,
    office_external_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Use no momento de criar a tarefa de verdade no L1 — o backend
    incrementa o `last_assigned_at` do assistente escolhido pra que o
    proximo claim pegue outro membro da fila."""
    from app.services.squad_assistant_resolver import resolve_assistant
    try:
        result = resolve_assistant(
            db,
            responsible_user_external_id=user_external_id,
            task_subtype_external_id=task_subtype_external_id,
            office_external_id=office_external_id,
            commit=True,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return schemas.AssistantResolution(
        user_external_id=result.user_external_id,
        squad_id=result.squad_id,
        squad_name=result.squad_name,
        fallback_reason=result.fallback_reason,
    )