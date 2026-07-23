"""Alertas por e-mail do módulo Distribuídos BB.

Mesmo mecanismo do alerta de falha do batch de classificação de publicações:
reusa o sender SMTP de `mail_service`, destinatários em
`settings.distribuidos_bb_alert_email` (env DISTRIBUIDOS_BB_ALERT_EMAIL).
Best-effort: NUNCA levanta exceção pro caller.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("distribuidos_bb.alertas")


def alertar_falha_cadastro(
    *,
    contexto: str,
    erro: str,
    planilha_id: Optional[int] = None,
    planilha_nome: Optional[str] = None,
    total_processos: Optional[int] = None,
    run_id: Optional[int] = None,
) -> None:
    """Avisa por e-mail que o cadastro automático no L1 falhou.

    `contexto`: de onde veio ("auto-cadastro da coleta", "retry automático", …).
    """
    try:
        from app.core.config import settings
        from app.services.mail_service import send_failure_report

        destinatarios = settings.distribuidos_bb_alert_email
        if not destinatarios:
            logger.warning(
                "Falha no cadastro BB (%s), mas DISTRIBUIDOS_BB_ALERT_EMAIL vazio — e-mail não enviado.",
                contexto,
            )
            return
        rotulo = planilha_nome or (f"planilha #{planilha_id}" if planilha_id else "sem planilha")
        qtd = f" · {total_processos} processo(s)" if total_processos else ""
        send_failure_report(
            failed_items=[{
                "cnj": f"{rotulo}{qtd}",
                "motivo": (erro or "erro desconhecido")[:1500],
                "execution_id": run_id,
            }],
            batch_source=f"Cadastro de Processo — Distribuídos BB ({contexto})",
            recipients=destinatarios,
            system_name="Flow",
        )
        logger.info("Alerta de falha do cadastro BB enviado (%s, planilha %s).", contexto, planilha_id)
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao enviar o alerta de e-mail do cadastro BB (ignorado).")
