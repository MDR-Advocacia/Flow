"""Orquestração da coleta na nuvem (Fase 2).

Fluxo por notificação, com LOG de tudo e ciência protegida:
  1. extrai do portal;
  2. WRITE-AHEAD: grava o processo (COLETADO) ANTES de qualquer ciência —
     nunca damos ciência sem registro;
  3. GATE de ciência (dupla trava: flag do run E flag global de segurança);
     só então clica "SIM" e marca CIENCIA_DADA;
  4. distribui (escritório + responsável + observação);
  5. commit por item (crash no meio não perde o que já foi feito).

Não conhece Playwright: recebe um `coletor` com a interface de
`portal.PortalBBColetor` (consultar/iterar/manter_sessao), o que permite
testar toda a lógica com um fake.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.distribuidos_bb import (
    BbProcesso,
    BbRun,
    NIVEL_AVISO,
    NIVEL_ERRO,
    NIVEL_INFO,
    NIVEL_SUCESSO,
    PROC_CIENCIA_DADA,
    PROC_COLETADO,
    RUN_CONCLUIDO,
    RUN_EM_ANDAMENTO,
    RUN_ERRO,
    SECAO_CIENCIA,
    SECAO_COLETA,
    SECAO_EXTRACAO,
    SECAO_SESSAO,
)
from app.services.distribuidos_bb import normalizacao as norm
from app.services.distribuidos_bb.distribuicao_service import distribuir_processo
from app.services.distribuidos_bb.log_service import registrar_evento

logger = logging.getLogger("distribuidos_bb.coleta")


def _fechar_sem_ciencia(notificacao: Any) -> None:
    """Clica 'NÃO' pra fechar o detalhe sem dar ciência (best-effort)."""
    try:
        cancelar = getattr(notificacao, "cancelar", None)
        if callable(cancelar):
            cancelar()
    except Exception:  # noqa: BLE001
        logger.warning("Distribuídos BB: falha ao fechar notificação com NÃO.", exc_info=True)


def criar_run(
    db: Session,
    *,
    data_inicial: Optional[str],
    data_final: Optional[str],
    confirmar_ciencia: bool,
    disparado_por_user_id: Optional[int],
) -> BbRun:
    run = BbRun(
        data_inicial=data_inicial,
        data_final=data_final,
        confirmar_ciencia=confirmar_ciencia,
        disparado_por_user_id=disparado_por_user_id,
        status=RUN_EM_ANDAMENTO,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _upsert_processo(db: Session, dados: dict[str, Any], run: BbRun) -> BbProcesso:
    """Write-ahead: grava/atualiza o processo (COLETADO) por fingerprint."""
    cnj = norm.normalizar_cnj(dados.get("Processo") or dados.get("cnj"))
    npj = (dados.get("NPJ") or dados.get("npj") or "").strip() or None
    fp = norm.fingerprint(cnj, npj)

    proc = (
        db.query(BbProcesso).filter(BbProcesso.fingerprint == fp).first()
        if fp != "sem-identidade"
        else None
    )
    novo = proc is None
    if proc is None:
        proc = BbProcesso(fingerprint=fp, status=PROC_COLETADO)
        db.add(proc)

    proc.run_id = run.id
    proc.cnj = cnj
    proc.npj = npj
    proc.notificacao_seq = dados.get("Notificação") or dados.get("notificacao_seq")
    proc.polo = (dados.get("Polo") or "").strip() or None
    proc.posicao = norm.polo_para_posicao(proc.polo)
    proc.natureza = (dados.get("Natureza") or "").strip() or None
    proc.acao = (dados.get("Ação") or "").strip() or None
    proc.valor_causa = norm.parse_valor_causa(dados.get("Valor da Causa"))
    proc.data_ajuizamento = norm.limpar_data_ajuizamento(dados.get("Data ajuizamento"))
    proc.situacao = (dados.get("Situação") or "").strip() or None
    proc.tramitacao = (dados.get("Tramitação") or "").strip() or None
    proc.advogado = (dados.get("Advogado") or "").strip() or None
    proc.adverso_principal = (dados.get("Adverso Principal") or "").strip() or None
    proc.raw = dados
    db.flush()

    registrar_evento(
        db,
        secao=SECAO_EXTRACAO,
        nivel=NIVEL_SUCESSO,
        acao="Capturado" if novo else "Reatualizado",
        mensagem=(
            f"Notificação {proc.notificacao_seq or '?'} lida e registrada: "
            f"{proc.posicao or '—'} · {proc.natureza or '—'} · "
            f"{proc.adverso_principal or 'sem adverso'}."
        ),
        dados={
            "cnj": cnj, "npj": npj, "polo": proc.polo, "natureza": proc.natureza,
            "valor_causa": float(proc.valor_causa) if proc.valor_causa is not None else None,
        },
        processo_id=proc.id,
        run_id=run.id,
    )
    return proc


def _processar_notificacao(db: Session, run: BbRun, notificacao: Any, *, gate_ciencia: bool) -> None:
    """Persiste (write-ahead), aplica ciência sob gate e distribui."""
    proc = _upsert_processo(db, notificacao.dados, run)
    run.total_coletados += 1

    # Gate de ciência: dupla trava (run + global). Nunca clica SIM fora disso.
    if gate_ciencia:
        ok = False
        try:
            ok = notificacao.confirmar_ciencia()
        except Exception as exc:  # noqa: BLE001
            registrar_evento(
                db, secao=SECAO_CIENCIA, nivel=NIVEL_ERRO, acao="Falha na ciência",
                mensagem=f"Erro ao tentar dar ciência: {exc}",
                processo_id=proc.id, run_id=run.id,
            )
        if ok:
            proc.status = PROC_CIENCIA_DADA
            proc.ciencia_dada_em = datetime.now(timezone.utc)
            run.total_ciencia += 1
            registrar_evento(
                db, secao=SECAO_CIENCIA, nivel=NIVEL_SUCESSO, acao="Ciência dada",
                mensagem="Ciência confirmada no portal (SIM). Ação irreversível registrada.",
                processo_id=proc.id, run_id=run.id,
            )
        else:
            # Não confirmou → fecha o modal com NÃO pra não travar a varredura.
            _fechar_sem_ciencia(notificacao)
            registrar_evento(
                db, secao=SECAO_CIENCIA, nivel=NIVEL_AVISO, acao="Ciência não dada",
                mensagem="Botão de ciência não confirmado (não visível ou falhou); fechado com NÃO.",
                processo_id=proc.id, run_id=run.id,
            )
    else:
        # Modo seguro: fecha o detalhe clicando NÃO (preserva a pendência).
        _fechar_sem_ciencia(notificacao)
        registrar_evento(
            db, secao=SECAO_CIENCIA, nivel=NIVEL_INFO, acao="Modo seguro",
            mensagem="Modo seguro ativo: o robô NÃO deu ciência (fechou com NÃO, notificação segue pendente).",
            processo_id=proc.id, run_id=run.id,
        )

    distribuir_processo(db, proc, run_id=run.id)
    if proc.responsavel_user_id:
        run.total_distribuidos += 1
    db.commit()


def executar_coleta(
    db: Session,
    run: BbRun,
    *,
    coletor: Any = None,
) -> BbRun:
    """Roda a coleta ponta a ponta e finaliza o run. `coletor` injetável p/ teste."""
    # Dupla trava de ciência: o run pediu E a flag global permite.
    gate_ciencia = bool(run.confirmar_ciencia and settings.distribuidos_bb_confirmar_ciencia)

    registrar_evento(
        db, secao=SECAO_SESSAO, nivel=NIVEL_INFO, acao="Início",
        mensagem=(
            f"Coleta iniciada (intervalo {run.data_inicial or 'hoje'} → "
            f"{run.data_final or 'hoje'}). Ciência: "
            + ("LIGADA" if gate_ciencia else "modo seguro (desligada)")
            + "."
        ),
        dados={"gate_ciencia": gate_ciencia}, run_id=run.id,
    )

    if coletor is None:
        from app.services.distribuidos_bb.portal import PortalBBColetor

        coletor = PortalBBColetor()

    try:
        with coletor as portal:
            qtd = portal.consultar(run.data_inicial, run.data_final)
            registrar_evento(
                db, secao=SECAO_COLETA, nivel=NIVEL_INFO, acao="Consulta",
                mensagem=f"{qtd} notificação(ões) encontrada(s) no intervalo.",
                dados={"quantidade": qtd}, run_id=run.id,
            )
            for notificacao in portal.iterar():
                try:
                    portal.manter_sessao()
                    _processar_notificacao(db, run, notificacao, gate_ciencia=gate_ciencia)
                except Exception as exc:  # noqa: BLE001
                    run.total_erros += 1
                    registrar_evento(
                        db, secao=SECAO_COLETA, nivel=NIVEL_ERRO, acao="Erro na notificação",
                        mensagem=f"Falha ao processar uma notificação: {exc}",
                        run_id=run.id,
                    )
                    db.commit()

        run.status = RUN_CONCLUIDO
        run.concluido_em = datetime.now(timezone.utc)
        registrar_evento(
            db, secao=SECAO_SESSAO, nivel=NIVEL_SUCESSO, acao="Concluído",
            mensagem=(
                f"Coleta concluída: {run.total_coletados} capturados, "
                f"{run.total_ciencia} com ciência, {run.total_distribuidos} distribuídos, "
                f"{run.total_erros} erro(s)."
            ),
            run_id=run.id,
        )
    except Exception as exc:  # noqa: BLE001
        run.status = RUN_ERRO
        run.erro = str(exc)
        run.concluido_em = datetime.now(timezone.utc)
        registrar_evento(
            db, secao=SECAO_SESSAO, nivel=NIVEL_ERRO, acao="Falha geral",
            mensagem=f"Coleta interrompida por erro: {exc}",
            run_id=run.id,
        )
        logger.exception("Distribuídos BB: coleta falhou (run %s).", run.id)
    finally:
        db.commit()

    return run


def executar_coleta_background(
    run_id: int,
    *,
    data_inicial: Optional[str],
    data_final: Optional[str],
) -> None:
    """Entrada pro background: abre sessão própria e roda a coleta do run."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        run = db.get(BbRun, run_id)
        if run is None:
            logger.error("Distribuídos BB: run %s não encontrado no background.", run_id)
            return
        executar_coleta(db, run)
    finally:
        db.close()
