"""Endpoints do módulo "Minha Equipe" (Performance de Equipes).

Restrito a administradores — é monitoramento de desempenho de colaboradores.
Lê das tabelas perf* (populadas pelo seed/ingestão). Ver
`docs/performance-equipes-plano.md`.
"""

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.dependencies import get_db
from app.models.legal_one import LegalOneUser
from app.services.performance import relatorios as rel_jobs
from app.services.performance.report import build_individual_pdf, build_sector_pdf
from app.services.performance.service import PerformanceService
from app.services.performance.teams import TEAM_KEYS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/performance", tags=["Performance (Minha Equipe)"])


def _user_teams(u) -> list:
    return [x for x in (getattr(u, "minha_equipe_equipes", None) or "").split(",") if x]


def _require_minha_equipe(current_user: LegalOneUser = Depends(get_current_user)) -> LegalOneUser:
    """Acesso ao módulo (qualquer time): admin OU permissão do menu."""
    if getattr(current_user, "role", "user") == "admin":
        return current_user
    if not getattr(current_user, "can_use_minha_equipe", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem permissão para o Minha Equipe.")
    return current_user


def require_team_access(
    team: str = Query(..., description="Slug do time (bb-reu, master-reu, ...)"),
    current_user: LegalOneUser = Depends(get_current_user),
) -> str:
    """Gate por time: admin vê todos; demais precisam do time liberado na árvore."""
    if team not in TEAM_KEYS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Time inexistente.")
    if getattr(current_user, "role", "user") == "admin":
        return team
    if not getattr(current_user, "can_use_minha_equipe", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem permissão para o Minha Equipe.")
    if team not in _user_teams(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem acesso a este time.")
    return team


# Alias do gate de módulo (relatórios-job e sync não são por time).
_require_admin = _require_minha_equipe
_admin = Depends(_require_admin)
_team = Depends(require_team_access)


def _require_admin_only(current_user: LegalOneUser = Depends(get_current_user)) -> LegalOneUser:
    """Restrito a admin (sobrou só pra rotinas globais, ex.: cancel-whitelist)."""
    if getattr(current_user, "role", "user") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Restrito a administradores.")
    return current_user


_adminonly = Depends(_require_admin_only)


def _exigir_acesso_ao_time(current_user: LegalOneUser, team: str) -> None:
    """Mesma regra do require_team_access, mas pra quando o time não vem na query
    (vem do corpo ou da própria pessoa). Admin passa; demais precisam do menu do
    módulo + o time liberado na árvore."""
    if getattr(current_user, "role", "user") == "admin":
        return  # admin passa mesmo com equipe legada fora do catálogo
    if team not in TEAM_KEYS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Time inexistente.")
    if not getattr(current_user, "can_use_minha_equipe", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem permissão para o Minha Equipe.")
    if team not in _user_teams(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem acesso a este time.")


def _equipe_da_pessoa(db: Session, pessoa_id: int) -> Optional[str]:
    from sqlalchemy import text

    row = db.execute(text("SELECT equipe FROM perf_pessoa WHERE id = :id"), {"id": pessoa_id}).fetchone()
    return row[0] if row else None


@router.get("/equipe", summary="Lista o time com métricas + KPIs", dependencies=[_team])
def equipe(
    team: str = Query(...),
    days: int = Query(30, ge=1, le=365),
    cargo: Optional[str] = Query(None, description="Filtra por cargo"),
    db: Session = Depends(get_db),
):
    return PerformanceService(db).equipe(days=days, cargo=cargo or None, team=team)


@router.get("/cargos", summary="Cargos distintos do time (filtro)", dependencies=[_team])
def cargos(team: str = Query(...), db: Session = Depends(get_db)):
    return {"cargos": PerformanceService(db).cargos(team=team)}


@router.get("/pessoa/{pessoa_id}", summary="Detalhe de uma pessoa (mix + ritmo/ócio)", dependencies=[_team])
def pessoa(
    pessoa_id: int,
    team: str = Query(...),
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    out = PerformanceService(db).pessoa_detalhe(pessoa_id, days=days)
    if out is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pessoa não encontrada.")
    return out


@router.get(
    "/pessoa/{pessoa_id}/atrasadas-por-tipo",
    summary="Quebra por subtipo do pool de uma pessoa (pizza do painel)",
    dependencies=[_team],
)
def pessoa_atrasadas_por_tipo(
    pessoa_id: int,
    team: str = Query(...),
    escopo: str = Query("atrasado", pattern="^(atrasado|pendente)$"),
    db: Session = Depends(get_db),
):
    out = PerformanceService(db).atrasadas_por_tipo(pessoa_id, escopo=escopo)
    if out is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pessoa não encontrada.")
    return out


@router.get("/tipos", summary="Mapa de impacto por subtipo (do time)", dependencies=[_team])
def tipos(team: str = Query(...), days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    return {"tipos": PerformanceService(db).tipos(days=days, team=team)}


@router.get("/dashboard", summary="Painel do time: vazão, pool/atrasado, jornada, top tipos", dependencies=[_team])
def dashboard(team: str = Query(...), days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    return PerformanceService(db).dashboard(days=days, team=team)


@router.get("/subtipo-detalhe", summary="Detalhe (capacity) de um subtipo do board", dependencies=[_team])
def subtipo_detalhe(
    team: str = Query(...),
    subtipo: str = Query(...),
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    return PerformanceService(db).subtipo_setor_detalhe(subtipo, days=days, team=team)


@router.get("/duplicadas", summary="Preview de duplicadas (mesma pasta + subtipo) de um tipo", dependencies=[_team])
def duplicadas(team: str = Query(...), subtipo: str = Query(...), db: Session = Depends(get_db)):
    return PerformanceService(db).duplicadas_subtipo(subtipo, team=team)


@router.get("/board-tarefas", summary="Curadoria do board (subtipos exibidos)", dependencies=[_team])
def board_tarefas(team: str = Query(...), db: Session = Depends(get_db)):
    return PerformanceService(db).board_listar(team)


@router.get("/board-tarefas/catalogo", summary="Subtipos disponíveis pra adicionar ao board", dependencies=[_team])
def board_catalogo(team: str = Query(...), busca: str = Query(""), db: Session = Depends(get_db)):
    return {"subtipos": PerformanceService(db).board_catalogo(team, busca)}


class BoardTarefaReq(BaseModel):
    subtipo: str


@router.post("/board-tarefas", summary="Adiciona um subtipo ao board", dependencies=[_team])
def board_adicionar(team: str = Query(...), req: BoardTarefaReq = ..., db: Session = Depends(get_db)):
    return PerformanceService(db).board_adicionar(team, req.subtipo)


@router.delete("/board-tarefas", summary="Remove um subtipo do board", dependencies=[_team])
def board_remover(team: str = Query(...), subtipo: str = Query(...), db: Session = Depends(get_db)):
    return PerformanceService(db).board_remover(team, subtipo)


class CancelarDupReq(BaseModel):
    subtipo: str


@router.post("/duplicadas/cancelar", summary="Inicia cancelamento de duplicadas (varredura LIVE + cancela)", dependencies=[_team])
def cancelar_duplicadas(team: str = Query(...), req: CancelarDupReq = ...):
    from app.services.performance import cancel_duplicadas

    return {"job_id": cancel_duplicadas.iniciar(team, req.subtipo)}


@router.post("/duplicadas/cancelar/abort", summary="Pede pra parar o lote de cancelamento", dependencies=[_team])
def cancelar_duplicadas_abort(team: str = Query(...), job_id: str = Query(...)):
    from app.services.performance import cancel_duplicadas

    return {"ok": cancel_duplicadas.solicitar_abort(job_id)}


@router.get("/duplicadas/cancelar/status", summary="Progresso do lote de cancelamento", dependencies=[_team])
def cancelar_duplicadas_status(team: str = Query(...), job_id: str = Query(...)):
    from app.services.performance import cancel_duplicadas

    st = cancel_duplicadas.status(job_id)
    if not st:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job não encontrado.")
    return st


# ── Whitelist + rotina em massa de cancelamento de duplicadas (admin) ──
class WhitelistReq(BaseModel):
    subtipo: str
    ativo: bool | None = None


@router.get("/cancel-whitelist", summary="Subtipos liberados pro cancelamento automático", dependencies=[_admin])
def cancel_whitelist(db: Session = Depends(get_db)):
    from app.services.performance import cancel_duplicadas

    return {"whitelist": cancel_duplicadas.whitelist_listar(db)}


@router.get("/cancel-whitelist/catalogo", summary="Catálogo global de subtipos (combobox)", dependencies=[_admin])
def cancel_whitelist_catalogo(busca: str = Query(""), db: Session = Depends(get_db)):
    from app.services.performance import cancel_duplicadas

    return {"subtipos": cancel_duplicadas.subtipos_catalogo(db, busca)}


@router.post("/cancel-whitelist", summary="Adiciona subtipo à whitelist", dependencies=[_admin])
def cancel_whitelist_add(req: WhitelistReq, db: Session = Depends(get_db)):
    from app.services.performance import cancel_duplicadas

    return {"whitelist": cancel_duplicadas.whitelist_add(db, req.subtipo, "admin")}


@router.post("/cancel-whitelist/toggle", summary="Liga/desliga subtipo da whitelist", dependencies=[_admin])
def cancel_whitelist_toggle(req: WhitelistReq, db: Session = Depends(get_db)):
    from app.services.performance import cancel_duplicadas

    return {"whitelist": cancel_duplicadas.whitelist_toggle(db, req.subtipo, bool(req.ativo))}


@router.delete("/cancel-whitelist", summary="Remove subtipo da whitelist", dependencies=[_admin])
def cancel_whitelist_remove(subtipo: str = Query(...), db: Session = Depends(get_db)):
    from app.services.performance import cancel_duplicadas

    return {"whitelist": cancel_duplicadas.whitelist_remover(db, subtipo)}


@router.get("/cancel-massa/logs", summary="Auditoria das rotinas de cancelamento em massa", dependencies=[_admin])
def cancel_massa_logs(db: Session = Depends(get_db)):
    from app.services.performance import cancel_duplicadas

    return {"logs": cancel_duplicadas.massa_logs(db)}


@router.post("/cancel-massa/run", summary="Dispara o cancelamento em massa agora (background)", dependencies=[_admin])
def cancel_massa_run(dry_run: bool = Query(False)):
    import threading

    from app.db.session import SessionLocal
    from app.services.performance import cancel_duplicadas

    def _bg():
        d = SessionLocal()
        try:
            cancel_duplicadas.cancelar_em_massa(d, dry_run=dry_run, origem="manual")
        finally:
            d.close()

    threading.Thread(target=_bg, daemon=True).start()
    return {"started": True, "dry_run": dry_run}


@router.get("/export", summary="Exporta xlsx de um recorte do time", dependencies=[_team])
def export(
    team: str = Query(...),
    escopo: str = Query("atrasado", pattern="^(atrasado|pendente|concluido)$"),
    pessoa_id: Optional[int] = Query(None),
    subtipo: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    data = PerformanceService(db).export_xlsx(escopo=escopo, pessoa_id=pessoa_id, subtipo=subtipo, days=days, team=team)
    fname = f"minha-equipe-{team}-{escopo}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/relatorio-setor", summary="Relatório PDF do time (Sonnet + fallback)", dependencies=[_team])
def relatorio_setor(team: str = Query(...), days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    pdf = build_sector_pdf(db, days=days, team=team)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="relatorio-{team}.pdf"'},
    )


@router.get("/pessoa/{pessoa_id}/relatorio", summary="Relatório PDF individual (raio-x + intervenções)", dependencies=[_team])
def relatorio_pessoa(pessoa_id: int, team: str = Query(...), days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    pdf = build_individual_pdf(db, pessoa_id, days=days)
    if pdf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pessoa não encontrada.")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="raio-x-pessoa-{pessoa_id}.pdf"'},
    )


# ── Relatórios como JOB persistente (sobrevivem à navegação) ──────────────
class CriarRelatorioReq(BaseModel):
    tipo: str = "setor"  # 'setor' | 'pessoa'
    team: str = "bb-reu"
    days: int = 30
    pessoa_id: Optional[int] = None


@router.post("/relatorios", summary="Dispara a geração de um relatório (job persistente)")
def criar_relatorio(
    req: CriarRelatorioReq,
    background: BackgroundTasks,
    current_user: LegalOneUser = Depends(_require_minha_equipe),
    db: Session = Depends(get_db),
):
    if req.team not in TEAM_KEYS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Time inexistente.")
    if getattr(current_user, "role", "user") != "admin" and req.team not in _user_teams(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem acesso a este time.")
    tipo = "pessoa" if req.tipo == "pessoa" else "setor"
    rid, label = rel_jobs.criar(
        db, tipo=tipo, days=req.days, pessoa_id=req.pessoa_id, user_id=current_user.id, team=req.team
    )
    background.add_task(rel_jobs.gerar, rid)
    return {"id": rid, "label": label, "status": "processando"}


@router.get("/relatorios", summary="Lista os relatórios do usuário")
def listar_relatorios(
    current_user: LegalOneUser = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    return {"items": rel_jobs.listar(db, current_user.id)}


@router.get("/relatorios/{relatorio_id}/download", summary="Baixa o PDF de um relatório pronto")
def baixar_relatorio(
    relatorio_id: int,
    current_user: LegalOneUser = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    pdf, _label = rel_jobs.get_pdf(db, relatorio_id, current_user.id)
    if pdf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Relatório não está pronto ou não encontrado.")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="relatorio-minha-equipe-{relatorio_id}.pdf"'},
    )


# ── Ingestão dos dados (download do relatório do L1) ──────────────────────
@router.get("/sync", summary="Último sync da ingestão (download do relatório do L1)", dependencies=[_admin])
def sync_status(db: Session = Depends(get_db)):
    from app.services.performance.report_ingest import get_last_sync, ja_sincronizou_hoje

    return {"last_sync": get_last_sync(), "ja_sincronizou_hoje": ja_sincronizou_hoje()}


def _run_sync_bg() -> None:
    from app.db.session import SessionLocal
    from app.services.onerequest._concurrency import single_worker_lock
    from app.services.performance.ingest_worker import _LOCK_KEY
    from app.services.performance.report_ingest import baixar_e_ingerir, gerar_e_ingerir

    # MESMO advisory lock do scheduler: sem ele, cliques repetidos no
    # "Atualizar agora" (ou o botão + o job das 9h juntos) rodavam ingestões
    # CONCORRENTES — cada uma deleta o que enxerga e insere a própria cópia,
    # triplicando o snapshot (caso real 22/07: 3 runs às 08:58/09:00/09:00,
    # 641k linhas pra 213k tarefas; painel contava 111 atrasadas onde eram 37).
    with single_worker_lock(_LOCK_KEY) as got:
        if not got:
            logger.info("Minha Equipe: ingestão manual pulada — já há uma rodando.")
            return
        db = SessionLocal()
        try:
            # FORÇA um relatório FRESCO (gerar_e_ingerir), não o último que o L1
            # já tinha. `baixar_e_ingerir` pega o relatório existente — que pode
            # ter sido gerado horas antes, com prazos/status defasados: caso real
            # 22/07, a Hellen aparecia com 20 "fatais hoje" que no L1 já tinham
            # sido dilatadas pra 23-27/07 (e 1 já concluída), porque o relatório
            # baixado era o das 13h. Gerar na hora reflete o estado ATUAL do L1.
            # Fallback pro download do existente se a geração falhar (ex.: runner
            # indisponível) — melhor um dado um pouco velho que dado nenhum.
            res = gerar_e_ingerir(db)
            if not res.get("ok"):
                logger.warning(
                    "Minha Equipe: geração fresca falhou (%s) — baixando o relatório existente.",
                    res.get("motivo"),
                )
                baixar_e_ingerir(db, force=True)
        except Exception:  # noqa: BLE001
            logger.exception("Minha Equipe: falha na ingestão manual.")
        finally:
            db.close()


@router.post("/sync", summary="Gera um relatório FRESCO no L1 e ingere (reflete o estado atual)", dependencies=[_admin])
def sync_now(background: BackgroundTasks):
    background.add_task(_run_sync_bg)
    return {
        "ok": True,
        "mensagem": (
            "Atualização disparada — gerando um relatório novo no L1 e reingerindo. "
            "Leva alguns minutos (o L1 monta o relatório do zero); o painel atualiza ao concluir."
        ),
    }


# ── Manutenção do roster (editor de equipe) ────────────────────────────────
# Antes era admin-only; agora qualquer colaborador com o TIME liberado na
# árvore do Minha Equipe pode ajustar a própria equipe (decisão do operador,
# 2026-07-21). O escopo continua POR TIME: mexer em pessoa de outro time (ou
# mover pessoa PARA outro time) exige acesso àquele time também.
@router.get("/roster", summary="Roster editável do time (manutenção)", dependencies=[_team])
def roster(team: str = Query(...), db: Session = Depends(get_db)):
    return {"pessoas": PerformanceService(db).roster(team)}


class RosterUpdateReq(BaseModel):
    cargo: Optional[str] = None
    equipe: Optional[str] = None
    is_supervisor: Optional[bool] = None
    ativo: Optional[bool] = None


@router.patch("/roster/{pessoa_id}", summary="Atualiza uma pessoa do roster")
def update_roster(
    pessoa_id: int,
    req: RosterUpdateReq,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(get_current_user),
):
    if req.equipe is not None and req.equipe not in TEAM_KEYS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Time inexistente.")
    equipe_atual = _equipe_da_pessoa(db, pessoa_id)
    if equipe_atual is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pessoa não encontrada.")
    _exigir_acesso_ao_time(current_user, equipe_atual)
    if req.equipe is not None and req.equipe != equipe_atual:
        # Mover pra OUTRO time exige acesso ao destino também.
        _exigir_acesso_ao_time(current_user, req.equipe)
    out = PerformanceService(db).update_pessoa(
        pessoa_id, cargo=req.cargo, equipe=req.equipe,
        is_supervisor=req.is_supervisor, ativo=req.ativo,
    )
    if out is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pessoa não encontrada.")
    return out


@router.get("/roster/candidatos", summary="Candidatos a adicionar ao time (usuários do L1)", dependencies=[_team])
def roster_candidatos(team: str = Query(...), busca: Optional[str] = Query(None), db: Session = Depends(get_db)):
    return {"candidatos": PerformanceService(db).candidatos(team, busca=busca)}


class AddPessoaReq(BaseModel):
    nome: str
    team: str


@router.post("/roster/adicionar", summary="Adiciona uma pessoa ao time")
def roster_adicionar(
    req: AddPessoaReq,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(get_current_user),
):
    if req.team not in TEAM_KEYS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Time inexistente.")
    _exigir_acesso_ao_time(current_user, req.team)
    out = PerformanceService(db).adicionar_pessoa(req.nome, req.team)
    if out is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nome inválido.")
    return out


@router.delete("/roster/{pessoa_id}", summary="Exclui a pessoa do sistema (saiu do escritório)")
def roster_excluir(
    pessoa_id: int,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(get_current_user),
):
    equipe_atual = _equipe_da_pessoa(db, pessoa_id)
    if equipe_atual is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pessoa não encontrada.")
    _exigir_acesso_ao_time(current_user, equipe_atual)
    out = PerformanceService(db).excluir_pessoa(pessoa_id)
    if out is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pessoa não encontrada.")
    return out
