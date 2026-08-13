#!/usr/bin/env python3
"""Build a local OneNotify BB fixture from complete production interval exports.

The script has two safe modes:

1. export-flow: read Flow publication records from the configured DATABASE_URL
   and write JSONL. This is intended to run read-only inside the production API
   container.
2. build-local: read that JSONL plus the real OneNotify Postgres database and
   seed a local SQLite database, then run the same deterministic reconciliation
   service used by the application.

No production writes are performed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import OrderedDict, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


DATE_BR_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")

PUBLICATION_RECORD_COLUMNS = [
    "id",
    "search_id",
    "legal_one_update_id",
    "origin_type",
    "update_type_id",
    "description",
    "notes",
    "publication_date",
    "creation_date",
    "linked_lawsuit_id",
    "linked_lawsuit_cnj",
    "linked_office_id",
    "raw_relationships",
    "status",
    "is_duplicate",
    "classification_item_id",
    "category",
    "subcategory",
    "polo",
    "audiencia_data",
    "audiencia_hora",
    "audiencia_link",
    "classifications",
    "natureza_processo",
    "uf",
    "scheduled_by_user_id",
    "scheduled_by_email",
    "scheduled_by_name",
    "scheduled_at",
    "ignored_by_user_id",
    "ignored_by_email",
    "ignored_by_name",
    "ignored_at",
    "created_at",
    "updated_at",
]

ONENOTIFY_COLUMNS = [
    "id",
    "npj",
    "tipo_notificacao",
    "data_notificacao",
    "adverso_principal",
    "status",
    "data_criacao",
    "numero_processo",
    "andamentos",
    "documentos",
    "id_processo_portal",
    "data_processamento",
    "detalhes_erro",
    "responsavel",
    "gerou_tarefa",
    "origem",
    "tentativas",
    "polo",
    "rpa_status",
    "bb_ciencia_status",
    "human_status",
    "flow_status",
    "flow_external_id",
    "flow_synced_at",
    "flow_last_error",
    "documentos_json",
]


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _json_or(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    raw = str(value).strip()
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _unique(values: list[Any]) -> list[Any]:
    seen = OrderedDict()
    for value in values:
        if value is None:
            continue
        key = json.dumps(value, sort_keys=True, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        if key and key not in seen:
            seen[key] = value
    return list(seen.values())


def _document_text(item: dict[str, Any]) -> str:
    extraction = item.get("extraction", {}) if isinstance(item.get("extraction"), dict) else {}
    pages = extraction.get("pages", []) if isinstance(extraction, dict) else []
    texts = []
    for page in pages:
        if isinstance(page, dict) and page.get("text"):
            texts.append(str(page["text"]))
    return "\n\n".join(texts).strip()


def _build_conteudo_payload(andamentos: list[dict[str, Any]], documentos_json: dict[str, Any] | None, documentos_originais: list[Any]):
    fontes_texto = []
    for index, andamento in enumerate(andamentos, start=1):
        if not isinstance(andamento, dict):
            continue
        texto = (andamento.get("detalhes") or andamento.get("descricao") or andamento.get("texto") or "").strip()
        if texto:
            fontes_texto.append(
                {
                    "tipo": "andamento",
                    "ordem": index,
                    "data": andamento.get("data"),
                    "titulo": andamento.get("descricao") or andamento.get("titulo"),
                    "texto": texto,
                }
            )

    documentos_items = []
    if isinstance(documentos_json, dict):
        documentos_items = [item for item in documentos_json.get("items", []) if isinstance(item, dict)]

    documentos_com_texto = 0
    documentos_exigem_ocr = 0
    documentos_links = []
    for index, item in enumerate(documentos_items, start=1):
        extraction = item.get("extraction", {}) if isinstance(item.get("extraction"), dict) else {}
        ocr_required = bool(extraction.get("ocr_required"))
        if ocr_required:
            documentos_exigem_ocr += 1
        texto_documento = _document_text(item)
        if texto_documento:
            documentos_com_texto += 1
            fontes_texto.append(
                {
                    "tipo": "documento",
                    "ordem": index,
                    "nome": item.get("nome"),
                    "classification": extraction.get("classification"),
                    "ocr_required": ocr_required,
                    "view_url": item.get("view_url"),
                    "download_url": item.get("download_url"),
                    "texto": texto_documento,
                }
            )
        documentos_links.append(
            {
                "nome": item.get("nome"),
                "relative_path": item.get("relative_path"),
                "access_mode": item.get("access_mode"),
                "classification": extraction.get("classification"),
                "ocr_required": ocr_required,
                "view_url": item.get("view_url"),
                "download_url": item.get("download_url"),
            }
        )

    total_documentos = len(documentos_items) if documentos_items else len(documentos_originais)
    return {
        "tem_texto": bool(fontes_texto),
        "tem_texto_andamentos": any(fonte["tipo"] == "andamento" for fonte in fontes_texto),
        "tem_documentos": total_documentos > 0,
        "tem_documentos_com_texto": documentos_com_texto > 0,
        "tem_documentos_ocr_required": documentos_exigem_ocr > 0,
        "total_andamentos": len(andamentos),
        "total_documentos": total_documentos,
        "total_documentos_com_texto": documentos_com_texto,
        "total_documentos_ocr_required": documentos_exigem_ocr,
        "fontes_texto": fontes_texto,
        "documentos_links": documentos_links,
    }


def _parse_br_date(value: Any) -> date | None:
    raw = _clean(value)
    if not raw or not DATE_BR_RE.match(raw):
        return None
    try:
        return datetime.strptime(raw, "%d/%m/%Y").date()
    except ValueError:
        return None


def _merge_documentos_json(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    items = []
    for row in rows:
        documentos_json = _json_or(row.get("documentos_json"), None)
        if isinstance(documentos_json, dict):
            items.extend([item for item in documentos_json.get("items", []) if isinstance(item, dict)])
    items = _unique(items)
    if not items:
        return None
    return {"schema_version": "onenotify.documents.v1", "items": items}


def _merge_list_json(rows: list[dict[str, Any]], column: str) -> list[Any]:
    values = []
    for row in rows:
        parsed = _json_or(row.get(column), [])
        if isinstance(parsed, list):
            values.extend(parsed)
        elif parsed:
            values.append(parsed)
    return _unique(values)


def _first(rows: list[dict[str, Any]], column: str) -> Any:
    for row in rows:
        value = _clean(row.get(column))
        if value:
            return value
    return None


def _status_values(rows: list[dict[str, Any]], column: str) -> list[str]:
    return sorted({str(row[column]) for row in rows if _clean(row.get(column))})


def _group_to_payload(key: tuple[str, str], rows: list[dict[str, Any]]) -> dict[str, Any]:
    npj, data_notificacao = key
    andamentos = _merge_list_json(rows, "andamentos")
    documentos_originais = _merge_list_json(rows, "documentos")
    documentos_json = _merge_documentos_json(rows)
    conteudo = _build_conteudo_payload(andamentos, documentos_json, documentos_originais)
    numero_processo = _first(rows, "numero_processo")
    polo = _first(rows, "polo")
    adverso = _first(rows, "adverso_principal")
    ids = [int(row["id"]) for row in rows if row.get("id") is not None]
    return {
        "schema_version": "onenotify.flow-intake.v1",
        "external_group_id": f"{npj}|{data_notificacao}",
        "ids": ids,
        "npj": npj,
        "numero_processo_cnj": numero_processo,
        "data_notificacao": data_notificacao,
        "numero_processo": numero_processo,
        "polo": polo,
        "adverso_principal": adverso,
        "processo": {
            "npj": npj,
            "numero_cnj": numero_processo,
            "polo": polo,
            "adverso_principal": adverso,
        },
        "tipos_notificacao": _status_values(rows, "tipo_notificacao"),
        "status_legacy": _status_values(rows, "status"),
        "rpa_status": _status_values(rows, "rpa_status"),
        "bb_ciencia_status": _status_values(rows, "bb_ciencia_status"),
        "human_status": _status_values(rows, "human_status"),
        "flow_status": _status_values(rows, "flow_status"),
        "responsavel": _first(rows, "responsavel"),
        "data_processamento": _first(rows, "data_processamento"),
        "detalhes_erro": _first(rows, "detalhes_erro"),
        "andamentos": andamentos,
        "documentos": documentos_json,
        "conteudo": conteudo,
        "source": "ONENOTIFY_BB",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def export_flow(args: argparse.Namespace) -> int:
    from sqlalchemy import create_engine, text

    database_url = args.database_url or os.environ["DATABASE_URL"]
    engine = create_engine(database_url)
    columns = ", ".join(PUBLICATION_RECORD_COLUMNS)
    query = text(
        f"""
        SELECT {columns}
        FROM publicacao_registros
        WHERE left(coalesce(publication_date, ''), 10) >= :date_from
          AND left(coalesce(publication_date, ''), 10) <= :date_to
        ORDER BY left(coalesce(publication_date, ''), 10), id
        """
    )
    count = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with engine.connect() as conn, args.output.open("w", encoding="utf-8") as fh:
        for row in conn.execute(query, {"date_from": args.date_from, "date_to": args.date_to}).mappings():
            fh.write(json.dumps(dict(row), default=_json_default, ensure_ascii=False) + "\n")
            count += 1
    print(json.dumps({"mode": "export-flow", "records": count, "output": str(args.output)}, ensure_ascii=False))
    return 0


def _load_flow_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _fetch_onenotify_rows(args: argparse.Namespace) -> tuple[list[dict[str, Any]], int]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    start = date.fromisoformat(args.date_from)
    end = date.fromisoformat(args.date_to)
    query = f"""
        SELECT {", ".join(ONENOTIFY_COLUMNS)}
        FROM notificacoes
        WHERE data_notificacao ~ '^\\d{{2}}/\\d{{2}}/\\d{{4}}$'
        ORDER BY data_notificacao, npj, id
    """
    valid_rows = []
    invalid_interval_rows = 0
    with psycopg2.connect(args.onenotify_dsn, connect_timeout=10) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            for row in cur:
                parsed = _parse_br_date(row.get("data_notificacao"))
                if parsed is None:
                    continue
                if start <= parsed <= end:
                    valid_rows.append(dict(row))
    return valid_rows, invalid_interval_rows


def _seed_flow_records(db: Any, flow_rows: list[dict[str, Any]]) -> int:
    from app.models.publication_search import PublicationRecord, PublicationSearch

    search_ids = sorted({int(row["search_id"]) for row in flow_rows if row.get("search_id") is not None})
    for search_id in search_ids:
        if not db.query(PublicationSearch).filter(PublicationSearch.id == search_id).first():
            db.add(
                PublicationSearch(
                    id=search_id,
                    status="CONCLUIDO",
                    date_from="2026-06-01",
                    date_to="2026-07-31",
                    origin_type="OfficialJournalsCrawler",
                    total_found=0,
                    total_new=0,
                    total_duplicate=0,
                )
            )
    db.flush()

    created = 0
    model_columns = {column.name for column in PublicationRecord.__table__.columns}
    for row in flow_rows:
        if not row.get("id"):
            continue
        if db.query(PublicationRecord).filter(PublicationRecord.id == int(row["id"])).first():
            continue
        data = {key: value for key, value in row.items() if key in model_columns}
        data["publication_date"] = (data.get("publication_date") or "")[:10] or None
        for date_key in ("created_at", "updated_at", "scheduled_at", "ignored_at"):
            data.pop(date_key, None)
        db.add(PublicationRecord(**data))
        created += 1
    db.flush()
    return created


def _build_onenotify_payloads(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        npj = _clean(row.get("npj"))
        data_notificacao = _clean(row.get("data_notificacao"))
        if not npj or not data_notificacao:
            continue
        grouped[(npj, data_notificacao)].append(row)
    return [_group_to_payload(key, rows_for_key) for key, rows_for_key in sorted(grouped.items(), key=lambda item: item[0])]


def build_local(args: argparse.Namespace) -> int:
    if args.output_db:
        output_db = args.output_db.resolve()
        output_db.parent.mkdir(parents=True, exist_ok=True)
        os.environ["DATABASE_URL"] = f"sqlite:///{output_db}"

    from app.db.session import SessionLocal
    from app.models.onenotify_bb import OneNotifyBBNotification
    from app.models.publication_search import PublicationRecord, PublicationSearch
    from app.services.onenotify_bb_service import OneNotifyBBService

    flow_rows = _load_flow_jsonl(args.flow_jsonl)
    notify_rows, invalid_interval_rows = _fetch_onenotify_rows(args)
    payloads = _build_onenotify_payloads(notify_rows)

    db = SessionLocal()
    try:
        bind = db.get_bind()
        for table in (PublicationSearch.__table__, PublicationRecord.__table__, OneNotifyBBNotification.__table__):
            table.create(bind=bind, checkfirst=True)
        if args.reset:
            db.query(OneNotifyBBNotification).delete()
            db.query(PublicationRecord).delete()
            db.query(PublicationSearch).delete()
            db.commit()
        created_flow = _seed_flow_records(db, flow_rows)
        service = OneNotifyBBService(db)
        batch_size = args.batch_size
        received = created_notify = updated_notify = 0
        for start in range(0, len(payloads), batch_size):
            result = service.ingest({"items": payloads[start : start + batch_size]})
            received += result["received"]
            created_notify += result["created"]
            updated_notify += result["updated"]
        stats = service.stats()
        db.commit()
        summary = {
            "mode": "build-local",
            "date_from": args.date_from,
            "date_to": args.date_to,
            "flow_records_exported": len(flow_rows),
            "flow_records_created": created_flow,
            "onenotify_rows": len(notify_rows),
            "onenotify_groups": len(payloads),
            "onenotify_invalid_interval_rows": invalid_interval_rows,
            "notifications_received": received,
            "notifications_created": created_notify,
            "notifications_updated": updated_notify,
            "stats": stats,
            "database_url": os.environ.get("DATABASE_URL"),
        }
        if args.summary_json:
            args.summary_json.parent.mkdir(parents=True, exist_ok=True)
            args.summary_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    finally:
        db.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="mode", required=True)

    export_parser = subparsers.add_parser("export-flow")
    export_parser.add_argument("--date-from", required=True)
    export_parser.add_argument("--date-to", required=True)
    export_parser.add_argument("--output", required=True, type=Path)
    export_parser.add_argument("--database-url")
    export_parser.set_defaults(func=export_flow)

    build_parser = subparsers.add_parser("build-local")
    build_parser.add_argument("--date-from", required=True)
    build_parser.add_argument("--date-to", required=True)
    build_parser.add_argument("--flow-jsonl", required=True, type=Path)
    build_parser.add_argument("--onenotify-dsn", required=True)
    build_parser.add_argument("--output-db", type=Path)
    build_parser.add_argument("--summary-json", type=Path)
    build_parser.add_argument("--batch-size", type=int, default=500)
    build_parser.add_argument("--reset", action="store_true")
    build_parser.set_defaults(func=build_local)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
