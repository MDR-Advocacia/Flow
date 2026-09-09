# -*- coding: utf-8 -*-
"""O vigia de PID mata RPA pendurado — e nunca a própria aplicação.

Nasceu do incidente de 08/09/2026: três runners de RPA travados havia dias
seguravam 300 dos 300 PIDs do container e a API parou de criar thread. O
sintoma apareceu longe da causa — "o módulo de publicações está lento", "não
consigo inserir tarefa" — porque QUALQUER requisição que precise de thread
falha quando o orçamento acaba.

A propriedade que este arquivo protege é a inversa da função: um vigia que
mata o processo errado é infinitamente pior que o problema que ele vigia. Por
isso o teste de `python`/`uvicorn` é o mais importante daqui — ele não mede
uma feature, mede um acidente que não pode acontecer.
"""
import pytest

from app.services import rpa_pid_watchdog as wd


@pytest.fixture
def proc_falso(monkeypatch):
    """Um /proc de mentira: {pid: (comm, idade_em_minutos)}."""
    tabela = {}

    monkeypatch.setattr(wd.os, "listdir", lambda p: list(tabela))
    monkeypatch.setattr(wd, "_comm", lambda pid: tabela[pid][0])
    monkeypatch.setattr(wd, "_idade_segundos", lambda pid: tabela[pid][1] * 60)
    monkeypatch.setattr(wd, "_cmdline", lambda pid: "cmd de " + tabela[pid][0])
    return tabela


def test_nunca_mata_python_nem_uvicorn(proc_falso):
    """A aplicação é velha DE PROPÓSITO — idade nela não é sintoma.

    Sem esta trava, o vigia derruba a API que ele existe para proteger.
    """
    proc_falso.update({
        "1":   ("python", 60 * 24 * 30),      # o uvicorn, de pé há um mês
        "7":   ("uvicorn", 60 * 24 * 30),
        "99":  ("chrome", 60 * 24 * 3),       # runner pendurado há 3 dias
    })

    achados = wd.listar_rpa_pendurado(idade_max_min=120)

    assert [a["pid"] for a in achados] == [99]


def test_rpa_novo_nao_e_pendurado(proc_falso):
    """Runner trabalhando agora não pode ser morto no meio do serviço.

    O tratamento de 2.4k publicações leva ~30 min; o corte de 120 min é folga
    larga de propósito, porque matar trabalho legítimo custa mais caro que
    esperar mais uma volta do job.
    """
    proc_falso.update({
        "50": ("node", 25),                   # rodando ha 25 min: honesto
        "51": ("chrome", 20),
        "80": ("node", 60 * 24),              # 1 dia: pendurado
    })

    assert [a["pid"] for a in wd.listar_rpa_pendurado(idade_max_min=120)] == [80]


def test_ciclo_mata_e_reporta(proc_falso, monkeypatch):
    """O ciclo completo: mede, mata, e o resumo conta o que houve."""
    proc_falso.update({"1": ("python", 99999), "99": ("chrome", 60 * 24 * 3)})
    mortos = []
    monkeypatch.setattr(wd.os, "kill", lambda pid, sig: mortos.append(pid), raising=False)
    # Windows nao tem SIGKILL; producao e' Linux, onde ele e' 9.
    monkeypatch.setattr(wd.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(wd, "ocupacao_pids", lambda: (290, 300))
    monkeypatch.setattr(wd, "_avisar", lambda *a, **k: None)

    resumo = wd.rodar_ciclo(matar=True)

    assert mortos == [99], "matou o processo errado (ou nenhum)"
    assert resumo["ocupacao_pct"] == 97
    assert resumo["mortos"] == 1


def test_fora_de_container_o_vigia_e_inofensivo(proc_falso, monkeypatch):
    """Sem cgroup legível (dev, fora de container) não há teto pra estourar.

    O vigia continua varrendo, mas a ocupação vira None em vez de explodir —
    vigia que quebra o boot em desenvolvimento não sobrevive à primeira
    semana.
    """
    monkeypatch.setattr(wd, "ocupacao_pids", lambda: (None, None))
    monkeypatch.setattr(wd, "_avisar", lambda *a, **k: None)
    monkeypatch.setattr(wd.os, "kill", lambda pid, sig: None, raising=False)

    resumo = wd.rodar_ciclo(matar=False)

    assert resumo["ocupacao_pct"] is None
    assert resumo["mortos"] == 0


# ── zumbi e repetição (08/09/2026, 22h) ────────────────────────────────


def test_zumbi_nao_e_pendurado_nem_e_morto(proc_falso, monkeypatch):
    """SIGKILL em zumbi não faz nada — só o pai colhe. Listá-lo como
    'pendurado' e 'removido' a cada ciclo era mentira repetida por e-mail."""
    proc_falso.update({"808": ("undetected_chro", 150), "99": ("chrome", 60 * 24)})
    monkeypatch.setattr(wd, "_estado", lambda pid: "Z" if pid == "808" else "S")

    achados = wd.listar_rpa_pendurado(idade_max_min=120)

    assert [a["pid"] for a in achados] == [99], "zumbi entrou na lista"


def test_mesmo_conjunto_de_pids_nao_repete_o_email(proc_falso, monkeypatch):
    """Processo que 'morre' de novo no ciclo seguinte não morreu: repetir o
    e-mail não muda nada e treina a ignorar o vigia."""
    proc_falso.update({"99": ("chrome", 60 * 24)})
    monkeypatch.setattr(wd, "_estado", lambda pid: "S")
    monkeypatch.setattr(wd.os, "kill", lambda pid, sig: None, raising=False)
    monkeypatch.setattr(wd.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(wd, "ocupacao_pids", lambda: (30, 800))
    wd._ultimo_reap.clear()
    avisos = []
    monkeypatch.setattr(wd, "_avisar", lambda *a, **k: avisos.append(a))

    wd.rodar_ciclo(matar=True)
    r2 = wd.rodar_ciclo(matar=True)

    assert len(avisos) == 1
    assert r2.get("email_repetido_suprimido") is True

    # um PID NOVO no conjunto volta a avisar
    proc_falso["77"] = ("node", 60 * 24)
    wd.rodar_ciclo(matar=True)
    assert len(avisos) == 2


def test_xvfb_nunca_e_alvo_por_mais_velho_que_seja(proc_falso, monkeypatch):
    """O display :99 nasce com o container e vive pra sempre — idade nele não
    é sintoma. Matá-lo deixa a coleta do BB (não-headless) sem onde abrir o
    Chrome. Em 08/09/2026 o vigia o listou a cada tick (192 min), com e-mail."""
    proc_falso.update({"10": ("Xvfb", 60 * 24 * 3), "99": ("chrome", 60 * 24)})
    monkeypatch.setattr(wd, "_estado", lambda pid: "S")

    assert [a["pid"] for a in wd.listar_rpa_pendurado(idade_max_min=120)] == [99]
