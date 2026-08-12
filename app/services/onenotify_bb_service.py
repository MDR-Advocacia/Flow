import difflib
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.onenotify_bb import (
    ONB_ACTION_REVISAR_MANUALMENTE,
    ONB_ACTION_SEM_TRATAMENTO_NOTIFY,
    ONB_ACTION_TRATAR_DOCUMENTO_FLOW,
    ONB_STATUS_CONCILIADA_AUTO,
    ONB_STATUS_PENDENTE_DOCUMENTO,
    ONB_STATUS_PENDENTE_FLOW,
    ONB_STATUS_REVISAO,
    OneNotifyBBNotification,
)
from app.models.publication_search import PublicationRecord
from app.services.publication_search_service import PublicationSearchService, extract_cnj_from_text

CNJ_RE = re.compile(r"\b\d{7}-?\d{2}\.?\d{4}\.?\d\.?\d{2}\.?\d{4}\b")
PUBLICATION_NOTIFICATION_TERMS = ("PUBLICACAO", "PUBLICAÇÃO", "DJ/DO")
AUTO_CONCILIATION_THRESHOLD = 0.80


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_date(value: Any) -> str | None:
    raw = _clean_text(value)
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return raw[:10]


def _normalize_digits(value: Any) -> str:
    return re.sub(r"\D", "", _clean_text(value))


def _format_cnj_from_digits(digits: str) -> str | None:
    if len(digits) != 20:
        return None
    return (
        f"{digits[:7]}-{digits[7:9]}.{digits[9:13]}."
        f"{digits[13]}.{digits[14:16]}.{digits[16:]}"
    )


def _extract_all_cnjs(text: str) -> list[str]:
    found: list[str] = []
    for match in CNJ_RE.findall(text or ""):
        digits = _normalize_digits(match)
        formatted = _format_cnj_from_digits(digits)
        if formatted and formatted not in found:
            found.append(formatted)
    return found


def _normalize_for_match(text: str) -> str:
    text = (text or "").upper()
    text = text.replace(">>>>>>", " ").replace("<<<<<<", " ")
    text = re.sub(r"[^0-9A-ZÀ-Ú]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _token_score(left: str, right: str) -> dict[str, float]:
    left_norm = _normalize_for_match(left)
    right_norm = _normalize_for_match(right)
    if not left_norm or not right_norm:
        return {"score": 0.0, "seq_ratio": 0.0, "token_containment": 0.0, "token_jaccard": 0.0}

    seq_ratio = difflib.SequenceMatcher(None, left_norm[:12_000], right_norm[:12_000]).ratio()
    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    intersection = left_tokens & right_tokens
    union = left_tokens | right_tokens
    smaller = min(len(left_tokens), len(right_tokens)) or 1
    token_containment = len(intersection) / smaller
    token_jaccard = len(intersection) / (len(union) or 1)
    score = max(seq_ratio, token_containment)
    return {
        "score": round(score, 4),
        "seq_ratio": round(seq_ratio, 4),
        "token_containment": round(token_containment, 4),
        "token_jaccard": round(token_jaccard, 4),
    }


def _split_blocks(text: str, max_chars: int = 420) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?;:])\s+", text)
    blocks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > max_chars:
            blocks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        blocks.append(current)
    return blocks


def _build_diff_rows(left: str, right: str, limit: int = 400) -> list[dict[str, Any]]:
    left_blocks = _split_blocks(left)
    right_blocks = _split_blocks(right)
    matcher = difflib.SequenceMatcher(None, left_blocks, right_blocks)
    rows: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        left_slice = left_blocks[i1:i2]
        right_slice = right_blocks[j1:j2]
        length = max(len(left_slice), len(right_slice), 1)
        for idx in range(length):
            rows.append(
                {
                    "kind": tag,
                    "left_line": i1 + idx + 1 if idx < len(left_slice) else None,
                    "right_line": j1 + idx + 1 if idx < len(right_slice) else None,
                    "left": left_slice[idx] if idx < len(left_slice) else "",
                    "right": right_slice[idx] if idx < len(right_slice) else "",
                }
            )
            if len(rows) >= limit:
                return rows
    return rows


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


class OneNotifyBBService:
    def __init__(self, db: Session):
        self.db = db
        self._publication_by_date_cache: dict[str, list[PublicationRecord]] = {}
        self._publication_text_cache: dict[int, str] = {}
        self._publication_digits_cache: dict[int, str] = {}

    def ingest(self, payload: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any]:
        items = self._payload_items(payload)
        created = 0
        updated = 0
        records: list[OneNotifyBBNotification] = []
        for item in items:
            external_group_id = _clean_text(item.get("external_group_id"))
            if not external_group_id:
                external_group_id = f"{item.get('npj')}|{item.get('data_notificacao')}"
            if not external_group_id or external_group_id == "None|None":
                raise ValueError("Payload sem external_group_id ou npj/data_notificacao.")

            record = (
                self.db.query(OneNotifyBBNotification)
                .filter(OneNotifyBBNotification.external_group_id == external_group_id)
                .first()
            )
            if record:
                updated += 1
            else:
                record = OneNotifyBBNotification(external_group_id=external_group_id)
                self.db.add(record)
                created += 1

            self._apply_payload(record, item)
            self._reconcile_record(record)
            records.append(record)

        self.db.commit()
        for record in records:
            self.db.refresh(record)
        return {
            "received": len(items),
            "created": created,
            "updated": updated,
            "records": [self._record_to_summary(record) for record in records],
        }

    def list_notifications(
        self,
        status: str | None = None,
        action: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        query = self.db.query(OneNotifyBBNotification)
        if status:
            query = query.filter(OneNotifyBBNotification.flow_status == status)
        if action:
            query = query.filter(OneNotifyBBNotification.action_suggested == action)
        if q:
            like = f"%{q.strip()}%"
            query = query.filter(
                or_(
                    OneNotifyBBNotification.npj.ilike(like),
                    OneNotifyBBNotification.numero_processo_cnj.ilike(like),
                    OneNotifyBBNotification.cnj_publicacao.ilike(like),
                    OneNotifyBBNotification.adverso_principal.ilike(like),
                )
            )
        total = query.count()
        items = (
            query.order_by(
                OneNotifyBBNotification.notification_date_iso.desc().nullslast(),
                OneNotifyBBNotification.id.desc(),
            )
            .limit(limit)
            .offset(offset)
            .all()
        )
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [self._record_to_summary(item) for item in items],
        }

    def stats(self) -> dict[str, Any]:
        total = self.db.query(func.count(OneNotifyBBNotification.id)).scalar() or 0
        matched = (
            self.db.query(func.count(OneNotifyBBNotification.id))
            .filter(OneNotifyBBNotification.matched_publication_record_id.isnot(None))
            .scalar()
            or 0
        )
        auto = (
            self.db.query(func.count(OneNotifyBBNotification.id))
            .filter(OneNotifyBBNotification.flow_status == ONB_STATUS_CONCILIADA_AUTO)
            .scalar()
            or 0
        )
        divergent = (
            self.db.query(func.count(OneNotifyBBNotification.id))
            .filter(OneNotifyBBNotification.cnj_divergent == True)  # noqa: E712
            .scalar()
            or 0
        )
        pending_document = (
            self.db.query(func.count(OneNotifyBBNotification.id))
            .filter(OneNotifyBBNotification.flow_status == ONB_STATUS_PENDENTE_DOCUMENTO)
            .scalar()
            or 0
        )
        no_match = (
            self.db.query(func.count(OneNotifyBBNotification.id))
            .filter(OneNotifyBBNotification.matched_publication_record_id.is_(None))
            .scalar()
            or 0
        )
        return {
            "total": total,
            "matched": matched,
            "matched_pct": round((matched / total) * 100, 1) if total else 0,
            "auto_conciliated": auto,
            "auto_conciliated_pct": round((auto / total) * 100, 1) if total else 0,
            "cnj_divergent": divergent,
            "pending_document": pending_document,
            "no_match": no_match,
        }

    def get_detail(self, notification_id: int) -> dict[str, Any] | None:
        record = (
            self.db.query(OneNotifyBBNotification)
            .filter(OneNotifyBBNotification.id == notification_id)
            .first()
        )
        if not record:
            return None
        return self._record_to_detail(record)

    def _payload_items(self, payload: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            return [item for item in payload["items"] if isinstance(item, dict)]
        if isinstance(payload, dict):
            return [payload]
        raise ValueError("Payload inválido.")

    def _apply_payload(self, record: OneNotifyBBNotification, item: dict[str, Any]) -> None:
        conteudo = item.get("conteudo") if isinstance(item.get("conteudo"), dict) else {}
        text_content = self._text_from_payload(item)
        cnjs_from_text = _extract_all_cnjs(text_content)
        cnj_publicacao = (
            item.get("cnj_publicacao")
            or item.get("publication_cnj")
            or (cnjs_from_text[0] if cnjs_from_text else extract_cnj_from_text(text_content))
        )
        numero_cnj = item.get("numero_processo_cnj") or item.get("numero_processo")
        publication_date = self._publication_date_from_payload(item)
        documentos = item.get("documentos") if isinstance(item.get("documentos"), dict) else None
        document_summary = self._document_summary(conteudo, documentos)

        record.source = item.get("source") or "ONENOTIFY_BB"
        record.schema_version = item.get("schema_version")
        record.notify_ids = _as_list(item.get("ids"))
        record.npj = item.get("npj")
        record.data_notificacao = item.get("data_notificacao")
        record.notification_date_iso = _normalize_date(item.get("data_notificacao"))
        record.publication_date = publication_date
        record.numero_processo_cnj = numero_cnj
        record.cnj_principal_notify = numero_cnj
        record.cnj_publicacao = cnj_publicacao
        record.cnj_divergent = bool(
            _normalize_digits(numero_cnj)
            and _normalize_digits(cnj_publicacao)
            and _normalize_digits(numero_cnj) != _normalize_digits(cnj_publicacao)
        )
        record.adverso_principal = item.get("adverso_principal") or (item.get("processo") or {}).get("adverso_principal")
        record.polo = item.get("polo") or (item.get("processo") or {}).get("polo")
        record.posicao_cliente = self._position_label(record.polo, text_content)
        record.tipos_notificacao = _as_list(item.get("tipos_notificacao"))
        record.rpa_status = _as_list(item.get("rpa_status"))
        record.bb_ciencia_status = _as_list(item.get("bb_ciencia_status"))
        record.human_status = _as_list(item.get("human_status"))
        record.flow_sync_status = _as_list(item.get("flow_status"))
        record.status_legacy = _as_list(item.get("status_legacy"))
        record.andamentos = item.get("andamentos") if isinstance(item.get("andamentos"), list) else []
        record.documentos = documentos
        record.conteudo = conteudo
        record.raw_payload = item
        record.text_content = text_content
        record.document_summary = document_summary

    def _reconcile_record(self, record: OneNotifyBBNotification) -> None:
        candidates = self._publication_candidates(record)
        best: tuple[PublicationRecord, dict[str, float]] | None = None
        for candidate in candidates:
            candidate_text = self._publication_text_cached(candidate)
            scores = _token_score(record.text_content or "", candidate_text)
            if not best or scores["score"] > best[1]["score"]:
                best = (candidate, scores)

        if best:
            publication, scores = best
            record.matched_publication_record_id = publication.id
            record.matched_legal_one_update_id = publication.legal_one_update_id
            record.matched_publication_status = publication.status
            record.match_score = scores["score"]
            record.match_strategy = "publication_date+cnj+text_similarity"
            record.match_reason = (
                f"CNJ/data compatíveis; score={scores['score']}; "
                f"seq={scores['seq_ratio']}; containment={scores['token_containment']}."
            )
            if self._is_publication_notification(record) and scores["score"] >= AUTO_CONCILIATION_THRESHOLD:
                record.flow_status = ONB_STATUS_CONCILIADA_AUTO
                record.action_suggested = ONB_ACTION_SEM_TRATAMENTO_NOTIFY
            else:
                record.flow_status = ONB_STATUS_REVISAO
                record.action_suggested = ONB_ACTION_REVISAR_MANUALMENTE
        else:
            record.matched_publication_record_id = None
            record.matched_legal_one_update_id = None
            record.matched_publication_status = None
            record.match_score = 0.0
            record.match_strategy = None
            record.match_reason = "Nenhuma publicação do Flow encontrada com mesma data e CNJ extraído."
            if (record.document_summary or {}).get("total_documentos", 0) > 0:
                record.flow_status = ONB_STATUS_PENDENTE_DOCUMENTO
                record.action_suggested = ONB_ACTION_TRATAR_DOCUMENTO_FLOW
            else:
                record.flow_status = ONB_STATUS_PENDENTE_FLOW
                record.action_suggested = ONB_ACTION_REVISAR_MANUALMENTE

        record.reconciled_at = datetime.now(timezone.utc)

    def _publication_candidates(self, record: OneNotifyBBNotification) -> list[PublicationRecord]:
        if not record.publication_date or not record.cnj_publicacao:
            return []
        cnj_digits = _normalize_digits(record.cnj_publicacao)
        formatted = _format_cnj_from_digits(cnj_digits) or record.cnj_publicacao
        query = (
            self.db.query(PublicationRecord)
            .filter(
                or_(
                    PublicationRecord.publication_date == record.publication_date,
                    PublicationRecord.publication_date.ilike(f"{record.publication_date}%"),
                )
            )
        )
        candidates = self._publication_by_date_cache.get(record.publication_date)
        if candidates is None:
            candidates = query.all()
            self._publication_by_date_cache[record.publication_date] = candidates
        matched: list[PublicationRecord] = []
        for candidate in candidates:
            candidate_text = self._publication_text_cached(candidate)
            candidate_digits = self._publication_digits_cache.get(candidate.id)
            if candidate_digits is None:
                candidate_digits = _normalize_digits(candidate_text)
                self._publication_digits_cache[candidate.id] = candidate_digits
            if (
                _normalize_digits(candidate.linked_lawsuit_cnj) == cnj_digits
                or cnj_digits in candidate_digits
                or formatted in candidate_text
            ):
                matched.append(candidate)
        return matched

    def _text_from_payload(self, item: dict[str, Any]) -> str:
        conteudo = item.get("conteudo") if isinstance(item.get("conteudo"), dict) else {}
        fontes = conteudo.get("fontes_texto") if isinstance(conteudo, dict) else []
        texts = []
        if isinstance(fontes, list):
            for fonte in fontes:
                if isinstance(fonte, dict) and fonte.get("texto"):
                    texts.append(str(fonte["texto"]))
        if not texts and item.get("texto"):
            texts.append(str(item["texto"]))
        return "\n\n".join(t.strip() for t in texts if t and t.strip())

    def _publication_date_from_payload(self, item: dict[str, Any]) -> str | None:
        for source in _as_list((item.get("conteudo") or {}).get("fontes_texto") if isinstance(item.get("conteudo"), dict) else []):
            if isinstance(source, dict) and source.get("data"):
                parsed = _normalize_date(source.get("data"))
                if parsed:
                    return parsed
        for key in ("data_publicacao", "publication_date", "data"):
            parsed = _normalize_date(item.get(key))
            if parsed:
                return parsed
        return None

    def _document_summary(self, conteudo: dict[str, Any], documentos: dict[str, Any] | None) -> dict[str, Any]:
        items = documentos.get("items", []) if isinstance(documentos, dict) else []
        return {
            "tem_documentos": bool(conteudo.get("tem_documentos")) or bool(items),
            "total_documentos": int(conteudo.get("total_documentos") or len(items) or 0),
            "total_documentos_com_texto": int(conteudo.get("total_documentos_com_texto") or 0),
            "total_documentos_ocr_required": int(conteudo.get("total_documentos_ocr_required") or 0),
            "tem_documentos_ocr_required": bool(conteudo.get("tem_documentos_ocr_required")),
        }

    def _position_label(self, polo: Any, text: str) -> str:
        raw = _clean_text(polo).lower()
        if "ativo" in raw and "passivo" not in raw:
            return "Autor / Polo ativo"
        if "passivo" in raw or "réu" in raw or "reu" in raw:
            return "Réu / Polo passivo"
        normalized = _normalize_for_match(text)
        if "BANCO DO BRASIL" in normalized or "BANCO DO BRASIL S A" in normalized:
            return "Banco citado no texto"
        return "Não identificado"

    def _is_publication_notification(self, record: OneNotifyBBNotification) -> bool:
        joined = " ".join(str(t) for t in (record.tipos_notificacao or []))
        joined = _normalize_for_match(joined)
        return any(term in joined for term in PUBLICATION_NOTIFICATION_TERMS)

    def _publication_text(self, publication: PublicationRecord) -> str:
        return "\n\n".join([_clean_text(publication.description), _clean_text(publication.notes)]).strip()

    def _publication_text_cached(self, publication: PublicationRecord) -> str:
        cached = self._publication_text_cache.get(publication.id)
        if cached is None:
            cached = self._publication_text(publication)
            self._publication_text_cache[publication.id] = cached
        return cached

    def _publication_to_dict(self, publication: PublicationRecord | None) -> dict[str, Any] | None:
        if not publication:
            return None
        return PublicationSearchService._record_to_dict(publication, include_full_text=True)

    def _record_to_summary(self, record: OneNotifyBBNotification) -> dict[str, Any]:
        return {
            "id": record.id,
            "external_group_id": record.external_group_id,
            "source": record.source,
            "notify_ids": record.notify_ids or [],
            "npj": record.npj,
            "data_notificacao": record.data_notificacao,
            "notification_date_iso": record.notification_date_iso,
            "publication_date": record.publication_date,
            "numero_processo_cnj": record.numero_processo_cnj,
            "cnj_publicacao": record.cnj_publicacao,
            "cnj_principal_notify": record.cnj_principal_notify,
            "cnj_divergent": record.cnj_divergent,
            "adverso_principal": record.adverso_principal,
            "posicao_cliente": record.posicao_cliente,
            "tipos_notificacao": record.tipos_notificacao or [],
            "flow_status": record.flow_status,
            "action_suggested": record.action_suggested,
            "matched_publication_record_id": record.matched_publication_record_id,
            "matched_legal_one_update_id": record.matched_legal_one_update_id,
            "matched_publication_status": record.matched_publication_status,
            "match_score": record.match_score,
            "match_strategy": record.match_strategy,
            "document_summary": record.document_summary or {},
        }

    def _record_to_detail(self, record: OneNotifyBBNotification) -> dict[str, Any]:
        publication = record.matched_publication
        notify_text = record.text_content or ""
        flow_text = self._publication_text(publication) if publication else ""
        detail = self._record_to_summary(record)
        detail.update(
            {
                "andamentos": record.andamentos or [],
                "documentos": record.documentos,
                "conteudo": record.conteudo or {},
                "raw_payload": record.raw_payload or {},
                "text_content": notify_text,
                "match_reason": record.match_reason,
                "matched_publication": self._publication_to_dict(publication),
                "diff": {
                    "score": record.match_score or 0,
                    "rows": _build_diff_rows(notify_text, flow_text),
                },
            }
        )
        return detail
