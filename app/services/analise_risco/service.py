"""Análise de Risco BB Réu — sync do espelho e listagem do painel.

FONTE do sync: a tabela `perf_l1_tarefa` (Agenda Analytics, ingerido
diariamente pelo Minha Equipe). Custo zero de API do L1: filtramos por NOME de
subtipo (configurável em app_settings) e fazemos upsert por `l1_task_id` na
tabela persistente `arb_analise_risco_tarefa` — o espelho perf é REPLACE
diário, a nossa tabela é o histórico que sobrevive.

O sync roda "lazy": o GET do painel dispara quando o último sync passou de
SYNC_STALE_SECONDS (então o painel fica fresco logo depois da ingestão diária,
sem worker novo). Quando a tarefa vira "Cumprido" no L1, a linha entra na fila
da esteira do portal (verif_status = NA_FILA) — o worker do card 3 consome.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.analise_risco import (
    AnaliseRiscoTarefa,
    VERIF_NA_FILA,
    VERIF_PENDENTE,
)
from app.models.performance import PerfTarefa
from app.services.app_settings import get_setting, set_setting

logger = logging.getLogger(__name__)

# Nomes de subtipo (CSV, case-insensitive) que identificam a tarefa de Análise
# de Risco no L1. Ajustável sem deploy via app_settings.
SUBTIPOS_KEY = "analise_risco_subtipos"
SUBTIPOS_DEFAULT = "Análise de Risco"

LAST_SYNC_KEY = "analise_risco_last_sync_at"
SYNC_STALE_SECONDS = 15 * 60

STATUS_CUMPRIDO = "Cumprido"
STATUS_PENDENTE = "Pendente"


def subtipos_configurados() -> list[str]:
    raw = get_setting(SUBTIPOS_KEY, SUBTIPOS_DEFAULT) or SUBTIPOS_DEFAULT
    return [s.strip() for s in raw.split(",") if s.strip()]


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def sync_do_espelho(db: Session) -> dict:
    """Upsert das tarefas do(s) subtipo(s) a partir do perf_l1_tarefa.

    Nunca deleta: tarefa que sumir do espelho (ex.: cancelada no L1) mantém o
    último estado conhecido aqui. Transição Pendente -> Cumprido põe a linha na
    fila da esteira (NA_FILA) e carimba concluida_em/cumprida_por.
    """
    nomes = [n.lower() for n in subtipos_configurados()]
    rows = (
        db.query(PerfTarefa)
        .filter(func.lower(PerfTarefa.subtipo).in_(nomes))
        .filter(PerfTarefa.l1_task_id.isnot(None))
        .all()
    )

    existentes = {
        r.l1_task_id: r for r in db.query(AnaliseRiscoTarefa).all()
    }

    inseridas = atualizadas = para_fila = 0
    vistos: set[int] = set()
    for p in rows:
        tid = int(p.l1_task_id)
        # O relatório pode ter mais de uma linha por tarefa (multi-envolvido);
        # a primeira vence — as demais só complementam nada.
        if tid in vistos:
            continue
        vistos.add(tid)

        row = existentes.get(tid)
        if row is None:
            row = AnaliseRiscoTarefa(
                l1_task_id=tid,
                verif_status=VERIF_PENDENTE,
            )
            db.add(row)
            existentes[tid] = row
            inseridas += 1
        else:
            atualizadas += 1

        row.subtipo = p.subtipo
        row.responsavel_nome = p.envolvido_nome or row.responsavel_nome
        row.npj = p.pasta or row.npj
        row.cnj = p.cnj or row.cnj
        row.agendada_em = p.cadastrado_em or row.agendada_em
        row.prazo = p.prazo_previsto or row.prazo

        status_novo = p.status
        if status_novo == STATUS_CUMPRIDO:
            row.cumprida_por_nome = p.cumprido_por_nome or row.cumprida_por_nome
            row.concluida_em = p.concluido_em or row.concluida_em
            # Entrou como cumprida: agenda a verificação no portal (uma vez).
            if row.status_l1 != STATUS_CUMPRIDO and row.verif_status == VERIF_PENDENTE:
                row.verif_status = VERIF_NA_FILA
                para_fila += 1
        row.status_l1 = status_novo

    db.commit()
    set_setting(LAST_SYNC_KEY, _agora().isoformat())

    resultado = {
        "fonte": len(rows),
        "tarefas": len(vistos),
        "inseridas": inseridas,
        "atualizadas": atualizadas,
        "enfileiradas_verificacao": para_fila,
        "subtipos": subtipos_configurados(),
    }
    logger.info("Análise de Risco sync: %s", resultado)
    return resultado


def sync_se_stale(db: Session) -> Optional[dict]:
    """Roda o sync só se o último passou de SYNC_STALE_SECONDS (chamado pelo
    GET do painel — mantém fresco sem worker dedicado)."""
    last = get_setting(LAST_SYNC_KEY)
    if last:
        try:
            idade = (_agora() - datetime.fromisoformat(last)).total_seconds()
            if 0 <= idade < SYNC_STALE_SECONDS:
                return None
        except ValueError:
            pass
    try:
        return sync_do_espelho(db)
    except Exception:  # noqa: BLE001 — painel nunca cai por falha de sync
        logger.exception("Análise de Risco: falha no sync lazy — painel segue com o dado atual.")
        db.rollback()
        return None


def listar(
    db: Session,
    *,
    status_l1: Optional[str] = None,
    responsavel: Optional[str] = None,
    divergente: Optional[bool] = None,
    busca: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    q = db.query(AnaliseRiscoTarefa)
    if status_l1:
        q = q.filter(AnaliseRiscoTarefa.status_l1 == status_l1)
    if responsavel:
        q = q.filter(AnaliseRiscoTarefa.responsavel_nome == responsavel)
    if divergente is not None:
        q = q.filter(AnaliseRiscoTarefa.divergente.is_(divergente))
    if busca:
        like = f"%{busca.strip()}%"
        q = q.filter(
            or_(
                AnaliseRiscoTarefa.npj.ilike(like),
                AnaliseRiscoTarefa.cnj.ilike(like),
                AnaliseRiscoTarefa.responsavel_nome.ilike(like),
            )
        )

    total = q.count()
    itens = (
        q.order_by(
            AnaliseRiscoTarefa.prazo.asc().nullslast(),
            AnaliseRiscoTarefa.id.asc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )

    # KPIs globais (independem do filtro/página) — mesma filosofia do OneRequest.
    agora = _agora()
    base = db.query(AnaliseRiscoTarefa)
    abertas = base.filter(AnaliseRiscoTarefa.status_l1 == STATUS_PENDENTE).count()
    vencidas = (
        base.filter(
            AnaliseRiscoTarefa.status_l1 == STATUS_PENDENTE,
            AnaliseRiscoTarefa.prazo.isnot(None),
            AnaliseRiscoTarefa.prazo < agora,
        ).count()
    )
    cumpridas = base.filter(AnaliseRiscoTarefa.status_l1 == STATUS_CUMPRIDO).count()
    aguardando_verif = base.filter(AnaliseRiscoTarefa.verif_status == VERIF_NA_FILA).count()
    divergentes = base.filter(AnaliseRiscoTarefa.divergente.is_(True)).count()

    responsaveis = [
        r[0]
        for r in db.query(AnaliseRiscoTarefa.responsavel_nome)
        .filter(AnaliseRiscoTarefa.responsavel_nome.isnot(None))
        .distinct()
        .order_by(AnaliseRiscoTarefa.responsavel_nome.asc())
        .all()
    ]

    def _iso(dt) -> Optional[str]:
        return dt.isoformat() if dt else None

    return {
        "total": total,
        "kpis": {
            "abertas": abertas,
            "vencidas": vencidas,
            "cumpridas": cumpridas,
            "aguardando_verificacao": aguardando_verif,
            "divergentes": divergentes,
        },
        "last_sync_at": get_setting(LAST_SYNC_KEY),
        "subtipos": subtipos_configurados(),
        "responsaveis": responsaveis,
        "items": [
            {
                "id": r.id,
                "l1_task_id": r.l1_task_id,
                "subtipo": r.subtipo,
                "responsavel_nome": r.responsavel_nome,
                "cumprida_por_nome": r.cumprida_por_nome,
                "npj": r.npj,
                "cnj": r.cnj,
                "agendada_em": _iso(r.agendada_em),
                "prazo": _iso(r.prazo),
                "status_l1": r.status_l1,
                "concluida_em": _iso(r.concluida_em),
                "verif_status": r.verif_status,
                "portal_analise_feita": r.portal_analise_feita,
                "portal_estado": r.portal_estado,
                "portal_exito": r.portal_exito,
                "portal_verificado_em": _iso(r.portal_verificado_em),
                "divergente": r.divergente,
                "trat_status": r.trat_status,
                "trat_anotacao": r.trat_anotacao,
                "trat_em": _iso(r.trat_em),
            }
            for r in itens
        ],
    }
