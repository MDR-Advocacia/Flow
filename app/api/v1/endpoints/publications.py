"""
Endpoints do motor de busca de publicações do Legal One.

Rotas:
  GET    /statistics                     → Contagens para o dashboard
  POST   /search                         → Dispara uma nova busca
  GET    /searches                       → Lista buscas anteriores
  GET    /searches/{id}                  → Detalhe de uma busca
  POST   /searches/{id}/cancel           → Cancela busca em andamento
  GET    /records                        → Lista registros de publicações (filtros)
  GET    /records/grouped                → Lista registros agrupados por processo
  GET    /records/{id}                   → Detalhe de um registro
  PATCH  /records/{id}                   → Atualiza status de um registro
  POST   /groups/{lawsuit_id}/schedule   → Agenda tarefa para um grupo (processo)
"""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core import auth as auth_security
from app.core.dependencies import get_db, get_api_client
from app.models.publication_batch import PUB_BATCH_STATUS_FAILED
from app.services.legal_one_client import LegalOneApiClient
from app.services.publication_batch_classifier import PublicationBatchClassifier
from app.services.publication_export_service import export_records_grouped_xlsx
from app.services.publication_search_service import PublicationSearchService

router = APIRouter()


def _get_batch_classifier(
    db: Session = Depends(get_db),
) -> PublicationBatchClassifier:
    return PublicationBatchClassifier(db=db)


def _get_service(
    db: Session = Depends(get_db),
    client: LegalOneApiClient = Depends(get_api_client),
) -> PublicationSearchService:
    return PublicationSearchService(db=db, client=client)


# ─── Schemas ────────────────────────────────────

class SearchRequest(BaseModel):
    date_from: str
    date_to: Optional[str] = None
    origin_type: str = "OfficialJournalsCrawler"
    # Aceita int (legado) OU CSV de IDs ("23,45,67") pra varrer múltiplos
    # escritórios na mesma busca. Service converte pra lista internamente.
    responsible_office_id: Optional[str] = None
    auto_classify: bool = True
    only_unlinked: bool = False  # Buscar apenas publicações sem processo vinculado


class UpdateRecordStatusRequest(BaseModel):
    status: str


class ScheduleGroupRequest(BaseModel):
    # Permite que o operador edite o payload proposto antes de enviar (1 tarefa)
    payload_override: Optional[dict] = None
    # Permite enviar várias tarefas numa única chamada (N tarefas → 1 agendamento)
    payload_overrides: Optional[list[dict]] = None
    # Se vazio, usa todos os registros do processo; se informado, só esses
    record_ids: Optional[list[int]] = None
    # Ignora a checagem de duplicata no L1. Frontend envia True quando o
    # operador confirma explicitamente o aviso de "já existe tarefa pendente".
    force_duplicate: bool = False


class ScheduleRecordsRequest(BaseModel):
    """Agendamento para publicações sem processo vinculado."""
    record_ids: list[int]
    payload_override: Optional[dict] = None
    payload_overrides: Optional[list[dict]] = None
    # Engolido por paridade com ScheduleGroupRequest (fluxo avulso não
    # consulta L1 porque não há lawsuit_id pra indexar a busca).
    force_duplicate: bool = False


class CheckDuplicatesRequest(BaseModel):
    """Payload pra consultar tasks pendentes no L1 por subtipo."""
    subtype_ids: list[int]


class ReclassifyRecordsRequest(BaseModel):
    """Override manual de categoria/subcategoria de um conjunto de publicações."""
    record_ids: list[int]
    category: str
    subcategory: Optional[str] = None


# ─── Dashboard ──────────────────────────────────

@router.get("/statistics")
def get_statistics(
    service: PublicationSearchService = Depends(_get_service),
):
    """Retorna contagens gerais para o painel de controle."""
    return service.get_statistics()


@router.get("/insights")
def get_operational_insights(
    period: str = Query("week", pattern="^(day|week|month|all)$"),
    service: PublicationSearchService = Depends(_get_service),
):
    """Retorna indicadores históricos leves para o drawer lateral da operação."""
    try:
        return service.get_operational_insights(period=period)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ─── Buscas ─────────────────────────────────────

@router.post("/search")
def create_search(
    payload: SearchRequest,
    background_tasks: BackgroundTasks,
    service: PublicationSearchService = Depends(_get_service),
    current_user=Depends(auth_security.get_current_user),
):
    """Dispara uma nova busca de publicações no Legal One."""
    import logging
    logger = logging.getLogger(__name__)

    def _run_search():
        try:
            # Parse responsible_office_id: aceita "23" (int single) OU
            # "23,45,67" (CSV). Service trabalha com list[int].
            office_ids: list[int] = []
            if payload.responsible_office_id:
                for part in str(payload.responsible_office_id).split(","):
                    p = part.strip()
                    if p:
                        try:
                            office_ids.append(int(p))
                        except ValueError:
                            pass
            service.create_and_run_search(
                date_from=payload.date_from,
                date_to=payload.date_to,
                origin_type=payload.origin_type,
                responsible_office_ids=office_ids or None,
                auto_classify=payload.auto_classify,
                requested_by=current_user.email if hasattr(current_user, "email") else None,
                only_unlinked=payload.only_unlinked,
            )
        except Exception as exc:
            logger.error("Erro na busca de publicacoes: %s", exc)

    background_tasks.add_task(_run_search)
    return {"message": "Busca iniciada em background.", "status": "EXECUTANDO"}


@router.post("/reclassify")
def reclassify_pending(
    background_tasks: BackgroundTasks,
    linked_office_id: Optional[str] = None,
    service: PublicationSearchService = Depends(_get_service),
    _=Depends(auth_security.get_current_user),
):
    """
    Reclassifica todos os registros com status NOVO que ainda têm texto.
    Útil para processar registros de buscas anteriores sem classificação.
    Aceita filtro opcional por escritório responsável (linked_office_id).
    """
    import logging as _logging
    _logger = _logging.getLogger(__name__)

    def _run():
        try:
            records = service.list_novo_with_text(linked_office_id=linked_office_id)
            if not records:
                _logger.info("Nenhum registro NOVO com texto para reclassificar.")
                return
            _logger.info("Reclassificando %d registros NOVO...", len(records))
            service._auto_classify_records(records)
            service._build_task_proposals(records)
        except Exception as exc:
            _logger.error("Erro na reclassificação: %s", exc)

    background_tasks.add_task(_run)
    return {"message": "Reclassificação iniciada em background.", "status": "EXECUTANDO"}


@router.post("/rebuild-proposals")
def rebuild_task_proposals(
    background_tasks: BackgroundTasks,
    linked_office_id: Optional[str] = Query(None, description="CSV de office IDs"),
    category: Optional[str] = Query(None, description="CSV de categorias"),
    uf: Optional[str] = Query(None, description="CSV de UFs"),
    polo: Optional[str] = Query(None, description="CSV: ativo,passivo,ambos"),
    natureza: Optional[str] = Query(None, description="CSV de naturezas"),
    vinculo: Optional[str] = Query(None, description="com_processo ou sem_processo"),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    service: PublicationSearchService = Depends(_get_service),
    _=Depends(auth_security.get_current_user),
):
    """
    Reconstrói propostas de tarefa para registros já classificados.

    Aceita TODOS os filtros da tela de Processos com Publicações, pra
    permitir reaplicar templates só no escopo que o operador está
    vendo — essencial quando se cria um template novo pra um escritório
    e não se quer reprocessar os outros 50k registros do sistema.

    O filtro de `status` é sempre implícito: apenas CLASSIFICADO é
    reconstruído (NOVO não tem category pra casar template).
    """
    import logging as _logging
    from app.models.publication_search import (
        PublicationRecord as PR,
        RECORD_STATUS_CLASSIFIED,
    )
    from app.services.publication_search_service import (
        _parse_csv_ints,
        _parse_csv_strs,
    )
    _logger = _logging.getLogger(__name__)

    def _run():
        try:
            q = service.db.query(PR).filter(
                PR.status == RECORD_STATUS_CLASSIFIED,
                PR.category.isnot(None),
            )
            # Aplica os mesmos filtros usados na listagem, com mesmo helper CSV.
            office_ids = _parse_csv_ints(linked_office_id)
            if office_ids:
                q = q.filter(PR.linked_office_id.in_(office_ids)) if len(office_ids) > 1 else q.filter(PR.linked_office_id == office_ids[0])
            cats = _parse_csv_strs(category)
            if cats:
                q = q.filter(PR.category.in_(cats)) if len(cats) > 1 else q.filter(PR.category == cats[0])
            ufs = [u.strip().upper() for u in _parse_csv_strs(uf)]
            if ufs:
                q = q.filter(PR.uf.in_(ufs)) if len(ufs) > 1 else q.filter(PR.uf == ufs[0])
            polos = [p.strip().lower() for p in _parse_csv_strs(polo)]
            if polos:
                q = q.filter(PR.polo.in_(polos)) if len(polos) > 1 else q.filter(PR.polo == polos[0])
            nats = _parse_csv_strs(natureza)
            if nats:
                q = q.filter(PR.natureza_processo.in_(nats)) if len(nats) > 1 else q.filter(PR.natureza_processo == nats[0])
            vincs = _parse_csv_strs(vinculo)
            if vincs and len(vincs) < 2:
                if vincs[0] == "sem_processo":
                    q = q.filter(PR.linked_lawsuit_id.is_(None))
                elif vincs[0] == "com_processo":
                    q = q.filter(PR.linked_lawsuit_id.isnot(None))
            if date_from:
                q = q.filter(PR.creation_date >= date_from)
            if date_to:
                q = q.filter(PR.creation_date < date_to + "T99")

            records = q.all()
            if not records:
                _logger.info(
                    "rebuild-proposals: nenhum registro classificado para os filtros atuais."
                )
                return
            _logger.info(
                "rebuild-proposals: reconstruindo propostas para %d registros...",
                len(records),
            )
            # A busca de responsável é limitada aos templates sem responsável nominal.
            service._build_task_proposals(records)
            _logger.info("rebuild-proposals: concluído.")
        except Exception as exc:
            _logger.error("Erro na reconstrução de propostas: %s", exc)

    background_tasks.add_task(_run)
    return {"message": "Reconstrução de propostas iniciada em background.", "status": "EXECUTANDO"}


# ─── Classificação em lote (Anthropic Batch API) ────────────────────────

def _prefetch_lawsuit_responsibles_for_batch(
    lawsuit_ids: list[int],
    batch_id: int,
) -> None:
    import logging

    logger = logging.getLogger(__name__)
    try:
        result = LegalOneApiClient().prefetch_lawsuit_responsibles_cache(lawsuit_ids)
        logger.info(
            "Pré-cache de responsáveis concluído para batch %s: %d/%d processos.",
            batch_id, len(result), len(lawsuit_ids),
        )
    except Exception as exc:
        logger.warning(
            "Falha no pré-cache de responsáveis para batch %s: %s",
            batch_id, exc,
        )


class SubmitBatchRequest(BaseModel):
    linked_office_id: Optional[str] = None
    limit: Optional[int] = None
    only_unlinked: bool = False  # Classificar apenas publicações sem processo vinculado


@router.post("/classify-batch/submit")
async def submit_classify_batch(
    payload: SubmitBatchRequest,
    background_tasks: BackgroundTasks,
    classifier: PublicationBatchClassifier = Depends(_get_batch_classifier),
    current_user=Depends(auth_security.get_current_user),
):
    """
    Envia publicações pendentes (NOVO, com texto, sem categoria) para
    a Message Batches API da Anthropic.

    Este endpoint é SÍNCRONO no que diz respeito à criação do batch
    (retorna o anthropic_batch_id imediatamente), mas a classificação
    das publicações acontece ASSINCRONAMENTE no lado da Anthropic.

    Aceita filtro por escritório e limite de registros.
    """
    records = classifier.collect_pending_records(
        linked_office_id=payload.linked_office_id,
        limit=payload.limit,
        only_unlinked=payload.only_unlinked,
    )
    if not records:
        raise HTTPException(
            status_code=404,
            detail="Nenhum registro pendente de classificação encontrado.",
        )

    try:
        email = current_user.email if hasattr(current_user, "email") else None
        batch = await classifier.submit_batch(
            records=records, requested_by_email=email
        )
        lawsuit_ids = sorted({
            int(rec.linked_lawsuit_id)
            for rec in records
            if rec.linked_lawsuit_id
        })
        if lawsuit_ids:
            background_tasks.add_task(
                _prefetch_lawsuit_responsibles_for_batch,
                lawsuit_ids,
                batch.id,
            )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao enviar batch para Anthropic: {exc}",
        )

    return classifier.batch_to_dict(batch)


@router.get("/classify-batch")
def list_classify_batches(
    limit: int = Query(50, ge=1, le=200),
    classifier: PublicationBatchClassifier = Depends(_get_batch_classifier),
    _=Depends(auth_security.get_current_user),
):
    """Lista os batches de classificação mais recentes."""
    batches = classifier.list_batches(limit=limit)
    return [classifier.batch_to_dict(b) for b in batches]


@router.get("/classify-batch/{batch_id}")
def get_classify_batch(
    batch_id: int,
    classifier: PublicationBatchClassifier = Depends(_get_batch_classifier),
    _=Depends(auth_security.get_current_user),
):
    """Retorna o estado atual de um batch (local cache)."""
    batch = classifier.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch não encontrado.")
    return classifier.batch_to_dict(batch)


@router.post("/classify-batch/{batch_id}/refresh")
async def refresh_classify_batch(
    batch_id: int,
    classifier: PublicationBatchClassifier = Depends(_get_batch_classifier),
    _=Depends(auth_security.get_current_user),
):
    """
    Consulta a Anthropic para atualizar o status do batch.
    Retorna os contadores atualizados sem baixar os resultados.
    """
    batch = classifier.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch não encontrado.")
    try:
        batch = await classifier.refresh_batch_status(batch)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao consultar status: {exc}")
    return classifier.batch_to_dict(batch)


@router.post("/classify-batch/{batch_id}/apply")
async def apply_classify_batch(
    batch_id: int,
    background_tasks: BackgroundTasks,
    classifier: PublicationBatchClassifier = Depends(_get_batch_classifier),
    _=Depends(auth_security.get_current_user),
):
    """
    Baixa os resultados de um batch PRONTO e aplica as classificações
    nos PublicationRecord correspondentes.

    Esta operação pode levar algum tempo (dependendo do tamanho do batch),
    então roda em background.
    """
    # Valida a existência usando a sessão da request; só guarda o id para o
    # background task — a sessão abaixo será fechada ao retornar a response.
    batch = classifier.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch não encontrado.")

    import logging as _logging
    _logger = _logging.getLogger(__name__)

    async def _run(target_batch_id: int):
        """
        Roda depois que a request termina. Como a sessão injetada via
        ``Depends(get_db)`` já está fechada nesse ponto, abrimos uma nova
        SessionLocal() aqui e carregamos o batch de novo. Isso evita o erro
        'Instance is not persistent within this Session' no commit final.
        """
        from app.db.session import SessionLocal
        db = SessionLocal()
        try:
            bg_classifier = PublicationBatchClassifier(db=db)
            fresh_batch = bg_classifier.get_batch(target_batch_id)
            if not fresh_batch:
                _logger.warning("Batch %s sumiu antes do apply.", target_batch_id)
                return
            refreshed = await bg_classifier.refresh_batch_status(fresh_batch)
            if not refreshed.results_url:
                _logger.info(
                    "Batch %s ainda não finalizado (status=%s). Pulando.",
                    target_batch_id, refreshed.anthropic_status,
                )
                return
            await bg_classifier.apply_batch_results(refreshed)
        except Exception as exc:
            _logger.exception("Erro ao aplicar batch %s: %s", target_batch_id, exc)
        finally:
            db.close()

    background_tasks.add_task(_run, batch_id)
    return {
        "message": "Aplicação do batch iniciada em background.",
        "batch_id": batch_id,
    }


@router.post("/classify-batch/{batch_id}/retry-errors")
async def retry_batch_errors(
    batch_id: int,
    classifier: PublicationBatchClassifier = Depends(_get_batch_classifier),
    current_user=Depends(auth_security.get_current_user),
):
    """
    Reprocessa os itens que falharam em um batch anterior.
    Coleta registros com erro, reseta status para NOVO, e envia novo batch.
    """
    batch = classifier.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch não encontrado.")
    # Aceita retry se há error_details, contadores de falha da Anthropic,
    # ou se o envio inteiro falhou depois de capturar os record_ids.
    has_errors = (
        bool(batch.error_details)
        or (batch.errored_count or 0) > 0
        or (batch.expired_count or 0) > 0
        or (batch.canceled_count or 0) > 0
        or (
            batch.status == PUB_BATCH_STATUS_FAILED
            and bool(batch.record_ids)
        )
    )
    if not has_errors:
        raise HTTPException(status_code=400, detail="Nenhum erro registrado neste batch.")

    records = classifier.collect_errored_records_from_batch(batch)
    if not records:
        raise HTTPException(
            status_code=400,
            detail="Nenhum registro com erro encontrado para reprocessamento.",
        )

    try:
        email = current_user.email if hasattr(current_user, "email") else None
        new_batch = await classifier.submit_batch(
            records=records, requested_by_email=email
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao enviar retry batch: {exc}",
        )

    return {
        "message": f"Novo batch criado com {len(records)} registros que falharam.",
        "original_batch_id": batch_id,
        "new_batch": classifier.batch_to_dict(new_batch),
    }


@router.post("/classify-batch/{batch_id}/reclassify-all")
async def reclassify_full_batch(
    batch_id: int,
    db: Session = Depends(get_db),
    classifier: PublicationBatchClassifier = Depends(_get_batch_classifier),
    current_user=Depends(auth_security.get_current_user),
):
    """Reclassifica TODOS os records de um batch — incluindo os que ja
    foram classificados com sucesso. Util quando o prompt foi atualizado
    e o operador quer reaplicar a classificacao em todo o lote com o
    prompt novo (versao 2026-05-08: persona advogado + filosofia
    'enquadrar antes de Para Analise').

    Diferente de retry_batch_errors (que pega so os que falharam),
    este endpoint zera category/subcategory/polo/classifications dos
    records e re-submete TODOS no lote. O batch original nao e
    apagado — fica como historico.

    Custo: cada record vira 1 chamada Anthropic. Lote de 516 = 516
    chamadas. Operador deve confirmar antes via UI."""
    from app.models.publication_search import PublicationRecord, RECORD_STATUS_NEW

    batch = classifier.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch não encontrado.")
    if not batch.record_ids:
        raise HTTPException(
            status_code=400,
            detail="Batch sem record_ids — nada a reclassificar.",
        )

    record_ids = list(batch.record_ids)
    records = (
        db.query(PublicationRecord)
        .filter(PublicationRecord.id.in_(record_ids))
        .all()
    )
    if not records:
        raise HTTPException(
            status_code=400,
            detail="Nenhum record encontrado pelos IDs do batch (foram removidos?).",
        )

    # Reseta a classificacao atual: zera cat/sub/polo/classifications
    # array. Status volta pra NEW pra que collect_pending_records
    # consiga pegar (apesar de submit_batch nao depender disso —
    # passa direto a lista).
    for r in records:
        r.category = None
        r.subcategory = None
        r.polo = None
        r.classifications = None
        r.status = RECORD_STATUS_NEW
    db.commit()

    try:
        email = current_user.email if hasattr(current_user, "email") else None
        new_batch = await classifier.submit_batch(
            records=records, requested_by_email=email
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao enviar reclassify-all batch: {exc}",
        )

    return {
        "message": (
            f"Novo batch criado com {len(records)} registros "
            f"(reclassificacao completa do lote {batch_id})."
        ),
        "original_batch_id": batch_id,
        "total_records": len(records),
        "new_batch": classifier.batch_to_dict(new_batch),
    }


@router.get("/classify-batch/{batch_id}/errors")
def get_batch_errors(
    batch_id: int,
    classifier: PublicationBatchClassifier = Depends(_get_batch_classifier),
    _=Depends(auth_security.get_current_user),
):
    """Retorna detalhes dos erros de um batch específico."""
    batch = classifier.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch não encontrado.")
    return {
        "batch_id": batch_id,
        "errored_count": batch.errored_count or 0,
        "expired_count": batch.expired_count or 0,
        "error_details": batch.error_details or {},
    }


@router.get("/searches")
def list_searches(
    limit: int = Query(20, ge=1, le=100),
    service: PublicationSearchService = Depends(_get_service),
):
    """Lista buscas anteriores."""
    return service.list_searches(limit=limit)


@router.get("/searches/{search_id}")
def get_search(
    search_id: int,
    service: PublicationSearchService = Depends(_get_service),
):
    """Retorna detalhe de uma busca."""
    try:
        return service.get_search(search_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/searches/{search_id}/cancel")
def cancel_search(
    search_id: int,
    service: PublicationSearchService = Depends(_get_service),
):
    """Cancela uma busca em andamento."""
    cancelled = service.cancel_search(search_id)
    if not cancelled:
        raise HTTPException(status_code=400, detail="Busca não pode ser cancelada.")
    return {"search_id": search_id, "status": "CANCELADO"}


# ─── Registros de Publicações ───────────────────

@router.get("/records")
def list_records(
    search_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    linked_office_id: Optional[str] = Query(None, description="Filtra por escritório responsável"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    service: PublicationSearchService = Depends(_get_service),
):
    """Lista registros de publicações com filtros."""
    return service.list_records(
        search_id=search_id,
        status=status,
        linked_office_id=linked_office_id,
        limit=limit,
        offset=offset,
    )


@router.get("/records/grouped")
def list_records_grouped(
    search_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    linked_office_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="Data início (YYYY-MM-DD). Filtra por creation_date (data do Ajus)."),
    date_to: Optional[str] = Query(None, description="Data fim (YYYY-MM-DD). Filtra por creation_date (data do Ajus)."),
    category: Optional[str] = Query(None, description="Filtra por categoria de classificação."),
    uf: Optional[str] = Query(None, description="UF/região derivada do CNJ (ex.: SP, RJ, TRT7, TRF1)."),
    vinculo: Optional[str] = Query(None, description="Filtro de vínculo: com_processo, sem_processo."),
    natureza: Optional[str] = Query(None, description="Filtra por natureza do processo (ex.: Embargos à Execução)."),
    polo: Optional[str] = Query(None, description="Filtra por polo indicado (ativo, passivo, ambos)."),
    cnj_search: Optional[str] = Query(None, description="Busca tolerante por CNJ (match por dígitos, ignora máscara)."),
    scheduled_by_user_id: Optional[str] = Query(None, description="CSV de user_ids do operador que cadastrou (Cadastrado por)."),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    service: PublicationSearchService = Depends(_get_service),
):
    """Lista registros agrupados por processo (linked_lawsuit_id)."""
    return service.list_records_grouped(
        search_id=search_id,
        status=status,
        linked_office_id=linked_office_id,
        date_from=date_from,
        date_to=date_to,
        category=category,
        uf=uf,
        vinculo=vinculo,
        natureza=natureza,
        polo=polo,
        cnj_search=cnj_search,
        scheduled_by_user_id=scheduled_by_user_id,
        limit=limit,
        offset=offset,
    )


@router.get("/records/grouped/export")
def export_records_grouped(
    search_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    linked_office_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="Data início (YYYY-MM-DD)."),
    date_to: Optional[str] = Query(None, description="Data fim (YYYY-MM-DD)."),
    category: Optional[str] = Query(None, description="Classificação primária."),
    uf: Optional[str] = Query(None, description="UF/região derivada do CNJ."),
    vinculo: Optional[str] = Query(None, description="Filtro de vínculo: com_processo, sem_processo."),
    natureza: Optional[str] = Query(None, description="Natureza do processo (avulsas)."),
    polo: Optional[str] = Query(None, description="Filtra por polo indicado (ativo, passivo, ambos)."),
    cnj_search: Optional[str] = Query(None, description="Busca tolerante por CNJ (match por dígitos)."),
    scheduled_by_user_id: Optional[str] = Query(None, description="CSV de user_ids do operador que cadastrou."),
    db: Session = Depends(get_db),
):
    """
    Exporta as publicações (respeitando os filtros aplicados na tela de
    Processos com Publicações) em um arquivo XLSX. O arquivo tem uma linha
    por publicação, mantém a ordem da tela (CNJ, data de publicação desc) e
    inclui uma aba "Filtros" com os parâmetros usados.
    """
    content, filename = export_records_grouped_xlsx(
        db=db,
        search_id=search_id,
        status=status,
        linked_office_id=linked_office_id,
        date_from=date_from,
        date_to=date_to,
        category=category,
        uf=uf,
        vinculo=vinculo,
        natureza=natureza,
        polo=polo,
        cnj_search=cnj_search,
        scheduled_by_user_id=scheduled_by_user_id,
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/lookup-by-cnj")
def lookup_by_cnj(
    cnj: str = Query(..., description="Número CNJ do processo (com ou sem formatação)."),
    service: PublicationSearchService = Depends(_get_service),
):
    """
    Diagnóstico por processo: retorna tudo que o sistema conhece sobre o CNJ
    informado — buscas que o alcançaram, publicações encontradas, classificação
    atribuída, status atual e estado na fila de tratamento (RPA).
    """
    try:
        return service.lookup_by_cnj(cnj)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/records/duplicate-divergences")
def list_duplicate_divergences(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: PublicationSearchService = Depends(_get_service),
):
    """
    Retorna duplicatas cujo texto difere do original.
    Útil para verificar se a deduplicação está descartando textos diferentes.
    """
    return service.list_duplicate_divergences(limit=limit, offset=offset)


@router.get("/records/{record_id}")
def get_record(
    record_id: int,
    service: PublicationSearchService = Depends(_get_service),
):
    """Retorna detalhe completo de um registro."""
    try:
        return service.get_record(record_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.patch("/records/{record_id}")
def update_record_status(
    record_id: int,
    payload: UpdateRecordStatusRequest,
    service: PublicationSearchService = Depends(_get_service),
    current_user=Depends(auth_security.get_current_user),
):
    """Atualiza o status de um registro de publicação.

    Quando o status vai pra IGNORADO ("dar ciência"), o `current_user` é
    repassado pro service pra registrar a autoria (ignored_by_*).
    """
    try:
        return service.update_record_status(
            record_id, payload.status, acted_by=current_user
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/records/reclassify")
def reclassify_records(
    payload: ReclassifyRecordsRequest,
    service: PublicationSearchService = Depends(_get_service),
    _=Depends(auth_security.get_current_user),
):
    """
    Aplica uma classificação manual a um conjunto de registros e reconstrói
    suas propostas de tarefa. Usado pelo operador quando a classificação
    automática precisa ser corrigida.
    """
    try:
        return service.reclassify_records(
            record_ids=payload.record_ids,
            category=payload.category,
            subcategory=payload.subcategory,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ─── Feedback de classificação ────────────────────

class ClassificationFeedbackRequest(BaseModel):
    record_id: int
    corrected_category: str
    corrected_subcategory: Optional[str] = None
    corrected_polo: Optional[str] = None
    corrected_natureza: Optional[str] = None
    error_type: Optional[str] = None   # "category" | "subcategory" | "polo" | "natureza" | "multiple"
    user_note: Optional[str] = None


@router.post("/records/feedback")
def submit_classification_feedback(
    payload: ClassificationFeedbackRequest,
    service: PublicationSearchService = Depends(_get_service),
    current_user=Depends(auth_security.get_current_user),
):
    """
    Registra feedback explícito do operador sobre uma classificação incorreta.
    O texto da publicação é salvo como excerpt para uso como exemplo no prompt.
    """
    from app.models.classification_feedback import ClassificationFeedback
    from app.models.publication_search import PublicationRecord

    rec = service.db.query(PublicationRecord).filter(PublicationRecord.id == payload.record_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Registro não encontrado.")

    fb = ClassificationFeedback(
        record_id=rec.id,
        feedback_type="explicit",
        original_category=rec.category,
        original_subcategory=rec.subcategory,
        original_polo=rec.polo,
        original_natureza=rec.natureza_processo,
        corrected_category=payload.corrected_category,
        corrected_subcategory=payload.corrected_subcategory,
        corrected_polo=payload.corrected_polo,
        corrected_natureza=payload.corrected_natureza,
        error_type=payload.error_type,
        user_note=payload.user_note,
        text_excerpt=(rec.description or "")[:500],
        office_external_id=rec.linked_office_id,
        created_by_email=current_user.email if hasattr(current_user, "email") else None,
    )
    service.db.add(fb)

    # Aplica a correção ao registro também
    if payload.corrected_category:
        rec.category = payload.corrected_category
    if payload.corrected_subcategory is not None:
        rec.subcategory = payload.corrected_subcategory
    if payload.corrected_polo:
        rec.polo = payload.corrected_polo
    if payload.corrected_natureza is not None:
        rec.natureza_processo = payload.corrected_natureza

    service.db.commit()
    return {"message": "Feedback registrado com sucesso.", "id": fb.id}


# ─── Agendamento por grupo (processo) ───────────

@router.post("/groups/{lawsuit_id}/schedule")
def schedule_group(
    lawsuit_id: int,
    payload: ScheduleGroupRequest,
    service: PublicationSearchService = Depends(_get_service),
    current_user=Depends(auth_security.get_current_user),
):
    """Agenda uma tarefa consolidada para todas as publicações de um processo."""
    try:
        result = service.schedule_group(
            lawsuit_id=lawsuit_id,
            payload_override=payload.payload_override,
            payload_overrides=payload.payload_overrides,
            scheduled_by=current_user,
            force_duplicate=payload.force_duplicate,
        )
        # Alias for frontend
        result["task_id"] = result.get("created_task_id")
        return result
    except ValueError as exc:
        # Erros de duplicata carregam prefixo "DUPLICATE_BLOCKED:<n>:" pro
        # frontend distinguir e mostrar modal de confirmação específica.
        detail = str(exc)
        status_code = 409 if detail.startswith("DUPLICATE_BLOCKED:") else 400
        raise HTTPException(status_code=status_code, detail=detail)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao agendar: {exc}")


@router.post("/groups/records/schedule")
def schedule_records(
    payload: ScheduleRecordsRequest,
    service: PublicationSearchService = Depends(_get_service),
    current_user=Depends(auth_security.get_current_user),
):
    """
    Agenda uma tarefa para publicações SEM processo vinculado.
    Usa templates globais (office_external_id IS NULL).
    """
    try:
        result = service.schedule_records(
            record_ids=payload.record_ids,
            payload_override=payload.payload_override,
            payload_overrides=payload.payload_overrides,
            scheduled_by=current_user,
            force_duplicate=payload.force_duplicate,
        )
        result["task_id"] = result.get("created_task_id")
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao agendar: {exc}")


@router.post("/groups/{lawsuit_id}/check-duplicates")
def check_duplicates(
    lawsuit_id: int,
    payload: CheckDuplicatesRequest,
    service: PublicationSearchService = Depends(_get_service),
    _=Depends(auth_security.get_current_user),
):
    """
    Retorna a lista de tasks já pendentes no L1 (statusId em 0/1/2) para
    cada subtype_id informado, agrupadas por subtipo. Usado pelo modal de
    agendamento pra alertar o operador antes do envio.
    """
    try:
        return service.check_duplicates_for_lawsuit(
            lawsuit_id=lawsuit_id,
            subtype_ids=payload.subtype_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao checar duplicatas: {exc}")


@router.post("/groups/{lawsuit_id}/refresh-today-dates")
def refresh_today_dates(
    lawsuit_id: int,
    service: PublicationSearchService = Depends(_get_service),
    _=Depends(auth_security.get_current_user),
):
    """
    Recalcula a data de agendamento das propostas cujo template tem
    referencia "data atual" (due_date_reference='today'), usando a data de
    HOJE. Chamado pelo modal de agendamento na ABERTURA — pra publicacao
    antiga nao ficar com data velha (a data e' congelada quando o template e'
    aplicado). So mexe nessas propostas; as demais ficam intactas.

    Retorna { "refreshed_dates_by_subtype": { <subtype_id>: { "startDateTime",
    "endDateTime" } } } pro frontend aplicar nos payloads do modal (uma vez,
    na abertura — respeita edicao manual posterior). Falha graceful: em erro
    retorna mapa vazio (nao trava o agendamento).
    """
    try:
        refreshed = service.refresh_today_reference_dates(lawsuit_id)
        return {"refreshed_dates_by_subtype": refreshed}
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "refresh-today-dates falhou p/ lawsuit %s: %s", lawsuit_id, exc
        )
        return {"refreshed_dates_by_subtype": {}}


@router.get("/groups/{lawsuit_id}/recent-tasks")
def recent_tasks(
    lawsuit_id: int,
    limit: int = Query(default=5, ge=1, le=20, description="Máximo de tarefas concluídas a retornar"),
    service: PublicationSearchService = Depends(_get_service),
    _=Depends(auth_security.get_current_user),
):
    """
    Diagnóstico de tarefas no L1 pra UI do detalhe da publicação. Retorna:
      - `pending`: TODAS as tarefas em curso (status 0/1/2)
      - `recent_completed`: até `limit` tarefas terminadas, mais recentes primeiro
    Cada item traz description, subtype_name, status_label, datas e l1_url.
    Falha graceful: em caso de erro do L1 retorna `check_failed=True`.
    """
    try:
        return service.get_recent_tasks_for_lawsuit(
            lawsuit_id=lawsuit_id,
            recent_completed_limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao consultar tarefas: {exc}")


@router.get("/groups/{lawsuit_id}/scheduled-audit")
def scheduled_audit(
    lawsuit_id: int,
    service: PublicationSearchService = Depends(_get_service),
    _=Depends(auth_security.get_current_user),
):
    """Auditoria de agendamento por processo: pra cada tarefa criada no L1,
    o payload EXATO enviado, quem agendou e se divergiu da proposta automática
    (override humano de subtipo/escritório/responsável). O L1 só guarda
    "Sistema" como criador — aqui fica o operador real."""
    from app.models.publication_task_audit import PublicationTaskAudit

    rows = (
        service.db.query(PublicationTaskAudit)
        .filter(PublicationTaskAudit.lawsuit_id == lawsuit_id)
        .order_by(PublicationTaskAudit.id.desc())
        .all()
    )
    return [
        {
            "id": a.id,
            "created_task_id": a.created_task_id,
            "subtype_id": a.subtype_id,
            "override_detected": a.override_detected,
            "override_fields": a.override_fields,
            "system_adjustments": a.system_adjustments,
            "scheduled_by_name": a.scheduled_by_name,
            "scheduled_by_email": a.scheduled_by_email,
            "scheduled_at": a.scheduled_at.isoformat() if a.scheduled_at else None,
            "sent_payload": a.sent_payload,
            "proposed_payload": a.proposed_payload,
        }
        for a in rows
    ]


# ─── Debug ──────────────────────────────────────

@router.get("/debug-api")
def debug_legalone_api(
    query: str = Query(default="", description="Query string OData manual, ex: $filter=originType eq 'OfficialJournalsCrawler'&$top=2"),
    client: LegalOneApiClient = Depends(get_api_client),
    _=Depends(auth_security.get_current_user),
):
    """
    Chama diretamente a API do LegalOne (/Updates) com a query string fornecida
    e retorna a resposta raw (status + body). Útil para diagnosticar erros 400.
    """
    base_url = client.base_url
    if query:
        url = f"{base_url}/Updates?{query}"
    else:
        url = f"{base_url}/Updates?$top=1"

    client._refresh_token_if_needed()
    headers = {"Authorization": f"Bearer {client._Auth.token}"}

    try:
        resp = client._session.get(url, headers=headers, timeout=30)
        return {
            "status_code": resp.status_code,
            "url_called": url,
            "response_body": resp.text[:4000],
        }
    except Exception as exc:
        return {"error": str(exc), "url_called": url}


@router.get("/debug/task-statuses")
def debug_task_statuses(
    sample_size: int = Query(default=200, ge=10, le=500, description="Amostra de tarefas a inspecionar"),
    client: LegalOneApiClient = Depends(get_api_client),
    _=Depends(auth_security.get_current_user),
):
    """
    Descobre o mapeamento canônico statusId → Description direto do L1.

    Faz uma chamada paginada ao /Tasks com `$expand=status`, agrega todos
    os status únicos encontrados na amostra e retorna o dicionário
    {statusId: {description, sample_count}}.

    Use uma vez pra atualizar L1_STATUS_LABELS_FULL no
    publication_search_service.py — a fonte da verdade é o L1.

    NOTA: o L1 tem cap de $top=30 no endpoint /Tasks, então a função
    pagina via @odata.nextLink até atingir `sample_size`. Pode ser
    lento (rate limiter de 1.2 req/s).
    """
    base_url = client.base_url
    client._refresh_token_if_needed()
    headers = {"Authorization": f"Bearer {client._Auth.token}"}

    discovered: dict[int, dict] = {}
    url: str | None = (
        f"{base_url}/Tasks?$top=30"
        f"&$select=id,statusId&$expand=status($select=id,description)"
    )
    pages_fetched = 0
    total_tasks_seen = 0

    try:
        while url and total_tasks_seen < sample_size:
            resp = client._session.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                return {
                    "error": f"HTTP {resp.status_code}",
                    "url_called": url,
                    "response_body": resp.text[:1000],
                    "discovered_so_far": discovered,
                }
            data = resp.json() or {}
            value = data.get("value", []) or []
            pages_fetched += 1
            total_tasks_seen += len(value)
            for t in value:
                # Lê dos dois lugares (statusId top-level e status.id/Id nested)
                # pra detectar divergência se houver.
                flat_id = t.get("statusId")
                nested = t.get("status") or {}
                # OData às vezes devolve PascalCase (Id, Description) e às
                # vezes camelCase (id, description). Tenta ambos.
                nested_id = nested.get("id") if "id" in nested else nested.get("Id")
                description = (
                    nested.get("description")
                    if "description" in nested
                    else nested.get("Description")
                )
                # Chave do agregado: o que VEMOS de fato (preferindo nested
                # se existir, senão flat).
                key = nested_id if nested_id is not None else flat_id
                if key is None:
                    continue
                entry = discovered.setdefault(
                    int(key),
                    {
                        "description": description,
                        "sample_count": 0,
                        "flat_id_match": True,
                        "example_task_ids": [],
                    },
                )
                entry["sample_count"] += 1
                if len(entry["example_task_ids"]) < 3:
                    entry["example_task_ids"].append(t.get("id"))
                if flat_id is not None and nested_id is not None and flat_id != nested_id:
                    entry["flat_id_match"] = False
                    entry.setdefault("flat_id_when_diverging", flat_id)
            url = data.get("@odata.nextLink")
            if total_tasks_seen >= sample_size:
                break

        # Ordenar por statusId pra leitura fácil
        ordered = dict(sorted(discovered.items()))
        return {
            "discovered_status_map": ordered,
            "total_tasks_inspected": total_tasks_seen,
            "pages_fetched": pages_fetched,
            "next_step_hint": (
                "Use 'description' como o label canônico em "
                "L1_STATUS_LABELS_FULL no publication_search_service.py. "
                "Se 'flat_id_match=False' aparecer pra algum status, "
                "significa que o campo flat 'statusId' diverge do "
                "'status.Id' nested — nesse caso usar o nested como fonte."
            ),
        }
    except Exception as exc:
        return {
            "error": str(exc),
            "discovered_so_far": discovered,
            "total_tasks_seen": total_tasks_seen,
        }


# ─── Classificações por Escritório (Overrides) ─────────────


class OverrideCreate(BaseModel):
    office_external_id: int
    category: str
    subcategory: str | None = None
    action: str = "exclude"  # "exclude" | "include_custom"
    custom_description: str | None = None


class OverrideUpdate(BaseModel):
    is_active: bool | None = None
    custom_description: str | None = None


class OverrideBulkRequest(BaseModel):
    category: str
    subcategory: str | None = None
    action: str = "exclude"  # "exclude" | "include_custom"
    custom_description: str | None = None
    # Se vazio/None, aplica a TODOS os escritórios conhecidos.
    office_external_ids: list[int] | None = None


class OverrideBulkForOfficeItem(BaseModel):
    category: str
    subcategory: str | None = None
    custom_description: str | None = None


class OverrideBulkForOfficeRequest(BaseModel):
    """
    Inverso do OverrideBulkRequest: UM escritorio, N combinacoes
    (category/subcategory). Usado pelo modal "Adicionar classificacoes" do
    regime manual - operador marca varias na taxonomia base e cria todos
    como include_custom de uma vez.
    """
    office_external_id: int
    items: list[OverrideBulkForOfficeItem]
    action: str = "include_custom"  # "exclude" | "include_custom"
    custom_description: str | None = None  # default opcional pra todos os items


class OverrideMigratePayload(BaseModel):
    """Payload do operador revisando um override v1 pra v2.

    O modal de pendencias envia category/subcategory selecionadas via
    ClassificationPicker (filtrado pelo polo_scope do escritorio).
    Demais campos (custom_description, is_active, action) ja estao
    preservados desde tax007."""

    category: str
    subcategory: str | None = None


@router.get("/classification-overrides")
def list_classification_overrides(
    office_external_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _=Depends(auth_security.get_current_user),
):
    """Lista overrides de classificação. Filtra por escritório se informado."""
    from app.models.office_classification import OfficeClassificationOverride

    query = db.query(OfficeClassificationOverride)
    if office_external_id is not None:
        query = query.filter(
            OfficeClassificationOverride.office_external_id == office_external_id
        )
    overrides = query.order_by(
        OfficeClassificationOverride.office_external_id,
        OfficeClassificationOverride.category,
    ).all()
    return [
        {
            "id": o.id,
            "office_external_id": o.office_external_id,
            "category": o.category,
            "subcategory": o.subcategory,
            "action": o.action,
            "custom_description": o.custom_description,
            "is_active": o.is_active,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "taxonomy_version": getattr(o, "taxonomy_version", None) or "v1",
            "legacy_label": getattr(o, "legacy_label", None),
            "needs_taxonomy_review": bool(getattr(o, "needs_taxonomy_review", False)),
        }
        for o in overrides
    ]


@router.post("/classification-overrides")
def create_classification_override(
    body: OverrideCreate,
    db: Session = Depends(get_db),
    _=Depends(auth_security.get_current_user),
):
    """Cria um override de classificação para um escritório."""
    from app.models.office_classification import OfficeClassificationOverride

    # `manual_mode` é um override singleton por escritório (sem categoria
    # nem subcategoria) que sinaliza "este escritório está em regime manual
    # de classificações" — taxonomia efetiva passa a ser apenas o que foi
    # explicitamente adicionado via include_custom + templates ativos.
    if body.action not in ("exclude", "include_custom", "manual_mode"):
        raise HTTPException(
            400,
            "action deve ser 'exclude', 'include_custom' ou 'manual_mode'",
        )

    existing = (
        db.query(OfficeClassificationOverride)
        .filter(
            OfficeClassificationOverride.office_external_id == body.office_external_id,
            OfficeClassificationOverride.category == body.category,
            OfficeClassificationOverride.subcategory == body.subcategory,
            OfficeClassificationOverride.action == body.action,
        )
        .first()
    )
    if existing:
        raise HTTPException(409, "Override já existe para esta combinação.")

    override = OfficeClassificationOverride(
        office_external_id=body.office_external_id,
        category=body.category,
        subcategory=body.subcategory,
        action=body.action,
        custom_description=body.custom_description,
    )
    db.add(override)
    db.commit()
    db.refresh(override)
    return {
        "id": override.id,
        "office_external_id": override.office_external_id,
        "category": override.category,
        "subcategory": override.subcategory,
        "action": override.action,
        "is_active": override.is_active,
    }


@router.patch("/classification-overrides/{override_id}")
def update_classification_override(
    override_id: int,
    body: OverrideUpdate,
    db: Session = Depends(get_db),
    _=Depends(auth_security.get_current_user),
):
    """Atualiza um override (ativa/desativa ou altera descrição)."""
    from app.models.office_classification import OfficeClassificationOverride

    override = db.query(OfficeClassificationOverride).filter_by(id=override_id).first()
    if not override:
        raise HTTPException(404, "Override não encontrado.")
    if body.is_active is not None:
        override.is_active = body.is_active
    if body.custom_description is not None:
        override.custom_description = body.custom_description
    db.commit()
    return {"ok": True, "id": override.id, "is_active": override.is_active}


@router.get("/classification-overrides/pending-review")
def list_overrides_pending_review(
    office_external_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _=Depends(auth_security.get_current_user),
):
    """Lista overrides v1 pendentes de revisao na taxonomy v2.

    Paginacao obrigatoria (regra da casa). Retorna {total, items, limit,
    offset}. Operador acessa esse endpoint do painel "Overrides
    Pendentes de Revisao" em Admin/Templates."""
    from app.models.office_classification import OfficeClassificationOverride

    base = db.query(OfficeClassificationOverride).filter(
        OfficeClassificationOverride.needs_taxonomy_review == True
    )
    if office_external_id is not None:
        base = base.filter(
            OfficeClassificationOverride.office_external_id == office_external_id
        )

    total = base.count()
    rows = (
        base.order_by(
            OfficeClassificationOverride.office_external_id,
            OfficeClassificationOverride.category,
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [
            {
                "id": o.id,
                "office_external_id": o.office_external_id,
                "category": o.category,
                "subcategory": o.subcategory,
                "action": o.action,
                "custom_description": o.custom_description,
                "is_active": o.is_active,
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "taxonomy_version": getattr(o, "taxonomy_version", None) or "v1",
                "legacy_label": getattr(o, "legacy_label", None),
                "needs_taxonomy_review": bool(getattr(o, "needs_taxonomy_review", False)),
            }
            for o in rows
        ],
        "limit": limit,
        "offset": offset,
    }


@router.post("/classification-overrides/{override_id}/migrate")
def migrate_override_to_v2(
    override_id: int,
    body: OverrideMigratePayload,
    db: Session = Depends(get_db),
    _=Depends(auth_security.get_current_user),
):
    """Migra um override v1 pra v2 - operador re-aponta a classificacao na
    arvore nova (filtrada pelo polo_scope do escritorio do override).

    Demais campos (custom_description, is_active, action) sao preservados
    como estavam. Atualiza category/subcategory pros valores selecionados,
    marca taxonomy_version='v2' e zera needs_taxonomy_review."""
    from app.models.office_classification import OfficeClassificationOverride
    from app.services.classifier.taxonomy import (
        validate_classification, repair_classification,
    )
    from app.models.legal_one import LegalOneOffice

    override = db.query(OfficeClassificationOverride).filter_by(id=override_id).first()
    if not override:
        raise HTTPException(404, "Override nao encontrado.")

    target_polo: Optional[str] = None
    office = (
        db.query(LegalOneOffice)
        .filter(LegalOneOffice.external_id == override.office_external_id)
        .first()
    )
    if office is not None:
        ps = getattr(office, "polo_scope", None)
        if ps in ("ativo", "passivo"):
            target_polo = ps

    cat_clean, sub_clean = repair_classification(
        body.category,
        body.subcategory or "-",
        polo_scope=target_polo,
        taxonomy_version="v2",
    )
    if not validate_classification(
        cat_clean, sub_clean,
        polo_scope=target_polo,
        taxonomy_version="v2",
    ):
        raise HTTPException(
            400,
            f"(categoria='{body.category}', subcategoria='{body.subcategory}') nao existe na taxonomia v2 do polo '{target_polo or 'ambos'}'.",
        )

    override.category = cat_clean
    override.subcategory = sub_clean if sub_clean != "-" else None
    override.taxonomy_version = "v2"
    override.needs_taxonomy_review = False
    db.commit()
    db.refresh(override)
    return {
        "id": override.id,
        "office_external_id": override.office_external_id,
        "category": override.category,
        "subcategory": override.subcategory,
        "action": override.action,
        "is_active": override.is_active,
        "taxonomy_version": override.taxonomy_version,
        "needs_taxonomy_review": override.needs_taxonomy_review,
    }


# ATENÇÃO: ordem das rotas DELETE importa. O FastAPI avalia rotas na
# ordem de declaração — `/bulk` PRECISA vir antes de `/{override_id}`,
# senão o "bulk" do path entra como valor de override_id, falha o cast
# pra int e devolve 422 ("value is not a valid integer"). O frontend
# então renderiza o `detail` como `[object Object]` no toast.
@router.post("/classification-overrides/bulk")
def bulk_create_classification_overrides(
    body: OverrideBulkRequest,
    db: Session = Depends(get_db),
    _=Depends(auth_security.get_current_user),
):
    """
    Aplica o mesmo override a múltiplos escritórios (ou a TODOS, se
    `office_external_ids` for omitido/vazio). Ignora silenciosamente
    escritórios que já possuem a combinação (category/subcategory/action).
    """
    from app.models.office_classification import OfficeClassificationOverride
    from app.models.legal_one import LegalOneOffice

    # `manual_mode` é um override singleton por escritório (sem categoria
    # nem subcategoria) que sinaliza "este escritório está em regime manual
    # de classificações" — taxonomia efetiva passa a ser apenas o que foi
    # explicitamente adicionado via include_custom + templates ativos.
    if body.action not in ("exclude", "include_custom", "manual_mode"):
        raise HTTPException(
            400,
            "action deve ser 'exclude', 'include_custom' ou 'manual_mode'",
        )

    target_ids = body.office_external_ids
    if not target_ids:
        target_ids = [
            o.external_id for o in db.query(LegalOneOffice).all() if o.external_id is not None
        ]

    if not target_ids:
        raise HTTPException(400, "Nenhum escritório disponível para aplicar o override.")

    created = 0
    skipped = 0
    for office_id in target_ids:
        existing = (
            db.query(OfficeClassificationOverride)
            .filter(
                OfficeClassificationOverride.office_external_id == office_id,
                OfficeClassificationOverride.category == body.category,
                OfficeClassificationOverride.subcategory == body.subcategory,
                OfficeClassificationOverride.action == body.action,
            )
            .first()
        )
        if existing:
            skipped += 1
            continue
        db.add(
            OfficeClassificationOverride(
                office_external_id=office_id,
                category=body.category,
                subcategory=body.subcategory,
                action=body.action,
                custom_description=body.custom_description,
            )
        )
        created += 1
    db.commit()
    return {
        "created": created,
        "skipped_existing": skipped,
        "total_offices": len(target_ids),
    }


@router.post("/classification-overrides/bulk-for-office")
def bulk_create_overrides_for_office(
    body: OverrideBulkForOfficeRequest,
    db: Session = Depends(get_db),
    _=Depends(auth_security.get_current_user),
):
    """
    Cria N overrides para UM escritorio de uma vez.

    Caso de uso principal: modal "Adicionar classificacoes" do regime
    manual. Operador marca varias combinacoes (category, subcategory) na
    taxonomia base e elas viram overrides include_custom em batch.

    Idempotente - combinacoes ja existentes sao contadas em
    skipped_existing, nao geram erro.
    """
    from app.models.office_classification import OfficeClassificationOverride

    if body.action not in ("exclude", "include_custom"):
        raise HTTPException(
            400,
            "action deve ser exclude ou include_custom "
            "(manual_mode e singleton, use o endpoint individual)",
        )

    if not body.items:
        return {"created": 0, "skipped_existing": 0, "total": 0}

    # Carrega todas as combinacoes existentes pra esse escritorio de uma
    # tacada so (evita N queries no loop).
    existing = {
        (ov.category, ov.subcategory, ov.action)
        for ov in db.query(OfficeClassificationOverride)
        .filter(
            OfficeClassificationOverride.office_external_id == body.office_external_id,
        )
        .all()
    }

    created = 0
    skipped = 0
    seen_in_request: set[tuple[str, str | None, str]] = set()

    for item in body.items:
        sub = item.subcategory or None
        key = (item.category, sub, body.action)

        # Evita duplicatas dentro do proprio request (operador marcou a
        # mesma combinacao 2x - pouco provavel mas barato proteger).
        if key in seen_in_request:
            skipped += 1
            continue
        seen_in_request.add(key)

        if key in existing:
            skipped += 1
            continue

        db.add(
            OfficeClassificationOverride(
                office_external_id=body.office_external_id,
                category=item.category,
                subcategory=sub,
                action=body.action,
                custom_description=item.custom_description or body.custom_description,
            )
        )
        created += 1

    db.commit()
    return {
        "created": created,
        "skipped_existing": skipped,
        "total": len(body.items),
    }


@router.delete("/classification-overrides/bulk")
def bulk_delete_classification_overrides(
    category: str = Query(...),
    subcategory: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _=Depends(auth_security.get_current_user),
):
    """
    Remove overrides que batem (category + subcategory opcional + action opcional)
    de TODOS os escritórios de uma vez.
    """
    from app.models.office_classification import OfficeClassificationOverride

    q = db.query(OfficeClassificationOverride).filter(
        OfficeClassificationOverride.category == category,
    )
    if subcategory is None:
        q = q.filter(OfficeClassificationOverride.subcategory.is_(None))
    else:
        q = q.filter(OfficeClassificationOverride.subcategory == subcategory)
    if action:
        q = q.filter(OfficeClassificationOverride.action == action)

    count = q.count()
    q.delete(synchronize_session=False)
    db.commit()
    return {"deleted": count}


@router.delete("/classification-overrides/{override_id}")
def delete_classification_override(
    override_id: int,
    db: Session = Depends(get_db),
    _=Depends(auth_security.get_current_user),
):
    """Remove um override de classificação (por id, escritório único)."""
    from app.models.office_classification import OfficeClassificationOverride

    override = db.query(OfficeClassificationOverride).filter_by(id=override_id).first()
    if not override:
        raise HTTPException(404, "Override não encontrado.")
    db.delete(override)
    db.commit()
    return {"ok": True}


@router.get("/classification-taxonomy")
def get_classification_taxonomy(
    office_external_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _=Depends(auth_security.get_current_user),
):
    """
    Retorna a taxonomia de classificacoes ATIVA (respeita
    `taxonomy_active_version`). Usada pelo filtro de Classificacao na
    PublicationsPage.

    Antes esse endpoint retornava `CLASSIFICATION_TREE` (constante v1
    legada) — entao com `taxonomy_active_version=v2` o filtro mostrava
    cats v1 ("2 Grau - Civel", "Sentenca"...) que nao casavam com cat
    nenhuma das publicacoes ja classificadas pela IA em v2
    ("Recursos e Julgamentos em 2 Grau", "Sentenca e Extincao"...).
    Resultado: filtrar por classificacao retornava 0 publicacoes.

    Com office_external_id, aplica overrides do escritorio (taxonomy v2).
    """
    from app.services.classifier.taxonomy import (
        _get_active_tree, get_active_taxonomy_version,
    )
    from app.services.classifier.prompts import load_office_overrides

    # IMPORTANTE: passar `taxonomy_version=<versao_ativa>` explicitamente.
    # Sem isso _get_active_tree() retorna v1+v2 misturado (comportamento
    # default herdado pra compat com classificador antes da fase 11) — e
    # o filtro de classificacao mostraria as duas arvores juntas, dobrando
    # as opcoes e confundindo o operador. Com taxonomy_version explicito,
    # so volta a arvore da versao vigente (v2 quando seedada).
    active_v = get_active_taxonomy_version() or "v1"
    tree_raw = _get_active_tree(
        taxonomy_version=active_v,
        office_external_id=office_external_id,
    )
    tree = {k: list(v) for k, v in tree_raw.items()}

    if office_external_id:
        try:
            excluded, custom = load_office_overrides(db, office_external_id)
            if excluded:
                cats_to_remove = set()
                for cat, sub in excluded:
                    if sub is None:
                        cats_to_remove.add(cat)
                    elif cat in tree and sub in tree[cat]:
                        tree[cat].remove(sub)
                for cat in cats_to_remove:
                    tree.pop(cat, None)
            if custom:
                for item in custom:
                    cat = item.get("category", "")
                    sub = item.get("subcategory")
                    if not cat:
                        continue
                    if cat not in tree:
                        tree[cat] = []
                    if sub and sub not in tree[cat]:
                        tree[cat].append(sub)
        except Exception:
            # Falha aplicando overrides nao deve quebrar o filtro.
            pass
        return {"office_external_id": office_external_id, "taxonomy": tree}

    return {"office_external_id": None, "taxonomy": tree}
