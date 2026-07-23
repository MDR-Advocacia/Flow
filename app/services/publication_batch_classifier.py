"""
Serviço de classificação em lote via Message Batches API da Anthropic.

Diferente do classificador síncrono (publication_search_service._auto_classify_records),
que processa uma publicação por vez respeitando rate limits, este serviço
envia centenas ou milhares de publicações em um único lote assíncrono.

Vantagens:
  - 50% mais barato (Batch API tem desconto permanente)
  - Sem problemas com rate limit de RPM/TPM
  - Escalável até 100.000 requisições por lote
  - Processamento tipicamente termina em minutos a algumas horas

Fluxo:
  1. submit_pending_classifications() → cria batch na Anthropic
  2. refresh_batch_status() → consulta status (polling ou manual)
  3. apply_batch_results() → baixa resultados e aplica no banco

Limitações:
  - Processamento assíncrono (não é tempo real; SLA de 24h da Anthropic)
  - Uma publicação pode expirar se o batch não for processado em 24h
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.classification import CLF_ITEM_FAILED, CLF_ITEM_SUCCESS
from app.models.publication_batch import (
    ANTHROPIC_STATUS_ENDED,
    PUB_BATCH_STATUS_APPLIED,
    PUB_BATCH_STATUS_FAILED,
    PUB_BATCH_STATUS_IN_PROGRESS,
    PUB_BATCH_STATUS_READY,
    PUB_BATCH_STATUS_SUBMITTED,
    PublicationBatchClassification,
)
from app.models.publication_search import (
    RECORD_STATUS_CLASSIFIED,
    RECORD_STATUS_ERROR,
    RECORD_STATUS_NEW,
    VALID_POLOS,
    PublicationRecord,
)
from app.services.classifier.ai_client import (
    MAX_PUBLICATION_TEXT_CHARS,
    AnthropicClassifierClient,
)
from app.services.classifier.prompts import (
    SYSTEM_PROMPT,
    build_feedback_examples,
    build_system_prompt_for_office,
    build_user_message,
    load_office_overrides,
)
from app.services.classifier.taxonomy import validate_classification, repair_classification
from app.services.classifier.response_schema import (
    validate_response,
    ResponseSchemaError,
)

logger = logging.getLogger(__name__)


def _alertar_falha_batch(batch, error_message: str, requested_by_email: Optional[str]) -> None:
    """Avisa por e-mail que o batch de classificação FALHOU ao ser criado na
    Anthropic (ex.: HTTP 502). Vale pro envio manual E pro job noturno, porque
    é chamado no ponto único onde o batch vira FAILED.

    Best-effort: qualquer erro aqui é engolido — não pode derrubar o registro de
    falha nem o fluxo do caller. Reusa o sender SMTP de mail_service."""
    try:
        from app.core.config import settings
        from app.services.mail_service import send_failure_report

        destinatarios = (
            settings.classificacao_alert_email
            or requested_by_email
            or settings.mail_to
            or settings.email_to
        )
        if not destinatarios:
            logger.warning(
                "Batch de classificação #%s falhou, mas não há destinatário de alerta "
                "(CLASSIFICACAO_ALERT_EMAIL/MAIL_TO). E-mail não enviado.",
                getattr(batch, "id", "?"),
            )
            return
        total = getattr(batch, "total_records", None) or 0
        origem = requested_by_email or "automático (job agendado)"
        send_failure_report(
            failed_items=[{
                "cnj": f"Batch de classificação #{getattr(batch, 'id', '?')} · {total} publicações",
                "motivo": (error_message or "erro desconhecido")[:1500],
                "execution_id": getattr(batch, "id", None),
            }],
            batch_source=f"Classificação de Publicações (Batch API) · origem: {origem}",
            recipients=destinatarios,
            system_name="Flow",
        )
        logger.info("Alerta de falha do batch de classificação #%s enviado.", getattr(batch, "id", "?"))
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao enviar alerta de e-mail do batch de classificação (ignorado).")


class PublicationBatchClassifier:
    """
    Orquestra a classificação em lote de publicações via Anthropic Batch API.
    """

    def __init__(self, db: Session, ai_client: Optional[AnthropicClassifierClient] = None):
        self.db = db
        self.ai = ai_client or AnthropicClassifierClient()

    # ──────────────────────────────────────────────────────────────────
    # Submit
    # ──────────────────────────────────────────────────────────────────

    def collect_pending_records(
        self,
        linked_office_id: Optional[int] = None,
        limit: Optional[int] = None,
        only_unlinked: bool = False,
    ) -> List[PublicationRecord]:
        """
        Retorna registros NOVOS com texto que precisam ser classificados,
        aplicando deduplicação agressiva: apenas UMA publicação por
        (processo, dia_de_publicação) é enviada ao batch.

        Rationale: certos tribunais (ex.: TJRN) publicam centenas de vezes a
        mesma intimação no mesmo dia para o mesmo processo. Classificamos
        apenas um representante dessa coorte e economizamos tokens.

        Os registros "irmãos" (mesmo processo + mesmo dia) NÃO são retornados,
        mas podem ter a classificação propagada depois, via apply_batch_results,
        se desejarmos (não implementado aqui — ficam como NOVO para não
        poluir a dedup e o usuário decide o que fazer com eles).
        """
        query = (
            self.db.query(PublicationRecord)
            .filter(PublicationRecord.status == RECORD_STATUS_NEW)
            .filter(PublicationRecord.is_duplicate == False)  # noqa: E712
            .filter(PublicationRecord.description.isnot(None))
            .filter(PublicationRecord.description != "")
            .filter(PublicationRecord.category.is_(None))
        )
        if linked_office_id is not None:
            query = query.filter(PublicationRecord.linked_office_id == linked_office_id)
        if only_unlinked:
            query = query.filter(PublicationRecord.linked_lawsuit_id.is_(None))
        query = query.order_by(PublicationRecord.id)

        # Anti-duplicacao: tira records que JA estao em batch ativo
        # (submetido / em processamento / pronto pra aplicar). Sem isso,
        # se o scheduler dispara antes do batch anterior terminar a
        # Anthropic, o collect pega os mesmos records e cria batch
        # duplicado — gasta 2x token e duplica errors.
        # Bug raiz observado em 2026-05-08: lotes #29 e #30 com 516
        # records identicos com 1 minuto de diferenca.
        active_record_ids: set[int] = set()
        active_batches = (
            self.db.query(PublicationBatchClassification.record_ids)
            .filter(
                PublicationBatchClassification.status.in_([
                    PUB_BATCH_STATUS_SUBMITTED,
                    PUB_BATCH_STATUS_IN_PROGRESS,
                    PUB_BATCH_STATUS_READY,
                ])
            )
            .all()
        )
        for row in active_batches:
            if row[0]:
                active_record_ids.update(row[0])
        if active_record_ids:
            query = query.filter(~PublicationRecord.id.in_(active_record_ids))
            logger.info(
                "collect_pending_records: %d records ja em batch ativo "
                "foram excluidos da fila",
                len(active_record_ids),
            )

        all_records = query.all()

        # Deduplicação: uma publicação por (processo, dia)
        seen_keys: set[tuple] = set()
        deduped: list[PublicationRecord] = []
        dup_skipped = 0

        for rec in all_records:
            key = self._dedup_key(rec)
            if key in seen_keys:
                dup_skipped += 1
                continue
            seen_keys.add(key)
            deduped.append(rec)
            if limit and len(deduped) >= limit:
                break

        if dup_skipped:
            logger.info(
                "Deduplicação: %d registros ignorados por (processo, dia) já "
                "representados no batch.",
                dup_skipped,
            )
        return deduped

    def _propagate_to_siblings(
        self,
        rec: PublicationRecord,
        category: str,
        subcategory: Optional[str],
        polo: Optional[str],
        audiencia_data: Optional[str] = None,
        audiencia_hora: Optional[str] = None,
        audiencia_link: Optional[str] = None,
    ) -> int:
        """
        Copia a classificação do registro `rec` para os registros "irmãos"
        que foram descartados pela deduplicação (mesmo processo + mesmo dia
        de publicação e status ainda NOVO).

        Retorna quantos registros foram atualizados.
        """
        sibling_query = (
            self.db.query(PublicationRecord)
            .filter(PublicationRecord.id != rec.id)
            .filter(PublicationRecord.status == RECORD_STATUS_NEW)
            .filter(PublicationRecord.category.is_(None))
        )
        # Mesmo processo
        if rec.linked_lawsuit_cnj:
            sibling_query = sibling_query.filter(
                PublicationRecord.linked_lawsuit_cnj == rec.linked_lawsuit_cnj
            )
        elif rec.linked_lawsuit_id:
            sibling_query = sibling_query.filter(
                PublicationRecord.linked_lawsuit_id == rec.linked_lawsuit_id
            )
        else:
            return 0

        siblings = sibling_query.all()
        if not siblings:
            return 0

        # Compara dia (extraído do publication_date) em Python para evitar
        # complicações com dialeto SQL em strings ISO
        target_day = self._dedup_key(rec)[1]
        count = 0
        for sib in siblings:
            if self._dedup_key(sib)[1] != target_day:
                continue
            sib.category = category
            sib.subcategory = subcategory
            sib.polo = polo
            sib.audiencia_data = audiencia_data
            sib.audiencia_hora = audiencia_hora
            sib.audiencia_link = audiencia_link
            sib.status = RECORD_STATUS_CLASSIFIED
            count += 1
        return count

    @staticmethod
    def _dedup_key(rec: PublicationRecord) -> tuple:
        """
        Retorna a chave de deduplicação: (processo, dia_da_publicação).

        - Se houver CNJ (linked_lawsuit_cnj), usa ele como identificador do
          processo; caso contrário cai no linked_lawsuit_id e, se ambos
          faltarem, usa o próprio id do registro (o que nunca colide).
        - O dia é extraído do publication_date (ISO). Se o campo não existir,
          também usa o próprio id para nunca colidir.
        """
        proc_key = (
            rec.linked_lawsuit_cnj
            or (f"lid-{rec.linked_lawsuit_id}" if rec.linked_lawsuit_id else None)
            or f"rec-{rec.id}"
        )
        day_key: str
        if rec.publication_date:
            try:
                # Normaliza dias: aceita ISO com timezone e YYYY-MM-DD
                raw = rec.publication_date.replace("Z", "+00:00")
                day_key = datetime.fromisoformat(raw).date().isoformat()
            except Exception:
                day_key = rec.publication_date[:10]
        else:
            day_key = f"nodate-{rec.id}"
        return (proc_key, day_key)

    async def submit_batch(
        self,
        records: List[PublicationRecord],
        requested_by_email: Optional[str] = None,
    ) -> PublicationBatchClassification:
        """
        Monta o lote e envia para a Anthropic Batch API.

        Args:
            records: publicações a classificar. Cada uma vira um item do batch,
                      com custom_id = str(record.id).
            requested_by_email: email do usuário que disparou (para auditoria).

        Returns:
            PublicationBatchClassification persistido com status ENVIADO.
        """
        if not records:
            raise ValueError("Nenhum registro para classificar.")

        # Pré-carrega prompts customizados por escritório (com overrides + feedback).
        # Cache key = (office_id, is_unlinked) pra não misturar prompts.
        office_prompts: dict[tuple, str] = {}
        feedback_cache: dict[int, str] = {}
        office_ids = {rec.linked_office_id for rec in records if rec.linked_office_id}
        # Polo configurado por escritorio (Admin > Polo dos Escritorios).
        # 'ativo'/'passivo' travam a arvore da IA no polo certo; 'ambos' ou
        # ausente -> None (sem filtro). Sem isso o batch classificava cego ao
        # polo e gravava a categoria do lado errado (ex.: BB/Autor caindo na
        # cat do passivo, que nao tem template do escritorio -> 'Sem template').
        office_polo_by_id: dict[int, Optional[str]] = {}
        office_path_by_id: dict[int, Optional[str]] = {}
        if office_ids:
            try:
                from app.models.legal_one import LegalOneOffice
                for off in (
                    self.db.query(LegalOneOffice)
                    .filter(LegalOneOffice.external_id.in_(office_ids))
                    .all()
                ):
                    raw = (getattr(off, "polo_scope", None) or "").strip().lower()
                    office_polo_by_id[off.external_id] = (
                        raw if raw in ("ativo", "passivo") else None
                    )
                    office_path_by_id[off.external_id] = getattr(off, "path", None)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Falha ao carregar polo dos escritorios: %s", exc)

        def _feedback_for(oid: int) -> str:
            if oid not in feedback_cache:
                try:
                    feedback_cache[oid] = build_feedback_examples(
                        self.db, oid if oid else None,
                        office_polo=office_polo_by_id.get(oid),
                    )
                except Exception as exc:
                    logger.warning("Falha ao carregar feedbacks do escritório %s: %s", oid, exc)
                    feedback_cache[oid] = ""
            return feedback_cache[oid]

        for oid in office_ids:
            try:
                excluded, custom = load_office_overrides(self.db, oid)
                fb = _feedback_for(oid)
                for unlinked in (False, True):
                    # Modo arvore enxuta (fase 13): sempre passamos oid
                    # pra build_system_prompt_for_office filtrar a arvore
                    # pelos templates do escritorio. Mesmo sem overrides
                    # explicitos, o filtro template-driven entra em acao
                    # quando o setting esta ativo (default true desde
                    # tax009).
                    office_prompts[(oid, unlinked)] = build_system_prompt_for_office(
                        excluded or None, custom or None,
                        is_unlinked=unlinked,
                        feedback_examples=fb,
                        polo_scope=office_polo_by_id.get(oid),
                        office_external_id=oid,
                    )
            except Exception as exc:
                # Log de ERRO (nao warning) com stack trace pra rastrear
                # falha real. Antes era warning silencioso e o build
                # caia no fallback global (sem filtro template-driven),
                # causando IA classificar em cats sem template do
                # escritorio — bug observado em 2026-05-08.
                logger.error(
                    "build_office_prompt.failed oid=%s erro=%s — "
                    "office_prompts NAO foi populado, fallback vai usar "
                    "prompt sem filtro template-driven se nao reconstruir",
                    oid, exc, exc_info=True,
                )
        # Prompt base para publicações sem escritório
        fb_global = _feedback_for(0)
        office_prompts[(0, False)] = build_system_prompt_for_office(
            feedback_examples=fb_global,
        ) if fb_global else SYSTEM_PROMPT
        office_prompts[(0, True)] = build_system_prompt_for_office(
            is_unlinked=True, feedback_examples=fb_global,
        )

        # Monta as requisições do batch
        batch_requests = []
        record_ids: list[int] = []
        for rec in records:
            text = (rec.description or "").strip()
            if not text:
                continue
            # Trunca textos muito longos para economizar tokens e não
            # estourar o context window da Haiku
            if len(text) > MAX_PUBLICATION_TEXT_CHARS:
                text = text[:MAX_PUBLICATION_TEXT_CHARS] + "\n[...texto truncado]"

            # Usa prompt específico do escritório + flag unlinked
            is_unlinked = rec.linked_lawsuit_id is None
            oid = rec.linked_office_id or 0
            cache_key = (oid, is_unlinked)
            prompt = office_prompts.get(cache_key)
            if prompt is None and oid:
                # Cache miss pra escritorio especifico (build_system_prompt
                # falhou la em cima OU oid nao estava em office_ids). Em
                # vez de cair no fallback global (sem filtro template-driven
                # — bug raiz que vazava cats sem template do escritorio
                # pra IA), RECONSTROI o prompt aqui mesmo passando oid.
                # Custo: build extra por record do escritorio sem cache,
                # mas garante que IA recebe arvore enxuta correta.
                logger.warning(
                    "office_prompt.cache_miss oid=%s unlinked=%s — "
                    "reconstruindo prompt sob demanda",
                    oid, is_unlinked,
                )
                try:
                    prompt = build_system_prompt_for_office(
                        is_unlinked=is_unlinked,
                        polo_scope=office_polo_by_id.get(oid),
                        office_external_id=oid,
                    )
                    office_prompts[cache_key] = prompt  # cacheia pra proxima
                except Exception as exc:
                    logger.error(
                        "office_prompt.rebuild_failed oid=%s erro=%s — "
                        "caindo no fallback global SEM filtro de templates",
                        oid, exc, exc_info=True,
                    )
                    prompt = office_prompts.get((0, is_unlinked), SYSTEM_PROMPT)
            elif prompt is None:
                # oid=0 (publicacao sem escritorio): fallback global e' OK,
                # nao tem como filtrar por templates de escritorio.
                prompt = office_prompts.get((0, is_unlinked), SYSTEM_PROMPT)

            user_msg = build_user_message(
                rec.linked_lawsuit_cnj or "", text,
                office_path=office_path_by_id.get(rec.linked_office_id or 0),
                office_polo=office_polo_by_id.get(rec.linked_office_id or 0),
            )
            batch_requests.append(
                self.ai.build_batch_request(
                    custom_id=str(rec.id),
                    system_prompt=prompt,
                    user_message=user_msg,
                )
            )
            record_ids.append(rec.id)

        if not batch_requests:
            raise ValueError("Nenhum registro com texto útil para classificar.")

        logger.info(
            "Enviando batch de classificação: %d publicações (solicitante=%s)",
            len(batch_requests),
            requested_by_email or "-",
        )

        # Envia para a Anthropic
        try:
            response = await self.ai.submit_batch(batch_requests)
        except Exception as exc:
            # Persiste um registro de falha para auditoria
            batch = PublicationBatchClassification(
                status=PUB_BATCH_STATUS_FAILED,
                total_records=len(batch_requests),
                record_ids=record_ids,
                model_used=self.ai.model,
                requested_by_email=requested_by_email,
                error_message=str(exc)[:2000],
            )
            self.db.add(batch)
            self.db.commit()
            self.db.refresh(batch)
            # Alerta por e-mail — o batch (manual OU job noturno) falhou ao ser
            # criado na Anthropic. Best-effort: nunca derruba o fluxo.
            _alertar_falha_batch(batch, str(exc), requested_by_email)
            raise

        # Persiste o registro local do batch
        batch = PublicationBatchClassification(
            anthropic_batch_id=response.get("id"),
            anthropic_status=response.get("processing_status"),
            status=PUB_BATCH_STATUS_SUBMITTED,
            total_records=len(batch_requests),
            record_ids=record_ids,
            model_used=self.ai.model,
            requested_by_email=requested_by_email,
            submitted_at=datetime.now(timezone.utc),
        )
        self.db.add(batch)
        self.db.commit()
        self.db.refresh(batch)

        logger.info(
            "Batch criado: local_id=%s, anthropic_id=%s, itens=%d",
            batch.id,
            batch.anthropic_batch_id,
            batch.total_records,
        )
        return batch

    # ──────────────────────────────────────────────────────────────────
    # Status & polling
    # ──────────────────────────────────────────────────────────────────

    async def refresh_batch_status(
        self, batch: PublicationBatchClassification
    ) -> PublicationBatchClassification:
        """
        Consulta o status atual do batch na Anthropic e atualiza o registro local.
        Não baixa nem aplica resultados — apenas atualiza contadores.
        """
        if not batch.anthropic_batch_id:
            raise ValueError(f"Batch {batch.id} sem anthropic_batch_id.")

        try:
            data = await self.ai.get_batch_status(batch.anthropic_batch_id)
        except Exception as exc:
            logger.warning("Falha ao consultar batch %s: %s", batch.anthropic_batch_id, exc)
            raise

        batch.anthropic_status = data.get("processing_status")

        # Contadores vêm em request_counts
        counts = data.get("request_counts", {}) or {}
        batch.succeeded_count = counts.get("succeeded", 0)
        batch.errored_count = counts.get("errored", 0)
        batch.expired_count = counts.get("expired", 0)
        batch.canceled_count = counts.get("canceled", 0)

        # Se terminou, guarda a URL dos resultados e atualiza status interno
        if data.get("processing_status") == ANTHROPIC_STATUS_ENDED:
            batch.results_url = data.get("results_url")
            if batch.status == PUB_BATCH_STATUS_SUBMITTED or \
                    batch.status == PUB_BATCH_STATUS_IN_PROGRESS:
                batch.status = PUB_BATCH_STATUS_READY
            if not batch.ended_at:
                ended_at_str = data.get("ended_at")
                if ended_at_str:
                    try:
                        batch.ended_at = datetime.fromisoformat(
                            ended_at_str.replace("Z", "+00:00")
                        )
                    except Exception:
                        batch.ended_at = datetime.now(timezone.utc)
                else:
                    batch.ended_at = datetime.now(timezone.utc)
        else:
            # Ainda processando
            if batch.status == PUB_BATCH_STATUS_SUBMITTED:
                batch.status = PUB_BATCH_STATUS_IN_PROGRESS

        self.db.commit()
        self.db.refresh(batch)
        return batch

    # ──────────────────────────────────────────────────────────────────
    # Apply results
    # ──────────────────────────────────────────────────────────────────

    async def apply_batch_results(
        self, batch: PublicationBatchClassification
    ) -> dict:
        """
        Baixa os resultados do batch e atualiza os PublicationRecord no banco.

        Pré-condição: batch deve estar com status PRONTO (results_url preenchido).

        Returns:
            dict com contadores: {"succeeded": N, "failed": N, "skipped": N}
        """
        if not batch.results_url:
            # Tenta atualizar o status primeiro
            await self.refresh_batch_status(batch)
            if not batch.results_url:
                raise ValueError(
                    f"Batch {batch.id} ainda não tem results_url. "
                    f"Status Anthropic: {batch.anthropic_status}"
                )

        logger.info(
            "Baixando resultados do batch %s (%d itens)",
            batch.anthropic_batch_id,
            batch.total_records,
        )

        results = await self.ai.get_batch_results(batch.results_url)

        succeeded = 0
        failed = 0
        skipped = 0
        error_details: dict[str, str] = {}

        for item in results:
            custom_id = item.get("custom_id")
            if not custom_id:
                skipped += 1
                continue

            try:
                record_id = int(custom_id)
            except (TypeError, ValueError):
                skipped += 1
                continue

            rec = (
                self.db.query(PublicationRecord)
                .filter(PublicationRecord.id == record_id)
                .first()
            )
            if not rec:
                skipped += 1
                continue

            # Extrai classificação
            try:
                classification = (
                    AnthropicClassifierClient.extract_classification_from_batch_result(item)
                )
            except Exception as exc:
                err_msg = str(exc)[:500]
                logger.warning(
                    "Falha ao processar item %s do batch: %s", custom_id, exc
                )
                rec.status = RECORD_STATUS_ERROR
                error_details[custom_id] = f"Extração falhou: {err_msg}"
                failed += 1
                continue

            # Schema cross-field: zera audiência se categoria não é
            # "Audiência Agendada", valida formatos. Erro estrutural
            # (sem categoria) é fatal pra esse item.
            try:
                clean = validate_response(classification)
            except ResponseSchemaError as exc:
                logger.warning(
                    "Schema inválido #%s: %s — payload=%s",
                    rec.id, exc, str(classification)[:300],
                )
                rec.status = RECORD_STATUS_ERROR
                error_details[custom_id] = f"Schema inválido: {exc}"
                failed += 1
                continue

            if clean.warnings:
                logger.warning(
                    "Schema warnings #%s: %s",
                    rec.id, "; ".join(clean.warnings),
                )

            # Auto-corrige inversões comuns (subcategoria emitida como categoria)
            cat_fixed, sub_fixed = repair_classification(
                clean.categoria, clean.subcategoria
            )
            if (cat_fixed, sub_fixed) != (clean.categoria, clean.subcategoria):
                logger.info(
                    "Classificação auto-corrigida #%s: (%s/%s) → (%s/%s)",
                    rec.id,
                    clean.categoria, clean.subcategoria,
                    cat_fixed, sub_fixed,
                )
            cat, sub = cat_fixed, sub_fixed
            # Mantém o dict `classification` em sincronia pra o registro de
            # _extra_classifications/raw e para a propagação de irmãos.
            classification["categoria"] = cat
            classification["subcategoria"] = sub
            classification["polo"] = clean.polo
            classification["audiencia_data"] = clean.audiencia_data
            classification["audiencia_hora"] = clean.audiencia_hora
            classification["audiencia_link"] = clean.audiencia_link

            polo = clean.polo
            aud_data = clean.audiencia_data
            aud_hora = clean.audiencia_hora
            aud_link = clean.audiencia_link

            if cat and validate_classification(cat, sub):
                rec.category = cat
                rec.subcategory = sub
                rec.polo = polo
                rec.audiencia_data = aud_data
                rec.audiencia_hora = aud_hora
                rec.audiencia_link = aud_link
                # Natureza do processo: só pra publicações sem pasta vinculada
                if rec.linked_lawsuit_id is None:
                    rec.natureza_processo = clean.natureza_processo
                # Múltiplas classificações
                extra = classification.get("_extra_classifications")
                if extra:
                    all_clf = [classification] + extra
                    rec.classifications = [
                        {k: v for k, v in c.items() if k != "_extra_classifications"}
                        for c in all_clf
                    ]
                rec.status = RECORD_STATUS_CLASSIFIED
                succeeded += 1
                logger.debug(
                    "Classificado #%s → %s / %s (polo=%s, aud=%s %s, nat=%s)",
                    rec.id, cat, sub, polo, aud_data, aud_hora,
                    rec.natureza_processo if rec.linked_lawsuit_id is None else "-",
                )
                # Propaga a classificação para os "irmãos" (mesmo processo,
                # mesmo dia) que foram descartados pela deduplicação.
                propagated = self._propagate_to_siblings(
                    rec, cat, sub, polo, aud_data, aud_hora, aud_link,
                )
                if propagated:
                    logger.debug(
                        "Propagado para %d registros irmãos de #%s",
                        propagated, rec.id,
                    )
            else:
                logger.warning(
                    "Classificação inválida #%s: cat=%s sub=%s", rec.id, cat, sub
                )
                rec.status = RECORD_STATUS_ERROR
                error_details[custom_id] = f"Classificação inválida: cat={cat}, sub={sub}"
                failed += 1

        self.db.commit()

        # Atualiza o batch
        batch.status = PUB_BATCH_STATUS_APPLIED
        batch.applied_at = datetime.now(timezone.utc)
        batch.succeeded_count = succeeded
        batch.errored_count = failed
        if error_details:
            batch.error_details = error_details
        self.db.commit()

        # Monta propostas de tarefa para todos os registros classificados com sucesso
        classified_ids = {
            int(item["custom_id"])
            for item in results
            if item.get("custom_id")
        }
        classified_records = (
            self.db.query(PublicationRecord)
            .filter(PublicationRecord.id.in_(classified_ids), PublicationRecord.category.isnot(None))
            .all()
        )
        if classified_records:
            try:
                from app.services.publication_search_service import PublicationSearchService
                svc = PublicationSearchService.__new__(PublicationSearchService)
                svc.db = self.db
                svc._build_task_proposals(classified_records)
                logger.info(
                    "Propostas de tarefa montadas para %d registros do batch %s",
                    len(classified_records), batch.anthropic_batch_id,
                )
            except Exception as exc:
                logger.warning("Falha ao montar propostas de tarefa: %s", exc)

        summary = {
            "succeeded": succeeded,
            "failed": failed,
            "skipped": skipped,
            "total": len(results),
        }
        logger.info(
            "Batch %s aplicado: %s",
            batch.anthropic_batch_id,
            summary,
        )
        return summary

    # ──────────────────────────────────────────────────────────────────
    # Consultas auxiliares
    # ──────────────────────────────────────────────────────────────────

    def get_batch(self, batch_id: int) -> Optional[PublicationBatchClassification]:
        return (
            self.db.query(PublicationBatchClassification)
            .filter(PublicationBatchClassification.id == batch_id)
            .first()
        )

    def list_batches(
        self, limit: int = 50
    ) -> List[PublicationBatchClassification]:
        return (
            self.db.query(PublicationBatchClassification)
            .order_by(PublicationBatchClassification.created_at.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def batch_to_dict(batch: PublicationBatchClassification) -> dict:
        return {
            "id": batch.id,
            "anthropic_batch_id": batch.anthropic_batch_id,
            "status": batch.status,
            "anthropic_status": batch.anthropic_status,
            "total_records": batch.total_records,
            "succeeded_count": batch.succeeded_count or 0,
            "errored_count": batch.errored_count or 0,
            "expired_count": batch.expired_count or 0,
            "canceled_count": batch.canceled_count or 0,
            "model_used": batch.model_used,
            "requested_by_email": batch.requested_by_email,
            "error_message": batch.error_message,
            "created_at": batch.created_at.isoformat() if batch.created_at else None,
            "submitted_at": batch.submitted_at.isoformat() if batch.submitted_at else None,
            "ended_at": batch.ended_at.isoformat() if batch.ended_at else None,
            "applied_at": batch.applied_at.isoformat() if batch.applied_at else None,
            "error_details": batch.error_details,
        }

    def collect_errored_records_from_batch(
        self, batch: PublicationBatchClassification
    ) -> List[PublicationRecord]:
        """
        Coleta os registros que falharam em um batch anterior
        para permitir reprocessamento.

        Tenta primeiro usar error_details (mapeamento custom_id → motivo).
        Isso também cobre batches antigos em que a classificação inválida
        ficou em error_details, mas o registro permaneceu NOVO.
        Se o envio inteiro falhou antes de haver resultados, usa record_ids.
        Como último fallback para batches antigos, usa record_ids + status ERRO.
        """
        use_error_details = bool(batch.error_details)
        use_failed_record_ids = (
            batch.status == PUB_BATCH_STATUS_FAILED
            and bool(batch.record_ids)
        )

        if use_error_details:
            # Caminho primário: usa o mapeamento detalhado de erros
            errored_ids = []
            for record_id_str in batch.error_details.keys():
                try:
                    errored_ids.append(int(record_id_str))
                except (TypeError, ValueError):
                    continue
        elif batch.record_ids:
            # Fallback: batch falhou inteiro ou batch antigo sem error_details.
            errored_ids = [int(rid) for rid in batch.record_ids if rid]
        else:
            return []

        if not errored_ids:
            return []

        query = (
            self.db.query(PublicationRecord)
            .filter(PublicationRecord.id.in_(errored_ids))
            .filter(PublicationRecord.description.isnot(None))
            .filter(PublicationRecord.description != "")
        )
        if use_error_details or use_failed_record_ids:
            query = query.filter(
                PublicationRecord.status.in_([RECORD_STATUS_ERROR, RECORD_STATUS_NEW])
            )
        else:
            query = query.filter(PublicationRecord.status == RECORD_STATUS_ERROR)
        records = query.all()

        # Reset status to NEW for reprocessing
        for rec in records:
            rec.category = None
            rec.subcategory = None
            rec.polo = None
            rec.audiencia_data = None
            rec.audiencia_hora = None
            rec.audiencia_link = None
            rec.classifications = None
            rec.status = RECORD_STATUS_NEW
        self.db.commit()

        return records
