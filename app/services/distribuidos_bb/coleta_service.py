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
    BbEnvolvido,
    BbProcesso,
    BbRun,
    CLIENTE_BB,
    CONTATO_NAO_RESOLVIDO,
    NIVEL_AVISO,
    NIVEL_ERRO,
    NIVEL_INFO,
    NIVEL_SUCESSO,
    PROC_CIENCIA_DADA,
    PROC_COLETADO,
    RUN_CONCLUIDO,
    RUN_EM_ANDAMENTO,
    RUN_ERRO,
    SECAO_CADASTRO,
    SECAO_CIENCIA,
    SECAO_COLETA,
    SECAO_ENVOLVIDOS,
    SECAO_EXTRACAO,
    SECAO_PLANILHA,
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


def _persistir_envolvidos(db: Session, proc: BbProcesso, lista: list) -> int:
    """Grava os envolvidos capturados na capa do NPJ (dedup por nome+documento)."""
    existentes = {(e.nome, e.cpf_cnpj) for e in proc.envolvidos}
    novos = 0
    for item in lista:
        nome = (item.get("nome") or "").strip()
        if not nome:
            continue
        cpf = norm.apenas_digitos(item.get("cpf_cnpj"))
        if cpf in ("0", ""):
            cpf = None
        chave = (nome, cpf)
        if chave in existentes:
            continue
        db.add(BbEnvolvido(
            processo_id=proc.id,
            nome=nome,
            papel=norm.polo_envolvido_normalizado(item.get("polo")),
            cpf_cnpj=cpf,
            tipo_pessoa=norm.tipo_pessoa_por_documento(cpf),
            status_contato=CONTATO_NAO_RESOLVIDO,
            raw={
                "mci": item.get("mci"),
                "relacao_bb": item.get("relacao"),
                "parte_principal": item.get("parte_principal"),
                "contrario_principal": item.get("contrario_principal"),
                "polo": item.get("polo"),
            },
        ))
        existentes.add(chave)
        novos += 1
    return novos


def _coletar_envolvidos(db: Session, run: BbRun, proc: BbProcesso, portal: Any) -> None:
    """Captura os envolvidos (Pessoas do Processo) da capa do NPJ — best-effort."""
    if not proc.npj or not hasattr(portal, "extrair_envolvidos"):
        return
    try:
        lista = portal.extrair_envolvidos(proc.npj)
    except Exception as exc:  # noqa: BLE001
        registrar_evento(
            db, secao=SECAO_ENVOLVIDOS, nivel=NIVEL_AVISO, acao="Falha ao ler envolvidos",
            mensagem=f"Não foi possível ler a capa do NPJ: {exc}",
            processo_id=proc.id, run_id=run.id,
        )
        return
    novos = _persistir_envolvidos(db, proc, lista or [])
    registrar_evento(
        db, secao=SECAO_ENVOLVIDOS, nivel=NIVEL_SUCESSO, acao="Envolvidos",
        mensagem=f"{novos} envolvido(s) capturado(s) da capa do NPJ (Pessoas do Processo).",
        dados={"capturados": len(lista or []), "novos": novos},
        processo_id=proc.id, run_id=run.id,
    )


def _processar_notificacao(
    db: Session, run: BbRun, notificacao: Any, portal: Any, *,
    gate_ciencia: bool, coletar_envolvidos: bool,
) -> None:
    """Persiste (write-ahead), captura envolvidos, aplica ciência sob gate e distribui."""
    proc = _upsert_processo(db, notificacao.dados, run)
    run.total_coletados += 1

    # Capa do NPJ (Pessoas do Processo) — página separada, não mexe na lista.
    if coletar_envolvidos:
        _coletar_envolvidos(db, run, proc, portal)

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


def _auto_cadastrar(db: Session, run: BbRun) -> None:
    """Gera a planilha do pool NOVO e importa no L1 (cria pastas + workflow).

    Best-effort: quem chama já embrulha em try/except. Marca os processos como
    PENDENTE_CADASTRO (via gerar_e_persistir) e dispara o import interno; o monitor
    confirma cada pasta depois (→ CADASTRADO_L1).
    """
    from app.services.distribuidos_bb.import_l1_service import cadastrar_planilha
    from app.services.distribuidos_bb.planilha_service import gerar_e_persistir

    # Só o pool do BB: senão varreria junto os Ativos pendentes (outro cliente,
    # outro fluxo de entrada) e os misturaria nesta planilha e neste run.
    planilha = gerar_e_persistir(db, cliente=CLIENTE_BB)
    if planilha is None:
        return
    db.commit()
    registrar_evento(
        db, secao=SECAO_CADASTRO, nivel=NIVEL_INFO, acao="Auto-cadastro iniciado",
        mensagem=(
            f"Planilha '{planilha.nome_arquivo}' ({planilha.total_processos} processo[s]) "
            f"gerada; importando no Legal One automaticamente…"
        ),
        dados={"planilha_id": planilha.id}, run_id=run.id,
    )
    db.commit()

    rel = cadastrar_planilha(bytes(planilha.conteudo), planilha.nome_arquivo, dry_run=False)
    novos = rel.get("novos", 0)
    # O robô subiu a planilha → marca como subida (não fica pendente na tela).
    planilha.subido_legalone = True
    planilha.subido_em = datetime.now(timezone.utc)
    # Contador do run (a UI mostra "cadastrados"): sem isto ficava 0 pra sempre,
    # mesmo com as pastas criadas no L1 — parecia que a rodagem não cadastrou nada.
    run.total_cadastrados += int(novos or 0)
    registrar_evento(
        db, secao=SECAO_CADASTRO, nivel=NIVEL_SUCESSO, acao="Auto-cadastro enviado",
        mensagem=(
            f"Import no Legal One enviado: {novos} pasta(s) nova(s) criada(s). "
            f"{rel.get('resultado', '')} O monitor confirma cada uma nos próximos ciclos."
        ),
        dados={"novos": novos, "planilha_id": planilha.id}, run_id=run.id,
    )
    db.commit()


def executar_coleta(
    db: Session,
    run: BbRun,
    *,
    coletor: Any = None,
    coletar_envolvidos: bool = True,
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
                    _processar_notificacao(
                        db, run, notificacao, portal,
                        gate_ciencia=gate_ciencia, coletar_envolvidos=coletar_envolvidos,
                    )
                except Exception as exc:  # noqa: BLE001
                    run.total_erros += 1
                    registrar_evento(
                        db, secao=SECAO_COLETA, nivel=NIVEL_ERRO, acao="Erro na notificação",
                        mensagem=f"Falha ao processar uma notificação: {exc}",
                        run_id=run.id,
                    )
                    db.commit()

            # Verificação pós-coleta: re-consulta a lista no BB pra confirmar
            # que ZEROU (quando deu ciência) ou quanto sobrou (modo seguro).
            try:
                restantes = portal.consultar(run.data_inicial, run.data_final)
                # Sinaliza pro wrapper de retentativa (atributo transiente, não
                # persistido): sobrou pendência mesmo com ciência = inconsistência.
                run._pos_coleta_restantes = restantes
                if gate_ciencia:
                    nivel_vf = NIVEL_SUCESSO if restantes == 0 else NIVEL_AVISO
                    msg_vf = (
                        "Verificação pós-coleta: lista de pendências ZERADA no BB."
                        if restantes == 0
                        else (
                            f"Verificação pós-coleta: {restantes} notificação(ões) ainda "
                            f"pendente(s) no BB (esperava 0 após a ciência) — revisar."
                        )
                    )
                else:
                    nivel_vf = NIVEL_INFO
                    msg_vf = (
                        f"Verificação pós-coleta: {restantes} notificação(ões) seguem "
                        f"pendentes no BB (esperado — modo seguro, nada recebeu ciência)."
                    )
                registrar_evento(
                    db, secao=SECAO_COLETA, nivel=nivel_vf, acao="Verificação pós-coleta",
                    mensagem=msg_vf,
                    dados={"restantes": restantes, "gate_ciencia": gate_ciencia},
                    run_id=run.id,
                )
                db.commit()
            except Exception as exc_vf:  # noqa: BLE001
                logger.warning(
                    "Distribuídos BB: verificação pós-coleta falhou (run %s): %s",
                    run.id, exc_vf,
                )

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
        db.commit()

        # Pool de planilha: NÃO gera planilha automática. Os distribuídos ficam
        # como NOVO (default) aguardando o operador mandar gerar. Aqui só
        # sinalizamos o que entrou no pool — e avisamos quando não veio nada.
        try:
            from app.services.distribuidos_bb.planilha_service import contar_pool_novos

            novos_pool = contar_pool_novos(db, cliente=CLIENTE_BB)
            if run.total_distribuidos > 0:
                registrar_evento(
                    db, secao=SECAO_PLANILHA, nivel=NIVEL_INFO, acao="Pool atualizado",
                    mensagem=(
                        f"{run.total_distribuidos} processo(s) novo(s) desta execução "
                        f"entraram no pool. Pool total aguardando planilha: {novos_pool}. "
                        f"O operador gera a planilha quando quiser."
                    ),
                    dados={"novos_execucao": run.total_distribuidos, "pool_total": novos_pool},
                    run_id=run.id,
                )
            else:
                registrar_evento(
                    db, secao=SECAO_PLANILHA, nivel=NIVEL_AVISO, acao="Sem processos",
                    mensagem=(
                        "Esta execução não teve processos novos — nada entrou no pool "
                        f"(pool total aguardando planilha segue em {novos_pool})."
                    ),
                    run_id=run.id,
                )
            db.commit()
        except Exception as exc_pool:  # noqa: BLE001
            logger.warning(
                "Distribuídos BB: falha ao sinalizar o pool (run %s): %s", run.id, exc_pool,
            )

        # Cadastro 100% automático (best-effort, nunca derruba o run): se ligado e
        # veio processo novo, gera a planilha do pool e importa no L1 (cria pastas
        # + dispara workflow). O monitor confirma cada pasta depois.
        if settings.distribuidos_bb_auto_cadastro_ativo and run.total_distribuidos > 0:
            try:
                _auto_cadastrar(db, run)
            except Exception as exc_ac:  # noqa: BLE001
                registrar_evento(
                    db, secao=SECAO_CADASTRO, nivel=NIVEL_ERRO, acao="Falha no auto-cadastro",
                    mensagem=f"Coleta ok, mas o cadastro automático no L1 falhou: {exc_ac}",
                    run_id=run.id,
                )
                db.commit()
                logger.exception("Distribuídos BB: auto-cadastro falhou (run %s).", run.id)
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


def _motivo_para_repetir(run: BbRun, *, erros_antes: int = 0) -> Optional[str]:
    """Por que esta rodagem merece nova tentativa? None = está tudo certo.

    Dois casos, ambos vistos em prod:
    - ERRO: falha geral (ex.: o SPA do PAJ não montou) — o run morre com 0 tudo;
    - INCONSISTÊNCIA: concluiu, mas alguma notificação falhou no meio, ou a
      verificação pós-coleta achou pendência sobrando mesmo com a ciência ligada.

    `erros_antes` = total_erros no início DESTA tentativa (o contador é cumulativo
    entre tentativas, então só interessa o que falhou agora).
    """
    if run.status == RUN_ERRO:
        return f"a rodagem falhou ({(run.erro or 'erro não detalhado')[:120]})"
    novos_erros = run.total_erros - erros_antes
    if novos_erros > 0:
        return f"{novos_erros} notificação(ões) falharam no meio da rodagem"
    gate_ciencia = bool(run.confirmar_ciencia and settings.distribuidos_bb_confirmar_ciencia)
    restantes = int(getattr(run, "_pos_coleta_restantes", 0) or 0)
    if gate_ciencia and restantes > 0:
        return f"sobraram {restantes} pendência(s) no BB após a ciência (esperava 0)"
    return None


def executar_coleta_background(
    run_id: int,
    *,
    data_inicial: Optional[str],
    data_final: Optional[str],
    coletar_envolvidos: bool = True,
) -> None:
    """Entrada pro background: abre sessão própria e roda a coleta do run.

    **Trava de resiliência**: o portal do BB é intermitente (falha numa rodagem e
    passa na seguinte, sem mudar nada). Então em vez de deixar o run morrer no
    erro esperando o operador mandar repetir na mão, repetimos automaticamente.

    Por que repetir é seguro mesmo com a ciência sendo IRREVERSÍVEL:
    - a ciência REMOVE a notificação da lista de pendências do BB, então a nova
      tentativa só enxerga o que ainda não foi tratado (retomada, não repetição);
    - o `fingerprint` faz upsert, então processo já capturado não duplica no banco;
    - as falhas observadas em prod acontecem no `_localizar_frame`, ANTES de
      qualquer ciência (run morre com 0 coletados).
    Os contadores do run são cumulativos entre as tentativas — refletem o total
    real da rodagem, já que o que foi feito não é refeito.
    """
    import time as _t

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        run = db.get(BbRun, run_id)
        if run is None:
            logger.error("Distribuídos BB: run %s não encontrado no background.", run_id)
            return

        tentativas = max(1, int(settings.distribuidos_bb_coleta_tentativas or 1))
        espera = max(0, int(settings.distribuidos_bb_coleta_retry_espera_seg or 0))

        for tentativa in range(1, tentativas + 1):
            erros_antes = run.total_erros
            executar_coleta(db, run, coletar_envolvidos=coletar_envolvidos)

            motivo = _motivo_para_repetir(run, erros_antes=erros_antes)
            if motivo is None:
                if tentativa > 1:
                    registrar_evento(
                        db, secao=SECAO_SESSAO, nivel=NIVEL_SUCESSO, acao="Recuperado na retentativa",
                        mensagem=(
                            f"A rodagem se recuperou sozinha na tentativa {tentativa} de "
                            f"{tentativas} — não precisou de repetição manual."
                        ),
                        dados={"tentativa": tentativa}, run_id=run.id,
                    )
                    db.commit()
                return

            if tentativa >= tentativas:
                registrar_evento(
                    db, secao=SECAO_SESSAO, nivel=NIVEL_ERRO, acao="Esgotou as tentativas",
                    mensagem=(
                        f"Desisti após {tentativas} tentativa(s): {motivo}. "
                        f"Precisa de olhada manual."
                    ),
                    dados={"tentativas": tentativas, "motivo": motivo}, run_id=run.id,
                )
                db.commit()
                return

            registrar_evento(
                db, secao=SECAO_SESSAO, nivel=NIVEL_AVISO, acao="Repetindo a rodagem",
                mensagem=(
                    f"Tentativa {tentativa} de {tentativas} não fechou limpa: {motivo}. "
                    f"Repetindo automaticamente em {espera}s (o que já teve ciência "
                    f"não é refeito — sai da lista do BB)."
                ),
                dados={"tentativa": tentativa, "motivo": motivo}, run_id=run.id,
            )
            # Reabre o run pra nova tentativa (o executar_coleta o fecha como ERRO/CONCLUIDO).
            run.status = RUN_EM_ANDAMENTO
            run.erro = None
            run.concluido_em = None
            run._pos_coleta_restantes = 0
            db.commit()

            if espera:
                _t.sleep(espera)
    finally:
        db.close()
