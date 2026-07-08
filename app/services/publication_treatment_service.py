"""
Servico da fila de tratamento incidental de publicacoes no Legal One.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.models.publication_search import (
    RECORD_STATUS_DISCARDED_DUPLICATE,
    RECORD_STATUS_IGNORED,
    RECORD_STATUS_OBSOLETE,
    RECORD_STATUS_SCHEDULED,
    PublicationRecord,
)
from app.models.publication_treatment import (
    ACTIVE_RUN_STATUSES,
    FINAL_RUN_STATUSES,
    QUEUE_STATUS_CANCELLED,
    QUEUE_STATUS_COMPLETED,
    QUEUE_STATUS_FAILED,
    QUEUE_STATUS_PENDING,
    QUEUE_STATUS_PROCESSING,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_COMPLETED_WITH_ERRORS,
    RUN_STATUS_FAILED,
    RUN_STATUS_PAUSED,
    RUN_STATUS_RUNNING,
    RUN_STATUS_STARTING,
    RUN_STATUS_STOPPED,
    RUN_TRIGGER_MANUAL,
    TREATMENT_TARGET_TREATED,
    TREATMENT_TARGET_WITHOUT_PROVIDENCE,
    PublicationTreatmentItem,
    PublicationTreatmentRun,
)

logger = logging.getLogger(__name__)


def _tail_runner_log_to_logger(
    file_path: Path,
    pid: int,
    run_id: int,
    level: int = logging.INFO,
    timeout_seconds: float = 7200.0,
) -> None:
    """
    Thread daemon: segue um arquivo de log do runner Node.js e emite cada
    linha no logger Python.  Assim os logs do runner aparecem no stdout
    do container (Coolify/docker logs) em tempo real, além de continuarem
    persistidos em disco pro debug futuro.

    Termina quando:
      - o processo PID some (runner concluiu ou crashou), OU
      - `timeout_seconds` se esgota (safety cap — evita thread zumbi se o
        runner ficar parado por um tempo absurdo).
    """

    def _pid_alive(target_pid: int) -> bool:
        try:
            os.kill(target_pid, 0)
        except OSError:
            return False
        return True

    started_at = time.monotonic()
    deadline = started_at + timeout_seconds

    # Espera o arquivo aparecer (runner pode levar ms pra criar).
    for _ in range(40):
        if file_path.exists():
            break
        if not _pid_alive(pid):
            return  # runner morreu antes de escrever qualquer coisa
        time.sleep(0.25)

    if not file_path.exists():
        logger.warning(
            "tratamento: arquivo de log %s não apareceu (pid=%s, run=%s)",
            file_path, pid, run_id,
        )
        return

    try:
        with file_path.open("r", encoding="utf-8", errors="replace") as fh:
            while True:
                line = fh.readline()
                if line:
                    logger.log(level, "[runner run=%s pid=%s] %s", run_id, pid, line.rstrip())
                    continue
                # Sem linha nova: confere se processo ainda vive
                if not _pid_alive(pid):
                    # Drena remanescente antes de sair
                    remaining = fh.read()
                    if remaining:
                        for leftover in remaining.splitlines():
                            logger.log(level, "[runner run=%s pid=%s] %s", run_id, pid, leftover)
                    logger.info(
                        "tratamento: tail do %s encerrado (pid=%s, run=%s, decorrido=%.1fs).",
                        file_path.name, pid, run_id, time.monotonic() - started_at,
                    )
                    return
                if time.monotonic() > deadline:
                    logger.warning(
                        "tratamento: tail do %s abortado por timeout (pid=%s, run=%s).",
                        file_path.name, pid, run_id,
                    )
                    return
                time.sleep(1.0)
    except Exception:
        logger.exception(
            "tratamento: falha no tail de %s (pid=%s, run=%s).",
            file_path, pid, run_id,
        )

RUNNER_STATUS_TREATED = "treated"
RUNNER_STATUS_WITHOUT_PROVIDENCE = "without_providence"
RUNNER_STATUS_SCHEDULED_RETRY = "scheduled_retry"
RUNNER_STATUS_PENDING = "pending"


class PublicationTreatmentService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _parse_iso_datetime(value: Any) -> Optional[datetime]:
        if not value or not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _serialize_datetime(value: Optional[datetime]) -> Optional[str]:
        return value.isoformat() if value else None

    @staticmethod
    def _read_json_file(file_path: Path, fallback: Any = None) -> Any:
        try:
            raw = file_path.read_text(encoding="utf-8").replace("\ufeff", "")
            return json.loads(raw)
        except (OSError, ValueError, json.JSONDecodeError):
            return fallback

    @staticmethod
    def _write_json_file(file_path: Path, payload: Any) -> None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _eligible_record_statuses() -> tuple[str, ...]:
        return (
            RECORD_STATUS_SCHEDULED,
            RECORD_STATUS_IGNORED,
            RECORD_STATUS_DISCARDED_DUPLICATE,
            RECORD_STATUS_OBSOLETE,
        )

    def _map_record_status_to_target(self, record_status: str) -> Optional[str]:
        if record_status == RECORD_STATUS_SCHEDULED:
            return TREATMENT_TARGET_TREATED
        if record_status == RECORD_STATUS_IGNORED:
            return TREATMENT_TARGET_WITHOUT_PROVIDENCE
        if record_status == RECORD_STATUS_DISCARDED_DUPLICATE:
            # Duplicatas (mesmo processo/data) não têm providência nova a tomar:
            # a publicação "irmã" já está sendo tratada. O RPA só precisa
            # marcar no Legal One como "sem providência" para limpar a caixa.
            return TREATMENT_TARGET_WITHOUT_PROVIDENCE
        if record_status == RECORD_STATUS_OBSOLETE:
            # Publicações anteriores à criação da pasta do processo já foram
            # auditadas na esteira de admissão — sem providência necessária.
            return TREATMENT_TARGET_WITHOUT_PROVIDENCE
        return None

    def _resolve_output_root(self) -> Path:
        if settings.publication_treatment_output_dir:
            return Path(settings.publication_treatment_output_dir)
        return Path(__file__).resolve().parents[2] / "output" / "playwright" / "legalone" / "publication-treatment"

    def _resolve_runner_script(self) -> Path:
        if settings.publication_treatment_runner_script:
            return Path(settings.publication_treatment_runner_script)
        # Runner versionado em app/runners/legalone/ (código-fonte).
        # output/playwright/legalone/ continua sendo usado só pra artefatos de runtime.
        return Path(__file__).resolve().parents[2] / "app" / "runners" / "legalone" / "treat-publications.js"

    def _resolve_node_binary(self) -> str:
        candidate = shutil.which("node") or shutil.which("node.exe")
        if not candidate:
            raise RuntimeError("Node.js nao encontrado no PATH. Instale o Node para executar o runner Playwright.")
        return candidate

    def _resolve_credentials(self) -> dict[str, str]:
        username = settings.legal_one_web_username or os.getenv("LEGALONE_WEB_USERNAME")
        password = settings.legal_one_web_password or os.getenv("LEGALONE_WEB_PASSWORD")
        key_label = settings.legal_one_web_key_label or os.getenv("LEGALONE_WEB_KEY_LABEL")
        missing = [
            name
            for name, value in {
                "LEGALONE_WEB_USERNAME": username,
                "LEGALONE_WEB_PASSWORD": password,
                "LEGALONE_WEB_KEY_LABEL": key_label,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(
                "Credenciais web do Legal One ausentes para o tratamento de publicacoes: "
                + ", ".join(missing)
            )
        return {
            "LEGALONE_WEB_USERNAME": username,
            "LEGALONE_WEB_PASSWORD": password,
            "LEGALONE_WEB_KEY_LABEL": key_label,
        }

    def _build_run_paths(self, run_id: int) -> dict[str, Path]:
        run_dir = self._resolve_output_root() / f"run-{run_id:06d}"
        return {
            "run_dir": run_dir,
            "input": run_dir / "input.json",
            "status": run_dir / "status.json",
            "control": run_dir / "control.txt",
            "log": run_dir / "runner.log",
            "error_log": run_dir / "runner.err.log",
            "artifacts": run_dir / "artifacts",
        }

    def _record_query(self, office_ids: Optional[list[int]] = None):
        query = self.db.query(PublicationRecord).options(joinedload(PublicationRecord.treatment_item))
        if office_ids:
            query = query.filter(PublicationRecord.linked_office_id.in_(office_ids))
        return query

    def sync_item_from_record(
        self,
        record: PublicationRecord,
        *,
        commit: bool = True,
    ) -> Optional[PublicationTreatmentItem]:
        now = self._utcnow()
        # Duplicatas agora entram na fila (mapeadas para "sem providência")
        # via _map_record_status_to_target + RECORD_STATUS_DISCARDED_DUPLICATE.
        target_status = self._map_record_status_to_target(record.status)
        item = record.treatment_item

        if target_status is None or not record.legal_one_update_id:
            if item is not None:
                item.source_record_status = record.status
                item.linked_lawsuit_id = record.linked_lawsuit_id
                item.linked_lawsuit_cnj = record.linked_lawsuit_cnj
                item.linked_office_id = record.linked_office_id
                item.publication_date = record.publication_date
                if item.queue_status != QUEUE_STATUS_COMPLETED:
                    item.queue_status = QUEUE_STATUS_CANCELLED
                    item.updated_at = now
            if commit:
                self.db.commit()
            return item

        if item is None:
            item = PublicationTreatmentItem(
                publication_record_id=record.id,
                legal_one_update_id=record.legal_one_update_id,
                linked_lawsuit_id=record.linked_lawsuit_id,
                linked_lawsuit_cnj=record.linked_lawsuit_cnj,
                linked_office_id=record.linked_office_id,
                publication_date=record.publication_date,
                source_record_status=record.status,
                target_status=target_status,
                queue_status=QUEUE_STATUS_PENDING,
                attempt_count=0,
                created_at=now,
                updated_at=now,
            )
            self.db.add(item)
            record.treatment_item = item
        else:
            previous_target_status = item.target_status
            item.legal_one_update_id = record.legal_one_update_id
            item.linked_lawsuit_id = record.linked_lawsuit_id
            item.linked_lawsuit_cnj = record.linked_lawsuit_cnj
            item.linked_office_id = record.linked_office_id
            item.publication_date = record.publication_date
            item.source_record_status = record.status
            item.target_status = target_status
            should_reset = previous_target_status != target_status or item.queue_status in {
                QUEUE_STATUS_CANCELLED,
                QUEUE_STATUS_FAILED,
            }
            if should_reset:
                item.queue_status = QUEUE_STATUS_PENDING
                item.treated_at = None
                item.last_error = None
                item.last_response = None
                item.last_run_id = None
                if previous_target_status != target_status:
                    item.attempt_count = 0
            item.updated_at = now

        if commit:
            self.db.commit()
        return item

    def backfill_eligible_items(self, office_ids: Optional[list[int]] = None) -> dict[str, int]:
        created = 0
        updated = 0
        cancelled = 0
        records = self._record_query(office_ids).all()
        for record in records:
            existing_id = record.treatment_item.id if record.treatment_item else None
            existing_status = record.treatment_item.queue_status if record.treatment_item else None
            item = self.sync_item_from_record(record, commit=False)
            if item is None:
                continue
            if existing_id is None:
                created += 1
            elif item.queue_status == QUEUE_STATUS_CANCELLED and existing_status != QUEUE_STATUS_CANCELLED:
                cancelled += 1
            else:
                updated += 1
        self.db.commit()
        return {
            "created": created,
            "updated": updated,
            "cancelled": cancelled,
            "scanned": len(records),
        }

    def get_summary(self, office_ids: Optional[list[int]] = None) -> dict[str, Any]:
        eligible_statuses = self._eligible_record_statuses()
        query = (
            self.db.query(PublicationTreatmentItem)
            .join(PublicationRecord, PublicationRecord.id == PublicationTreatmentItem.publication_record_id)
            .filter(PublicationRecord.status.in_(eligible_statuses))
        )
        if office_ids:
            query = query.filter(PublicationTreatmentItem.linked_office_id.in_(office_ids))
        total_items = query.count()
        status_rows = (
            query.with_entities(
                PublicationTreatmentItem.queue_status,
                func.count(PublicationTreatmentItem.id),
            )
            .group_by(PublicationTreatmentItem.queue_status)
            .all()
        )
        target_rows = (
            query.with_entities(
                PublicationTreatmentItem.target_status,
                func.count(PublicationTreatmentItem.id),
            )
            .group_by(PublicationTreatmentItem.target_status)
            .all()
        )
        eligible_records_query = self._record_query(office_ids).filter(
            PublicationRecord.status.in_(eligible_statuses),
        )
        by_status = {status: count for status, count in status_rows}
        by_target = {status: count for status, count in target_rows}
        queue_count = (
            by_status.get(QUEUE_STATUS_PENDING, 0)
            + by_status.get(QUEUE_STATUS_PROCESSING, 0)
            + by_status.get(QUEUE_STATUS_FAILED, 0)
        )
        return {
            "total_items": total_items,
            "eligible_records": eligible_records_query.count(),
            "queue_count": queue_count,
            "pending_count": by_status.get(QUEUE_STATUS_PENDING, 0),
            "processing_count": by_status.get(QUEUE_STATUS_PROCESSING, 0),
            "completed_count": by_status.get(QUEUE_STATUS_COMPLETED, 0),
            "failed_count": by_status.get(QUEUE_STATUS_FAILED, 0),
            "cancelled_count": by_status.get(QUEUE_STATUS_CANCELLED, 0),
            "treated_target_count": by_target.get(TREATMENT_TARGET_TREATED, 0),
            "without_providence_target_count": by_target.get(TREATMENT_TARGET_WITHOUT_PROVIDENCE, 0),
        }

    def _item_to_dict(self, item: PublicationTreatmentItem) -> dict[str, Any]:
        record = item.record
        return {
            "id": item.id,
            "publication_record_id": item.publication_record_id,
            "legal_one_update_id": item.legal_one_update_id,
            "linked_lawsuit_id": item.linked_lawsuit_id,
            "linked_lawsuit_cnj": item.linked_lawsuit_cnj,
            "linked_office_id": item.linked_office_id,
            "publication_date": item.publication_date,
            "source_record_status": item.source_record_status,
            "target_status": item.target_status,
            "queue_status": item.queue_status,
            "attempt_count": item.attempt_count,
            "last_run_id": item.last_run_id,
            "last_attempt_at": self._serialize_datetime(item.last_attempt_at),
            "treated_at": self._serialize_datetime(item.treated_at),
            "last_error": item.last_error,
            "last_response": item.last_response,
            "created_at": self._serialize_datetime(item.created_at),
            "updated_at": self._serialize_datetime(item.updated_at),
            "record_status": record.status if record else None,
            "publication_link": (
                f"https://firm.legalone.com.br/publications?publicationId={item.legal_one_update_id}&treatStatus=3"
                if item.legal_one_update_id
                else None
            ),
        }

    def list_items(
        self,
        *,
        office_ids: Optional[list[int]] = None,
        queue_status: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = (
            self.db.query(PublicationTreatmentItem)
            .options(joinedload(PublicationTreatmentItem.record))
            .order_by(PublicationTreatmentItem.id.desc())
        )
        if office_ids:
            query = query.filter(PublicationTreatmentItem.linked_office_id.in_(office_ids))
        if queue_status:
            query = query.filter(PublicationTreatmentItem.queue_status == queue_status)
        return [self._item_to_dict(item) for item in query.limit(limit).all()]

    def _run_to_dict(self, run: PublicationTreatmentRun, *, include_internal_paths: bool = True) -> dict[str, Any]:
        payload = {
            "id": run.id,
            "status": run.status,
            "trigger_type": run.trigger_type,
            "triggered_by_email": run.triggered_by_email,
            "automation_id": run.automation_id,
            "total_items": run.total_items,
            "processed_items": run.processed_items,
            "success_count": run.success_count,
            "failed_count": run.failed_count,
            "retry_pending_count": run.retry_pending_count,
            "batch_size": run.batch_size,
            "total_batches": run.total_batches,
            "current_batch": run.current_batch,
            "max_attempts": run.max_attempts,
            "generated_at": self._serialize_datetime(run.generated_at),
            "sleep_until": self._serialize_datetime(run.sleep_until),
            "started_at": self._serialize_datetime(run.started_at),
            "finished_at": self._serialize_datetime(run.finished_at),
            "error_message": run.error_message,
            "is_final": run.status in FINAL_RUN_STATUSES,
        }
        if include_internal_paths:
            payload.update(
                {
                    "input_file_path": run.input_file_path,
                    "status_file_path": run.status_file_path,
                    "control_file_path": run.control_file_path,
                    "log_file_path": run.log_file_path,
                    "error_log_file_path": run.error_log_file_path,
                }
            )
        return payload

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        runs = (
            self.db.query(PublicationTreatmentRun)
            .order_by(PublicationTreatmentRun.id.desc())
            .limit(limit)
            .all()
        )
        should_commit = False
        for run in runs:
            if not self._should_sync_run_from_file(run):
                continue
            self._sync_run_from_status_file(run, commit=False)
            should_commit = True
        if should_commit:
            self.db.commit()
        return [self._run_to_dict(run) for run in runs]

    def _read_control_signal(self, run: Optional[PublicationTreatmentRun]) -> str:
        if not run or not run.control_file_path:
            return "run"
        control_file = Path(run.control_file_path)
        if not control_file.exists():
            return "run"
        try:
            signal = control_file.read_text(encoding="utf-8").strip().lower()
        except OSError:
            return "run"
        return signal if signal in {"pause", "stop"} else "run"

    @staticmethod
    def _should_sync_run_from_file(run: Optional[PublicationTreatmentRun]) -> bool:
        if not run or not run.status_file_path:
            return False
        # Runs finais já persistidos não precisam reler artefato de disco a cada
        # poll do painel. Isso reduz I/O desnecessário e evita travar o monitor
        # se um caminho antigo do container ficar lento ou indisponível.
        if run.status in FINAL_RUN_STATUSES and run.finished_at:
            return False
        return True

    def get_run(self, run_id: int, *, sync_from_file: bool = True) -> Optional[PublicationTreatmentRun]:
        self.db.expire_all()
        run = self.db.query(PublicationTreatmentRun).filter(PublicationTreatmentRun.id == run_id).first()
        if run and sync_from_file and self._should_sync_run_from_file(run):
            self._sync_run_from_status_file(run)
        return run

    def get_active_run(self) -> Optional[PublicationTreatmentRun]:
        active_runs = (
            self.db.query(PublicationTreatmentRun)
            .filter(PublicationTreatmentRun.status.in_(tuple(ACTIVE_RUN_STATUSES)))
            .order_by(PublicationTreatmentRun.id.desc())
            .all()
        )
        for run in active_runs:
            self._sync_run_from_status_file(run, commit=False)
        if active_runs:
            self.db.commit()
        return (
            self.db.query(PublicationTreatmentRun)
            .filter(PublicationTreatmentRun.status.in_(tuple(ACTIVE_RUN_STATUSES)))
            .order_by(PublicationTreatmentRun.id.desc())
            .first()
        )

    def _build_runner_items(self, items: list[PublicationTreatmentItem]) -> list[dict[str, Any]]:
        return [
            {
                "index": index,
                "sequenceNumber": str(index).zfill(4),
                "queueItemId": item.id,
                "publicationRecordId": item.publication_record_id,
                "publicationId": item.legal_one_update_id,
                "cnj": item.linked_lawsuit_cnj,
                "lawsuitId": item.linked_lawsuit_id,
                "publicationDate": item.publication_date,
                "officeId": item.linked_office_id,
                "targetStatus": item.target_status,
                "sourceRecordStatus": item.source_record_status,
            }
            for index, item in enumerate(items, start=1)
        ]

    def _build_initial_status_payload(
        self,
        *,
        runner_items: list[dict[str, Any]],
        control_file_path: str,
        batch_size: int,
        max_attempts: int,
    ) -> dict[str, Any]:
        total_items = len(runner_items)
        return {
            "generatedAt": self._utcnow().isoformat(),
            "state": "starting",
            "batchSize": batch_size,
            "totalBatches": max(1, (total_items + batch_size - 1) // batch_size) if total_items else 1,
            "currentBatch": 1,
            "controlFile": control_file_path,
            "totalItems": total_items,
            "processedItems": 0,
            "successCount": 0,
            "failedCount": 0,
            "retryPendingCount": 0,
            "remainingItems": total_items,
            "maxAttempts": max_attempts,
            "items": [
                {
                    **item,
                    "status": RUNNER_STATUS_PENDING,
                    "attempts": 0,
                    "retryPending": False,
                    "startedAt": None,
                    "finishedAt": None,
                    "error": None,
                }
                for item in runner_items
            ],
        }

    def start_run(
        self,
        *,
        office_ids: Optional[list[int]] = None,
        item_ids: Optional[list[int]] = None,
        trigger_type: str = RUN_TRIGGER_MANUAL,
        triggered_by_email: Optional[str] = None,
        automation_id: Optional[int] = None,
    ) -> dict[str, Any]:
        # Auto-heal: um run zumbi (runner morto sem gravar estado final)
        # travaria todo inicio novo com "already_running" para sempre —
        # tanto o botao manual quanto o autorun recorrente.
        self.recover_stale_runs(reason="pre-start")
        # Retries direcionados (item_ids) pulam o backfill global para evitar
        # reabrir itens FALHA de outros processos sem intencao explicita.
        if item_ids:
            backfill = {"created": 0, "updated": 0, "cancelled": 0, "scanned": 0}
        else:
            backfill = self.backfill_eligible_items(office_ids)
        active_run = self.get_active_run()
        if active_run:
            return {
                "started": False,
                "reason": "already_running",
                "backfill": backfill,
                "run": self._run_to_dict(active_run),
            }

        items_query = (
            self.db.query(PublicationTreatmentItem)
            .join(PublicationRecord, PublicationRecord.id == PublicationTreatmentItem.publication_record_id)
            .options(joinedload(PublicationTreatmentItem.record))
            .filter(PublicationRecord.status.in_(self._eligible_record_statuses()))
            .filter(PublicationTreatmentItem.queue_status.in_((QUEUE_STATUS_PENDING, QUEUE_STATUS_FAILED)))
            .order_by(PublicationTreatmentItem.id.asc())
        )
        if office_ids:
            items_query = items_query.filter(PublicationTreatmentItem.linked_office_id.in_(office_ids))
        if item_ids:
            items_query = items_query.filter(PublicationTreatmentItem.id.in_(item_ids))

        items = items_query.all()
        if not items:
            return {
                "started": False,
                "reason": "no_pending_items",
                "backfill": backfill,
                "run": None,
            }

        runner_script = self._resolve_runner_script()
        if not runner_script.exists():
            raise RuntimeError(f"Runner Playwright nao encontrado em {runner_script}")

        credentials = self._resolve_credentials()
        node_binary = self._resolve_node_binary()
        batch_size = max(1, settings.publication_treatment_batch_size)
        pause_seconds = max(0, settings.publication_treatment_pause_seconds)
        max_attempts = max(1, settings.publication_treatment_max_attempts)

        run = PublicationTreatmentRun(
            status=RUN_STATUS_STARTING,
            trigger_type=trigger_type or RUN_TRIGGER_MANUAL,
            triggered_by_email=triggered_by_email,
            automation_id=automation_id,
            total_items=len(items),
            processed_items=0,
            success_count=0,
            failed_count=0,
            retry_pending_count=0,
            batch_size=batch_size,
            total_batches=max(1, (len(items) + batch_size - 1) // batch_size),
            current_batch=1,
            max_attempts=max_attempts,
            generated_at=self._utcnow(),
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

        paths = self._build_run_paths(run.id)
        runner_items = self._build_runner_items(items)
        initial_status = self._build_initial_status_payload(
            runner_items=runner_items,
            control_file_path=str(paths["control"]),
            batch_size=batch_size,
            max_attempts=max_attempts,
        )

        self._write_json_file(paths["input"], runner_items)
        self._write_json_file(paths["status"], initial_status)
        paths["control"].parent.mkdir(parents=True, exist_ok=True)
        paths["control"].write_text("run", encoding="utf-8")
        paths["log"].parent.mkdir(parents=True, exist_ok=True)
        paths["log"].touch(exist_ok=True)
        paths["error_log"].touch(exist_ok=True)
        paths["artifacts"].mkdir(parents=True, exist_ok=True)

        run.input_file_path = str(paths["input"])
        run.status_file_path = str(paths["status"])
        run.control_file_path = str(paths["control"])
        run.log_file_path = str(paths["log"])
        run.error_log_file_path = str(paths["error_log"])

        for item in items:
            item.last_run_id = run.id
            item.queue_status = QUEUE_STATUS_PENDING
            item.updated_at = self._utcnow()

        command = [
            node_binary,
            str(runner_script),
            "--input",
            str(paths["input"]),
            "--output",
            str(paths["status"]),
            "--control-file",
            str(paths["control"]),
            "--batch-size",
            str(batch_size),
            "--pause-between-batches-seconds",
            str(pause_seconds),
            "--max-attempts",
            str(max_attempts),
            "--artifacts-dir",
            str(paths["artifacts"]),
        ]

        env = {**os.environ, **credentials}
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with paths["log"].open("ab") as stdout, paths["error_log"].open("ab") as stderr:
            proc = subprocess.Popen(  # noqa: S603
                command,
                cwd=str(runner_script.parent),
                env=env,
                stdout=stdout,
                stderr=stderr,
                creationflags=creation_flags,
            )

        logger.info(
            "tratamento: runner iniciado run=%s pid=%s batch=%s items=%s",
            run.id, proc.pid, batch_size, len(items),
        )

        # Espelha runner.log / runner.err.log no stdout do container via threads
        # daemon. Isso é o que faltava pra o operador ver o que o runner está
        # fazendo sem precisar `docker exec` e `tail` nos arquivos.
        threading.Thread(
            target=_tail_runner_log_to_logger,
            args=(paths["log"], proc.pid, run.id, logging.INFO),
            daemon=True,
            name=f"runner-stdout-tail-{run.id}",
        ).start()
        threading.Thread(
            target=_tail_runner_log_to_logger,
            args=(paths["error_log"], proc.pid, run.id, logging.WARNING),
            daemon=True,
            name=f"runner-stderr-tail-{run.id}",
        ).start()

        run.status = RUN_STATUS_RUNNING
        self.db.commit()
        self.db.refresh(run)

        return {
            "started": True,
            "reason": "started",
            "backfill": backfill,
            "run": self._run_to_dict(run),
        }

    def _map_runner_state_to_run_status(self, payload: dict[str, Any]) -> str:
        state = (payload.get("state") or "").strip().lower()
        failed_count = int(payload.get("failedCount") or 0)
        retry_pending_count = int(payload.get("retryPendingCount") or 0)
        remaining_items = int(payload.get("remainingItems") or 0)

        if state == "paused":
            return RUN_STATUS_PAUSED
        if state in {"running", "sleeping", "starting"}:
            return RUN_STATUS_RUNNING
        if state == "completed":
            if failed_count > 0 or retry_pending_count > 0:
                return RUN_STATUS_COMPLETED_WITH_ERRORS
            return RUN_STATUS_COMPLETED
        if state == "stopped":
            if remaining_items <= 0:
                if failed_count > 0 or retry_pending_count > 0:
                    return RUN_STATUS_COMPLETED_WITH_ERRORS
                return RUN_STATUS_COMPLETED
            return RUN_STATUS_STOPPED
        return RUN_STATUS_FAILED

    def _sync_items_from_status_payload(self, run_id: int, payload: dict[str, Any]) -> None:
        items = payload.get("items") or []
        if not isinstance(items, list):
            return

        items_by_id = {
            item.id: item
            for item in self.db.query(PublicationTreatmentItem)
            .filter(PublicationTreatmentItem.last_run_id == run_id)
            .all()
        }

        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            queue_item_id = raw_item.get("queueItemId")
            if not queue_item_id:
                continue

            item = items_by_id.get(int(queue_item_id))
            if item is None:
                item = self.db.query(PublicationTreatmentItem).filter(
                    PublicationTreatmentItem.id == int(queue_item_id)
                ).first()
            if item is None:
                continue

            now = self._utcnow()
            status = raw_item.get("status")
            final_status = raw_item.get("finalStatus") or status

            if status in {RUNNER_STATUS_TREATED, RUNNER_STATUS_WITHOUT_PROVIDENCE}:
                item.queue_status = QUEUE_STATUS_COMPLETED
                item.treated_at = self._parse_iso_datetime(raw_item.get("finishedAt")) or now
                item.last_error = None
            elif status == RUNNER_STATUS_PENDING:
                item.queue_status = QUEUE_STATUS_PENDING
            elif status == RUNNER_STATUS_SCHEDULED_RETRY:
                item.queue_status = QUEUE_STATUS_FAILED
                item.last_error = raw_item.get("error")
            else:
                item.queue_status = QUEUE_STATUS_FAILED
                item.last_error = raw_item.get("error")

            item.attempt_count = max(int(item.attempt_count or 0), int(raw_item.get("attempts") or 0))
            item.last_attempt_at = (
                self._parse_iso_datetime(raw_item.get("finishedAt"))
                or self._parse_iso_datetime(raw_item.get("startedAt"))
                or item.last_attempt_at
            )
            item.last_run_id = run_id
            item.last_response = raw_item.get("response") or raw_item.get("result") or raw_item.get("payload")
            if status in {RUNNER_STATUS_PENDING, RUNNER_STATUS_SCHEDULED_RETRY} and final_status in {
                RUNNER_STATUS_TREATED,
                RUNNER_STATUS_WITHOUT_PROVIDENCE,
            }:
                item.queue_status = QUEUE_STATUS_PROCESSING
            item.updated_at = now

    def _sync_run_from_status_file(self, run: PublicationTreatmentRun, *, commit: bool = True) -> PublicationTreatmentRun:
        if not run.status_file_path:
            return run

        status_file = Path(run.status_file_path)
        payload = self._read_json_file(status_file)
        if not isinstance(payload, dict):
            return run

        run.generated_at = self._parse_iso_datetime(payload.get("generatedAt")) or run.generated_at
        run.total_items = int(payload.get("totalItems") or run.total_items or 0)
        run.processed_items = int(payload.get("processedItems") or 0)
        run.success_count = int(payload.get("successCount") or 0)
        run.failed_count = int(payload.get("failedCount") or 0)
        run.retry_pending_count = int(payload.get("retryPendingCount") or 0)
        run.batch_size = int(payload.get("batchSize") or run.batch_size or 0) or None
        run.total_batches = int(payload.get("totalBatches") or run.total_batches or 0) or None
        run.current_batch = int(payload.get("currentBatch") or run.current_batch or 0) or None
        run.max_attempts = int(payload.get("maxAttempts") or run.max_attempts or 0) or None
        run.sleep_until = self._parse_iso_datetime(payload.get("sleepUntil"))
        run.status = self._map_runner_state_to_run_status(payload)

        if run.status in FINAL_RUN_STATUSES and not run.finished_at:
            run.finished_at = self._parse_iso_datetime(payload.get("generatedAt")) or self._utcnow()

        self._sync_items_from_status_payload(run.id, payload)

        if commit:
            self.db.commit()
            self.db.refresh(run)
        return run

    def retry_item(
        self,
        item_id: int,
        *,
        triggered_by_email: Optional[str] = None,
    ) -> dict[str, Any]:
        """Reenfileira um unico item (tipicamente em FALHA) e inicia um run dedicado."""
        item = (
            self.db.query(PublicationTreatmentItem)
            .filter(PublicationTreatmentItem.id == item_id)
            .first()
        )
        if item is None:
            raise ValueError(f"Item de tratamento #{item_id} nao encontrado.")
        if item.queue_status == QUEUE_STATUS_COMPLETED:
            raise ValueError(
                f"Item #{item_id} ja foi concluido e nao pode ser reexecutado."
            )
        if item.queue_status == QUEUE_STATUS_PROCESSING:
            raise RuntimeError(
                f"Item #{item_id} esta em processamento no momento. Aguarde a execucao atual finalizar."
            )

        active_run = self.get_active_run()
        if active_run:
            raise RuntimeError(
                "Ja existe uma execucao em andamento. Aguarde finalizar antes de reexecutar itens individualmente."
            )

        now = self._utcnow()
        item.queue_status = QUEUE_STATUS_PENDING
        item.last_error = None
        item.last_response = None
        item.attempt_count = 0
        item.treated_at = None
        item.updated_at = now
        self.db.commit()
        self.db.refresh(item)

        return self.start_run(
            item_ids=[item_id],
            triggered_by_email=triggered_by_email,
            trigger_type=RUN_TRIGGER_MANUAL,
        )

    def set_run_control(self, run_id: int, action: str) -> dict[str, Any]:
        run = self.get_run(run_id, sync_from_file=True)
        if not run:
            raise ValueError(f"Execucao #{run_id} nao encontrada.")
        if not run.control_file_path:
            raise ValueError(f"Execucao #{run_id} nao possui arquivo de controle.")

        desired_signal = "pause" if action == "pause" else "run"
        control_file = Path(run.control_file_path)
        control_file.parent.mkdir(parents=True, exist_ok=True)
        control_file.write_text(desired_signal, encoding="utf-8")

        return {
            "message": "Sinal aplicado com sucesso.",
            "action": action,
            "signal": desired_signal,
            "control_file": str(control_file),
            "run": self._run_to_dict(run),
        }

    def get_autorun_info(self) -> dict[str, Any]:
        """Config do autorun recorrente exposta pro painel (proxima execucao automatica)."""
        info: dict[str, Any] = {
            "enabled": bool(settings.publication_treatment_autorun_enabled),
            "cron_expression": settings.publication_treatment_autorun_cron,
            "next_run_at": None,
        }
        if not info["enabled"]:
            return info
        try:
            from zoneinfo import ZoneInfo

            from apscheduler.triggers.cron import CronTrigger

            tz = ZoneInfo("America/Sao_Paulo")
            trigger = CronTrigger.from_crontab(
                settings.publication_treatment_autorun_cron, timezone=tz
            )
            next_fire = trigger.get_next_fire_time(None, datetime.now(tz))
            info["next_run_at"] = next_fire.isoformat() if next_fire else None
        except Exception:  # noqa: BLE001 — cron invalida nao pode derrubar o monitor
            logger.warning(
                "tratamento: cron do autorun invalida (%s) — painel fica sem proxima execucao.",
                settings.publication_treatment_autorun_cron,
            )
        return info

    def recover_stale_runs(
        self,
        *,
        threshold_minutes: Optional[int] = None,
        reason: str = "tick",
    ) -> int:
        """
        Marca como FALHA runs ativos cujo runner morreu sem gravar estado final
        (SIGKILL/OOM/restart de container mata o subprocess Node e o status.json
        congela em "running"). Sem isso, get_active_run devolve o zumbi pra
        sempre e todo start_run — manual ou do autorun — responde
        "already_running", travando o modulo ate intervencao manual.

        Criterio: sem heartbeat (generatedAt do status.json) ha mais de
        `threshold_minutes`, ignorando runs pausados de proposito (acao do
        operador) e runners dormindo entre lotes (sleep_until ainda no futuro).
        Itens presos em PROCESSANDO voltam pra PENDENTE e entram no proximo ciclo.
        """
        threshold = max(5, threshold_minutes or settings.publication_treatment_stale_run_minutes)
        now = self._utcnow()
        cutoff = now - timedelta(minutes=threshold)

        active_runs = (
            self.db.query(PublicationTreatmentRun)
            .filter(PublicationTreatmentRun.status.in_(tuple(ACTIVE_RUN_STATUSES)))
            .all()
        )
        recovered = 0
        for run in active_runs:
            # Releitura do artefato: o runner pode ter finalizado sem o banco refletir.
            self._sync_run_from_status_file(run, commit=False)
            if run.status not in ACTIVE_RUN_STATUSES:
                continue
            if run.status == RUN_STATUS_PAUSED or self._read_control_signal(run) == "pause":
                continue

            heartbeat = run.generated_at or run.started_at
            if heartbeat is None:
                continue
            if heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=timezone.utc)
            sleep_until = run.sleep_until
            if sleep_until is not None and sleep_until.tzinfo is None:
                sleep_until = sleep_until.replace(tzinfo=timezone.utc)
            last_sign_of_life = max(heartbeat, sleep_until) if sleep_until else heartbeat
            if last_sign_of_life >= cutoff:
                continue

            run.status = RUN_STATUS_FAILED
            run.finished_at = now
            run.error_message = (
                f"Execucao zumbi detectada ({reason}): sem heartbeat do runner ha mais de "
                f"{threshold} min. O processo Playwright provavelmente morreu "
                "(OOM/restart/deploy) sem gravar estado final. Itens em PROCESSANDO "
                "voltaram pra fila e serao tratados no proximo ciclo."
            )
            stuck_items = (
                self.db.query(PublicationTreatmentItem)
                .filter(PublicationTreatmentItem.last_run_id == run.id)
                .filter(PublicationTreatmentItem.queue_status == QUEUE_STATUS_PROCESSING)
                .all()
            )
            for item in stuck_items:
                item.queue_status = QUEUE_STATUS_PENDING
                item.updated_at = now
            recovered += 1
            logger.warning(
                "tratamento: run #%s reapado como zumbi (%s) — %d item(ns) devolvido(s) pra fila.",
                run.id, reason, len(stuck_items),
            )

        if active_runs:
            self.db.commit()
        return recovered

    def get_monitor(self, *, office_ids: Optional[list[int]] = None) -> dict[str, Any]:
        active_run = self.get_active_run()
        latest_run = active_run
        if latest_run is None:
            latest_run = (
                self.db.query(PublicationTreatmentRun)
                .order_by(PublicationTreatmentRun.id.desc())
                .first()
            )
            if latest_run and self._should_sync_run_from_file(latest_run):
                latest_run = self.get_run(latest_run.id, sync_from_file=True)

        summary = self.get_summary(office_ids)
        recent_items = self.list_items(office_ids=office_ids, limit=25)
        recent_failures = self.list_items(office_ids=office_ids, queue_status=QUEUE_STATUS_FAILED, limit=15)
        run_payload = self._run_to_dict(latest_run) if latest_run else None

        progress_percentage = 0
        control_signal = self._read_control_signal(latest_run)
        if run_payload and run_payload["total_items"]:
            progress_percentage = int((run_payload["processed_items"] / run_payload["total_items"]) * 100)

        return {
            "summary": summary,
            "active_run": run_payload,
            "available": bool(run_payload),
            "progress_percentage": progress_percentage,
            "control_signal": control_signal,
            "recent_items": recent_items,
            "recent_failures": recent_failures,
            "autorun": self.get_autorun_info(),
        }

    def wait_for_run_completion(
        self,
        run_id: int,
        *,
        poll_seconds: Optional[int] = None,
        timeout_seconds: int = 6 * 60 * 60,
    ) -> dict[str, Any]:
        effective_poll = max(1, poll_seconds or settings.publication_treatment_monitor_poll_seconds)
        deadline = time.monotonic() + timeout_seconds

        while True:
            run = self.get_run(run_id, sync_from_file=True)
            if not run:
                raise RuntimeError(f"Execucao de tratamento #{run_id} nao encontrada.")

            payload = self._run_to_dict(run)
            if payload["is_final"]:
                return payload
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Tempo esgotado aguardando a execucao #{run_id} finalizar.")
            time.sleep(effective_poll)
