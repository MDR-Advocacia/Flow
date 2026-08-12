"""Registra no processo o motivo de ele NÃO ter ido pro Legal One.

## Por que isso existe

O import do L1 devolve, para cada linha da planilha, se ela foi aceita ou
recusada. Até 03/08/2026 as recusadas eram simplesmente puladas: o processo
ficava `PENDENTE_CADASTRO` com a coluna `erro` **NULL**, e a informação do
porquê morria ali.

Caso real: o processo 0801099-88.2026.8.14.0003 saiu na planilha 57 em
31/07/2026. O Legal One recusou a linha por congestionamento da própria
infraestrutura dele (`ServiceBusy`, código 50002 — erro transitório, a mensagem
literalmente diz "wait 10 seconds and try again"). A linha foi descartada, o
processo ficou pendente para sempre, e **nenhum registro dizia o motivo**.
Conferido na base: 917 processos, ZERO com motivo registrado.

Só foi descoberto porque o operador reparou na tela e perguntou. Um dia depois
de a captura de publicações ter falhado exatamente pelo mesmo padrão — a falha
existia, mas não tinha onde aparecer.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger("distribuidos_bb.descartes")


def _digitos(valor: Any) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def registrar_descartes(
    db,
    rel: dict[str, Any],
    *,
    run_id: Optional[int] = None,
    planilha_id: Optional[int] = None,
) -> int:
    """Grava no processo o motivo da recusa e registra o evento.

    `rel` é o relatório devolvido por `cadastrar_planilha`. Best-effort: nunca
    levanta — um problema aqui não pode derrubar o cadastro dos que deram certo.

    Devolve quantos processos receberam o motivo.
    """
    descartadas = (rel or {}).get("descartadas") or []
    if not descartadas:
        return 0

    try:
        from app.models.distribuidos_bb import (
            NIVEL_AVISO,
            POOL_PENDENTE_CADASTRO,
            SECAO_CADASTRO,
            BbProcesso,
        )
        from app.services.distribuidos_bb.log_service import registrar_evento

        # Índice por CNJ dos processos que estão esperando cadastro. Restringe à
        # planilha quando o caller sabe qual é — o mesmo CNJ pode estar em mais
        # de uma planilha ao longo do tempo.
        q = db.query(BbProcesso).filter(
            BbProcesso.planilha_status == POOL_PENDENTE_CADASTRO
        )
        if planilha_id is not None:
            q = q.filter(BbProcesso.planilha_id == planilha_id)
        por_cnj: dict[str, Any] = {}
        for p in q.all():
            d = _digitos(p.cnj)
            if d:
                por_cnj.setdefault(d, p)

        marcados = 0
        sem_processo = 0
        for item in descartadas:
            motivo = (item.get("motivo") or "").strip() or "recusada pelo Legal One"
            d = _digitos(item.get("cnj"))
            proc = por_cnj.get(d) if d else None
            if proc is None:
                sem_processo += 1
                continue
            proc.erro = motivo[:2000]
            registrar_evento(
                db,
                secao=SECAO_CADASTRO,
                nivel=NIVEL_AVISO,
                acao="Não cadastrado",
                mensagem=f"O processo não entrou no Legal One. {motivo}",
                dados={"linha_import_id": item.get("id"), "cnj": item.get("cnj")},
                processo_id=proc.id,
                run_id=run_id,
            )
            marcados += 1

        if marcados:
            db.commit()
            logger.warning(
                "Import L1: %s processo(s) NÃO cadastrados, motivo registrado em cada um.",
                marcados,
            )
        if sem_processo:
            # Linha recusada que não casou com processo nenhum: pode ser lixo de
            # import antigo no staging do L1. Fica no log, não some.
            logger.info(
                "Import L1: %s linha(s) recusada(s) sem processo correspondente "
                "na fila (provável resíduo de import anterior).", sem_processo,
            )
        return marcados
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao registrar os motivos de descarte (ignorado).")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0
