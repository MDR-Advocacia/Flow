"""Relatório de utilização do sistema (adesão) — só para administradores.

Módulo separado do admin.py de propósito: aquele arquivo já passou de 1.300
linhas e é justamente o tipo em que o Edit truncou código neste projeto.
"""
import csv
import io
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core import auth
from app.core.dependencies import get_db
from app.models.legal_one import LegalOneUser
from app.services import uso_relatorio, uso_service

router = APIRouter()
logger = logging.getLogger(__name__)


def _exige_admin(current_user: LegalOneUser) -> None:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )


@router.get("/uso")
def relatorio_de_uso(
    dias: int = Query(30, ge=1, le=365),
    apenas_supervisores: bool = Query(False),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    """Adesão por usuário: navegação + ação efetiva no período."""
    _exige_admin(current_user)
    # Descarrega o que está em memória antes de ler: sem isto o administrativo
    # abre a tela e não vê o que ele mesmo acabou de fazer, o que faz o
    # relatório inteiro parecer quebrado.
    try:
        uso_service.descarregar()
    except Exception:  # noqa: BLE001
        pass

    dados = uso_relatorio.gerar(
        db, dias=dias, apenas_supervisores=apenas_supervisores)
    itens = dados.pop("itens")
    return {**dados, "total": len(itens), "items": itens[offset:offset + limit]}


@router.get("/uso/export")
def exportar_uso(
    dias: int = Query(30, ge=1, le=365),
    apenas_supervisores: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    """Mesmo relatório em CSV, para levar à reunião."""
    _exige_admin(current_user)
    try:
        uso_service.descarregar()
    except Exception:  # noqa: BLE001
        pass

    dados = uso_relatorio.gerar(
        db, dias=dias, apenas_supervisores=apenas_supervisores)

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Nome", "E-mail", "Cargo", "Supervisor", "Equipes", "Situação",
                "Último acesso", "Dias com acesso", "Requisições",
                "Módulos usados", "Ações efetivas"])
    for i in dados["itens"]:
        w.writerow([
            i["nome"], i["email"], i["cargo"],
            "Sim" if i["supervisor"] else "Não",
            ", ".join(i["equipes"]), i["situacao"],
            (i["ultimo_acesso"] or "")[:16].replace("T", " "),
            i["dias_ativos"], i["requisicoes"],
            ", ".join(i["modulos"]), i["acoes"],
        ])
    buf.seek(0)
    # utf-8-sig: sem o BOM o Excel em pt-BR abre os acentos quebrados.
    return StreamingResponse(
        iter([buf.getvalue().encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="utilizacao_{dias}dias.csv"'},
    )
