"""Controle de Embargos — visão única dos embargos à execução do BB Autor.

Embargos à execução correm em autos APARTADOS e chegam ao escritório por três
caminhos, que até 14/09/2026 viviam em telas separadas:

  • TRIBUNAL — o advogado da casa NÃO está no processo dos embargos: só o monitor
    deste módulo (DataJud + DJEN) descobre;
  • PUBLICAÇÃO SEM PASTA — ele está, mas a pasta dos embargos não existe no L1:
    o motor sem pasta de Publicações classifica "Embargos à Execução" (e cria a
    tarefa 1404 "Verificar novo Embargo");
  • PUBLICAÇÃO COM PASTA — a publicação caiu numa pasta do BB Autor (escritório
    22) com subcategoria "Embargos à execução / monitórios": na pasta do
    incidente (trabalho feito) ou na pasta da EXECUÇÃO sem incidente (falha de
    cadastro — decisão do operador).

Este módulo JUNTA as três num caso por execução + embargos, sem mexer no motor
de Publicações (regra dos dois motores): só lê `publicacao_registros` e o L1.
Regra de fim, dita pelo operador: a pasta incidental existe no L1 = trabalho da
Controladoria feito, ponto — não há controle de tarefa de resposta.
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.embargos_execucao import (
    CASO_CADASTRADO,
    CASO_DESCARTADO,
    CASO_PENDENTE,
    DECISAO_CONFIRMADO,
    DECISAO_PENDENTE,
    DECISAO_RECUSADO,
    ESTADO_AGUARDANDO_JANELA,
    ESTADO_CONCLUIDO,
    ESTADO_CONFIRMADO,
    ESTADO_ENCONTRADO,
    ESTADO_JA_CADASTRADO,
    ESTADO_MONITORANDO,
    ESTADO_SEM_CNJ,
    EVT_AVISO,
    NIVEIS_FORTES,
    NIVEL_PUBLICACAO,
    ORIG_L1,
    ORIG_PUB_INCIDENTE,
    ORIG_PUB_NA_PASTA,
    ORIG_PUB_SEM_PASTA,
    ORIG_TRIBUNAL,
    ORIGENS_COLUNA,
    ORIGENS_PUBLICACAO,
    PUB_IGNORADA_MONITORIA,
    PUB_IGNORADA_NAO_EMBARGOS,
    PUB_IGNORADA_PROPRIOS_AUTOS,
    SECAO_CONTROLE,
    SECAO_L1,
    SECAO_OPERADOR,
    EmbCandidato,
    EmbCaso,
    EmbCasoPublicacao,
    EmbEvento,
    EmbExecucao,
)
from app.models.publication_search import (
    RECORD_STATUS_DISCARDED_DUPLICATE,
    RECORD_STATUS_IGNORED,
    RECORD_STATUS_OBSOLETE,
    PublicationRecord,
)
from app.services.app_settings import get_setting, set_setting
from app.services.embargos_execucao import datajud_embargos, service
from app.services.embargos_execucao.normaliza import (
    cnj_digitos,
    formatar_cnj,
    nome_normalizado,
    nomes_batem,
)

logger = logging.getLogger(__name__)

OFFICE_BB_AUTOR = 22
# Mesmo literal de publication_sem_pasta_motor.TIPO_EMBARGOS_EXECUCAO (não
# importa o motor: ele puxa o cliente de IA).
TIPO_EMBARGOS_SEM_PASTA = "Embargos à Execução"
# "Defesa do Devedor e Incidentes / Embargos à execução / monitórios" (BB Autor).
SUBCATEGORIA_EMBARGOS = "embargos à execu%"
CLASSE_EMBARGOS_EXECUCAO = 172
_STATUS_FORA = (RECORD_STATUS_IGNORED, RECORD_STATUS_DISCARDED_DUPLICATE, RECORD_STATUS_OBSOLETE)

VERIFICAR_L1_A_CADA = timedelta(hours=6)
# Menos de 24 h: o job é diário e a passagem de ontem terminou minutos depois
# do horário — com 24 h exatas o caso pularia um dia sim, um dia não.
TENTAR_VINCULO_A_CADA = timedelta(hours=20)

ESTADOS_VIGILANCIA = (ESTADO_SEM_CNJ, ESTADO_AGUARDANDO_JANELA, ESTADO_MONITORANDO)
ETAPA_VIGILANCIA = "vigilancia"
ETAPA_PENDENTE = "pendente"
ETAPA_CADASTRADO = "cadastrado"
ETAPA_DESCARTADO = "descartado"
_ETAPA_ESTADO = {
    ETAPA_PENDENTE: CASO_PENDENTE,
    ETAPA_CADASTRADO: CASO_CADASTRADO,
    ETAPA_DESCARTADO: CASO_DESCARTADO,
}

SETTING_STATUS = "embargos_controle_status"

_CNJ_RE = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")
_MONITORIA = re.compile(r"monit[óo]ri", re.I)
_EMBARGOS_EXECUCAO_TXT = re.compile(r"embargos\s+(?:[àa]\s+execu|do\s+devedor|do\s+executado)", re.I)
_PROPRIOS_AUTOS = re.compile(r"(?:nos|nestes|dos)\s+pr[óo]prios\s+autos", re.I)


# ── utilidades ───────────────────────────────────────────────────────
def _agora() -> datetime:
    return service.agora()


def _evento(db: Session, caso: EmbCaso, mensagem: str, **kw: Any) -> None:
    service.registrar_evento(
        db, kw.pop("secao", SECAO_CONTROLE), mensagem,
        caso_id=caso.id, execucao_id=kw.pop("execucao_id", caso.execucao_id), **kw,
    )


def origens_do_caso(caso: EmbCaso) -> list[str]:
    return [o for o, col in ORIGENS_COLUNA.items() if getattr(caso, col)]


def _add_origem(db: Session, caso: EmbCaso, origem: str) -> None:
    col = ORIGENS_COLUNA[origem]
    if getattr(caso, col):
        return
    setattr(caso, col, True)
    if not caso.primeira_origem:
        caso.primeira_origem = origem
    elif origem != caso.primeira_origem:
        _evento(db, caso, f"O mesmo caso chegou também por {ORIGEM_TEXTO[origem]} — vinculado, sem nova tarefa.")


ORIGEM_TEXTO = {
    ORIG_TRIBUNAL: "consulta ao tribunal",
    ORIG_PUB_SEM_PASTA: "publicação sem pasta",
    ORIG_PUB_NA_PASTA: "publicação na pasta da execução",
    ORIG_PUB_INCIDENTE: "publicação na pasta dos embargos",
    ORIG_L1: "Legal One",
}


def _novo_caso(db: Session, cnj: Optional[str] = None) -> EmbCaso:
    d = cnj_digitos(cnj)
    caso = EmbCaso(
        cnj_embargos=formatar_cnj(d) if d else None, cnj_embargos_digitos=d,
        estado=CASO_PENDENTE, detectado_em=_agora(), verificacoes_l1=0,
        falha_cadastro=False, vinculo_confirmado=False,
        origem_tribunal=False, origem_pub_sem_pasta=False, origem_pub_na_pasta=False,
        origem_pub_incidente=False, origem_l1=False,
    )
    db.add(caso)
    db.flush()
    return caso


def _caso_por_cnj(db: Session, cnj: Optional[str], *, criar: bool = True) -> Optional[EmbCaso]:
    d = cnj_digitos(cnj)
    if not d:
        return None
    caso = db.query(EmbCaso).filter(EmbCaso.cnj_embargos_digitos == d).first()
    if caso is None and criar:
        caso = _novo_caso(db, d)
        _evento(db, caso, f"Caso aberto: embargos {caso.cnj_embargos}.")
    return caso


def _trecho(texto: Optional[str]) -> Optional[str]:
    if not texto:
        return None
    i = max(0, texto.upper().find("EMBARG"))
    return re.sub(r"\s+", " ", texto[max(0, i - 200): i + 500]).strip() or None


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _detectado_desde(caso: EmbCaso, quando: Optional[datetime]) -> None:
    """Detecção = o primeiro sinal (publicação capturada / achado do monitor),
    não o dia em que o controle leu."""
    quando = _utc(quando)
    if quando is not None and (caso.detectado_em is None or quando < _utc(caso.detectado_em)):
        caso.detectado_em = quando


def _ja_lidas(db: Session) -> set[int]:
    return {r[0] for r in db.query(EmbCasoPublicacao.publicacao_id)}


def _registrar_publicacao(db: Session, caso: Optional[EmbCaso], rec: PublicationRecord,
                          origem: str, lidas: set[int]) -> None:
    if rec.id in lidas:
        return
    if caso is not None:
        _detectado_desde(caso, rec.created_at)
    db.add(EmbCasoPublicacao(
        caso_id=caso.id if caso else None, publicacao_id=rec.id, origem=origem,
        data_publicacao=(rec.publication_date or "")[:10] or None,
        status_publicacao=rec.status, linked_lawsuit_id=rec.linked_lawsuit_id,
        trecho=_trecho(rec.description),
    ))
    db.flush()
    lidas.add(rec.id)


def _vincular_execucao(db: Session, caso: EmbCaso, *, exe: Optional[EmbExecucao] = None,
                       cnj_execucao: Optional[str] = None, pasta: Optional[str] = None,
                       lawsuit_id: Optional[int] = None, motivo: Optional[str] = None) -> None:
    d = cnj_digitos(cnj_execucao)
    if exe is None and d:
        exe = db.query(EmbExecucao).filter(EmbExecucao.cnj_digitos == d).first()
    if exe is None and pasta:
        exe = db.query(EmbExecucao).filter(EmbExecucao.pasta == pasta).first()
    caso.cnj_execucao = caso.cnj_execucao or (formatar_cnj(d) if d else None) or (exe.cnj if exe else None)
    pasta_exe = exe.pasta if exe and not exe.pasta.startswith("CNJ ") else None
    caso.pasta_execucao = caso.pasta_execucao or pasta or pasta_exe
    caso.lawsuit_id_execucao = caso.lawsuit_id_execucao or lawsuit_id or (exe.lawsuit_id if exe else None)
    if exe is not None and caso.execucao_id != exe.id:
        caso.execucao_id = exe.id
        caso.execucao = exe
        _evento(db, caso, f"Vinculado à execução {exe.pasta}" + (f" ({motivo})." if motivo else "."),
                execucao_id=exe.id)


def _marcar_cadastrado(db: Session, caso: EmbCaso, *, incidente_id: Optional[int],
                       folder: Optional[str], via: str) -> None:
    if caso.estado == CASO_CADASTRADO and caso.incidente_folder:
        return
    caso.estado = CASO_CADASTRADO
    caso.incidente_id = incidente_id or caso.incidente_id
    caso.incidente_folder = folder or caso.incidente_folder
    caso.cadastrado_em = caso.cadastrado_em or _agora()
    _evento(db, caso, f"Pasta dos embargos no Legal One: {caso.incidente_folder or f'id {incidente_id}'} ({via}). Caso encerrado.",
            secao=SECAO_L1)
    exe = caso.execucao
    if exe is not None and exe.estado in (*ESTADOS_VIGILANCIA, ESTADO_ENCONTRADO):
        exe.estado = ESTADO_JA_CADASTRADO
        exe.proxima_consulta = None
        exe.incidente_id = caso.incidente_id
        exe.incidente_folder = caso.incidente_folder
        exe.incidente_cnj = caso.cnj_embargos or exe.incidente_cnj
        service.registrar_evento(
            db, SECAO_L1,
            f"Embargos {caso.cnj_embargos or ''} já cadastrados no Legal One ({caso.incidente_folder}) — "
            "monitoramento encerrado pelo Controle de Embargos.",
            execucao_id=exe.id, caso_id=caso.id,
        )


# ── L1 ───────────────────────────────────────────────────────────────
def _procedural_issue(l1, lawsuit_id: int) -> Optional[dict[str, Any]]:
    resp = l1._request_with_retry(
        "GET", f"{l1.base_url}/ProceduralIssues",
        params={
            "$filter": f"id eq {int(lawsuit_id)}",
            "$select": "id,folder,title,identifierNumber,relatedLitigationId,actionTypeId,responsibleOfficeId",
            "$top": 1,
        },
    )
    itens = (resp.json() or {}).get("value") or []
    return itens[0] if itens else None


def _pasta(l1, lawsuit_id: Optional[int]) -> dict[str, Any]:
    if not lawsuit_id:
        return {}
    try:
        return l1.get_lawsuit_by_id(int(lawsuit_id), params={"$select": "id,folder,identifierNumber"}) or {}
    except Exception:  # noqa: BLE001 — a pasta é informativa
        return {}


def _incidentes_da_pasta(l1, pasta: str) -> list[dict[str, Any]]:
    resp = l1._request_with_retry(
        "GET", f"{l1.base_url}/ProceduralIssues",
        params={
            "$filter": f"startswith(folder,'{pasta.replace(chr(39), '')}/')",
            "$select": "id,folder,title,identifierNumber,actionTypeId",
            "$top": 30,
        },
    )
    return (resp.json() or {}).get("value") or []


# ── fonte 1: monitor do tribunal ─────────────────────────────────────
def importar_tribunal(db: Session, resumo: dict[str, int]) -> None:
    exes = (
        db.query(EmbExecucao)
        .filter(EmbExecucao.estado.in_((ESTADO_ENCONTRADO, ESTADO_CONFIRMADO, ESTADO_JA_CADASTRADO, ESTADO_CONCLUIDO)))
        .all()
    )
    for exe in exes:
        incidente_d = cnj_digitos(exe.incidente_cnj)
        coberto = False
        for cand in exe.candidatos:
            if cand.nivel == NIVEL_PUBLICACAO:
                coberto = coberto or cand.cnj_digitos == incidente_d
                continue  # nasceu do próprio controle
            if cand.decisao == DECISAO_RECUSADO:
                caso = _caso_por_cnj(db, cand.cnj, criar=False)
                if (caso is not None and caso.estado == CASO_PENDENTE
                        and origens_do_caso(caso) == [ORIG_TRIBUNAL]):
                    caso.estado = CASO_DESCARTADO
                    caso.descartado_motivo = "Candidato recusado pelo operador no monitor do tribunal."
                    _evento(db, caso, "Descartado: o operador recusou o vínculo no monitor.")
                    resumo["descartados"] += 1
                continue
            confirmado = cand.decisao == DECISAO_CONFIRMADO or cand.id == exe.confirmado_candidato_id
            no_l1 = bool(cand.l1_folder) or (incidente_d and cand.cnj_digitos == incidente_d)
            if not (confirmado or no_l1 or (cand.decisao == DECISAO_PENDENTE and cand.nivel in NIVEIS_FORTES)):
                continue
            novo = _caso_por_cnj(db, cand.cnj, criar=False) is None
            caso = _caso_por_cnj(db, cand.cnj)
            _add_origem(db, caso, ORIG_TRIBUNAL)
            _detectado_desde(caso, exe.encontrado_em or cand.criado_em)
            caso.candidato_id = caso.candidato_id or cand.id
            caso.vinculo_confirmado = bool(caso.vinculo_confirmado or confirmado or no_l1)
            nomes = cand.djen_nomes_casados or cand.djen_embargantes or []
            caso.embargante = caso.embargante or (", ".join(nomes) if nomes else None)
            _vincular_execucao(db, caso, exe=exe, motivo="monitor do tribunal")
            if no_l1:
                coberto = True
                _marcar_cadastrado(db, caso, incidente_id=cand.l1_litigation_id or exe.incidente_id,
                                   folder=cand.l1_folder or exe.incidente_folder, via="monitor do tribunal")
            resumo["tribunal_novos" if novo else "tribunal_atualizados"] += 1
        # Incidente achado pela pasta antes de qualquer candidato.
        if exe.estado in (ESTADO_JA_CADASTRADO, ESTADO_CONCLUIDO) and incidente_d and not coberto:
            if not any(c.cnj_digitos == incidente_d for c in exe.candidatos):
                caso = _caso_por_cnj(db, exe.incidente_cnj)
                _add_origem(db, caso, ORIG_L1)
                caso.vinculo_confirmado = True
                _vincular_execucao(db, caso, exe=exe)
                _marcar_cadastrado(db, caso, incidente_id=exe.incidente_id, folder=exe.incidente_folder,
                                   via="incidente na pasta da execução")


# ── fonte 2: publicações sem pasta ───────────────────────────────────
def _cnjs(lista: Any) -> list[str]:
    saida = []
    for item in lista or []:
        c = item.get("cnj") if isinstance(item, dict) else item
        if cnj_digitos(c):
            saida.append(c)
    return saida


def importar_sem_pasta(db: Session, lidas: set[int], resumo: dict[str, int]) -> None:
    recs = (
        db.query(PublicationRecord)
        .filter(
            PublicationRecord.linked_lawsuit_id.is_(None),
            PublicationRecord.category == TIPO_EMBARGOS_SEM_PASTA,
            ~PublicationRecord.status.in_(_STATUS_FORA),
        )
        .order_by(PublicationRecord.id.asc())
        .all()
    )
    for rec in recs:
        if rec.id in lidas:
            continue
        raw = rec.raw_relationships if isinstance(rec.raw_relationships, dict) else {}
        ctx = raw.get("_sem_pasta") or {}
        ficha = ctx.get("ficha") or {}
        quem = " ".join(str(x or "") for x in (ctx.get("cliente"), ficha.get("cliente"), ficha.get("embargado")))
        if "BANCO DO BRASIL" not in nome_normalizado(quem):
            resumo["sem_pasta_outro_cliente"] += 1
            continue
        cnj = ficha.get("cnj") if cnj_digitos(ficha.get("cnj")) else next(iter(_cnjs(ctx.get("cnjs"))), None)
        if not cnj:
            resumo["sem_pasta_sem_cnj"] += 1
            continue
        novo = _caso_por_cnj(db, cnj, criar=False) is None
        caso = _caso_por_cnj(db, cnj)
        _add_origem(db, caso, ORIG_PUB_SEM_PASTA)
        tarefa = (ctx.get("agendamento_automatico") or {}).get("task_id")
        if tarefa and not caso.tarefa_l1_id:
            caso.tarefa_l1_id = int(tarefa)
        caso.embargante = caso.embargante or ficha.get("embargante")

        exec_cnj, lawsuit_id = None, None
        if cnj_digitos(ficha.get("cnj_execucao")) and cnj_digitos(ficha.get("cnj_execucao")) != caso.cnj_embargos_digitos:
            exec_cnj = ficha.get("cnj_execucao")
        for n in ctx.get("nossos") or []:
            c = n.get("cnj") if isinstance(n, dict) else n
            if cnj_digitos(c) and cnj_digitos(c) != caso.cnj_embargos_digitos:
                exec_cnj = exec_cnj or c
                if isinstance(n, dict) and cnj_digitos(c) == cnj_digitos(exec_cnj):
                    lawsuit_id = n.get("lawsuit_id")
                break
        exe = None
        if not exec_cnj:
            cand = db.query(EmbCandidato).filter(EmbCandidato.cnj_digitos == caso.cnj_embargos_digitos).first()
            exe = cand.execucao if cand else None
        _vincular_execucao(db, caso, exe=exe, cnj_execucao=exec_cnj, lawsuit_id=lawsuit_id,
                           motivo="CNJ da execução citado na publicação" if exec_cnj else "mesmo CNJ achado no tribunal")
        _registrar_publicacao(db, caso, rec, ORIG_PUB_SEM_PASTA, lidas)
        resumo["sem_pasta_novos" if novo else "sem_pasta_vinculados"] += 1


# ── fonte 3: publicações com pasta (BB Autor) ────────────────────────
def _capa(datajud, cnj: Optional[str]):
    if not cnj_digitos(cnj):
        return None
    try:
        return datajud.consultar_capa(cnj)
    except Exception as exc:  # noqa: BLE001 — DataJud é reforço, não bloqueia
        logger.info("Controle de Embargos: DataJud não respondeu para %s (%s).", cnj, exc)
        return None


def _cnj_embargos_no_texto(datajud, texto: Optional[str], cnj_pasta: Optional[str]) -> Optional[str]:
    proprio = cnj_digitos(cnj_pasta)
    # Embargos são distribuídos por dependência: mesmo segmento, tribunal e
    # comarca (J.TR.OOOO) da execução — nos 20 pares reais conferidos em
    # 11/09/2026, sempre. Sem isso, CNJ de jurisprudência citada na decisão
    # (caso real: TJAL 2019 citado numa execução do TJRN) virava "embargos".
    sufixo = proprio[13:] if proprio else None
    vistos: list[str] = []
    for c in _CNJ_RE.findall(texto or ""):
        d = cnj_digitos(c)
        if d != proprio and (sufixo is None or d[13:] == sufixo) and c not in vistos:
            vistos.append(c)
    for c in vistos[:3]:
        capa = _capa(datajud, c)
        if capa is not None and capa.classe_codigo == CLASSE_EMBARGOS_EXECUCAO:
            return c
    return None


def importar_na_pasta(db: Session, l1, datajud, lidas: set[int], resumo: dict[str, int]) -> None:
    recs = (
        db.query(PublicationRecord)
        .filter(
            PublicationRecord.linked_lawsuit_id.isnot(None),
            PublicationRecord.linked_office_id == OFFICE_BB_AUTOR,
            PublicationRecord.subcategory.ilike(SUBCATEGORIA_EMBARGOS),
            ~PublicationRecord.status.in_(_STATUS_FORA),
        )
        .order_by(PublicationRecord.id.asc())
        .all()
    )
    for rec in recs:
        if rec.id in lidas:
            continue
        try:
            inc = _procedural_issue(l1, rec.linked_lawsuit_id)
        except Exception as exc:  # noqa: BLE001 — volta no próximo ciclo
            logger.warning("Controle de Embargos: L1 falhou para a publicação %s: %s", rec.id, exc)
            resumo["erros_l1"] += 1
            continue

        if inc:
            cnj = inc.get("identifierNumber") or rec.linked_lawsuit_cnj
            novo = _caso_por_cnj(db, cnj, criar=False) is None
            caso = _caso_por_cnj(db, cnj) if cnj_digitos(cnj) else _novo_caso(db)
            _add_origem(db, caso, ORIG_PUB_INCIDENTE)
            base = _pasta(l1, inc.get("relatedLitigationId"))
            _vincular_execucao(db, caso, cnj_execucao=base.get("identifierNumber"), pasta=base.get("folder"),
                               lawsuit_id=inc.get("relatedLitigationId"), motivo="pasta mãe do incidente")
            _marcar_cadastrado(db, caso, incidente_id=inc.get("id"), folder=inc.get("folder"),
                               via="publicação já na pasta dos embargos")
            _registrar_publicacao(db, caso, rec, ORIG_PUB_INCIDENTE, lidas)
            resumo["na_pasta_incidente" if novo else "na_pasta_incidente_vinculadas"] += 1
            continue

        capa = _capa(datajud, rec.linked_lawsuit_cnj)
        if capa is not None and capa.classe_nome and _MONITORIA.search(capa.classe_nome):
            _registrar_publicacao(db, None, rec, PUB_IGNORADA_MONITORIA, lidas)
            resumo["monitoria_ignorada"] += 1
            continue
        if capa is None and _MONITORIA.search(rec.description or "") and not _EMBARGOS_EXECUCAO_TXT.search(rec.description or ""):
            # Sem DataJud, só o texto: fica de fora nesta rodada (não marca lida).
            resumo["monitoria_pelo_texto"] += 1
            continue

        base = _pasta(l1, rec.linked_lawsuit_id)
        if capa is not None and capa.classe_codigo == CLASSE_EMBARGOS_EXECUCAO:
            # A pasta vinculada É a dos embargos (cadastrada como pasta própria).
            caso = _caso_por_cnj(db, rec.linked_lawsuit_cnj)
            _add_origem(db, caso, ORIG_PUB_INCIDENTE)
            _marcar_cadastrado(db, caso, incidente_id=rec.linked_lawsuit_id, folder=base.get("folder"),
                               via="publicação na pasta própria dos embargos")
            _registrar_publicacao(db, caso, rec, ORIG_PUB_INCIDENTE, lidas)
            resumo["na_pasta_propria"] += 1
            continue

        if not _EMBARGOS_EXECUCAO_TXT.search(rec.description or ""):
            # A subcategoria mistura embargos de declaração e afins.
            _registrar_publicacao(db, None, rec, PUB_IGNORADA_NAO_EMBARGOS, lidas)
            resumo["nao_e_embargos_execucao"] += 1
            continue

        emb = _cnj_embargos_no_texto(datajud, rec.description, rec.linked_lawsuit_cnj)
        if not emb and _PROPRIOS_AUTOS.search(rec.description or ""):
            # Embargos opostos dentro da própria execução: não há processo
            # apartado, logo não há pasta incidental a cadastrar.
            _registrar_publicacao(db, None, rec, PUB_IGNORADA_PROPRIOS_AUTOS, lidas)
            resumo["proprios_autos_ignorada"] += 1
            continue

        if emb:
            # Processo de embargos APARTADO confirmado no DataJud e a publicação
            # chegou na execução: "embargos identificados" — circunstância da
            # comunicação do judiciário, não erro de cadastro (operador, 14/09/2026).
            caso = _caso_por_cnj(db, emb)
            if not caso.falha_cadastro:
                caso.falha_cadastro = True
                _evento(db, caso, "Embargos identificados: o processo dos embargos foi confirmado no tribunal e a "
                                  "intimação chegou na pasta da execução — falta a pasta dos embargos.", nivel=EVT_AVISO)
            resumo["falha_cadastro"] += 1
        else:
            caso = (
                db.query(EmbCaso)
                .filter(EmbCaso.lawsuit_id_execucao == rec.linked_lawsuit_id,
                        EmbCaso.cnj_embargos_digitos.is_(None), EmbCaso.estado == CASO_PENDENTE)
                .first()
            )
            if caso is None:
                caso = _novo_caso(db)
                _evento(db, caso, "A verificar: a publicação na pasta da execução fala em embargos à execução, "
                                  "mas o processo apartado não foi identificado.", nivel=EVT_AVISO)
            resumo["a_verificar"] += 1
        _add_origem(db, caso, ORIG_PUB_NA_PASTA)
        _vincular_execucao(db, caso, cnj_execucao=rec.linked_lawsuit_cnj, pasta=base.get("folder"),
                           lawsuit_id=rec.linked_lawsuit_id, motivo="publicação na pasta da execução")
        _registrar_publicacao(db, caso, rec, ORIG_PUB_NA_PASTA, lidas)


# ── casamento pela vara + embargante ─────────────────────────────────
def vincular_por_vara(db: Session, datajud, resumo: dict[str, int], limite: int = 20) -> None:
    corte = _agora() - TENTAR_VINCULO_A_CADA
    casos = (
        db.query(EmbCaso)
        .filter(EmbCaso.estado == CASO_PENDENTE, EmbCaso.execucao_id.is_(None),
                EmbCaso.cnj_embargos_digitos.isnot(None), EmbCaso.embargante.isnot(None),
                or_(EmbCaso.vinculo_tentado_em.is_(None), EmbCaso.vinculo_tentado_em < corte))
        .limit(limite)
        .all()
    )
    for caso in casos:
        caso.vinculo_tentado_em = _agora()
        capa = _capa(datajud, caso.cnj_embargos)
        if capa is None or capa.orgao_codigo is None:
            continue
        nomes = [n for n in re.split(r"\s*,\s*|\s+e\s+", caso.embargante or "") if n.strip()]
        hits = [
            exe for exe in db.query(EmbExecucao).filter(EmbExecucao.orgao_codigo == capa.orgao_codigo)
            if any(nomes_batem(n, p.nome) for p in exe.partes if p.demandada for n in nomes)
        ]
        if len(hits) == 1:
            _vincular_execucao(db, caso, exe=hits[0], motivo="mesma vara e embargante é parte demandada")
            resumo["vinculados_pela_vara"] += 1


# ── a publicação chegou antes do monitor: encerra sem novo aviso ─────
def parar_monitor_por_publicacao(db: Session, caso: EmbCaso) -> bool:
    exe = caso.execucao
    if exe is None or caso.estado != CASO_PENDENTE or exe.estado not in ESTADOS_VIGILANCIA:
        return False
    if not any(getattr(caso, ORIGENS_COLUNA[o]) for o in ORIGENS_PUBLICACAO):
        return False
    d = caso.cnj_embargos_digitos
    if d and not any(c.cnj_digitos == d for c in exe.candidatos):
        exe.candidatos.append(EmbCandidato(
            cnj=caso.cnj_embargos, cnj_digitos=d, nivel=NIVEL_PUBLICACAO, decisao=DECISAO_PENDENTE,
            distribuicao_dependencia=False, peticao_mesmo_dia=False,
            djen_embargantes=[caso.embargante] if caso.embargante else None,
        ))
    agora = _agora()
    exe.estado = ESTADO_ENCONTRADO
    exe.encontrado_em = agora
    exe.aviso_enviado_em = agora  # a publicação já chegou à equipe
    exe.proxima_consulta = None
    service.registrar_evento(
        db, SECAO_CONTROLE,
        f"Embargos {caso.cnj_embargos or ''} chegaram por publicação"
        + (f" (tarefa {caso.tarefa_l1_id} já criada)" if caso.tarefa_l1_id else "")
        + " — monitoramento do tribunal encerrado, sem novo aviso.",
        execucao_id=exe.id, caso_id=caso.id,
    )
    return True


def caso_de_publicacao_para(db: Session, cnjs_digitos: list[str]) -> Optional[EmbCaso]:
    """Usado pelo monitor: esses embargos já chegaram por Publicações?"""
    alvos = [d for d in cnjs_digitos if d]
    if not alvos:
        return None
    for caso in db.query(EmbCaso).filter(EmbCaso.cnj_embargos_digitos.in_(alvos)):
        if any(getattr(caso, ORIGENS_COLUNA[o]) for o in ORIGENS_PUBLICACAO):
            return caso
    return None


# ── conferência no L1: a pasta incidental já existe? ─────────────────
def verificar_caso(db: Session, l1, caso: EmbCaso) -> bool:
    caso.verificado_l1_em = _agora()
    caso.verificacoes_l1 = (caso.verificacoes_l1 or 0) + 1
    if not caso.pasta_execucao and caso.cnj_execucao:
        # CNJ da execução citado na publicação, execução fora do monitor: a
        # pasta vem do L1 (e é nela que se procura o incidente logo abaixo).
        base = l1.search_lawsuit_by_cnj(caso.cnj_execucao)
        if base and base.get("id"):
            pasta = _pasta(l1, base["id"]).get("folder")
            if pasta:
                _vincular_execucao(db, caso, pasta=pasta, lawsuit_id=int(base["id"]),
                                   motivo="pasta da execução achada no Legal One")
    if caso.cnj_embargos:
        achado = l1.search_lawsuit_by_cnj(caso.cnj_embargos)
        if achado and achado.get("id"):
            folder = _pasta(l1, achado["id"]).get("folder")
            _marcar_cadastrado(db, caso, incidente_id=int(achado["id"]), folder=folder,
                               via="CNJ dos embargos encontrado no Legal One")
            return True
    if caso.pasta_execucao and not caso.pasta_execucao.startswith("CNJ "):
        from app.services.embargos_execucao.monitor import incidentes_de_embargos

        embargos = incidentes_de_embargos(_incidentes_da_pasta(l1, caso.pasta_execucao),
                                          [caso.cnj_embargos_digitos] if caso.cnj_embargos_digitos else [])
        if caso.cnj_embargos_digitos:
            # Execução pode ter mais de um embargo (um por executado): só vale o do mesmo número.
            embargos = [i for i in embargos if cnj_digitos(i.get("identifierNumber")) in (None, caso.cnj_embargos_digitos)]
        if embargos:
            inc = embargos[0]
            _marcar_cadastrado(db, caso, incidente_id=inc.get("id"), folder=inc.get("folder"),
                               via="incidente de embargos na pasta da execução")
            return True
    return False


def verificar_pendentes(db: Session, l1, resumo: dict[str, int], limite: int = 40) -> None:
    corte = _agora() - VERIFICAR_L1_A_CADA
    casos = (
        db.query(EmbCaso)
        .filter(EmbCaso.estado == CASO_PENDENTE,
                or_(EmbCaso.verificado_l1_em.is_(None), EmbCaso.verificado_l1_em < corte))
        .order_by(EmbCaso.verificado_l1_em.asc().nullsfirst(), EmbCaso.id.asc())
        .limit(limite)
        .all()
    )
    for caso in casos:
        try:
            if verificar_caso(db, l1, caso):
                resumo["cadastrados_no_l1"] += 1
        except Exception as exc:  # noqa: BLE001 — um caso não derruba os outros
            logger.warning("Controle de Embargos: conferência do caso %s no L1 falhou: %s", caso.id, exc)
            resumo["erros_l1"] += 1
        db.commit()


# ── orquestração ─────────────────────────────────────────────────────
def sincronizar(db: Session, l1=None, datajud=datajud_embargos, *, verificar: bool = True) -> dict[str, int]:
    resumo: dict[str, int] = defaultdict(int)
    lidas = _ja_lidas(db)
    importar_tribunal(db, resumo)
    db.commit()
    importar_sem_pasta(db, lidas, resumo)
    db.commit()
    if l1 is not None:
        importar_na_pasta(db, l1, datajud, lidas, resumo)
        db.commit()
    vincular_por_vara(db, datajud, resumo)
    db.commit()
    for caso in db.query(EmbCaso).filter(EmbCaso.estado == CASO_PENDENTE, EmbCaso.execucao_id.isnot(None)):
        if parar_monitor_por_publicacao(db, caso):
            resumo["monitores_encerrados"] += 1
    db.commit()
    if l1 is not None and verificar:
        verificar_pendentes(db, l1, resumo)
    return dict(resumo)


def status() -> dict[str, Any]:
    bruto = get_setting(SETTING_STATUS, "") or ""
    try:
        st = json.loads(bruto) if bruto else {}
    except ValueError:
        st = {}
    if st.get("running") and st.get("iniciado_em"):
        try:
            if (_agora() - datetime.fromisoformat(st["iniciado_em"])).total_seconds() > 30 * 60:
                st["running"] = False
        except ValueError:
            pass
    return st


def gravar_status(**campos: Any) -> None:
    atual = status()
    atual.update(campos)
    set_setting(SETTING_STATUS, json.dumps(atual, ensure_ascii=False, default=str))


# ── leitura ──────────────────────────────────────────────────────────
def _iso(v) -> Optional[str]:
    return v.isoformat() if v else None


def _dto_caso(caso: EmbCaso, n_pub: int = 0, ultima_pub: Optional[str] = None) -> dict[str, Any]:
    exe = caso.execucao
    return {
        "tipo": "caso",
        "id": caso.id,
        "cnj_embargos": caso.cnj_embargos,
        "pasta_execucao": caso.pasta_execucao,
        "cnj_execucao": caso.cnj_execucao,
        "execucao_id": caso.execucao_id,
        "execucao_estado": exe.estado if exe else None,
        "estado": caso.estado,
        "falha_cadastro": bool(caso.falha_cadastro),
        "vinculo_confirmado": bool(caso.vinculo_confirmado),
        "origens": origens_do_caso(caso),
        "primeira_origem": caso.primeira_origem,
        "embargante": caso.embargante,
        "tarefa_l1_id": caso.tarefa_l1_id,
        "detectado_em": _iso(caso.detectado_em),
        "cadastrado_em": _iso(caso.cadastrado_em),
        "incidente_id": caso.incidente_id,
        "incidente_folder": caso.incidente_folder,
        "verificado_l1_em": _iso(caso.verificado_l1_em),
        "descartado_motivo": caso.descartado_motivo,
        "publicacoes": n_pub,
        "ultima_publicacao": ultima_pub,
    }


def _dto_vigilancia(exe: EmbExecucao) -> dict[str, Any]:
    return {
        "tipo": "execucao",
        "id": exe.id,
        "execucao_id": exe.id,
        "pasta_execucao": exe.pasta,
        "cnj_execucao": exe.cnj,
        "estado": exe.estado,
        "data_ajuizamento": _iso(exe.data_ajuizamento),
        "proxima_consulta": _iso(exe.proxima_consulta),
        "consultas_feitas": exe.consultas_feitas or 0,
        "responsavel_nome": exe.responsavel_nome,
    }


def listar(
    db: Session,
    *,
    etapa: str = ETAPA_PENDENTE,
    origem: Optional[str] = None,
    so_falha: bool = False,
    sem_execucao: bool = False,
    a_verificar: bool = False,
    busca: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    pend = db.query(EmbCaso).filter(EmbCaso.estado == CASO_PENDENTE)
    kpis = {
        "vigilancia": db.query(func.count(EmbExecucao.id)).filter(EmbExecucao.estado.in_(ESTADOS_VIGILANCIA)).scalar() or 0,
        "pendente": pend.count(),
        "pendente_falha_cadastro": pend.filter(EmbCaso.falha_cadastro.is_(True)).count(),
        # Sem execução = não se sabe nem a pasta; execução fora do monitor (anterior
        # ao corte) mas com pasta conhecida não entra aqui.
        "pendente_sem_execucao": pend.filter(EmbCaso.execucao_id.is_(None), EmbCaso.pasta_execucao.is_(None)).count(),
        "pendente_a_verificar": pend.filter(EmbCaso.origem_pub_na_pasta.is_(True),
                                            EmbCaso.cnj_embargos_digitos.is_(None)).count(),
        "publicacoes_ignoradas": {
            o: int(n) for o, n in db.query(EmbCasoPublicacao.origem, func.count(EmbCasoPublicacao.id))
            .filter(EmbCasoPublicacao.caso_id.is_(None)).group_by(EmbCasoPublicacao.origem)
        },
        "cadastrado": db.query(func.count(EmbCaso.id)).filter(EmbCaso.estado == CASO_CADASTRADO).scalar() or 0,
        "descartado": db.query(func.count(EmbCaso.id)).filter(EmbCaso.estado == CASO_DESCARTADO).scalar() or 0,
        "pendente_por_origem": {
            o: pend.filter(getattr(EmbCaso, col).is_(True)).count() for o, col in ORIGENS_COLUNA.items()
        },
    }
    termo = (busca or "").strip()
    digitos = re.sub(r"\D", "", termo)

    if etapa == ETAPA_VIGILANCIA:
        q = db.query(EmbExecucao).filter(EmbExecucao.estado.in_(ESTADOS_VIGILANCIA))
        if termo:
            filtros = [EmbExecucao.pasta.ilike(f"%{termo}%"), EmbExecucao.cnj.ilike(f"%{termo}%")]
            if len(digitos) >= 7:
                filtros.append(EmbExecucao.cnj_digitos.like(f"%{digitos}%"))
            q = q.filter(or_(*filtros))
        total = q.count()
        itens = q.order_by(EmbExecucao.proxima_consulta.asc().nullslast(), EmbExecucao.id.asc()).offset(offset).limit(limit).all()
        return {"etapa": etapa, "total": total, "kpis": kpis, "status": status(),
                "items": [_dto_vigilancia(e) for e in itens]}

    q = db.query(EmbCaso).filter(EmbCaso.estado == _ETAPA_ESTADO.get(etapa, CASO_PENDENTE))
    if origem in ORIGENS_COLUNA:
        q = q.filter(getattr(EmbCaso, ORIGENS_COLUNA[origem]).is_(True))
    if so_falha:
        q = q.filter(EmbCaso.falha_cadastro.is_(True))
    if sem_execucao:
        q = q.filter(EmbCaso.execucao_id.is_(None), EmbCaso.pasta_execucao.is_(None))
    if a_verificar:
        q = q.filter(EmbCaso.origem_pub_na_pasta.is_(True), EmbCaso.cnj_embargos_digitos.is_(None))
    if termo:
        filtros = [
            EmbCaso.cnj_embargos.ilike(f"%{termo}%"), EmbCaso.pasta_execucao.ilike(f"%{termo}%"),
            EmbCaso.cnj_execucao.ilike(f"%{termo}%"), EmbCaso.embargante.ilike(f"%{termo}%"),
            EmbCaso.incidente_folder.ilike(f"%{termo}%"),
        ]
        if len(digitos) >= 7:
            filtros.append(EmbCaso.cnj_embargos_digitos.like(f"%{digitos}%"))
        q = q.filter(or_(*filtros))
    total = q.count()
    ordem = EmbCaso.cadastrado_em.desc().nullslast() if etapa == ETAPA_CADASTRADO else EmbCaso.detectado_em.asc().nullslast()
    itens = q.order_by(ordem, EmbCaso.id.asc()).offset(offset).limit(limit).all()
    ids = [c.id for c in itens]
    pubs: dict[int, tuple[int, Optional[str]]] = {}
    if ids:
        for cid, n, ultima in (
            db.query(EmbCasoPublicacao.caso_id, func.count(EmbCasoPublicacao.id), func.max(EmbCasoPublicacao.data_publicacao))
            .filter(EmbCasoPublicacao.caso_id.in_(ids)).group_by(EmbCasoPublicacao.caso_id)
        ):
            pubs[cid] = (int(n), ultima)
    return {
        "etapa": etapa, "total": total, "kpis": kpis, "status": status(),
        "items": [_dto_caso(c, *pubs.get(c.id, (0, None))) for c in itens],
    }


def detalhe(db: Session, caso_id: int) -> Optional[dict[str, Any]]:
    caso = db.get(EmbCaso, caso_id)
    if caso is None:
        return None
    cand = db.get(EmbCandidato, caso.candidato_id) if caso.candidato_id else None
    eventos = (
        db.query(EmbEvento).filter(EmbEvento.caso_id == caso.id)
        .order_by(EmbEvento.criado_em.desc(), EmbEvento.id.desc()).limit(200).all()
    )
    exe = caso.execucao
    return {
        "caso": _dto_caso(caso, len(caso.publicacoes),
                          max((p.data_publicacao or "" for p in caso.publicacoes), default=None) or None),
        "execucao": exe and {
            "id": exe.id, "pasta": exe.pasta, "cnj": exe.cnj, "estado": exe.estado,
            "data_ajuizamento": _iso(exe.data_ajuizamento), "orgao_nome": exe.orgao_nome,
            "responsavel_nome": exe.responsavel_nome,
        },
        "publicacoes": [
            {"id": p.id, "publicacao_id": p.publicacao_id, "origem": p.origem, "data_publicacao": p.data_publicacao,
             "status_publicacao": p.status_publicacao, "linked_lawsuit_id": p.linked_lawsuit_id, "trecho": p.trecho}
            for p in caso.publicacoes
        ],
        "candidato": cand and {
            "id": cand.id, "cnj": cand.cnj, "nivel": cand.nivel, "decisao": cand.decisao,
            "djen_status": cand.djen_status, "djen_trecho": cand.djen_trecho, "djen_data": cand.djen_data,
            "orgao_nome": cand.orgao_nome, "data_ajuizamento": _iso(cand.data_ajuizamento),
        },
        "eventos": [
            {"id": e.id, "secao": e.secao, "nivel": e.nivel, "mensagem": e.mensagem, "criado_em": _iso(e.criado_em)}
            for e in eventos
        ],
    }


# ── ações do operador ────────────────────────────────────────────────
def _caso(db: Session, caso_id: int) -> EmbCaso:
    caso = db.get(EmbCaso, caso_id)
    if caso is None:
        raise LookupError("Caso não encontrado.")
    return caso


def verificar_agora(db: Session, caso_id: int, l1, *, user_id: Optional[int] = None) -> bool:
    caso = _caso(db, caso_id)
    achou = verificar_caso(db, l1, caso)
    if not achou:
        _evento(db, caso, "Conferido no Legal One agora: a pasta dos embargos ainda não existe.",
                secao=SECAO_OPERADOR, user_id=user_id)
    db.commit()
    return achou


def descartar(db: Session, caso_id: int, *, motivo: Optional[str], user_id: Optional[int] = None) -> EmbCaso:
    caso = _caso(db, caso_id)
    if not (motivo or "").strip():
        raise ValueError("Informe o motivo do descarte.")
    caso.estado = CASO_DESCARTADO
    caso.descartado_motivo = motivo.strip()
    caso.decidido_por_user_id = user_id
    _evento(db, caso, f"Descartado pelo operador: {caso.descartado_motivo}", secao=SECAO_OPERADOR, user_id=user_id)
    db.commit()
    return caso


def reabrir(db: Session, caso_id: int, *, user_id: Optional[int] = None) -> EmbCaso:
    caso = _caso(db, caso_id)
    caso.estado = CASO_PENDENTE
    caso.descartado_motivo = None
    caso.verificado_l1_em = None
    _evento(db, caso, "Caso reaberto — volta pra conferência no Legal One.", secao=SECAO_OPERADOR, user_id=user_id)
    db.commit()
    return caso
