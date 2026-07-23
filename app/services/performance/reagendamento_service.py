"""Detecção e board de REAGENDAMENTOS (adiamentos de prazo).

O calo histórico: tarefa que a pessoa empurra o prazo pra frente pra não vencer.
Sempre foi invisível porque só olhávamos o estado atual. A sacada: o L1 guarda
os relatórios "Agenda Analytics" e nós ingerimos 2x/dia — comparar a foto da
MANHÃ (07h) com a da NOITE (19h) do mesmo dia isola o que a pessoa mexeu no
prazo DURANTE o dia de trabalho.

Fluxo (2 crons):
- 07h  → `capturar_manha`: grava o prazo de cada tarefa pendente (baseline).
- 19h  → `detectar_noite`: compara a noite contra a manhã e grava os ADIAMENTOS
         (prazo empurrado pra frente). Antecipações são ignoradas.

Ambos leem de `perf_l1_tarefa`, que o job de ingestão do bracket refresca ANTES
(gerar_e_ingerir). O board lê só de `perf_reagendamento` — agregação barata.
"""
from __future__ import annotations

import datetime as _dt
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_BRT = "America/Sao_Paulo"

try:
    from zoneinfo import ZoneInfo

    _TZ = ZoneInfo(_BRT)
except Exception:  # pragma: no cover
    _TZ = None


def _hoje_brt() -> _dt.date:
    return (_dt.datetime.now(tz=_TZ) if _TZ else _dt.datetime.now()).date()


def _dia_brt_meia_noite() -> _dt.datetime:
    """Hoje 00:00 no fuso BRT (aware) — a chave `dia` do bracket."""
    d = _hoje_brt()
    if _TZ:
        return _dt.datetime(d.year, d.month, d.day, tzinfo=_TZ)
    return _dt.datetime(d.year, d.month, d.day)


def capturar_manha(db: Session) -> dict:
    """Grava a foto da MANHÃ: prazo de cada tarefa PENDENTE com prazo, hoje.

    Replace total do dia (limpa perf_prazo_manha e regrava do snapshot atual).
    Deve rodar logo após o ingest da manhã (perf_l1_tarefa já refrescada)."""
    dia = _dia_brt_meia_noite()
    db.execute(text("DELETE FROM perf_prazo_manha"))
    inserido = db.execute(
        text(
            """
            INSERT INTO perf_prazo_manha
                (l1_task_id, dia, prazo, pessoa_id, pessoa_nome, equipe, subtipo, pasta, cnj, capturado_em)
            SELECT DISTINCT ON (t.l1_task_id)
                   t.l1_task_id, :dia, t.prazo_previsto, t.pessoa_id, p.nome, p.equipe,
                   t.subtipo, t.pasta, t.cnj, now()
            FROM perf_l1_tarefa t
            LEFT JOIN perf_pessoa p ON p.id = t.pessoa_id
            WHERE t.l1_task_id IS NOT NULL
              AND t.status = 'Pendente'
              AND t.prazo_previsto IS NOT NULL
            ORDER BY t.l1_task_id
            """
        ),
        {"dia": dia},
    ).rowcount
    db.commit()
    logger.info("Reagendamento: baseline da manhã gravado — %s tarefa(s) pendentes.", inserido)
    return {"ok": True, "baseline": inserido, "dia": dia.date().isoformat()}


def detectar_noite(db: Session) -> dict:
    """Compara a NOITE (perf_l1_tarefa atual) com a manhã e grava os ADIAMENTOS.

    Adiamento = tarefa presente nas duas fotos, ainda Pendente à noite, com a
    conclusão prevista EMPURRADA pra frente (prazo_noite > prazo_manha). Só do
    dia da baseline. Idempotente: apaga os eventos do dia antes de regravar."""
    dia = _dia_brt_meia_noite()
    base = db.execute(
        text("SELECT count(*), min(dia) FROM perf_prazo_manha")
    ).fetchone()
    if not base or not base[0]:
        logger.warning("Reagendamento: sem baseline da manhã — detecção da noite pulada.")
        return {"ok": False, "motivo": "sem_baseline_manha"}

    db.execute(text("DELETE FROM perf_reagendamento WHERE dia = :dia"), {"dia": dia})
    inserido = db.execute(
        text(
            """
            INSERT INTO perf_reagendamento
                (dia, l1_task_id, pessoa_id, pessoa_nome, equipe, subtipo, pasta, cnj,
                 prazo_de, prazo_para, dias_adiado, era_fatal_hoje, detectado_em)
            SELECT :dia, m.l1_task_id,
                   COALESCE(t.pessoa_id, m.pessoa_id),
                   COALESCE(p.nome, m.pessoa_nome),
                   COALESCE(p.equipe, m.equipe),
                   COALESCE(t.subtipo, m.subtipo),
                   COALESCE(t.pasta, m.pasta),
                   COALESCE(t.cnj, m.cnj),
                   m.prazo, t.prazo_previsto,
                   ( (t.prazo_previsto AT TIME ZONE :tz)::date
                     - (m.prazo AT TIME ZONE :tz)::date ),
                   ( (m.prazo AT TIME ZONE :tz)::date = (now() AT TIME ZONE :tz)::date ),
                   now()
            FROM perf_prazo_manha m
            JOIN perf_l1_tarefa t ON t.l1_task_id = m.l1_task_id
            LEFT JOIN perf_pessoa p ON p.id = t.pessoa_id
            WHERE t.status = 'Pendente'
              AND t.prazo_previsto IS NOT NULL
              AND m.prazo IS NOT NULL
              -- ADIAMENTO: empurrado pra frente (compara por DATA, BRT). Mudança
              -- só de horário no MESMO dia não conta como adiamento.
              AND (t.prazo_previsto AT TIME ZONE :tz)::date
                  > (m.prazo AT TIME ZONE :tz)::date
            """
        ),
        {"dia": dia, "tz": _BRT},
    ).rowcount
    db.commit()
    logger.info("Reagendamento: %s adiamento(s) detectado(s) no bracket de %s.",
                inserido, dia.date().isoformat())
    return {"ok": True, "adiamentos": inserido, "dia": dia.date().isoformat()}


# ── Board / leitura ─────────────────────────────────────────────────────────

def resumo(db: Session, *, equipe: str | None = None, dias: int = 30) -> dict:
    """Agrega perf_reagendamento pro board: KPIs + por pessoa + por dia + por
    subtipo + reincidentes (tarefa adiada em vários dias). Recorte por equipe e
    janela de `dias`."""
    desde = _dia_brt_meia_noite() - _dt.timedelta(days=max(1, dias))
    where = "dia >= :desde"
    params: dict = {"desde": desde}
    if equipe:
        where += " AND equipe = :equipe"
        params["equipe"] = equipe

    kpi = db.execute(
        text(
            f"""
            SELECT count(*) AS total,
                   count(DISTINCT l1_task_id) AS tarefas,
                   count(DISTINCT pessoa_id) AS pessoas,
                   count(*) FILTER (WHERE era_fatal_hoje) AS fatais_empurrados,
                   COALESCE(round(avg(dias_adiado)::numeric, 1), 0) AS dias_medio,
                   COALESCE(max(dias_adiado), 0) AS dias_max
            FROM perf_reagendamento WHERE {where}
            """
        ),
        params,
    ).fetchone()

    por_pessoa = db.execute(
        text(
            f"""
            SELECT pessoa_id, COALESCE(NULLIF(pessoa_nome, ''), '(sem responsável)') AS nome,
                   count(*) AS total,
                   count(*) FILTER (WHERE era_fatal_hoje) AS fatais,
                   COALESCE(round(avg(dias_adiado)::numeric, 1), 0) AS dias_medio,
                   count(DISTINCT l1_task_id) AS tarefas
            FROM perf_reagendamento WHERE {where}
            GROUP BY pessoa_id, pessoa_nome
            ORDER BY total DESC
            LIMIT 60
            """
        ),
        params,
    ).fetchall()

    por_dia = db.execute(
        text(
            f"""
            SELECT (dia AT TIME ZONE :tz)::date AS d,
                   count(*) AS total,
                   count(*) FILTER (WHERE era_fatal_hoje) AS fatais
            FROM perf_reagendamento WHERE {where}
            GROUP BY 1 ORDER BY 1
            """
        ),
        {**params, "tz": _BRT},
    ).fetchall()

    por_subtipo = db.execute(
        text(
            f"""
            SELECT COALESCE(NULLIF(subtipo, ''), '(sem subtipo)') AS subtipo, count(*) AS total
            FROM perf_reagendamento WHERE {where}
            GROUP BY 1 ORDER BY 2 DESC LIMIT 15
            """
        ),
        params,
    ).fetchall()

    # Reincidentes: a MESMA tarefa adiada em vários dias distintos (a "bola pra
    # frente" repetida — o pior caso de gestão).
    reincidentes = db.execute(
        text(
            f"""
            SELECT l1_task_id,
                   max(pessoa_nome) AS pessoa,
                   max(subtipo) AS subtipo,
                   max(pasta) AS pasta,
                   max(cnj) AS cnj,
                   count(DISTINCT dia) AS vezes,
                   min((prazo_de AT TIME ZONE :tz)::date) AS primeiro_prazo,
                   max((prazo_para AT TIME ZONE :tz)::date) AS ultimo_prazo,
                   sum(dias_adiado) AS dias_total
            FROM perf_reagendamento WHERE {where}
            GROUP BY l1_task_id
            HAVING count(DISTINCT dia) > 1
            ORDER BY vezes DESC, dias_total DESC
            LIMIT 40
            """
        ),
        {**params, "tz": _BRT},
    ).fetchall()

    def _iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v

    return {
        "kpis": {
            "total": kpi.total or 0,
            "tarefas": kpi.tarefas or 0,
            "pessoas": kpi.pessoas or 0,
            "fatais_empurrados": kpi.fatais_empurrados or 0,
            "dias_medio": float(kpi.dias_medio or 0),
            "dias_max": kpi.dias_max or 0,
        },
        "por_pessoa": [
            {
                "pessoa_id": r.pessoa_id, "nome": r.nome, "total": r.total,
                "fatais": r.fatais, "dias_medio": float(r.dias_medio or 0), "tarefas": r.tarefas,
            }
            for r in por_pessoa
        ],
        "por_dia": [
            {"dia": _iso(r.d), "total": r.total, "fatais": r.fatais} for r in por_dia
        ],
        "por_subtipo": [{"subtipo": r.subtipo, "total": r.total} for r in por_subtipo],
        "reincidentes": [
            {
                "l1_task_id": r.l1_task_id, "pessoa": r.pessoa, "subtipo": r.subtipo,
                "pasta": r.pasta, "cnj": r.cnj, "vezes": r.vezes,
                "primeiro_prazo": _iso(r.primeiro_prazo), "ultimo_prazo": _iso(r.ultimo_prazo),
                "dias_total": r.dias_total,
            }
            for r in reincidentes
        ],
    }


def lista_eventos(
    db: Session, *, equipe: str | None = None, pessoa_id: int | None = None,
    dia: str | None = None, subtipo: str | None = None,
    dias: int = 30, limit: int = 100, offset: int = 0,
) -> dict:
    """Lista paginada dos eventos de adiamento (drill ao clicar num gráfico).

    Filtros combináveis: `pessoa_id` (clicou na barra da pessoa), `dia`
    (YYYY-MM-DD, clicou na barra do dia) e `subtipo` (clicou na barra do tipo).
    A descrição livre da tarefa NÃO vive aqui — o front enriquece ao vivo do L1
    por l1_task_id (mesmo caminho do Balanceador)."""
    desde = _dia_brt_meia_noite() - _dt.timedelta(days=max(1, dias))
    where = "dia >= :desde"
    params: dict = {"desde": desde, "limit": limit, "offset": offset}
    if equipe:
        where += " AND equipe = :equipe"
        params["equipe"] = equipe
    if pessoa_id:
        where += " AND pessoa_id = :pid"
        params["pid"] = pessoa_id
    if dia:
        where += " AND (dia AT TIME ZONE :tzf)::date = :dia"
        params["tzf"] = _BRT
        params["dia"] = dia
    if subtipo:
        # "(sem subtipo)" no gráfico = subtipo NULL/vazio no banco.
        if subtipo == "(sem subtipo)":
            where += " AND (subtipo IS NULL OR subtipo = '')"
        else:
            where += " AND subtipo = :sub"
            params["sub"] = subtipo

    total = db.execute(
        text(f"SELECT count(*) FROM perf_reagendamento WHERE {where}"), params
    ).scalar()
    rows = db.execute(
        text(
            f"""
            SELECT (dia AT TIME ZONE :tz)::date AS d, l1_task_id, pessoa_nome, subtipo,
                   pasta, cnj, prazo_de, prazo_para, dias_adiado, era_fatal_hoje
            FROM perf_reagendamento WHERE {where}
            ORDER BY dia DESC, dias_adiado DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        {**params, "tz": _BRT},
    ).fetchall()

    def _iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v

    return {
        "total": total or 0,
        "items": [
            {
                "dia": _iso(r.d), "l1_task_id": r.l1_task_id, "pessoa": r.pessoa_nome,
                "subtipo": r.subtipo, "pasta": r.pasta, "cnj": r.cnj,
                "prazo_de": _iso(r.prazo_de), "prazo_para": _iso(r.prazo_para),
                "dias_adiado": r.dias_adiado, "era_fatal_hoje": r.era_fatal_hoje,
            }
            for r in rows
        ],
    }
