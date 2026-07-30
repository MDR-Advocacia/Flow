"""
Testes do modo batch do scheduler de pull_publications.

Cobertura:
1. batch mode dispara 1 fetch L1 + fan-out (vs. legado N fetches).
2. Quando todos os escritorios estao em backoff, nao toca no L1.
3. Falha no fetch L1 marca TODOS os escritorios ativos como FAILED.
4. Modo legado (feature flag OFF) ainda funciona: 1 fetch por office.
5. `create_and_run_search` com `prefetched_publications` pula a chamada L1.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models as _models  # noqa: F401 — registers tables on Base.metadata
from app.db.session import Base
from app.models.legal_one import LegalOneOffice
from app.models.publication_search import PublicationSearch
from app.services.scheduled_automation_service import ScheduledAutomationService


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine, Session()


def _seed_offices(db, n: int = 3) -> list[int]:
    """Cria N escritorios L1 (internal_id=1..N, external_id=101..100+N)."""
    ids: list[int] = []
    for i in range(1, n + 1):
        off = LegalOneOffice(
            external_id=100 + i,
            name=f"Escritorio {i}",
            path=f"MDR / Escritorio {i}",
        )
        db.add(off)
        db.flush()
        ids.append(off.id)
    db.commit()
    return ids


def _stub_create_and_run_search(*, total_new_per_call=1):
    """Stub que finge que `create_and_run_search` rodou e cria 1 PublicationSearch
    no DB pra cada chamada. Captura todos os kwargs pra inspeção."""
    calls = []

    def stub(self, **kwargs):
        calls.append(kwargs)
        # Cria 1 PublicationSearch row pra preservar o contrato da UI
        # (Histórico de Buscas espera 1 linha por office).
        search = PublicationSearch(
            status="COMPLETED",
            date_from=kwargs.get("date_from"),
            date_to=kwargs.get("date_to"),
            origin_type=kwargs.get("origin_type", "OfficialJournalsCrawler"),
            office_filter=str(kwargs.get("responsible_office_id")),
            requested_by_email=kwargs.get("requested_by"),
            total_found=total_new_per_call,
            total_new=total_new_per_call,
        )
        self.db.add(search)
        self.db.commit()
        return {
            "search_id": search.id,
            "total_found": total_new_per_call,
            "total_new": total_new_per_call,
        }

    return stub, calls


def _patch_alertas(monkeypatch):
    """Captura os alertas em vez de mandar e-mail. Devolve (falhas, contingencias)."""
    falhas, contingencias = [], []
    monkeypatch.setattr(
        "app.services.publication_capture_alerts.alertar_falha_captura",
        lambda **kw: falhas.append(kw),
    )
    monkeypatch.setattr(
        "app.services.publication_capture_alerts.alertar_contingencia_ativada",
        lambda **kw: contingencias.append(kw),
    )
    return falhas, contingencias


def _patch_contingencia(monkeypatch, resultado=None):
    """Neutraliza (ou encena) a contingencia por relatorio do L1.

    Sem isso, todo teste que simula queda da API do L1 vai no SITE do Legal One
    e gera um relatorio de verdade — a suite fica lenta e passa a depender do L1
    estar no ar pra passar. `resultado=None` = contingencia nao resolve.
    """
    chamadas = []

    def _fake(db, **kw):
        chamadas.append(kw)
        return resultado or {"ok": False, "motivo": "desligada_no_teste"}

    monkeypatch.setattr(
        "app.services.publication_l1_report_fallback.capturar_publicacoes",
        _fake,
    )
    return chamadas


def _patch_l1_client_init(monkeypatch):
    """Bloqueia o __init__ do LegalOneApiClient (não tenta autenticar)."""
    from app.services.legal_one_client import LegalOneApiClient

    def fake_init(self, *args, **kwargs):
        self.access_token = "fake-token"
        self.access_token_expires_at = datetime.now(timezone.utc).timestamp() + 3600

    monkeypatch.setattr(LegalOneApiClient, "__init__", fake_init)


def test_batch_mode_calls_l1_once_and_creates_one_search_per_office(monkeypatch):
    """Cenário principal: 3 offices ativos → 1 chamada L1, 3 PublicationSearch rows."""
    _patch_l1_client_init(monkeypatch)
    engine, db = _make_session()
    try:
        office_ids = _seed_offices(db, n=3)

        # Mock fetch_publications_for_window pra retornar fixture sem hit no L1
        sample_pubs = [
            {"id": 1001, "date": "2026-05-07", "relationships": []},
            {"id": 1002, "date": "2026-05-07", "relationships": []},
        ]
        fetch_mock = MagicMock(return_value=sample_pubs)
        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.fetch_publications_for_window",
            fetch_mock,
        )

        # Stub create_and_run_search (isola o teste do persist real)
        stub, calls = _stub_create_and_run_search(total_new_per_call=2)
        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.create_and_run_search",
            stub,
        )

        svc = ScheduledAutomationService(db)
        result = svc._execute_pull_publications(
            office_ids=office_ids,
            automation_id=None,
            run_id=None,
        )

        # 1 fetch L1 só (não 3)
        assert fetch_mock.call_count == 1, (
            f"Esperava 1 fetch L1 (modo batch), recebeu {fetch_mock.call_count}"
        )

        # 3 chamadas a create_and_run_search (uma por office), todas com prefetched
        assert len(calls) == 3
        for c in calls:
            assert c.get("prefetched_publications") is sample_pubs, (
                "create_and_run_search no batch mode deve receber prefetched_publications"
            )
            assert c.get("requested_by") == "scheduler"

        # Office IDs externos passados (101, 102, 103)
        ext_ids = sorted(c["responsible_office_id"] for c in calls)
        assert ext_ids == [101, 102, 103]

        # 3 PublicationSearch rows (UI Histórico de Buscas)
        rows = db.query(PublicationSearch).all()
        assert len(rows) == 3

        # Resultado consolidado
        assert result["records_found"] == 6  # 2 por office × 3 offices
        assert sorted(result["offices_ok"]) == sorted(office_ids)
        assert result["offices_failed"] == []
        assert result["offices_skipped"] == []
    finally:
        db.close()
        engine.dispose()


def test_all_offices_skipped_returns_without_l1_call(monkeypatch):
    """Se todos os offices estão em backoff, fetch_all_publications NUNCA é chamado."""
    _patch_l1_client_init(monkeypatch)
    engine, db = _make_session()
    try:
        office_ids = _seed_offices(db, n=2)

        fetch_mock = MagicMock(return_value=[])
        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.fetch_publications_for_window",
            fetch_mock,
        )

        # Force todos os offices a "skip" (em backoff)
        monkeypatch.setattr(
            ScheduledAutomationService,
            "_should_skip_office",
            lambda self, office_id, now: True,
        )

        svc = ScheduledAutomationService(db)
        result = svc._execute_pull_publications(
            office_ids=office_ids,
            automation_id=None,
            run_id=None,
        )

        assert fetch_mock.call_count == 0
        assert result["records_found"] == 0
        assert result["offices_ok"] == []
        assert result["offices_failed"] == []
        assert sorted(result["offices_skipped"]) == sorted(office_ids)
    finally:
        db.close()
        engine.dispose()


def test_l1_fetch_failure_marks_all_active_offices_as_failed(monkeypatch):
    """Se o fetch L1 batch falha, todos os offices ativos viram FAILED nesta rodada."""
    _patch_l1_client_init(monkeypatch)
    # Contingencia por relatorio NAO resolve: o comportamento esperado aqui
    # segue sendo marcar todos os escritorios como falha.
    _patch_contingencia(monkeypatch, resultado=None)
    engine, db = _make_session()
    try:
        office_ids = _seed_offices(db, n=3)

        # fetch_all dá erro (simula timeout/rate limit/L1 down)
        fetch_mock = MagicMock(side_effect=RuntimeError("L1 timeout"))
        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.fetch_publications_for_window",
            fetch_mock,
        )

        # create_and_run_search nem deveria ser chamado nesse caso
        stub, calls = _stub_create_and_run_search()
        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.create_and_run_search",
            stub,
        )

        svc = ScheduledAutomationService(db)
        result = svc._execute_pull_publications(
            office_ids=office_ids,
            automation_id=None,
            run_id=None,
        )

        assert fetch_mock.call_count == 1
        assert len(calls) == 0, "Não deve chamar create_and_run_search se L1 falhou"
        assert result["records_found"] == 0
        assert result["offices_ok"] == []
        assert sorted(result["offices_failed"]) == sorted(office_ids)
        assert result["offices_skipped"] == []
    finally:
        db.close()
        engine.dispose()


def test_legacy_mode_calls_l1_per_office_when_flag_disabled(monkeypatch):
    """Com PUBLICATION_SCHEDULER_BATCH_MODE=False, comportamento legado: 1 fetch por office."""
    _patch_l1_client_init(monkeypatch)
    engine, db = _make_session()
    try:
        office_ids = _seed_offices(db, n=3)

        # Desliga batch mode
        from app.core.config import settings
        monkeypatch.setattr(settings, "publication_scheduler_batch_mode", False)

        fetch_mock = MagicMock(return_value=[])
        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.fetch_publications_for_window",
            fetch_mock,
        )

        stub, calls = _stub_create_and_run_search(total_new_per_call=0)
        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.create_and_run_search",
            stub,
        )

        svc = ScheduledAutomationService(db)
        svc._execute_pull_publications(
            office_ids=office_ids,
            automation_id=None,
            run_id=None,
        )

        # Em modo legado: nenhuma chamada a fetch_publications_for_window
        # (cada create_and_run_search faz seu próprio fetch interno)
        assert fetch_mock.call_count == 0

        # 3 chamadas a create_and_run_search SEM prefetched_publications
        assert len(calls) == 3
        for c in calls:
            assert c.get("prefetched_publications") is None, (
                "Modo legado não deve passar prefetched_publications"
            )
    finally:
        db.close()
        engine.dispose()


def test_create_and_run_search_with_prefetched_skips_l1_fetch(monkeypatch):
    """Service: passar `prefetched_publications` pula `client.fetch_all_publications`."""
    from app.services.legal_one_client import LegalOneApiClient
    from app.services.publication_search_service import PublicationSearchService

    _patch_l1_client_init(monkeypatch)
    engine, db = _make_session()
    try:
        # Cliente cuja fetch_all_publications vai EXPLODIR se for chamada
        client = LegalOneApiClient()
        fetch_mock = MagicMock(side_effect=AssertionError(
            "fetch_all_publications NÃO deveria ser chamado quando prefetched_publications é passado"
        ))
        client.fetch_all_publications = fetch_mock

        # Mocks pros sub-passos do fluxo (enrich + persist) — isolam o teste
        # do banco real do L1.
        monkeypatch.setattr(
            PublicationSearchService,
            "_enrich_with_lawsuit_data",
            lambda self, pubs: pubs,
        )

        svc = PublicationSearchService(db, client)

        # Sem nenhuma publicação na fixture: o filtro/persist não tem nada a fazer,
        # mas o IMPORTANTE é que o assertion no fetch_mock não dispare.
        result = svc.create_and_run_search(
            date_from="2026-05-07T00:00:00Z",
            date_to="2026-05-07T23:59:59Z",
            prefetched_publications=[],
            requested_by="test",
        )

        assert fetch_mock.call_count == 0
        assert result.get("id") is not None
        # Status PT-BR: CONCLUIDO/EXECUTANDO/FALHA/PENDENTE/CANCELADO
        assert result.get("status") in ("CONCLUIDO", "EXECUTANDO", "FALHA", "PENDENTE")
    finally:
        db.close()
        engine.dispose()


def test_contingencia_por_relatorio_salva_a_rodada_quando_a_api_do_l1_cai(monkeypatch):
    """API do L1 fora + relatorio OK = a captura acontece assim mesmo.

    E o cenario real de 30/07/2026: o /Updates respondia 502 mas o site do L1
    estava no ar. Antes, a rodada inteira virava falha e ninguem capturava nada.
    """
    _patch_l1_client_init(monkeypatch)
    engine, db = _make_session()
    try:
        office_ids = _seed_offices(db, n=3)

        fetch_mock = MagicMock(side_effect=RuntimeError("L1 502"))
        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.fetch_publications_for_window",
            fetch_mock,
        )
        stub, calls = _stub_create_and_run_search()
        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.create_and_run_search",
            stub,
        )

        publicacoes = [{
            "id": -123456,
            "originType": "OfficialJournalsCrawler",
            "typeId": 5,
            "description": "texto da publicacao",
            "date": "2026-07-30T00:00:00Z",
            "relationships": [{"linkType": "Litigation", "linkId": 3903}],
            "_cnj": "0000967-85.2024.5.05.0019",
            "_responsible_office_id": 101,
            "_lawsuit_creation_date": "2025-03-10T00:00:00Z",
        }]
        chamadas = _patch_contingencia(monkeypatch, resultado={
            "ok": True,
            "publicacoes": publicacoes,
            "report_id": 13432,
            "total": 1,
            "processos": 1,
            "data_inicio": "29/07/2026",
            "data_fim": "30/07/2026",
        })

        svc = ScheduledAutomationService(db)
        result = svc._execute_pull_publications(
            office_ids=office_ids, automation_id=None, run_id=None,
        )

        # A contingencia foi acionada UMA vez (nao por escritorio).
        assert len(chamadas) == 1
        # E o fan-out seguiu normal, com a lista vinda do relatorio.
        assert len(calls) == 3, "cada escritorio deve processar o subset dele"
        for c in calls:
            assert c["prefetched_publications"] == publicacoes
        assert result["offices_failed"] == []
        assert sorted(result["offices_ok"]) == sorted(office_ids)
    finally:
        db.close()
        engine.dispose()


def test_contingencia_desligada_mantem_o_comportamento_antigo(monkeypatch):
    """Com a chave desligada, nem tenta o relatorio — a rodada falha direto."""
    from app.core.config import settings

    _patch_l1_client_init(monkeypatch)
    monkeypatch.setattr(settings, "publication_report_fallback_enabled", False)
    chamadas = _patch_contingencia(monkeypatch, resultado={"ok": True, "publicacoes": []})
    engine, db = _make_session()
    try:
        office_ids = _seed_offices(db, n=2)
        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.fetch_publications_for_window",
            MagicMock(side_effect=RuntimeError("L1 down")),
        )
        stub, calls = _stub_create_and_run_search()
        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.create_and_run_search",
            stub,
        )

        svc = ScheduledAutomationService(db)
        result = svc._execute_pull_publications(
            office_ids=office_ids, automation_id=None, run_id=None,
        )

        assert chamadas == [], "chave desligada nao pode acionar o relatorio"
        assert len(calls) == 0
        assert sorted(result["offices_failed"]) == sorted(office_ids)
    finally:
        db.close()
        engine.dispose()


# ── Modo legado: e o que roda em PRODUCAO ──────────────────────────────
# PUBLICATION_SCHEDULER_BATCH_MODE=false no Coolify. Contingencia e alerta
# enganchados so no batch ficariam instalados no corredor errado.

def _legado(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "publication_scheduler_batch_mode", False)


def test_legado_contingencia_recupera_os_escritorios_que_falharam(monkeypatch):
    """API do L1 fora + relatorio OK = a captura acontece pelo caminho legado."""
    _patch_l1_client_init(monkeypatch)
    _legado(monkeypatch)
    falhas, contingencias = _patch_alertas(monkeypatch)
    engine, db = _make_session()
    try:
        office_ids = _seed_offices(db, n=3)
        publicacoes = [{"id": -1, "relationships": [{"linkType": "Litigation", "linkId": 1}]}]
        _patch_contingencia(monkeypatch, resultado={
            "ok": True, "publicacoes": publicacoes, "report_id": 13432,
            "total": 1, "processos": 1,
            "data_inicio": "29/07/2026", "data_fim": "30/07/2026",
        })

        chamadas = []

        def _stub(self, **kw):
            chamadas.append(kw)
            # A 1a passada (sem prefetched) falha; a da contingencia funciona.
            if kw.get("prefetched_publications") is None:
                raise RuntimeError("L1 502")
            return {"total_new": 5}

        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.create_and_run_search",
            _stub,
        )

        svc = ScheduledAutomationService(db)
        r = svc._execute_pull_publications(
            office_ids=office_ids, automation_id=None, run_id=None,
        )

        # 3 tentativas diretas + 3 pela contingencia
        assert len(chamadas) == 6
        assert sum(1 for c in chamadas if c.get("prefetched_publications")) == 3
        assert r["offices_failed"] == []
        assert sorted(r["offices_ok"]) == sorted(office_ids)
        assert r["records_found"] == 15
        # Nada foi perdido: alerta de FALHA nao vai; o de contingencia vai.
        assert falhas == []
        assert len(contingencias) == 1
        assert contingencias[0]["report_id"] == 13432
    finally:
        db.close()
        engine.dispose()


def test_legado_avisa_UMA_vez_quando_nem_a_contingencia_resolve(monkeypatch):
    """O buraco de 30/07: 13 escritorios caidos e ninguem avisado.

    Agora avisa — e UMA vez por rodada, nao uma por escritorio.
    """
    _patch_l1_client_init(monkeypatch)
    _legado(monkeypatch)
    falhas, contingencias = _patch_alertas(monkeypatch)
    engine, db = _make_session()
    try:
        office_ids = _seed_offices(db, n=13)
        _patch_contingencia(monkeypatch, resultado={"ok": False, "motivo": "relatorio_nao_criado"})

        def _stub(self, **kw):
            raise RuntimeError("L1 502")

        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.create_and_run_search",
            _stub,
        )

        svc = ScheduledAutomationService(db)
        r = svc._execute_pull_publications(
            office_ids=office_ids, automation_id=None, run_id=None,
        )

        assert len(r["offices_failed"]) == 13
        assert len(falhas) == 1, "um alerta por RODADA, nao 13"
        assert len(falhas[0]["escritorios_falha"]) == 13
        assert falhas[0]["escritorios_ok"] == []
        assert "relatorio_nao_criado" in (falhas[0]["contingencia"] or "")
        assert contingencias == []
    finally:
        db.close()
        engine.dispose()


def test_legado_falha_parcial_avisa_com_os_dois_numeros(monkeypatch):
    """Parte capturou: o alerta precisa dizer quantos de quantos."""
    _patch_l1_client_init(monkeypatch)
    _legado(monkeypatch)
    falhas, _ = _patch_alertas(monkeypatch)
    _patch_contingencia(monkeypatch, resultado={"ok": False, "motivo": "x"})
    engine, db = _make_session()
    try:
        office_ids = _seed_offices(db, n=3)
        vistos = []

        def _stub(self, **kw):
            vistos.append(kw)
            if kw.get("prefetched_publications") is None and len(vistos) == 1:
                raise RuntimeError("so o primeiro falha")
            return {"total_new": 1}

        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.create_and_run_search",
            _stub,
        )

        svc = ScheduledAutomationService(db)
        r = svc._execute_pull_publications(
            office_ids=office_ids, automation_id=None, run_id=None,
        )

        assert len(r["offices_failed"]) == 1
        assert len(r["offices_ok"]) == 2
        assert len(falhas) == 1
        assert len(falhas[0]["escritorios_falha"]) == 1
        assert len(falhas[0]["escritorios_ok"]) == 2
    finally:
        db.close()
        engine.dispose()


def test_legado_rodada_sem_falha_nao_alerta_ninguem(monkeypatch):
    """Alerta so quando ha o que alertar."""
    _patch_l1_client_init(monkeypatch)
    _legado(monkeypatch)
    falhas, contingencias = _patch_alertas(monkeypatch)
    chamou = _patch_contingencia(monkeypatch, resultado={"ok": True, "publicacoes": []})
    engine, db = _make_session()
    try:
        office_ids = _seed_offices(db, n=3)
        stub, calls = _stub_create_and_run_search(total_new_per_call=2)
        monkeypatch.setattr(
            "app.services.publication_search_service.PublicationSearchService.create_and_run_search",
            stub,
        )

        svc = ScheduledAutomationService(db)
        r = svc._execute_pull_publications(
            office_ids=office_ids, automation_id=None, run_id=None,
        )

        assert r["offices_failed"] == []
        assert falhas == [] and contingencias == []
        assert chamou == [], "sem falha, nem toca no relatorio"
    finally:
        db.close()
        engine.dispose()
