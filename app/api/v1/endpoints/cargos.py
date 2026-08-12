"""Cargos (RBAC) — a política de acesso é POR CARGO, com exceção por usuário.

Modelo espelhado do Painel Financeiro (projeto irmão). A regra de cálculo vive
em `app/services/permissoes.py`; aqui é só a casca HTTP.

Módulo separado de propósito: o admin.py já passou de 1.300 linhas e é o tipo de
arquivo em que o Edit truncou código neste projeto.
"""
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import auth
from app.core.dependencies import get_db
from app.models.legal_one import FlowCargo, LegalOneUser
from app.services import permissoes as perm
from app.services.performance.teams import listar as listar_equipes

import logging

router = APIRouter()
me_router = APIRouter()
logger = logging.getLogger(__name__)


def _exige_admin(current_user: LegalOneUser) -> None:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )


class CargoPayload(BaseModel):
    nome: str
    descricao: Optional[str] = None
    modulos: Optional[Dict[str, bool]] = None
    equipes_modo: Optional[str] = None
    equipes: Optional[List[str]] = None
    ativo: Optional[bool] = None


class CargoDoUsuarioPayload(BaseModel):
    cargo_id: Optional[int] = None


def _dto(c: FlowCargo, usuarios: int) -> dict:
    return {
        "id": c.id, "nome": c.nome, "descricao": c.descricao,
        "modulos": c.modulos or {}, "equipes_modo": c.equipes_modo,
        "equipes": c.equipes or [], "ativo": c.ativo, "usuarios": usuarios,
    }


@router.get("/cargos/catalogo", tags=["Admin"], summary="Módulos, modos de equipe e equipes")
def catalogo(current_user: LegalOneUser = Depends(auth.get_current_user)):
    _exige_admin(current_user)
    return {
        "modulos": perm.MODULOS,
        "equipes_modos": [
            {"key": perm.MODO_NENHUMA, "label": "Nenhuma equipe"},
            {"key": perm.MODO_LISTA, "label": "Somente as equipes escolhidas"},
            {"key": perm.MODO_TODAS, "label": "Todas as equipes"},
            {"key": perm.MODO_SUPERVISIONADAS,
             "label": "As equipes que a pessoa supervisiona (resolve sozinho)"},
        ],
        "equipes": listar_equipes(),
    }


@router.get("/cargos", tags=["Admin"], summary="Lista os cargos com quantos usuários cada um tem")
def listar(
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _exige_admin(current_user)
    contagem = {
        cid: int(n)
        for cid, n in db.query(LegalOneUser.cargo_id, func.count(LegalOneUser.id))
        .group_by(LegalOneUser.cargo_id).all() if cid
    }
    return [_dto(c, contagem.get(c.id, 0)) for c in db.query(FlowCargo).order_by(FlowCargo.nome).all()]


@router.post("/cargos", tags=["Admin"], status_code=201, summary="Cria um cargo")
def criar(
    payload: CargoPayload,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _exige_admin(current_user)
    nome = (payload.nome or "").strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Informe o nome do cargo.")
    if db.query(FlowCargo).filter(func.lower(FlowCargo.nome) == nome.lower()).first():
        raise HTTPException(status_code=409, detail=f"Já existe um cargo chamado '{nome}'.")
    modo = payload.equipes_modo or perm.MODO_NENHUMA
    if modo not in perm.MODOS_EQUIPE:
        raise HTTPException(status_code=400, detail=f"Modo de equipe inválido: {modo}")
    c = FlowCargo(
        nome=nome,
        descricao=(payload.descricao or None),
        modulos={k: bool(v) for k, v in (payload.modulos or {}).items() if k in perm.MODULO_KEYS},
        equipes_modo=modo,
        equipes=list(payload.equipes or []),
        ativo=True,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    logger.info("Cargo criado: %s por %s", nome, current_user.email)
    return _dto(c, 0)


@router.put("/cargos/{cargo_id}", tags=["Admin"], summary="Edita o cargo e recalcula quem o herda")
def editar(
    cargo_id: int,
    payload: CargoPayload,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _exige_admin(current_user)
    c = db.get(FlowCargo, cargo_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Cargo não encontrado.")
    nome = (payload.nome or "").strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Informe o nome do cargo.")
    if db.query(FlowCargo).filter(
        func.lower(FlowCargo.nome) == nome.lower(), FlowCargo.id != cargo_id
    ).first():
        raise HTTPException(status_code=409, detail=f"Já existe um cargo chamado '{nome}'.")

    c.nome = nome
    c.descricao = payload.descricao or None
    if payload.modulos is not None:
        c.modulos = {k: bool(v) for k, v in payload.modulos.items() if k in perm.MODULO_KEYS}
    if payload.equipes_modo is not None:
        if payload.equipes_modo not in perm.MODOS_EQUIPE:
            raise HTTPException(status_code=400, detail="Modo de equipe inválido.")
        c.equipes_modo = payload.equipes_modo
    if payload.equipes is not None:
        c.equipes = list(payload.equipes)
    if payload.ativo is not None:
        c.ativo = bool(payload.ativo)
    db.commit()

    # A política mudou → rematerializa o cache de todo mundo que herda o cargo.
    res = perm.aplicar_em_todos(db, cargo_id=cargo_id)
    db.refresh(c)
    logger.info("Cargo %s editado por %s (%s recalculado[s]).",
                c.nome, current_user.email, res["total_alterados"])
    n = db.query(func.count(LegalOneUser.id)).filter(LegalOneUser.cargo_id == cargo_id).scalar() or 0
    dto = _dto(c, n)
    dto["recalculados"] = res["total_alterados"]
    return dto


@router.delete("/cargos/{cargo_id}", tags=["Admin"], summary="Desativa o cargo (bloqueia se tiver usuário)")
def excluir(
    cargo_id: int,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _exige_admin(current_user)
    c = db.get(FlowCargo, cargo_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Cargo não encontrado.")
    n = db.query(func.count(LegalOneUser.id)).filter(LegalOneUser.cargo_id == cargo_id).scalar() or 0
    if n:
        # Desativar com gente dentro derrubaria o acesso de todos de uma vez.
        raise HTTPException(
            status_code=409,
            detail=f"{n} usuário(s) ainda usam '{c.nome}'. Mova-os para outro cargo antes de excluir.",
        )
    c.ativo = False
    db.commit()
    logger.info("Cargo %s desativado por %s", c.nome, current_user.email)
    return {"ok": True, "nome": c.nome}


@router.patch("/users/{user_id}/cargo", tags=["Admin"],
              summary="Troca o cargo do usuário PRESERVANDO o acesso efetivo atual")
def trocar_cargo(
    user_id: int,
    payload: CargoDoUsuarioPayload,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _exige_admin(current_user)
    user = db.query(LegalOneUser).filter(LegalOneUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    if payload.cargo_id is not None and db.get(FlowCargo, payload.cargo_id) is None:
        raise HTTPException(status_code=404, detail="Cargo não encontrado.")
    # Troca preservando o efetivo: o que o novo cargo não cobrir vira exceção,
    # pra ninguém perder acesso sem o admin ver.
    perm.definir_cargo(db, user, payload.cargo_id)
    db.commit()
    return {"ok": True, "user_id": user.id, "efetivas": perm.efetivas(db, user)}


@me_router.get("/me/permissions", tags=["User"], summary="Permissões efetivas do usuário logado")
def minhas_permissoes(
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    ef = perm.efetivas(db, current_user)
    # Admin bypassa os gates do backend — o retorno reflete isso pra UI não
    # mostrar "sem acesso" pra quem, na prática, entra em tudo.
    if ef["admin"]:
        ef["modulos"] = {k: True for k in perm.MODULO_KEYS}
        ef["equipes"] = [t["key"] for t in listar_equipes()]
    return ef
