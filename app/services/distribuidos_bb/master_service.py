"""Ingestão do cliente Banco Master: Listagem de Ações Judiciais → pré-cadastro.

O operador exporta a "Listagem de Ações Judiciais" do sistema do cliente e sobe
aqui. Diferente da Ativos (lista seca de números, que o DataJud precisa
enriquecer), **a Listagem já traz a capa completa** — ação, polo, partes, UF,
comarca, data de ajuizamento e valor da causa. Por isso este fluxo NÃO consulta
o DataJud: não há o que enriquecer, e uma consulta por processo só adicionaria
latência e chance de erro.

Antes disso existir, o Master era atendido por um módulo separado (Administração
→ Base Banco Master → Conversão L1) que apenas CONVERTIA a planilha e devolvia o
xlsx pro operador subir na mão. Funcionava, mas não persistia nada: sem lote, sem
métrica, sem saber o que chegou por dia. As regras de negócio abaixo são as
mesmas daquele módulo (`base_processual/conversao_l1.py`), agora com os valores
em configuração editável em vez de constantes de código.

Server-backed com progresso: um `BbAtivosLote` (cliente=MASTER) rastreia o
andamento, igual à Ativos.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.distribuidos_bb import (
    CLIENTE_MASTER,
    DUP_REPETIDO_LOTE,
    LOTE_CONCLUIDO,
    LOTE_ERRO,
    PARTE_A_CLASSIFICAR,
    POOL_NOVO,
    PROC_DISTRIBUIDO,
    BbAtivosDuplicado,
    BbAtivosLote,
    BbConfig,
    BbProcesso,
)
from app.services.base_processual.conversao_l1 import (
    _converter_valor_causa,
    _eh_agravo,
    _extrair_nome,
    _ler_linhas_listagem,
    _limpar_data,
)
from app.services.distribuidos_bb.ativos_service import (
    _montar_tramitacao,
    _natureza_do_cnj,
)
from app.services.distribuidos_bb.datajud_ativos import apenas_digitos, formatar_cnj
from app.services.distribuidos_bb.distribuicao_service import distribuir_processo

logger = logging.getLogger("distribuidos_bb.master")


def _cfg(db: Session, chave: str, default: str) -> str:
    c = db.get(BbConfig, chave)
    return c.valor if (c and c.valor is not None) else default


def _fingerprint_master(cnj: str) -> str:
    # Prefixo por cliente: o mesmo CNJ pode existir pra BB, Ativos e Master
    # (pastas diferentes, a MDR conduz lados diferentes), então não pode colidir
    # no unique de fingerprint.
    return f"master:cnj:{cnj}"


def _formatar_data(valor: Any) -> Optional[str]:
    """Data de ajuizamento como texto dd/mm/aaaa (é o que a planilha do L1 espera).

    A Listagem devolve datetime quando a célula é data de verdade e string
    quando veio digitada; "A cadastrar" já é filtrado por `_limpar_data`.
    """
    valor = _limpar_data(valor)
    if valor is None or valor == "":
        return None
    if isinstance(valor, (datetime, date)):
        return valor.strftime("%d/%m/%Y")
    return str(valor).strip() or None


def parse_listagem_master(conteudo: bytes) -> list[dict]:
    """Lê a Listagem de Ações Judiciais e devolve as linhas canônicas.

    Reusa o leitor do módulo de conversão (`_ler_linhas_listagem`), que localiza
    o cabeçalho pela coluna "Polo" e tolera as variações de nome do export.
    Levanta ValueError se o arquivo não for uma Listagem.

    Dedup por CNJ dentro do próprio arquivo — a Listagem repete o processo
    quando ele tem mais de um envolvido.
    """
    linhas: list[dict] = []
    vistos: set[str] = set()
    for raw in _ler_linhas_listagem(conteudo):
        digs = apenas_digitos(raw.get("Processo"))
        if len(digs) != 20:
            # Número fora do padrão CNJ (20 dígitos) — sem ele não há como
            # deduplicar nem casar a pasta no L1 depois.
            linhas.append({"cnj": None, "digitos": digs, "raw": raw})
            continue
        if digs in vistos:
            continue
        vistos.add(digs)
        linhas.append({"cnj": formatar_cnj(digs), "digitos": digs, "raw": raw})
    return linhas


def criar_lote(
    db: Session, *, nome_arquivo: str, total: int, user_id: Optional[int],
) -> BbAtivosLote:
    lote = BbAtivosLote(
        cliente=CLIENTE_MASTER,
        nome_arquivo=nome_arquivo,
        total=total,
        disparado_por_user_id=user_id,
    )
    db.add(lote)
    db.commit()
    db.refresh(lote)
    return lote


def _cadastrar_lote(db: Session, lote_id: int, processo_ids: list[int]) -> None:
    """Gera a planilha de migração DESTE lote e importa no Legal One.

    Mesmo caminho da Ativos: o import interno é o que dispara o workflow no L1
    (o POST /Lawsuits da API REST não dispara). O monitor confirma cada pasta
    depois. É esta etapa que substitui o "baixa o xlsx e sobe na mão" do módulo
    antigo do Master.
    """
    from app.services.distribuidos_bb.cadastro_descartes import registrar_descartes
    from app.services.distribuidos_bb.import_l1_service import cadastrar_planilha
    from app.services.distribuidos_bb.planilha_service import (
        cnjs_liberados_da_planilha,
        gerar_e_persistir,
    )

    planilha = gerar_e_persistir(db, processo_ids=processo_ids, cliente=CLIENTE_MASTER)
    if planilha is None:
        return
    db.commit()

    # `cnjs_liberados` é obrigatório: o L1 marca `duplicated` sempre que já
    # existe pasta com aquele CNJ no tenant, INCLUSIVE quando é de outro cliente.
    # Como a MDR conduz os dois lados em vários processos, isso é rotina — a
    # falta dessa liberação já recusou pasta da Ativos em 10/08/2026. A trava
    # anterior (`_marcar_ja_existentes_no_l1`) já removeu quem tinha pasta do
    # MESMO cliente, então o que sobra pendente é duplicata de outro cliente e
    # DEVE ser cadastrada.
    rel = cadastrar_planilha(
        bytes(planilha.conteudo), planilha.nome_arquivo, dry_run=False,
        cnjs_liberados=cnjs_liberados_da_planilha(db, planilha.id),
    )
    registrar_descartes(db, rel, planilha_id=planilha.id)
    planilha.subido_legalone = True
    planilha.subido_em = datetime.now(timezone.utc)
    db.commit()
    logger.info(
        "Master: lote %s → planilha '%s' importada no L1 (%s pasta[s] nova[s]).",
        lote_id, planilha.nome_arquivo, rel.get("novos", 0),
    )


def _registrar_duplicado(
    db: Session, *, lote_id: int, cnj: str, digs: str, motivo: str, parte: Optional[str],
) -> None:
    """Persiste um CNJ pulado. Best-effort — nunca derruba a ingestão."""
    try:
        ja = (
            db.query(BbAtivosDuplicado)
            .filter(
                BbAtivosDuplicado.lote_id == lote_id,
                BbAtivosDuplicado.cnj_digitos == digs,
            )
            .first()
        )
        if ja:
            return
        db.add(BbAtivosDuplicado(
            lote_id=lote_id, cnj=cnj, cnj_digitos=digs, motivo=motivo,
            parte=(parte or None),
        ))
    except Exception:  # noqa: BLE001
        logger.warning("Master: falha ao registrar duplicado %s (lote %s).", cnj, lote_id)


def ingerir_lote_background(lote_id: int, linhas: list[dict]) -> None:
    """Cria os processos a partir da Listagem e fecha no cadastro do L1.

    Sem DataJud: a Listagem já é a capa. Roda em thread própria.
    """
    from app.db.session import SessionLocal

    db = SessionLocal()
    criados_ids: list[int] = []
    try:
        lote = db.get(BbAtivosLote, lote_id)
        if lote is None:
            return

        # Termo do agravo: editável, mas o default é o que o módulo antigo
        # gravava. A observação é o que dispara o workflow certo no L1, então
        # errar aqui manda o processo pro fluxo errado.
        termo_agravo = _cfg(db, "master_observacao_agravo", "bmagravo")

        for linha in linhas:
            cnj = linha.get("cnj")
            raw = linha.get("raw") or {}
            try:
                if not cnj:
                    lote.invalidos += 1
                    lote.processados += 1
                    db.commit()
                    continue

                digs = linha["digitos"]
                fp = _fingerprint_master(cnj)
                if db.query(BbProcesso).filter(BbProcesso.fingerprint == fp).first():
                    lote.duplicados += 1
                    lote.processados += 1
                    _registrar_duplicado(
                        db, lote_id=lote_id, cnj=cnj, digs=digs,
                        motivo=DUP_REPETIDO_LOTE,
                        parte=_extrair_nome(raw.get("Autores", "")) or None,
                    )
                    db.commit()
                    continue

                # O Master é SEMPRE Réu — é a premissa da carteira, e era
                # hardcoded no módulo antigo ("Banco Master sempre como Réu").
                # O polo Passivo é o que casa a linha de roteamento do Master
                # em bbd_escritorios.
                adverso = _extrair_nome(raw.get("Autores", "")) or PARTE_A_CLASSIFICAR
                proc = BbProcesso(
                    cliente=CLIENTE_MASTER,
                    cnj=cnj,
                    fingerprint=fp,
                    status=PROC_DISTRIBUIDO,
                    planilha_status=POOL_NOVO,
                    posicao="Réu",
                    polo="Passivo",
                    # Natureza = CATÁLOGO do L1 (Civel/Trabalhista, ASCII). O
                    # módulo antigo fixava "Cível" com acento, o que servia no
                    # upload manual mas reprova a validação do import
                    # automático (o parser do L1 mutila o acento do nosso xlsx).
                    # Pelo segmento J do CNJ também cobre o caso trabalhista.
                    natureza=_natureza_do_cnj(digs),
                    acao=(raw.get("Ação") or None),
                    adverso_principal=adverso,
                    data_ajuizamento=_formatar_data(raw.get("Data ajuizamento")),
                    tramitacao=_montar_tramitacao(
                        raw.get("UF"), raw.get("Comarca"),
                    ),
                    valor_causa=_converter_valor_causa(raw.get("Valor da Causa")),
                    raw={"master_listagem": {
                        k: (v.isoformat() if isinstance(v, (datetime, date)) else v)
                        for k, v in raw.items()
                    }},
                )

                db.add(proc)
                db.flush()  # precisa do id pros eventos da distribuição

                # Distribuição: mesmo motor das outras carteiras. O escritório do
                # Master tem responsável FIXO, então não consome rodízio.
                distribuir_processo(db, proc)

                # Agravo: a única exceção da observação padrão (bmcomum, vinda do
                # escritório). A regra é do módulo antigo — ação "Agravo de
                # Instrumento" OU número terminado em ".0000" — e não cabe na
                # tabela de regras editáveis, que não olha o texto da ação.
                if _eh_agravo(raw.get("Ação"), raw.get("Processo")):
                    proc.observacao = termo_agravo

                lote.criados += 1
                lote.processados += 1
                db.commit()
                criados_ids.append(proc.id)
            except Exception:  # noqa: BLE001
                db.rollback()
                logger.exception("Master: falha ao ingerir CNJ %s (lote %s).", cnj, lote_id)
                lote = db.get(BbAtivosLote, lote_id)
                if lote:
                    lote.processados += 1
                    db.commit()

        # Fecha a sequência: planilha do que ENTROU neste lote e cadastro no L1.
        # Best-effort: nunca derruba o lote (os processos já estão salvos e
        # podem ser recadastrados pelo pool).
        if criados_ids:
            try:
                _cadastrar_lote(db, lote_id, criados_ids)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Master: cadastro do lote %s falhou.", lote_id)
                lote = db.get(BbAtivosLote, lote_id)
                if lote:
                    lote.erro = f"Processos criados, mas o cadastro no L1 falhou: {exc}"
                    db.commit()

        lote = db.get(BbAtivosLote, lote_id)
        if lote:
            lote.status = LOTE_CONCLUIDO
            lote.concluido_em = datetime.now(timezone.utc)
            db.commit()
        logger.info(
            "Master: lote %s concluído (criados=%s, dup=%s, inválidos=%s).",
            lote_id,
            lote.criados if lote else "?",
            lote.duplicados if lote else "?",
            lote.invalidos if lote else "?",
        )
    except Exception:  # noqa: BLE001
        logger.exception("Master: erro geral no lote %s.", lote_id)
        try:
            lote = db.get(BbAtivosLote, lote_id)
            if lote:
                lote.status = LOTE_ERRO
                lote.erro = "Erro inesperado na ingestão."
                db.commit()
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()


def disparar_ingestao(
    db: Session, *, conteudo: bytes, nome_arquivo: str, user_id: Optional[int],
) -> dict:
    """Lê a Listagem, cria o lote e dispara a ingestão em background."""
    linhas = parse_listagem_master(conteudo)
    validas = [x for x in linhas if x.get("cnj")]
    if not validas:
        raise ValueError(
            "Nenhum processo com número CNJ válido encontrado na Listagem."
        )
    lote = criar_lote(
        db, nome_arquivo=nome_arquivo, total=len(linhas), user_id=user_id,
    )
    thread = threading.Thread(
        target=ingerir_lote_background, args=(lote.id, linhas), daemon=True,
    )
    thread.start()
    return {
        "lote_id": lote.id,
        "total": len(linhas),
        "validos": len(validas),
        "invalidos": len(linhas) - len(validas),
    }
