import logging
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.core import auth
from app.core.dependencies import get_batch_task_creation_service, get_db
from app.models.legal_one import LegalOneTaskType, LegalOneUser, SavedFilter
from app.models.task_group import TaskParentGroup
from app.services.batch_task_creation_service import BatchTaskCreationService
from app.services.metadata_sync_service import run_metadata_sync_job

router = APIRouter()
me_router = APIRouter()
logger = logging.getLogger(__name__)


class TaskSubTypeSchema(BaseModel):
    id: int
    name: str
    squad_ids: List[int]


class TaskTypeGroupSchema(BaseModel):
    parent_id: int
    parent_name: str
    sub_types: List[TaskSubTypeSchema]


class TaskParentGroupUpdatePayload(BaseModel):
    name: str


@router.post("/sync-metadata", status_code=202, summary="Sincronizar metadados do Legal One", tags=["Admin"])
def sync_metadata(
    background_tasks: BackgroundTasks,
):
    logger.info("Endpoint /sync-metadata chamado. Adicionando tarefa em background.")
    background_tasks.add_task(run_metadata_sync_job)
    return {"message": "Processo de sincronizacao de metadados do Legal One iniciado em segundo plano."}


@router.post(
    "/sync-caches",
    status_code=202,
    summary="Pré-carregar caches de escritórios e processos",
    tags=["Admin"],
)
def sync_caches(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Dispara em background o pre-warm dos caches:
    1. Índice de processos por escritório (office_lawsuit_index)
    2. Cache de dados do processo (lawsuit_cache: CNJ, creationDate, officeId)

    Evita que a primeira busca de publicações seja lenta e economiza
    chamadas à API do Legal One.
    """
    from app.models.legal_one import LegalOneOffice

    offices = (
        db.query(LegalOneOffice)
        .filter(LegalOneOffice.is_active == True)  # noqa: E712
        .all()
    )
    office_ids = [o.external_id for o in offices]

    if not office_ids:
        return {"message": "Nenhum escritório ativo encontrado."}

    def _run():
        import time as _time
        from app.db.session import SessionLocal
        from app.services.office_lawsuit_index_service import (
            OfficeLawsuitIndexService,
            _do_full_sync,
            _do_incremental_sync,
        )
        from app.models.office_lawsuit_index import OfficeLawsuitIndex, OfficeLawsuitSync
        from app.services.legal_one_client import LegalOneApiClient

        bg_db = SessionLocal()
        try:
            client = LegalOneApiClient()
            svc = OfficeLawsuitIndexService(bg_db, client)

            # Sync SEQUENCIAL dos escritórios — um de cada vez para não
            # estourar o rate limit (90 req/min) com threads paralelas.
            for idx, oid in enumerate(office_ids):
                logger.info("Pre-warm: sync índice escritório %s (%d/%d)", oid, idx + 1, len(office_ids))
                try:
                    state = svc._get_or_create_state(oid)

                    # Pula se já tem sync rodando (disparado por outro request)
                    if state.in_progress:
                        logger.info("Pre-warm: escritório %s já em sync, pulando.", oid)
                        continue

                    needs_full = (
                        state.last_full_sync_at is None
                        or not svc.is_fresh(oid)
                    )

                    # Marca como in_progress
                    from datetime import datetime, timezone
                    state.in_progress = True
                    state.last_sync_status = "running"
                    state.last_sync_error = None
                    state.progress_pct = 0
                    state.started_at = datetime.now(timezone.utc)
                    state.finished_at = None
                    bg_db.commit()

                    # Roda SYNC SÍNCRONO (mesma thread, sem spawn de thread extra)
                    if needs_full:
                        _do_full_sync(bg_db, client, state)
                    else:
                        _do_incremental_sync(bg_db, client, state)

                except Exception as exc:
                    logger.warning("Pre-warm: falha no índice do escritório %s: %s", oid, exc)
                    # Marca erro no estado
                    try:
                        st = bg_db.query(OfficeLawsuitSync).filter(
                            OfficeLawsuitSync.office_id == oid
                        ).one_or_none()
                        if st:
                            st.in_progress = False
                            st.last_sync_status = "error"
                            st.last_sync_error = str(exc)[:1000]
                            bg_db.commit()
                    except Exception:
                        bg_db.rollback()

                # Pausa entre escritórios para aliviar o rate limit
                if idx < len(office_ids) - 1:
                    _time.sleep(2)

            # Pre-warm lawsuit_cache: busca dados (CNJ + creationDate) de todos
            # os processos indexados. O client salva automaticamente no cache.
            all_lawsuit_ids = [
                r[0]
                for r in bg_db.query(OfficeLawsuitIndex.lawsuit_id).distinct().all()
            ]
            logger.info("Pre-warm: carregando cache de %s processos...", len(all_lawsuit_ids))

            if all_lawsuit_ids:
                # Batches menores + pausa para não estourar rate limit
                BATCH = 100
                for i in range(0, len(all_lawsuit_ids), BATCH):
                    chunk = all_lawsuit_ids[i:i + BATCH]
                    try:
                        client.fetch_lawsuits_by_ids(chunk)
                        logger.info(
                            "Pre-warm cache: %d/%d processos...",
                            min(i + BATCH, len(all_lawsuit_ids)),
                            len(all_lawsuit_ids),
                        )
                    except Exception as exc:
                        logger.warning("Pre-warm: falha no batch %s-%s: %s", i, i + len(chunk), exc)
                    # Pausa entre batches
                    _time.sleep(1)

            logger.info("Pre-warm: concluído. %s escritórios, %s processos.", len(office_ids), len(all_lawsuit_ids))
        except Exception as exc:
            logger.exception("Pre-warm: erro geral: %s", exc)
        finally:
            bg_db.close()

    background_tasks.add_task(_run)
    return {
        "message": f"Pre-warm de caches iniciado para {len(office_ids)} escritórios.",
        "offices": len(office_ids),
    }


@router.get(
    "/cache-status",
    summary="Status dos caches de escritórios e processos",
    tags=["Admin"],
)
def get_cache_status(db: Session = Depends(get_db)):
    """
    Retorna status detalhado dos caches:
    - Índice de processos por escritório (office_lawsuit_index)
    - Cache de dados dos processos (lawsuit_cache)
    - Metadados (escritórios, usuários, tipos de tarefa)
    """
    from app.models.legal_one import LegalOneOffice, LegalOneUser, LegalOneTaskType
    from app.models.office_lawsuit_index import OfficeLawsuitIndex, OfficeLawsuitSync
    from app.models.lawsuit_cache import LawsuitCache, LAWSUIT_CACHE_TTL
    from app.services.office_lawsuit_index_service import FULL_SYNC_TTL
    from datetime import datetime, timezone
    from sqlalchemy import func as sa_func

    # ── Metadados ──
    offices_count = db.query(sa_func.count(LegalOneOffice.id)).filter(
        LegalOneOffice.is_active == True  # noqa: E712
    ).scalar() or 0
    users_count = db.query(sa_func.count(LegalOneUser.id)).filter(
        LegalOneUser.is_active == True  # noqa: E712
    ).scalar() or 0
    task_types_count = db.query(sa_func.count(LegalOneTaskType.id)).filter(
        LegalOneTaskType.is_active == True  # noqa: E712
    ).scalar() or 0

    # ── Índices por escritório ──
    sync_states = db.query(OfficeLawsuitSync).all()
    now = datetime.now(timezone.utc)

    offices_index = []
    any_in_progress = False
    total_indexed = 0

    # Nomes dos escritórios — prioriza `path` (hierarquia completa) sobre `name`
    # para que o admin mostre "MDR Advocacia > Contencioso > Autor" em vez de só "Autor".
    office_names: dict[int, str] = {}
    if sync_states:
        oids = [s.office_id for s in sync_states]
        rows = db.query(LegalOneOffice).filter(LegalOneOffice.external_id.in_(oids)).all()
        office_names = {o.external_id: (o.path or o.name) for o in rows}

    for s in sync_states:
        last = s.last_full_sync_at
        is_fresh = False
        if last:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            is_fresh = (now - last) < FULL_SYNC_TTL

        offices_index.append({
            "office_id": s.office_id,
            "office_name": office_names.get(s.office_id, f"ID {s.office_id}"),
            "total_ids": s.total_ids or 0,
            "in_progress": bool(s.in_progress),
            "progress_pct": s.progress_pct or 0,
            "status": s.last_sync_status,
            "error": s.last_sync_error,
            "is_fresh": is_fresh,
            "last_sync": s.finished_at.isoformat() if s.finished_at else None,
        })
        if s.in_progress:
            any_in_progress = True
        total_indexed += (s.total_ids or 0)

    # ── Cache de processos ──
    lawsuit_cache_total = db.query(sa_func.count(LawsuitCache.lawsuit_id)).scalar() or 0
    cutoff = now - LAWSUIT_CACHE_TTL
    lawsuit_cache_fresh = db.query(sa_func.count(LawsuitCache.lawsuit_id)).filter(
        LawsuitCache.fetched_at >= cutoff
    ).scalar() or 0
    lawsuit_cache_stale = lawsuit_cache_total - lawsuit_cache_fresh

    return {
        "metadata": {
            "offices": offices_count,
            "users": users_count,
            "task_types": task_types_count,
        },
        "office_index": {
            "offices": offices_index,
            "total_indexed": total_indexed,
            "any_in_progress": any_in_progress,
        },
        "lawsuit_cache": {
            "total": lawsuit_cache_total,
            "fresh": lawsuit_cache_fresh,
            "stale": lawsuit_cache_stale,
            "ttl_hours": LAWSUIT_CACHE_TTL.total_seconds() / 3600,
        },
    }


@router.get(
    "/task-types",
    summary="Listar tipos de tarefa agrupados",
    tags=["Admin"],
    response_model=List[TaskTypeGroupSchema],
)
def get_task_types_grouped(db: Session = Depends(get_db)):
    # M2M Squad↔TaskType removida em sqd002 (squads agora sao por
    # escritorio). squad_ids fica sempre vazio — campo mantido na resposta
    # apenas pra compat com clientes legados.
    task_types = db.query(LegalOneTaskType).options(
        joinedload(LegalOneTaskType.subtypes),
    ).filter(
        LegalOneTaskType.is_active == True  # noqa: E712
    ).order_by(LegalOneTaskType.name).all()
    custom_group_names = {group.id: group.name for group in db.query(TaskParentGroup).all()}

    response_data = []
    for task_type in task_types:
        sub_types_data = [
            TaskSubTypeSchema(
                id=sub_type.id,
                name=sub_type.name,
                squad_ids=[],
            )
            for sub_type in sorted(task_type.subtypes, key=lambda item: item.name)
            if sub_type.is_active
        ]

        response_data.append(
            TaskTypeGroupSchema(
                parent_id=task_type.id,
                parent_name=custom_group_names.get(task_type.id, task_type.name),
                sub_types=sub_types_data,
            )
        )

    return response_data


@router.put("/task-parent-groups/{parent_id}", summary="Renomear grupo pai de tarefas", tags=["Admin"])
def update_task_parent_group(
    parent_id: int,
    payload: TaskParentGroupUpdatePayload,
    db: Session = Depends(get_db),
):
    task_type = db.query(LegalOneTaskType).filter(LegalOneTaskType.id == parent_id).first()
    if not task_type:
        raise HTTPException(status_code=404, detail="Grupo de tarefa nao encontrado.")

    normalized_name = payload.name.strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="O nome do grupo nao pode ficar vazio.")

    group = db.query(TaskParentGroup).filter(TaskParentGroup.id == parent_id).first()
    if group is None:
        group = TaskParentGroup(id=parent_id, name=normalized_name)
        db.add(group)
    else:
        group.name = normalized_name

    db.commit()
    db.refresh(group)
    return {"id": group.id, "name": group.name}


# Endpoint /task-types/associate removido em sqd002 (2026-05-04). A
# associacao Squad↔TaskType (M2M) foi descartada — squads agora sao
# por escritorio responsavel (Squad.office_external_id) e o tie-break
# do resolver usa office em vez de tipos de tarefa. Renomeacao de
# grupos pais continua disponivel em /task-parent-groups/{id}.


class RetryBatchRequest(BaseModel):
    item_ids: Optional[List[int]] = None  # se None, retry em todos os FALHA


@router.post(
    "/batch-executions/{execution_id}/retry",
    status_code=202,
    summary="Reprocessar itens falhos de um lote (opcionalmente seletivo por item_ids)",
    tags=["Admin"],
)
def retry_failed_batch_items(
    execution_id: int,
    background_tasks: BackgroundTasks,
    payload: Optional[RetryBatchRequest] = None,
    service: BatchTaskCreationService = Depends(get_batch_task_creation_service),
):
    target_ids = payload.item_ids if payload else None
    logger.info(
        "Reprocessar lote %s (itens=%s)",
        execution_id,
        f"{len(target_ids)} seletivos" if target_ids else "todos FALHA",
    )
    background_tasks.add_task(service.retry_failed_items, execution_id, target_ids)
    return {
        "message": f"Reprocessamento do lote {execution_id} iniciado.",
        "selective": bool(target_ids),
        "count": len(target_ids) if target_ids else None,
    }


@router.get(
    "/batch-executions/{execution_id}/error-groups",
    summary="Agrupa falhas do lote por mensagem de erro (para retry seletivo)",
    tags=["Admin"],
)
def get_batch_error_groups(
    execution_id: int,
    db: Session = Depends(get_db),
):
    """Retorna grupos de falhas com o mesmo error_message e seus item_ids."""
    from app.models.batch_execution import BatchExecution, BatchExecutionItem

    exec_ = db.query(BatchExecution).filter(BatchExecution.id == execution_id).first()
    if not exec_:
        raise HTTPException(status_code=404, detail="Lote não encontrado")

    failed = (
        db.query(BatchExecutionItem)
        .filter(
            BatchExecutionItem.execution_id == execution_id,
            BatchExecutionItem.status == "FALHA",
        )
        .all()
    )

    groups: Dict[str, Dict[str, Any]] = {}
    for it in failed:
        key = (it.error_message or "(sem mensagem)").strip()
        bucket = groups.setdefault(key, {"error_message": key, "count": 0, "item_ids": [], "sample_processes": []})
        bucket["count"] += 1
        bucket["item_ids"].append(it.id)
        if len(bucket["sample_processes"]) < 3:
            bucket["sample_processes"].append(it.process_number)

    ordered = sorted(groups.values(), key=lambda g: g["count"], reverse=True)
    return {
        "execution_id": execution_id,
        "total_failed": len(failed),
        "groups": ordered,
    }


# ─── User Management ────────────────────────────────────────────────────────

class UserUpdateRequest(BaseModel):
    role: Optional[str] = None
    can_schedule_batch: Optional[bool] = None
    can_use_publications: Optional[bool] = None
    can_use_prazos_iniciais: Optional[bool] = None
    can_use_onerequest: Optional[bool] = None
    notify_onerequest_errors: Optional[bool] = None
    can_use_minha_equipe: Optional[bool] = None
    minha_equipe_equipes: Optional[list] = None
    can_manage_distribuidos_bb: Optional[bool] = None
    default_office_id: Optional[int] = None


class UserResponseSchema(BaseModel):
    id: int
    email: str
    name: str
    role: str
    can_schedule_batch: bool
    can_use_publications: bool
    can_use_prazos_iniciais: bool = False
    can_use_onerequest: bool = False
    notify_onerequest_errors: bool = False
    default_office_id: Optional[int] = None

    class Config:
        from_attributes = True


def _equipes_to_list(u) -> list:
    """CSV de chaves de equipe (minha_equipe_equipes) → lista, ignorando vazios."""
    raw = getattr(u, "minha_equipe_equipes", None)
    return [x for x in (raw or "").split(",") if x]


@router.get("/users", tags=["Admin"])
def list_users(
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    """List all users (admin only)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    users = db.query(LegalOneUser).order_by(LegalOneUser.name).all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "name": u.name,
            "external_id": u.external_id,
            "is_active": u.is_active,
            "role": u.role,
            "can_schedule_batch": u.can_schedule_batch,
            "can_use_publications": u.can_use_publications,
            "can_use_prazos_iniciais": getattr(u, "can_use_prazos_iniciais", False),
            "can_use_onerequest": getattr(u, "can_use_onerequest", False),
            "can_use_minha_equipe": getattr(u, "can_use_minha_equipe", False),
            "minha_equipe_equipes": _equipes_to_list(u),
            "can_manage_distribuidos_bb": getattr(u, "can_manage_distribuidos_bb", False),
            "notify_onerequest_errors": getattr(u, "notify_onerequest_errors", False),
            "default_office_id": u.default_office_id,
            "has_password": u.hashed_password is not None,
            "is_sso": getattr(u, "last_sso_at", None) is not None,
            "must_change_password": u.must_change_password,
        }
        for u in users
    ]


@router.patch("/users/{user_id}", tags=["Admin"])
def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    """Update user permissions (admin only)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    user = db.query(LegalOneUser).filter(LegalOneUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if payload.role is not None:
        user.role = payload.role
    if payload.can_schedule_batch is not None:
        user.can_schedule_batch = payload.can_schedule_batch
    if payload.can_use_publications is not None:
        user.can_use_publications = payload.can_use_publications
    if payload.can_use_prazos_iniciais is not None:
        user.can_use_prazos_iniciais = payload.can_use_prazos_iniciais
    if payload.can_use_onerequest is not None:
        user.can_use_onerequest = payload.can_use_onerequest
    if payload.can_use_minha_equipe is not None:
        user.can_use_minha_equipe = payload.can_use_minha_equipe
    if payload.minha_equipe_equipes is not None:
        user.minha_equipe_equipes = ",".join(payload.minha_equipe_equipes)
    if payload.can_manage_distribuidos_bb is not None:
        user.can_manage_distribuidos_bb = payload.can_manage_distribuidos_bb
    if payload.notify_onerequest_errors is not None:
        user.notify_onerequest_errors = payload.notify_onerequest_errors
    if payload.default_office_id is not None:
        user.default_office_id = payload.default_office_id

    db.commit()
    db.refresh(user)
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "can_schedule_batch": user.can_schedule_batch,
        "can_use_publications": user.can_use_publications,
        "can_use_prazos_iniciais": getattr(user, "can_use_prazos_iniciais", False),
        "can_use_onerequest": getattr(user, "can_use_onerequest", False),
        "can_use_minha_equipe": getattr(user, "can_use_minha_equipe", False),
        "minha_equipe_equipes": _equipes_to_list(user),
        "can_manage_distribuidos_bb": getattr(user, "can_manage_distribuidos_bb", False),
        "notify_onerequest_errors": getattr(user, "notify_onerequest_errors", False),
        "default_office_id": user.default_office_id,
    }


@router.post("/users/{user_id}/activate", tags=["Admin"])
def activate_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    """Activate user and generate temporary password (admin only)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    user = db.query(LegalOneUser).filter(LegalOneUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Generate temporary password
    temp_password = auth.generate_temp_password()
    user.hashed_password = auth.get_password_hash(temp_password)
    user.is_active = True
    user.must_change_password = True

    db.commit()
    db.refresh(user)

    # Return the plaintext password ONCE
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "temp_password": temp_password,
        "message": "Esta senha só será exibida uma vez. Repasse-a ao usuário com segurança.",
    }


@router.post("/users/{user_id}/reset-password", tags=["Admin"])
def reset_user_password(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    """Reset user password (admin only)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    user = db.query(LegalOneUser).filter(LegalOneUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Generate temporary password
    temp_password = auth.generate_temp_password()
    user.hashed_password = auth.get_password_hash(temp_password)
    user.must_change_password = True

    db.commit()
    db.refresh(user)

    # Return the plaintext password ONCE
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "temp_password": temp_password,
        "message": "Esta senha só será exibida uma vez. Repasse-a ao usuário com segurança.",
    }


@router.post("/users/{user_id}/deactivate", tags=["Admin"])
def deactivate_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    """Deactivate user (admin only)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    user = db.query(LegalOneUser).filter(LegalOneUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.is_active = False
    db.commit()
    db.refresh(user)

    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "is_active": user.is_active,
    }


# ─── SSO Account Linking (Microsoft Entra) ──────────────────────────────────
# O email do Entra (@mdradvocacia.com) diverge do email guardado no Legal One
# (a maioria é @gmail pessoal), então o vínculo é feito por SIMILARIDADE DE NOME
# + confirmação do admin. "Pendente" = usuário provisionado via SSO sem vínculo
# com o Legal One (external_id IS NULL — ver app/api/v1/endpoints/auth.py).
# Ao unificar, o email da Microsoft passa a ser o email do usuário do Legal One
# e os próximos logins casam direto pelo email.

SSO_NAME_SIMILARITY_THRESHOLD = 0.2  # similaridade pg_trgm (0..1)


class SsoUnifyRequest(BaseModel):
    pending_user_id: int
    target_user_id: int


def _name_similarity(name: str):
    """Similaridade (trigrama, sem acento) do LegalOneUser.name contra `name`."""
    return func.similarity(
        func.unaccent(func.lower(LegalOneUser.name)),
        func.unaccent(func.lower(name)),
    )


@router.get("/sso/pending", tags=["Admin"])
def list_sso_pending(
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    """Lista contas provisionadas via SSO ainda NÃO vinculadas (external_id NULL)
    e, pra cada uma, os usuários do Legal One mais parecidos por nome —
    candidatos pra unificação. (admin only)"""
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    pendings = (
        db.query(LegalOneUser)
        .filter(LegalOneUser.external_id.is_(None))
        .order_by(LegalOneUser.name)
        .all()
    )

    result = []
    for p in pendings:
        sim = _name_similarity(p.name)
        rows = (
            db.query(LegalOneUser, sim.label("sim"))
            .filter(LegalOneUser.external_id.isnot(None))
            .filter(sim > SSO_NAME_SIMILARITY_THRESHOLD)
            .order_by(sim.desc())
            .limit(5)
            .all()
        )
        result.append({
            "id": p.id,
            "name": p.name,
            "email": p.email,
            "candidates": [
                {
                    "id": c.id,
                    "name": c.name,
                    "email": c.email,
                    "external_id": c.external_id,
                    "role": c.role,
                    "is_active": c.is_active,
                    "similarity": round(float(s or 0), 3),
                }
                for (c, s) in rows
            ],
        })
    return result


@router.post("/sso/unify", tags=["Admin"])
def unify_sso_account(
    payload: SsoUnifyRequest,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    """Unifica uma conta SSO pendente com um usuário existente do Legal One:
    o email da Microsoft (do pendente) passa a ser o email do usuário do Legal
    One (mantendo external_id, permissões e dados). A conta pendente é removida.
    A partir daí os próximos logins SSO casam direto pelo email. (admin only)"""
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    pending = (
        db.query(LegalOneUser)
        .filter(LegalOneUser.id == payload.pending_user_id, LegalOneUser.external_id.is_(None))
        .first()
    )
    if not pending:
        raise HTTPException(status_code=404, detail="Conta pendente não encontrada (ou já vinculada).")

    target = (
        db.query(LegalOneUser)
        .filter(LegalOneUser.id == payload.target_user_id, LegalOneUser.external_id.isnot(None))
        .first()
    )
    if not target:
        raise HTTPException(status_code=404, detail="Usuário do Legal One (alvo) não encontrado.")

    new_email = pending.email  # email institucional Microsoft

    # Migra dados que por acaso estejam no pendente (defensivo — pendentes
    # recém-criados não costumam ter nada).
    db.query(SavedFilter).filter(SavedFilter.user_id == pending.id).update(
        {"user_id": target.id}, synchronize_session=False
    )

    # Remove o pendente PRIMEIRO (libera o email único), depois aplica no alvo.
    db.delete(pending)
    db.flush()
    target.email = new_email
    target.is_active = True
    db.commit()
    db.refresh(target)

    return {
        "unified": True,
        "user": {
            "id": target.id,
            "name": target.name,
            "email": target.email,
            "external_id": target.external_id,
            "role": target.role,
        },
    }


# ─── Taxonomy v2 toggle (fase 11) ───────────────────────────────────────────


class TaxonomyToggleResponse(BaseModel):
    active_version: str  # 'v1' | 'v2'
    pending_templates: int
    pending_overrides: int
    can_activate_v2: bool
    threshold: int


class TaxonomyToggleRequest(BaseModel):
    active_version: str  # 'v1' | 'v2'
    # Se True, ignora o gate de pendentes (operador confirmou que aceita
    # subir v2 com templates ainda pendentes; eles vao ficar dormentes
    # ate serem revisados).
    force: bool = False


# Threshold default: nao deixa ativar v2 se houver mais de N templates
# pendentes — protege contra "subi v2 e perdi 90% das propostas
# automaticas". Operador pode setar `force=true` pra burlar.
TAXONOMY_TOGGLE_THRESHOLD = 0


def _count_taxonomy_pending(db: Session) -> tuple[int, int]:
    """Retorna (templates_pendentes, overrides_pendentes)."""
    from app.models.task_template import TaskTemplate
    from app.models.office_classification import OfficeClassificationOverride

    tpl = (
        db.query(TaskTemplate)
        .filter(TaskTemplate.needs_taxonomy_review == True)
        .count()
    )
    ovr = (
        db.query(OfficeClassificationOverride)
        .filter(OfficeClassificationOverride.needs_taxonomy_review == True)
        .count()
    )
    return tpl, ovr


@router.get(
    "/taxonomy/settings",
    response_model=TaxonomyToggleResponse,
    summary="Estado do toggle taxonomy v1<->v2",
    tags=["Admin"],
)
def get_taxonomy_settings(db: Session = Depends(get_db)):
    """Le o toggle global e a contagem de pendentes pra que a UI
    decida se permite ativar v2 (threshold)."""
    from app.services.app_settings import get_setting

    active = (get_setting("taxonomy_active_version") or "v1").strip().lower()
    if active not in ("v1", "v2"):
        active = "v1"

    pending_tpl, pending_ovr = _count_taxonomy_pending(db)
    total_pending = pending_tpl + pending_ovr
    can_activate = (
        active == "v2" or total_pending <= TAXONOMY_TOGGLE_THRESHOLD
    )
    return TaxonomyToggleResponse(
        active_version=active,
        pending_templates=pending_tpl,
        pending_overrides=pending_ovr,
        can_activate_v2=can_activate,
        threshold=TAXONOMY_TOGGLE_THRESHOLD,
    )


@router.patch(
    "/taxonomy/settings",
    response_model=TaxonomyToggleResponse,
    summary="Atualizar toggle taxonomy v1<->v2",
    tags=["Admin"],
)
def update_taxonomy_settings(
    body: TaxonomyToggleRequest,
    db: Session = Depends(get_db),
):
    """Atualiza o toggle global. Bloqueia ativacao da v2 quando ha
    pendentes acima do threshold (a menos que `force=true`).

    Reverter pra v1 e sempre permitido — nao tem gate."""
    from app.services.app_settings import set_setting
    from app.services.classifier.taxonomy import invalidate_taxonomy_cache

    target = body.active_version.strip().lower()
    if target not in ("v1", "v2"):
        raise HTTPException(
            400,
            "active_version deve ser 'v1' ou 'v2'.",
        )

    if target == "v2" and not body.force:
        pending_tpl, pending_ovr = _count_taxonomy_pending(db)
        total = pending_tpl + pending_ovr
        if total > TAXONOMY_TOGGLE_THRESHOLD:
            raise HTTPException(
                409,
                f"Existem {total} item(ns) pendente(s) de revisao "
                f"({pending_tpl} templates + {pending_ovr} overrides). "
                "Revise via Admin > Templates Pendentes de Revisao antes "
                "de ativar a v2 — ou use force=true pra ignorar o gate.",
            )

    set_setting(
        "taxonomy_active_version",
        target,
        description=(
            "Versao da taxonomia ativa globalmente. v1=legacy, v2=nova "
            "com polos. Mude via Admin > Toggle Taxonomy."
        ),
    )
    # Invalida cache de taxonomia pra que o proximo classifier carregue
    # a arvore correta imediatamente (nao espera os 60s do TTL).
    invalidate_taxonomy_cache()

    pending_tpl, pending_ovr = _count_taxonomy_pending(db)
    return TaxonomyToggleResponse(
        active_version=target,
        pending_templates=pending_tpl,
        pending_overrides=pending_ovr,
        can_activate_v2=True,
        threshold=TAXONOMY_TOGGLE_THRESHOLD,
    )


# ─── Template-driven taxonomy toggle (fase 13) ──────────────────────────────


class TemplateDrivenTaxonomyResponse(BaseModel):
    enabled: bool
    description: str


class TemplateDrivenTaxonomyRequest(BaseModel):
    enabled: bool


@router.get(
    "/template-driven-taxonomy/settings",
    response_model=TemplateDrivenTaxonomyResponse,
    summary="Estado do modo arvore enxuta (template-driven taxonomy)",
    tags=["Admin"],
)
def get_template_driven_taxonomy(_db: Session = Depends(get_db)):
    """Quando ativo, a arvore aplicavel a cada escritorio so inclui cats
    com pelo menos um template ativo do escritorio (ou global). Cats
    residuais "Para Analise" sempre entram via whitelist."""
    from app.services.app_settings import get_setting

    raw = (get_setting("template_driven_taxonomy") or "true").strip().lower()
    enabled = raw in ("true", "1", "yes", "on")
    return TemplateDrivenTaxonomyResponse(
        enabled=enabled,
        description=(
            "Modo arvore enxuta ativo: a IA so ve categorias com template do escritorio"
            if enabled
            else "Modo arvore completa: IA ve a arvore inteira (filtrada apenas por polo/version)"
        ),
    )


@router.patch(
    "/template-driven-taxonomy/settings",
    response_model=TemplateDrivenTaxonomyResponse,
    summary="Atualizar modo arvore enxuta",
    tags=["Admin"],
)
def update_template_driven_taxonomy(
    body: TemplateDrivenTaxonomyRequest,
    _db: Session = Depends(get_db),
):
    """Liga ou desliga o modo enxuto. Invalida o cache de taxonomia
    inteiro pra que a proxima classificacao reflita imediatamente."""
    from app.services.app_settings import set_setting
    from app.services.classifier.taxonomy import invalidate_taxonomy_cache

    set_setting(
        "template_driven_taxonomy",
        "true" if body.enabled else "false",
        description=(
            "Quando true, a arvore aplicavel a um escritorio so inclui categorias "
            "com pelo menos um template ativo do escritorio (ou global). "
            "Cats residuais 'Para Analise' sempre aparecem via whitelist."
        ),
    )
    invalidate_taxonomy_cache()
    return TemplateDrivenTaxonomyResponse(
        enabled=body.enabled,
        description=(
            "Modo arvore enxuta ativo: a IA so ve categorias com template do escritorio"
            if body.enabled
            else "Modo arvore completa: IA ve a arvore inteira (filtrada apenas por polo/version)"
        ),
    )


# ─── Saved Filters ──────────────────────────────────────────────────────────

class SavedFilterCreateRequest(BaseModel):
    name: str
    module: str  # "publications", "scheduler", etc.
    filters_json: Dict[str, Any]
    is_default: bool = False


class SavedFilterSchema(BaseModel):
    id: int
    name: str
    module: str
    filters_json: Dict[str, Any]
    is_default: bool

    class Config:
        from_attributes = True


@me_router.get("/me/saved-filters", tags=["User"])
def get_saved_filters(
    module: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    """Get user's saved filters."""
    query = db.query(SavedFilter).filter(SavedFilter.user_id == current_user.id)
    if module:
        query = query.filter(SavedFilter.module == module)

    filters = query.order_by(SavedFilter.created_at.desc()).all()
    return [
        {
            "id": f.id,
            "name": f.name,
            "module": f.module,
            "filters_json": f.filters_json,
            "is_default": f.is_default,
        }
        for f in filters
    ]


@me_router.post("/me/saved-filters", status_code=201, tags=["User"])
def create_saved_filter(
    payload: SavedFilterCreateRequest,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    """Create a new saved filter for the user."""
    # If marked as default, unset any existing default for this module
    if payload.is_default:
        db.query(SavedFilter).filter(
            SavedFilter.user_id == current_user.id,
            SavedFilter.module == payload.module,
            SavedFilter.is_default == True,
        ).update({"is_default": False})

    saved_filter = SavedFilter(
        user_id=current_user.id,
        name=payload.name,
        module=payload.module,
        filters_json=payload.filters_json,
        is_default=payload.is_default,
    )
    db.add(saved_filter)
    db.commit()
    db.refresh(saved_filter)

    return {
        "id": saved_filter.id,
        "name": saved_filter.name,
        "module": saved_filter.module,
        "filters_json": saved_filter.filters_json,
        "is_default": saved_filter.is_default,
    }


@me_router.delete("/me/saved-filters/{filter_id}", status_code=204, tags=["User"])
def delete_saved_filter(
    filter_id: int,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    """Delete a saved filter."""
    saved_filter = db.query(SavedFilter).filter(
        SavedFilter.id == filter_id,
        SavedFilter.user_id == current_user.id,
    ).first()

    if not saved_filter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found")

    db.delete(saved_filter)
    db.commit()
    return None


# ─── User Self-Service Endpoints ───────────────────────────────────────────


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class MeResponseSchema(BaseModel):
    id: int
    name: str
    email: str
    role: str
    can_schedule_batch: bool
    can_use_publications: bool
    can_use_prazos_iniciais: bool = False
    can_use_onerequest: bool = False
    can_use_minha_equipe: bool = False
    minha_equipe_equipes: list = []
    can_manage_distribuidos_bb: bool = False
    default_office_id: Optional[int]
    must_change_password: bool

    class Config:
        from_attributes = True


@me_router.get("/me", tags=["User"])
def get_current_user_info(
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    """Get current user information."""
    return {
        "id": current_user.id,
        "name": current_user.name,
        "email": current_user.email,
        "role": current_user.role,
        "can_schedule_batch": current_user.can_schedule_batch,
        "can_use_publications": current_user.can_use_publications,
        "can_use_prazos_iniciais": getattr(current_user, "can_use_prazos_iniciais", False),
        "can_use_onerequest": getattr(current_user, "can_use_onerequest", False),
        "can_use_minha_equipe": getattr(current_user, "can_use_minha_equipe", False),
        "minha_equipe_equipes": _equipes_to_list(current_user),
        "can_manage_distribuidos_bb": getattr(current_user, "can_manage_distribuidos_bb", False),
        "default_office_id": current_user.default_office_id,
        "must_change_password": current_user.must_change_password,
    }


@me_router.post("/me/change-password", tags=["User"])
def change_password(
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: LegalOneUser = Depends(auth.get_current_user),
):
    """Change current user password."""
    # Validate current password
    if not current_user.hashed_password or not auth.verify_password(
        payload.current_password, current_user.hashed_password
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Senha atual incorreta.",
        )

    # Validate new password
    auth.validate_password(payload.new_password)

    # Update password
    current_user.hashed_password = auth.get_password_hash(payload.new_password)
    current_user.must_change_password = False

    db.commit()
    db.refresh(current_user)

    return {
        "id": current_user.id,
        "email": current_user.email,
        "name": current_user.name,
        "message": "Senha alterada com sucesso.",
    }
