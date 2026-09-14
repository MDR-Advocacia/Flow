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

## Motivo obrigatório (14/09/2026)

Passagem 251 (12/09/2026 12:00): 2 processos com ciência dada no BB, planilha
enviada, e o import respondeu "nada novo a cadastrar" — as linhas nem tinham
voltado da revisão do L1. Sem descarte, sem CNJ pra casar (pré-processo), o
motivo não foi gravado em lugar nenhum e a passagem ficou verde com 0 pastas.
Regra do operador: processo que não virou pasta TEM que ter o motivo. Por isso:

- planilha que não enviou NADA grava o motivo geral em todos os pendentes dela;
- `motivar_pendentes_sem_motivo` é a rede de segurança do monitor: pendente que
  passou da hora sem pasta e sem motivo recebe o que se sabe de fato.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger("distribuidos_bb.descartes")

# Brasil sem horário de verão desde 2019 — offset fixo evita depender de tzdata.
_BRT = timezone(timedelta(hours=-3))
# Quanto o monitor espera, depois do envio, antes de declarar a pasta ausente.
_SEM_MOTIVO_APOS_MIN = 60


def _digitos(valor: Any) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def _utc(ts: Optional[datetime]) -> Optional[datetime]:
    """SQLite devolve naive (UTC); Postgres, aware."""
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _brt(ts: Optional[datetime]) -> str:
    return ts.astimezone(_BRT).strftime("%d/%m às %H:%M") if ts else "data desconhecida"


def _marcar(db, proc, motivo: str, *, dados: dict, run_id: Optional[int]) -> None:
    from app.models.distribuidos_bb import NIVEL_AVISO, SECAO_CADASTRO
    from app.services.distribuidos_bb.log_service import registrar_evento

    proc.erro = motivo[:2000]
    registrar_evento(
        db,
        secao=SECAO_CADASTRO,
        nivel=NIVEL_AVISO,
        acao="Não cadastrado",
        mensagem=f"O processo não entrou no Legal One. {motivo}",
        dados=dados,
        processo_id=proc.id,
        run_id=run_id if run_id is not None else proc.run_id,
    )


def registrar_descartes(
    db,
    rel: dict[str, Any],
    *,
    run_id: Optional[int] = None,
    planilha_id: Optional[int] = None,
) -> int:
    """Grava no processo o motivo de ele não ter ido pro L1 e registra o evento.

    `rel` é o relatório devolvido por `cadastrar_planilha`. Duas fontes de motivo:

    1. linha recusada pelo L1 (`descartadas`) — casada com o processo pelo CNJ;
    2. planilha que não enviou NADA num import real (`novos == 0`): todo processo
       ainda pendente dela recebe o motivo geral do import — inclusive os sem
       CNJ, que o casamento por CNJ nunca alcança.

    Best-effort: nunca levanta — um problema aqui não pode derrubar o cadastro
    dos que deram certo. Devolve quantos processos receberam o motivo.
    """
    rel = rel or {}
    descartadas = rel.get("descartadas") or []
    nada_enviado = (
        planilha_id is not None
        and "novos" in rel
        and not rel.get("dry_run")
        and int(rel.get("novos") or 0) == 0
    )
    if not descartadas and not nada_enviado:
        return 0

    try:
        from app.models.distribuidos_bb import POOL_PENDENTE_CADASTRO, BbProcesso

        # Processos esperando cadastro. Restringe à planilha quando o caller
        # sabe qual é — o mesmo CNJ pode estar em mais de uma planilha.
        q = db.query(BbProcesso).filter(
            BbProcesso.planilha_status == POOL_PENDENTE_CADASTRO
        )
        if planilha_id is not None:
            q = q.filter(BbProcesso.planilha_id == planilha_id)
        pendentes = q.all()
        por_cnj: dict[str, Any] = {}
        for p in pendentes:
            d = _digitos(p.cnj)
            if d:
                por_cnj.setdefault(d, p)

        marcados_ids: set[int] = set()
        sem_processo = 0
        for item in descartadas:
            motivo = (item.get("motivo") or "").strip() or "recusada pelo Legal One"
            d = _digitos(item.get("cnj"))
            proc = por_cnj.get(d) if d else None
            if proc is None:
                sem_processo += 1
                continue
            _marcar(
                db, proc, motivo,
                dados={"linha_import_id": item.get("id"), "cnj": item.get("cnj")},
                run_id=run_id,
            )
            marcados_ids.add(proc.id)

        if nada_enviado:
            from app.services.distribuidos_bb.import_l1_service import motivo_nao_cadastro

            motivo_geral = motivo_nao_cadastro(rel)
            for proc in pendentes:
                if proc.id in marcados_ids:
                    continue
                _marcar(
                    db, proc, motivo_geral,
                    dados={
                        "planilha_id": planilha_id,
                        "linhas_esperadas": rel.get("linhas_esperadas"),
                        "linhas_encontradas": rel.get("linhas_encontradas"),
                    },
                    run_id=run_id,
                )
                marcados_ids.add(proc.id)

        if marcados_ids:
            db.commit()
            logger.warning(
                "Import L1: %s processo(s) NÃO cadastrados, motivo registrado em cada um.",
                len(marcados_ids),
            )
        if sem_processo:
            # Linha recusada que não casou com processo nenhum: pode ser lixo de
            # import antigo no staging do L1. Fica no log, não some.
            logger.info(
                "Import L1: %s linha(s) recusada(s) sem processo correspondente "
                "na fila (provável resíduo de import anterior).", sem_processo,
            )
        return len(marcados_ids)
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao registrar os motivos de descarte (ignorado).")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0


def motivar_pendentes_sem_motivo(
    db,
    agora: Optional[datetime] = None,
    *,
    apos_min: int = _SEM_MOTIVO_APOS_MIN,
    limite: int = 500,
) -> int:
    """Rede de segurança: processo "Pendente cadastro" sem motivo não existe.

    Qualquer caminho que um dia deixar de gravar o motivo (import, retry, envio
    manual, versão futura) cai aqui: passado `apos_min` do envio — ou da geração,
    se a planilha nunca subiu — o processo que continua pendente e mudo recebe o
    que se sabe de fato. Tombamento fica de fora (fila própria, com cota).
    Best-effort. Devolve quantos processos receberam o motivo.
    """
    try:
        from app.models.distribuidos_bb import (
            POOL_PENDENTE_CADASTRO,
            BbPlanilha,
            BbProcesso,
        )

        agora = agora or datetime.now(timezone.utc)
        corte = agora - timedelta(minutes=apos_min)
        # `->` cru: igual em Postgres e no SQLite da suíte (ver o monitor).
        marca_tombamento = BbProcesso.raw.op("->")("tombamento")
        linhas = (
            db.query(BbProcesso, BbPlanilha)
            .join(BbPlanilha, BbPlanilha.id == BbProcesso.planilha_id)
            .filter(
                BbProcesso.planilha_status == POOL_PENDENTE_CADASTRO,
                BbProcesso.erro.is_(None),
                marca_tombamento.is_(None),
                BbPlanilha.created_at < corte,
            )
            .order_by(BbProcesso.id)
            .limit(limite)
            .all()
        )
        marcados = 0
        for proc, pl in linhas:
            enviada_em = _utc(pl.subido_em)
            if pl.subido_legalone:
                if enviada_em is not None and enviada_em > corte:
                    continue  # enviada há pouco: o monitor ainda está confirmando
                motivo = (
                    f"A planilha {pl.nome_arquivo} foi enviada ao Legal One em "
                    f"{_brt(enviada_em or _utc(pl.created_at))} e a pasta deste processo não "
                    f"apareceu lá depois de {apos_min} min: o L1 não criou a pasta."
                )
            else:
                motivo = (
                    f"A planilha {pl.nome_arquivo} foi gerada em {_brt(_utc(pl.created_at))} "
                    "e o envio ao Legal One ainda não deu certo (segue nas retentativas "
                    "automáticas)."
                )
            _marcar(db, proc, motivo, dados={"planilha_id": pl.id}, run_id=None)
            marcados += 1
        if marcados:
            db.commit()
            logger.warning(
                "Monitor cadastro: %s processo(s) pendentes sem pasta receberam o motivo.",
                marcados,
            )
        return marcados
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao registrar o motivo dos pendentes sem pasta (ignorado).")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0
