"""Vigia de Filas: UM lugar que pergunta, pra TODA fila do sistema, se o
resultado bate com o que o status promete — e avisa por e-mail quando não.

POR QUE EXISTE (08/09/2026)
---------------------------
Num só dia: o Tratamento Web parado havia 3 dias com o runner pendurado; um
lote de classificação pronto na Anthropic e nunca aplicado; uma planilha de
2.756 agendamentos "CONCLUÍDA" com 0 processados; 5 de 6 coletas do BB
travadas; 15 processos com ciência dada no portal e pasta nenhuma no L1.
Nenhum deles mandou e-mail. Cada fila tinha o seu reaper — DEZ reapers em 25
tabelas de ciclo de vida — e nenhum olhava pra fora nem conferia RESULTADO:
todos só viravam status.

A classe por trás disso tem três caras, e o vigia mira as três:

1. **Travar é invisível.** Todo tratamento de falha é `except`; travamento não
   levanta exceção. Aqui a pergunta não é "deu erro?", é "há quanto tempo
   isso está no mesmo lugar?".
2. **"Sem erro" vira "sucesso".** Status registra o que o código pretendia,
   não o que aconteceu. Aqui sucesso é conferido pela CONTAGEM: entrada > 0 e
   processado = 0 é mentira, venha de onde vier.
3. **Cada fila com seu salva-vidas, nenhum olhando pra fora.** Aqui é um
   lugar, um vocabulário, um e-mail.

O QUE ELE NÃO FAZ
-----------------
Não conserta nada. Não mata processo (isso é do rpa_pid_watchdog e dos
reapers de cada fila), não devolve item pra fila, não reaplica lote. Vigia
que age vira mais um reaper — e a experiência de hoje é que reaper que age
sem conferir resultado é parte do problema. Ele OLHA e AVISA. Best-effort do
começo ao fim: um invariante que quebra não impede os outros, e falha no
e-mail não derruba o ciclo.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# Repete o MESMO alerta (mesma chave) só depois disto. O ciclo roda de 10 em
# 10 min; sem isto o operador receberia 6 e-mails por hora sobre a mesma fila
# parada — e alarme que repete treina a ignorar alarme.
_REPETE_APOS_S = int(os.environ.get("VIGIA_FILAS_REPETE_APOS_S", str(6 * 3600)))
# Janela de interesse pra "sucesso vazio": o que é acionável agora. Lotes
# mortos de maio continuam no banco pra auditoria, mas não no e-mail de hoje.
_JANELA_DIAS = int(os.environ.get("VIGIA_FILAS_JANELA_DIAS", "7"))

GRAVE = "ERRO"
AVISO = "AVISO"


@dataclass
class Violacao:
    fila: str
    invariante: str
    chave: str            # estável entre ciclos: é o que controla a repetição
    gravidade: str
    mensagem: str
    dados: dict[str, Any] = field(default_factory=dict)

    def linha_email(self) -> dict[str, Any]:
        return {
            "cnj": f"[{self.gravidade}] {self.fila} — {self.invariante}",
            "motivo": self.mensagem[:1500],
            "execution_id": self.dados.get("id"),
        }


# ── utilitários ────────────────────────────────────────────────────────


def _utc(ts: Optional[datetime]) -> Optional[datetime]:
    """Normaliza pra UTC-aware. SQLite devolve naive; Postgres, aware. Sem isto
    a conta de idade levanta TypeError e derruba o ciclo inteiro."""
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _ha(agora: datetime, ts: Optional[datetime]) -> str:
    ts = _utc(ts)
    if ts is None:
        return "tempo desconhecido"
    seg = max(int((agora - ts).total_seconds()), 0)
    if seg < 3600:
        return f"{seg // 60} min"
    if seg < 48 * 3600:
        return f"{seg // 3600} h"
    return f"{seg // 86400} dia(s)"


# ── invariantes ────────────────────────────────────────────────────────
# Cada função devolve a lista de violações que enxerga. Todas recebem
# (db, agora) e nada mais; todas são chamadas dentro de try/except.


def _sucesso_vazio_lotes(db, agora: datetime) -> list[Violacao]:
    """Lote CONCLUÍDO que declara linhas e não processou nenhuma (caso 6019)."""
    from sqlalchemy import func

    from app.models.batch_execution import (
        BATCH_STATUS_COMPLETED,
        BatchExecution,
        BatchExecutionItem,
    )

    corte = agora - timedelta(days=_JANELA_DIAS)
    lotes = (
        db.query(BatchExecution)
        .filter(
            BatchExecution.status == BATCH_STATUS_COMPLETED,
            BatchExecution.total_items > 0,
            func.coalesce(BatchExecution.success_count, 0) == 0,
            func.coalesce(BatchExecution.failure_count, 0) == 0,
            BatchExecution.start_time >= corte,
        )
        .all()
    )
    out = []
    for lote in lotes:
        pendentes = (
            db.query(BatchExecutionItem)
            .filter(BatchExecutionItem.execution_id == lote.id,
                    BatchExecutionItem.status == "PENDENTE")
            .count()
        )
        if pendentes == 0:
            continue
        out.append(Violacao(
            fila="Agendamento em lote", invariante="sucesso vazio",
            chave=f"lote:sucesso_vazio:{lote.id}", gravidade=GRAVE,
            mensagem=(
                f"Lote #{lote.id} ({lote.source_filename or lote.source}) está CONCLUÍDO "
                f"declarando {lote.total_items} linha(s), mas nenhuma foi processada e "
                f"{pendentes} continuam PENDENTE. O visto verde é falso: ninguém agendou nada."
            ),
            dados={"id": lote.id, "total": lote.total_items, "pendentes": pendentes},
        ))
    return out


def _sucesso_vazio_tratamento(db, agora: datetime) -> list[Violacao]:
    from app.models.publication_treatment import (
        RUN_STATUS_COMPLETED,
        RUN_STATUS_COMPLETED_WITH_ERRORS,
        PublicationTreatmentRun,
    )

    corte = agora - timedelta(days=_JANELA_DIAS)
    runs = (
        db.query(PublicationTreatmentRun)
        .filter(
            PublicationTreatmentRun.status.in_(
                (RUN_STATUS_COMPLETED, RUN_STATUS_COMPLETED_WITH_ERRORS)
            ),
            PublicationTreatmentRun.total_items > 0,
            PublicationTreatmentRun.processed_items == 0,
            PublicationTreatmentRun.started_at >= corte,
        )
        .all()
    )
    return [
        Violacao(
            fila="Tratamento Web", invariante="sucesso vazio",
            chave=f"tratamento:sucesso_vazio:{r.id}", gravidade=GRAVE,
            mensagem=(
                f"Execução #{r.id} terminou como {r.status} com {r.total_items} item(ns) "
                "e 0 processados. Terminou sem fazer nada."
            ),
            dados={"id": r.id, "total": r.total_items},
        )
        for r in runs
    ]


def _sem_sinal_de_vida(db, agora: datetime) -> list[Violacao]:
    """Run 'ativa' há mais tempo do que qualquer trabalho legítimo leva.

    Os tetos aqui são FOLGADOS de propósito e ficam ACIMA dos reapers de cada
    fila: se o vigia enxerga, é porque o reaper daquela fila também falhou.
    """
    out: list[Violacao] = []

    from app.models.distribuidos_bb import RUN_EM_ANDAMENTO, BbRun

    for r in db.query(BbRun).filter(
        BbRun.status == RUN_EM_ANDAMENTO,
        BbRun.iniciado_em < agora - timedelta(minutes=90),
    ).all():
        out.append(Violacao(
            fila="Coleta BB", invariante="sem sinal de vida",
            chave=f"coleta:viva:{r.id}", gravidade=GRAVE,
            mensagem=(
                f"Coleta #{r.id} está EM_ANDAMENTO há {_ha(agora, r.iniciado_em)} — uma "
                "passagem honesta leva ~6 min. O reaper dela (60 min) também não a fechou."
            ),
            dados={"id": r.id},
        ))

    from app.models.publication_treatment import ACTIVE_RUN_STATUSES, PublicationTreatmentRun

    for r in db.query(PublicationTreatmentRun).filter(
        PublicationTreatmentRun.status.in_(tuple(ACTIVE_RUN_STATUSES)),
        PublicationTreatmentRun.started_at < agora - timedelta(hours=6),
    ).all():
        out.append(Violacao(
            fila="Tratamento Web", invariante="sem sinal de vida",
            chave=f"tratamento:viva:{r.id}", gravidade=GRAVE,
            mensagem=(
                f"Execução #{r.id} está {r.status} há {_ha(agora, r.started_at)} "
                f"({r.processed_items}/{r.total_items}). Rodada inteira leva ~30 min."
            ),
            dados={"id": r.id},
        ))

    from app.models.scheduled_automation import ScheduledAutomationRun

    for r in db.query(ScheduledAutomationRun).filter(
        ScheduledAutomationRun.status == "running",
        ScheduledAutomationRun.started_at < agora - timedelta(hours=6),
    ).all():
        out.append(Violacao(
            fila="Automação noturna", invariante="sem sinal de vida",
            chave=f"automacao:viva:{r.id}", gravidade=GRAVE,
            mensagem=(
                f"Automação #{r.id} está 'running' há {_ha(agora, r.started_at)}; último "
                f"progresso há {_ha(agora, r.progress_updated_at)} ({r.progress_phase or '?'})."
            ),
            dados={"id": r.id},
        ))

    from app.models.publication_sem_pasta import PublicacaoSemPastaRun

    for r in db.query(PublicacaoSemPastaRun).filter(
        PublicacaoSemPastaRun.status == "running",
        PublicacaoSemPastaRun.started_at < agora - timedelta(hours=3),
    ).all():
        out.append(Violacao(
            fila="Fila sem pasta", invariante="sem sinal de vida",
            chave=f"sem_pasta:viva:{r.id}", gravidade=GRAVE,
            mensagem=f"Rodada #{r.id} do motor sem pasta está 'running' há {_ha(agora, r.started_at)}.",
            dados={"id": r.id},
        ))

    from app.models.batch_execution import BATCH_STATUS_PROCESSING, BatchExecution

    for r in db.query(BatchExecution).filter(
        BatchExecution.status == BATCH_STATUS_PROCESSING,
        BatchExecution.lease_expires_at.isnot(None),
        BatchExecution.lease_expires_at < agora - timedelta(minutes=60),
    ).all():
        out.append(Violacao(
            fila="Agendamento em lote", invariante="sem sinal de vida",
            chave=f"lote:viva:{r.id}", gravidade=AVISO,
            mensagem=(
                f"Lote #{r.id} está PROCESSANDO com a garra vencida há "
                f"{_ha(agora, r.lease_expires_at)} e nenhum worker o retomou."
            ),
            dados={"id": r.id},
        ))

    from app.models.publication_search import SEARCH_STATUS_RUNNING, PublicationSearch

    for r in db.query(PublicationSearch).filter(
        PublicationSearch.status == SEARCH_STATUS_RUNNING,
        PublicationSearch.created_at < agora - timedelta(hours=2),
    ).all():
        out.append(Violacao(
            fila="Busca de publicações", invariante="sem sinal de vida",
            chave=f"busca:viva:{r.id}", gravidade=AVISO,
            mensagem=(
                f"Busca #{r.id} está EXECUTANDO há {_ha(agora, r.created_at)}; o watchdog "
                "dela fecha órfã em 30 min e não fechou."
            ),
            dados={"id": r.id},
        ))
    return out


def _fila_parada(db, agora: datetime) -> list[Violacao]:
    """Itens pendentes envelhecendo sem ninguém os reivindicar."""
    out: list[Violacao] = []
    from sqlalchemy import func

    from app.models.publication_treatment import (
        ACTIVE_RUN_STATUSES,
        QUEUE_STATUS_PENDING,
        PublicationTreatmentItem,
        PublicationTreatmentRun,
    )

    pend = (
        db.query(func.count(PublicationTreatmentItem.id), func.min(PublicationTreatmentItem.created_at))
        .filter(PublicationTreatmentItem.queue_status == QUEUE_STATUS_PENDING)
        .one()
    )
    n_pend, mais_antigo = pend[0] or 0, pend[1]
    ativa = (
        db.query(PublicationTreatmentRun.id)
        .filter(PublicationTreatmentRun.status.in_(tuple(ACTIVE_RUN_STATUSES)))
        .first()
    )
    if n_pend and ativa is None and _utc(mais_antigo) and _utc(mais_antigo) < agora - timedelta(hours=36):
        out.append(Violacao(
            fila="Tratamento Web", invariante="fila parada",
            chave="tratamento:parada", gravidade=AVISO,
            mensagem=(
                f"{n_pend} publicação(ões) PENDENTE no Tratamento Web, a mais antiga há "
                f"{_ha(agora, mais_antigo)}, e nenhuma execução ativa. O autorun roda 4x/dia; "
                "36 h sem andar é fila parada."
            ),
            dados={"pendentes": n_pend},
        ))

    from app.models.batch_execution import FINAL_BATCH_STATUSES, BatchExecution, BatchExecutionItem

    orfas = (
        db.query(BatchExecutionItem.execution_id, func.count(BatchExecutionItem.id))
        .join(BatchExecution, BatchExecution.id == BatchExecutionItem.execution_id)
        .filter(
            BatchExecutionItem.status == "PENDENTE",
            BatchExecution.status.in_(tuple(FINAL_BATCH_STATUSES)),
            BatchExecution.start_time >= agora - timedelta(days=_JANELA_DIAS),
        )
        .group_by(BatchExecutionItem.execution_id)
        .all()
    )
    for exec_id, n in orfas:
        out.append(Violacao(
            fila="Agendamento em lote", invariante="itens órfãos",
            chave=f"lote:orfaos:{exec_id}", gravidade=GRAVE,
            mensagem=(
                f"Lote #{exec_id} já está em status final e ainda tem {n} linha(s) PENDENTE. "
                "Ninguém vai processá-las: lote fechado não volta pra fila."
            ),
            dados={"id": exec_id, "pendentes": n},
        ))

    from app.models.publication_search import RECORD_STATUS_NEW, PublicationRecord

    novos = (
        db.query(func.count(PublicationRecord.id), func.min(PublicationRecord.created_at))
        .filter(
            PublicationRecord.status == RECORD_STATUS_NEW,
            PublicationRecord.is_duplicate == False,  # noqa: E712
            PublicationRecord.created_at < agora - timedelta(hours=30),
        )
        .one()
    )
    if novos[0]:
        out.append(Violacao(
            fila="Classificação de publicações", invariante="fila parada",
            chave="classificacao:parada", gravidade=AVISO,
            mensagem=(
                f"{novos[0]} publicação(ões) NOVA(S) há mais de 30 h sem classificar (a mais "
                f"antiga há {_ha(agora, novos[1])}). A classificação noturna não passou por elas."
            ),
            dados={"pendentes": novos[0]},
        ))
    return out


def _refem_ciencia_sem_cadastro(db, agora: datetime) -> list[Violacao]:
    """Ciência dada no portal do BB (irreversível) e nenhuma planilha pro L1.

    É o único invariante com efeito EXTERNO já consumado: o BB não mostra mais
    a notificação. Se a pasta não nascer, o processo simplesmente some da vida
    de todo mundo. Por isso é ERRO cedo (2 h), não aviso tarde.
    """
    from sqlalchemy import func

    from app.models.distribuidos_bb import POOL_NOVO, BbProcesso

    q = (
        db.query(func.count(BbProcesso.id), func.min(BbProcesso.ciencia_dada_em))
        .filter(
            BbProcesso.ciencia_dada_em.isnot(None),
            BbProcesso.planilha_status == POOL_NOVO,
            BbProcesso.ciencia_dada_em < agora - timedelta(hours=2),
        )
        .one()
    )
    if not q[0]:
        return []
    return [Violacao(
        fila="Cadastro BB", invariante="refém",
        chave="cadastro_bb:refem", gravidade=GRAVE,
        mensagem=(
            f"{q[0]} processo(s) com ciência dada no portal do BB há mais de 2 h (o mais "
            f"antigo há {_ha(agora, q[1])}) e SEM planilha pro L1. O BB não os mostra mais; "
            "se a pasta não nascer, eles desaparecem."
        ),
        dados={"refens": q[0]},
    )]


def _lote_externo_nao_aplicado(db, agora: datetime) -> list[Violacao]:
    """Lote de classificação parado no provedor (caso 164: pronto na Anthropic
    às 09:20, aplicado à mão às 09:35 — só porque alguém olhou)."""
    from app.models.publication_batch import (
        PUB_BATCH_STATUS_IN_PROGRESS,
        PUB_BATCH_STATUS_READY,
        PUB_BATCH_STATUS_SUBMITTED,
        PUB_BATCH_STATUS_WARMING,
        PublicationBatchClassification,
    )

    lotes = (
        db.query(PublicationBatchClassification)
        .filter(
            PublicationBatchClassification.status.in_((
                PUB_BATCH_STATUS_WARMING, PUB_BATCH_STATUS_SUBMITTED,
                PUB_BATCH_STATUS_IN_PROGRESS, PUB_BATCH_STATUS_READY,
            )),
            PublicationBatchClassification.created_at < agora - timedelta(hours=3),
        )
        .all()
    )
    return [
        Violacao(
            fila="Classificação de publicações", invariante="lote externo não aplicado",
            chave=f"batch:nao_aplicado:{b.id}", gravidade=AVISO,
            mensagem=(
                f"Lote #{b.id} ({b.total_records} registros) está {b.status} há "
                f"{_ha(agora, b.created_at)}. Lotes da Anthropic fecham em minutos; o resgate "
                "automático só roda na próxima rodada noturna."
            ),
            dados={"id": b.id},
        )
        for b in lotes
    ]


def _fila_intratavel(db, agora: datetime) -> list[Violacao]:
    """Item na fila do Tratamento Web que NUNCA vai dar certo: id sintético.

    Publicação do fallback (DJEN/planilha) tem `legal_one_update_id` negativo
    porque não existe no L1 — e o Tratamento Web marca coisas NO L1. Em
    09/09/2026 havia 2.399 desses na fila desde 30/07 e 27/08, 0 tratados em
    toda a história; cada run gastava ~2.400 requisições no L1 e ~250 MB de
    screenshot de falha pra descobrir isso de novo, quatro vezes por dia. O
    `sync_item_from_record` passou a cancelar na origem; este invariante é a
    rede: se voltar a entrar por outra porta, aparece aqui.
    """
    from sqlalchemy import func

    from app.models.publication_treatment import (
        QUEUE_STATUS_FAILED,
        QUEUE_STATUS_PENDING,
        PublicationTreatmentItem,
    )

    q = (
        db.query(func.count(PublicationTreatmentItem.id), func.min(PublicationTreatmentItem.created_at))
        .filter(
            PublicationTreatmentItem.queue_status.in_((QUEUE_STATUS_PENDING, QUEUE_STATUS_FAILED)),
            PublicationTreatmentItem.legal_one_update_id < 0,
        )
        .one()
    )
    if not q[0]:
        return []
    return [Violacao(
        fila="Tratamento Web", invariante="fila intratável",
        chave="tratamento:intratavel", gravidade=AVISO,
        mensagem=(
            f"{q[0]} item(ns) na fila do Tratamento Web com id sintético (negativo) — "
            "publicação que não existe no L1 e nunca poderá ser tratada lá (o mais antigo "
            f"há {_ha(agora, q[1])}). Cada run gasta requisições e disco falhando neles."
        ),
        dados={"intrataveis": q[0]},
    )]


INVARIANTES: tuple[Callable[[Any, datetime], list[Violacao]], ...] = (
    _sucesso_vazio_lotes,
    _sucesso_vazio_tratamento,
    _sem_sinal_de_vida,
    _fila_parada,
    _refem_ciencia_sem_cadastro,
    _lote_externo_nao_aplicado,
    _fila_intratavel,
)


# ── ciclo ──────────────────────────────────────────────────────────────

_avisadas: dict[str, float] = {}


def inspecionar(db, agora: Optional[datetime] = None) -> list[Violacao]:
    """Roda todos os invariantes. Um que quebra não cala os outros."""
    agora = agora or datetime.now(timezone.utc)
    achados: list[Violacao] = []
    for inv in INVARIANTES:
        try:
            achados.extend(inv(db, agora))
        except Exception:  # noqa: BLE001
            logger.exception("Vigia de filas: invariante %s quebrou (ignorado).", inv.__name__)
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
    achados.sort(key=lambda v: (v.gravidade != GRAVE, v.fila, v.chave))
    return achados


def _novas(achados: list[Violacao], agora_mono: float) -> list[Violacao]:
    return [
        v for v in achados
        if (agora_mono - _avisadas.get(v.chave, -1e18)) >= _REPETE_APOS_S
    ]


def _avisar(achados: list[Violacao], novas: list[Violacao]) -> bool:
    """Um e-mail com TODAS as violações atuais, disparado só quando há novidade."""
    try:
        from app.services.mail_service import send_failure_report

        destinatarios = (
            getattr(settings, "publication_alert_email", None)
            or getattr(settings, "distribuidos_bb_alert_email", None)
        )
        if not destinatarios:
            logger.error("Vigia de filas: %d violação(ões) e nenhum destinatário configurado.", len(achados))
            return False
        graves = sum(1 for v in achados if v.gravidade == GRAVE)
        ok = send_failure_report(
            failed_items=[v.linha_email() for v in achados],
            batch_source=(
                f"Vigia de Filas — {len(achados)} problema(s) em aberto "
                f"({graves} grave(s), {len(novas)} novo(s))"
            ),
            recipients=destinatarios,
            system_name="Flow",
        )
        return bool(ok)
    except Exception:  # noqa: BLE001
        logger.exception("Vigia de filas: falha ao enviar o alerta (ignorado).")
        return False


def rodar_ciclo(db=None, *, avisar: bool = True) -> dict[str, Any]:
    """Inspeciona, decide se avisa, devolve o resumo (testes e endpoint)."""
    from app.db.session import SessionLocal

    proprio = db is None
    db = db or SessionLocal()
    try:
        achados = inspecionar(db)
    finally:
        if proprio:
            db.close()

    agora_mono = time.monotonic()
    novas = _novas(achados, agora_mono)
    enviado = False
    if achados and novas and avisar:
        enviado = _avisar(achados, novas)
        if enviado:
            for v in novas:
                _avisadas[v.chave] = agora_mono
    # Chave que sumiu da lista volta a poder alertar na próxima vez que aparecer.
    vivas = {v.chave for v in achados}
    for chave in [c for c in _avisadas if c not in vivas]:
        _avisadas.pop(chave, None)

    if achados:
        logger.warning(
            "Vigia de filas: %d violação(ões) [%s]%s.",
            len(achados), ", ".join(sorted({v.fila for v in achados})),
            " — e-mail enviado" if enviado else "",
        )
    else:
        logger.info("Vigia de filas: todas as filas com resultado coerente.")
    return {
        "violacoes": [v.__dict__ for v in achados],
        "novas": len(novas),
        "email_enviado": enviado,
    }


def register_vigia_filas_job(scheduler) -> None:
    """Job periódico — registrado só no worker líder, como os demais."""
    if not getattr(settings, "vigia_filas_enabled", True):
        logger.info("Vigia de filas desabilitado por configuração.")
        return

    def _tick():
        try:
            rodar_ciclo()
        except Exception:  # noqa: BLE001
            logger.exception("Vigia de filas: ciclo falhou (ignorado).")

    minutos = int(os.environ.get("VIGIA_FILAS_INTERVALO_MIN", "10"))
    scheduler.add_job(
        _tick,
        trigger="interval",
        minutes=minutos,
        id="vigia_filas",
        name="Vigia de Filas (resultado x status)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Vigia de filas registrado (a cada %s min, %d invariantes).", minutos, len(INVARIANTES))
