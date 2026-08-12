"""Enriquecimento de publicações com as ETIQUETAS (tags) do processo no L1.

Motivação: uma publicação de processo estratégico foi perdida porque o Flow
não mostrava a etiqueta do processo — o operador tratou sem saber que era
especial. A API REST do L1 NÃO expõe etiquetas (investigação 2026-07-23);
a única leitura é pelo caminho web: a página de edição do processo renderiza
o hidden `selectedTagsHidden` com a lista JSON [{Id, Name, ClassName, ColorId}].

Fluxo:
  - Job periódico (30 min) pega os lawsuits DISTINTOS com publicação recente
    que não estão no cache (ou venceram o TTL) e busca as etiquetas de cada um
    com throttle — volume real: ~500-650 lawsuits/dia, pico ~1.600 (segunda).
  - `list_records_grouped` injeta `l1_etiquetas` por grupo a partir do cache.
  - Etiqueta é rara (marca processo especial): a maioria dos processos grava
    `[]` no cache e nunca mais é consultada dentro do TTL.

Reusa a sessão web compartilhada do legacy_task_http (mesma dos runners) —
GETs de leitura não derrubam a sessão de ninguém (L1 é single-session por
usuário, mas aqui não há novo login quando a sessão do cache está viva).
"""
from __future__ import annotations

import html as _html
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Regex do hidden da página de edição — fonte única das etiquetas salvas.
_HIDDEN_RE = re.compile(r'id="selectedTagsHidden"[^>]*value="([^"]*)"')

_THROTTLE_S = 1.1          # respeita o rate do L1 web (~1 req/s)
_TTL_HORAS = 20            # revalida 1x/dia útil (etiqueta muda raramente)
_MAX_POR_RODADA = 400      # teto por tick do job (30 min) — cobre o pico do dia
_JANELA_PUBS_DIAS = 3      # só lawsuits com publicação recente interessam


def _parse_etiquetas(html_page: str) -> list[dict] | None:
    """Extrai as etiquetas do HTML da página de edição. None = página sem o
    hidden (layout inesperado/permissão) — nesse caso NÃO gravamos no cache."""
    m = _HIDDEN_RE.search(html_page)
    if not m:
        return None
    raw = _html.unescape(m.group(1)).strip()
    if not raw:
        return []
    try:
        tags = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("selectedTagsHidden não parseou: %s", raw[:200])
        return None
    out = []
    for t in tags if isinstance(tags, list) else []:
        if not isinstance(t, dict):
            continue
        out.append({
            "id": t.get("Id"),
            "name": t.get("Name"),
            "class_name": t.get("ClassName"),
            "color_id": t.get("ColorId"),
        })
    return out


def enrich_etiquetas_recentes(db: Session) -> dict:
    """Busca no L1 as etiquetas dos lawsuits com publicação recente que estão
    fora do cache (ou vencidos). Devolve resumo {candidatos, buscados, com_etiqueta,
    falhas}. Não levanta exceção — job periódico é best-effort."""
    limite_ttl = datetime.now(timezone.utc) - timedelta(hours=_TTL_HORAS)
    rows = db.execute(
        text(
            f"""
            SELECT DISTINCT pr.linked_lawsuit_id
              FROM publicacao_registros pr
              LEFT JOIN pub_l1_etiqueta_cache ec ON ec.lawsuit_id = pr.linked_lawsuit_id
             WHERE pr.linked_lawsuit_id IS NOT NULL
               AND pr.created_at >= now() - interval '{_JANELA_PUBS_DIAS} days'
               AND (ec.lawsuit_id IS NULL OR ec.fetched_at < :limite)
             LIMIT :max_n
            """
        ),
        {"limite": limite_ttl, "max_n": _MAX_POR_RODADA},
    ).fetchall()
    ids = [int(r[0]) for r in rows]
    resumo = {"candidatos": len(ids), "buscados": 0, "com_etiqueta": 0, "falhas": 0}
    if not ids:
        return resumo

    # Sessão web compartilhada (login só se o cache de cookies estiver morto).
    from app.services.legal_one_client import LegalOneApiClient
    from app.services.prazos_iniciais.legacy_task_http_cancellation_service import (
        LegacyTaskHttpCancellationService,
    )

    svc = LegacyTaskHttpCancellationService(client=LegalOneApiClient())
    cookies = svc._ensure_session()
    base = svc._web_base_url()

    for i, lid in enumerate(ids):
        try:
            resp = requests.get(
                f"{base}/processos/processos/edit/{lid}",
                cookies=cookies, timeout=45, allow_redirects=True,
            )
            # Sessão caiu no meio (redirect pra login) → relogin único e retry.
            if resp.status_code in (401, 403) or "/login" in (resp.url or "").lower():
                svc._invalidate_session()
                cookies = svc._ensure_session()
                resp = requests.get(
                    f"{base}/processos/processos/edit/{lid}",
                    cookies=cookies, timeout=45, allow_redirects=True,
                )
            etiquetas = _parse_etiquetas(resp.text) if resp.status_code == 200 else None
            if etiquetas is None:
                resumo["falhas"] += 1
            else:
                db.execute(
                    text(
                        """
                        INSERT INTO pub_l1_etiqueta_cache (lawsuit_id, etiquetas, fetched_at)
                        VALUES (:lid, CAST(:et AS json), now())
                        ON CONFLICT (lawsuit_id)
                        DO UPDATE SET etiquetas = CAST(:et AS json), fetched_at = now()
                        """
                    ),
                    {"lid": lid, "et": json.dumps(etiquetas, ensure_ascii=False)},
                )
                db.commit()
                resumo["buscados"] += 1
                if etiquetas:
                    resumo["com_etiqueta"] += 1
        except Exception:  # noqa: BLE001
            resumo["falhas"] += 1
            logger.exception("Falha ao buscar etiquetas do lawsuit %s", lid)
            db.rollback()
        if i < len(ids) - 1:
            time.sleep(_THROTTLE_S)

    logger.info(
        "Etiquetas L1: %d candidatos, %d buscados, %d com etiqueta, %d falhas.",
        resumo["candidatos"], resumo["buscados"], resumo["com_etiqueta"], resumo["falhas"],
    )
    return resumo


def etiquetas_por_lawsuit(db: Session, lawsuit_ids: list[int]) -> dict[int, list[dict]]:
    """Lê o cache pra um conjunto de lawsuits (1 query). Ausente do cache = {}
    (ainda não enriquecido — o front trata como 'sem informação', não 'sem tag')."""
    if not lawsuit_ids:
        return {}
    rows = db.execute(
        text("SELECT lawsuit_id, etiquetas FROM pub_l1_etiqueta_cache WHERE lawsuit_id = ANY(:ids)"),
        {"ids": [int(x) for x in lawsuit_ids]},
    ).fetchall()
    out: dict[int, list[dict]] = {}
    for r in rows:
        val = r[1]
        if isinstance(val, str):
            try:
                val = json.loads(val)
            except (ValueError, TypeError):
                val = []
        out[int(r[0])] = val if isinstance(val, list) else []
    return out


def _job_tick() -> None:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        enrich_etiquetas_recentes(db)
    except Exception:  # noqa: BLE001
        logger.exception("Tick do job de etiquetas L1 estourou (ignorado).")
    finally:
        db.close()


def register_publication_etiquetas_job(scheduler) -> None:
    """Job periódico de enriquecimento — 30 min, sem sobreposição. Quando não
    há candidato (caso comum fora da janela do pull), o tick é 1 SELECT."""
    scheduler.add_job(
        _job_tick,
        trigger="interval",
        minutes=30,
        id="publication_l1_etiquetas",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Job de etiquetas L1 das publicações registrado (30 min).")
