"""Endpoints do Fluxo Embargos à Execução (aba da Controladoria no Minha Equipe).

Gate: o mesmo do Minha Equipe para o time `bb-cadastro` (Controladoria) —
admin, ou permissão do menu + o time liberado na árvore. Ver
docs/embargos-execucao-plano.md.
"""
from __future__ import annotations

import logging
import threading
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.dependencies import get_db
from app.models.embargos_execucao import ORIGEM_PLANILHA
from app.models.legal_one import LegalOneUser
from app.services.embargos_execucao import importacao, monitor, relatorio_l1, service, tarefas, worker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/embargos-execucao", tags=["Embargos à Execução"])

_MAX_UPLOAD = 15 * 1024 * 1024


def _gate(current_user: LegalOneUser = Depends(get_current_user)) -> LegalOneUser:
    from app.api.v1.endpoints.performance import _exigir_acesso_ao_time

    _exigir_acesso_ao_time(current_user, service.TEAM_KEY)
    return current_user


def _uid(user: LegalOneUser) -> Optional[int]:
    return getattr(user, "id", None)


def _erro(exc: Exception):
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _l1():
    from app.services.legal_one_client import LegalOneApiClient

    try:
        return LegalOneApiClient()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Legal One indisponível: {exc}")


class ParametrosIn(BaseModel):
    corte_relatorio: Optional[date] = None
    janela_dias_uteis: Optional[int] = None
    intervalo_dias_uteis: Optional[int] = None
    teto_dias: Optional[int] = None
    aviso_emails: Optional[list[str]] = None


class ManualIn(BaseModel):
    pasta: Optional[str] = None
    cnj: Optional[str] = None
    npj: Optional[str] = None
    data_ajuizamento: date


class AjusteIn(BaseModel):
    dias_uteis_janela: Optional[int] = None
    anotacao: Optional[str] = None


class DecisaoIn(BaseModel):
    decisao: str  # CONFIRMADO | RECUSADO


class EncerrarIn(BaseModel):
    motivo: Optional[str] = None


class TemplateIn(BaseModel):
    nome: str
    ativo: bool = True
    ordem: int = 0
    subtipo_id: int
    responsavel_modo: str = "FIXO"
    responsavel_contact_id: Optional[int] = None
    prazo_dias_uteis: int = 5
    prioridade: str = "Normal"
    descricao_template: str
    observacoes_template: Optional[str] = None


@router.get("", summary="Board: execuções monitoradas com KPIs por estado")
def listar(
    estado: Optional[str] = Query(None),
    cliente: Optional[str] = Query(None),
    partes_status: Optional[str] = Query(None),
    busca: Optional[str] = Query(None, description="Pasta, CNJ, NPJ ou responsável"),
    ordenar: str = Query("proxima_consulta"),
    direcao: str = Query("asc"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _user: LegalOneUser = Depends(_gate),
):
    dados = service.listar(
        db, estado=estado, cliente=cliente, partes_status=partes_status, busca=busca,
        ordenar=ordenar, direcao=direcao, limit=limit, offset=offset,
    )
    dados["relatorio"] = relatorio_l1.status_relatorio()
    dados["partes"] = worker.status_partes()
    return dados


@router.get("/parametros", summary="Parâmetros do fluxo (corte, janela, intervalo, teto, aviso)")
def ler_parametros(_user: LegalOneUser = Depends(_gate)):
    return service.parametros()


@router.put("/parametros", summary="Ajusta os parâmetros do fluxo")
def salvar_parametros(body: ParametrosIn, _user: LegalOneUser = Depends(_gate)):
    try:
        return service.salvar_parametros(body.model_dump(exclude_none=True))
    except ValueError as exc:
        _erro(exc)


# ── Relatório do L1 ──────────────────────────────────────────────────
@router.get("/relatorio/status", summary="Estado da geração/importação do relatório do L1")
def relatorio_status(_user: LegalOneUser = Depends(_gate)):
    return relatorio_l1.status_relatorio()


@router.post("/relatorio/gerar", summary="Gera o relatório no L1 agora e importa os casos novos")
def relatorio_gerar(user: LegalOneUser = Depends(_gate)):
    if relatorio_l1.em_andamento():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="O relatório já está sendo gerado.")
    relatorio_l1._status("na fila", running=True)
    uid = _uid(user)

    def _rodar():
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            relatorio_l1.gerar_e_importar(db, user_id=uid)
        finally:
            db.close()

    threading.Thread(target=_rodar, name="embargos-relatorio", daemon=True).start()
    return {"ok": True, "mensagem": "Geração disparada — roda no servidor, pode sair da tela."}


# ── Partes no portal do BB ───────────────────────────────────────────
@router.get("/partes/status", summary="Estado da coleta de partes no portal do BB")
def partes_status_endpoint(_user: LegalOneUser = Depends(_gate)):
    return worker.status_partes()


@router.post("/partes/coletar", summary="Dispara agora a coleta das partes pendentes no portal do BB")
def partes_coletar(_user: LegalOneUser = Depends(_gate)):
    if not worker.disparar_partes_manual():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A coleta de partes já está rodando.")
    return {"ok": True, "mensagem": "Coleta disparada no portal do BB — roda no servidor."}


# ── Entrada manual / planilha ────────────────────────────────────────
@router.post("/importar", summary="Importa planilha (legado) com pasta/CNJ e data do ajuizamento")
async def importar(
    arquivo: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: LegalOneUser = Depends(_gate),
):
    conteudo = await arquivo.read()
    if len(conteudo) > _MAX_UPLOAD:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Arquivo acima de 15 MB.")
    try:
        return importacao.importar_bytes(
            db, conteudo, arquivo.filename or "planilha.xlsx", origem=ORIGEM_PLANILHA, user_id=_uid(user),
        )
    except ValueError as exc:
        _erro(exc)


@router.post("/manual", summary="Inclui uma execução à mão")
def manual(body: ManualIn, db: Session = Depends(get_db), user: LegalOneUser = Depends(_gate)):
    try:
        return service.adicionar_manual(
            db, pasta=body.pasta, cnj=body.cnj, npj=body.npj,
            data_ajuizamento=body.data_ajuizamento, user_id=_uid(user),
        )
    except ValueError as exc:
        _erro(exc)


# ── Templates das tarefas do incidente ───────────────────────────────
@router.get("/templates", summary="Templates das tarefas disparadas no incidente")
def templates_listar(db: Session = Depends(get_db), _user: LegalOneUser = Depends(_gate)):
    return tarefas.listar_templates(db)


@router.post("/templates", summary="Cria template de tarefa")
def templates_criar(body: TemplateIn, db: Session = Depends(get_db), user: LegalOneUser = Depends(_gate)):
    try:
        return tarefas.salvar_template(db, body.model_dump(), user_id=_uid(user))
    except (LookupError, ValueError) as exc:
        _erro(exc)


@router.put("/templates/{template_id}", summary="Altera template de tarefa")
def templates_alterar(template_id: int, body: TemplateIn, db: Session = Depends(get_db),
                      user: LegalOneUser = Depends(_gate)):
    try:
        return tarefas.salvar_template(db, body.model_dump(), template_id=template_id, user_id=_uid(user))
    except (LookupError, ValueError) as exc:
        _erro(exc)


@router.delete("/templates/{template_id}", summary="Exclui template de tarefa")
def templates_excluir(template_id: int, db: Session = Depends(get_db), _user: LegalOneUser = Depends(_gate)):
    try:
        tarefas.excluir_template(db, template_id)
    except LookupError as exc:
        _erro(exc)
    return {"ok": True}


# ── Execução ─────────────────────────────────────────────────────────
@router.get("/{execucao_id}", summary="Detalhe da execução: partes, candidatos, tarefas e trilha")
def detalhe(execucao_id: int, db: Session = Depends(get_db), _user: LegalOneUser = Depends(_gate)):
    dados = service.detalhe(db, execucao_id)
    if dados is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execução não encontrada.")
    return dados


@router.patch("/{execucao_id}", summary="Ajusta janela (15/20/25 dias úteis) e anotação")
def ajustar(execucao_id: int, body: AjusteIn, db: Session = Depends(get_db),
            user: LegalOneUser = Depends(_gate)):
    try:
        service.ajustar(db, execucao_id, dias_uteis_janela=body.dias_uteis_janela,
                        anotacao=body.anotacao, user_id=_uid(user))
    except (LookupError, ValueError) as exc:
        _erro(exc)
    return service.detalhe(db, execucao_id)


@router.post("/{execucao_id}/consultar", summary="Antecipa a consulta ao tribunal e roda agora")
def consultar(execucao_id: int, db: Session = Depends(get_db), user: LegalOneUser = Depends(_gate)):
    try:
        service.consultar_agora(db, execucao_id, user_id=_uid(user))
    except (LookupError, ValueError) as exc:
        _erro(exc)

    def _rodar():
        from app.db.session import SessionLocal

        s = SessionLocal()
        try:
            monitor.processar_um(s, execucao_id)
        finally:
            s.close()

    threading.Thread(target=_rodar, name=f"embargos-consulta-{execucao_id}", daemon=True).start()
    return {"ok": True, "mensagem": "Consulta em andamento — atualize em alguns instantes."}


@router.post("/{execucao_id}/partes", summary="Coleta agora as partes desta execução no portal do BB")
def recoletar(execucao_id: int, db: Session = Depends(get_db), user: LegalOneUser = Depends(_gate)):
    try:
        service.recoletar_partes(db, execucao_id, user_id=_uid(user))
    except (LookupError, ValueError) as exc:
        _erro(exc)
    disparou = worker.disparar_partes_manual(execucao_id)
    dados = service.detalhe(db, execucao_id)
    dados["partes_disparada"] = disparou
    return dados


@router.post("/{execucao_id}/candidatos/{candidato_id}/decisao",
             summary="Confirma ou recusa o vínculo do candidato com a execução")
def decidir(execucao_id: int, candidato_id: int, body: DecisaoIn,
            db: Session = Depends(get_db), user: LegalOneUser = Depends(_gate)):
    try:
        service.decidir_candidato(db, execucao_id, candidato_id, body.decisao.upper(), user_id=_uid(user))
    except (LookupError, ValueError) as exc:
        _erro(exc)
    return service.detalhe(db, execucao_id)


@router.get("/{execucao_id}/tarefas/previa",
            summary="Localiza o incidente no L1 e mostra as tarefas que os templates vão criar")
def tarefas_previa(execucao_id: int, db: Session = Depends(get_db), _user: LegalOneUser = Depends(_gate)):
    try:
        return tarefas.previa(db, execucao_id, _l1())
    except (LookupError, ValueError) as exc:
        _erro(exc)


@router.post("/{execucao_id}/tarefas/disparar", summary="Cria no incidente as tarefas dos templates ativos")
def tarefas_disparar(execucao_id: int, db: Session = Depends(get_db), user: LegalOneUser = Depends(_gate)):
    try:
        resultado = tarefas.disparar(db, execucao_id, _l1(), user_id=_uid(user))
    except (LookupError, ValueError) as exc:
        _erro(exc)
    dados = service.detalhe(db, execucao_id)
    dados["resultado_disparo"] = resultado
    return dados


@router.post("/{execucao_id}/encerrar", summary="Tira a execução do fluxo")
def encerrar(execucao_id: int, body: EncerrarIn, db: Session = Depends(get_db),
             user: LegalOneUser = Depends(_gate)):
    try:
        service.encerrar(db, execucao_id, motivo=body.motivo, user_id=_uid(user))
    except LookupError as exc:
        _erro(exc)
    return service.detalhe(db, execucao_id)


@router.post("/{execucao_id}/reabrir", summary="Devolve a execução à agenda")
def reabrir(execucao_id: int, db: Session = Depends(get_db), user: LegalOneUser = Depends(_gate)):
    try:
        service.reabrir(db, execucao_id, user_id=_uid(user))
    except LookupError as exc:
        _erro(exc)
    return service.detalhe(db, execucao_id)
