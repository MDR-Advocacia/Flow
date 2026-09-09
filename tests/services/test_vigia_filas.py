# -*- coding: utf-8 -*-
"""Vigia de Filas: cada invariante enxerga o incidente que o motivou.

Os cenários abaixo são os de 08/09/2026, um por um. O contrato do vigia é
simples e por isso testável: fila com resultado incoerente com o status →
violação; fila saudável → silêncio; um invariante quebrado não cala os outros;
e o mesmo problema não vira e-mail a cada 10 minutos.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.batch_execution import (
    BATCH_STATUS_COMPLETED,
    BATCH_STATUS_PROCESSING,
    BatchExecution,
    BatchExecutionItem,
)
from app.models.distribuidos_bb import (
    CLIENTE_BB,
    POOL_NOVO,
    POOL_PENDENTE_CADASTRO,
    PROC_DISTRIBUIDO,
    RUN_EM_ANDAMENTO,
    BbProcesso,
    BbRun,
)
from app.models.publication_batch import (
    PUB_BATCH_STATUS_APPLIED,
    PUB_BATCH_STATUS_IN_PROGRESS,
    PublicationBatchClassification,
)
from app.models.publication_search import (
    RECORD_STATUS_CLASSIFIED,
    RECORD_STATUS_NEW,
    SEARCH_STATUS_COMPLETED,
    PublicationRecord,
    PublicationSearch,
)
from app.models.publication_treatment import (
    QUEUE_STATUS_PENDING,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_RUNNING,
    PublicationTreatmentItem,
    PublicationTreatmentRun,
)
from app.services import vigia_filas as vf

AGORA = datetime.now(timezone.utc)


def _ha(**kw):
    return AGORA - timedelta(**kw)


@pytest.fixture(autouse=True)
def _memoria_limpa():
    vf._avisadas.clear()
    yield
    vf._avisadas.clear()


@pytest.fixture
def enviados(monkeypatch):
    chamadas = []

    def _fake(**kw):
        chamadas.append(kw)
        return True

    monkeypatch.setattr("app.services.mail_service.send_failure_report", _fake)
    monkeypatch.setattr(vf.settings, "publication_alert_email", "ti@mdradvocacia.com")
    return chamadas


def _invariantes(db, nome):
    return [v for v in vf.inspecionar(db, AGORA) if v.invariante == nome]


# ── saudável ────────────────────────────────────────────────────────────


def test_banco_saudavel_nao_gera_violacao_nem_email(db_session, enviados):
    db_session.add(BbRun(status="CONCLUIDO", iniciado_em=_ha(minutes=30), concluido_em=_ha(minutes=25)))
    db_session.add(BatchExecution(source="Planilha", status=BATCH_STATUS_COMPLETED,
                                  start_time=_ha(hours=1), total_items=3, success_count=3))
    db_session.commit()

    r = vf.rodar_ciclo(db_session)

    assert r["violacoes"] == []
    assert enviados == []


# ── sucesso vazio (lote 6019) ───────────────────────────────────────────


def test_lote_concluido_com_zero_processados_e_linhas_pendentes(db_session):
    lote = BatchExecution(source="Planilha", source_filename="agendamento.xlsx",
                          status=BATCH_STATUS_COMPLETED, start_time=_ha(hours=7),
                          total_items=2756, success_count=0, failure_count=0)
    db_session.add(lote)
    db_session.flush()
    for i in range(3):
        db_session.add(BatchExecutionItem(execution_id=lote.id, process_number=str(i), status="PENDENTE"))
    db_session.commit()

    achados = _invariantes(db_session, "sucesso vazio")

    assert len(achados) == 1 and achados[0].gravidade == vf.GRAVE
    assert "2756" in achados[0].mensagem and "visto verde é falso" in achados[0].mensagem
    # e o mesmo lote também aparece pelo lado dos itens órfãos
    assert any(v.invariante == "itens órfãos" for v in vf.inspecionar(db_session, AGORA))


def test_lote_concluido_que_processou_de_verdade_passa(db_session):
    db_session.add(BatchExecution(source="Planilha", status=BATCH_STATUS_COMPLETED,
                                  start_time=_ha(hours=1), total_items=321, success_count=252,
                                  failure_count=69))
    db_session.commit()
    assert _invariantes(db_session, "sucesso vazio") == []


def test_tratamento_concluido_sem_processar_nada(db_session):
    db_session.add(PublicationTreatmentRun(status=RUN_STATUS_COMPLETED, total_items=2382,
                                           processed_items=0, started_at=_ha(hours=5)))
    db_session.commit()
    achados = _invariantes(db_session, "sucesso vazio")
    assert len(achados) == 1 and achados[0].fila == "Tratamento Web"


# ── sem sinal de vida (coleta 240, TW 246) ──────────────────────────────


def test_coleta_em_andamento_ha_horas(db_session):
    db_session.add(BbRun(status=RUN_EM_ANDAMENTO, iniciado_em=_ha(hours=3)))
    db_session.add(BbRun(status=RUN_EM_ANDAMENTO, iniciado_em=_ha(minutes=10)))  # legítima
    db_session.commit()

    achados = _invariantes(db_session, "sem sinal de vida")

    assert len(achados) == 1
    assert achados[0].fila == "Coleta BB" and "3 h" in achados[0].mensagem


def test_tratamento_web_iniciando_desde_a_madrugada(db_session):
    db_session.add(PublicationTreatmentRun(status="INICIANDO", total_items=2684,
                                           processed_items=0, started_at=_ha(hours=17)))
    db_session.commit()
    achados = _invariantes(db_session, "sem sinal de vida")
    assert [v.fila for v in achados] == ["Tratamento Web"]


def test_lote_com_garra_vencida_e_ninguem_retomou(db_session):
    db_session.add(BatchExecution(source="Planilha", status=BATCH_STATUS_PROCESSING,
                                  start_time=_ha(hours=3), total_items=10,
                                  lease_expires_at=_ha(hours=2)))
    db_session.commit()
    achados = _invariantes(db_session, "sem sinal de vida")
    assert len(achados) == 1 and achados[0].gravidade == vf.AVISO


# ── fila parada (2.809 do TW; NOVO sem classificar) ─────────────────────


def _publicacoes(db, n, status, ha):
    busca = PublicationSearch(status=SEARCH_STATUS_COMPLETED, date_from="2026-09-01")
    db.add(busca)
    db.flush()
    recs = []
    for i in range(n):
        r = PublicationRecord(search_id=busca.id, legal_one_update_id=700000 + i + int(ha.timestamp()) % 1000 * 10,
                              status=status, created_at=ha, is_duplicate=False)
        db.add(r)
        recs.append(r)
    db.flush()
    return recs


def test_tratamento_web_parado_com_pendentes_velhos_e_sem_execucao(db_session):
    recs = _publicacoes(db_session, 5, RECORD_STATUS_CLASSIFIED, _ha(days=3))
    for i, r in enumerate(recs):
        db_session.add(PublicationTreatmentItem(
            publication_record_id=r.id, legal_one_update_id=r.legal_one_update_id,
            source_record_status="CLASSIFICADO", target_status="AGENDADO",
            queue_status=QUEUE_STATUS_PENDING, created_at=_ha(days=3),
        ))
    db_session.commit()

    achados = _invariantes(db_session, "fila parada")

    tw = [v for v in achados if v.fila == "Tratamento Web"]
    assert len(tw) == 1 and "5 publicação" in tw[0].mensagem


def test_tratamento_web_com_execucao_ativa_nao_e_fila_parada(db_session):
    recs = _publicacoes(db_session, 2, RECORD_STATUS_CLASSIFIED, _ha(days=3))
    for r in recs:
        db_session.add(PublicationTreatmentItem(
            publication_record_id=r.id, legal_one_update_id=r.legal_one_update_id,
            source_record_status="CLASSIFICADO", target_status="AGENDADO",
            queue_status=QUEUE_STATUS_PENDING, created_at=_ha(days=3),
        ))
    db_session.add(PublicationTreatmentRun(status=RUN_STATUS_RUNNING, total_items=2,
                                           processed_items=1, started_at=_ha(minutes=5)))
    db_session.commit()
    assert [v for v in _invariantes(db_session, "fila parada") if v.fila == "Tratamento Web"] == []


def test_publicacoes_novas_sem_classificar_ha_mais_de_30h(db_session):
    _publicacoes(db_session, 4, RECORD_STATUS_NEW, _ha(hours=40))
    _publicacoes(db_session, 2, RECORD_STATUS_NEW, _ha(hours=2))  # da noite, normal
    db_session.commit()
    achados = [v for v in _invariantes(db_session, "fila parada") if v.fila.startswith("Classificação")]
    assert len(achados) == 1 and "4 publicação" in achados[0].mensagem


# ── refém (os 15 da run 240) ────────────────────────────────────────────


def test_ciencia_dada_sem_planilha_e_refem(db_session):
    run = BbRun(status="ERRO", iniciado_em=_ha(hours=3), concluido_em=_ha(hours=2))
    db_session.add(run)
    db_session.flush()
    for i in range(15):
        db_session.add(BbProcesso(cliente=CLIENTE_BB, fingerprint=f"r:{i}", status=PROC_DISTRIBUIDO,
                                  planilha_status=POOL_NOVO, run_id=run.id,
                                  ciencia_dada_em=_ha(hours=2, minutes=30)))
    # já enviado pro L1: não é refém
    db_session.add(BbProcesso(cliente=CLIENTE_BB, fingerprint="ok", status=PROC_DISTRIBUIDO,
                              planilha_status=POOL_PENDENTE_CADASTRO, ciencia_dada_em=_ha(hours=5)))
    db_session.commit()

    achados = _invariantes(db_session, "refém")

    assert len(achados) == 1 and achados[0].gravidade == vf.GRAVE
    assert "15 processo" in achados[0].mensagem


def test_ciencia_recente_ainda_nao_e_refem(db_session):
    db_session.add(BbProcesso(cliente=CLIENTE_BB, fingerprint="novo", status=PROC_DISTRIBUIDO,
                              planilha_status=POOL_NOVO, ciencia_dada_em=_ha(minutes=20)))
    db_session.commit()
    assert _invariantes(db_session, "refém") == []


# ── lote externo não aplicado (batch 164) ───────────────────────────────


def test_lote_anthropic_em_processamento_ha_horas(db_session):
    db_session.add(PublicationBatchClassification(status=PUB_BATCH_STATUS_IN_PROGRESS,
                                                  total_records=688, created_at=_ha(hours=5)))
    db_session.add(PublicationBatchClassification(status=PUB_BATCH_STATUS_APPLIED,
                                                  total_records=728, created_at=_ha(hours=30)))
    db_session.commit()
    achados = _invariantes(db_session, "lote externo não aplicado")
    assert len(achados) == 1 and "688" in achados[0].mensagem


# ── e-mail: uma vez, com tudo, e sem repetir ────────────────────────────


def test_email_sai_uma_vez_com_todas_as_violacoes_e_nao_repete(db_session, enviados):
    db_session.add(BbRun(status=RUN_EM_ANDAMENTO, iniciado_em=_ha(hours=3)))
    db_session.add(PublicationBatchClassification(status=PUB_BATCH_STATUS_IN_PROGRESS,
                                                  total_records=1, created_at=_ha(hours=5)))
    db_session.commit()

    r1 = vf.rodar_ciclo(db_session)
    r2 = vf.rodar_ciclo(db_session)

    assert r1["email_enviado"] is True and r1["novas"] == 2
    assert len(enviados) == 1, "o mesmo problema não pode virar e-mail a cada ciclo"
    assert len(enviados[0]["failed_items"]) == 2, "o e-mail carrega TODAS as violações"
    assert "2 problema(s)" in enviados[0]["batch_source"]
    assert r2["email_enviado"] is False and r2["novas"] == 0


def test_violacao_nova_reabre_o_email_e_lista_tudo(db_session, enviados):
    db_session.add(BbRun(status=RUN_EM_ANDAMENTO, iniciado_em=_ha(hours=3)))
    db_session.commit()
    vf.rodar_ciclo(db_session)

    db_session.add(PublicationBatchClassification(status=PUB_BATCH_STATUS_IN_PROGRESS,
                                                  total_records=1, created_at=_ha(hours=5)))
    db_session.commit()
    r = vf.rodar_ciclo(db_session)

    assert r["novas"] == 1 and len(enviados) == 2
    assert len(enviados[1]["failed_items"]) == 2


def test_chave_que_sumiu_pode_alertar_de_novo(db_session, enviados):
    run = BbRun(status=RUN_EM_ANDAMENTO, iniciado_em=_ha(hours=3))
    db_session.add(run)
    db_session.commit()
    vf.rodar_ciclo(db_session)
    run.status = "ERRO"
    db_session.commit()
    vf.rodar_ciclo(db_session)          # sumiu → esquece a chave
    run.status = RUN_EM_ANDAMENTO
    db_session.commit()
    vf.rodar_ciclo(db_session)          # voltou → alerta de novo
    assert len(enviados) == 2


# ── resiliência ─────────────────────────────────────────────────────────


def test_invariante_quebrado_nao_cala_os_outros(db_session, monkeypatch):
    def _explode(db, agora):
        raise RuntimeError("tabela nova sem migration")
    monkeypatch.setattr(vf, "INVARIANTES", (_explode, vf._sem_sinal_de_vida))
    db_session.add(BbRun(status=RUN_EM_ANDAMENTO, iniciado_em=_ha(hours=3)))
    db_session.commit()

    achados = vf.inspecionar(db_session, AGORA)

    assert len(achados) == 1 and achados[0].fila == "Coleta BB"


def test_smtp_fora_nao_derruba_o_ciclo(db_session, monkeypatch):
    def _explode(**kw):
        raise RuntimeError("smtp fora")
    monkeypatch.setattr("app.services.mail_service.send_failure_report", _explode)
    monkeypatch.setattr(vf.settings, "publication_alert_email", "ti@mdradvocacia.com")
    db_session.add(BbRun(status=RUN_EM_ANDAMENTO, iniciado_em=_ha(hours=3)))
    db_session.commit()

    r = vf.rodar_ciclo(db_session)

    assert len(r["violacoes"]) == 1 and r["email_enviado"] is False
