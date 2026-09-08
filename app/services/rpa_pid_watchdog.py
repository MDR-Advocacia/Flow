"""Vigia de PIDs do container: mata RPA pendurado antes que ele derrube a API.

O INCIDENTE (08/09/2026)
------------------------
Operadoras reportaram o módulo de publicações lento, com "erros diversos" e
sem conseguir inserir tarefa. Não era banco nem lógica: o container da API
estava **sem PIDs**.

    pids.current = 300 / pids.max = 300
    RuntimeError: can't start new thread
    /bin/sh: 1: Cannot fork          (o healthcheck nem forkava — 671 falhas seguidas)

Quem ocupava as 300: runners de RPA pendurados havia DIAS, cada um segurando
uma árvore de Chrome viva —

    treat-publications.js   3d10h   6 chrome-headless
    treat-publications.js   1d07h   6 chrome-headless
    cancel-legacy-task.js     20h   5 chrome-headless
    undetected_chromedriver 3d05h   4 chrome

Cada Chrome carrega 12–22 threads; três runners pendurados consomem o
orçamento inteiro. A partir daí QUALQUER requisição que precise de thread
falha — e o sintoma aparece longe da causa, no módulo que a pessoa estava
usando. O Tratamento Web vinha falhando desde 06/09 (execuções 243–246, 0
processados) e cada tentativa deixava mais um Chrome órfão: o vazamento se
realimentava.

O QUE ESTE VIGIA FAZ
--------------------
A cada ciclo:

1. mede `pids.current`/`pids.max` do cgroup do próprio container;
2. mata processo de RPA (chrome/node/chromedriver) parado há mais de
   `RPA_WATCHDOG_IDADE_MAX_MIN` — runner honesto termina em minutos, então
   idade alta é prova de pendurado. NUNCA toca em python/uvicorn;
3. avisa por e-mail quando reapou alguém ou quando a ocupação passa de
   `RPA_WATCHDOG_ALERTA_PCT` — o alerta chega ANTES do teto, não depois.

Só o worker líder registra o job (mesma regra dos outros trabalhos de fundo).
Best-effort do começo ao fim: vigia que derruba a aplicação é pior que o
problema que ele vigia.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import time
from pathlib import Path
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# Nomes de processo que são RPA e podem ser mortos quando envelhecem.
# `python` está FORA de propósito: é o uvicorn e os workers da aplicação.
_RPA_COMMS = re.compile(r"^(chrome|chromium|node|undetected_chro|chromedriver|Xvfb)", re.I)

# Runner honesto do L1 fecha em poucos minutos. Duas horas é folga larga
# pra rodada pesada (o tratamento de 2.4k publicações leva ~30 min) e ainda
# assim pega o pendurado muito antes de ele acumular.
_IDADE_MAX_MIN = int(os.environ.get("RPA_WATCHDOG_IDADE_MAX_MIN", "120"))
# Acima disso o e-mail sai mesmo sem ter matado ninguém: é a curva subindo.
_ALERTA_PCT = int(os.environ.get("RPA_WATCHDOG_ALERTA_PCT", "70"))
# Não repete o mesmo e-mail antes disso (o job roda de 10 em 10 min).
_ALERTA_INTERVALO_S = int(os.environ.get("RPA_WATCHDOG_ALERTA_INTERVALO_S", "3600"))

_ultimo_alerta = {"t": 0.0}


# ── leitura do cgroup ──────────────────────────────────────────────────


def _ler_int(caminho: Path) -> Optional[int]:
    try:
        txt = caminho.read_text().strip()
    except OSError:
        return None
    if txt == "max":
        return None
    try:
        return int(txt)
    except ValueError:
        return None


def ocupacao_pids() -> tuple[Optional[int], Optional[int]]:
    """(current, max) de PIDs do cgroup deste processo. (None, None) se ilegível.

    cgroup v2 primeiro (é o que o host usa); v1 como plano B para ambiente
    antigo. Fora de container os arquivos não existem e o vigia vira no-op —
    o que é o certo: em desenvolvimento não há teto pra estourar.
    """
    for base in (Path("/sys/fs/cgroup"), Path("/sys/fs/cgroup/pids")):
        atual = _ler_int(base / "pids.current")
        if atual is None:
            continue
        return atual, _ler_int(base / "pids.max")
    return None, None


# ── varredura de processos ─────────────────────────────────────────────


def _idade_segundos(pid: str) -> Optional[float]:
    """Idade do processo, do /proc — sem depender de `ps` (que precisa forkar,
    justamente o que falha quando o container está sem PIDs)."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            campos = fh.read().split()
        # campo 22 (0-based 21) = starttime em ticks desde o boot
        starttime = int(campos[21])
        with open("/proc/uptime", "rb") as fh:
            uptime = float(fh.read().split()[0])
        hz = os.sysconf("SC_CLK_TCK") or 100
        return uptime - (starttime / hz)
    except (OSError, IndexError, ValueError):
        return None


def _comm(pid: str) -> str:
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return ""


def _cmdline(pid: str) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", "replace"
        ).strip()
    except OSError:
        return ""


def listar_rpa_pendurado(idade_max_min: int = _IDADE_MAX_MIN) -> list[dict]:
    """Processos de RPA parados há mais que o limite. Só leitura."""
    achados: list[dict] = []
    limite = idade_max_min * 60
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return achados
    for pid in pids:
        comm = _comm(pid)
        if not comm or not _RPA_COMMS.match(comm):
            continue
        idade = _idade_segundos(pid)
        if idade is None or idade < limite:
            continue
        achados.append({
            "pid": int(pid),
            "comm": comm,
            "idade_min": round(idade / 60),
            "cmd": _cmdline(pid)[:120],
        })
    return sorted(achados, key=lambda x: -x["idade_min"])


def _matar(pid: int) -> bool:
    """SIGKILL direto: o alvo está pendurado justamente por não responder."""
    try:
        os.kill(pid, signal.SIGKILL)
        return True
    except ProcessLookupError:
        return True                      # já morreu — objetivo cumprido
    except OSError:
        logger.warning("Watchdog RPA: sem permissão pra matar o PID %s.", pid)
        return False


# ── alerta ─────────────────────────────────────────────────────────────


def _avisar(assunto: str, linhas: list[dict], *, forcar: bool = False) -> None:
    agora = time.monotonic()
    if not forcar and (agora - _ultimo_alerta["t"]) < _ALERTA_INTERVALO_S:
        logger.info("Watchdog RPA: alerta suprimido (janela de repetição).")
        return
    try:
        from app.services.mail_service import send_failure_report

        destinatarios = (
            getattr(settings, "publication_alert_email", None)
            or getattr(settings, "distribuidos_bb_alert_email", None)
        )
        if not destinatarios:
            logger.warning("Watchdog RPA: sem destinatário de alerta configurado.")
            return
        send_failure_report(
            failed_items=linhas,
            batch_source=assunto,
            recipients=destinatarios,
            system_name="Flow",
        )
        _ultimo_alerta["t"] = agora
        logger.info("Watchdog RPA: alerta enviado para %s.", destinatarios)
    except Exception:  # noqa: BLE001
        logger.exception("Watchdog RPA: falha ao enviar o alerta (ignorado).")


# ── ciclo ──────────────────────────────────────────────────────────────


def rodar_ciclo(*, matar: bool = True) -> dict:
    """Mede, reapa e alerta. Devolve o resumo (usado nos testes e no endpoint)."""
    atual, teto = ocupacao_pids()
    pct = round(100.0 * atual / teto) if (atual and teto) else None
    pendurados = listar_rpa_pendurado()

    mortos = []
    if matar:
        for p in pendurados:
            if _matar(p["pid"]):
                mortos.append(p)

    resumo = {
        "pids_atual": atual,
        "pids_max": teto,
        "ocupacao_pct": pct,
        "pendurados": len(pendurados),
        "mortos": len(mortos),
        "detalhe": pendurados[:20],
    }

    if mortos:
        logger.warning(
            "Watchdog RPA: %d processo(s) pendurado(s) removido(s) (ocupação %s%%).",
            len(mortos), pct,
        )
        _avisar(
            "Watchdog de RPA — processos pendurados removidos",
            [{
                "cnj": f"PID {m['pid']} · {m['comm']} · parado há {m['idade_min']} min",
                "motivo": (
                    f"Processo de RPA pendurado foi encerrado para liberar PIDs do "
                    f"container (ocupação {pct}% de {teto}). Comando: {m['cmd']}"
                ),
            } for m in mortos],
            forcar=True,          # reap é evento, não ruído: sempre avisa
        )
    elif pct is not None and pct >= _ALERTA_PCT:
        logger.warning("Watchdog RPA: ocupação de PIDs em %s%% (%s/%s).", pct, atual, teto)
        _avisar(
            "Watchdog de RPA — ocupação de PIDs alta",
            [{
                "cnj": f"PIDs {atual}/{teto} ({pct}%)",
                "motivo": (
                    "A ocupação de PIDs do container passou do limite de alerta. "
                    "Quando chega a 100% a API para de criar thread e o sintoma "
                    "aparece como lentidão e erro aleatório nos módulos. Nenhum "
                    "processo de RPA estava pendurado o bastante para ser "
                    "removido — verificar o que está consumindo."
                ),
            }],
        )
    return resumo


def register_rpa_pid_watchdog_job(scheduler) -> None:
    """Job periódico do vigia — registrado só no worker líder."""
    if not getattr(settings, "rpa_pid_watchdog_enabled", True):
        logger.info("Watchdog RPA desabilitado por configuração.")
        return

    def _tick():
        try:
            rodar_ciclo(matar=True)
        except Exception:  # noqa: BLE001
            logger.exception("Watchdog RPA: ciclo falhou (ignorado).")

    scheduler.add_job(
        _tick,
        trigger="interval",
        minutes=int(os.environ.get("RPA_WATCHDOG_INTERVALO_MIN", "10")),
        id="rpa_pid_watchdog",
        name="Vigia de PIDs / RPA pendurado",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info(
        "Watchdog de PIDs registrado (a cada %s min, mata RPA parado há %s min, "
        "alerta em %s%%).",
        os.environ.get("RPA_WATCHDOG_INTERVALO_MIN", "10"), _IDADE_MAX_MIN, _ALERTA_PCT,
    )
