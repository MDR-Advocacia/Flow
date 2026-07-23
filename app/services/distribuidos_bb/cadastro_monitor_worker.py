"""Monitor de cadastro no Legal One (APScheduler).

A partir do momento em que a planilha é gerada, cada processo fica
`PENDENTE_CADASTRO`. De 2 em 2 minutos este monitor bate na API do Legal One
procurando a pasta por **CNJ + escritório responsável**; quando acha, marca o
processo como `CADASTRADO_L1` (guarda o id e o folder da pasta) e loga.

Ciclo: NOVO → (operador gera planilha) PENDENTE_CADASTRO → (monitor acha) CADASTRADO_L1.

Roda em thread do BackgroundScheduler → abre a própria SessionLocal.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger("distribuidos_bb.monitor_cadastro")

JOB_ID = "distribuidos_bb_monitor_cadastro"
# Lock entre workers do uvicorn (só 1 checa o L1, senão 4× requests/corrida).
_LOCK_KEY = 826100007


def verificar_pendentes(db, *, client=None, limite: int = 300) -> dict:
    """Varre os PENDENTE_CADASTRO com CNJ e confirma no L1 os que já existem.

    Devolve {verificados, confirmados, sem_cnj_ignorados}.
    """
    from app.models.distribuidos_bb import (
        NIVEL_SUCESSO,
        POOL_CADASTRADO_L1,
        POOL_PENDENTE_CADASTRO,
        SECAO_CADASTRO,
        BbProcesso,
    )
    from app.services.distribuidos_bb import cadastro_l1
    from app.services.distribuidos_bb.log_service import registrar_evento

    pendentes = (
        db.query(BbProcesso)
        .filter(BbProcesso.planilha_status == POOL_PENDENTE_CADASTRO)
        .order_by(BbProcesso.l1_verificado_em.asc().nullsfirst())
        .limit(limite)
        .all()
    )
    if not pendentes:
        return {"verificados": 0, "confirmados": 0, "sem_cnj_ignorados": 0}

    if client is None:
        from app.services.legal_one_client import LegalOneApiClient

        client = LegalOneApiClient()

    office_cache: dict[str, int | None] = {}
    verificados = confirmados = sem_id = 0
    planilhas_afetadas: set[int] = set()
    agora = datetime.now(timezone.utc)

    for p in pendentes:
        # Sem NENHUM identificador (nem CNJ nem NPJ) não dá pra confirmar.
        if not p.cnj and not p.npj:
            sem_id += 1
            continue
        verificados += 1
        try:
            path = p.escritorio_path or ""
            if path not in office_cache:
                office_cache[path] = cadastro_l1.resolver_office_por_path(client, path)
            office_id = office_cache[path]

            pasta = None
            via = None
            # 1) Por CNJ + escritório (respeita BB vs Ativos no mesmo CNJ).
            if p.cnj:
                res = cadastro_l1.verificar_duplicado(client, p.cnj, office_id)
                achados = res.get("no_mesmo_escritorio") or []
                if achados:
                    pasta, via = achados[0], f"CNJ {p.cnj}"
            # 2) Por NPJ (o import grava o NPJ como TITLE) — resolve os SEM CNJ
            #    e reforça os demais. Prefere mesmo escritório, senão aceita o NPJ
            #    (que é único por processo BB).
            if pasta is None and p.npj:
                por_npj = cadastro_l1.buscar_lawsuit_por_npj(client, p.npj)
                if por_npj:
                    mesmo = [m for m in por_npj if m.get("office") == office_id]
                    pasta = (mesmo or por_npj)[0]
                    via = f"NPJ {p.npj}"

            p.l1_verificado_em = agora
            if pasta:
                p.planilha_status = POOL_CADASTRADO_L1
                p.l1_lawsuit_id = pasta.get("id")
                p.l1_folder = pasta.get("folder")
                p.cadastro_confirmado_em = agora
                confirmados += 1
                if p.planilha_id:
                    planilhas_afetadas.add(p.planilha_id)
                registrar_evento(
                    db, secao=SECAO_CADASTRO, nivel=NIVEL_SUCESSO,
                    acao="Cadastro confirmado no L1",
                    mensagem=(
                        f"Cadastro confirmado no Legal One: pasta {pasta.get('folder')} "
                        f"(id {pasta.get('id')}) — casado por {via}."
                    ),
                    dados={"lawsuit_id": pasta.get("id"), "folder": pasta.get("folder"), "via": via},
                    processo_id=p.id, run_id=p.run_id,
                )
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception(
                "Monitor cadastro: falha ao verificar processo %s (CNJ %s / NPJ %s).",
                p.id, p.cnj, p.npj,
            )

    # Rede de segurança: marca como SUBIDA a planilha cujos processos já estão
    # TODOS cadastrados no L1 (não deixa "pendente de envio" na tela, mesmo que o
    # cadastro tenha vindo por um caminho que não marcou na hora).
    if planilhas_afetadas:
        from app.models.distribuidos_bb import BbPlanilha

        for pid in planilhas_afetadas:
            restam = (
                db.query(BbProcesso)
                .filter(
                    BbProcesso.planilha_id == pid,
                    BbProcesso.planilha_status == POOL_PENDENTE_CADASTRO,
                )
                .count()
            )
            if restam == 0:
                pl = db.get(BbPlanilha, pid)
                if pl is not None and not pl.subido_legalone:
                    pl.subido_legalone = True
                    pl.subido_em = agora
        db.commit()

    logger.info(
        "Monitor cadastro L1: %s verificado(s), %s confirmado(s), %s sem identificador.",
        verificados, confirmados, sem_id,
    )
    return {"verificados": verificados, "confirmados": confirmados, "sem_cnj_ignorados": sem_id}


# Retry automático do auto-cadastro que falhou (planilha gerada mas nunca
# importada no L1 — ex.: 401 do gateway em 2026-07-23 deixou 5 processos órfãos).
_RETRY_COOLDOWN_MIN = 45
_RETRY_MAX = 5
_RETRY_MIN_IDADE_MIN = 10   # não re-tentar planilha recém-gerada (coleta em curso)
_RETRY_MAX_IDADE_H = 48     # velha demais = tratamento manual

_ACAO_RETRY = "Retry do auto-cadastro"
_ACAO_RETRY_ESGOTADO = "Retry do auto-cadastro esgotado"
_ACAO_RETRY_BLOQUEADO = "Retry do auto-cadastro bloqueado"


def retentar_planilhas_orfas(db) -> None:
    """Re-tenta o import das planilhas geradas-mas-nunca-subidas (auto-cadastro
    que falhou). Regras de segurança:

    - Só re-tenta quando NENHUM processo da planilha foi confirmado no L1
      (all-or-nothing): estado misto pode duplicar pasta SEM CNJ (a dedup do L1
      pra linha sem CNJ é por nome — falso positivo que o fluxo ignora de
      propósito) → nesse caso alerta e deixa manual.
    - Cooldown de 45 min entre tentativas; máximo 5 (depois alerta e para).
    """
    from datetime import timedelta

    from sqlalchemy import text as _text

    from app.models.distribuidos_bb import (
        NIVEL_AVISO,
        NIVEL_ERRO,
        NIVEL_SUCESSO,
        POOL_PENDENTE_CADASTRO,
        SECAO_CADASTRO,
        BbPlanilha,
        BbProcesso,
    )
    from app.services.distribuidos_bb.alertas import alertar_falha_cadastro
    from app.services.distribuidos_bb.log_service import registrar_evento

    agora = datetime.now(timezone.utc)
    candidatas = (
        db.query(BbPlanilha)
        .filter(
            BbPlanilha.subido_legalone.is_(False),
            BbPlanilha.created_at <= agora - timedelta(minutes=_RETRY_MIN_IDADE_MIN),
            BbPlanilha.created_at >= agora - timedelta(hours=_RETRY_MAX_IDADE_H),
        )
        .order_by(BbPlanilha.id)
        .limit(3)
        .all()
    )
    for pl in candidatas:
        total = db.query(BbProcesso).filter(BbProcesso.planilha_id == pl.id).count()
        if total == 0:
            continue  # planilha sem processos vinculados (ex.: geração manual avulsa)
        pendentes = (
            db.query(BbProcesso)
            .filter(
                BbProcesso.planilha_id == pl.id,
                BbProcesso.planilha_status == POOL_PENDENTE_CADASTRO,
            )
            .count()
        )
        # histórico de retries dessa planilha (eventos são a memória do retry)
        hist = db.execute(
            _text(
                "SELECT acao, max(created_at) AS ultima, count(*) AS qtd FROM bbd_eventos "
                "WHERE acao IN (:a1, :a2, :a3) AND dados->>'planilha_id' = :pid "
                "GROUP BY acao"
            ),
            {"a1": _ACAO_RETRY, "a2": _ACAO_RETRY_ESGOTADO, "a3": _ACAO_RETRY_BLOQUEADO,
             "pid": str(pl.id)},
        ).fetchall()
        por_acao = {r.acao: r for r in hist}
        if _ACAO_RETRY_ESGOTADO in por_acao or _ACAO_RETRY_BLOQUEADO in por_acao:
            continue  # já desistiu/alertou — não insistir nem re-spammar

        if pendentes < total:
            # Estado misto (parte já confirmada no L1) — retry cheio é inseguro.
            registrar_evento(
                db, secao=SECAO_CADASTRO, nivel=NIVEL_AVISO, acao=_ACAO_RETRY_BLOQUEADO,
                mensagem=(
                    f"Planilha '{pl.nome_arquivo}' está parcialmente cadastrada "
                    f"({total - pendentes}/{total} confirmados) — retry automático seria "
                    "inseguro (risco de duplicar pasta sem CNJ). Tratar manualmente."
                ),
                dados={"planilha_id": str(pl.id)},
            )
            db.commit()
            alertar_falha_cadastro(
                contexto="retry automático bloqueado (estado misto)",
                erro=(
                    f"{total - pendentes} de {total} processos da planilha já estão no L1; "
                    "os demais precisam de import manual seletivo."
                ),
                planilha_id=pl.id, planilha_nome=pl.nome_arquivo, total_processos=total,
            )
            continue

        retry_row = por_acao.get(_ACAO_RETRY)
        tentativas = int(retry_row.qtd) if retry_row else 0
        if retry_row is not None:
            ultima = retry_row.ultima
            if ultima is not None and ultima.tzinfo is None:
                ultima = ultima.replace(tzinfo=timezone.utc)
            if ultima is not None and (agora - ultima) < timedelta(minutes=_RETRY_COOLDOWN_MIN):
                continue  # cooldown
        if tentativas >= _RETRY_MAX:
            registrar_evento(
                db, secao=SECAO_CADASTRO, nivel=NIVEL_ERRO, acao=_ACAO_RETRY_ESGOTADO,
                mensagem=(
                    f"Auto-cadastro da planilha '{pl.nome_arquivo}' falhou {tentativas}x — "
                    "desistindo do retry automático. Importar manualmente."
                ),
                dados={"planilha_id": str(pl.id)},
            )
            db.commit()
            alertar_falha_cadastro(
                contexto=f"retry automático esgotado ({tentativas} tentativas)",
                erro="Todas as tentativas de import no L1 falharam. Importar manualmente.",
                planilha_id=pl.id, planilha_nome=pl.nome_arquivo, total_processos=total,
            )
            continue

        # Tenta de novo (evento ANTES, pra valer como cooldown mesmo se estourar).
        registrar_evento(
            db, secao=SECAO_CADASTRO, nivel=NIVEL_AVISO, acao=_ACAO_RETRY,
            mensagem=(
                f"Re-tentando o import da planilha '{pl.nome_arquivo}' no Legal One "
                f"(tentativa {tentativas + 1}/{_RETRY_MAX})…"
            ),
            dados={"planilha_id": str(pl.id)},
        )
        db.commit()
        try:
            from app.services.distribuidos_bb.import_l1_service import cadastrar_planilha

            rel = cadastrar_planilha(bytes(pl.conteudo), pl.nome_arquivo, dry_run=False)
            pl.subido_legalone = True
            pl.subido_em = datetime.now(timezone.utc)
            registrar_evento(
                db, secao=SECAO_CADASTRO, nivel=NIVEL_SUCESSO, acao="Retry do auto-cadastro OK",
                mensagem=(
                    f"Import da planilha '{pl.nome_arquivo}' enviado na retentativa: "
                    f"{rel.get('novos', 0)} pasta(s) nova(s). O monitor confirma nos próximos ciclos."
                ),
                dados={"planilha_id": str(pl.id), "novos": rel.get("novos", 0)},
            )
            db.commit()
            logger.info("Retry do auto-cadastro OK (planilha %s).", pl.id)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            registrar_evento(
                db, secao=SECAO_CADASTRO, nivel=NIVEL_ERRO, acao="Falha no retry do auto-cadastro",
                mensagem=f"Retentativa {tentativas + 1}/{_RETRY_MAX} falhou: {exc}",
                dados={"planilha_id": str(pl.id)},
            )
            db.commit()
            logger.exception("Retry do auto-cadastro falhou (planilha %s).", pl.id)
            if tentativas + 1 >= _RETRY_MAX:
                alertar_falha_cadastro(
                    contexto=f"retry automático ({tentativas + 1}ª e última tentativa)",
                    erro=str(exc),
                    planilha_id=pl.id, planilha_nome=pl.nome_arquivo, total_processos=total,
                )


def _tick() -> None:
    from app.db.session import SessionLocal
    from app.services.onerequest._concurrency import single_worker_lock

    # Só UM worker do uvicorn bate no L1 (evita 4× requests e corrida na marcação).
    with single_worker_lock(_LOCK_KEY) as got:
        if not got:
            return
        db = SessionLocal()
        try:
            verificar_pendentes(db)
        except Exception:  # noqa: BLE001
            logger.exception("Monitor cadastro L1: erro inesperado no tick.")
        finally:
            db.close()
        db = SessionLocal()
        try:
            retentar_planilhas_orfas(db)
        except Exception:  # noqa: BLE001
            logger.exception("Monitor cadastro L1: erro no retry de planilhas órfãs.")
        finally:
            db.close()


def register_distribuidos_bb_monitor_cadastro_job(scheduler) -> None:
    """Registra o monitor recorrente (default 2 min)."""
    from apscheduler.triggers.interval import IntervalTrigger

    from app.core.config import settings

    if not settings.distribuidos_bb_monitor_cadastro_ativo:
        logger.info("Monitor cadastro L1: desligado (distribuidos_bb_monitor_cadastro_ativo=False).")
        return

    minutos = max(1, int(settings.distribuidos_bb_monitor_intervalo_min or 2))
    scheduler.add_job(
        _tick,
        trigger=IntervalTrigger(minutes=minutos),
        id=JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Monitor cadastro L1: registrado (a cada %s min).", minutos)
