"""Endpoints do Balanceador de Agenda.

Leitura: diagnóstico de carga + matriz de redistribuição + detalhe por subtipo
(snapshot perf_l1_tarefa) + fila AO VIVO do L1. Reusa o gate por time do Minha
Equipe.

Escrita REAL (2026-07-02): `/reatribuir` dispara um job server-backed que troca
responsável+executante no L1 (PATCH normal; tarefa de Workflow vai pro bucket
`workflow_bloqueadas`). Progresso via `/reatribuir/status`. Ver
app/services/performance/reatribuir_job.py.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1.endpoints.performance import require_team_access
from app.core.auth import get_current_user
from app.core.dependencies import get_db
from app.models.legal_one import LegalOneUser
from app.services.performance.balanceador import BalanceadorService

router = APIRouter(prefix="/balanceador", tags=["Balanceador de Agenda"])
_team = Depends(require_team_access)


@router.get("/diagnostico", summary="Carga pendente por colaborador (atrasado/fatal hoje/futuro)", dependencies=[_team])
def diagnostico(team: str = Query(...), db: Session = Depends(get_db)):
    return {"colaboradores": BalanceadorService(db).diagnostico(team)}


@router.get("/redistribuir", summary="Matriz subtipo × colaborador dos escolhidos", dependencies=[_team])
def redistribuir(
    team: str = Query(...),
    pessoas: str = Query(..., description="ids separados por vírgula"),
    dias: int = Query(0, description="janela futura em dias; 0 = tudo"),
    db: Session = Depends(get_db),
):
    ids = [int(x) for x in pessoas.split(",") if x.strip().isdigit()]
    return {"matriz": BalanceadorService(db).redistribuir_matriz(team, ids, dias)}


@router.get("/tarefas", summary="Tarefas individuais de um (colaborador, subtipo)", dependencies=[_team])
def tarefas(
    team: str = Query(...),
    pessoa_id: int = Query(...),
    subtipo: str = Query(...),
    dias: int = Query(0),
    db: Session = Depends(get_db),
):
    return {"tarefas": BalanceadorService(db).redistribuir_tarefas(team, pessoa_id, subtipo, dias)}


@router.get("/descricoes", summary="Descrição (assunto) ao vivo do L1 por task ids", dependencies=[_team])
def descricoes(team: str = Query(...), ids: str = Query(...), db: Session = Depends(get_db)):
    idlist = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    return {"descricoes": BalanceadorService(db).descricoes(idlist)}


@router.get("/live-pessoa", summary="Pendentes AO VIVO do L1 de uma pessoa (matriz + detalhe)", dependencies=[_team])
def live_pessoa(
    team: str = Query(...),
    pessoa_id: int = Query(...),
    dias: int = Query(0),
    incluir_atrasadas: bool = Query(True),
    db: Session = Depends(get_db),
):
    return BalanceadorService(db).live_pessoa(team, pessoa_id, dias, incluir_atrasadas)


@router.get("/usuarios", summary="Destinos da fila: setor primeiro + busca externa no L1", dependencies=[_team])
def usuarios(team: str = Query(...), busca: str = Query(""), db: Session = Depends(get_db)):
    return {"usuarios": BalanceadorService(db).buscar_usuarios(team, busca)}


@router.get("/fila-pref", summary="Destinos recorrentes da fila p/ (origem, subtipo)", dependencies=[_team])
def fila_pref_sugestoes(
    team: str = Query(...),
    origem_pessoa_id: int = Query(...),
    subtipo: str = Query(...),
    db: Session = Depends(get_db),
):
    return {"sugestoes": BalanceadorService(db).sugestoes_fila(team, origem_pessoa_id, subtipo)}


class FilaPrefReq(BaseModel):
    origem_pessoa_id: int
    subtipo: str
    alvos: list  # [{id, nome}]


@router.post("/fila-pref", summary="Aprende os destinos da fila (origem, subtipo)", dependencies=[_team])
def fila_pref_registrar(team: str = Query(...), req: FilaPrefReq = ..., db: Session = Depends(get_db)):
    return BalanceadorService(db).registrar_fila_pref(team, req.origem_pessoa_id, req.subtipo, req.alvos)


class RegistrarLogReq(BaseModel):
    movimentos: list


@router.post("/log", summary="Registra o log de uma redistribuição (aba Relatórios)", dependencies=[_team])
def registrar_log(
    team: str = Query(...),
    req: RegistrarLogReq = ...,
    current_user: LegalOneUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return BalanceadorService(db).registrar_log(team, current_user, req.movimentos)


@router.get("/logs", summary="Lista os logs de redistribuição do time", dependencies=[_team])
def listar_logs(team: str = Query(...), db: Session = Depends(get_db)):
    return {"logs": BalanceadorService(db).listar_logs(team)}


# ── Reatribuição EM LOTE (escrita REAL no L1) — job server-backed com progresso ──


class ReatribuirItem(BaseModel):
    task_id: int
    to_id: int | None = None
    to_nome: str | None = None


class ReatribuirReq(BaseModel):
    itens: list[ReatribuirItem]
    movimentos: list = []  # move-level (from/to/subtipo/qtd), só pra auditoria


@router.post(
    "/reatribuir",
    summary="Dispara a reatribuição EM LOTE das tarefas (PATCH normal; Workflow vai pro bucket bloqueado)",
    dependencies=[_team],
)
def reatribuir(
    team: str = Query(...),
    dry_run: bool = Query(False, description="só lê participantes (prova acesso, conta) — não grava"),
    req: ReatribuirReq = ...,
    current_user: LegalOneUser = Depends(get_current_user),
):
    from app.services.performance import reatribuir_job

    itens = [i.model_dump() for i in req.itens]
    job_id = reatribuir_job.iniciar(team, itens, req.movimentos, dry_run, current_user)
    return {"job_id": job_id, "status": reatribuir_job.status(job_id)}


@router.get("/reatribuir/status", summary="Progresso do job de reatribuição", dependencies=[_team])
def reatribuir_status(team: str = Query(...), job_id: str = Query(...)):
    from app.services.performance import reatribuir_job

    st = reatribuir_job.status(job_id)
    if st is None:
        return {"status": "not_found"}
    return st


@router.post("/reatribuir/abort", summary="Aborta o job de reatribuição", dependencies=[_team])
def reatribuir_abort(team: str = Query(...), job_id: str = Query(...)):
    from app.services.performance import reatribuir_job

    return {"ok": reatribuir_job.solicitar_abort(job_id)}
