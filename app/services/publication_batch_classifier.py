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
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.classification import CLF_ITEM_FAILED, CLF_ITEM_SUCCESS
from app.models.publication_batch import (
    ANTHROPIC_STATUS_ENDED,
    PUB_BATCH_STATUS_APPLIED,
    PUB_BATCH_STATUS_FAILED,
    PUB_BATCH_STATUS_IN_PROGRESS,
    PUB_BATCH_STATUS_READY,
    PUB_BATCH_STATUS_SUBMITTED,
    PUB_BATCH_STATUS_WARMING,
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
                    # AQUECENDO entra aqui: o lote ainda nao partiu, mas os
                    # registros JA estao comprometidos com ele. Sem isto, uma
                    # segunda coleta durante o aquecimento pegaria os mesmos
                    # registros e criaria lote duplicado — o bug dos lotes
                    # #29/#30, agora com uma janela nova pra acontecer.
                    PUB_BATCH_STATUS_WARMING,
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
        quem_pratica_ato: Optional[str] = None,
        exige_providencia_nossa: Optional[bool] = None,
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
            # pub010 — irmão é a MESMA publicação deduplicada, então de quem é
            # o ato é o mesmo. Não propagar deixaria o irmão fora da r6 sem
            # motivo e criaria assimetria entre cópias do mesmo texto.
            sib.quem_pratica_ato = quem_pratica_ato
            sib.exige_providencia_nossa = exige_providencia_nossa
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

    def montar_requisicoes(
        self, records: List[PublicationRecord],
    ) -> tuple[list[tuple[str, str, str]], list[int]]:
        """Monta (custom_id, system_prompt, user_message) para cada registro.

        Extraído de `submit_batch` porque a promoção do lote aquecido precisa
        RECONSTRUIR as mesmas requisições a partir de `record_ids` — persistir
        o payload inteiro seria guardar megabytes de texto de publicação no
        banco durante o aquecimento.

        Determinístico: os mesmos registros produzem os mesmos prefixos, que é
        o que faz o aquecimento valer para o lote real.
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

        # Monta as requisições do batch. Guarda (custom_id, system, user) e só
        # materializa o payload depois de decidir se o cache entra.
        pendentes: list[tuple[str, str, str]] = []
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
            pendentes.append((str(rec.id), prompt, user_msg))
            record_ids.append(rec.id)

        if not pendentes:
            raise ValueError("Nenhum registro com texto útil para classificar.")

        return pendentes, record_ids

    async def submit_batch(
        self,
        records: List[PublicationRecord],
        requested_by_email: Optional[str] = None,
    ) -> PublicationBatchClassification:
        """Envia o lote, aquecendo o cache antes quando o caching está ligado.

        DUAS FASES, como a doc da Batches API prescreve. Com o cache ligado
        este método NÃO envia o lote real: dispara o batch de aquecimento e
        devolve o registro em AQUECENDO. Quem termina é `promover_aquecidos`,
        chamado pelo poller — esperar o aquecimento leva minutos e o endpoint
        HTTP não pode ficar pendurado.

        Com o cache desligado, envia direto (comportamento anterior).
        """
        pendentes, record_ids = self.montar_requisicoes(records)

        if not settings.classifier_prompt_cache_enabled:
            return await self._enviar_lote(
                pendentes, record_ids, requested_by_email, usar_cache=False,
            )
        return await self._iniciar_aquecimento(
            pendentes, record_ids, requested_by_email,
        )

    async def _iniciar_aquecimento(
        self,
        pendentes: list[tuple[str, str, str]],
        record_ids: list[int],
        requested_by_email: Optional[str],
    ) -> PublicationBatchClassification:
        """Fase 1: dispara o batch de aquecimento e persiste o lote AQUECENDO."""
        distintos = list(dict.fromkeys(p for _, p, _ in pendentes))
        try:
            resp = await self.ai.submit_batch(self.ai.build_warm_requests(distintos))
            warm_id = resp.get("id")
            if not warm_id:
                raise ValueError("resposta do aquecimento veio sem id de batch")
        except Exception as exc:  # noqa: BLE001
            # Aquecer é otimização de custo, não requisito. Falhou, o lote sai
            # SEM cache: o caro é a fila parar, não o token.
            logger.error(
                "Aquecimento em lote falhou (%s) — enviando SEM cache.", exc,
            )
            return await self._enviar_lote(
                pendentes, record_ids, requested_by_email, usar_cache=False,
            )

        batch = PublicationBatchClassification(
            status=PUB_BATCH_STATUS_WARMING,
            total_records=len(pendentes),
            record_ids=record_ids,
            model_used=self.ai.model,
            requested_by_email=requested_by_email,
            warm_batch_id=warm_id,
            warm_started_at=datetime.now(timezone.utc),
            warm_prefixos=len(distintos),
        )
        self.db.add(batch)
        self.db.commit()
        self.db.refresh(batch)
        logger.info(
            "Aquecimento iniciado: local_id=%s warm_batch=%s prefixos=%d "
            "(o lote real com %d publicações sai quando o aquecimento fechar)",
            batch.id, warm_id, len(distintos), len(pendentes),
        )
        return batch

    async def promover_aquecidos(self) -> dict:
        """Fase 2: para cada lote AQUECENDO, envia o lote real quando der.

        Três desfechos por lote:
          - aquecimento concluiu -> envia COM cache (o caso que queremos)
          - estourou a janela    -> envia SEM cache
          - aquecimento falhou   -> envia SEM cache

        Nunca deixa lote parado em AQUECENDO: esse estado sombreia os registros
        na coleta, e foi assim que o batch 114 escondeu 521 publicações por 20
        dias.
        """
        aquecendo = (
            self.db.query(PublicationBatchClassification)
            .filter(PublicationBatchClassification.status == PUB_BATCH_STATUS_WARMING)
            .all()
        )
        if not aquecendo:
            return {"promovidos": 0, "sem_cache": 0, "aguardando": 0}

        promovidos = sem_cache = aguardando = 0
        for batch in aquecendo:
            situacao = None
            try:
                st = await self.ai.get_batch_status(batch.warm_batch_id)
                situacao = st.get("processing_status")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Lote %s: falha ao consultar o aquecimento %s (%s).",
                    batch.id, batch.warm_batch_id, exc,
                )

            idade_min = 0.0
            if batch.warm_started_at:
                inicio = batch.warm_started_at
                if inicio.tzinfo is None:
                    inicio = inicio.replace(tzinfo=timezone.utc)
                idade_min = (datetime.now(timezone.utc) - inicio).total_seconds() / 60

            estourou = idade_min > settings.classifier_prompt_cache_warm_timeout_min
            if situacao != "ended" and not estourou:
                aguardando += 1
                continue

            com_cache = situacao == "ended"
            if not com_cache:
                logger.error(
                    "Lote %s: aquecimento em %s após %.1f min — enviando SEM "
                    "cache pra não segurar a fila.",
                    batch.id, situacao or "erro de consulta", idade_min,
                )

            registros = (
                self.db.query(PublicationRecord)
                .filter(PublicationRecord.id.in_(batch.record_ids or []))
                .all()
            )
            if not registros:
                batch.status = PUB_BATCH_STATUS_FAILED
                batch.error_message = "registros do lote sumiram durante o aquecimento"
                self.db.commit()
                continue

            try:
                pendentes, record_ids = self.montar_requisicoes(registros)
                await self._enviar_lote(
                    pendentes, record_ids, batch.requested_by_email,
                    usar_cache=com_cache, batch=batch,
                )
                batch.warm_ended_at = datetime.now(timezone.utc)
                self.db.commit()
                if com_cache:
                    promovidos += 1
                else:
                    sem_cache += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception("Lote %s: falha ao promover (%s).", batch.id, exc)
                batch.status = PUB_BATCH_STATUS_FAILED
                batch.error_message = str(exc)[:2000]
                self.db.commit()
                _alertar_falha_batch(batch, str(exc), batch.requested_by_email)

        if promovidos or sem_cache:
            logger.info(
                "Promoção de aquecidos: %d com cache, %d sem cache, %d ainda "
                "aquecendo.", promovidos, sem_cache, aguardando,
            )
        return {"promovidos": promovidos, "sem_cache": sem_cache,
                "aguardando": aguardando}

    async def _enviar_lote(
        self,
        pendentes: list[tuple[str, str, str]],
        record_ids: list[int],
        requested_by_email: Optional[str],
        usar_cache: bool,
        batch: Optional[PublicationBatchClassification] = None,
    ) -> PublicationBatchClassification:
        """Envia o lote real. `batch` preenchido = promoção de um AQUECENDO."""
        # ── Prompt caching ────────────────────────────────────────────────
        # 92% da entrada deste lote é system prompt (medido no lote 146), e
        # dentro do lote cada variante é byte-idêntica porque `office_prompts`
        # memoiza por (escritório, sem-pasta). Cacheando o prefixo, a segunda
        # requisição em diante paga 10% pelo mesmo texto.
        #
        # O aquecimento precisa vir ANTES do envio: na Batches API o acerto é
        # "melhor esforço" — as 600+ requisições rodam concorrentes e nada
        # O aquecimento NAO acontece mais aqui. Ele e' a fase 1
        # (`_iniciar_aquecimento`), roda como batch proprio e precisa CONCLUIR
        # antes deste envio - e' isso que torna a entrada de cache visivel para
        # os workers do batch. Aquecer com chamadas sincronas logo antes de
        # submeter, como faziamos, nao produzia acerto: medido nos lotes
        # 147-149, o lote gravava milhoes de tokens mesmo com 8/8 prefixos
        # aquecidos, e o cache saiu mais caro que nao cachear.
        batch_requests = [
            self.ai.build_batch_request(
                custom_id=cid, system_prompt=p, user_message=u, cache=usar_cache,
            )
            for cid, p, u in pendentes
        ]

        logger.info(
            "Enviando batch de classificação: %d publicações "
            "(solicitante=%s, cache=%s, aquecido=%s)",
            len(batch_requests),
            requested_by_email or "-",
            "ligado" if usar_cache else "desligado",
            getattr(batch, "warm_batch_id", None) or "não",
        )

        # Envia para a Anthropic
        try:
            response = await self.ai.submit_batch(batch_requests)
        except Exception as exc:
            # Persiste um registro de falha para auditoria (reusando o
            # registro do aquecimento quando é promoção).
            if batch is None:
                batch = PublicationBatchClassification(
                    total_records=len(batch_requests),
                    record_ids=record_ids,
                    model_used=self.ai.model,
                    requested_by_email=requested_by_email,
                )
                self.db.add(batch)
            batch.status = PUB_BATCH_STATUS_FAILED
            batch.error_message = str(exc)[:2000]
            self.db.commit()
            self.db.refresh(batch)
            # Alerta por e-mail — o batch (manual OU job noturno) falhou ao ser
            # criado na Anthropic. Best-effort: nunca derruba o fluxo.
            _alertar_falha_batch(batch, str(exc), requested_by_email)
            raise

        # Persiste o registro local do batch. Na promoção o registro JÁ
        # existe (nasceu AQUECENDO) — atualizar em vez de criar mantém uma
        # linha por lote no histórico, com o id do aquecimento junto.
        if batch is None:
            batch = PublicationBatchClassification(
                total_records=len(batch_requests),
                record_ids=record_ids,
                model_used=self.ai.model,
                requested_by_email=requested_by_email,
            )
            self.db.add(batch)
        batch.anthropic_batch_id = response.get("id")
        batch.anthropic_status = response.get("processing_status")
        batch.status = PUB_BATCH_STATUS_SUBMITTED
        batch.total_records = len(batch_requests)
        batch.record_ids = record_ids
        batch.submitted_at = datetime.now(timezone.utc)
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

    async def recover_stale_batches(self, max_age_minutes: int = 60) -> dict:
        """Revisita batches não-terminais antigos e resolve cada um.

        Existe por causa do batch 114 (17/07/2026): o processo morreu no meio
        do polling (redeploy na janela), o batch ficou EM_PROCESSAMENTO pra
        sempre e as 521 publicações dele ficaram SOMBREADAS — o
        collect_pending_records exclui, corretamente, registro que já está em
        batch ativo, então elas nunca mais entraram em rodada nenhuma. Vinte
        dias invisíveis, sem alerta, até o operador estranhar a tela. Nada no
        sistema revisitava batch não-terminal; agora isto roda antes de toda
        classificação agendada.

        Para cada batch pendurado: se a Anthropic terminou, aplica os
        resultados (classificação JÁ PAGA — o caso 114 recuperou as 521 sem
        gastar um token novo); se ainda processa de verdade, deixa; se a
        consulta falhar (batch expirado/apagado na Anthropic), marca FALHA —
        isso solta os registros de volta pra fila, que é o comportamento
        correto: melhor reclassificar do que esconder.
        """
        limite = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
        pendurados = (
            self.db.query(PublicationBatchClassification)
            .filter(
                PublicationBatchClassification.status.in_([
                    # AQUECENDO entra aqui: o lote ainda nao partiu, mas os
                    # registros JA estao comprometidos com ele. Sem isto, uma
                    # segunda coleta durante o aquecimento pegaria os mesmos
                    # registros e criaria lote duplicado — o bug dos lotes
                    # #29/#30, agora com uma janela nova pra acontecer.
                    PUB_BATCH_STATUS_WARMING,
                    PUB_BATCH_STATUS_SUBMITTED,
                    PUB_BATCH_STATUS_IN_PROGRESS,
                    PUB_BATCH_STATUS_READY,
                ]),
                PublicationBatchClassification.created_at < limite,
            )
            .all()
        )
        resumo = {"verificados": len(pendurados), "aplicados": 0,
                  "liberados": 0, "em_andamento": 0}
        for batch in pendurados:
            try:
                batch = await self.refresh_batch_status(batch)
            except Exception as exc:  # noqa: BLE001
                # Anthropic não conhece mais o batch (expirou/apagado): FALHA
                # devolve os registros à fila na próxima coleta.
                logger.warning(
                    "recover_stale_batches: batch %s irrecuperável (%s) — "
                    "marcando FALHA pra liberar os registros.", batch.id, exc,
                )
                batch.status = PUB_BATCH_STATUS_FAILED
                batch.error_message = f"Zumbi irrecuperável: {str(exc)[:300]}"
                self.db.commit()
                resumo["liberados"] += 1
                continue
            if batch.anthropic_status == ANTHROPIC_STATUS_ENDED:
                try:
                    await self.apply_batch_results(batch)
                    resumo["aplicados"] += 1
                    logger.info(
                        "recover_stale_batches: batch %s estava pronto na "
                        "Anthropic — resultados aplicados.", batch.id,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "recover_stale_batches: batch %s pronto mas o apply "
                        "falhou: %s", batch.id, exc,
                    )
            else:
                resumo["em_andamento"] += 1
        if resumo["verificados"]:
            logger.info("recover_stale_batches: %s", resumo)
        return resumo

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
        # Telemetria de token. Somada por lote pra responder duas perguntas que
        # hoje ninguem consegue responder: quanto a classificacao custa, e
        # (depois do prompt caching) se o cache pegou de verdade.
        uso_total = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
        uso_itens = 0

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

            # Usage ANTES de tentar extrair a classificação: item que falhou
            # no parse também queimou token, e ignorá-lo subcontaria o custo
            # real do lote exatamente nos casos que mais interessam.
            u = AnthropicClassifierClient.extract_usage_from_batch_result(item)
            if any(u.values()):
                for k in uso_total:
                    uso_total[k] += u[k]
                uso_itens += 1

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
            classification["quem_pratica_ato"] = clean.quem_pratica_ato
            classification["exige_providencia_nossa"] = clean.exige_providencia_nossa

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
                # pub010 — de quem é o ato. Fica NULL quando a IA não emitiu
                # ou emitiu fora do enum (o schema descarta com warning); a
                # regra r6 do shadow simplesmente não dispara nesses casos.
                rec.quem_pratica_ato = clean.quem_pratica_ato
                rec.exige_providencia_nossa = clean.exige_providencia_nossa
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
                    clean.quem_pratica_ato, clean.exige_providencia_nossa,
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
        batch.usage_input_tokens = uso_total["input_tokens"]
        batch.usage_output_tokens = uso_total["output_tokens"]
        batch.usage_cache_read_tokens = uso_total["cache_read_input_tokens"]
        batch.usage_cache_creation_tokens = uso_total["cache_creation_input_tokens"]
        batch.usage_itens_contados = uso_itens
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
            "usage": {**uso_total, "itens_contados": uso_itens},
        }
        logger.info(
            "Batch %s aplicado: %s",
            batch.anthropic_batch_id,
            summary,
        )
        # Linha separada e legivel — e' o que o operador vai ler no log pra
        # comparar antes/depois do prompt caching.
        try:
            from app.services.classifier.anthropic_pricing import calcular_custo_usd

            custo = calcular_custo_usd(
                modelo=batch.model_used,
                input_tokens=uso_total["input_tokens"],
                output_tokens=uso_total["output_tokens"],
                cache_read_tokens=uso_total["cache_read_input_tokens"],
                cache_creation_tokens=uso_total["cache_creation_input_tokens"],
                batch=True,
            )
            if custo:
                summary["custo"] = custo
                logger.info(
                    "Batch %s — CUSTO: US$ %.4f (%s itens, entrada total %s tok, "
                    "saida %s tok). Cache: leitura %s / gravacao %s tok. "
                    "Sem cache custaria US$ %.4f (economia US$ %.4f).",
                    batch.anthropic_batch_id, custo["custo_total_usd"], uso_itens,
                    custo["entrada_total_tokens"], uso_total["output_tokens"],
                    uso_total["cache_read_input_tokens"],
                    uso_total["cache_creation_input_tokens"],
                    custo["custo_sem_cache_usd"], custo["economia_usd"],
                )
            else:
                logger.warning(
                    "Batch %s — usage somado (%s tok entrada) mas modelo %r nao "
                    "esta na tabela de precos; custo nao calculado.",
                    batch.anthropic_batch_id, uso_total["input_tokens"],
                    batch.model_used,
                )
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao calcular custo do batch (ignorado).")
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
            # Telemetria de token (migration pub009). `usage_input_tokens` é só
            # o que vem DEPOIS do último breakpoint de cache — a entrada total
            # é input + cache_read + cache_creation. Sem caching ligado, os dois
            # de cache são 0 e o input é a entrada inteira.
            "usage": {
                "input_tokens": batch.usage_input_tokens,
                "output_tokens": batch.usage_output_tokens,
                "cache_read_tokens": batch.usage_cache_read_tokens,
                "cache_creation_tokens": batch.usage_cache_creation_tokens,
                "itens_contados": batch.usage_itens_contados,
            },
            # Referência pela CLASSE, não por `self`: batch_to_dict é
            # @staticmethod e não recebe self — o `self.` aqui derrubou a
            # listagem de lotes da UI com NameError em 14/08 (o 500 era
            # engolido pelo catch silencioso do front, que mostrava
            # "nenhum lote enviado" com 146 lotes no banco).
            "custo": PublicationBatchClassifier._custo_do_batch(batch),
        }

    @staticmethod
    def _custo_do_batch(batch: PublicationBatchClassification) -> Optional[dict]:
        """Custo estimado a partir do usage gravado. None em lote antigo (sem
        telemetria) ou modelo fora da tabela de preços."""
        if not batch.usage_itens_contados:
            return None
        try:
            from app.services.classifier.anthropic_pricing import calcular_custo_usd

            return calcular_custo_usd(
                modelo=batch.model_used,
                input_tokens=batch.usage_input_tokens or 0,
                output_tokens=batch.usage_output_tokens or 0,
                cache_read_tokens=batch.usage_cache_read_tokens or 0,
                cache_creation_tokens=batch.usage_cache_creation_tokens or 0,
                batch=True,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao calcular custo do batch %s (ignorado).", batch.id)
            return None

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
