# -*- coding: utf-8 -*-
"""Worker lento não é morto pelo supervisor do uvicorn (15/09/2026).

O supervisor do uvicorn 0.34 espera o pong de cada worker por 5 s fixos e mata
quem passa disso. Às 08:49 o host ficou ~40 s travado em OOM global e dois
workers da API foram mortos só por estarem lentos, levando junto requisições,
o scheduler e a thread do lote 40 do Master. O kernel não matou worker nenhum.

`app.core.uvicorn_supervisor` passa a esperar UVICORN_TIMEOUT_WORKER_HEALTHCHECK
(padrão 120 s) e mantém o resto: processo morto é trocado na hora, e worker que
não responde nem assim continua sendo reiniciado.
"""
import logging
import threading
import time
from multiprocessing import Pipe
from pathlib import Path

import pytest
from uvicorn.supervisors import multiprocess as mp

from app.core import uvicorn_supervisor as sup

precisa_do_patch = pytest.mark.skipif(
    sup.uvicorn_tem_opcao_nativa(),
    reason="uvicorn >= 0.37 tem --timeout-worker-healthcheck nativo; o patch não é usado",
)


class _Processo:
    """Imita o multiprocessing.Process que o supervisor guarda em `.process`."""

    pid = 8551

    def __init__(self, vivo=True):
        self._vivo = vivo

    def is_alive(self):
        return self._vivo


class _Worker:
    """Imita o `uvicorn.supervisors.multiprocess.Process`."""

    pid = 8551

    def __init__(self, *, vivo=True, responde=True):
        self.process = _Processo(vivo)
        self.responde = responde
        self.esperas = []

    def ping(self, timeout):
        self.esperas.append(timeout)
        return self.responde


@pytest.fixture
def tolerante(monkeypatch):
    # Guarda o original: o teardown do monkeypatch desfaz a troca da classe.
    monkeypatch.setattr(mp.Process, "is_alive", mp.Process.is_alive)
    sup.instalar_tolerancia(120)
    return mp.Process.is_alive


@precisa_do_patch
def test_uvicorn_instalado_ainda_tem_o_contrato_que_o_patch_usa():
    import inspect

    assert "timeout" in inspect.signature(mp.Process.ping).parameters
    assert isinstance(mp.Process.pid, property)
    # O laço do supervisor decide matar o worker pelo is_alive().
    assert ".is_alive(" in inspect.getsource(mp.Multiprocess.keep_subprocess_alive)


@precisa_do_patch
def test_supervisor_espera_o_pong_pela_tolerancia_e_nao_pelos_5s(tolerante):
    w = _Worker()

    # O supervisor chama sem argumento: `process.is_alive()`.
    assert tolerante(w) is True
    assert w.esperas == [120]


@precisa_do_patch
def test_worker_que_nao_responde_nem_na_tolerancia_continua_sendo_reiniciado(tolerante, caplog):
    w = _Worker(responde=False)

    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        assert tolerante(w) is False
    assert "não respondeu ao health check" in caplog.text


@precisa_do_patch
def test_processo_que_ja_morreu_e_trocado_sem_esperar_ping(tolerante):
    w = _Worker(vivo=False)

    assert tolerante(w) is False
    assert w.esperas == []


@precisa_do_patch
def test_resposta_lenta_fica_registrada_no_log(tolerante, monkeypatch, caplog):
    relogio = iter([1000.0, 1038.0])
    monkeypatch.setattr(sup.time, "monotonic", lambda: next(relogio))

    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        assert tolerante(_Worker()) is True
    assert "levou 38 s" in caplog.text


@precisa_do_patch
def test_instalar_de_novo_nao_empilha_a_troca(tolerante):
    sup.instalar_tolerancia(999)
    w = _Worker()

    assert mp.Process.is_alive(w) is True
    assert w.esperas == [120]


def _worker_com_pipe_real():
    w = mp.Process.__new__(mp.Process)
    w.parent_conn, w.child_conn = Pipe()
    w.process = _Processo(vivo=True)
    return w


def _pong_atrasado(w, atraso_s):
    def responder():
        time.sleep(atraso_s)
        w.pong()

    t = threading.Thread(target=responder, daemon=True)
    t.start()
    return t


@precisa_do_patch
def test_pipe_real_sem_tolerancia_o_mesmo_atraso_mataria_o_worker():
    w = _worker_com_pipe_real()
    t = _pong_atrasado(w, 0.5)

    # Mesmo mecanismo dos 5 s do uvicorn, em escala de teste (0,1 s).
    assert w.ping(0.1) is False
    t.join()


@precisa_do_patch
def test_pipe_real_pong_atrasado_dentro_da_tolerancia_mantem_o_worker(monkeypatch):
    monkeypatch.setattr(mp.Process, "is_alive", mp.Process.is_alive)
    sup.instalar_tolerancia(2.0)
    w = _worker_com_pipe_real()
    t = _pong_atrasado(w, 0.5)

    assert mp.Process.is_alive(w, timeout=0.1) is True
    t.join()


@pytest.mark.parametrize(
    "valor, esperado",
    [(None, 120.0), ("", 120.0), ("300", 300.0), ("abc", 120.0), ("1", 5.0)],
)
def test_tolerancia_vem_do_painel_e_nunca_fica_abaixo_do_padrao_do_uvicorn(monkeypatch, valor, esperado):
    if valor is None:
        monkeypatch.delenv(sup.ENV_TIMEOUT, raising=False)
    else:
        monkeypatch.setenv(sup.ENV_TIMEOUT, valor)

    assert sup.timeout_health_check_s() == esperado


def test_uvicorn_novo_recebe_a_opcao_nativa_uma_vez_so():
    base = ["main:app", "--workers", "4"]

    assert sup.montar_argv(base, 120, nativo=True) == base + ["--timeout-worker-healthcheck", "120"]
    ja = base + ["--timeout-worker-healthcheck=60"]
    assert sup.montar_argv(ja, 120, nativo=True) == ja
    assert sup.montar_argv(base, 120, nativo=False) == base


def test_script_de_start_sobe_a_api_pelo_supervisor_tolerante():
    script = (Path(__file__).resolve().parents[2] / "scripts" / "docker-api-start.sh").read_text(encoding="utf-8")

    assert "exec python -m app.core.uvicorn_supervisor main:app" in script
    assert "python -m uvicorn main:app" not in script
