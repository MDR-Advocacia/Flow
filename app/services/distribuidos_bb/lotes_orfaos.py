"""Lote de upload (Ativos / Banco Master) cuja execução morreu no meio.

A ingestão do lote roda numa thread do worker do uvicorn que atendeu o upload
(`ingerir_lote_background`). Se o worker morre, a thread morre junto e o
`finally` que fecharia o lote nunca roda: ele fica EM_ANDAMENTO pra sempre, e a
tela de importação consulta o progresso a cada 1,5 s sem nunca terminar.

Caso real (15/09/2026): lote 40 do Master, 23 de 23 processados, planilha 232
gerada e o import no Legal One no meio da recaptura do token quando o host
ficou sem memória e o supervisor do uvicorn matou o worker às 08:49. O cadastro
se recuperou sozinho (retry do monitor às 08:58, pasta confirmada às 09:00),
mas o lote seguiu "em andamento".

Sinal de vida: a thread segura uma trava de arquivo do lote enquanto roda
(`lote_em_execucao`). O sistema operacional solta a trava quando o processo
morre, então trava livre + lote EM_ANDAMENTO = ninguém está conduzindo o lote.
É o mesmo mecanismo da eleição do worker líder em main.py. Heartbeat no banco
não serviria: a etapa do import fica minutos sem mexer em contador nenhum.

O que acontece com o órfão:
- processo do lote que ficou no pool sem planilha segue pro cadastro, pelo
  mesmo `_cadastrar_lote` que a thread chamaria;
- tudo processado: CONCLUIDO, sem mensagem (o que faltar do cadastro, o retry
  de planilha do monitor resolve);
- linha que nem chegou a ser lida: ERRO curto pedindo o arquivo de novo. O
  conteúdo do upload não fica guardado, então isso o sistema não refaz sozinho.
"""
from __future__ import annotations

import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

from filelock import FileLock, Timeout
from sqlalchemy.orm import Session

from app.models.distribuidos_bb import (
    CLIENTE_ATIVOS,
    CLIENTE_MASTER,
    LOTE_CONCLUIDO,
    LOTE_EM_ANDAMENTO,
    LOTE_ERRO,
    POOL_NOVO,
    PROC_DISTRIBUIDO,
    BbAtivosLote,
    BbProcesso,
)

logger = logging.getLogger("distribuidos_bb.lotes_orfaos")

# /tmp é um só pra todos os workers do container.
_DIR_TRAVAS = tempfile.gettempdir()
# A thread pega a trava milissegundos depois de o lote nascer; a graça só
# existe pra nunca olhar um lote antes disso.
_GRACA_MIN_PADRAO = 5
# Quanto a thread espera pela trava ao começar. Só estaria ocupada se o reaper
# estivesse sondando aquele lote no mesmo instante.
_ESPERA_TRAVA_S = 30

# Marca que a ingestão de cada carteira grava em `BbProcesso.raw`. Separa o
# processo do lote de outro do mesmo cliente que esteja no pool por outro
# caminho (pasta avulsa grava {"origem": "pasta_avulsa"}).
_MARCA_DO_LOTE = {
    CLIENTE_MASTER: "master_listagem",
    CLIENTE_ATIVOS: "ativos_planilha",
}


def _caminho_trava(lote_id: int) -> str:
    return os.path.join(_DIR_TRAVAS, f"flow-bbd-lote-{lote_id}.lock")


def _graca_min() -> int:
    try:
        return max(1, int(os.environ.get("BBD_LOTE_ORFAO_GRACA_MIN", _GRACA_MIN_PADRAO)))
    except ValueError:
        return _GRACA_MIN_PADRAO


@contextmanager
def lote_em_execucao(lote_id: int) -> Iterator[None]:
    """Segura a trava do lote enquanto o bloco roda (sinal de vida pro reaper).

    Nunca impede a ingestão: sem trava, só loga e segue.
    """
    trava: Optional[FileLock] = None
    try:
        candidata = FileLock(_caminho_trava(lote_id))
        candidata.acquire(timeout=_ESPERA_TRAVA_S)
        trava = candidata
    except Exception:  # noqa: BLE001
        logger.warning(
            "Lote %s: não consegui a trava de execução; segue sem ela.", lote_id,
            exc_info=True,
        )
    try:
        yield
    finally:
        if trava is not None:
            try:
                trava.release()
            except Exception:  # noqa: BLE001
                logger.warning("Lote %s: falha ao soltar a trava de execução.", lote_id)


@contextmanager
def assumir_lote_orfao(lote_id: int) -> Iterator[bool]:
    """Pega a trava do lote se ninguém estiver com ela. Cede True se pegou.

    Enquanto o bloco roda, o lote fica "vivo" pra qualquer outra sondagem.
    Erro inesperado cede False: na dúvida, não mexe no lote.
    """
    trava = FileLock(_caminho_trava(lote_id))
    try:
        trava.acquire(timeout=0)
    except Timeout:
        yield False
        return
    except Exception:  # noqa: BLE001
        logger.warning("Lote %s: não consegui sondar a trava de execução.", lote_id, exc_info=True)
        yield False
        return
    try:
        yield True
    finally:
        try:
            trava.release()
        except Exception:  # noqa: BLE001
            pass


def lote_esta_vivo(lote_id: int) -> bool:
    with assumir_lote_orfao(lote_id) as livre:
        return not livre


def _pool_do_lote(db: Session, lote: BbAtivosLote) -> list[int]:
    """Processos criados por ESTE lote que continuam no pool sem planilha."""
    marca = _MARCA_DO_LOTE.get(lote.cliente)
    if not marca:
        return []
    # O lote seguinte do mesmo cliente é dono do que nasceu depois dele.
    proximo = (
        db.query(BbAtivosLote.iniciado_em)
        .filter(BbAtivosLote.cliente == lote.cliente, BbAtivosLote.id > lote.id)
        .order_by(BbAtivosLote.id)
        .first()
    )
    q = db.query(BbProcesso).filter(
        BbProcesso.cliente == lote.cliente,
        BbProcesso.planilha_status == POOL_NOVO,
        BbProcesso.status == PROC_DISTRIBUIDO,
        BbProcesso.created_at >= lote.iniciado_em,
    )
    if proximo is not None:
        q = q.filter(BbProcesso.created_at < proximo[0])
    return [
        p.id for p in q.order_by(BbProcesso.id).all()
        if isinstance(p.raw, dict) and marca in p.raw
    ]


def _cadastrar_pool(db: Session, lote: BbAtivosLote, processo_ids: list[int]) -> None:
    if lote.cliente == CLIENTE_MASTER:
        from app.services.distribuidos_bb import master_service as carteira
    else:
        from app.services.distribuidos_bb import ativos_service as carteira
    carteira._cadastrar_lote(db, lote.id, processo_ids)


def reapear_lotes_orfaos(
    db: Session, *, graca_min: Optional[int] = None, agora: Optional[datetime] = None,
) -> dict:
    """Retoma o cadastro e fecha os lotes EM_ANDAMENTO que ninguém conduz."""
    graca = _graca_min() if graca_min is None else graca_min
    agora = agora or datetime.now(timezone.utc)
    candidatos = [
        row[0]
        for row in db.query(BbAtivosLote.id)
        .filter(
            BbAtivosLote.status == LOTE_EM_ANDAMENTO,
            BbAtivosLote.iniciado_em < agora - timedelta(minutes=graca),
        )
        .order_by(BbAtivosLote.id)
        .all()
    ]
    fechados: list[int] = []
    retomados: dict[int, list[int]] = {}
    for lote_id in candidatos:
        with assumir_lote_orfao(lote_id) as assumido:
            if not assumido:
                continue  # a thread do lote está viva em algum worker
            lote = db.get(BbAtivosLote, lote_id)
            if lote is None or lote.status != LOTE_EM_ANDAMENTO:
                continue  # a thread fechou o lote entre a consulta e a trava

            pool = _pool_do_lote(db, lote)
            if pool:
                retomados[lote_id] = pool
                logger.warning(
                    "Lote %s (%s) sem execução viva: retomando o cadastro de %d processo(s) "
                    "que ficaram no pool.", lote_id, lote.cliente, len(pool),
                )
                try:
                    _cadastrar_pool(db, lote, pool)
                except Exception:  # noqa: BLE001
                    # Planilha gerada e import que estourou: o retry do monitor
                    # assume, como faria com a thread original.
                    db.rollback()
                    logger.warning(
                        "Lote %s: retomada do cadastro falhou; o retry de planilha assume.",
                        lote_id, exc_info=True,
                    )

            lote = db.get(BbAtivosLote, lote_id)
            if lote is None or lote.status != LOTE_EM_ANDAMENTO:
                continue
            lote.concluido_em = datetime.now(timezone.utc)
            if (lote.processados or 0) >= (lote.total or 0):
                lote.status = LOTE_CONCLUIDO
            else:
                lote.status = LOTE_ERRO
                lote.erro = (
                    f"A importação parou em {lote.processados} de {lote.total} linha(s) e "
                    "não retoma sozinha, porque o arquivo não fica guardado. Suba o arquivo "
                    "de novo: o que já entrou é reconhecido e não duplica."
                )
            db.commit()
            fechados.append(lote_id)
            logger.warning(
                "Lote %s (%s) sem execução viva fechado como %s (%s de %s processados).",
                lote_id, lote.cliente, lote.status, lote.processados, lote.total,
            )
    return {"fechados": fechados, "retomados": retomados}
