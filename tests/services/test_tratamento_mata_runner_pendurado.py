# -*- coding: utf-8 -*-
"""O reaper do Tratamento Web MATA o runner antes de marcar falha.

O INCIDENTE (08/09/2026)
------------------------
Operadoras reportaram o módulo de publicações lento e com "erros diversos".
A causa não estava em publicação nenhuma: o container da API estava sem PID.

    pids.current = 300 / pids.max = 300
    RuntimeError: can't start new thread
    /bin/sh: 1: Cannot fork

Segurando as 300 estavam runners de RPA **vivos e travados** havia dias, cada
um com sua árvore de Chrome — `treat-publications.js` de 3d10h, outro de
1d07h. O reaper já existia e já tinha marcado esses runs como FALHA; o que ele
nunca fez foi encostar no processo, porque a premissa era que o runner já
estava morto ("OOM/restart/deploy"). A premissa era falsa, e o efeito colateral
foi longe: o vazamento derrubou também a busca de responsável de pasta
(ThreadPoolExecutor sem thread), e 650 propostas de tarefa foram gravadas sem
responsável.

Marcar falha no banco não libera PID. Matar, sim — e é preciso matar o GRUPO,
porque o Chromium do Playwright não morre junto com o Node (~7 processos ficam
por runner abandonado).
"""
import os
import signal

import pytest

from app.services.publication_treatment_service import PublicationTreatmentService as S


class _Run:
    def __init__(self, pid=None, rid=246):
        self.id = rid
        self.runner_pid = pid


@pytest.fixture
def espiao(monkeypatch):
    """Troca os syscalls por um diário do que foi chamado."""
    diario = {"kill": [], "killpg": [], "vivo": True, "lider": True}

    def _kill(pid, sig):
        if not diario["vivo"]:
            raise ProcessLookupError()
        if sig != 0:
            diario["kill"].append((pid, sig))

    # raising=False porque no Windows (onde a suite roda em dev) `killpg` e
    # `getpgid` nem existem — o codigo de producao e' Linux.
    monkeypatch.setattr(os, "kill", _kill, raising=False)
    monkeypatch.setattr(
        os, "killpg", lambda pid, sig: diario["killpg"].append((pid, sig)),
        raising=False,
    )
    monkeypatch.setattr(
        os, "getpgid", lambda pid: pid if diario["lider"] else pid + 1,
        raising=False,
    )
    monkeypatch.setattr(os, "name", "posix", raising=False)
    # Windows nao tem SIGKILL; producao e' Linux, onde ele e' 9.
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    return diario


def test_mata_o_grupo_inteiro_nao_so_o_node(espiao):
    """O killpg é o ponto: leva o Node e os ~6 Chrome de uma vez."""
    desfecho = S._encerrar_runner(_Run(pid=4242))

    assert espiao["killpg"] == [(4242, 9)], "não matou o grupo de processos"
    assert espiao["kill"] == [], "não devia precisar do kill individual"
    assert "4242" in desfecho and "grupo" in desfecho


def test_processo_que_nao_lidera_grupo_leva_kill_individual(espiao):
    """Sem grupo próprio, um killpg subiria pro uvicorn — e derrubaria a API.

    Pior (sobra Chrome), mas nunca perigoso: é a troca certa.
    """
    espiao["lider"] = False

    desfecho = S._encerrar_runner(_Run(pid=4242))

    assert espiao["killpg"] == [], "killpg em grupo alheio atingiria o uvicorn"
    assert espiao["kill"] == [(4242, 9)]
    assert "processo" in desfecho


def test_runner_ja_morto_nao_vira_erro(espiao):
    """Objetivo cumprido é objetivo cumprido — não é falha a reportar."""
    espiao["vivo"] = False

    desfecho = S._encerrar_runner(_Run(pid=4242))

    assert espiao["killpg"] == [] and espiao["kill"] == []
    assert "já estava morto" in desfecho


def test_run_anterior_ao_pub015_diz_isso_em_vez_de_fingir(espiao):
    """Execução antiga não tem PID gravado. O texto do evento tem que contar
    isso, senão o operador lê "encerrado" e acredita."""
    desfecho = S._encerrar_runner(_Run(pid=None))

    assert espiao["killpg"] == [] and espiao["kill"] == []
    assert "sem PID registrado" in desfecho
