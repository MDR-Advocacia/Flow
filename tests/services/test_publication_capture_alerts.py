"""
Testes do alerta da captura de publicações.

O que precisa estar garantido:
  - falha na captura AVISA alguém (era o buraco: em 30/07/2026 a rodada falhou
    nos 13 escritórios e ninguém soube);
  - UM alerta por rodada, não um por escritório;
  - sem destinatário configurado, o silêncio fica registrado como ERROR — não
    passa batido de novo;
  - o alerta NUNCA derruba a captura, aconteça o que acontecer com o SMTP.
"""
import logging

import pytest

from app.services import publication_capture_alerts as alertas


@pytest.fixture
def enviados(monkeypatch):
    """Captura as chamadas ao sender SMTP em vez de mandar e-mail."""
    chamadas = []

    def _fake(**kw):
        chamadas.append(kw)
        return True

    monkeypatch.setattr(
        "app.services.mail_service.send_failure_report",
        lambda **kw: _fake(**kw),
    )
    return chamadas


@pytest.fixture
def com_destinatario(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "publication_alert_email", "ti@mdradvocacia.com")
    return settings


# ── Falha ──────────────────────────────────────────────────────────────

def test_falha_total_avisa_e_manda_subir_a_planilha(enviados, com_destinatario):
    alertas.alertar_falha_captura(
        escritorios_falha=[1, 2, 3], escritorios_ok=[],
        erro="HTTP 502 do Legal One", janela="30/07 01:00 a 30/07 02:00",
    )
    assert len(enviados) == 1
    envio = enviados[0]
    assert envio["recipients"] == "ti@mdradvocacia.com"
    assert envio["batch_source"] == "Captura de Publicações"
    item = envio["failed_items"][0]
    assert "3 de 3" in item["cnj"]
    assert "502" in item["motivo"]
    # Sem NENHUM escritório capturando, o operador precisa saber o que fazer.
    assert "Importar planilha" in item["motivo"]


def test_falha_parcial_nao_manda_subir_planilha(enviados, com_destinatario):
    """Se parte capturou, o caminho manual não é a orientação certa."""
    alertas.alertar_falha_captura(
        escritorios_falha=[1], escritorios_ok=[2, 3], erro="timeout",
    )
    item = enviados[0]["failed_items"][0]
    assert "1 de 3" in item["cnj"]
    assert "Importar planilha" not in item["motivo"]


def test_um_alerta_por_rodada_e_nao_um_por_escritorio(enviados, com_destinatario):
    """13 escritórios caídos = 1 e-mail. Alerta que vira ruído ninguém lê."""
    alertas.alertar_falha_captura(
        escritorios_falha=list(range(1, 14)), escritorios_ok=[], erro="L1 fora",
    )
    assert len(enviados) == 1
    assert "13 de 13" in enviados[0]["failed_items"][0]["cnj"]


def test_resultado_da_contingencia_entra_no_alerta(enviados, com_destinatario):
    alertas.alertar_falha_captura(
        escritorios_falha=[1], escritorios_ok=[], erro="502",
        contingencia="não resolveu (planilha_invalida)",
    )
    assert "planilha_invalida" in enviados[0]["failed_items"][0]["motivo"]


# ── Destinatário ───────────────────────────────────────────────────────

def test_cai_no_email_to_quando_nao_ha_destinatario_especifico(enviados, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "publication_alert_email", None)
    monkeypatch.setattr(settings, "email_to", "fallback@mdradvocacia.com")
    alertas.alertar_falha_captura(escritorios_falha=[1], escritorios_ok=[], erro="x")
    assert enviados[0]["recipients"] == "fallback@mdradvocacia.com"


def test_sem_destinatario_registra_error_em_vez_de_silenciar(enviados, monkeypatch, caplog):
    from app.core.config import settings

    monkeypatch.setattr(settings, "publication_alert_email", None)
    monkeypatch.setattr(settings, "email_to", None)
    with caplog.at_level(logging.ERROR):
        alertas.alertar_falha_captura(
            escritorios_falha=[1, 2], escritorios_ok=[], erro="x",
        )
    assert enviados == []
    # O ponto: a falta de destinatário não pode ser mais um silêncio.
    assert any(r.levelno >= logging.ERROR for r in caplog.records)
    assert "ninguém vai ser avisado" in caplog.text


# ── Robustez ───────────────────────────────────────────────────────────

def test_alerta_nunca_derruba_a_captura(monkeypatch, com_destinatario):
    """SMTP explodindo não pode virar exceção na rodada."""
    def _explode(**kw):
        raise RuntimeError("SMTP fora do ar")

    monkeypatch.setattr("app.services.mail_service.send_failure_report", _explode)
    # Não deve levantar.
    alertas.alertar_falha_captura(escritorios_falha=[1], escritorios_ok=[], erro="x")
    alertas.alertar_contingencia_ativada(
        total_publicacoes=10, processos=10, report_id=1,
    )


# ── Contingência ───────────────────────────────────────────────────────

def test_contingencia_ativada_avisa_que_nada_foi_perdido(enviados, com_destinatario):
    alertas.alertar_contingencia_ativada(
        total_publicacoes=1238, processos=1238, report_id=13432,
        janela="29/07/2026 a 30/07/2026", erro_api="HTTP 502",
    )
    assert len(enviados) == 1
    item = enviados[0]["failed_items"][0]
    assert "contingência" in enviados[0]["batch_source"].lower()
    assert "1238" in item["motivo"]
    assert "13432" in item["motivo"]
    assert "Nada foi perdido" in item["motivo"]
    # Degradação silenciosa é como se perde a próxima.
    assert "API do Legal One" in item["motivo"]
