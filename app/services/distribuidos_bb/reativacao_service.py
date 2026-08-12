"""Reativação de pasta fechada que o cliente reenviou (+ tarefa no L1).

## Por que isso existe

Quando a ingestão encontra pasta do mesmo cliente pro CNJ, ela NÃO recadastra
(evita duplicar) — correto. O que faltava era olhar o **status** dessa pasta:
baixada/arquivada e ativa davam no mesmo "já cadastrado", e o processo saía da
fila sem tarefa e sem alerta.

Na carteira do Banco Master isso é a regra, não a exceção: de 8.756 pastas,
6.494 estão arquivadas e 215 baixadas (77%). Quando o cliente reenvia um desses,
o processo voltou a andar — a pasta precisa ser REATIVADA e alguém precisa
receber o trabalho.

## Como reativa

Dois caminhos, nessa ordem (a ordem importa e não é preferência estética):

1. `PATCH /Lawsuits({id})` com `{"statusId": 1, "closingDate": null}` — ativa E
   limpa a data de baixa numa tacada. Só funciona nas pastas "limpas".
2. Caminho web (`ModalAlterarEmLote`, CampoId=3) — necessário porque a maioria
   das pastas tem a trava de honorário obrigatório do tenant, que faz o PATCH
   devolver 400 reclamando de custom fields que o próprio schema OData não
   aceita no PATCH. Limitação: o web não limpa `closingDate` (cosmético).

## Progresso

Reusa `BbAtivosAgendamentoJob` e o worker `_run_job` do agendamento em lote —
mesmo modelo, mesma barra, mesma tela de progresso. O que muda é a origem dos
itens (processos na fila de reativação, não duplicados da Ativos) e o passo de
reativação que roda ANTES de criar a tarefa.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.distribuidos_bb import (
    AGEND_EM_ANDAMENTO,
    L1_PASTA_ATIVO,
    L1_PASTA_LABEL,
    REATIV_DISPENSADA,
    REATIV_PENDENTE,
    BbAtivosAgendamentoJob,
    BbProcesso,
)
from app.models.legal_one import LegalOneUser

logger = logging.getLogger("distribuidos_bb.reativacao")


# ── Fila ──────────────────────────────────────────────────────────────────

def listar_pendentes(
    db: Session,
    *,
    cliente: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Processos cuja pasta no L1 está fechada e que o cliente reenviou."""
    q = (
        db.query(BbProcesso)
        .filter(
            BbProcesso.reativacao_status == REATIV_PENDENTE,
            BbProcesso.l1_lawsuit_id.isnot(None),
        )
        .order_by(BbProcesso.id.desc())
    )
    if cliente:
        q = q.filter(BbProcesso.cliente == cliente)
    total = q.count()
    rows = q.limit(limit).offset(offset).all()

    nomes = {
        u.id: u.name
        for u in db.query(LegalOneUser)
        .filter(
            LegalOneUser.id.in_({p.responsavel_user_id for p in rows if p.responsavel_user_id} or {0})
        )
        .all()
    }
    return {
        "total": total,
        "items": [
            {
                "id": p.id,
                "cliente": p.cliente,
                "cnj": p.cnj,
                "adverso": p.adverso_principal,
                "l1_lawsuit_id": p.l1_lawsuit_id,
                "l1_folder": p.l1_folder,
                "l1_status_id": p.l1_status_id,
                "l1_status": L1_PASTA_LABEL.get(p.l1_status_id or 0, "—"),
                "escritorio_path": p.escritorio_path,
                "responsavel_id": p.responsavel_user_id,
                "responsavel_nome": nomes.get(p.responsavel_user_id),
                "detectado_em": (
                    p.cadastro_confirmado_em.isoformat()
                    if p.cadastro_confirmado_em else None
                ),
            }
            for p in rows
        ],
    }


def contar_pendentes(db: Session, *, cliente: Optional[str] = None) -> int:
    """Badge da fila (a tela chama sempre; mantém barato)."""
    q = db.query(BbProcesso).filter(BbProcesso.reativacao_status == REATIV_PENDENTE)
    if cliente:
        q = q.filter(BbProcesso.cliente == cliente)
    return q.count()


def dispensar(db: Session, *, processo_ids: list[int]) -> int:
    """Tira da fila SEM reativar — o operador olhou e a pasta segue fechada.

    Existe para a fila não virar ruído permanente: sem isso, o caso legítimo
    (cliente reenviou algo que realmente acabou) ficaria pendente para sempre e
    o operador aprenderia a ignorar o badge.
    """
    n = (
        db.query(BbProcesso)
        .filter(
            BbProcesso.id.in_(processo_ids),
            BbProcesso.reativacao_status == REATIV_PENDENTE,
        )
        .update({BbProcesso.reativacao_status: REATIV_DISPENSADA},
                synchronize_session=False)
    )
    db.commit()
    return int(n or 0)


# ── Reativação da pasta no L1 ─────────────────────────────────────────────

def reativar_pasta(client, lawsuit_id: int) -> dict[str, Any]:
    """Volta a pasta pro status Ativo. Devolve {ok, via, erro}.

    Tenta o PATCH (que também limpa a data de baixa) e cai pro caminho web
    quando a trava de tenant recusa — ver o docstring do módulo.
    """
    # 1) REST: resolve tudo quando a pasta não tem a trava.
    try:
        payload = {"statusId": L1_PASTA_ATIVO, "closingDate": None}
        ultimo_erro = None
        for entity in ("/Lawsuits", "/Litigations"):
            try:
                r = client._request_with_retry(
                    "PATCH", f"{client.base_url}{entity}/{lawsuit_id}", json=payload,
                )
                if r.status_code in (200, 204):
                    return {"ok": True, "via": "PATCH", "erro": None}
                ultimo_erro = f"HTTP {r.status_code}"
            except Exception as exc:  # noqa: BLE001
                ultimo_erro = str(exc)[:200]
                # 404 = id pertence à outra entity; qualquer outro erro é real
                # (validação/permissão) e trocar de entity não ajuda.
                resp = getattr(exc, "response", None)
                if resp is not None and resp.status_code != 404:
                    break
        logger.info(
            "Reativação: PATCH não serviu na pasta %s (%s) — indo pro caminho web.",
            lawsuit_id, ultimo_erro,
        )
    except Exception:  # noqa: BLE001
        logger.warning("Reativação: PATCH falhou na pasta %s.", lawsuit_id)

    # 2) Web: obrigatório na maioria das pastas (trava de honorário do tenant).
    try:
        from app.services.prazos_iniciais.legacy_task_http_cancellation_service import (
            LegacyTaskHttpCancellationService,
        )

        svc = LegacyTaskHttpCancellationService()
        svc.post_alterar_status_pasta(
            lawsuit_ids=[int(lawsuit_id)], status_id=L1_PASTA_ATIVO,
        )
        return {"ok": True, "via": "web", "erro": None}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Reativação: caminho web falhou na pasta %s.", lawsuit_id)
        return {"ok": False, "via": "web", "erro": str(exc)[:300]}


def confirmar_reativacao(client, lawsuit_id: int) -> Optional[int]:
    """Relê o status da pasta. O POST web responde antes de aplicar (assíncrono),
    então Success=true NÃO é confirmação — quem confirma é a releitura."""
    for entity in ("/Lawsuits", "/Litigations"):
        try:
            r = client._request_with_retry(
                "GET", f"{client.base_url}{entity}({lawsuit_id})",
                params={"$select": "id,statusId"},
            )
            if r.status_code == 200:
                return r.json().get("statusId")
        except Exception:  # noqa: BLE001
            continue
    return None


# ── Plano + disparo (reusa o job/worker do agendamento em lote) ───────────

def montar_plano(
    db: Session,
    *,
    processo_ids: list[int],
    responsavel_ids: list[int],
    dividir_igual: bool,
) -> list[dict[str, Any]]:
    """Casa cada processo da fila a um responsável, no formato que o worker
    do agendamento já consome (`itens` do job).

    Quando `responsavel_ids` vem vazio, cada processo fica com o responsável
    que ele JÁ tem — que no Master é o fixo da carteira. É o default certo:
    a pasta volta pra quem já cuidava dela.
    """
    procs = (
        db.query(BbProcesso)
        .filter(
            BbProcesso.id.in_(processo_ids),
            BbProcesso.l1_lawsuit_id.isnot(None),
        )
        .order_by(BbProcesso.id)
        .all()
    )
    if not procs:
        return []

    ids_necessarios = set(responsavel_ids) | {
        p.responsavel_user_id for p in procs if p.responsavel_user_id
    }
    users = {
        u.id: u
        for u in db.query(LegalOneUser).filter(LegalOneUser.id.in_(ids_necessarios or {0})).all()
    }
    alvos = [uid for uid in responsavel_ids if uid in users]

    plano: list[dict[str, Any]] = []
    for i, p in enumerate(procs):
        if alvos:
            uid = alvos[i % len(alvos)] if dividir_igual else alvos[0]
        else:
            uid = p.responsavel_user_id
        u = users.get(uid) if uid else None
        plano.append({
            "processo_id": p.id,
            "cnj": p.cnj,
            "lawsuit_id": p.l1_lawsuit_id,
            "folder": p.l1_folder,
            "parte": p.adverso_principal,
            "l1_status_id": p.l1_status_id,
            "l1_status": L1_PASTA_LABEL.get(p.l1_status_id or 0, "—"),
            # O worker usa esta flag pra decidir se reativa antes de agendar.
            "precisa_reativar": True,
            "responsavel_id": u.id if u else None,
            "responsavel_external_id": u.external_id if u else None,
            "responsavel_nome": u.name if u else None,
        })
    return plano


def resumo_por_responsavel(plano: list[dict]) -> list[dict]:
    agg: dict[Any, dict] = {}
    for p in plano:
        a = agg.setdefault(p["responsavel_id"], {
            "responsavel_id": p["responsavel_id"],
            "responsavel_nome": p["responsavel_nome"] or "— sem responsável",
            "total": 0,
        })
        a["total"] += 1
    return sorted(agg.values(), key=lambda x: (-x["total"], x["responsavel_nome"] or ""))


def preview(
    db: Session,
    *,
    processo_ids: list[int],
    responsavel_ids: list[int],
    dividir_igual: bool,
) -> dict[str, Any]:
    """Dry-run leve: o que seria reativado e pra quem iria a tarefa. Não escreve."""
    plano = montar_plano(
        db, processo_ids=processo_ids, responsavel_ids=responsavel_ids,
        dividir_igual=dividir_igual,
    )
    sem_resp = [p for p in plano if not p.get("responsavel_external_id")]
    return {
        "total": len(plano),
        "por_responsavel": resumo_por_responsavel(plano),
        "sem_responsavel": len(sem_resp),
        "itens": plano[:50],
    }


def disparar(
    db: Session,
    *,
    processo_ids: list[int],
    responsavel_ids: list[int],
    dividir_igual: bool,
    config: dict[str, Any],
    dry_run: bool,
    user_id: Optional[int],
) -> dict[str, Any]:
    """Cria o job e solta o worker. Devolve {job_id, total}."""
    plano = montar_plano(
        db, processo_ids=processo_ids, responsavel_ids=responsavel_ids,
        dividir_igual=dividir_igual,
    )
    if not plano:
        raise ValueError(
            "Nenhum processo elegível (é preciso ter pasta no Legal One resolvida)."
        )

    cfg = dict(config or {})
    # A flag que liga o passo de reativação no worker compartilhado.
    cfg["reativar_pasta"] = True
    job = BbAtivosAgendamentoJob(
        status=AGEND_EM_ANDAMENTO,
        dry_run=bool(dry_run),
        total=len(plano),
        config=cfg,
        itens=plano,
        disparado_por_user_id=user_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    from app.services.distribuidos_bb.ativos_agendamento_service import _run_job

    threading.Thread(target=_run_job, args=(job.id,), daemon=True).start()
    return {"job_id": job.id, "total": len(plano)}
