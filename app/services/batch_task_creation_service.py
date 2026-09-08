import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, joinedload

from app.api.v1.schemas import BatchInteractiveCreationRequest, BatchTaskCreationRequest
from app.core.config import settings
from app.models.batch_execution import (
    BATCH_PROCESSOR_GENERIC,
    BATCH_PROCESSOR_SPREADSHEET_INTERACTIVE,
    BATCH_PROCESSOR_SPREADSHEET_UPLOAD,
    BATCH_STATUS_CANCELLED,
    BATCH_STATUS_COMPLETED,
    BATCH_STATUS_COMPLETED_WITH_ERRORS,
    BATCH_STATUS_PAUSED,
    BATCH_STATUS_PENDING,
    BATCH_STATUS_PROCESSING,
    FINAL_BATCH_STATUSES,
    QUEUEABLE_BATCH_PROCESSORS,
    BatchExecution,
    BatchExecutionItem,
)
from app.models.legal_one import LegalOneTaskSubType
from app.services.batch_strategies.base_strategy import BaseStrategy
from app.services.batch_strategies.onerequest_strategy import OnerequestStrategy
from app.services.batch_strategies.onesid_strategy import OnesidStrategy
from app.services.batch_strategies.spreadsheet_strategy import SpreadsheetStrategy
from app.services.batch_utils import build_task_fingerprint, load_successful_fingerprints
from app.services.legal_one_client import LegalOneApiClient


logger = logging.getLogger(__name__)

# Quanto o worker espera as linhas de um lote aparecerem antes de desistir
# dele. Inserir 2.756 linhas leva ~1,5s; um minuto e' folga larga e ainda
# assim curto o bastante pra nao deixar lote morto ocupando a fila.
_ESPERA_MAXIMA_LINHAS_S = 60


class BatchTaskCreationService:
    def __init__(self, db: Session, client: LegalOneApiClient | None):
        self.db = db
        self.client = client
        self._strategies: dict[str, type[BaseStrategy]] = {
            "Onesid": OnesidStrategy,
            "Planilha": SpreadsheetStrategy,
            "OneRequest": OnerequestStrategy,
        }
        self._lease_duration = timedelta(seconds=max(settings.batch_worker_lease_seconds, 30))

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    def _clear_execution_claim(self, execution_log: BatchExecution) -> None:
        execution_log.worker_id = None
        execution_log.heartbeat_at = None
        execution_log.lease_expires_at = None

    def _refresh_execution_counts(self, execution_id: int) -> tuple[int, int]:
        success_count = (
            self.db.query(BatchExecutionItem)
            .filter(
                BatchExecutionItem.execution_id == execution_id,
                BatchExecutionItem.status == "SUCESSO",
            )
            .count()
        )
        failure_count = (
            self.db.query(BatchExecutionItem)
            .filter(
                BatchExecutionItem.execution_id == execution_id,
                BatchExecutionItem.status == "FALHA",
            )
            .count()
        )
        execution = self.db.query(BatchExecution).filter(BatchExecution.id == execution_id).first()
        if execution:
            execution.success_count = success_count
            execution.failure_count = failure_count
            self.db.commit()
        return success_count, failure_count

    def _finalize_execution(self, execution_log: BatchExecution, *, cancelled: bool = False) -> None:
        success_count, failure_count = self._refresh_execution_counts(execution_log.id)
        execution_log = self.db.query(BatchExecution).filter(BatchExecution.id == execution_log.id).first()
        if not execution_log:
            return

        execution_log.end_time = self._utcnow()
        self._clear_execution_claim(execution_log)
        execution_log.success_count = success_count
        execution_log.failure_count = failure_count

        if cancelled or execution_log.status == BATCH_STATUS_CANCELLED:
            execution_log.status = BATCH_STATUS_CANCELLED
        elif failure_count > 0:
            execution_log.status = BATCH_STATUS_COMPLETED_WITH_ERRORS
        else:
            execution_log.status = BATCH_STATUS_COMPLETED
        self.db.commit()

    def _get_execution_status(self, execution_id: int) -> str:
        execution = (
            self.db.query(BatchExecution)
            .filter(BatchExecution.id == execution_id)
            .first()
        )
        if not execution:
            return BATCH_STATUS_CANCELLED
        return execution.status

    async def _wait_for_execution_signal(self, execution_id: int) -> str:
        while True:
            status = self._get_execution_status(execution_id)
            if status == BATCH_STATUS_PAUSED:
                await asyncio.sleep(1)
                continue
            return status

    @staticmethod
    def _build_interactive_deadline_iso(due_date: str, due_time: str | None) -> str:
        local_tz = ZoneInfo("America/Sao_Paulo")
        due_date_obj = datetime.strptime(due_date, "%Y-%m-%d").date()
        parsed_time = datetime.strptime(due_time, "%H:%M").time() if due_time else time(23, 59, 59)
        aware_deadline = datetime.combine(due_date_obj, parsed_time).replace(tzinfo=local_tz)
        return aware_deadline.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _resolve_interactive_subtype(self, local_subtype_id: int) -> LegalOneTaskSubType:
        subtype = (
            self.db.query(LegalOneTaskSubType)
            .options(joinedload(LegalOneTaskSubType.parent_type))
            .filter(LegalOneTaskSubType.id == local_subtype_id)
            .first()
        )
        if not subtype or not subtype.parent_type:
            raise ValueError("Subtipo de tarefa nao encontrado na base local.")
        return subtype

    @staticmethod
    def _normalize_cnj_number(cnj_number: Any) -> str:
        if cnj_number is None:
            return ""
        return str(cnj_number).strip()

    def _preload_lawsuits_chunked(
        self,
        cnj_numbers: list[Any],
        execution_id: int,
        worker_id: str,
        *,
        chunk_size: int = 300,
    ) -> tuple[dict[str, dict[str, Any]], set[str], str | None]:
        # Precarrega processos por CNJ em blocos, emitindo heartbeat e
        # checando o sinal de controle entre cada bloco. Em lotes grandes
        # o preload em chamada unica estourava o lease (o heartbeat so
        # rodava no loop de itens, que so comeca depois do preload), o que
        # disparava re-claim por outra replica e travava o lote em
        # PROCESSANDO 0/0 indefinidamente.
        #
        # Retorna (lawsuit_lookup, prefetched_cnj_numbers, stop_signal).
        # stop_signal != None => o chamador deve abortar e finalizar
        # conforme o sinal (cancelado/pausado/perdido).
        normalized_numbers: list[str] = []
        seen_numbers: set[str] = set()
        for cnj_number in cnj_numbers:
            normalized = self._normalize_cnj_number(cnj_number)
            if not normalized or normalized in seen_numbers:
                continue
            normalized_numbers.append(normalized)
            seen_numbers.add(normalized)

        lawsuit_lookup: dict[str, dict[str, Any]] = {}
        prefetched = set(normalized_numbers)
        if not normalized_numbers or self.client is None:
            return lawsuit_lookup, prefetched, None

        for start in range(0, len(normalized_numbers), chunk_size):
            signal = self._get_control_signal(execution_id, worker_id)
            if signal != BATCH_STATUS_PROCESSING:
                return lawsuit_lookup, prefetched, signal
            if not self.heartbeat_execution(execution_id, worker_id):
                return lawsuit_lookup, prefetched, "LOST"
            chunk = normalized_numbers[start:start + chunk_size]
            lawsuit_lookup.update(self.client.search_lawsuits_by_cnj_numbers(chunk))

        return lawsuit_lookup, prefetched, None

    def _handle_preload_stop(self, execution_id: int, signal: str) -> None:
        execution = self.db.query(BatchExecution).filter(BatchExecution.id == execution_id).first()
        if not execution:
            return
        if signal == BATCH_STATUS_CANCELLED:
            self._finalize_execution(execution, cancelled=True)
        elif signal == BATCH_STATUS_PAUSED:
            self._clear_execution_claim(execution)
            self.db.commit()
        # MISSING / LOST: outra replica assumiu — nada a finalizar aqui.

    def create_spreadsheet_execution(
        self,
        *,
        file_content: bytes,
        source_filename: str | None,
        requested_by_email: str | None = None,
    ) -> BatchExecution:
        strategy = SpreadsheetStrategy(self.db, self.client)
        extracted = strategy.extract_rows_for_queue(file_content)
        rows = extracted["rows"]

        execution_log = BatchExecution(
            source="Planilha",
            processor_type=BATCH_PROCESSOR_SPREADSHEET_UPLOAD,
            source_filename=source_filename,
            requested_by_email=requested_by_email,
            status=BATCH_STATUS_PENDING,
            total_items=len(rows),
            start_time=self._utcnow(),
        )
        # flush (nao commit): atribui o id sem publicar a execucao pra
        # ninguem de fora. O commit unico la embaixo faz a execucao e as
        # linhas dela aparecerem JUNTAS — ver a nota da corrida no metodo
        # irmao `create_interactive_execution`.
        self.db.add(execution_log)
        self.db.flush()

        for row_data in rows:
            self.db.add(
                BatchExecutionItem(
                    execution_id=execution_log.id,
                    process_number=row_data.get("CNJ") or row_data.get("PASTA") or "N/A",
                    input_data=row_data,
                    status="PENDENTE",
                )
            )
        self.db.commit()
        self.db.refresh(execution_log)
        return execution_log

    def create_interactive_execution(
        self,
        request: BatchInteractiveCreationRequest,
        requested_by_email: str | None = None,
    ) -> BatchExecution:
        execution_log = BatchExecution(
            source=f"Planilha Interativa ({request.source_filename})",
            processor_type=BATCH_PROCESSOR_SPREADSHEET_INTERACTIVE,
            source_filename=request.source_filename,
            requested_by_email=requested_by_email,
            status=BATCH_STATUS_PENDING,
            total_items=len(request.tasks),
            start_time=self._utcnow(),
        )
        # CORRIDA (medida em producao, lote 6019 de 08/09/2026): commitar a
        # execucao aqui a publica com status PENDENTE, e o worker — que
        # varre a fila de 5 em 5 segundos — pode reivindica-la ANTES de as
        # linhas existirem. Ele entao encontra zero itens, conclui que nao ha
        # nada a fazer e fecha o lote como CONCLUIDO. Foi o que aconteceu com
        # 2.756 agendamentos: 1,5s de duracao, 0 processados, os 2.756 itens
        # intactos em PENDENTE e um visto verde na tela dizendo que deu certo.
        #
        # A janela e' proporcional ao tamanho da planilha (o tempo de inserir
        # as linhas), entao o defeito castiga justamente o lote grande — e
        # some no lote pequeno do teste. Uma transacao so' elimina a janela:
        # `flush` da o id sem publicar nada.
        self.db.add(execution_log)
        self.db.flush()

        for task_data in request.tasks:
            self.db.add(
                BatchExecutionItem(
                    execution_id=execution_log.id,
                    process_number=task_data.cnj_number,
                    input_data=task_data.model_dump(),
                    status="PENDENTE",
                )
            )
        self.db.commit()
        self.db.refresh(execution_log)
        return execution_log

    def _claimable_execution_filter(self, now_utc: datetime):
        return or_(
            BatchExecution.status == BATCH_STATUS_PENDING,
            and_(
                BatchExecution.status == BATCH_STATUS_PROCESSING,
                or_(
                    BatchExecution.lease_expires_at.is_(None),
                    BatchExecution.lease_expires_at < now_utc,
                ),
            ),
        )

    def claim_next_execution(self, worker_id: str) -> int | None:
        now_utc = self._utcnow()
        candidate_rows = (
            self.db.query(BatchExecution.id)
            .filter(
                BatchExecution.processor_type.in_(QUEUEABLE_BATCH_PROCESSORS),
                self._claimable_execution_filter(now_utc),
            )
            .order_by(BatchExecution.start_time.asc(), BatchExecution.id.asc())
            .limit(10)
            .all()
        )

        for candidate in candidate_rows:
            updated = (
                self.db.query(BatchExecution)
                .filter(
                    BatchExecution.id == candidate.id,
                    self._claimable_execution_filter(now_utc),
                )
                .update(
                    {
                        BatchExecution.status: BATCH_STATUS_PROCESSING,
                        BatchExecution.worker_id: worker_id,
                        BatchExecution.heartbeat_at: now_utc,
                        BatchExecution.lease_expires_at: now_utc + self._lease_duration,
                        BatchExecution.end_time: None,
                    },
                    synchronize_session=False,
                )
            )
            if updated:
                self.db.commit()
                return candidate.id
            self.db.rollback()

        return None

    def heartbeat_execution(self, execution_id: int, worker_id: str) -> bool:
        now_utc = self._utcnow()
        updated = (
            self.db.query(BatchExecution)
            .filter(
                BatchExecution.id == execution_id,
                BatchExecution.worker_id == worker_id,
                BatchExecution.status == BATCH_STATUS_PROCESSING,
            )
            .update(
                {
                    BatchExecution.heartbeat_at: now_utc,
                    BatchExecution.lease_expires_at: now_utc + self._lease_duration,
                },
                synchronize_session=False,
            )
        )
        if not updated:
            self.db.rollback()
            return False
        self.db.commit()
        return True

    def _get_control_signal(self, execution_id: int, worker_id: str) -> str:
        execution = (
            self.db.query(BatchExecution)
            .filter(BatchExecution.id == execution_id)
            .first()
        )
        if not execution:
            return "MISSING"
        if execution.status == BATCH_STATUS_PAUSED:
            return BATCH_STATUS_PAUSED
        if execution.status == BATCH_STATUS_CANCELLED:
            return BATCH_STATUS_CANCELLED
        if execution.worker_id != worker_id:
            return "LOST"
        return execution.status

    async def _process_interactive_item(
        self,
        log_item: BatchExecutionItem,
        *,
        known_fingerprints: set[str],
        lawsuit_lookup: dict[str, dict[str, Any]] | None = None,
        prefetched_cnj_numbers: set[str] | None = None,
    ) -> bool:
        try:
            payload = log_item.input_data or {}
            subtype = self._resolve_interactive_subtype(int(payload["sub_type_id"]))
            end_datetime_iso = self._build_interactive_deadline_iso(
                payload["due_date"],
                payload.get("due_time"),
            )
            fingerprint = build_task_fingerprint(
                process_number=payload["cnj_number"],
                subtype_identifier=subtype.external_id,
                responsible_identifier=payload["responsible_external_id"],
                due_datetime_iso=end_datetime_iso,
                origin_identifier=subtype.parent_type.external_id,
            )
            log_item.fingerprint = fingerprint

            if fingerprint in known_fingerprints:
                raise ValueError("Tarefa duplicada detectada: ja existe um agendamento igual processado com sucesso.")

            cnj_number = self._normalize_cnj_number(payload.get("cnj_number"))
            lawsuit = (lawsuit_lookup or {}).get(cnj_number)
            if lawsuit is None and (prefetched_cnj_numbers is None or cnj_number not in prefetched_cnj_numbers):
                lawsuit = self.client.search_lawsuit_by_cnj(cnj_number)
            if not lawsuit or not lawsuit.get("id"):
                raise Exception("Processo nao encontrado no Legal One.")

            lawsuit_id = lawsuit["id"]
            responsible_office_id = lawsuit.get("responsibleOfficeId")
            if not responsible_office_id:
                raise Exception("Processo nao possui escritorio responsavel definido.")

            start_datetime_iso = self._utcnow().isoformat().replace("+00:00", "Z")
            task_payload = {
                "description": payload["description"],
                "priority": "Normal",
                "startDateTime": start_datetime_iso,
                "endDateTime": end_datetime_iso,
                "status": {"id": 0},
                "typeId": subtype.parent_type.external_id,
                "subTypeId": subtype.external_id,
                "responsibleOfficeId": responsible_office_id,
                "originOfficeId": responsible_office_id,
                "participants": [
                    {
                        "contact": {"id": payload["responsible_external_id"]},
                        "isResponsible": True,
                        "isExecuter": True,
                        "isRequester": True,
                    }
                ],
            }

            created_task = self.client.create_task(task_payload)
            if not created_task or not created_task.get("id"):
                raise Exception("Falha na criacao da tarefa (resposta invalida da API).")

            task_id = created_task["id"]
            self.client.link_task_to_lawsuit(task_id, {"linkType": "Litigation", "linkId": lawsuit_id})

            log_item.status = "SUCESSO"
            log_item.created_task_id = task_id
            log_item.error_message = None
            known_fingerprints.add(fingerprint)
            self.db.commit()
            return True
        except Exception as exc:
            log_item.status = "FALHA"
            log_item.error_message = str(exc)
            self.db.commit()
            return False

    async def _process_items_loop(
        self,
        execution_id: int,
        worker_id: str,
        *,
        item_handler,
    ) -> None:
        item_ids = [
            row.id
            for row in (
                self.db.query(BatchExecutionItem.id)
                .filter(
                    BatchExecutionItem.execution_id == execution_id,
                    BatchExecutionItem.status.in_(["PENDENTE", "REPROCESSANDO"]),
                )
                .order_by(BatchExecutionItem.id.asc())
                .all()
            )
        ]

        # TRAVA DE SANIDADE. A transacao acima fecha a corrida na origem,
        # mas quem conclui o lote e' este laco — e concluir "sem nada a
        # fazer" um lote que DECLARA ter 2.756 linhas e' sempre mentira,
        # venha a divergencia de onde vier. Aqui ele solta a garra e deixa o
        # lote voltar pra fila em vez de carimbar CONCLUIDO por cima de
        # trabalho que ninguem fez.
        if not item_ids:
            execucao = (
                self.db.query(BatchExecution)
                .filter(BatchExecution.id == execution_id)
                .first()
            )
            if execucao and (execucao.total_items or 0) > 0:
                gravadas = (
                    self.db.query(BatchExecutionItem)
                    .filter(BatchExecutionItem.execution_id == execution_id)
                    .count()
                )
                if gravadas == 0:
                    # start_time pode voltar NAIVE (SQLite na suite, e
                    # qualquer driver que perca o fuso): comparar direto
                    # levanta TypeError e mata o laco inteiro — a trava
                    # derrubaria justamente o worker que ela protege.
                    nascido = execucao.start_time
                    if nascido.tzinfo is None:
                        nascido = nascido.replace(tzinfo=timezone.utc)
                    idade = (self._utcnow() - nascido).total_seconds()
                    if idade < _ESPERA_MAXIMA_LINHAS_S:
                        # Ainda pode ser a gravacao em andamento: devolve pra
                        # fila e tenta de novo no proximo giro.
                        logger.warning(
                            "Lote %s reivindicado sem nenhuma das %s linhas "
                            "gravadas (%.1fs de vida) — devolvido pra fila.",
                            execution_id, execucao.total_items, idade,
                        )
                        execucao.status = BATCH_STATUS_PENDING
                        self._clear_execution_claim(execucao)
                        self.db.commit()
                        return
                    # Passou da espera: as linhas nao vem mais. Nao concluir
                    # calado — o operador precisa saber que o lote morreu.
                    logger.error(
                        "Lote %s declara %s linhas e nao gravou NENHUMA em "
                        "%.0fs. Encerrando como cancelado para nao registrar "
                        "sucesso falso.",
                        execution_id, execucao.total_items, idade,
                    )
                    self._finalize_execution(execucao, cancelled=True)
                    return

        for item_id in item_ids:
            signal = self._get_control_signal(execution_id, worker_id)
            if signal == BATCH_STATUS_PAUSED:
                execution = self.db.query(BatchExecution).filter(BatchExecution.id == execution_id).first()
                if execution:
                    self._clear_execution_claim(execution)
                    self.db.commit()
                return
            if signal == BATCH_STATUS_CANCELLED:
                execution = self.db.query(BatchExecution).filter(BatchExecution.id == execution_id).first()
                if execution:
                    self._finalize_execution(execution, cancelled=True)
                return
            if signal in {"MISSING", "LOST"}:
                return
            if not self.heartbeat_execution(execution_id, worker_id):
                return

            item = self.db.query(BatchExecutionItem).filter(BatchExecutionItem.id == item_id).first()
            if not item or item.status not in {"PENDENTE", "REPROCESSANDO"}:
                continue
            await item_handler(item)
            self._refresh_execution_counts(execution_id)

        execution = self.db.query(BatchExecution).filter(BatchExecution.id == execution_id).first()
        if not execution:
            return

        status = self._get_control_signal(execution_id, worker_id)
        if status == BATCH_STATUS_CANCELLED:
            self._finalize_execution(execution, cancelled=True)
            return
        if status == BATCH_STATUS_PAUSED:
            self._clear_execution_claim(execution)
            self.db.commit()
            return
        self._finalize_execution(execution)

    async def process_claimed_execution(self, execution_id: int, worker_id: str) -> None:
        execution = (
            self.db.query(BatchExecution)
            .options(joinedload(BatchExecution.items))
            .filter(BatchExecution.id == execution_id)
            .first()
        )
        if not execution or execution.worker_id != worker_id:
            return

        try:
            if execution.processor_type == BATCH_PROCESSOR_SPREADSHEET_UPLOAD:
                strategy = SpreadsheetStrategy(self.db, self.client)
                caches = await strategy._load_caches()
                known_fingerprints = load_successful_fingerprints(self.db)
                pending_rows = [
                    item.input_data or {}
                    for item in execution.items
                    if item.status in {"PENDENTE", "REPROCESSANDO"}
                ]
                lawsuit_lookup, prefetched_cnj_numbers, stop_signal = self._preload_lawsuits_chunked(
                    [row.get("CNJ") for row in pending_rows],
                    execution.id,
                    worker_id,
                )
                if stop_signal is not None:
                    self._handle_preload_stop(execution.id, stop_signal)
                    return

                async def handle_item(item: BatchExecutionItem):
                    await strategy.process_single_item(
                        item,
                        item.input_data or {},
                        caches,
                        known_fingerprints=known_fingerprints,
                        lawsuit_lookup=lawsuit_lookup,
                        prefetched_cnj_numbers=prefetched_cnj_numbers,
                    )

                await self._process_items_loop(
                    execution.id,
                    worker_id,
                    item_handler=handle_item,
                )
                return

            if execution.processor_type == BATCH_PROCESSOR_SPREADSHEET_INTERACTIVE:
                known_fingerprints = load_successful_fingerprints(self.db)
                pending_payloads = [
                    item.input_data or {}
                    for item in execution.items
                    if item.status in {"PENDENTE", "REPROCESSANDO"}
                ]
                lawsuit_lookup, prefetched_cnj_numbers, stop_signal = self._preload_lawsuits_chunked(
                    [payload.get("cnj_number") for payload in pending_payloads],
                    execution.id,
                    worker_id,
                )
                if stop_signal is not None:
                    self._handle_preload_stop(execution.id, stop_signal)
                    return

                async def handle_item(item: BatchExecutionItem):
                    await self._process_interactive_item(
                        item,
                        known_fingerprints=known_fingerprints,
                        lawsuit_lookup=lawsuit_lookup,
                        prefetched_cnj_numbers=prefetched_cnj_numbers,
                    )

                await self._process_items_loop(
                    execution.id,
                    worker_id,
                    item_handler=handle_item,
                )
                return

            logging.warning("Processador de lote nao suportado pelo worker: %s", execution.processor_type)
            execution.status = BATCH_STATUS_COMPLETED_WITH_ERRORS
            self._clear_execution_claim(execution)
            execution.end_time = self._utcnow()
            self.db.commit()
        except Exception as exc:
            logging.error("Erro ao processar execucao %s: %s", execution_id, exc, exc_info=True)
            execution = self.db.query(BatchExecution).filter(BatchExecution.id == execution_id).first()
            if execution:
                self._finalize_execution(
                    execution,
                    cancelled=(execution.status == BATCH_STATUS_CANCELLED),
                )

    async def process_spreadsheet_request(self, file_content: bytes, execution_id: int):
        logging.info("Iniciando processamento legado de lote via planilha. ID Execucao: %s", execution_id)

        execution_log = self.db.query(BatchExecution).filter(BatchExecution.id == execution_id).first()
        if not execution_log:
            logging.error("Log de execucao %s nao encontrado. Abortando.", execution_id)
            return

        if execution_log.status == BATCH_STATUS_CANCELLED:
            self._finalize_execution(execution_log, cancelled=True)
            return

        execution_log.status = BATCH_STATUS_PROCESSING
        execution_log.processor_type = BATCH_PROCESSOR_SPREADSHEET_UPLOAD
        self.db.commit()

        try:
            spreadsheet_request = BatchTaskCreationRequest(
                fonte="Planilha",
                processos=[],
                file_content=file_content,
            )
            strategy_instance = SpreadsheetStrategy(self.db, self.client)
            result = await strategy_instance.process_batch(spreadsheet_request, execution_log)
            execution_log.success_count = result.get("sucesso", 0)
            execution_log.failure_count = result.get("falhas", 0)
            self._finalize_execution(execution_log, cancelled=result.get("cancelled", False))
        except Exception as exc:
            logging.error("Erro catastrofico ao processar a planilha: %s", exc, exc_info=True)
            total = execution_log.total_items or 0
            sucesso = execution_log.success_count or 0
            execution_log.failure_count = max(0, total - sucesso)
            self._finalize_execution(execution_log)

    async def process_interactive_batch_request(
        self,
        request: BatchInteractiveCreationRequest,
        requested_by_email: str | None = None,
    ):
        execution = self.create_interactive_execution(request, requested_by_email=requested_by_email)
        logging.info("Execucao interativa %s criada e aguardando worker.", execution.id)

    async def process_batch_request(self, request: BatchTaskCreationRequest):
        logging.info(
            "Recebida requisicao de lote da fonte '%s' com %s processos.",
            request.fonte,
            len(request.processos),
        )
        now_utc = self._utcnow()

        execution_log = BatchExecution(
            source=request.fonte,
            processor_type=BATCH_PROCESSOR_GENERIC,
            status=BATCH_STATUS_PROCESSING,
            total_items=len(request.processos),
            start_time=now_utc,
        )
        self.db.add(execution_log)
        self.db.commit()
        self.db.refresh(execution_log)

        try:
            strategy_class = self._strategies.get(request.fonte)
            if not strategy_class:
                raise ValueError(f"Nenhuma estrategia encontrada para a fonte: '{request.fonte}'")

            strategy_instance = strategy_class(self.db, self.client)
            result = await strategy_instance.process_batch(request, execution_log)
            execution_log.success_count = result.get("sucesso", 0)
            execution_log.failure_count = result.get("falhas", 0)
            self._finalize_execution(execution_log, cancelled=result.get("cancelled", False))
        except Exception as exc:
            logging.error("Erro catastrofico ao processar o lote: %s", exc, exc_info=True)
            execution_log.failure_count = execution_log.total_items - execution_log.success_count
            self._finalize_execution(execution_log)

    async def retry_failed_items(self, original_execution_id: int, target_item_ids: list[int] = None):
        logging.info("Enfileirando retentativa inteligente para lote ID: %s", original_execution_id)

        original_execution = (
            self.db.query(BatchExecution)
            .options(joinedload(BatchExecution.items))
            .filter(BatchExecution.id == original_execution_id)
            .first()
        )
        if not original_execution:
            logging.error("Lote %s nao encontrado.", original_execution_id)
            return

        failed_items = []
        for item in original_execution.items:
            if item.status != "FALHA":
                continue
            if item.input_data is None:
                continue
            if target_item_ids and item.id not in target_item_ids:
                continue
            failed_items.append(item)

        if not failed_items:
            logging.warning("Nenhum item elegivel para retry encontrado no lote %s.", original_execution_id)
            return

        for item in failed_items:
            item.status = "PENDENTE"
            item.error_message = None

        original_execution.status = BATCH_STATUS_PENDING
        original_execution.end_time = None
        self._clear_execution_claim(original_execution)
        self.db.commit()
        self._refresh_execution_counts(original_execution.id)
