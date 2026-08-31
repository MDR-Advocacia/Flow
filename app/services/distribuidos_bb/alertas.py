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


# Quantos processos precisam ter passado pela pesquisa de vínculos, sem NENHUM
# vínculo encontrado, antes de desconfiar. Abaixo disso é ruído estatístico:
# a maioria das partes realmente não tem outra ação nossa.
VINCULO_ZERO_MINIMO = 40
# Não repete o alerta antes disso (a coleta roda de 8 em 8 horas).
VINCULO_ZERO_INTERVALO_H = 24


def checar_vinculos_zerados(db) -> bool:
    """Alerta quando a pesquisa de vínculos roda mas NUNCA acha nada.

    O motor identifica "processo nosso" comparando `numeroAdvogadoProcesso` com
    o código do advogado do MDR no BB (`vinculo_advogado_mdr`, default 8706512).
    Se o BB trocar esse código, a pesquisa continua respondendo 200 e o motor
    passa a devolver ZERO vínculos para sempre — sem erro, sem log, sem nada.
    Este guarda existe só para isso: silêncio prolongado é sintoma, não sucesso.

    Devolve True se alertou. Best-effort: nunca levanta pro caller.
    """
    try:
        from datetime import datetime, timedelta, timezone

        from app.models.distribuidos_bb import BbConfig, BbProcesso

        desde = datetime.now(timezone.utc) - timedelta(days=30)
        base = db.query(BbProcesso).filter(BbProcesso.vinculos_verificado_em >= desde)
        verificados = base.count()
        if verificados < VINCULO_ZERO_MINIMO:
            return False
        com_vinculo = base.filter(BbProcesso.vinculos_qtd > 0).count()
        if com_vinculo > 0:
            return False

        # Trava de repetição: no máximo um alerta por VINCULO_ZERO_INTERVALO_H.
        agora = datetime.now(timezone.utc)
        chave = "vinculo_alerta_zero_em"
        cfg = db.get(BbConfig, chave)
        if cfg is not None and cfg.valor:
            try:
                ultimo = datetime.fromisoformat(cfg.valor)
                if ultimo.tzinfo is None:
                    ultimo = ultimo.replace(tzinfo=timezone.utc)
                if (agora - ultimo) < timedelta(hours=VINCULO_ZERO_INTERVALO_H):
                    return False
            except (TypeError, ValueError):
                pass

        advogado = db.get(BbConfig, "vinculo_advogado_mdr")
        codigo = (advogado.valor if advogado else None) or "8706512 (default do código)"
        logger.warning(
            "Vínculos: %s processos verificados em 30 dias e NENHUM vínculo encontrado.", verificados,
        )

        from app.core.config import settings
        from app.services.mail_service import send_failure_report

        destinatarios = settings.distribuidos_bb_alert_email
        if destinatarios:
            send_failure_report(
                failed_items=[{
                    "cnj": f"{verificados} processos verificados nos últimos 30 dias",
                    "motivo": (
                        "A pesquisa de vínculos no portal do BB rodou normalmente, mas NÃO "
                        "encontrou nenhum vínculo em nenhum processo. O suspeito nº 1 é o "
                        f"código do advogado do MDR usado no filtro: {codigo}. Se o BB tiver "
                        "trocado esse número, a pesquisa responde 200 e devolve zero para "
                        "sempre, sem erro. Confira o código no portal e ajuste a config "
                        "'vinculo_advogado_mdr' em Distribuídos BB."
                    ),
                    "execution_id": None,
                }],
                batch_source="Vínculos BB — nenhum vínculo encontrado (possível filtro quebrado)",
                recipients=destinatarios,
                system_name="Flow",
            )
        else:
            logger.warning("DISTRIBUIDOS_BB_ALERT_EMAIL vazio — alerta de vínculos zerados não enviado.")

        if cfg is None:
            db.add(BbConfig(chave=chave, valor=agora.isoformat()))
        else:
            cfg.valor = agora.isoformat()
        db.commit()
        return True
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao checar vínculos zerados (ignorado).")
        return False
