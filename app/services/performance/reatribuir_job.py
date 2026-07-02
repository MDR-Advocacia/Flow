"""Reatribuição EM LOTE de tarefas (Balanceador de Agenda) — ESCRITA REAL no L1.

Espelha o padrão do `cancel_duplicadas`: roda numa thread daemon, progresso
PERSISTIDO em `balanceador_reatribuir_job` pro polling enxergar mesmo com vários
workers do uvicorn. Nunca depende da tela ficar aberta.

Método (já provado em prod 2026-06-26, ver
docs/legalone-reatribuir-responsavel-executante-tarefa.md):
  - tarefa NORMAL → PATCH /Tasks/{id} `participants` (REPLACE): lê os atuais,
    PRESERVA o solicitante (isRequester) e seta responsável+executante pro novo.
  - tarefa de WORKFLOW (Modelo de Procedimento) → a API trava (HTTP 400). Não dá
    pra reatribuir por API; vai pro bucket `workflow_bloqueadas` (tratamento
    manual/RPA — POST web ModalEnvolvimentoEmLote).

Buckets: reatribuidas / workflow_bloqueadas / falhas. Suporta abort. dry_run só
lê os participantes (prova acesso, conta) — não grava nada no L1.
"""

import logging
import threading
import time
import uuid

from sqlalchemy import func, text

from app.db.session import SessionLocal
from app.models.performance import BalanceadorReatribuirJob

logger = logging.getLogger(__name__)

_MAX = 2000          # trava de segurança por lote
_THROTTLE = 0.35     # respiro entre tarefas (L1 ~1,2 req/s; 2 calls por tarefa)
_DETALHE_FLUSH = 10  # grava a lista de detalhe a cada N tarefas


def _resolver_destino_cid(db, to_id, to_nome):
    """Resolve o destino (como veio da UI) pro contact_id do L1.

    O `to_id` da UI é ambíguo: destino do SETOR carrega perf_pessoa.id; destino
    EXTERNO carrega o id de usuário do L1. Resolve pelo NOME primeiro (setor e
    externo compartilham o catálogo do L1), com fallbacks por id.
    """
    from app.models.performance import PerfPessoa
    from app.services.performance.balanceador import _user_map, _users
    from app.services.performance.seed import norm

    umap = _user_map()
    if to_nome:
        cid = umap.get(norm(to_nome))
        if cid:
            return cid
    if to_id:
        if any(u["id"] == to_id for u in _users()):
            return to_id  # já é o contact_id do L1 (destino externo)
        p = db.query(PerfPessoa).filter(PerfPessoa.id == to_id).first()
        if p:  # destino do setor: perf_pessoa.id -> nome_norm -> contact_id
            return umap.get(p.nome_norm)
    return None


def _reatribuir_uma(c, task_id: int, cid: int) -> dict:
    """Lê participantes atuais, preserva o solicitante e troca responsável +
    executante pro `cid`. Devolve {"reason", "http"}."""
    try:
        atuais = c.get_task_participants(task_id)
    except Exception:  # noqa: BLE001
        logger.exception("get_task_participants falhou (task %s)", task_id)
        return {"reason": "error", "http": None}

    requester_id = None
    for p in atuais or []:
        if p.get("isRequester"):
            ct = p.get("contact") or {}
            if ct.get("id"):
                requester_id = ct["id"]
                break

    if requester_id and requester_id != cid:
        desired = [
            {"contact": {"id": cid}, "isResponsible": True, "isExecuter": True, "isRequester": False},
            {"contact": {"id": requester_id}, "isResponsible": False, "isExecuter": False, "isRequester": True},
        ]
    else:
        # novo == solicitante, ou tarefa sem solicitante: um participante com tudo.
        desired = [
            {"contact": {"id": cid}, "isResponsible": True, "isExecuter": True, "isRequester": True},
        ]
    return c.update_task_participants(task_id, desired)


def iniciar(team, itens, movimentos, dry_run, user) -> str:
    """Cria o job, dispara a thread e devolve o job_id. `itens` = lista de
    {task_id, to_id, to_nome} (task-level, resolvida do modal)."""
    itens = [it for it in (itens or []) if it.get("task_id")][:_MAX]
    job_id = uuid.uuid4().hex[:12]
    db = SessionLocal()
    try:
        db.add(
            BalanceadorReatribuirJob(
                id=job_id,
                team=team,
                status="running",
                dry_run=bool(dry_run),
                total=len(itens),
                detalhe=[],
                criado_por_id=getattr(user, "id", None),
                criado_por_nome=getattr(user, "name", None) or getattr(user, "email", None),
            )
        )
        db.commit()
    finally:
        db.close()
    threading.Thread(
        target=_run, args=(job_id, team, itens, movimentos or [], bool(dry_run)), daemon=True
    ).start()
    return job_id


def solicitar_abort(job_id: str) -> bool:
    db = SessionLocal()
    try:
        j = db.get(BalanceadorReatribuirJob, job_id)
        if not j or j.status == "done":
            return False
        j.status = "aborting"
        db.commit()
        return True
    finally:
        db.close()


def _abortado(db, job_id: str) -> bool:
    return db.execute(
        text("SELECT status FROM balanceador_reatribuir_job WHERE id = :id"), {"id": job_id}
    ).scalar() == "aborting"


def _run(job_id: str, team: str, itens: list, movimentos: list, dry_run: bool) -> None:
    from app.services.legal_one_client import LegalOneApiClient

    db = SessionLocal()
    try:
        c = LegalOneApiClient()
        job = db.get(BalanceadorReatribuirJob, job_id)
        detalhe: list = []
        destino_cache: dict = {}

        for i, it in enumerate(itens):
            if _abortado(db, job_id):
                break
            task_id = it.get("task_id")
            to_id = it.get("to_id")
            to_nome = it.get("to_nome")
            key = (to_id, to_nome)
            if key not in destino_cache:
                destino_cache[key] = _resolver_destino_cid(db, to_id, to_nome)
            cid = destino_cache[key]

            if not cid:
                reason, http = "destino_nao_resolvido", None
            elif dry_run:
                try:
                    c.get_task_participants(int(task_id))
                    reason, http = "dry_ok", None
                except Exception:  # noqa: BLE001
                    reason, http = "error", None
            else:
                res = _reatribuir_uma(c, int(task_id), int(cid))
                reason, http = res.get("reason", "error"), res.get("http")

            if reason in ("reassigned", "dry_ok"):
                job.reatribuidas = (job.reatribuidas or 0) + 1
            elif reason == "workflow_locked":
                job.workflow_bloqueadas = (job.workflow_bloqueadas or 0) + 1
            else:
                job.falhas = (job.falhas or 0) + 1

            detalhe.append(
                {"task_id": task_id, "to_id": to_id, "to_nome": to_nome, "reason": reason, "http": http}
            )
            job.feito = i + 1
            if (i + 1) % _DETALHE_FLUSH == 0:
                job.detalhe = list(detalhe)
            db.commit()
            time.sleep(_THROTTLE)

        job.detalhe = list(detalhe)
        job.status = "done"
        job.terminado_em = func.now()
        db.commit()

        # Auditoria move-level na aba Relatórios (só na escrita real).
        if not dry_run and movimentos:
            try:
                from app.models.performance import BalanceadorLog

                db.add(
                    BalanceadorLog(
                        team=team,
                        criado_por_id=job.criado_por_id,
                        criado_por_nome=job.criado_por_nome,
                        total_movimentos=len(movimentos),
                        total_tarefas=sum(int(m.get("qtd") or 0) for m in movimentos),
                        origem="l1",
                        detalhe=movimentos,
                    )
                )
                db.commit()
            except Exception:  # noqa: BLE001
                logger.exception("falha ao gravar BalanceadorLog do job %s", job_id)
    except Exception:  # noqa: BLE001
        logger.exception("job de reatribuição %s estourou", job_id)
        try:
            j = db.get(BalanceadorReatribuirJob, job_id)
            if j:
                j.status = "done"
                j.terminado_em = func.now()
                db.commit()
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()


def status(job_id: str) -> dict | None:
    db = SessionLocal()
    try:
        j = db.get(BalanceadorReatribuirJob, job_id)
        if not j:
            return None
        return {
            "job_id": j.id,
            "status": j.status,
            "dry_run": j.dry_run,
            "total": j.total or 0,
            "feito": j.feito or 0,
            "reatribuidas": j.reatribuidas or 0,
            "workflow_bloqueadas": j.workflow_bloqueadas or 0,
            "falhas": j.falhas or 0,
            "detalhe": j.detalhe or [],
        }
    finally:
        db.close()
