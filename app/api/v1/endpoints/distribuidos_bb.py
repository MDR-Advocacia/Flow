"""Endpoints do módulo Distribuídos BB (Banco do Brasil).

Superfície da UI: dashboard, listagem de processos, auditoria por processo,
log global de eventos, ingestão do RPA legado e CRUD das tabelas editáveis
(escritórios/filas, responsáveis, equipe de envolvidos).

Gating: `can_manage_distribuidos_bb` (admin sempre passa) — permissão dedicada
que o admin concede por usuário na tela de Administração.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

import threading

from app.core import auth
from app.core.config import settings
from app.core.dependencies import get_db
from app.models.distribuidos_bb import (
    BbClassificacao,
    BbEquipeMembro,
    BbEscritorio,
    BbRegraObservacao,
    BbResponsavel,
    BbRun,
    SECAO_CONFIGURACAO,
)
from app.models.legal_one import LegalOneUser
from app.services.distribuidos_bb import coleta_service
from app.services.distribuidos_bb.log_service import registrar_evento
from app.services.distribuidos_bb.onelog_client import OneLogClient, OneLogError
from app.services.distribuidos_bb.seed import seed_all
from app.services.distribuidos_bb.service import DistribuidosBBService

router = APIRouter(prefix="/distribuidos-bb", tags=["Distribuídos BB"])
logger = logging.getLogger(__name__)


def _require_gestao(current_user: LegalOneUser) -> None:
    """Permite admin OU quem tem a permissão can_manage_distribuidos_bb."""
    if current_user.role == "admin":
        return
    if not getattr(current_user, "can_manage_distribuidos_bb", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Você não tem permissão para o módulo Distribuídos BB.",
        )


# ─────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────


class EscritorioPayload(BaseModel):
    nome: Optional[str] = None
    escritorio_path: Optional[str] = None
    criterio_polo: Optional[str] = None
    criterio_natureza: Optional[str] = None
    responsavel_fixo_user_id: Optional[int] = None
    observacao_padrao: Optional[str] = None
    ativo: Optional[bool] = None
    ordem: Optional[int] = None


class ResponsavelPayload(BaseModel):
    escritorio_id: int
    user_id: int
    ordem: Optional[int] = None
    ativo: Optional[bool] = None


class EquipeMembroPayload(BaseModel):
    responsavel_user_id: int
    membro_user_id: int
    classificacao: str = Field(..., min_length=1)
    ativo: Optional[bool] = None


class RegraObservacaoPayload(BaseModel):
    nome: Optional[str] = None
    criterio_posicao: Optional[str] = None   # Réu | Autor | Interessado | ""(qualquer)
    criterio_natureza: Optional[str] = None
    criterio_cnj: Optional[str] = None        # "com" | "sem" | ""(qualquer)
    texto: Optional[str] = None
    ativo: Optional[bool] = None
    ordem: Optional[int] = None


class IngerirPayload(BaseModel):
    linhas: list[dict[str, Any]] = Field(..., description="Linhas capturadas (formato do RPA legado).")
    run_id: Optional[int] = None


class ColetarPayload(BaseModel):
    data_inicial: Optional[str] = Field(None, description="DD/MM/AAAA (vazio = hoje).")
    data_final: Optional[str] = Field(None, description="DD/MM/AAAA (vazio = hoje).")
    confirmar_ciencia: bool = Field(
        False, description="Se True (e a trava global permitir), o robô dá ciência (SIM) no BB."
    )
    coletar_envolvidos: bool = Field(
        True, description="Também abre a capa do NPJ e captura as Pessoas do Processo (mais lento)."
    )


def _run_dto(run: BbRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "data_inicial": run.data_inicial,
        "data_final": run.data_final,
        "status": run.status,
        "confirmar_ciencia": run.confirmar_ciencia,
        "total_coletados": run.total_coletados,
        "total_ciencia": run.total_ciencia,
        "total_distribuidos": run.total_distribuidos,
        "total_cadastrados": run.total_cadastrados,
        "total_erros": run.total_erros,
        "iniciado_em": run.iniciado_em.isoformat() if run.iniciado_em else None,
        "concluido_em": run.concluido_em.isoformat() if run.concluido_em else None,
        "erro": run.erro,
    }


# ─────────────────────────────────────────────────────────────────────
# Dashboard / listagens / auditoria / log
# ─────────────────────────────────────────────────────────────────────


@router.get("/dashboard", summary="KPIs e visão geral do módulo")
def get_dashboard(
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    return DistribuidosBBService(db).dashboard()


@router.get("/processos", summary="Lista processos distribuídos (paginado)")
def listar_processos(
    status_filtro: Optional[str] = Query(None, alias="status"),
    escritorio_id: Optional[int] = Query(None),
    busca: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    return DistribuidosBBService(db).listar_processos(
        status=status_filtro, escritorio_id=escritorio_id, busca=busca, limit=limit, offset=offset,
    )


@router.get("/processos/{processo_id}/auditoria", summary="Auditoria completa de um processo")
def auditoria_processo(
    processo_id: int,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    resultado = DistribuidosBBService(db).auditoria_processo(processo_id)
    if resultado is None:
        raise HTTPException(status_code=404, detail="Processo não encontrado.")
    return resultado


@router.get("/eventos", summary="Log global de eventos (paginado)")
def listar_eventos(
    secao: Optional[str] = Query(None),
    nivel: Optional[str] = Query(None),
    processo_id: Optional[int] = Query(None),
    run_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    return DistribuidosBBService(db).listar_eventos(
        secao=secao, nivel=nivel, processo_id=processo_id, run_id=run_id, limit=limit, offset=offset,
    )


@router.post("/ingerir", summary="Ingere linhas capturadas (RPA legado) e distribui")
def ingerir(
    payload: IngerirPayload,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    return DistribuidosBBService(db).ingerir_linhas(payload.linhas, run_id=payload.run_id)


@router.post("/seed", summary="Cria a configuração inicial a partir dos padrões do robô")
def seed(
    forcar: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    return seed_all(db, forcar=forcar)


# ─────────────────────────────────────────────────────────────────────
# Coleta na nuvem (OneLog + Playwright) — Fase 2
# ─────────────────────────────────────────────────────────────────────


@router.post("/testar-onelog", summary="Testa o login no OneLog (sem abrir o navegador)")
def testar_onelog(
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    """Diagnóstico isolado: pede uma sessão ao OneLog e confere se vieram cookies.

    NÃO abre o Chromium nem toca no portal BB — serve só pra validar credenciais
    e conectividade com o OneLog.
    """
    _require_gestao(current_user)
    cliente = OneLogClient()
    if not cliente.configurado:
        return {
            "ok": False,
            "configurado": False,
            "erro": "Credenciais do OneLog não configuradas (DISTRIBUIDOS_BB_ONELOG_USERNAME/PASSWORD).",
        }
    try:
        sessao = cliente.obter_sessao()
        cookies = sessao.get("cookies", []) or []
        registrar_evento(
            db, secao=SECAO_CONFIGURACAO, acao="Teste OneLog",
            mensagem=f"Login no OneLog OK: {len(cookies)} cookie(s) recebido(s).",
            dados={"cookies": len(cookies)}, commit=True,
        )
        return {
            "ok": len(cookies) > 0,
            "configurado": True,
            "cookies": len(cookies),
            "user_agent": sessao.get("user_agent"),
            "api_url": cliente.api_url,
            "usuario": cliente.username,
            "erro": None if cookies else "OneLog respondeu, mas não devolveu cookies.",
        }
    except OneLogError as exc:
        registrar_evento(
            db, secao=SECAO_CONFIGURACAO, acao="Teste OneLog", nivel="ERRO",
            mensagem=f"Falha no login do OneLog: {exc}", commit=True,
        )
        return {"ok": False, "configurado": True, "erro": str(exc), "api_url": cliente.api_url, "usuario": cliente.username}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "configurado": True, "erro": f"Erro inesperado: {exc}", "api_url": cliente.api_url}


@router.post("/coletar", summary="Dispara uma coleta no portal BB (background)")
def coletar(
    payload: ColetarPayload,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)

    if not OneLogClient().configurado:
        raise HTTPException(
            status_code=503,
            detail=(
                "Coleta indisponível: credenciais do OneLog não configuradas "
                "(distribuidos_bb_onelog_username/password no ambiente)."
            ),
        )

    run = coleta_service.criar_run(
        db,
        data_inicial=payload.data_inicial,
        data_final=payload.data_final,
        confirmar_ciencia=payload.confirmar_ciencia,
        disparado_por_user_id=current_user.id,
    )

    # Aviso claro quando o operador pediu ciência mas a trava global bloqueia.
    ciencia_efetiva = payload.confirmar_ciencia and settings.distribuidos_bb_confirmar_ciencia

    thread = threading.Thread(
        target=coleta_service.executar_coleta_background,
        args=(run.id,),
        kwargs={
            "data_inicial": payload.data_inicial,
            "data_final": payload.data_final,
            "coletar_envolvidos": payload.coletar_envolvidos,
        },
        daemon=True,
    )
    thread.start()

    return {
        "run_id": run.id,
        "status": run.status,
        "ciencia_efetiva": ciencia_efetiva,
        "aviso_ciencia": (
            None
            if not payload.confirmar_ciencia or ciencia_efetiva
            else "Você pediu ciência, mas a trava global de segurança está desligada — coleta rodará em modo seguro."
        ),
    }


@router.get("/runs", summary="Lista execuções de coleta (paginado)")
def listar_runs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    q = db.query(BbRun).order_by(BbRun.id.desc())
    total = q.count()
    rows = q.limit(limit).offset(offset).all()
    return {"total": total, "items": [_run_dto(r) for r in rows]}


@router.get("/runs/{run_id}", summary="Progresso de uma execução de coleta")
def get_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    run = db.get(BbRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Execução não encontrada.")
    return _run_dto(run)


# ─────────────────────────────────────────────────────────────────────
# Usuários (para os comboboxes de configuração)
# ─────────────────────────────────────────────────────────────────────


@router.get("/config/usuarios", summary="Lista usuários do Legal One (para seleção)")
def listar_usuarios(
    busca: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    q = db.query(LegalOneUser).filter(LegalOneUser.is_active.is_(True))
    if busca:
        q = q.filter(LegalOneUser.name.ilike(f"%{busca.strip()}%"))
    rows = q.order_by(LegalOneUser.name).limit(500).all()
    return [{"id": u.id, "name": u.name} for u in rows]


# ─────────────────────────────────────────────────────────────────────
# Config: escritórios/filas + responsáveis (tabelas editáveis)
# ─────────────────────────────────────────────────────────────────────


def _escritorio_dto(db: Session, esc: BbEscritorio) -> dict[str, Any]:
    nomes = dict(
        db.query(LegalOneUser.id, LegalOneUser.name)
        .filter(LegalOneUser.id.in_([r.user_id for r in esc.responsaveis] or [0]))
        .all()
    )
    fixo_nome = None
    if esc.responsavel_fixo_user_id:
        fixo = db.get(LegalOneUser, esc.responsavel_fixo_user_id)
        fixo_nome = fixo.name if fixo else None
    return {
        "id": esc.id,
        "nome": esc.nome,
        "escritorio_path": esc.escritorio_path,
        "criterio_polo": esc.criterio_polo,
        "criterio_natureza": esc.criterio_natureza,
        "responsavel_fixo_user_id": esc.responsavel_fixo_user_id,
        "responsavel_fixo_nome": fixo_nome,
        "observacao_padrao": esc.observacao_padrao,
        "ativo": esc.ativo,
        "ordem": esc.ordem,
        "responsaveis": [
            {
                "id": r.id,
                "user_id": r.user_id,
                "nome": nomes.get(r.user_id),
                "ordem": r.ordem,
                "ativo": r.ativo,
            }
            for r in esc.responsaveis
        ],
    }


@router.get("/config/escritorios", summary="Lista escritórios/filas com responsáveis")
def listar_escritorios(
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    escritorios = db.query(BbEscritorio).order_by(BbEscritorio.ordem, BbEscritorio.id).all()
    return [_escritorio_dto(db, e) for e in escritorios]


@router.post("/config/escritorios", status_code=201, summary="Cria escritório/fila")
def criar_escritorio(
    payload: EscritorioPayload,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    if not (payload.nome or "").strip() or not (payload.escritorio_path or "").strip():
        raise HTTPException(status_code=400, detail="Nome e caminho do escritório são obrigatórios.")
    esc = BbEscritorio(
        nome=payload.nome.strip(),
        escritorio_path=payload.escritorio_path.strip(),
        criterio_polo=payload.criterio_polo,
        criterio_natureza=payload.criterio_natureza,
        responsavel_fixo_user_id=payload.responsavel_fixo_user_id,
        observacao_padrao=payload.observacao_padrao,
        ativo=payload.ativo if payload.ativo is not None else True,
        ordem=payload.ordem or 0,
    )
    db.add(esc)
    registrar_evento(db, secao=SECAO_CONFIGURACAO, acao="Escritório criado", mensagem=f"Escritório '{esc.nome}' criado.")
    db.commit()
    db.refresh(esc)
    return _escritorio_dto(db, esc)


@router.patch("/config/escritorios/{escritorio_id}", summary="Edita escritório/fila")
def editar_escritorio(
    escritorio_id: int,
    payload: EscritorioPayload,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    esc = db.get(BbEscritorio, escritorio_id)
    if esc is None:
        raise HTTPException(status_code=404, detail="Escritório não encontrado.")
    for campo in (
        "nome", "escritorio_path", "criterio_polo", "criterio_natureza",
        "responsavel_fixo_user_id", "observacao_padrao", "ativo", "ordem",
    ):
        valor = getattr(payload, campo)
        if valor is not None:
            setattr(esc, campo, valor)
    registrar_evento(db, secao=SECAO_CONFIGURACAO, acao="Escritório editado", mensagem=f"Escritório '{esc.nome}' atualizado.")
    db.commit()
    db.refresh(esc)
    return _escritorio_dto(db, esc)


@router.delete("/config/escritorios/{escritorio_id}", summary="Desativa escritório/fila (soft)")
def desativar_escritorio(
    escritorio_id: int,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    esc = db.get(BbEscritorio, escritorio_id)
    if esc is None:
        raise HTTPException(status_code=404, detail="Escritório não encontrado.")
    esc.ativo = False
    registrar_evento(db, secao=SECAO_CONFIGURACAO, acao="Escritório desativado", mensagem=f"Escritório '{esc.nome}' desativado.")
    db.commit()
    return {"ok": True}


@router.post("/config/responsaveis", status_code=201, summary="Adiciona responsável à fila")
def adicionar_responsavel(
    payload: ResponsavelPayload,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    esc = db.get(BbEscritorio, payload.escritorio_id)
    if esc is None:
        raise HTTPException(status_code=404, detail="Escritório não encontrado.")
    ja = (
        db.query(BbResponsavel)
        .filter(BbResponsavel.escritorio_id == payload.escritorio_id, BbResponsavel.user_id == payload.user_id)
        .first()
    )
    if ja:
        raise HTTPException(status_code=409, detail="Esse responsável já está na fila deste escritório.")
    resp = BbResponsavel(
        escritorio_id=payload.escritorio_id,
        user_id=payload.user_id,
        ordem=payload.ordem or 0,
        ativo=payload.ativo if payload.ativo is not None else True,
    )
    db.add(resp)
    db.commit()
    db.refresh(esc)
    return _escritorio_dto(db, esc)


@router.delete("/config/responsaveis/{responsavel_id}", summary="Remove responsável da fila")
def remover_responsavel(
    responsavel_id: int,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    resp = db.get(BbResponsavel, responsavel_id)
    if resp is None:
        raise HTTPException(status_code=404, detail="Responsável não encontrado.")
    db.delete(resp)
    db.commit()
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────
# Config: equipe de envolvidos (por responsável)
# ─────────────────────────────────────────────────────────────────────


@router.get("/config/classificacoes", summary="Catálogo de classificações/posições de envolvido")
def listar_classificacoes(
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    rows = (
        db.query(BbClassificacao)
        .filter(BbClassificacao.ativo.is_(True))
        .order_by(BbClassificacao.ordem, BbClassificacao.id)
        .all()
    )
    return [
        {
            "id": c.id,
            "nome": c.nome,
            "situacao": c.situacao,
            "participante_tipo": c.participante_tipo,
            "position_id_l1": c.position_id_l1,
        }
        for c in rows
    ]


@router.get("/config/responsaveis", summary="Responsáveis distintos (para gerir equipes)")
def listar_responsaveis_distintos(
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    """Une os responsáveis das filas (round-robin) + os fixos dos escritórios,
    com quantos membros de equipe cada um já tem — pra tela de Equipes.
    """
    _require_gestao(current_user)
    ids: set[int] = set()
    for (uid,) in db.query(BbResponsavel.user_id).distinct().all():
        if uid:
            ids.add(uid)
    for (uid,) in db.query(BbEscritorio.responsavel_fixo_user_id).distinct().all():
        if uid:
            ids.add(uid)
    if not ids:
        return []
    nomes = dict(
        db.query(LegalOneUser.id, LegalOneUser.name).filter(LegalOneUser.id.in_(ids)).all()
    )
    # contagem de membros de equipe por responsável
    contagem: dict[int, int] = {}
    for uid, qtd in (
        db.query(BbEquipeMembro.responsavel_user_id, func.count(BbEquipeMembro.id))
        .group_by(BbEquipeMembro.responsavel_user_id)
        .all()
    ):
        contagem[uid] = qtd
    return sorted(
        [
            {"user_id": uid, "nome": nomes.get(uid), "membros": contagem.get(uid, 0)}
            for uid in ids
        ],
        key=lambda x: (x["nome"] or "").lower(),
    )


def _regra_dto(r: BbRegraObservacao) -> dict[str, Any]:
    return {
        "id": r.id,
        "nome": r.nome,
        "criterio_posicao": r.criterio_posicao,
        "criterio_natureza": r.criterio_natureza,
        "criterio_cnj": r.criterio_cnj,
        "texto": r.texto,
        "ativo": r.ativo,
        "ordem": r.ordem,
    }


@router.get("/config/regras-observacao", summary="Regras do campo Observação (ativam o workflow no L1)")
def listar_regras_observacao(
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    rows = db.query(BbRegraObservacao).order_by(BbRegraObservacao.ordem, BbRegraObservacao.id).all()
    return [_regra_dto(r) for r in rows]


@router.post("/config/regras-observacao", status_code=201, summary="Cria regra de observação")
def criar_regra_observacao(
    payload: RegraObservacaoPayload,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    if not (payload.nome or "").strip() or not (payload.texto or "").strip():
        raise HTTPException(status_code=400, detail="Nome e texto da observação são obrigatórios.")
    ordem = payload.ordem
    if ordem is None:
        maior = db.query(func.max(BbRegraObservacao.ordem)).scalar()
        ordem = (maior or 0) + 1
    r = BbRegraObservacao(
        nome=payload.nome.strip(),
        criterio_posicao=(payload.criterio_posicao or None) or None,
        criterio_natureza=(payload.criterio_natureza or None) or None,
        criterio_cnj=(payload.criterio_cnj or None) or None,
        texto=payload.texto.strip(),
        ativo=payload.ativo if payload.ativo is not None else True,
        ordem=ordem,
    )
    db.add(r)
    registrar_evento(db, secao=SECAO_CONFIGURACAO, acao="Regra criada", mensagem=f"Regra de observação '{r.nome}' → '{r.texto}'.")
    db.commit()
    db.refresh(r)
    return _regra_dto(r)


@router.patch("/config/regras-observacao/{regra_id}", summary="Edita regra de observação")
def editar_regra_observacao(
    regra_id: int,
    payload: RegraObservacaoPayload,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    r = db.get(BbRegraObservacao, regra_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Regra não encontrada.")
    # Campos de texto: "" limpa o critério (vira "qualquer"); None = não mexe.
    if payload.nome is not None:
        r.nome = payload.nome.strip() or r.nome
    if payload.texto is not None and payload.texto.strip():
        r.texto = payload.texto.strip()
    for campo in ("criterio_posicao", "criterio_natureza", "criterio_cnj"):
        valor = getattr(payload, campo)
        if valor is not None:
            setattr(r, campo, valor.strip() or None)
    if payload.ativo is not None:
        r.ativo = payload.ativo
    if payload.ordem is not None:
        r.ordem = payload.ordem
    registrar_evento(db, secao=SECAO_CONFIGURACAO, acao="Regra editada", mensagem=f"Regra de observação '{r.nome}' atualizada.")
    db.commit()
    db.refresh(r)
    return _regra_dto(r)


@router.delete("/config/regras-observacao/{regra_id}", summary="Remove regra de observação")
def remover_regra_observacao(
    regra_id: int,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    r = db.get(BbRegraObservacao, regra_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Regra não encontrada.")
    db.delete(r)
    registrar_evento(db, secao=SECAO_CONFIGURACAO, acao="Regra removida", mensagem=f"Regra de observação '{r.nome}' removida.")
    db.commit()
    return {"ok": True}


@router.get("/config/equipe/{responsavel_user_id}", summary="Equipe (envolvidos) de um responsável")
def listar_equipe(
    responsavel_user_id: int,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    rows = (
        db.query(BbEquipeMembro)
        .filter(BbEquipeMembro.responsavel_user_id == responsavel_user_id)
        .all()
    )
    nomes = dict(
        db.query(LegalOneUser.id, LegalOneUser.name)
        .filter(LegalOneUser.id.in_([r.membro_user_id for r in rows] or [0]))
        .all()
    )
    return [
        {
            "id": r.id,
            "membro_user_id": r.membro_user_id,
            "membro_nome": nomes.get(r.membro_user_id),
            "classificacao": r.classificacao,
            "ativo": r.ativo,
        }
        for r in rows
    ]


@router.post("/config/equipe", status_code=201, summary="Adiciona membro à equipe de um responsável")
def adicionar_membro_equipe(
    payload: EquipeMembroPayload,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    ja = (
        db.query(BbEquipeMembro)
        .filter(
            BbEquipeMembro.responsavel_user_id == payload.responsavel_user_id,
            BbEquipeMembro.membro_user_id == payload.membro_user_id,
            BbEquipeMembro.classificacao == payload.classificacao,
        )
        .first()
    )
    if ja:
        raise HTTPException(status_code=409, detail="Esse membro já está na equipe com essa classificação.")
    membro = BbEquipeMembro(
        responsavel_user_id=payload.responsavel_user_id,
        membro_user_id=payload.membro_user_id,
        classificacao=payload.classificacao.strip(),
        ativo=payload.ativo if payload.ativo is not None else True,
    )
    db.add(membro)
    db.commit()
    db.refresh(membro)
    return {"id": membro.id, "ok": True}


@router.delete("/config/equipe/{membro_id}", summary="Remove membro da equipe")
def remover_membro_equipe(
    membro_id: int,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    _require_gestao(current_user)
    membro = db.get(BbEquipeMembro, membro_id)
    if membro is None:
        raise HTTPException(status_code=404, detail="Membro não encontrado.")
    db.delete(membro)
    db.commit()
    return {"ok": True}
