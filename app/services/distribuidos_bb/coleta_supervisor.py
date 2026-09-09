"""Supervisor da coleta Distribuídos BB: processo filho + TETO de relógio.

A coleta passa a rodar em `coleta_runner.py`, um processo em sessão própria.
Este módulo é o pai: dispara, espera até o teto e, se estourar, mata o GRUPO
inteiro (node, chrome, chromedriver — tudo que a coleta abriu), fecha o run
com a VERDADE, registra o evento e avisa por e-mail.

POR QUE (08/09/2026)
--------------------
Cinco de seis passagens agendadas travaram em dois dias. A run 240 deu
ciência em 15 notificações (irreversível no portal do BB), distribuiu e
travou dentro do Playwright com o navegador vivo — 59 min em silêncio. Como
thread dentro do uvicorn, nada podia encerrá-la: o reaper virou o status no
banco e escreveu que "o processo morreu (redeploy)" — falso —, a árvore de
Chrome ficou órfã ocupando 220 dos 300 PIDs do container, e nenhum e-mail
saiu. O padrão que NUNCA vazou nesta casa é o do login do cancel-legacy-task:
Popen em sessão própria + wait(timeout) + killpg. É ele, aplicado à coleta.

O TETO
------
Uma passagem honesta leva ~6 min. O pior caso legítimo é OneLog demorando
(até 15 min por tentativa) vezes 3 tentativas ≈ 48 min. O default de 60 min
cobre isso; acima é travamento, não trabalho. O reaper de runs zumbis fica
em 90 min como REDE DE SEGURANÇA — só age se este supervisor também morrer
(o container reiniciou no meio, por exemplo).

O QUE ELE NÃO FAZ
-----------------
Não reimplementa retentativa nem fecho de run: `executar_coleta_background`
continua fazendo isso dentro do filho. O supervisor só toca no run quando o
filho NÃO conseguiu — estouro de teto ou saída com erro antes de gravar
estado final.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.core.config import settings

logger = logging.getLogger("distribuidos_bb.supervisor")

# Raiz do projeto (onde `app/` mora): é o cwd do filho, pra `-m app....` resolver
# igual em produção (/app) e em desenvolvimento.
_RAIZ = Path(__file__).resolve().parents[3]

ACAO_ESTOURO = "Coleta morta por estouro de tempo"
ACAO_FILHO_CAIU = "Processo da coleta saiu com erro"
ACAO_FILHO_INICIADO = "Coleta iniciada em processo próprio"


def _teto_min(teto_min: Optional[int]) -> int:
    if teto_min is not None:
        return max(5, int(teto_min))
    return max(5, int(getattr(settings, "distribuidos_bb_coleta_teto_min", 60)))


def montar_comando(run_id: int, *, data_inicial: Optional[str], data_final: Optional[str],
                   coletar_envolvidos: bool) -> list[str]:
    cmd = [sys.executable, "-m", "app.services.distribuidos_bb.coleta_runner",
           "--run-id", str(run_id)]
    if data_inicial:
        cmd += ["--data-inicial", data_inicial]
    if data_final:
        cmd += ["--data-final", data_final]
    if not coletar_envolvidos:
        cmd.append("--sem-envolvidos")
    return cmd


def _matar_grupo(proc: subprocess.Popen) -> str:
    """SIGKILL no grupo (posix) — o filho nasceu líder de sessão, então o
    grupo é dele e só dele. Fallback pro PID sozinho: pior (sobra Chrome, que
    o watchdog de PIDs recolhe), nunca perigoso."""
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
            modo = "grupo"
        else:  # pragma: no cover - dev Windows
            proc.kill()
            modo = "processo"
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass
        modo = "processo"
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - kernel demorando
        pass
    return modo


def _fechar_run_se_ainda_aberto(run_id: int, *, erro: str, acao: str, dados: dict[str, Any]) -> bool:
    """Fecha o run como ERRO SÓ se o filho não o fechou antes (corrida no
    limite do teto é possível: ele grava CONCLUÍDO e morre um segundo depois).
    Devolve True se este fecho aconteceu."""
    from app.db.session import SessionLocal
    from app.models.distribuidos_bb import NIVEL_ERRO, RUN_EM_ANDAMENTO, RUN_ERRO, SECAO_SESSAO, BbRun
    from app.services.distribuidos_bb.log_service import registrar_evento

    db = SessionLocal()
    try:
        run = db.get(BbRun, run_id)
        if run is None or run.status != RUN_EM_ANDAMENTO:
            return False
        run.status = RUN_ERRO
        run.erro = erro
        run.concluido_em = datetime.now(timezone.utc)
        registrar_evento(db, secao=SECAO_SESSAO, nivel=NIVEL_ERRO, acao=acao,
                         mensagem=erro, dados=dados, run_id=run_id)
        db.commit()
        return True
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("Supervisor: falha ao fechar o run %s (ignorado).", run_id)
        return False
    finally:
        db.close()


def _avisar(contexto: str, erro: str, run_id: int) -> None:
    try:
        from app.services.distribuidos_bb.alertas import alertar_falha_cadastro

        alertar_falha_cadastro(contexto=contexto, erro=erro, run_id=run_id)
    except Exception:  # noqa: BLE001
        logger.exception("Supervisor: falha ao enviar o alerta (ignorado).")


def _registrar_inicio(run_id: int, pid: int, teto: int) -> None:
    """PID no evento: BbRun não tem coluna pra isso e migration à 1h da manhã
    não é hora. Fica auditável em bbd_eventos, que é onde o operador olha."""
    from app.db.session import SessionLocal
    from app.models.distribuidos_bb import NIVEL_INFO, SECAO_SESSAO
    from app.services.distribuidos_bb.log_service import registrar_evento

    db = SessionLocal()
    try:
        registrar_evento(
            db, secao=SECAO_SESSAO, nivel=NIVEL_INFO, acao=ACAO_FILHO_INICIADO,
            mensagem=f"Coleta rodando no processo {pid}, com teto de {teto} min.",
            dados={"pid": pid, "teto_min": teto}, run_id=run_id,
        )
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("Supervisor: não registrou o PID do filho (ignorado).", exc_info=True)
    finally:
        db.close()


def rodar_coleta_supervisionada(
    run_id: int,
    *,
    data_inicial: Optional[str],
    data_final: Optional[str],
    coletar_envolvidos: bool = True,
    teto_min: Optional[int] = None,
) -> dict[str, Any]:
    """Roda a coleta num processo filho e volta quando ele termina ou estoura.

    Bloqueante de propósito: quem chama (tick do agendador, thread do
    endpoint) já está fora do caminho das requisições, e é justamente o
    "ficar esperando com relógio" que dá a garantia.
    """
    teto = _teto_min(teto_min)
    cmd = montar_comando(run_id, data_inicial=data_inicial, data_final=data_final,
                         coletar_envolvidos=coletar_envolvidos)
    popen_kwargs: dict[str, Any] = {}
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True   # grupo próprio → killpg seguro

    logger.info("Supervisor: run %s → %s (teto %s min)", run_id, " ".join(cmd[2:]), teto)
    proc = subprocess.Popen(  # noqa: S603 - comando montado aqui, sem entrada externa
        cmd, cwd=str(_RAIZ), env=os.environ.copy(), **popen_kwargs,
    )
    _registrar_inicio(run_id, proc.pid, teto)

    try:
        rc = proc.wait(timeout=teto * 60)
    except subprocess.TimeoutExpired:
        modo = _matar_grupo(proc)
        erro = (
            f"Coleta estourou o teto de {teto} min e foi morta pelo supervisor "
            f"({modo} {proc.pid}). Travou sem sinal de vida — não é redeploy nem "
            "OneLog: o processo estava vivo e parado. O que já recebeu ciência no "
            "portal fica no pool e a recuperação do pool órfão cadastra no L1."
        )
        fechou = _fechar_run_se_ainda_aberto(
            run_id, erro=erro, acao=ACAO_ESTOURO,
            dados={"pid": proc.pid, "teto_min": teto, "modo": modo},
        )
        logger.error("Supervisor: run %s morta por estouro (%s %s).", run_id, modo, proc.pid)
        _avisar(f"coleta morta pelo supervisor após {teto} min sem terminar", erro, run_id)
        return {"desfecho": "estourou", "pid": proc.pid, "teto_min": teto, "fechou_run": fechou}

    if rc != 0:
        erro = (
            f"O processo da coleta (pid {proc.pid}) saiu com código {rc} antes de "
            "gravar o estado final do run. Ver o log do container pelo pid."
        )
        fechou = _fechar_run_se_ainda_aberto(
            run_id, erro=erro, acao=ACAO_FILHO_CAIU, dados={"pid": proc.pid, "rc": rc},
        )
        if fechou:
            # Só avisa se FOI este fecho: se o filho já tinha marcado ERRO por
            # conta própria, o e-mail dele ("Esgotou as tentativas") já saiu.
            _avisar(f"processo da coleta saiu com código {rc}", erro, run_id)
        logger.warning("Supervisor: run %s — filho saiu com rc=%s (fechou_run=%s).", run_id, rc, fechou)
        return {"desfecho": "falhou", "pid": proc.pid, "rc": rc, "fechou_run": fechou}

    logger.info("Supervisor: run %s terminou (pid %s).", run_id, proc.pid)
    return {"desfecho": "terminou", "pid": proc.pid, "rc": 0, "fechou_run": False}
