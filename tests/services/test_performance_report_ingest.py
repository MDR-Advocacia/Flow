import json

from app.services.performance import report_ingest


def test_mesmo_relatorio_reconhece_snapshots_novos_e_legados():
    rel = {"id": "9123", "title": "token-Agenda Analytics (417)"}

    assert report_ingest._mesmo_relatorio(rel, {"report_id": 9123})
    assert report_ingest._mesmo_relatorio(rel, {"relatorio": "9123"})
    assert report_ingest._mesmo_relatorio(
        rel, {"relatorio": "token-Agenda Analytics (417)"}
    )
    assert not report_ingest._mesmo_relatorio(
        rel, {"report_id": 9122, "relatorio": "outro relatório"}
    )


def test_fallback_manual_nao_reingere_o_snapshot_anterior(monkeypatch):
    from app.services.prazos_iniciais import legacy_task_helpers

    rel = {
        "id": "9123",
        "title": "token-Agenda Analytics (417)",
        "data": "24/08/2026",
    }

    class SessaoSemDownload:
        def get(self, *_args, **_kwargs):
            raise AssertionError("o relatório repetido não deve ser baixado")

    monkeypatch.setattr(legacy_task_helpers, "web_base_url", lambda: "https://l1")
    monkeypatch.setattr(report_ingest, "_session", lambda: SessaoSemDownload())
    monkeypatch.setattr(report_ingest, "_find_latest", lambda _s, _b: rel)
    monkeypatch.setattr(report_ingest, "get_last_sync", lambda: {"report_id": 9123})

    resultado = report_ingest.baixar_e_ingerir(
        object(), force=True, only_if_new=True
    )

    assert resultado == {
        "ok": False,
        "motivo": "nenhum_relatorio_novo",
        "relatorio": rel["title"],
        "report_id": rel["id"],
        "data": rel["data"],
    }


def test_last_sync_ignora_cache_local_de_outro_worker(monkeypatch):
    from app.services import app_settings

    eventos = []
    payload = {"ok": True, "em": "2026-08-24T13:01:02-03:00"}
    monkeypatch.setattr(
        app_settings,
        "invalidate_app_settings_cache",
        lambda key: eventos.append(("invalidate", key)),
    )
    monkeypatch.setattr(
        app_settings,
        "get_setting",
        lambda key, default=None: eventos.append(("get", key)) or json.dumps(payload),
    )

    assert report_ingest.get_last_sync() == payload
    assert eventos == [
        ("invalidate", report_ingest.SETTING_LAST_SYNC),
        ("get", report_ingest.SETTING_LAST_SYNC),
    ]
