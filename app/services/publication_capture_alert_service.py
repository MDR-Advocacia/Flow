"""Outbox durável para alertas da captura de publicações.

O serviço entrega em modo *at-least-once*: uma queda depois de o SMTP aceitar
a mensagem e antes do commit pode gerar uma repetição, mas nunca transforma
uma falha de envio em sucesso. A chave idempotente impede que o mesmo evento
crie várias linhas, e o claim ``processing`` evita envio simultâneo por sweeps
concorrentes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.publication_capture import (
    PUBLICATION_ALERT_STATUS_DEAD_LETTER,
    PUBLICATION_ALERT_STATUS_PENDING,
    PUBLICATION_ALERT_STATUS_PROCESSING,
    PUBLICATION_ALERT_STATUS_SENT,
    PublicationCaptureAlert,
    PublicationFetchAttempt,
)
from app.models.publication_search import PublicationSearch

logger = logging.getLogger(__name__)

# Falha imediata -> 1 min -> 5 min -> 30 min -> 1 h -> 3 h -> 12 h.
# Depois disso, repete a cada 12 h até o SMTP confirmar. O chamador ainda pode
# informar um limite positivo para eventos que aceitem dead-letter.
ALERT_RETRY_BACKOFF_MINUTES = (1, 5, 30, 60, 180, 720)
DEFAULT_ALERT_MAX_ATTEMPTS = 0
DEFAULT_ALERT_LEASE_MINUTES = 10
DEFAULT_ALERT_SWEEP_LIMIT = 100
DEFAULT_ALERT_REPAIR_GRACE_MINUTES = 10
DEFAULT_ALERT_REPAIR_LIMIT = 100


class AlertIdempotencyConflict(ValueError):
    """A mesma chave idempotente foi reutilizada com outro conteúdo."""


@dataclass(frozen=True)
class AlertDeliveryResult:
    alert_id: int
    attempted: bool
    sent: bool
    status: str
    attempt_count: int
    error: Optional[str] = None


@dataclass(frozen=True)
class AlertSweepResult:
    considered: int = 0
    attempted: int = 0
    sent: int = 0
    pending: int = 0
    dead_letter: int = 0
    errors: int = 0


@dataclass(frozen=True)
class AlertRepairResult:
    considered: int = 0
    linked_existing: int = 0
    created: int = 0
    errors: int = 0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _json_safe(value: Any) -> Any:
    """Converte datetimes/objetos auxiliares para um payload JSON persistível."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _normalize_recipients(
    recipients: Iterable[str] | str,
) -> list[str]:
    if isinstance(recipients, str):
        raw = recipients.split(",")
    else:
        raw = list(recipients)

    normalized: list[str] = []
    seen: set[str] = set()
    for value in raw:
        email = str(value or "").strip()
        key = email.casefold()
        if email and key not in seen:
            normalized.append(email)
            seen.add(key)
    return normalized


class PublicationCaptureAlertService:
    def __init__(
        self,
        db: Session,
        *,
        lease_minutes: int = DEFAULT_ALERT_LEASE_MINUTES,
    ) -> None:
        self.db = db
        self.lease = timedelta(minutes=max(1, int(lease_minutes)))

    @staticmethod
    def _validate_key(idempotency_key: str) -> str:
        key = str(idempotency_key or "").strip()
        if not key:
            raise ValueError("idempotency_key do alerta não pode ser vazia.")
        if len(key) > 255:
            raise ValueError("idempotency_key do alerta excede 255 caracteres.")
        return key

    @staticmethod
    def _backoff_for_attempt(attempt_count: int) -> timedelta:
        index = min(
            max(int(attempt_count), 1) - 1,
            len(ALERT_RETRY_BACKOFF_MINUTES) - 1,
        )
        return timedelta(minutes=ALERT_RETRY_BACKOFF_MINUTES[index])

    @staticmethod
    def _assert_idempotent_payload(
        existing: PublicationCaptureAlert,
        *,
        alert_type: str,
        recipients: list[str],
        failed_items: list[dict[str, Any]],
        batch_source: str,
        system_name: str,
        alert_context: Optional[dict[str, Any]],
        max_attempts: int,
    ) -> None:
        actual = {
            "alert_type": existing.alert_type,
            "recipients": existing.recipients or [],
            "failed_items": existing.failed_items or [],
            "batch_source": existing.batch_source,
            "system_name": existing.system_name,
            "alert_context": existing.alert_context,
            "max_attempts": int(existing.max_attempts or 0),
        }
        expected = {
            "alert_type": alert_type,
            "recipients": recipients,
            "failed_items": failed_items,
            "batch_source": batch_source,
            "system_name": system_name,
            "alert_context": alert_context,
            "max_attempts": max_attempts,
        }
        if actual != expected:
            raise AlertIdempotencyConflict(
                "idempotency_key já existe com outro payload: "
                f"{existing.idempotency_key}"
            )

    def enqueue(
        self,
        *,
        idempotency_key: str,
        alert_type: str,
        recipients: Iterable[str] | str,
        failed_items: list[dict[str, Any]],
        batch_source: str,
        system_name: str = "Flow",
        alert_context: Optional[dict[str, Any]] = None,
        max_attempts: int = DEFAULT_ALERT_MAX_ATTEMPTS,
        attempt_immediately: bool = True,
        now: Optional[datetime] = None,
    ) -> PublicationCaptureAlert:
        """Cria uma única linha e, por padrão, tenta entregá-la imediatamente.

        Repetir a chamada com a mesma chave e o mesmo payload devolve a linha
        original. Reutilizar a chave com conteúdo diferente levanta
        :class:`AlertIdempotencyConflict`.
        """
        key = self._validate_key(idempotency_key)
        kind = str(alert_type or "").strip()
        source = str(batch_source or "").strip()
        system = str(system_name or "Flow").strip() or "Flow"
        recipient_list = _normalize_recipients(recipients)
        safe_items = _json_safe(failed_items or [])
        safe_context = _json_safe(alert_context) if alert_context is not None else None
        attempts_limit = max(0, int(max_attempts))
        effective_now = _as_utc(now) if now is not None else _utcnow()

        if not kind:
            raise ValueError("alert_type não pode ser vazio.")
        if len(kind) > 64:
            raise ValueError("alert_type excede 64 caracteres.")
        if not source:
            raise ValueError("batch_source não pode ser vazio.")
        if len(source) > 255:
            raise ValueError("batch_source excede 255 caracteres.")
        if len(system) > 64:
            raise ValueError("system_name excede 64 caracteres.")
        if not recipient_list:
            raise ValueError("O alerta precisa de pelo menos um destinatário.")
        if not safe_items or not all(isinstance(item, dict) for item in safe_items):
            raise ValueError("failed_items precisa conter ao menos um objeto.")

        existing = (
            self.db.query(PublicationCaptureAlert)
            .filter(PublicationCaptureAlert.idempotency_key == key)
            .first()
        )
        if existing is None:
            alert = PublicationCaptureAlert(
                idempotency_key=key,
                alert_type=kind,
                status=PUBLICATION_ALERT_STATUS_PENDING,
                recipients=recipient_list,
                failed_items=safe_items,
                batch_source=source,
                system_name=system,
                alert_context=safe_context,
                attempt_count=0,
                max_attempts=attempts_limit,
                next_retry_at=effective_now,
            )
            self.db.add(alert)
            try:
                self.db.commit()
                self.db.refresh(alert)
                existing = alert
            except IntegrityError:
                # Outro worker pode ter inserido a mesma chave entre o SELECT
                # e o COMMIT. A restrição UNIQUE decide; nós recarregamos.
                self.db.rollback()
                existing = (
                    self.db.query(PublicationCaptureAlert)
                    .filter(PublicationCaptureAlert.idempotency_key == key)
                    .first()
                )
                if existing is None:
                    raise

        self._assert_idempotent_payload(
            existing,
            alert_type=kind,
            recipients=recipient_list,
            failed_items=safe_items,
            batch_source=source,
            system_name=system,
            alert_context=safe_context,
            max_attempts=attempts_limit,
        )

        if attempt_immediately:
            self.deliver(existing.id, now=effective_now)

        self.db.expire_all()
        return (
            self.db.query(PublicationCaptureAlert)
            .filter(PublicationCaptureAlert.id == existing.id)
            .one()
        )

    def _claim(self, alert_id: int, now: datetime) -> bool:
        stale_before = now - self.lease
        claimable = or_(
            and_(
                PublicationCaptureAlert.status == PUBLICATION_ALERT_STATUS_PENDING,
                or_(
                    PublicationCaptureAlert.next_retry_at.is_(None),
                    PublicationCaptureAlert.next_retry_at <= now,
                ),
            ),
            and_(
                PublicationCaptureAlert.status == PUBLICATION_ALERT_STATUS_PROCESSING,
                or_(
                    PublicationCaptureAlert.locked_at.is_(None),
                    PublicationCaptureAlert.locked_at <= stale_before,
                ),
            ),
        )
        claimed = (
            self.db.query(PublicationCaptureAlert)
            .filter(
                PublicationCaptureAlert.id == int(alert_id),
                claimable,
            )
            .update(
                {
                    PublicationCaptureAlert.status: PUBLICATION_ALERT_STATUS_PROCESSING,
                    PublicationCaptureAlert.locked_at: now,
                },
                synchronize_session=False,
            )
        )
        self.db.commit()
        self.db.expire_all()
        return claimed == 1

    def _result_without_attempt(
        self,
        alert_id: int,
    ) -> AlertDeliveryResult:
        row = (
            self.db.query(PublicationCaptureAlert)
            .filter(PublicationCaptureAlert.id == int(alert_id))
            .first()
        )
        if row is None:
            raise LookupError(f"Alerta de captura #{alert_id} não encontrado.")
        return AlertDeliveryResult(
            alert_id=row.id,
            attempted=False,
            sent=row.status == PUBLICATION_ALERT_STATUS_SENT,
            status=row.status,
            attempt_count=int(row.attempt_count or 0),
            error=row.last_error,
        )

    def deliver(
        self,
        alert_id: int,
        *,
        now: Optional[datetime] = None,
    ) -> AlertDeliveryResult:
        """Tenta entregar uma linha elegível e atualiza seu backoff."""
        attempted_at = _as_utc(now) if now is not None else _utcnow()
        if not self._claim(alert_id, attempted_at):
            return self._result_without_attempt(alert_id)

        row = (
            self.db.query(PublicationCaptureAlert)
            .filter(PublicationCaptureAlert.id == int(alert_id))
            .one()
        )
        next_attempt_count = int(row.attempt_count or 0) + 1
        max_attempts = max(0, int(row.max_attempts or 0))

        sent = False
        error: Optional[str] = None
        try:
            from app.services.mail_service import send_failure_report

            sent = bool(
                send_failure_report(
                    failed_items=list(row.failed_items or []),
                    batch_source=row.batch_source,
                    recipients=list(row.recipients or []),
                    system_name=row.system_name,
                )
            )
            if not sent:
                error = "SMTP não confirmou o envio do alerta."
        except Exception as exc:  # noqa: BLE001 - erro vira estado persistido
            error = f"{type(exc).__name__}: {exc}"
            logger.exception("Falha SMTP no alerta de captura #%s.", row.id)

        if sent:
            status = PUBLICATION_ALERT_STATUS_SENT
            next_retry_at = None
            sent_at = attempted_at
            last_error = None
        elif max_attempts > 0 and next_attempt_count >= max_attempts:
            status = PUBLICATION_ALERT_STATUS_DEAD_LETTER
            next_retry_at = None
            sent_at = None
            last_error = (error or "Falha desconhecida no SMTP.")[:4000]
        else:
            status = PUBLICATION_ALERT_STATUS_PENDING
            next_retry_at = attempted_at + self._backoff_for_attempt(
                next_attempt_count
            )
            sent_at = None
            last_error = (error or "Falha desconhecida no SMTP.")[:4000]

        # locked_at também é o token do claim. Se o lease expirou e outro
        # worker assumiu a linha, este worker não sobrescreve o estado novo.
        updated = (
            self.db.query(PublicationCaptureAlert)
            .filter(
                PublicationCaptureAlert.id == row.id,
                PublicationCaptureAlert.status
                == PUBLICATION_ALERT_STATUS_PROCESSING,
                PublicationCaptureAlert.locked_at == attempted_at,
            )
            .update(
                {
                    PublicationCaptureAlert.status: status,
                    PublicationCaptureAlert.attempt_count: next_attempt_count,
                    PublicationCaptureAlert.next_retry_at: next_retry_at,
                    PublicationCaptureAlert.last_attempt_at: attempted_at,
                    PublicationCaptureAlert.sent_at: sent_at,
                    PublicationCaptureAlert.locked_at: None,
                    PublicationCaptureAlert.last_error: last_error,
                },
                synchronize_session=False,
            )
        )
        self.db.commit()
        self.db.expire_all()
        if updated != 1:
            logger.warning(
                "Alerta #%s perdeu o lease durante o envio; mantendo estado "
                "gravado pelo worker mais recente.",
                row.id,
            )
            return self._result_without_attempt(row.id)

        current = (
            self.db.query(PublicationCaptureAlert)
            .filter(PublicationCaptureAlert.id == row.id)
            .one()
        )
        return AlertDeliveryResult(
            alert_id=current.id,
            attempted=True,
            sent=current.status == PUBLICATION_ALERT_STATUS_SENT,
            status=current.status,
            attempt_count=int(current.attempt_count or 0),
            error=current.last_error,
        )

    def sweep_due(
        self,
        *,
        limit: int = DEFAULT_ALERT_SWEEP_LIMIT,
        now: Optional[datetime] = None,
    ) -> AlertSweepResult:
        """Processa alertas vencidos e recupera claims abandonados."""
        effective_now = _as_utc(now) if now is not None else _utcnow()
        stale_before = effective_now - self.lease
        due = or_(
            and_(
                PublicationCaptureAlert.status == PUBLICATION_ALERT_STATUS_PENDING,
                or_(
                    PublicationCaptureAlert.next_retry_at.is_(None),
                    PublicationCaptureAlert.next_retry_at <= effective_now,
                ),
            ),
            and_(
                PublicationCaptureAlert.status
                == PUBLICATION_ALERT_STATUS_PROCESSING,
                or_(
                    PublicationCaptureAlert.locked_at.is_(None),
                    PublicationCaptureAlert.locked_at <= stale_before,
                ),
            ),
        )
        alert_ids = [
            row[0]
            for row in (
                self.db.query(PublicationCaptureAlert.id)
                .filter(due)
                .order_by(
                    PublicationCaptureAlert.next_retry_at.asc(),
                    PublicationCaptureAlert.id.asc(),
                )
                .limit(max(1, int(limit)))
                .all()
            )
        ]

        attempted = sent = pending = dead_letter = errors = 0
        for alert_id in alert_ids:
            try:
                result = self.deliver(alert_id, now=effective_now)
            except Exception:  # noqa: BLE001 - um alerta não bloqueia os demais
                errors += 1
                self.db.rollback()
                logger.exception(
                    "Sweep não conseguiu processar alerta de captura #%s.",
                    alert_id,
                )
                continue

            if not result.attempted:
                continue
            attempted += 1
            if result.status == PUBLICATION_ALERT_STATUS_SENT:
                sent += 1
            elif result.status == PUBLICATION_ALERT_STATUS_DEAD_LETTER:
                dead_letter += 1
            else:
                pending += 1

        return AlertSweepResult(
            considered=len(alert_ids),
            attempted=attempted,
            sent=sent,
            pending=pending,
            dead_letter=dead_letter,
            errors=errors,
        )


def sweep_publication_capture_alerts(
    db: Session,
    *,
    limit: int = DEFAULT_ALERT_SWEEP_LIMIT,
    now: Optional[datetime] = None,
) -> AlertSweepResult:
    """Entrypoint fino para integração futura com o scheduler."""
    return PublicationCaptureAlertService(db).sweep_due(
        limit=limit,
        now=now,
    )


def repair_missing_publication_capture_alerts(
    db: Session,
    *,
    limit: int = DEFAULT_ALERT_REPAIR_LIMIT,
    grace_minutes: int = DEFAULT_ALERT_REPAIR_GRACE_MINUTES,
    now: Optional[datetime] = None,
) -> AlertRepairResult:
    """Reconstrói outboxes ausentes após um restart na janela pós-commit.

    O marcador ``alert_required``/``l1_alert_required_*`` é gravado junto com
    a própria falha. O vínculo com o outbox vem logo depois; se o processo cair
    nesse intervalo, esta rotina encontra o marcador órfão e cria um alerta
    idempotente. A carência evita disputar com uma execução normal ainda ativa.
    """
    from app.core.config import settings

    effective_now = _as_utc(now) if now is not None else _utcnow()
    cutoff = effective_now - timedelta(
        minutes=max(1, int(grace_minutes))
    )
    row_limit = max(1, int(limit))
    recipients = (
        settings.publication_capture_alert_email
        or settings.classificacao_alert_email
        or settings.mail_to
        or settings.email_to
    )
    if not recipients:
        logger.error(
            "Não foi possível reparar alertas da captura: nenhum destinatário "
            "está configurado."
        )
        return AlertRepairResult(errors=1)

    scheduled_rows = (
        db.query(PublicationFetchAttempt)
        .filter(
            PublicationFetchAttempt.alert_required.is_(True),
            PublicationFetchAttempt.alert_outbox_id.is_(None),
            PublicationFetchAttempt.created_at <= cutoff,
        )
        .order_by(PublicationFetchAttempt.id.asc())
        .limit(row_limit)
        .all()
    )
    remaining = max(0, row_limit - len(scheduled_rows))
    manual_rows = (
        db.query(PublicationSearch)
        .filter(
            PublicationSearch.l1_alert_required_attempt.isnot(None),
            PublicationSearch.l1_alert_outbox_id.is_(None),
            PublicationSearch.l1_alert_required_at.isnot(None),
            PublicationSearch.l1_alert_required_at <= cutoff,
        )
        .order_by(PublicationSearch.id.asc())
        .limit(remaining)
        .all()
        if remaining
        else []
    )

    needed_attempt_ids = {int(row.id) for row in scheduled_rows}
    needed_manual = {
        (int(row.id), int(row.l1_alert_required_attempt))
        for row in manual_rows
    }
    scheduled_existing: dict[int, int] = {}
    manual_existing: dict[tuple[int, int], int] = {}
    if needed_attempt_ids or needed_manual:
        for alert_id, context in (
            db.query(
                PublicationCaptureAlert.id,
                PublicationCaptureAlert.alert_context,
            )
            .filter(PublicationCaptureAlert.alert_context.isnot(None))
            .order_by(PublicationCaptureAlert.id.desc())
            .all()
        ):
            context = context if isinstance(context, dict) else {}
            for attempt_id in context.get("attempt_ids") or []:
                try:
                    attempt_id = int(attempt_id)
                except (TypeError, ValueError):
                    continue
                if attempt_id in needed_attempt_ids:
                    scheduled_existing.setdefault(attempt_id, int(alert_id))

            try:
                manual_key = (
                    int(context.get("search_id")),
                    int(context.get("reconciliation_attempt")),
                )
            except (TypeError, ValueError):
                manual_key = None
            if manual_key in needed_manual:
                manual_existing.setdefault(manual_key, int(alert_id))

    linked_existing = 0
    for row in scheduled_rows:
        alert_id = scheduled_existing.get(int(row.id))
        if alert_id is not None:
            row.alert_outbox_id = alert_id
            linked_existing += 1
    for row in manual_rows:
        key = (int(row.id), int(row.l1_alert_required_attempt))
        alert_id = manual_existing.get(key)
        if alert_id is not None:
            row.l1_alert_outbox_id = alert_id
            linked_existing += 1
    if linked_existing:
        db.commit()

    service = PublicationCaptureAlertService(db)
    created = errors = 0
    for row in scheduled_rows:
        if row.alert_outbox_id is not None:
            continue
        try:
            retry_text = (
                f"Nova tentativa prevista para {row.next_retry_at.isoformat()}."
                if row.next_retry_at
                else "Limite de tentativas atingido; requer intervenção."
            )
            alert = service.enqueue(
                idempotency_key=(
                    f"repair-scheduled-capture-attempt:{row.id}"
                ),
                alert_type="scheduled_capture_repair",
                recipients=recipients,
                failed_items=[
                    {
                        "cnj": f"Escritório {row.office_id}",
                        "motivo": (
                            f"{row.last_error or 'Falha na captura de publicações.'}\n"
                            f"Tentativa: {row.attempt_n}. {retry_text}"
                        ),
                        "execution_id": row.automation_id,
                    }
                ],
                batch_source=(
                    "Reparo Automático · Busca Agendada de Publicações · "
                    f"Automação #{row.automation_id or '?'}"
                ),
                system_name="Flow",
                alert_context={
                    "automation_id": row.automation_id,
                    "attempt_ids": [int(row.id)],
                    "repaired_after_restart": True,
                },
                attempt_immediately=False,
                now=effective_now,
            )
            (
                db.query(PublicationFetchAttempt)
                .filter(
                    PublicationFetchAttempt.id == row.id,
                    PublicationFetchAttempt.alert_outbox_id.is_(None),
                )
                .update(
                    {
                        PublicationFetchAttempt.alert_outbox_id: alert.id,
                    },
                    synchronize_session=False,
                )
            )
            db.commit()
            created += 1
        except Exception:
            errors += 1
            db.rollback()
            logger.exception(
                "Falha ao reparar alerta da tentativa de captura #%s.",
                row.id,
            )

    for row in manual_rows:
        if row.l1_alert_outbox_id is not None:
            continue
        attempt_number = int(row.l1_alert_required_attempt)
        try:
            retry_text = (
                "Reconciliação automática prevista para "
                f"{row.l1_reconciliation_next_retry_at.isoformat()}."
                if row.l1_reconciliation_next_retry_at
                else "Reconciliação sem próxima data registrada."
            )
            alert = service.enqueue(
                idempotency_key=(
                    f"repair-manual-capture-search:{row.id}:"
                    f"attempt:{attempt_number}"
                ),
                alert_type="manual_capture_repair",
                recipients=recipients,
                failed_items=[
                    {
                        "cnj": f"Busca manual de publicações #{row.id}",
                        "motivo": (
                            f"{row.l1_reconciliation_last_error or row.error_message or 'Legal One GET /Updates falhou.'}\n"
                            f"{retry_text}"
                        ),
                        "execution_id": row.id,
                    }
                ],
                batch_source="Reparo Automático · Busca Manual de Publicações",
                system_name="Flow",
                alert_context={
                    "search_id": int(row.id),
                    "reconciliation_attempt": attempt_number,
                    "repaired_after_restart": True,
                },
                attempt_immediately=False,
                now=effective_now,
            )
            (
                db.query(PublicationSearch)
                .filter(
                    PublicationSearch.id == row.id,
                    PublicationSearch.l1_alert_required_attempt
                    == attempt_number,
                    PublicationSearch.l1_alert_outbox_id.is_(None),
                )
                .update(
                    {
                        PublicationSearch.l1_alert_outbox_id: alert.id,
                    },
                    synchronize_session=False,
                )
            )
            db.commit()
            created += 1
        except Exception:
            errors += 1
            db.rollback()
            logger.exception(
                "Falha ao reparar alerta da busca manual #%s.",
                row.id,
            )

    return AlertRepairResult(
        considered=len(scheduled_rows) + len(manual_rows),
        linked_existing=linked_existing,
        created=created,
        errors=errors,
    )
