#!/usr/bin/env python3
"""Importa uma amostra real OneNotify x Flow para teste local.

O script não consulta produção e não altera nenhum serviço externo. Ele lê:

1. `notify_flow_publication_match_report.csv`, gerado a partir do OneNotify.
2. `publicacao_registros_matched_sample.csv`, exportado read-only do Flow.

Em seguida, semeia `publicacao_registros` locais ausentes e envia payloads no
mesmo contrato aceito pelo intake `/api/v1/onenotify-bb/intake`.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal
from app.models.onenotify_bb import OneNotifyBBNotification
from app.models.publication_search import PublicationRecord, PublicationSearch
from app.services.onenotify_bb_service import OneNotifyBBService


def _bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "t", "sim", "yes"}


def _json_or_none(value: str | None) -> Any:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _seed_flow_records(db, rows: list[dict[str, str]]) -> int:
    created = 0
    search_ids = sorted({int(r["search_id"]) for r in rows if r.get("search_id")})
    for search_id in search_ids:
        existing = db.query(PublicationSearch).filter(PublicationSearch.id == search_id).first()
        if not existing:
            db.add(
                PublicationSearch(
                    id=search_id,
                    status="CONCLUIDO",
                    date_from="2026-01-01",
                    date_to="2026-12-31",
                    origin_type="FixtureRealOneNotifyBB",
                    total_found=0,
                    total_new=0,
                    total_duplicate=0,
                )
            )
    db.flush()

    for row in rows:
        record_id = int(row["id"])
        existing = db.query(PublicationRecord).filter(PublicationRecord.id == record_id).first()
        if existing:
            continue
        db.add(
            PublicationRecord(
                id=record_id,
                search_id=int(row["search_id"]),
                legal_one_update_id=int(row["legal_one_update_id"]),
                origin_type=row.get("origin_type") or "OfficialJournalsCrawler",
                update_type_id=int(row["update_type_id"]) if row.get("update_type_id") else None,
                description=row.get("description"),
                notes=row.get("notes"),
                publication_date=(row.get("publication_date") or "")[:10] or None,
                creation_date=row.get("creation_date"),
                linked_lawsuit_id=int(row["linked_lawsuit_id"]) if row.get("linked_lawsuit_id") else None,
                linked_lawsuit_cnj=row.get("linked_lawsuit_cnj"),
                linked_office_id=int(row["linked_office_id"]) if row.get("linked_office_id") else None,
                raw_relationships=_json_or_none(row.get("raw_relationships")),
                status=row.get("status") or "NOVO",
                is_duplicate=_bool(row.get("is_duplicate")),
                classification_item_id=(
                    int(row["classification_item_id"]) if row.get("classification_item_id") else None
                ),
                category=row.get("category"),
                subcategory=row.get("subcategory"),
                polo=row.get("polo"),
                classifications=_json_or_none(row.get("classifications")),
                natureza_processo=row.get("natureza_processo"),
                uf=row.get("uf"),
                scheduled_by_email=row.get("scheduled_by_email"),
                scheduled_by_name=row.get("scheduled_by_name"),
                ignored_by_email=row.get("ignored_by_email"),
                ignored_by_name=row.get("ignored_by_name"),
            )
        )
        created += 1
    db.flush()
    return created


def _notification_payload(row: dict[str, str], row_index: int) -> dict[str, Any]:
    notify_text = row.get("notify_excerpt") or ""
    notify_text_truncated = notify_text.rstrip().endswith("...")
    external_group_id = (
        f"real-sample|row:{row_index}|notify:{row.get('notify_id') or 'sem-id'}|"
        f"item:{row.get('item_index') or '0'}|npj:{row.get('npj')}"
    )
    return {
        "schema_version": "onenotify.flow-intake.v1",
        "external_group_id": external_group_id,
        "ids": [int(row["notify_id"])] if row.get("notify_id") else [],
        "npj": row.get("npj"),
        "numero_processo_cnj": row.get("cnj_principal"),
        "cnj_publicacao": row.get("cnj_publicacao"),
        "data_notificacao": row.get("data_notificacao"),
        "polo": None,
        "adverso_principal": row.get("adverso_principal"),
        "processo": {
            "npj": row.get("npj"),
            "numero_cnj": row.get("cnj_principal"),
            "polo": None,
            "adverso_principal": row.get("adverso_principal"),
        },
        "tipos_notificacao": [row.get("descricao") or "PUBLICACAO DJ/DO"],
        "status_legacy": ["Processado"],
        "rpa_status": ["PROCESSADO"],
        "bb_ciencia_status": ["CIENCIA_CONFIRMADA"],
        "human_status": ["NOVO"],
        "flow_status": ["NAO_ENVIADO"],
        "andamentos": [
            {
                "data": row.get("publication_date"),
                "descricao": row.get("descricao") or "PUBLICACAO DJ/DO",
                "detalhes": notify_text,
            }
        ],
        "documentos": {
            "schema_version": "onenotify.documents.v1",
            "items": [],
        },
        "conteudo": {
            "tem_texto": bool(notify_text),
            "tem_texto_andamentos": bool(notify_text),
            "texto_truncado": notify_text_truncated,
            "texto_truncado_motivo": (
                "A amostra local foi montada a partir de notify_excerpt, não do payload integral do OneNotify."
                if notify_text_truncated
                else None
            ),
            "tem_documentos": False,
            "tem_documentos_com_texto": False,
            "tem_documentos_ocr_required": False,
            "total_andamentos": 1 if notify_text else 0,
            "total_documentos": 0,
            "total_documentos_com_texto": 0,
            "total_documentos_ocr_required": 0,
            "fontes_texto": [
                {
                    "tipo": "andamento",
                    "ordem": 1,
                    "data": row.get("publication_date"),
                    "titulo": row.get("descricao") or "PUBLICACAO DJ/DO",
                    "texto": notify_text,
                }
            ],
            "documentos_links": [],
        },
        "source": "ONENOTIFY_BB",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comparison-csv", required=True, type=Path)
    parser.add_argument("--flow-records-csv", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--create-schema",
        action="store_true",
        help="Cria apenas as tabelas mínimas da amostra local se elas não existirem.",
    )
    parser.add_argument(
        "--verbose-records",
        action="store_true",
        help="Inclui todos os registros importados no JSON de saída.",
    )
    args = parser.parse_args()

    comparison_rows = _load_csv(args.comparison_csv)
    flow_rows = _load_csv(args.flow_records_csv)
    if args.limit > 0:
        comparison_rows = comparison_rows[: args.limit]

    db = SessionLocal()
    try:
        if args.create_schema:
            bind = db.get_bind()
            PublicationSearch.__table__.create(bind=bind, checkfirst=True)
            PublicationRecord.__table__.create(bind=bind, checkfirst=True)
            OneNotifyBBNotification.__table__.create(bind=bind, checkfirst=True)
        created_flow = _seed_flow_records(db, flow_rows)
        payloads = [_notification_payload(row, idx) for idx, row in enumerate(comparison_rows, start=1)]
        result = OneNotifyBBService(db).ingest({"items": payloads})
        db.commit()
        stats = OneNotifyBBService(db).stats()
        notification_summary = {k: v for k, v in result.items() if k != "records"}
        if args.verbose_records:
            notification_summary["records"] = result["records"]
        print(
            json.dumps(
                {
                    "flow_records_seeded": created_flow,
                    "notifications": notification_summary,
                    "stats": stats,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
