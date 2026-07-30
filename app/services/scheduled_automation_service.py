"""
Service para gerenciar agendamentos automáticos.

Integra com APScheduler para executar jobs periodicamente.
"""

import hashlib
import json
import logging
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Iterator

from sqlalchemy.orm import Session
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.models.scheduled_automation import ScheduledAutomation, ScheduledAutomationRun
from app.models.publication_search import PublicationRecord
from app.models.publication_capture import (
    OfficePublicationCursor,
    PublicationFetchAttempt,
    ATTEMPT_STATUS_SUCCESS,
    ATTEMPT_STATUS_FAILED,
    ATTEMPT_STATUS_DEAD_LETTER,
    CURSOR_STATUS_OK,
    CURSOR_STATUS_FAILED,
    CURSOR_STATUS_DEAD_LETTER,
    RETRY_BACKOFF_MINUTES,
    MAX_CONSECUTIVE_FAILURES_BEFORE_DEAD_LETTER,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

AUTOMATION_LOCK_NAMESPACE = 4242
PUBLICATION_RETRY_SWEEP_LOCK_NAMESPACE = 4243
PUBLICATION_RETRY_SWEEP_LOCK_KEY = 1
PUBLICATION_RETRY_JOB_ID = "publication_capture_retry_sweep"


def _publication_alert_key(
    alert_type: str,
    *,
    automation_id: Optional[int],
    run_id: Optional[int],
    payload: Any,
) -> str:
    """Chave estável para não duplicar um mesmo aviso em reentrâncias."""
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]
    return (
        f"{alert_type}:automation:{automation_id or 0}:"
        f"run:{run_id or 0}:{digest}"
    )


@contextmanager
def _postgres_advisory_lock(namespace: int, key: int) -> Iterator[bool]:
    """Lock cross-worker; libera sempre, inclusive se o callback falhar."""
    from sqlalchemy import text as sql_text
    from app.db.session import engine

    connection = engine.connect()
    acquired = False
    try:
        acquired = bool(
            connection.execute(
                sql_text("SELECT pg_try_advisory_lock(:namespace, :key)"),
                {"namespace": namespace, "key": key},
            ).scalar()
        )
        yield acquired
    finally:
        if acquired:
            try:
                connection.execute(
                    sql_text("SELECT pg_advisory_unlock(:namespace, :key)"),
                    {"namespace": namespace, "key": key},
                )
            except Exception:
                logger.exception(
                    "Falha ao liberar advisory lock (%s, %s)",
                    namespace,
                    key,
                )
        connection.close()


def _overlap_hours() -> int:
    """Overlap defensivo (em horas) aplicado às rodagens recorrentes.

    Configurável via env `PUBLICATION_OVERLAP_HOURS`. Como o filtro usa
    creationDate (disponibilização no L1), 1h já é suficiente.
    """
    return settings.publication_overlap_hours


def _initial_lookback_days() -> int:
    """Dias que a primeira rodagem (sem cursor) olha para trás.

    Configurável via env `PUBLICATION_INITIAL_LOOKBACK_DAYS`.
    """
    return settings.publication_initial_lookback_days


# Aliases para retro-compatibilidade (usados em outros pontos do módulo).
DEFAULT_OVERLAP_HOURS = _overlap_hours()
INITIAL_LOOKBACK_DAYS = _initial_lookback_days()


class ScheduledAutomationService:
    """Manages scheduled automations and APScheduler integration."""

    def __init__(self, db: Session, scheduler: Optional[BackgroundScheduler] = None):
        self.db = db
        self.scheduler = scheduler

    def create_automation(
        self,
        name: str,
        office_ids: List[int],
        steps: List[str],
        cron_expression: Optional[str] = None,
        interval_minutes: Optional[int] = None,
        created_by: Optional[int] = None,
        initial_lookback_days: Optional[int] = None,
        overlap_hours: Optional[int] = None,
    ) -> ScheduledAutomation:
        """Create a new scheduled automation."""
        automation = ScheduledAutomation(
            name=name,
            office_ids=office_ids,
            steps=steps,
            cron_expression=cron_expression,
            interval_minutes=interval_minutes,
            created_by=created_by,
            is_enabled=True,
            initial_lookback_days=initial_lookback_days,
            overlap_hours=overlap_hours,
        )
        self.db.add(automation)
        self.db.commit()
        self.db.refresh(automation)

        # Register with scheduler if enabled
        if self.scheduler:
            self._register_job(automation)

        return automation

    def update_automation(
        self,
        automation_id: int,
        name: Optional[str] = None,
        office_ids: Optional[List[int]] = None,
        steps: Optional[List[str]] = None,
        cron_expression: Optional[str] = None,
        interval_minutes: Optional[int] = None,
        is_enabled: Optional[bool] = None,
        initial_lookback_days: Optional[int] = None,
        overlap_hours: Optional[int] = None,
    ) -> ScheduledAutomation:
        """Update a scheduled automation."""
        automation = self.db.query(ScheduledAutomation).filter(
            ScheduledAutomation.id == automation_id
        ).first()

        if not automation:
            raise ValueError(f"Automation {automation_id} not found")

        if name is not None:
            automation.name = name
        if office_ids is not None:
            automation.office_ids = office_ids
        if steps is not None:
            automation.steps = steps
        if cron_expression is not None:
            automation.cron_expression = cron_expression
        if interval_minutes is not None:
            automation.interval_minutes = interval_minutes
        if is_enabled is not None:
            automation.is_enabled = is_enabled
        if initial_lookback_days is not None:
            automation.initial_lookback_days = initial_lookback_days
        if overlap_hours is not None:
            automation.overlap_hours = overlap_hours

        self.db.commit()
        self.db.refresh(automation)

        # Update job in scheduler
        if self.scheduler:
            job_id = f"automation_{automation_id}"
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
            if automation.is_enabled:
                self._register_job(automation)

        return automation

    def delete_automation(self, automation_id: int) -> None:
        """Delete a scheduled automation."""
        automation = self.db.query(ScheduledAutomation).filter(
            ScheduledAutomation.id == automation_id
        ).first()

        if not automation:
            raise ValueError(f"Automation {automation_id} not found")

        # Remove from scheduler
        if self.scheduler:
            job_id = f"automation_{automation_id}"
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)

        self.db.delete(automation)
        self.db.commit()

    def get_automation(self, automation_id: int) -> Optional[ScheduledAutomation]:
        """Get a scheduled automation by ID."""
        return self.db.query(ScheduledAutomation).filter(
            ScheduledAutomation.id == automation_id
        ).first()

    def list_automations(self, is_enabled: Optional[bool] = None) -> List[ScheduledAutomation]:
        """List scheduled automations."""
        query = self.db.query(ScheduledAutomation)
        if is_enabled is not None:
            query = query.filter(ScheduledAutomation.is_enabled == is_enabled)
        return query.order_by(ScheduledAutomation.created_at.desc()).all()

    def get_runs(self, automation_id: int, limit: int = 50) -> List[ScheduledAutomationRun]:
        """Get runs for a specific automation."""
        return self.db.query(ScheduledAutomationRun).filter(
            ScheduledAutomationRun.automation_id == automation_id
        ).order_by(ScheduledAutomationRun.created_at.desc()).limit(limit).all()

    def _register_job(self, automation: ScheduledAutomation) -> None:
        """Register a job in APScheduler."""
        if not self.scheduler:
            logger.warning("Scheduler not configured, cannot register job for automation %d", automation.id)
            return

        job_id = f"automation_{automation.id}"

        try:
            # Determine trigger
            if automation.cron_expression:
                try:
                    from zoneinfo import ZoneInfo
                    br_tz = ZoneInfo("America/Sao_Paulo")
                except Exception:
                    br_tz = None
                trigger = CronTrigger.from_crontab(automation.cron_expression, timezone=br_tz) if br_tz else CronTrigger.from_crontab(automation.cron_expression)
            elif automation.interval_minutes:
                trigger = IntervalTrigger(minutes=automation.interval_minutes)
            else:
                logger.error("Automation %d has no schedule defined", automation.id)
                return

            # Register job. coalesce=True + max_instances=1 protegem contra
            # misfire storm: se o app rebootou e perdeu N execucoes do cron
            # (ex.: feriado prolongado ou deploy demorado), o APScheduler
            # combina os misfires em UMA execucao soh em vez de disparar N
            # em sequencia (cada uma classificando os mesmos 490 acumulados,
            # torrando tokens 4x). misfire_grace_time=3600 aceita execucoes
            # ate 1h atrasadas (alem disso descarta — o proximo cron pega).
            self.scheduler.add_job(
                self._execute_automation,
                trigger=trigger,
                id=job_id,
                args=[automation.id],
                name=automation.name,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=3600,
            )
            logger.info("Registered job %s for automation %s", job_id, automation.name)
        except Exception as e:
            logger.error("Failed to register job for automation %d: %s", automation.id, e)

    def _update_progress(
        self,
        run_id: int,
        phase: Optional[str] = None,
        current: Optional[int] = None,
        total: Optional[int] = None,
        message: Optional[str] = None,
    ) -> None:
        """Update progress fields on a run, committing immediately so the UI sees it."""
        try:
            run = self.db.query(ScheduledAutomationRun).filter(
                ScheduledAutomationRun.id == run_id
            ).first()
            if not run:
                return
            if phase is not None:
                run.progress_phase = phase
            if current is not None:
                run.progress_current = current
            if total is not None:
                run.progress_total = total
            if message is not None:
                run.progress_message = message
            run.progress_updated_at = datetime.now(timezone.utc)
            self.db.commit()
        except Exception:
            logger.exception("Falha ao atualizar progress de run %s", run_id)
            try:
                self.db.rollback()
            except Exception:
                pass

    @staticmethod
    def _failure_detail(
        office_id: int,
        error: str,
        attempt: PublicationFetchAttempt,
    ) -> Dict[str, Any]:
        return {
            "office_id": office_id,
            "error": (error or "Erro desconhecido")[:2000],
            "attempt_n": int(attempt.attempt_n or 1),
            "attempt_id": int(attempt.id),
            "attempt_status": attempt.status,
            "next_retry_at": (
                attempt.next_retry_at.isoformat()
                if attempt.next_retry_at is not None
                else None
            ),
        }

    def _record_automation_pull_failure(
        self,
        automation: ScheduledAutomation,
        error: str,
    ) -> List[Dict[str, Any]]:
        """Cria estado de retry mesmo se a etapa falhar antes do loop normal."""
        now = datetime.now(timezone.utc)
        failures: List[Dict[str, Any]] = []
        for office_id in automation.office_ids or []:
            try:
                cursor = self._get_or_create_cursor(office_id)
                window_from, window_to = self._compute_window(
                    cursor,
                    now,
                    initial_lookback_days=automation.initial_lookback_days,
                    overlap_hours=automation.overlap_hours,
                )
                attempt = self._record_attempt_failure(
                    office_id,
                    window_from,
                    window_to,
                    error,
                    automation.id,
                )
                failures.append(
                    self._failure_detail(office_id, error, attempt)
                )
            except Exception:
                logger.exception(
                    "Não foi possível persistir retry do escritório %s.",
                    office_id,
                )
                try:
                    self.db.rollback()
                except Exception:
                    pass
        return failures

    def _alert_publication_capture_failures(
        self,
        automation_id: Optional[int],
        failures: List[Dict[str, Any]],
        *,
        run_id: Optional[int] = None,
        is_retry: bool = False,
    ) -> bool:
        """Envia um único e-mail por rodada, com todos os escritórios afetados."""
        if not failures or automation_id is None:
            return False

        try:
            from app.models.legal_one import LegalOneOffice
            from app.models.publication_capture import (
                PUBLICATION_ALERT_STATUS_SENT,
            )
            from app.services.publication_capture_alert_service import (
                PublicationCaptureAlertService,
            )

            automation = None
            if automation_id is not None:
                automation = (
                    self.db.query(ScheduledAutomation)
                    .filter(ScheduledAutomation.id == automation_id)
                    .first()
                )

            office_ids = sorted(
                {
                    int(item["office_id"])
                    for item in failures
                    if item.get("office_id") is not None
                }
            )
            office_rows = (
                self.db.query(
                    LegalOneOffice.id,
                    LegalOneOffice.path,
                    LegalOneOffice.name,
                )
                .filter(LegalOneOffice.id.in_(office_ids))
                .all()
                if office_ids
                else []
            )
            office_labels = {
                row[0]: (row[1] or row[2] or f"Escritório {row[0]}")
                for row in office_rows
            }

            failed_items = []
            for item in failures:
                office_id = item.get("office_id")
                retry_at = item.get("next_retry_at")
                retry_text = (
                    f"Nova tentativa automática prevista para {retry_at}."
                    if retry_at
                    else "Limite de tentativas atingido; requer intervenção."
                )
                failed_items.append(
                    {
                        "cnj": office_labels.get(
                            office_id,
                            f"Escritório {office_id or '?'}",
                        ),
                        "motivo": (
                            f"{item.get('error') or 'Erro desconhecido'}\n"
                            f"Tentativa: {item.get('attempt_n', '?')}. "
                            f"{retry_text}"
                        ),
                        "execution_id": run_id,
                    }
                )

            recipients = (
                settings.publication_capture_alert_email
                or settings.classificacao_alert_email
                or settings.mail_to
                or settings.email_to
            )
            if not recipients:
                logger.error(
                    "Falha na captura de publicações sem destinatário de alerta "
                    "(PUBLICATION_CAPTURE_ALERT_EMAIL/CLASSIFICACAO_ALERT_EMAIL/MAIL_TO)."
                )
                return False

            automation_label = (
                automation.name
                if automation is not None
                else f"Automação #{automation_id or '?'}"
            )
            source = (
                f"Retry da Busca de Publicações · {automation_label}"
                if is_retry
                else f"Busca Agendada de Publicações · {automation_label}"
            )
            attempt_ids = sorted(
                {
                    int(item["attempt_id"])
                    for item in failures
                    if item.get("attempt_id") is not None
                }
            )
            alert = PublicationCaptureAlertService(self.db).enqueue(
                idempotency_key=_publication_alert_key(
                    "scheduled_capture_retry_failure"
                    if is_retry
                    else "scheduled_capture_failure",
                    automation_id=automation_id,
                    run_id=run_id,
                    payload=failures,
                ),
                alert_type=(
                    "scheduled_capture_retry_failure"
                    if is_retry
                    else "scheduled_capture_failure"
                ),
                failed_items=failed_items,
                batch_source=source,
                recipients=recipients,
                system_name="Flow",
                alert_context={
                    "automation_id": automation_id,
                    "run_id": run_id,
                    "is_retry": is_retry,
                    "failure_count": len(failures),
                    "attempt_ids": attempt_ids,
                },
            )
            if attempt_ids:
                (
                    self.db.query(PublicationFetchAttempt)
                    .filter(
                        PublicationFetchAttempt.id.in_(attempt_ids),
                        PublicationFetchAttempt.alert_required.is_(True),
                    )
                    .update(
                        {
                            PublicationFetchAttempt.alert_outbox_id: alert.id,
                        },
                        synchronize_session=False,
                    )
                )
                self.db.commit()
            sent = alert.status == PUBLICATION_ALERT_STATUS_SENT
            if sent:
                logger.info(
                    "Alerta da captura enviado: automation=%s run=%s falhas=%s retry=%s",
                    automation_id,
                    run_id,
                    len(failures),
                    is_retry,
                )
            else:
                logger.warning(
                    "Alerta da captura persistido para retry de e-mail: "
                    "automation=%s run=%s alert=%s status=%s",
                    automation_id,
                    run_id,
                    alert.id,
                    alert.status,
                )
            return bool(sent)
        except Exception:
            logger.exception(
                "Falha ao enviar alerta da captura de publicações "
                "(automation=%s run=%s).",
                automation_id,
                run_id,
            )
            return False

    def _alert_publication_capture_degraded(
        self,
        automation_id: Optional[int],
        degraded: List[Dict[str, Any]],
        fallback_metadata: Dict[str, Any],
        *,
        run_id: Optional[int] = None,
        is_retry: bool = False,
    ) -> bool:
        """Avisa o 502 mesmo quando o DJEN recuperou a captura."""
        if not degraded or automation_id is None:
            return False
        try:
            from app.models.legal_one import LegalOneOffice
            from app.models.publication_capture import (
                PUBLICATION_ALERT_STATUS_SENT,
            )
            from app.services.publication_capture_alert_service import (
                PublicationCaptureAlertService,
            )

            automation = (
                self.db.query(ScheduledAutomation)
                .filter(ScheduledAutomation.id == automation_id)
                .first()
            )
            office_ids = sorted(
                {
                    int(item["office_id"])
                    for item in degraded
                    if item.get("office_id") is not None
                }
            )
            rows = (
                self.db.query(
                    LegalOneOffice.id,
                    LegalOneOffice.path,
                    LegalOneOffice.name,
                )
                .filter(LegalOneOffice.id.in_(office_ids))
                .all()
            )
            labels = {
                row[0]: (row[1] or row[2] or f"Escritório {row[0]}")
                for row in rows
            }
            jurisdictions = fallback_metadata.get("jurisdictions") or {}
            report = (
                fallback_metadata.get("report_title")
                or fallback_metadata.get("report_id")
                or fallback_metadata.get("source")
                or "snapshot local"
            )
            items = []
            for item in degraded:
                retry_at = item.get("next_retry_at")
                items.append(
                    {
                        "cnj": labels.get(
                            item.get("office_id"),
                            f"Escritório {item.get('office_id') or '?'}",
                        ),
                        "motivo": (
                            "Legal One GET /Updates retornou HTTP 502. "
                            "A contingência suplementar DJEN foi executada.\n"
                            f"Publicações DJEN da rodada: {fallback_metadata.get('publications', 0)}. "
                            f"Relatório: {report}. Tribunais: "
                            f"{jurisdictions or 'nenhum resultado no período'}.\n"
                            f"{fallback_metadata.get('coverage_note') or 'Cobertura parcial por OAB; reconciliação Legal One obrigatória.'} "
                            "A reconciliação com o Legal One continua agendada"
                            + (
                                f" para {retry_at}."
                                if retry_at
                                else " e atingiu o limite de tentativas."
                            )
                        ),
                        "execution_id": run_id,
                    }
                )

            recipients = (
                settings.publication_capture_alert_email
                or settings.classificacao_alert_email
                or settings.mail_to
                or settings.email_to
            )
            if not recipients:
                return False
            automation_label = (
                automation.name
                if automation is not None
                else f"Automação #{automation_id}"
            )
            source = (
                "Contingência DJEN no Retry"
                if is_retry
                else "Contingência DJEN na Busca Agendada"
            )
            attempt_ids = sorted(
                {
                    int(item["attempt_id"])
                    for item in degraded
                    if item.get("attempt_id") is not None
                }
            )
            alert = PublicationCaptureAlertService(self.db).enqueue(
                idempotency_key=_publication_alert_key(
                    "scheduled_djen_retry_degraded"
                    if is_retry
                    else "scheduled_djen_degraded",
                    automation_id=automation_id,
                    run_id=run_id,
                    payload={
                        "degraded": degraded,
                        "fallback_metadata": fallback_metadata,
                    },
                ),
                alert_type=(
                    "scheduled_djen_retry_degraded"
                    if is_retry
                    else "scheduled_djen_degraded"
                ),
                failed_items=items,
                batch_source=f"{source} · {automation_label}",
                recipients=recipients,
                system_name="Flow",
                alert_context={
                    "automation_id": automation_id,
                    "run_id": run_id,
                    "is_retry": is_retry,
                    "fallback_metadata": fallback_metadata,
                    "attempt_ids": attempt_ids,
                },
            )
            if attempt_ids:
                (
                    self.db.query(PublicationFetchAttempt)
                    .filter(
                        PublicationFetchAttempt.id.in_(attempt_ids),
                        PublicationFetchAttempt.alert_required.is_(True),
                    )
                    .update(
                        {
                            PublicationFetchAttempt.alert_outbox_id: alert.id,
                        },
                        synchronize_session=False,
                    )
                )
                self.db.commit()
            return alert.status == PUBLICATION_ALERT_STATUS_SENT
        except Exception:
            logger.exception(
                "Falha ao enviar alerta da contingência DJEN "
                "(automation=%s run=%s).",
                automation_id,
                run_id,
            )
            return False

    def _execute_automation(self, automation_id: int) -> None:
        """Execute an automation, protegido por advisory lock no Postgres.

        Garante que apenas UMA instancia rode por vez mesmo quando multiplos
        workers do uvicorn (ou replicas do container) tem cada um seu proprio
        APScheduler in-memory disparando o mesmo cron em paralelo. Workers
        derrotados na disputa do lock logam e abortam imediatamente em ~5ms,
        sem criar ScheduledAutomationRun.

        Causa raiz do bug: scheduler em app/core/scheduler.py:11 e instanciado
        a nivel de modulo, entao com UVICORN_WORKERS>1 cada processo carrega
        um BackgroundScheduler independente (MemoryJobStore default) e
        dispara o mesmo cron sem coordenacao. max_instances=1 do APScheduler
        so protege dentro de UM processo, nao entre processos.
        """
        with _postgres_advisory_lock(
            AUTOMATION_LOCK_NAMESPACE,
            automation_id,
        ) as acquired:
            if not acquired:
                logger.info(
                    "Automation %d: outro worker/container ja esta executando "
                    "esta automation - abortando esta instancia.",
                    automation_id,
                )
                return
            self._execute_automation_inner(automation_id)

    def _execute_automation_inner(self, automation_id: int) -> None:
        """Execute an automation (called by scheduler)."""
        logger.info("Executing automation %d", automation_id)

        run = ScheduledAutomationRun(
            automation_id=automation_id,
            status="running",
            progress_phase="starting",
            progress_message="Iniciando execução...",
            progress_updated_at=datetime.now(timezone.utc),
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        run_id = run.id

        steps_executed = []
        try:
            automation = self.db.query(ScheduledAutomation).filter(
                ScheduledAutomation.id == automation_id
            ).first()

            if not automation:
                raise ValueError(f"Automation {automation_id} not found")

            total_steps = len(automation.steps)

            # Execute steps
            for step_idx, step in enumerate(automation.steps, start=1):
                try:
                    if step == "pull_publications":
                        self._update_progress(
                            run_id,
                            phase="pull_publications",
                            current=0,
                            total=len(automation.office_ids),
                            message=f"Etapa {step_idx}/{total_steps}: Buscando publicações",
                        )
                        result = self._execute_pull_publications(
                            automation.office_ids,
                            automation_id=automation.id,
                            initial_lookback_days=automation.initial_lookback_days,
                            overlap_hours=automation.overlap_hours,
                            run_id=run_id,
                        )
                        failed_offices = result.get("offices_failed") or []
                        degraded_offices = result.get("offices_degraded") or []
                        steps_executed.append({
                            "step": "pull_publications",
                            "status": (
                                "failed"
                                if failed_offices
                                else ("warning" if degraded_offices else "success")
                            ),
                            "records_found": result.get("records_found", 0),
                            "offices_ok": result.get("offices_ok", []),
                            "offices_failed": failed_offices,
                            "offices_degraded": degraded_offices,
                            "offices_skipped": result.get("offices_skipped", []),
                            "failures": result.get("failures", []),
                            "fallback_metadata": result.get("fallback_metadata"),
                            "alert_sent": bool(result.get("alert_sent")),
                        })
                    elif step == "classify":
                        # Skip se o step pull_publications anterior (no mesmo
                        # run) retornou 0 novos. Evita reclassificar os mesmos
                        # registros pendentes acumulados em cada cron diario
                        # quando nao houve nada novo no dia — economizou
                        # tokens da Anthropic Batch API. Caso de borda: se o
                        # classify roda sozinho (sem pull antes), o
                        # `pull_records_found` fica None e a logica permite
                        # classificar (intencional: operador pode acionar
                        # classify isolado via futuro endpoint manual).
                        pull_records_found = next(
                            (
                                int(s.get("records_found", 0))
                                for s in steps_executed
                                if s.get("step") == "pull_publications"
                            ),
                            None,
                        )
                        if pull_records_found == 0:
                            pull_step = next(
                                (
                                    item
                                    for item in steps_executed
                                    if item.get("step") == "pull_publications"
                                ),
                                {},
                            )
                            skip_reason = (
                                "pull_failed"
                                if pull_step.get("status") == "failed"
                                else "no_new_records"
                            )
                            logger.info(
                                "Classify pulado: pull_publications retornou 0 novos. "
                                "Sem nada pra classificar nesse run.",
                            )
                            steps_executed.append({
                                "step": "classify",
                                "status": "skipped",
                                "records_classified": 0,
                                "reason": skip_reason,
                            })
                        else:
                            self._update_progress(
                                run_id,
                                phase="classify",
                                current=0,
                                total=None,
                                message=f"Etapa {step_idx}/{total_steps}: Classificando publicações",
                            )
                            result = self._execute_classify(automation.office_ids, run_id=run_id)
                            steps_executed.append({
                                "step": "classify",
                                "status": "success",
                                "records_classified": result.get("records_classified", 0),
                            })
                    elif step == "treat_publications":
                        self._update_progress(
                            run_id,
                            phase="treat_publications:start",
                            current=0,
                            total=None,
                            message=f"Etapa {step_idx}/{total_steps}: Tratando publicações no Legal One",
                        )
                        result = self._execute_treat_publications(
                            automation.office_ids,
                            automation_id=automation.id,
                            run_id=run_id,
                        )
                        steps_executed.append({
                            "step": "treat_publications",
                            "status": "warning" if result.get("failed_count", 0) else "success",
                            "treated_count": result.get("success_count", 0),
                            "failed_count": result.get("failed_count", 0),
                            "run_id": result.get("run_id"),
                        })
                    else:
                        steps_executed.append({
                            "step": step,
                            "status": "skipped",
                            "reason": f"Unknown step: {step}",
                        })
                except Exception as e:
                    logger.error("Step %s failed: %s", step, e)
                    alert_sent = False
                    pull_failures: List[Dict[str, Any]] = []
                    if step == "pull_publications":
                        pull_failures = self._record_automation_pull_failure(
                            automation,
                            str(e),
                        )
                        alert_sent = self._alert_publication_capture_failures(
                            automation.id,
                            pull_failures
                            or [
                                {
                                    "office_id": None,
                                    "error": str(e),
                                    "attempt_n": "?",
                                    "next_retry_at": None,
                                }
                            ],
                            run_id=run_id,
                        )
                    steps_executed.append({
                        "step": step,
                        "status": "failed",
                        "error": str(e),
                        **(
                            {
                                "alert_sent": alert_sent,
                                "offices_failed": [
                                    item.get("office_id")
                                    for item in pull_failures
                                    if item.get("office_id") is not None
                                ],
                                "failures": pull_failures,
                            }
                            if step == "pull_publications"
                            else {}
                        ),
                    })

            # Update run and automation
            failed_steps = [
                item
                for item in steps_executed
                if item.get("status") == "failed"
            ]
            warning_steps = [
                item
                for item in steps_executed
                if item.get("status") == "warning"
            ]
            failure_summary = "; ".join(
                (
                    item.get("error")
                    or (
                        f"{len(item.get('offices_failed') or [])} "
                        "escritório(s) falharam na busca"
                    )
                )
                for item in failed_steps
            )
            warning_summary = "; ".join(
                (
                    item.get("error")
                    or (
                        f"{len(item.get('offices_degraded') or [])} "
                        "escritório(s) cobertos pelo DJEN, com reconciliação "
                        "do Legal One pendente"
                    )
                )
                for item in warning_steps
            )

            run.status = (
                "failed"
                if failed_steps
                else ("warning" if warning_steps else "success")
            )
            run.steps_executed = steps_executed
            run.error_message = failure_summary or warning_summary or None
            run.finished_at = datetime.now(timezone.utc)
            run.progress_phase = (
                "failed"
                if failed_steps
                else ("warning" if warning_steps else "done")
            )
            run.progress_message = (
                "Execução concluída com falha"
                if failed_steps
                else (
                    "Execução concluída com contingência DJEN"
                    if warning_steps
                    else "Execução concluída"
                )
            )
            run.progress_updated_at = datetime.now(timezone.utc)

            automation.last_run_at = datetime.now(timezone.utc)
            automation.last_status = run.status
            automation.last_error = failure_summary or warning_summary or None

            if failed_steps:
                logger.error(
                    "Automation %d concluída com falha: %s",
                    automation_id,
                    failure_summary,
                )
            elif warning_steps:
                logger.warning(
                    "Automation %d concluída com contingência: %s",
                    automation_id,
                    warning_summary,
                )
            else:
                logger.info("Automation %d completed successfully", automation_id)
        except Exception as e:
            logger.error("Automation %d failed: %s", automation_id, e)
            run.status = "failed"
            run.error_message = str(e)
            run.finished_at = datetime.now(timezone.utc)

            automation = self.db.query(ScheduledAutomation).filter(
                ScheduledAutomation.id == automation_id
            ).first()
            if automation:
                automation.last_run_at = datetime.now(timezone.utc)
                automation.last_status = "failed"
                automation.last_error = str(e)

        finally:
            self.db.commit()

    # ──────────────────────────────────────────────
    # Cursor + retry helpers
    # ──────────────────────────────────────────────

    def _collect_due_publication_retries(
        self,
        now: Optional[datetime] = None,
    ) -> Dict[int, List[int]]:
        """Agrupa escritórios com a tentativa mais recente vencida."""
        now = now or datetime.now(timezone.utc)
        groups: Dict[int, List[int]] = defaultdict(list)
        automation_cache: Dict[int, Optional[ScheduledAutomation]] = {}

        cursors = (
            self.db.query(OfficePublicationCursor)
            .filter(OfficePublicationCursor.last_status == CURSOR_STATUS_FAILED)
            .all()
        )
        for cursor in cursors:
            latest = (
                self.db.query(PublicationFetchAttempt)
                .filter(PublicationFetchAttempt.office_id == cursor.office_id)
                .order_by(PublicationFetchAttempt.id.desc())
                .first()
            )
            if (
                latest is None
                or latest.status != ATTEMPT_STATUS_FAILED
                or latest.next_retry_at is None
                or latest.automation_id is None
            ):
                continue

            retry_at = latest.next_retry_at
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            if retry_at > now:
                continue

            automation_id = int(latest.automation_id)
            if automation_id not in automation_cache:
                automation_cache[automation_id] = (
                    self.db.query(ScheduledAutomation)
                    .filter(ScheduledAutomation.id == automation_id)
                    .first()
                )
            automation = automation_cache[automation_id]
            if (
                automation is None
                or not automation.is_enabled
                or "pull_publications" not in (automation.steps or [])
            ):
                continue
            groups[automation_id].append(cursor.office_id)

        return {
            automation_id: sorted(set(office_ids))
            for automation_id, office_ids in groups.items()
        }

    def run_due_publication_retries(self) -> int:
        """Executa retries persistidos; seguro com vários workers da API."""
        if not settings.publication_capture_retry_enabled:
            return 0

        with _postgres_advisory_lock(
            PUBLICATION_RETRY_SWEEP_LOCK_NAMESPACE,
            PUBLICATION_RETRY_SWEEP_LOCK_KEY,
        ) as acquired:
            if not acquired:
                logger.debug("Outro worker já está processando retries de publicações.")
                return 0

            executed = 0
            groups = self._collect_due_publication_retries()
            for automation_id, office_ids in groups.items():
                has_running = (
                    self.db.query(ScheduledAutomationRun.id)
                    .filter(
                        ScheduledAutomationRun.automation_id == automation_id,
                        ScheduledAutomationRun.status == "running",
                    )
                    .first()
                    is not None
                )
                if has_running:
                    logger.info(
                        "Retry da automation %s adiado: execução principal ainda ativa.",
                        automation_id,
                    )
                    continue
                if self._execute_publication_retry(automation_id, office_ids):
                    executed += 1
            return executed

    def _execute_publication_retry(
        self,
        automation_id: int,
        office_ids: List[int],
    ) -> bool:
        """Executa somente os escritórios vencidos, sob o mesmo lock da rotina."""
        with _postgres_advisory_lock(
            AUTOMATION_LOCK_NAMESPACE,
            automation_id,
        ) as acquired:
            if not acquired:
                logger.info(
                    "Retry da automation %s adiado: lock ocupado.",
                    automation_id,
                )
                return False
            self._execute_publication_retry_inner(automation_id, office_ids)
            return True

    def _execute_publication_retry_inner(
        self,
        automation_id: int,
        office_ids: List[int],
    ) -> None:
        automation = (
            self.db.query(ScheduledAutomation)
            .filter(ScheduledAutomation.id == automation_id)
            .first()
        )
        if automation is None or not automation.is_enabled or not office_ids:
            return

        run = ScheduledAutomationRun(
            automation_id=automation_id,
            status="running",
            progress_phase="pull_publications_retry",
            progress_message=(
                f"Retry automático da busca para {len(office_ids)} escritório(s)"
            ),
            progress_updated_at=datetime.now(timezone.utc),
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

        steps_executed: List[Dict[str, Any]] = []
        try:
            result = self._execute_pull_publications(
                office_ids,
                automation_id=automation.id,
                initial_lookback_days=automation.initial_lookback_days,
                overlap_hours=automation.overlap_hours,
                run_id=run.id,
                is_retry=True,
            )
            failed_offices = result.get("offices_failed") or []
            degraded_offices = result.get("offices_degraded") or []
            steps_executed.append(
                {
                    "step": "pull_publications",
                    "retry": True,
                    "status": (
                        "failed"
                        if failed_offices
                        else ("warning" if degraded_offices else "success")
                    ),
                    "records_found": result.get("records_found", 0),
                    "offices_ok": result.get("offices_ok", []),
                    "offices_failed": failed_offices,
                    "offices_degraded": degraded_offices,
                    "offices_skipped": result.get("offices_skipped", []),
                    "failures": result.get("failures", []),
                    "fallback_metadata": result.get("fallback_metadata"),
                    "alert_sent": bool(result.get("alert_sent")),
                }
            )

            if (
                int(result.get("records_found", 0) or 0) > 0
                and "classify" in (automation.steps or [])
            ):
                try:
                    classified = self._execute_classify(office_ids, run_id=run.id)
                    steps_executed.append(
                        {
                            "step": "classify",
                            "retry": True,
                            "status": "success",
                            "records_classified": classified.get(
                                "records_classified",
                                0,
                            ),
                        }
                    )
                except Exception as exc:
                    logger.exception(
                        "Classificação do retry da automation %s falhou.",
                        automation_id,
                    )
                    steps_executed.append(
                        {
                            "step": "classify",
                            "retry": True,
                            "status": "failed",
                            "error": str(exc),
                        }
                    )

            failed_steps = [
                item
                for item in steps_executed
                if item.get("status") == "failed"
            ]
            warning_steps = [
                item
                for item in steps_executed
                if item.get("status") == "warning"
            ]
            failure_summary = "; ".join(
                (
                    item.get("error")
                    or (
                        f"{len(item.get('offices_failed') or [])} "
                        "escritório(s) falharam no retry"
                    )
                )
                for item in failed_steps
            )
            warning_summary = "; ".join(
                (
                    item.get("error")
                    or (
                        f"{len(item.get('offices_degraded') or [])} "
                        "escritório(s) cobertos pelo DJEN, com reconciliação "
                        "do Legal One pendente"
                    )
                )
                for item in warning_steps
            )
            run.status = (
                "failed"
                if failed_steps
                else ("warning" if warning_steps else "success")
            )
            run.error_message = failure_summary or warning_summary or None
            run.steps_executed = steps_executed
            run.finished_at = datetime.now(timezone.utc)
            run.progress_phase = (
                "failed"
                if failed_steps
                else ("warning" if warning_steps else "done")
            )
            run.progress_message = (
                "Retry automático falhou"
                if failed_steps
                else (
                    "Retry concluído com contingência DJEN"
                    if warning_steps
                    else "Retry automático concluído"
                )
            )
            run.progress_updated_at = datetime.now(timezone.utc)

            automation.last_run_at = datetime.now(timezone.utc)
            automation.last_status = run.status
            automation.last_error = failure_summary or warning_summary or None
        except Exception as exc:
            logger.exception(
                "Erro inesperado no retry da automation %s.",
                automation_id,
            )
            alert_sent = self._alert_publication_capture_failures(
                automation_id,
                [
                    {
                        "office_id": None,
                        "error": str(exc),
                        "attempt_n": "?",
                        "next_retry_at": None,
                    }
                ],
                run_id=run.id,
                is_retry=True,
            )
            run.status = "failed"
            run.error_message = str(exc)
            run.steps_executed = [
                {
                    "step": "pull_publications",
                    "retry": True,
                    "status": "failed",
                    "error": str(exc),
                    "alert_sent": alert_sent,
                }
            ]
            run.finished_at = datetime.now(timezone.utc)
            run.progress_phase = "failed"
            run.progress_message = "Retry automático falhou"
            run.progress_updated_at = datetime.now(timezone.utc)
            automation.last_run_at = datetime.now(timezone.utc)
            automation.last_status = "failed"
            automation.last_error = str(exc)
        finally:
            self.db.commit()

    def _get_or_create_cursor(self, office_id: int) -> OfficePublicationCursor:
        cursor = self.db.query(OfficePublicationCursor).filter(
            OfficePublicationCursor.office_id == office_id
        ).first()
        if cursor is None:
            cursor = OfficePublicationCursor(office_id=office_id, consecutive_failures=0)
            self.db.add(cursor)
            self.db.commit()
            self.db.refresh(cursor)
        return cursor

    def _compute_window(
        self,
        cursor: OfficePublicationCursor,
        now: datetime,
        initial_lookback_days: Optional[int] = None,
        overlap_hours: Optional[int] = None,
    ) -> tuple[datetime, datetime]:
        """Retorna (date_from, date_to) aplicando overlap defensivo.

        A janela é expressa no eixo `creationDate` (data em que o Legal One
        disponibilizou a publicação).

        Se `initial_lookback_days` / `overlap_hours` forem passados (vindos
        da configuração da automação), usa esses valores. Caso contrário,
        cai nos defaults globais.
        """
        effective_overlap = overlap_hours if overlap_hours is not None else _overlap_hours()
        effective_lookback = initial_lookback_days if initial_lookback_days is not None else _initial_lookback_days()

        overlap = timedelta(hours=effective_overlap)
        if cursor.last_successful_date is None:
            date_from = now - timedelta(days=effective_lookback)
        else:
            date_from = cursor.last_successful_date - overlap
        return date_from, now

    def _should_skip_office(self, office_id: int, now: datetime) -> bool:
        """Pula somente quando a tentativa MAIS RECENTE ainda está em backoff."""
        cursor = (
            self.db.query(OfficePublicationCursor)
            .filter(OfficePublicationCursor.office_id == office_id)
            .first()
        )
        if cursor is None or cursor.last_status != CURSOR_STATUS_FAILED:
            return False

        latest = (
            self.db.query(PublicationFetchAttempt)
            .filter(PublicationFetchAttempt.office_id == office_id)
            .order_by(PublicationFetchAttempt.id.desc())
            .first()
        )
        if (
            latest is None
            or latest.status != ATTEMPT_STATUS_FAILED
            or latest.next_retry_at is None
        ):
            return False
        retry_at = latest.next_retry_at
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return retry_at > now

    def _record_attempt_success(
        self,
        office_id: int,
        window_from: datetime,
        window_to: datetime,
        records_found: int,
        automation_id: Optional[int],
    ) -> None:
        attempt = PublicationFetchAttempt(
            office_id=office_id,
            window_from=window_from,
            window_to=window_to,
            status=ATTEMPT_STATUS_SUCCESS,
            attempt_n=1,
            records_found=records_found,
            automation_id=automation_id,
        )
        self.db.add(attempt)

        cursor = self._get_or_create_cursor(office_id)
        cursor.last_successful_date = window_to
        cursor.last_run_at = datetime.now(timezone.utc)
        cursor.last_status = CURSOR_STATUS_OK
        cursor.last_error = None
        cursor.consecutive_failures = 0
        cursor.djen_reconciliation_pending = False
        cursor.djen_covered_from = None
        cursor.djen_covered_to = None
        cursor.djen_fallback_at = None
        cursor.djen_coverage_complete = False
        cursor.djen_coverage_metadata = None
        self.db.commit()

    def _record_attempt_failure(
        self,
        office_id: int,
        window_from: datetime,
        window_to: datetime,
        error: str,
        automation_id: Optional[int],
        *,
        djen_covered: bool = False,
        djen_coverage_complete: bool = False,
        djen_coverage_metadata: Optional[Dict[str, Any]] = None,
    ) -> PublicationFetchAttempt:
        cursor = self._get_or_create_cursor(office_id)
        cursor.consecutive_failures = (cursor.consecutive_failures or 0) + 1
        cursor.last_run_at = datetime.now(timezone.utc)
        cursor.last_error = error[:2000]
        if djen_covered:
            cursor.djen_reconciliation_pending = True
            cursor.djen_covered_from = window_from
            cursor.djen_covered_to = window_to
            cursor.djen_fallback_at = datetime.now(timezone.utc)
            cursor.djen_coverage_complete = bool(djen_coverage_complete)
            cursor.djen_coverage_metadata = (
                dict(djen_coverage_metadata)
                if djen_coverage_metadata
                else None
            )

        attempt_n = cursor.consecutive_failures
        has_djen_reconciliation = bool(
            cursor.djen_reconciliation_pending or djen_covered
        )
        if (
            attempt_n >= MAX_CONSECUTIVE_FAILURES_BEFORE_DEAD_LETTER
            and not has_djen_reconciliation
        ):
            status = ATTEMPT_STATUS_DEAD_LETTER
            cursor.last_status = CURSOR_STATUS_DEAD_LETTER
            next_retry = None
            logger.error(
                "Office %s entrou em dead_letter após %d falhas consecutivas",
                office_id, attempt_n,
            )
        else:
            status = ATTEMPT_STATUS_FAILED
            cursor.last_status = CURSOR_STATUS_FAILED
            backoff_min = RETRY_BACKOFF_MINUTES[min(attempt_n - 1, len(RETRY_BACKOFF_MINUTES) - 1)]
            next_retry = datetime.now(timezone.utc) + timedelta(minutes=backoff_min)

        attempt = PublicationFetchAttempt(
            office_id=office_id,
            window_from=window_from,
            window_to=window_to,
            status=status,
            attempt_n=attempt_n,
            next_retry_at=next_retry,
            last_error=error[:2000],
            automation_id=automation_id,
            alert_required=True,
        )
        self.db.add(attempt)
        self.db.commit()
        self.db.refresh(attempt)
        return attempt

    def _execute_pull_publications(
        self,
        office_ids: List[int],
        automation_id: Optional[int] = None,
        initial_lookback_days: Optional[int] = None,
        overlap_hours: Optional[int] = None,
        run_id: Optional[int] = None,
        is_retry: bool = False,
    ) -> Dict[str, Any]:
        """
        Executa pull_publications por escritório, usando watermark + overlap defensivo
        e registrando retry/dead-letter em publication_fetch_attempt.
        """
        from app.services.legal_one_client import LegalOneApiClient
        from app.services.publication_search_service import PublicationSearchService

        now = datetime.now(timezone.utc)
        total_found = 0
        skipped: List[int] = []
        failed: List[int] = []
        ok: List[int] = []
        degraded: List[int] = []
        failure_details: List[Dict[str, Any]] = []
        degraded_details: List[Dict[str, Any]] = []
        fallback_metadata: Dict[str, Any] = {}
        alert_sent = False

        # Cliente L1 + service
        client = LegalOneApiClient()
        search_service = PublicationSearchService(self.db, client)

        # Mapeia office_id interno → external_id (id real no Legal One).
        # O frontend manda sempre o id interno; o filtro de publicações
        # precisa bater com `responsibleOfficeId` dos processos, que é
        # o external_id.
        from app.models.legal_one import LegalOneOffice as _LOOffice
        _rows = (
            self.db.query(_LOOffice.id, _LOOffice.external_id)
            .filter(_LOOffice.id.in_(office_ids))
            .all()
        )
        internal_to_external = {r[0]: r[1] for r in _rows}
        logger.info(
            "Mapeando office_ids internos → external_id (L1): %s",
            internal_to_external,
        )

        # Pré-computa janela e backoff por office, separando os ativos
        # dos pulados (em backoff). Se TODOS estão pulados, sai sem
        # tocar no L1. Comum aos dois modos (batch e legado).
        active: List[tuple[int, datetime, datetime]] = []
        for office_id in office_ids:
            if self._should_skip_office(office_id, now):
                logger.info("Office %s pulado (em backoff).", office_id)
                skipped.append(office_id)
                continue
            cursor = self._get_or_create_cursor(office_id)
            df, dt = self._compute_window(
                cursor,
                now,
                initial_lookback_days=initial_lookback_days,
                overlap_hours=overlap_hours,
            )
            active.append((office_id, df, dt))

        if not active:
            logger.info("Nenhum escritório ativo nesta rodada (todos em backoff/pulados).")
            return {
                "records_found": 0,
                "offices_ok": ok,
                "offices_failed": failed,
                "offices_degraded": degraded,
                "offices_skipped": skipped,
                "failures": failure_details,
                "fallback_metadata": fallback_metadata,
                "alert_sent": alert_sent,
            }

        allow_djen_fallback = True
        if is_retry:
            active_ids = [office_id for office_id, _, _ in active]
            reconciliation_state = {
                row[0]: (bool(row[1]), bool(row[2]))
                for row in self.db.query(
                    OfficePublicationCursor.office_id,
                    OfficePublicationCursor.djen_reconciliation_pending,
                    OfficePublicationCursor.djen_coverage_complete,
                )
                .filter(OfficePublicationCursor.office_id.in_(active_ids))
                .all()
            }
            # Cadernos completos deixam o retry exclusivo para o /Updates.
            # Cobertura parcial (comum antes das 03h) refaz o DJEN até fechar.
            allow_djen_fallback = any(
                not all(reconciliation_state.get(office_id, (False, False)))
                for office_id in active_ids
            )

        # ── BATCH MODE: 1 fetch L1 + fan-out ──────────────────────────
        # Substitui o loop legado de "1 fetch por escritório" (saturava
        # rate limit do L1 em rodadas multi-banco). O L1 devolve TODAS as
        # publicações do período independente de filtro de escritório
        # (filtro fino é client-side em Python), então em vez de paginar
        # N vezes a mesma janela, paginamos 1 vez a UNIÃO das janelas
        # (cobre cursores divergentes) e cada office filtra seu subset em
        # memória. Cada office continua tendo 1 PublicationSearch row
        # (UI Histórico de Buscas), seu cursor próprio e seu retry/backoff.
        from app.core.config import settings as _settings

        use_batch_mode = bool(_settings.publication_scheduler_batch_mode)
        if _settings.djen_fallback_enabled and not use_batch_mode:
            # O fallback é uma coleta nacional. Rodá-lo no loop legado faria
            # até uma paginação completa por escritório; com a contingência
            # habilitada, o fan-out batch é obrigatório.
            logger.warning(
                "PUBLICATION_SCHEDULER_BATCH_MODE=false ignorado porque "
                "DJEN_FALLBACK_ENABLED=true; usando fetch único + fan-out."
            )
            use_batch_mode = True

        if use_batch_mode:
            union_from = min(df for _, df, _ in active)
            union_to = max(dt for _, _, dt in active)
            total_active = len(active)

            if run_id is not None:
                self._update_progress(
                    run_id,
                    phase="pull_publications",
                    current=0,
                    total=total_active,
                    message=(
                        f"Buscando publicações L1 (1 chamada cobrindo "
                        f"{union_from:%Y-%m-%d %H:%M}..{union_to:%Y-%m-%d %H:%M})"
                    ),
                )

            try:
                publications = search_service.fetch_publications_for_window(
                    date_from=union_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    date_to=union_to.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    responsible_office_ids=[
                        internal_to_external.get(office_id, office_id)
                        for office_id, _, _ in active
                    ],
                    allow_djen_fallback=allow_djen_fallback,
                )
                fallback_metadata = dict(search_service.last_fetch_metadata)
                fallback_used = bool(
                    fallback_metadata.get("fallback_used")
                )
                logger.info(
                    "Batch %s fetch: %s publicações no período união (%s..%s) "
                    "— fan-out p/ %s escritórios.",
                    "DJEN" if fallback_used else "L1",
                    len(publications), union_from, union_to, total_active,
                )
            except Exception as exc:  # noqa: BLE001
                # L1 caiu — todos os offices ativos viram FALHA nesta rodada.
                # Logamos UMA stack trace; cada office só recebe o resumo.
                logger.exception(
                    "Falha no fetch L1 batch: marcando %s escritórios como falha.",
                    total_active,
                )
                err_msg = f"L1 batch fetch failed: {exc}"
                for office_id, df, dt in active:
                    attempt = self._record_attempt_failure(
                        office_id,
                        df,
                        dt,
                        err_msg,
                        automation_id,
                    )
                    failed.append(office_id)
                    failure_details.append(
                        self._failure_detail(office_id, err_msg, attempt)
                    )
                alert_sent = self._alert_publication_capture_failures(
                    automation_id,
                    failure_details,
                    run_id=run_id,
                    is_retry=is_retry,
                )
                if run_id is not None:
                    self._update_progress(
                        run_id,
                        phase="pull_publications",
                        current=total_active,
                        total=total_active,
                        message=f"Falha L1 fetch — {total_active} escritórios marcados como falha",
                    )
                return {
                    "records_found": 0,
                    "offices_ok": ok,
                    "offices_failed": failed,
                    "offices_degraded": degraded,
                    "offices_skipped": skipped,
                    "failures": failure_details,
                    "fallback_metadata": fallback_metadata,
                    "alert_sent": alert_sent,
                }

            # Fan-out: cada office processa o subset que é dele.
            fallback_used = bool(fallback_metadata.get("fallback_used"))
            for idx, (office_id, date_from, date_to) in enumerate(active, start=1):
                if run_id is not None:
                    ext = internal_to_external.get(office_id, office_id)
                    self._update_progress(
                        run_id,
                        phase="pull_publications",
                        current=idx - 1,
                        total=total_active,
                        message=f"Distribuindo p/ escritório {idx}/{total_active} (L1 id={ext})",
                    )

                try:
                    result = search_service.create_and_run_search(
                        date_from=date_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        date_to=date_to.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        responsible_office_id=internal_to_external.get(office_id, office_id),
                        origin_type="DJEN" if fallback_used else "OfficialJournalsCrawler",
                        auto_classify=False,
                        requested_by="scheduler",
                        prefetched_publications=publications,
                    )
                    records_found = int(result.get("total_new", 0) or result.get("total_found", 0) or 0)
                    total_found += records_found
                    if fallback_used:
                        primary_error = (
                            fallback_metadata.get("primary_error")
                            or "Legal One GET /Updates retornou HTTP 502."
                        )
                        attempt = self._record_attempt_failure(
                            office_id,
                            date_from,
                            date_to,
                            (
                                f"{primary_error} Contingência DJEN capturou "
                                f"{records_found} publicação(ões) como cobertura "
                                "suplementar; reconciliação L1 pendente."
                            ),
                            automation_id,
                            djen_covered=True,
                            djen_coverage_complete=bool(
                                fallback_metadata.get("coverage_complete")
                            ),
                            djen_coverage_metadata=fallback_metadata,
                        )
                        degraded.append(office_id)
                        degraded_details.append(
                            self._failure_detail(
                                office_id,
                                str(primary_error),
                                attempt,
                            )
                        )
                    else:
                        self._record_attempt_success(
                            office_id,
                            date_from,
                            date_to,
                            records_found,
                            automation_id,
                        )
                    ok.append(office_id)
                    if run_id is not None:
                        self._update_progress(
                            run_id,
                            current=idx,
                            total=total_active,
                            message=f"Escritório {idx}/{total_active}: +{records_found} publicações (total {total_found})",
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Falha ao processar publicações do escritório %s", office_id)
                    attempt = self._record_attempt_failure(
                        office_id,
                        date_from,
                        date_to,
                        str(exc),
                        automation_id,
                    )
                    failed.append(office_id)
                    failure_details.append(
                        self._failure_detail(office_id, str(exc), attempt)
                    )
                    if run_id is not None:
                        self._update_progress(
                            run_id,
                            current=idx,
                            total=total_active,
                            message=f"Escritório {idx}/{total_active}: falhou",
                        )

            if failure_details:
                alert_sent = self._alert_publication_capture_failures(
                    automation_id,
                    failure_details,
                    run_id=run_id,
                    is_retry=is_retry,
                )
            if degraded_details:
                alert_sent = (
                    self._alert_publication_capture_degraded(
                        automation_id,
                        degraded_details,
                        fallback_metadata,
                        run_id=run_id,
                        is_retry=is_retry,
                    )
                    or alert_sent
                )

            return {
                "records_found": total_found,
                "offices_ok": ok,
                "offices_failed": failed,
                "offices_degraded": degraded,
                "offices_skipped": skipped,
                "failures": failure_details,
                "degraded": degraded_details,
                "fallback_metadata": fallback_metadata,
                "alert_sent": alert_sent,
            }

        # ── LEGACY MODE (feature flag OFF) ────────────────────────────
        # Mantido pra rollback caso o batch mode dê problema. Seta
        # `PUBLICATION_SCHEDULER_BATCH_MODE=false` no Coolify e restart.
        # Remover após 1 semana de batch mode estável.
        total_offices = len(active)
        for idx, (office_id, date_from, date_to) in enumerate(active, start=1):
            if run_id is not None:
                ext = internal_to_external.get(office_id, office_id)
                self._update_progress(
                    run_id,
                    phase="pull_publications",
                    current=idx - 1,
                    total=total_offices,
                    message=f"Buscando escritório {idx}/{total_offices} (L1 id={ext})",
                )

            try:
                result = search_service.create_and_run_search(
                    # Formato ISO com hora/minuto — se usar só %Y-%m-%d, o
                    # client expande para T00:00:00Z e janelas menores que 1
                    # dia (overlap de horas) ficam ge==le e retornam 0.
                    date_from=date_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    date_to=date_to.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    responsible_office_id=internal_to_external.get(office_id, office_id),
                    auto_classify=False,
                    requested_by="scheduler",
                    allow_djen_fallback=allow_djen_fallback,
                )
                records_found = int(result.get("total_new", 0) or result.get("total_found", 0) or 0)
                total_found += records_found
                result_fetch_metadata = result.get("fetch_metadata") or {}
                if result_fetch_metadata.get("fallback_used"):
                    fallback_metadata = dict(result_fetch_metadata)
                    attempt = self._record_attempt_failure(
                        office_id,
                        date_from,
                        date_to,
                        (
                            f"{fallback_metadata.get('primary_error') or 'Legal One /Updates HTTP 502.'} "
                            f"Contingência DJEN capturou {records_found} publicação(ões) "
                            "como cobertura suplementar; "
                            "reconciliação L1 pendente."
                        ),
                        automation_id,
                        djen_covered=True,
                        djen_coverage_complete=bool(
                            fallback_metadata.get("coverage_complete")
                        ),
                        djen_coverage_metadata=fallback_metadata,
                    )
                    degraded.append(office_id)
                    degraded_details.append(
                        self._failure_detail(
                            office_id,
                            str(
                                fallback_metadata.get("primary_error")
                                or "Legal One /Updates HTTP 502."
                            ),
                            attempt,
                        )
                    )
                else:
                    self._record_attempt_success(
                        office_id,
                        date_from,
                        date_to,
                        records_found,
                        automation_id,
                    )
                ok.append(office_id)
                if run_id is not None:
                    self._update_progress(
                        run_id,
                        current=idx,
                        total=total_offices,
                        message=f"Escritório {idx}/{total_offices}: +{records_found} publicações (total {total_found})",
                    )
            except Exception as exc:  # noqa: BLE001 — queremos capturar qualquer falha
                logger.exception("Falha ao capturar publicações do escritório %s", office_id)
                attempt = self._record_attempt_failure(
                    office_id,
                    date_from,
                    date_to,
                    str(exc),
                    automation_id,
                )
                failed.append(office_id)
                failure_details.append(
                    self._failure_detail(office_id, str(exc), attempt)
                )
                # O modo legado pode levar muitos minutos. A primeira falha
                # avisa imediatamente; ao final enviamos o consolidado.
                if len(failure_details) == 1:
                    alert_sent = self._alert_publication_capture_failures(
                        automation_id,
                        failure_details,
                        run_id=run_id,
                        is_retry=is_retry,
                    )
                if run_id is not None:
                    self._update_progress(
                        run_id,
                        current=idx,
                        total=total_offices,
                        message=f"Escritório {idx}/{total_offices}: falhou",
                    )

        if len(failure_details) > 1:
            alert_sent = (
                self._alert_publication_capture_failures(
                    automation_id,
                    failure_details,
                    run_id=run_id,
                    is_retry=is_retry,
                )
                or alert_sent
            )
        if degraded_details:
            alert_sent = (
                self._alert_publication_capture_degraded(
                    automation_id,
                    degraded_details,
                    fallback_metadata,
                    run_id=run_id,
                    is_retry=is_retry,
                )
                or alert_sent
            )

        return {
            "records_found": total_found,
            "offices_ok": ok,
            "offices_failed": failed,
            "offices_degraded": degraded,
            "offices_skipped": skipped,
            "failures": failure_details,
            "degraded": degraded_details,
            "fallback_metadata": fallback_metadata,
            "alert_sent": alert_sent,
        }

    def _execute_classify(self, office_ids: List[int], run_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Classifica publicações NOVO dos escritórios indicados via Anthropic
        Batch API.

        Fluxo:
          1. Mapeia office_id interno → external_id (L1), que é o valor
             salvo em PublicationRecord.linked_office_id.
          2. Coleta registros pendentes (status=NOVO, sem category).
          3. Submete batch à Anthropic.
          4. Faz polling até o batch terminar (timeout defensivo).
          5. Aplica resultados e atualiza os registros.
        """
        import asyncio
        import time
        from app.models.legal_one import LegalOneOffice as _LOOffice
        from app.services.publication_batch_classifier import (
            PublicationBatchClassifier,
            ANTHROPIC_STATUS_ENDED,
        )

        logger.info("Classifying publications for offices (internal ids): %s", office_ids)

        # 1) Mapeia id interno → external_id
        rows = (
            self.db.query(_LOOffice.id, _LOOffice.external_id)
            .filter(_LOOffice.id.in_(office_ids))
            .all()
        )
        internal_to_external = {r[0]: r[1] for r in rows}
        external_office_ids = [
            internal_to_external.get(oid, oid) for oid in office_ids
        ]
        logger.info(
            "Classify: office_ids externos (L1) = %s", external_office_ids
        )

        classifier = PublicationBatchClassifier(db=self.db)

        # 2) Coleta pendentes em todos os escritórios selecionados
        if run_id is not None:
            self._update_progress(run_id, phase="classify:collect", message="Coletando publicações pendentes...")
        all_records = []
        for ext_oid in external_office_ids:
            recs = classifier.collect_pending_records(linked_office_id=ext_oid)
            logger.info(
                "Classify: escritório %s → %d registros pendentes.", ext_oid, len(recs),
            )
            all_records.extend(recs)

        if not all_records:
            logger.info("Classify: nada a classificar.")
            if run_id is not None:
                self._update_progress(run_id, phase="classify:done", current=0, total=0, message="Nada para classificar")
            return {"records_classified": 0, "batch_id": None}

        total_records = len(all_records)
        if run_id is not None:
            self._update_progress(
                run_id,
                phase="classify:submit",
                current=0,
                total=total_records,
                message=f"Submetendo batch à Anthropic ({total_records} registros)...",
            )

        # 3) Submete batch (API async → asyncio.run)
        async def _run_flow():
            batch = await classifier.submit_batch(
                records=all_records, requested_by_email="scheduler"
            )
            logger.info(
                "Classify: batch %s submetido (%d registros).",
                batch.anthropic_batch_id, len(all_records),
            )

            # 4) Polling até terminar (timeout ~30 min)
            poll_interval = 30   # s
            max_wait = 30 * 60   # s
            deadline = time.monotonic() + max_wait

            while time.monotonic() < deadline:
                batch = await classifier.refresh_batch_status(batch)
                if run_id is not None:
                    done = (batch.succeeded_count or 0) + (batch.errored_count or 0)
                    self._update_progress(
                        run_id,
                        phase="classify:poll",
                        current=done,
                        total=total_records,
                        message=f"Anthropic: {done}/{total_records} classificadas (status={batch.anthropic_status})",
                    )
                if batch.anthropic_status == ANTHROPIC_STATUS_ENDED:
                    break
                logger.info(
                    "Classify: batch %s status=%s (succ=%s err=%s) — aguardando...",
                    batch.anthropic_batch_id,
                    batch.anthropic_status,
                    batch.succeeded_count,
                    batch.errored_count,
                )
                await asyncio.sleep(poll_interval)
            else:
                logger.warning(
                    "Classify: batch %s não terminou dentro de %ds; seguindo sem aplicar.",
                    batch.anthropic_batch_id, max_wait,
                )
                return {"records_classified": 0, "batch_id": batch.id, "timeout": True}

            # 5) Apply
            if run_id is not None:
                self._update_progress(
                    run_id,
                    phase="classify:apply",
                    current=total_records,
                    total=total_records,
                    message="Aplicando resultados nos registros...",
                )
            result = await classifier.apply_batch_results(batch)
            logger.info(
                "Classify: batch %s aplicado. Resultado: %s",
                batch.anthropic_batch_id, result,
            )
            return {
                "records_classified": result.get("succeeded", 0),
                "batch_id": batch.id,
                "failed": result.get("failed", 0),
                "skipped": result.get("skipped", 0),
                "total": result.get("total", 0),
            }

        try:
            return asyncio.run(_run_flow())
        except Exception as exc:
            logger.exception("Classify: falha na execução do batch: %s", exc)
            return {"records_classified": 0, "error": str(exc)}

    def _execute_treat_publications(
        self,
        office_ids: List[int],
        automation_id: Optional[int] = None,
        run_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        import time

        from app.models.publication_treatment import (
            RUN_STATUS_COMPLETED,
            RUN_STATUS_COMPLETED_WITH_ERRORS,
        )
        from app.services.publication_treatment_service import PublicationTreatmentService

        treatment_service = PublicationTreatmentService(self.db)
        start_result = treatment_service.start_run(
            office_ids=office_ids,
            trigger_type="AUTOMACAO",
            triggered_by_email="scheduler",
            automation_id=automation_id,
        )

        if not start_result.get("started"):
            existing_run = start_result.get("run") or {}
            if start_result.get("reason") == "already_running" and existing_run.get("id"):
                if run_id is not None:
                    self._update_progress(
                        run_id,
                        phase="treat_publications:wait",
                        message="Tratamento já está em execução; acompanhando run existente...",
                    )
                treatment_run_id = existing_run["id"]
            else:
                if run_id is not None:
                    self._update_progress(
                        run_id,
                        phase="treat_publications:done",
                        current=0,
                        total=0,
                        message="Nenhuma publicação pendente para tratamento.",
                    )
                return {
                    "run_id": existing_run.get("id"),
                    "success_count": existing_run.get("success_count", 0),
                    "failed_count": existing_run.get("failed_count", 0),
                }
        else:
            treatment_run_id = start_result["run"]["id"]
        poll_seconds = max(1, settings.publication_treatment_monitor_poll_seconds)

        while True:
            snapshot = treatment_service.get_run(treatment_run_id, sync_from_file=True)
            if not snapshot:
                raise RuntimeError(f"Run de tratamento #{treatment_run_id} não encontrado.")

            snapshot_payload = treatment_service._run_to_dict(snapshot)  # noqa: SLF001
            if run_id is not None:
                self._update_progress(
                    run_id,
                    phase="treat_publications:wait",
                    current=snapshot_payload.get("processed_items"),
                    total=snapshot_payload.get("total_items"),
                    message=(
                        f"Tratamento L1: {snapshot_payload.get('processed_items', 0)}/"
                        f"{snapshot_payload.get('total_items', 0)} processadas"
                    ),
                )

            if snapshot_payload["is_final"]:
                final_status = snapshot_payload["status"]
                if final_status not in {RUN_STATUS_COMPLETED, RUN_STATUS_COMPLETED_WITH_ERRORS}:
                    raise RuntimeError(
                        f"Tratamento de publicações finalizou com status {final_status}."
                    )

                if run_id is not None:
                    self._update_progress(
                        run_id,
                        phase="treat_publications:done",
                        current=snapshot_payload.get("processed_items"),
                        total=snapshot_payload.get("total_items"),
                        message=(
                            f"Tratamento concluído: {snapshot_payload.get('success_count', 0)} sucesso(s), "
                            f"{snapshot_payload.get('failed_count', 0)} falha(s)."
                        ),
                    )

                return {
                    "run_id": treatment_run_id,
                    "success_count": snapshot_payload.get("success_count", 0),
                    "failed_count": snapshot_payload.get("failed_count", 0),
                }

            time.sleep(poll_seconds)


def run_publication_capture_retry_sweep() -> int:
    """Consome retries agendados, manuais e alertas pendentes."""
    if not settings.publication_capture_retry_enabled:
        return 0

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        processed = 0
        try:
            processed += ScheduledAutomationService(
                db=db
            ).run_due_publication_retries()
        except Exception:
            logger.exception(
                "Falha no sweep de retries agendados da captura."
            )
            db.rollback()

        try:
            from app.services.legal_one_client import LegalOneApiClient
            from app.services.publication_search_service import (
                PublicationSearchService,
            )

            processed += PublicationSearchService(
                db,
                LegalOneApiClient(),
            ).reconcile_due_manual_searches()
        except Exception:
            logger.exception(
                "Falha no sweep de reconciliações manuais do Legal One."
            )
            db.rollback()

        try:
            from app.services.publication_capture_alert_service import (
                repair_missing_publication_capture_alerts,
                sweep_publication_capture_alerts,
            )

            repair_result = repair_missing_publication_capture_alerts(db)
            processed += int(
                repair_result.created + repair_result.linked_existing
            )
            alert_result = sweep_publication_capture_alerts(db)
            processed += int(alert_result.attempted)
        except Exception:
            logger.exception("Falha no sweep de alertas da captura.")
            db.rollback()

        return processed
    finally:
        db.close()


def register_publication_capture_retry_job(
    scheduler: BackgroundScheduler,
) -> None:
    """Registra o consumidor periódico do estado persistido no Postgres."""
    if not settings.publication_capture_retry_enabled:
        logger.info("Retry automático da captura de publicações desabilitado.")
        return

    poll_minutes = max(
        1,
        int(settings.publication_capture_retry_poll_minutes or 5),
    )
    scheduler.add_job(
        run_publication_capture_retry_sweep,
        trigger=IntervalTrigger(minutes=poll_minutes),
        id=PUBLICATION_RETRY_JOB_ID,
        name="Retry automático da captura de publicações",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    logger.info(
        "Retry automático da captura registrado (a cada %s min).",
        poll_minutes,
    )
