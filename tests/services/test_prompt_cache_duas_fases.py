# -*- coding: utf-8 -*-
"""Trava o fluxo de DUAS FASES do prompt caching (pub011).

Cada teste guarda uma lição que custou dinheiro ou risco:

  - o aquecimento vai como BATCH e o lote real só parte depois que ele fecha
    (aquecer com chamada síncrona não produzia acerto: lotes 147-149 gravaram
    milhões de tokens e o cache saiu mais caro que não cachear);
  - AQUECENDO sombreia registros na coleta, senão uma segunda passada cria
    lote duplicado com os mesmos registros;
  - aquecimento que não fecha NÃO segura a fila — manda sem cache;
  - `max_tokens` do aquecimento é 1, porque 0 é rejeitado dentro de batch.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.publication_batch import (
    PUB_BATCH_STATUS_SUBMITTED,
    PUB_BATCH_STATUS_WARMING,
    PublicationBatchClassification,
)
from app.models.publication_search import (
    RECORD_STATUS_NEW,
    PublicationRecord,
    PublicationSearch,
)
from app.services.publication_batch_classifier import PublicationBatchClassifier


class _AiFake:
    """Cliente falso: registra o que foi submetido e devolve status roteirizado."""

    model = "claude-haiku-4-5-fake"
    max_tokens = 4096

    def __init__(self, status_aquecimento="ended"):
        self.status_aquecimento = status_aquecimento
        self.submissoes: list[list[dict]] = []
        self.consultas: list[str] = []

    # reusa a implementação real de montagem, que é o que queremos exercitar
    from app.services.classifier.ai_client import AnthropicClassifierClient as _Real
    _system_blocks = _Real._system_blocks
    build_batch_request = _Real.build_batch_request
    build_warm_requests = _Real.build_warm_requests

    async def submit_batch(self, requests):
        self.submissoes.append(requests)
        return {"id": f"msgbatch_fake_{len(self.submissoes)}",
                "processing_status": "in_progress"}

    async def get_batch_status(self, batch_id):
        self.consultas.append(batch_id)
        return {"processing_status": self.status_aquecimento}


_seq = [0]


def _registro(db, texto="Intime-se o autor para manifestar em 15 dias."):
    busca = PublicationSearch(
        status="CONCLUIDO",
        date_from="2026-08-01T00:00:00Z",
        date_to="2026-08-01T23:59:59Z",
    )
    db.add(busca)
    db.flush()
    _seq[0] += 1
    rec = PublicationRecord(
        search_id=busca.id,
        legal_one_update_id=900000 + _seq[0],
        description=texto,
        status=RECORD_STATUS_NEW,
        is_duplicate=False,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


@pytest.mark.asyncio
async def test_submit_com_cache_nao_envia_o_lote_real_ainda(db_session, monkeypatch):
    """Fase 1: o lote nasce AQUECENDO e o único batch enviado é o aquecimento."""
    monkeypatch.setattr(
        "app.services.publication_batch_classifier.settings."
        "classifier_prompt_cache_enabled", True, raising=False,
    )
    ai = _AiFake()
    svc = PublicationBatchClassifier(db=db_session, ai_client=ai)
    rec = _registro(db_session)

    batch = await svc.submit_batch([rec])

    assert batch.status == PUB_BATCH_STATUS_WARMING
    assert batch.warm_batch_id, "o id do batch de aquecimento tem que ficar gravado"
    assert batch.anthropic_batch_id is None, "o lote real NÃO pode ter partido"
    assert len(ai.submissoes) == 1, "só o aquecimento foi submetido"
    # uma requisição por prefixo distinto — aqui só existe um
    assert len(ai.submissoes[0]) == batch.warm_prefixos == 1


@pytest.mark.asyncio
async def test_aquecimento_usa_max_tokens_1_e_cache_no_system(db_session, monkeypatch):
    """0 é rejeitado dentro de batch; e o cache_control fica no system."""
    monkeypatch.setattr(
        "app.services.publication_batch_classifier.settings."
        "classifier_prompt_cache_enabled", True, raising=False,
    )
    ai = _AiFake()
    svc = PublicationBatchClassifier(db=db_session, ai_client=ai)
    await svc.submit_batch([_registro(db_session)])

    req = ai.submissoes[0][0]
    assert req["params"]["max_tokens"] == 1
    system = req["params"]["system"]
    assert isinstance(system, list) and system[0].get("cache_control"), (
        "o aquecimento precisa marcar cache_control no system"
    )
    assert "cache_control" not in str(req["params"]["messages"]), (
        "cache_control NUNCA na mensagem do usuário — ela muda a cada requisição"
    )


@pytest.mark.asyncio
async def test_promocao_envia_o_lote_real_com_cache(db_session, monkeypatch):
    """Fase 2: aquecimento fechado -> lote real parte, reusando o registro."""
    monkeypatch.setattr(
        "app.services.publication_batch_classifier.settings."
        "classifier_prompt_cache_enabled", True, raising=False,
    )
    ai = _AiFake(status_aquecimento="ended")
    svc = PublicationBatchClassifier(db=db_session, ai_client=ai)
    rec = _registro(db_session)
    batch = await svc.submit_batch([rec])
    batch_id = batch.id

    resultado = await svc.promover_aquecidos()

    assert resultado["promovidos"] == 1
    db_session.refresh(batch)
    assert batch.id == batch_id, "promoção reusa a MESMA linha, não cria outra"
    assert batch.status == PUB_BATCH_STATUS_SUBMITTED
    assert batch.anthropic_batch_id
    assert batch.warm_ended_at is not None
    assert len(ai.submissoes) == 2, "aquecimento + lote real"
    # o lote real precisa sair COM cache_control, senão o aquecimento foi à toa
    assert ai.submissoes[1][0]["params"]["system"][0].get("cache_control")


@pytest.mark.asyncio
async def test_aquecimento_que_nao_fecha_nao_segura_a_fila(db_session, monkeypatch):
    """Estourou a janela -> manda SEM cache. Fila parada é pior que lote caro."""
    monkeypatch.setattr(
        "app.services.publication_batch_classifier.settings."
        "classifier_prompt_cache_enabled", True, raising=False,
    )
    ai = _AiFake(status_aquecimento="in_progress")
    svc = PublicationBatchClassifier(db=db_session, ai_client=ai)
    batch = await svc.submit_batch([_registro(db_session)])

    # ainda dentro da janela: espera, não promove
    assert (await svc.promover_aquecidos())["aguardando"] == 1
    db_session.refresh(batch)
    assert batch.status == PUB_BATCH_STATUS_WARMING

    # envelheceu além do limite
    batch.warm_started_at = datetime.now(timezone.utc) - timedelta(hours=3)
    db_session.commit()

    resultado = await svc.promover_aquecidos()
    assert resultado["sem_cache"] == 1
    db_session.refresh(batch)
    assert batch.status == PUB_BATCH_STATUS_SUBMITTED
    # Sem cache o `system` volta a ser string crua (sem blocos, sem
    # cache_control) — exatamente o payload do comportamento anterior.
    assert isinstance(ai.submissoes[-1][0]["params"]["system"], str), (
        "sem aquecimento confirmado o lote sai SEM cache"
    )


def test_aquecendo_sombreia_registros_na_coleta(db_session):
    """Sem isto, uma segunda coleta duplicaria o lote — o bug dos #29/#30."""
    svc = PublicationBatchClassifier(db=db_session, ai_client=_AiFake())
    rec = _registro(db_session)
    assert rec.id in [r.id for r in svc.collect_pending_records()]

    db_session.add(PublicationBatchClassification(
        status=PUB_BATCH_STATUS_WARMING, total_records=1, record_ids=[rec.id],
        model_used="x", warm_batch_id="msgbatch_warm",
    ))
    db_session.commit()

    assert rec.id not in [r.id for r in svc.collect_pending_records()]


@pytest.mark.asyncio
async def test_cache_desligado_envia_direto(db_session, monkeypatch):
    """Com a flag off nada aquece e o lote parte na hora (comportamento antigo)."""
    monkeypatch.setattr(
        "app.services.publication_batch_classifier.settings."
        "classifier_prompt_cache_enabled", False, raising=False,
    )
    ai = _AiFake()
    svc = PublicationBatchClassifier(db=db_session, ai_client=ai)

    batch = await svc.submit_batch([_registro(db_session)])

    assert batch.status == PUB_BATCH_STATUS_SUBMITTED
    assert batch.warm_batch_id is None
    assert len(ai.submissoes) == 1, "nenhum batch de aquecimento"
    assert isinstance(ai.submissoes[0][0]["params"]["system"], str), (
        "com a flag desligada o system vai como string crua, sem cache_control"
    )
