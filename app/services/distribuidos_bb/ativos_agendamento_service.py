"""Agendamento de tarefa em lote sobre duplicados Ativos (já existentes no L1).

O operador seleciona duplicados (com pasta L1 já resolvida) e agenda uma tarefa
em cada — subtipo, prazo (default hoje+1), prioridade, escritório, descrição —
dividindo os responsáveis igual (round-robin por contagem) OU tudo pra uma
pessoa. Reusa o mesmo motor das Publicações/Tarefas por Planilha:
`create_task` + `link_task_to_lawsuit`.

Server-backed com progresso (BbAtivosAgendamentoJob): a UI dispara, um worker
cria as tarefas e a barra acompanha. `dry_run=True` só monta o plano (não escreve
no L1). Anti-duplicidade: pula a pasta que já tenha uma tarefa ABERTA do mesmo
subtipo (evita recriar o que o operador já agendou).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.distribuidos_bb import (
    AGEND_CONCLUIDO,
    AGEND_EM_ANDAMENTO,
    AGEND_ERRO,
    BbAtivosAgendamentoJob,
    BbAtivosDuplicado,
)
from app.models.legal_one import LegalOneUser

logger = logging.getLogger("distribuidos_bb.ativos.agendamento")

DEFAULT_TASK_STATUS_ID = 0  # 0 = Pendente


# ── Divisão dos responsáveis ───────────────────────────────────────────────

def montar_plano(
    db: Session,
    *,
    duplicado_ids: list[int],
    responsavel_ids: list[int],
    dividir_igual: bool,
) -> list[dict[str, Any]]:
    """Casa cada duplicado (com pasta L1) a um responsável.

    - dividir_igual=True: round-robin por CONTAGEM entre os responsáveis (mesma
      lógica do splitRR do Balanceador — diferença máxima de 1 tarefa por pessoa).
    - dividir_igual=False: todos vão pro primeiro (único) responsável.

    Pula silenciosamente duplicado sem pasta L1 resolvida (não dá pra criar
    tarefa sem a pasta). Ordena por id pra o rateio ser estável/reprodutível.
    """
    if not responsavel_ids:
        return []

    dups = (
        db.query(BbAtivosDuplicado)
        .filter(
            BbAtivosDuplicado.id.in_(duplicado_ids),
            BbAtivosDuplicado.l1_lawsuit_id.isnot(None),
        )
        .order_by(BbAtivosDuplicado.id)
        .all()
    )

    # Nomes dos responsáveis (pra exibir no preview e no resultado).
    users = {
        u.id: u for u in db.query(LegalOneUser).filter(LegalOneUser.id.in_(responsavel_ids)).all()
    }
    # Preserva a ordem que o operador escolheu.
    alvos = [uid for uid in responsavel_ids if uid in users]
    if not alvos:
        return []

    plano = []
    for i, d in enumerate(dups):
        uid = alvos[i % len(alvos)] if dividir_igual else alvos[0]
        u = users[uid]
        plano.append({
            "duplicado_id": d.id,
            "cnj": d.cnj,
            "lawsuit_id": d.l1_lawsuit_id,
            "folder": d.l1_folder,
            "parte": d.parte,
            "responsavel_id": u.id,
            "responsavel_external_id": u.external_id,
            "responsavel_nome": u.name,
        })
    return plano


def resumo_por_responsavel(plano: list[dict]) -> list[dict]:
    """Agrega o plano por responsável, pros cards do preview."""
    agg: dict[int, dict] = {}
    for p in plano:
        a = agg.setdefault(p["responsavel_id"], {
            "responsavel_id": p["responsavel_id"], "responsavel_nome": p["responsavel_nome"], "total": 0,
        })
        a["total"] += 1
    return sorted(agg.values(), key=lambda x: (-x["total"], x["responsavel_nome"] or ""))


def preview(
    db: Session,
    *,
    duplicado_ids: list[int],
    responsavel_ids: list[int],
    dividir_igual: bool,
) -> dict:
    """Dry-run leve (sem job): o que SERIA criado. Não toca no L1."""
    plano = montar_plano(
        db, duplicado_ids=duplicado_ids,
        responsavel_ids=responsavel_ids, dividir_igual=dividir_igual,
    )
    return {
        # plano só inclui duplicado com pasta L1 resolvida; o resto não dá tarefa.
        "total_pastas": len(plano),
        "sem_pasta": max(0, len(set(duplicado_ids)) - len(plano)),
        "por_responsavel": resumo_por_responsavel(plano),
        "plano": plano,
    }


# ── Execução (worker) ──────────────────────────────────────────────────────

def _payload_tarefa(config: dict, item: dict) -> dict:
    """Monta o payload de create_task pra uma pasta, a partir da config do modal."""
    data_iso = config["data_iso"]  # ISO com data+hora (endDateTime/startDateTime)
    office = config.get("office_external_id")
    return {
        "description": config.get("descricao") or config.get("subtipo_nome") or "Tarefa",
        "priority": config.get("prioridade") or "Normal",
        "startDateTime": data_iso,
        "endDateTime": data_iso,
        "publishDate": config.get("publish_date_iso") or data_iso,
        "notes": config.get("observacoes"),
        "status": {"id": DEFAULT_TASK_STATUS_ID},
        "typeId": config["type_id"],
        "subTypeId": config["subtype_id"],
        "responsibleOfficeId": office,
        "originOfficeId": office,
        "participants": [
            {
                "contact": {"id": item["responsavel_external_id"]},
                "isResponsible": True,
                "isExecuter": True,
                "isRequester": True,
            }
        ],
    }


def _ja_tem_tarefa_aberta(client, lawsuit_id: int, subtype_id: int) -> bool:
    """Anti-duplicidade: a pasta já tem tarefa ABERTA (não cumprida/cancelada)
    desse subtipo? Evita recriar o que o operador já agendou. Best-effort —
    falha na checagem NÃO bloqueia (retorna False = segue e cria)."""
    try:
        # statusId 2/3 = cumprida/cancelada (fechadas); qualquer outra é aberta.
        tarefas = client.find_tasks_for_lawsuit(lawsuit_id, subtype_id=subtype_id, top=30)
        for t in tarefas or []:
            if t.get("statusId") not in (2, 3):
                return True
    except Exception:  # noqa: BLE001
        logger.warning("Agendamento: checagem anti-dup falhou (pasta %s).", lawsuit_id)
    return False


def _run_job(job_id: int) -> None:
    from app.db.session import SessionLocal
    from app.services.legal_one_client import LegalOneApiClient

    db = SessionLocal()
    try:
        job = db.get(BbAtivosAgendamentoJob, job_id)
        if not job:
            return
        config = job.config or {}
        itens = list(job.itens or [])
        client = None if job.dry_run else LegalOneApiClient()

        for it in itens:
            if it.get("status") in ("criado", "pulado", "falha"):
                continue
            lawsuit_id = it["lawsuit_id"]
            try:
                if job.dry_run:
                    it["status"] = "simulado"
                else:
                    if _ja_tem_tarefa_aberta(client, lawsuit_id, config["subtype_id"]):
                        it["status"] = "pulado"
                        it["erro"] = "Já havia tarefa aberta desse subtipo na pasta."
                        job.pulados += 1
                    else:
                        criada = client.create_task(_payload_tarefa(config, it))
                        if not criada or not criada.get("id"):
                            raise RuntimeError(
                                client.format_last_create_task_error()
                                if hasattr(client, "format_last_create_task_error")
                                else "L1 não retornou id da tarefa."
                            )
                        tid = criada["id"]
                        client.link_task_to_lawsuit(
                            tid, {"linkType": "Litigation", "linkId": lawsuit_id}
                        )
                        it["status"] = "criado"
                        it["task_id"] = tid
                        job.criados += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception("Agendamento: falha na pasta %s (job %s).", lawsuit_id, job_id)
                it["status"] = "falha"
                it["erro"] = str(exc)[:300]
                job.falhas += 1
            finally:
                job.processados += 1
                # Reatribui pra o SQLAlchemy detectar a mutação do JSONB.
                job.itens = list(itens)
                db.commit()

        job.status = AGEND_CONCLUIDO
        job.concluido_em = datetime.now(timezone.utc)
        db.commit()
        logger.info(
            "Agendamento job %s concluído (dry_run=%s criados=%s pulados=%s falhas=%s).",
            job_id, job.dry_run, job.criados, job.pulados, job.falhas,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Agendamento: erro geral no job %s.", job_id)
        try:
            job = db.get(BbAtivosAgendamentoJob, job_id)
            if job:
                job.status = AGEND_ERRO
                job.erro = "Erro inesperado no agendamento."
                db.commit()
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()


def disparar(
    db: Session,
    *,
    duplicado_ids: list[int],
    responsavel_ids: list[int],
    dividir_igual: bool,
    config: dict,
    dry_run: bool,
    user_id: Optional[int],
) -> dict:
    """Cria o job, persiste o plano e sobe o worker em background."""
    plano = montar_plano(
        db, duplicado_ids=duplicado_ids,
        responsavel_ids=responsavel_ids, dividir_igual=dividir_igual,
    )
    if not plano:
        raise ValueError("Nenhuma pasta com L1 resolvida entre os selecionados — resolva as pastas primeiro.")

    itens = [{**p, "status": "pendente", "task_id": None, "erro": None} for p in plano]
    job = BbAtivosAgendamentoJob(
        status=AGEND_EM_ANDAMENTO, dry_run=dry_run,
        total=len(itens), config=config, itens=itens,
        disparado_por_user_id=user_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    threading.Thread(target=_run_job, args=(job.id,), daemon=True).start()
    return {"job_id": job.id, "total": len(itens), "dry_run": dry_run}


def status(db: Session, job_id: int) -> Optional[dict]:
    job = db.get(BbAtivosAgendamentoJob, job_id)
    if not job:
        return None
    return {
        "id": job.id,
        "status": job.status,
        "dry_run": job.dry_run,
        "total": job.total,
        "processados": job.processados,
        "criados": job.criados,
        "pulados": job.pulados,
        "falhas": job.falhas,
        "erro": job.erro,
        "itens": job.itens or [],
        "iniciado_em": job.iniciado_em.isoformat() if job.iniciado_em else None,
        "concluido_em": job.concluido_em.isoformat() if job.concluido_em else None,
    }
