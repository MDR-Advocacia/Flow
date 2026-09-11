"""Jobs do Fluxo Embargos à Execução (APScheduler + advisory lock).

Decisão do operador (11/09/2026):
  • relatório do L1 (modelo 799) UMA vez por dia, 7h20 — gera, baixa e importa
    só caso novo (data de corte);
  • partes do BB UMA passagem por dia, de madrugada — Chromium o dia inteiro
    pilharia o servidor; o board tem botão pra disparar na hora;
  • monitor do tribunal de hora em hora das 6h às 20h — consome os cards com
    consulta vencida (a cadência real em dias úteis mora no card).

Chaves de lock: 826100009 relatório, ...010 partes, ...011 monitor
(001-008 já em uso; ver portal_verify_worker).
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_LOCK_RELATORIO = 826100009
_LOCK_PARTES = 826100010
_LOCK_MONITOR = 826100011
_RAIZ = Path(__file__).resolve().parents[3]
_TZ = "America/Sao_Paulo"
SETTING_PARTES_STATUS = "embargos_execucao_partes_status"


def _tick_relatorio() -> None:
    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.services.embargos_execucao import relatorio_l1
    from app.services.onerequest._concurrency import single_worker_lock

    if not settings.embargos_execucao_relatorio_ativo:
        return
    with single_worker_lock(_LOCK_RELATORIO) as got:
        if not got or relatorio_l1.em_andamento():
            return
        db = SessionLocal()
        try:
            r = relatorio_l1.gerar_e_importar(db)
            logger.info("Embargos relatório: %s", {k: r.get(k) for k in ("ok", "report_id", "novas", "antes_do_corte", "erro")})
        except Exception:  # noqa: BLE001
            logger.exception("Embargos: tick do relatório falhou.")
        finally:
            db.close()


# ── Partes do BB ─────────────────────────────────────────────────────
def status_partes() -> dict[str, Any]:
    from app.core.config import settings
    from app.services.app_settings import get_setting
    from app.services.embargos_execucao import service

    bruto = get_setting(SETTING_PARTES_STATUS, "") or ""
    try:
        st = json.loads(bruto) if bruto else {}
    except ValueError:
        st = {}
    if st.get("running") and st.get("iniciado_em"):
        try:
            ini = datetime.fromisoformat(st["iniciado_em"])
            if (service.agora() - ini).total_seconds() > (settings.embargos_execucao_partes_teto_min + 10) * 60:
                st["running"] = False
        except ValueError:
            pass
    return st


def _gravar_status_partes(**campos: Any) -> None:
    from app.services.app_settings import set_setting

    atual = status_partes()
    atual.update(campos)
    set_setting(SETTING_PARTES_STATUS, json.dumps(atual, ensure_ascii=False))


def rodar_partes_supervisionado(limite: int, teto_min: int, execucao_id: Optional[int] = None) -> dict[str, Any]:
    cmd = [sys.executable, "-m", "app.services.embargos_execucao.partes_runner", "--limite", str(limite)]
    if execucao_id:
        cmd += ["--execucao-id", str(execucao_id)]
    kwargs: dict[str, Any] = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, cwd=str(_RAIZ), env=os.environ.copy(), **kwargs)  # noqa: S603
    try:
        rc = proc.wait(timeout=max(5, teto_min) * 60)
        return {"desfecho": "terminou", "rc": rc, "pid": proc.pid}
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:  # pragma: no cover - dev Windows
                proc.kill()
        except OSError:
            proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            pass
        _registrar_estouro(proc.pid, teto_min)
        return {"desfecho": "estourou", "pid": proc.pid}


def _registrar_estouro(pid: int, teto_min: int) -> None:
    from app.db.session import SessionLocal
    from app.models.embargos_execucao import EVT_ERRO, SECAO_PARTES
    from app.services.embargos_execucao import service

    db = SessionLocal()
    try:
        service.registrar_evento(
            db, SECAO_PARTES,
            f"Coleta de partes no portal do BB travou e foi encerrada após {teto_min} min (processo {pid}). "
            "Os NPJs voltam na próxima passagem.",
            nivel=EVT_ERRO, dados={"pid": pid, "teto_min": teto_min},
        )
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    finally:
        db.close()


def rodar_partes(execucao_id: Optional[int] = None, origem: str = "agendada") -> dict[str, Any]:
    """Uma passagem da coleta de partes (agendada ou pelo botão), com lock."""
    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.services.distribuidos_bb.onelog_client import OneLogClient
    from app.services.embargos_execucao import service
    from app.services.onerequest._concurrency import single_worker_lock

    if not OneLogClient().configurado:
        _gravar_status_partes(running=False, ultimo={"erro": "OneLog não configurado.", "em": service.agora().isoformat()})
        return {"desfecho": "sem_onelog"}
    with single_worker_lock(_LOCK_PARTES) as got:
        if not got:
            return {"desfecho": "ocupado"}
        db = SessionLocal()
        try:
            na_fila = len(service.fila_partes(db, settings.embargos_execucao_partes_lote, execucao_id=execucao_id))
        finally:
            db.close()
        if not na_fila:
            _gravar_status_partes(running=False, ultimo={"na_fila": 0, "origem": origem, "em": service.agora().isoformat()})
            return {"desfecho": "fila_vazia"}
        _gravar_status_partes(running=True, iniciado_em=service.agora().isoformat(), na_fila=na_fila, origem=origem)
        try:
            r = rodar_partes_supervisionado(
                settings.embargos_execucao_partes_lote, settings.embargos_execucao_partes_teto_min, execucao_id,
            )
        finally:
            _gravar_status_partes(running=False, ultimo={"na_fila": na_fila, "origem": origem,
                                                         "em": service.agora().isoformat()})
        logger.info("Embargos partes (%s): %s", origem, r)
        return {**r, "na_fila": na_fila}


def disparar_partes_manual(execucao_id: Optional[int] = None) -> bool:
    """Botão do board/da execução. False = já tem passagem rodando."""
    if status_partes().get("running"):
        return False
    _gravar_status_partes(running=True, iniciado_em=_agora_iso(), origem="manual")
    threading.Thread(
        target=rodar_partes, kwargs={"execucao_id": execucao_id, "origem": "manual"},
        name="embargos-partes-manual", daemon=True,
    ).start()
    return True


def _agora_iso() -> str:
    from app.services.embargos_execucao import service

    return service.agora().isoformat()


def _tick_partes() -> None:
    from app.core.config import settings

    if not settings.embargos_execucao_partes_ativo:
        return
    try:
        rodar_partes(origem="agendada")
    except Exception:  # noqa: BLE001
        logger.exception("Embargos: tick das partes falhou.")


def _tick_monitor() -> None:
    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.services.embargos_execucao import monitor
    from app.services.onerequest._concurrency import single_worker_lock

    if not settings.embargos_execucao_monitor_ativo:
        return
    with single_worker_lock(_LOCK_MONITOR) as got:
        if not got:
            return
        db = SessionLocal()
        try:
            monitor.tick(db, limite=settings.embargos_execucao_monitor_lote)
        except Exception:  # noqa: BLE001
            logger.exception("Embargos: tick do monitor falhou.")
        finally:
            db.close()


def register_embargos_execucao_jobs(scheduler) -> None:
    from app.core.config import settings

    scheduler.add_job(
        _tick_relatorio,
        trigger=CronTrigger(hour=settings.embargos_execucao_relatorio_horarios, minute=20, timezone=_TZ),
        id="embargos_execucao_relatorio", replace_existing=True, max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        _tick_partes,
        trigger=CronTrigger(hour=settings.embargos_execucao_partes_hora, minute=0, timezone=_TZ),
        id="embargos_execucao_partes", replace_existing=True, max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        _tick_monitor,
        trigger=CronTrigger(hour="6-20", minute=40, timezone=_TZ),
        id="embargos_execucao_monitor", replace_existing=True, max_instances=1, coalesce=True,
    )
    logger.info(
        "Embargos à Execução: jobs registrados — relatório (%sh20), partes (%sh), monitor (6h-20h).",
        settings.embargos_execucao_relatorio_horarios, settings.embargos_execucao_partes_hora,
    )
