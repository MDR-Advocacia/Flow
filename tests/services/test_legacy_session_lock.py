"""
Testes dos guarda-corpos da sessão web do L1 (incidente de 04/08/2026).

O que aconteceu: um erro não tratado no JS de login deixou o subprocess Node
vivo e pendurado por 1h+; o `subprocess.run` não tinha timeout, então o worker
ficou preso segurando o filelock da sessão. Todo caller seguinte estourava a
espera e falhava com "web_erro" — 335 tarefas numa execução do Balanceador, e
o mesmo padrão explica as 619 recusas em bloco de 31/07 e 02/08. A requisição
nem chegava ao Legal One.

O que precisa estar garantido:
  - login pendurado é MORTO no teto (_LOGIN_TIMEOUT_S) — o lock nunca fica
    preso indefinidamente;
  - login que falhou deixa marcador: os próximos falham RÁPIDO no cooldown,
    em vez de tentar o próprio login em série segurando o lock;
  - login que funciona limpa o marcador e grava a sessão;
  - a fase web do Balanceador ABORTA o lote inteiro na 1ª falha de sessão,
    marcando o motivo em tudo, em vez de rastejar grupo a grupo.
"""
import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from app.services.prazos_iniciais import legacy_task_http_cancellation_service as mod
from app.services.prazos_iniciais.legacy_task_http_cancellation_service import (
    LegacyTaskHttpCancellationService,
    SessionIndisponivelError,
)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    """Serviço com todos os paths compartilhados apontando pro tmp."""
    monkeypatch.setattr(mod, "_SESSION_CACHE_PATH", tmp_path / "session.json")
    monkeypatch.setattr(mod, "_SESSION_LOCK_PATH", tmp_path / "session.lock")
    monkeypatch.setattr(
        mod, "_LOGIN_FAILURE_MARKER", tmp_path / "login_failure.json"
    )
    return LegacyTaskHttpCancellationService(client=object(), resolver=object())


def _marcar_falha(tmp_path, *, ha_segundos: int, erro="SSO fora"):
    (tmp_path / "login_failure.json").write_text(
        json.dumps({
            "at": (
                datetime.now(timezone.utc) - timedelta(seconds=ha_segundos)
            ).isoformat(),
            "erro": erro,
        }),
        encoding="utf-8",
    )


# ── Cooldown anti-tempestade ───────────────────────────────────────────

def test_falha_recente_faz_o_caller_falhar_rapido_sem_tentar_login(
    svc, tmp_path, monkeypatch
):
    """Durante uma janela de SSO fora, N callers tentando login em série
    (1-3 min cada, segurando o lock) é a tempestade que derrubou o lote."""
    _marcar_falha(tmp_path, ha_segundos=10)

    def _nao_pode_chamar():
        raise AssertionError("não deveria tentar login dentro do cooldown")

    monkeypatch.setattr(svc, "_login_via_node", _nao_pode_chamar)
    with pytest.raises(SessionIndisponivelError) as exc:
        svc._ensure_session()
    assert "cooldown" in str(exc.value)
    assert "SSO fora" in str(exc.value)


def test_marcador_vencido_deixa_tentar_de_novo(svc, tmp_path, monkeypatch):
    _marcar_falha(tmp_path, ha_segundos=mod._LOGIN_FAILURE_COOLDOWN_S + 30)
    monkeypatch.setattr(
        svc, "_login_via_node", lambda: {".ASPXAUTH": "tok", "outro": "x"}
    )
    assert svc._ensure_session()[".ASPXAUTH"] == "tok"


def test_login_que_falha_grava_o_marcador_e_explica(svc, tmp_path, monkeypatch):
    def _explode():
        raise RuntimeError("Login Playwright falhou (exit_code=1)")

    monkeypatch.setattr(svc, "_login_via_node", _explode)
    with pytest.raises(SessionIndisponivelError) as exc:
        svc._ensure_session()
    assert "Login web do L1 falhou" in str(exc.value)
    marcador = json.loads((tmp_path / "login_failure.json").read_text("utf-8"))
    assert "exit_code=1" in marcador["erro"]


def test_login_ok_limpa_marcador_e_o_proximo_caller_usa_o_cache(
    svc, tmp_path, monkeypatch
):
    _marcar_falha(tmp_path, ha_segundos=mod._LOGIN_FAILURE_COOLDOWN_S + 30)
    chamadas = []

    def _login():
        chamadas.append(1)
        return {".ASPXAUTH": "tok"}

    monkeypatch.setattr(svc, "_login_via_node", _login)
    svc._ensure_session()
    assert not (tmp_path / "login_failure.json").exists()
    # segunda chamada: fast path do arquivo, sem novo login
    svc._ensure_session()
    assert len(chamadas) == 1


# ── Teto do login (a peça central) ─────────────────────────────────────

def test_login_pendurado_e_morto_no_teto(svc, tmp_path, monkeypatch):
    """O caso real: node vivo e pendurado por 1h+ segurando o lock."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setattr(mod, "resolve_runner_script", lambda: tmp_path / "r.js")
    (tmp_path / "r.js").write_text("// stub", encoding="utf-8")
    monkeypatch.setattr(mod, "resolve_node_binary", lambda: "node")
    monkeypatch.setattr(mod, "resolve_web_credentials", lambda: {})
    monkeypatch.setattr(svc, "_resolve_login_paths", lambda: run_dir)

    matou = []

    class _ProcPendurado:
        pid = 4242

        def wait(self, timeout=None):
            if not matou:
                raise subprocess.TimeoutExpired(cmd="node", timeout=timeout)
            return 137

        def kill(self):
            matou.append("kill")

    monkeypatch.setattr(
        mod.subprocess, "Popen", lambda *a, **k: _ProcPendurado()
    )
    monkeypatch.setattr(mod.os, "killpg", lambda pid, sig: matou.append("killpg"),
                        raising=False)

    with pytest.raises(RuntimeError) as exc:
        svc._login_via_node()
    assert "excedeu" in str(exc.value)
    assert matou, "o processo pendurado tem que ser morto"


# ── Fase web do Balanceador aborta o lote ──────────────────────────────

def test_fase_web_aborta_o_lote_inteiro_na_primeira_falha_de_sessao(monkeypatch):
    """Sem sessão, insistir grupo a grupo custa minutos de lock POR GRUPO pra
    falhar igual — a execução de 491 tarefas rastejou por horas assim."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.session import Base
    from app.models.performance import BalanceadorReatribuirJob
    from app.services.performance import reatribuir_job as rj

    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    db = sessionmaker(bind=eng)()
    job = BalanceadorReatribuirJob(id="teste-lock", team="bb-reu", status="running",
                                   total=4, feito=4)
    db.add(job)
    db.commit()

    tentativas = []

    class _SvcSemSessao:
        def __init__(self, *a, **k):
            pass

        def post_reassign(self, **kw):
            tentativas.append(kw["task_ids"])
            raise SessionIndisponivelError("Timeout (>240s) esperando o lock")

    monkeypatch.setattr(mod, "LegacyTaskHttpCancellationService", _SvcSemSessao)
    monkeypatch.setattr(
        "app.services.performance.balanceador._users", lambda: []
    )
    monkeypatch.setattr(rj, "_abortado", lambda *a: False)

    # dois grupos: (1,1)->2 com idx 0-1 e (3,3)->2 com idx 2-3
    wf_queue = [
        {"task_id": 101, "cid": 2, "idx": 0, "origem": "tarefa", "exec_de": 1, "resp_de": 1},
        {"task_id": 102, "cid": 2, "idx": 1, "origem": "tarefa", "exec_de": 1, "resp_de": 1},
        {"task_id": 201, "cid": 2, "idx": 2, "origem": "tarefa", "exec_de": 3, "resp_de": 3},
        {"task_id": 202, "cid": 2, "idx": 3, "origem": "tarefa", "exec_de": 3, "resp_de": 3},
    ]
    detalhe = [{"task_id": q["task_id"], "reason": "web_pendente"} for q in wf_queue]

    rj._fase_workflow_web(db, job, object(), wf_queue, detalhe)

    assert len(tentativas) == 1, "1ª falha de sessão para o lote — não insiste"
    assert all(d["reason"] == "web_erro" for d in detalhe)
    assert all(d.get("erro") for d in detalhe), "todo mundo sai com motivo"
    assert "interrompido sem tentar" in detalhe[2]["erro"]
    assert job.workflow_bloqueadas == 4
    db.close()
