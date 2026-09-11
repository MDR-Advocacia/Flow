"""Tarefas do incidente de embargos: templates configuráveis + disparo pelo Flow.

Decisão do operador (11/09/2026): o cadastro do incidente no Legal One e no
portal do BB é MANUAL por enquanto. Depois de cadastrar, o controlador pede ao
Flow para localizar o incidente no L1 (ProceduralIssues da pasta "Proc - X/00N")
e disparar as tarefas definidas na tela de templates — mutável, sem deploy.

A tarefa vai vinculada ao INCIDENTE, não à execução: é assim que as tarefas
feitas à mão já nascem (ex.: tarefa 476538 "Impugnação aos Embargos" →
relationship Litigation 98774 = "Proc - 0068694/001").
"""
from __future__ import annotations

import logging
import string
from datetime import date
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.embargos_execucao import (
    DISPARO_CRIADA,
    DISPARO_FALHA,
    ESTADO_CONCLUIDO,
    ESTADO_CONFIRMADO,
    ESTADO_JA_CADASTRADO,
    EVT_AVISO,
    EVT_ERRO,
    EVT_INFO,
    RESP_ADVOGADO_CARD,
    RESP_FIXO,
    SECAO_L1,
    SECAO_TAREFAS,
    EmbCandidato,
    EmbExecucao,
    EmbTarefaDisparo,
    EmbTarefaTemplate,
)
from app.models.legal_one import LegalOneTaskSubType, LegalOneUser
from app.services.embargos_execucao import service
from app.services.embargos_execucao.normaliza import cnj_digitos, formatar_cnj, nome_normalizado
from app.services.prazos_iniciais.prazo_calculator import add_business_days

logger = logging.getLogger(__name__)

PRIORIDADES = ("Low", "Normal", "High")
ESTADOS_QUE_DISPARAM = (ESTADO_CONFIRMADO, ESTADO_JA_CADASTRADO, ESTADO_CONCLUIDO)
# Status do L1 que contam como tarefa ainda aberta (0 Pendente, 4 Iniciado, 5 Reagendado).
_STATUS_ABERTOS = [0, 4, 5]

PLACEHOLDERS = {
    "pasta_execucao": "Pasta da execução (Proc - 0068694)",
    "cnj_execucao": "CNJ da execução",
    "npj": "NPJ da execução",
    "pasta_incidente": "Pasta do incidente (Proc - 0068694/001)",
    "cnj_embargos": "CNJ dos embargos",
    "embargante": "Nome do(s) embargante(s) lido(s) no DJEN",
    "data_ajuizamento_embargos": "Data de ajuizamento dos embargos",
    "data_ajuizamento_execucao": "Data de ajuizamento da execução",
    "responsavel": "Advogado responsável da execução (relatório do L1)",
}


class _Faltante(dict):
    def __missing__(self, chave: str) -> str:
        return "{" + chave + "}"


def renderizar(modelo: Optional[str], contexto: dict[str, Any]) -> str:
    if not modelo:
        return ""
    try:
        return string.Formatter().vformat(modelo, (), _Faltante(contexto))
    except (ValueError, IndexError):
        # Chave mal formada ("{", "{0}"): devolve o texto como está, sem quebrar o disparo.
        return modelo


# ── Templates ────────────────────────────────────────────────────────
def _dto_template(t: EmbTarefaTemplate) -> dict[str, Any]:
    return {
        "id": t.id,
        "nome": t.nome,
        "ativo": bool(t.ativo),
        "ordem": t.ordem or 0,
        "tipo_id": t.tipo_id,
        "subtipo_id": t.subtipo_id,
        "subtipo_nome": t.subtipo_nome,
        "responsavel_modo": t.responsavel_modo,
        "responsavel_contact_id": t.responsavel_contact_id,
        "responsavel_nome": t.responsavel_nome,
        "prazo_dias_uteis": t.prazo_dias_uteis,
        "prioridade": t.prioridade,
        "descricao_template": t.descricao_template,
        "observacoes_template": t.observacoes_template,
        "atualizado_em": t.atualizado_em.isoformat() if t.atualizado_em else None,
    }


def listar_templates(db: Session) -> dict[str, Any]:
    # Lista curta e estável (as tarefas de um tipo de incidente): sem paginação
    # por decisão consciente — cabe numa tela.
    itens = db.query(EmbTarefaTemplate).order_by(EmbTarefaTemplate.ordem.asc(), EmbTarefaTemplate.id.asc()).all()
    return {
        "total": len(itens),
        "items": [_dto_template(t) for t in itens],
        "placeholders": PLACEHOLDERS,
        "prioridades": list(PRIORIDADES),
        "responsavel_modos": {
            RESP_FIXO: "Pessoa fixa",
            RESP_ADVOGADO_CARD: "Advogado responsável da execução",
        },
    }


def salvar_template(
    db: Session,
    dados: dict[str, Any],
    *,
    template_id: Optional[int] = None,
    user_id: Optional[int] = None,
) -> dict[str, Any]:
    nome = (dados.get("nome") or "").strip()
    if not nome:
        raise ValueError("Dê um nome ao template.")
    subtipo = (
        db.query(LegalOneTaskSubType)
        .filter(LegalOneTaskSubType.external_id == int(dados.get("subtipo_id") or 0))
        .first()
    )
    if subtipo is None:
        raise ValueError("Subtipo de tarefa não encontrado no catálogo do Legal One.")
    modo = dados.get("responsavel_modo") or RESP_FIXO
    if modo not in (RESP_FIXO, RESP_ADVOGADO_CARD):
        raise ValueError("Modo de responsável inválido.")
    contato_id, contato_nome = None, None
    if modo == RESP_FIXO:
        usuario = (
            db.query(LegalOneUser)
            .filter(LegalOneUser.external_id == int(dados.get("responsavel_contact_id") or 0))
            .first()
        )
        if usuario is None:
            raise ValueError("Escolha a pessoa responsável pela tarefa.")
        contato_id, contato_nome = usuario.external_id, usuario.name
    prazo = int(dados.get("prazo_dias_uteis") if dados.get("prazo_dias_uteis") is not None else 5)
    if not 0 <= prazo <= 60:
        raise ValueError("Prazo deve ficar entre 0 e 60 dias úteis.")
    prioridade = dados.get("prioridade") or "Normal"
    if prioridade not in PRIORIDADES:
        raise ValueError("Prioridade inválida.")
    descricao = (dados.get("descricao_template") or "").strip()
    if not descricao:
        raise ValueError("A descrição da tarefa é obrigatória.")

    if template_id is None:
        tpl = EmbTarefaTemplate()
        db.add(tpl)
    else:
        tpl = db.get(EmbTarefaTemplate, template_id)
        if tpl is None:
            raise LookupError("Template não encontrado.")
    tpl.nome = nome
    tpl.ativo = bool(dados.get("ativo", True))
    tpl.ordem = int(dados.get("ordem") or 0)
    tpl.tipo_id = subtipo.parent_type_external_id
    tpl.subtipo_id = subtipo.external_id
    tpl.subtipo_nome = subtipo.name
    tpl.responsavel_modo = modo
    tpl.responsavel_contact_id = contato_id
    tpl.responsavel_nome = contato_nome
    tpl.prazo_dias_uteis = prazo
    tpl.prioridade = prioridade
    tpl.descricao_template = descricao
    tpl.observacoes_template = (dados.get("observacoes_template") or "").strip() or None
    tpl.atualizado_por_user_id = user_id
    db.commit()
    return _dto_template(tpl)


def excluir_template(db: Session, template_id: int) -> None:
    tpl = db.get(EmbTarefaTemplate, template_id)
    if tpl is None:
        raise LookupError("Template não encontrado.")
    # O histórico de disparos guarda cópia do que foi enviado — excluir não apaga rastro.
    db.delete(tpl)
    db.commit()


# ── Incidente no L1 ──────────────────────────────────────────────────
def _candidato_confirmado(db: Session, exe: EmbExecucao) -> Optional[EmbCandidato]:
    if exe.confirmado_candidato_id:
        return db.get(EmbCandidato, exe.confirmado_candidato_id)
    return None


def localizar_incidente(db: Session, exe: EmbExecucao, l1_client) -> Optional[dict[str, Any]]:
    """Procura o incidente de embargos cadastrado na pasta e guarda no card."""
    if not exe.pasta or exe.pasta.startswith("CNJ "):
        raise ValueError("A execução não tem pasta do Legal One — não há onde procurar o incidente.")
    resp = l1_client._request_with_retry(
        "GET", f"{l1_client.base_url}/ProceduralIssues",
        params={
            "$filter": f"startswith(folder,'{exe.pasta.replace(chr(39), '')}/')",
            "$select": "id,folder,title,identifierNumber,actionTypeId,responsibleOfficeId,distributionDate",
            "$top": 30,
        },
    )
    itens = (resp.json() or {}).get("value") or []
    from app.services.embargos_execucao.monitor import incidentes_de_embargos

    embargos = incidentes_de_embargos(itens, [c.cnj_digitos for c in exe.candidatos])
    if not embargos:
        if exe.incidente_id:
            # Embargos cadastrados fora da pasta da execução (achados pelo CNJ no monitor).
            return {"id": exe.incidente_id, "folder": exe.incidente_folder,
                    "identifierNumber": exe.incidente_cnj, "responsibleOfficeId": exe.incidente_office_id}
        return None
    confirmado = _candidato_confirmado(db, exe)
    alvo = None
    if confirmado:
        alvo = next((i for i in embargos if cnj_digitos(i.get("identifierNumber")) == confirmado.cnj_digitos), None)
    alvo = alvo or sorted(embargos, key=lambda i: i.get("folder") or "")[-1]
    mudou = exe.incidente_id != alvo.get("id")
    exe.incidente_id = alvo.get("id")
    exe.incidente_folder = alvo.get("folder")
    exe.incidente_cnj = formatar_cnj(alvo.get("identifierNumber")) or alvo.get("identifierNumber")
    exe.incidente_office_id = alvo.get("responsibleOfficeId")
    if mudou:
        service.registrar_evento(
            db, SECAO_L1,
            f"Incidente localizado no Legal One: {exe.incidente_folder} ({exe.incidente_cnj or 'sem CNJ'}).",
            execucao_id=exe.id, dados={"incidente": alvo},
        )
    db.commit()
    return alvo


# ── Prévia e disparo ─────────────────────────────────────────────────
def _contexto(exe: EmbExecucao, cand: Optional[EmbCandidato]) -> dict[str, Any]:
    return {
        "pasta_execucao": exe.pasta or "",
        "cnj_execucao": exe.cnj or "",
        "npj": exe.npj or "",
        "pasta_incidente": exe.incidente_folder or "",
        "cnj_embargos": exe.incidente_cnj or (cand.cnj if cand else ""),
        "embargante": ", ".join((cand.djen_nomes_casados or cand.djen_embargantes or []) if cand else []),
        "data_ajuizamento_embargos": cand.data_ajuizamento.strftime("%d/%m/%Y") if cand and cand.data_ajuizamento else "",
        "data_ajuizamento_execucao": exe.data_ajuizamento.strftime("%d/%m/%Y") if exe.data_ajuizamento else "",
        "responsavel": exe.responsavel_nome or "",
    }


def _responsavel(db: Session, tpl: EmbTarefaTemplate, exe: EmbExecucao) -> tuple[Optional[int], Optional[str], Optional[str]]:
    if tpl.responsavel_modo == RESP_FIXO:
        if not tpl.responsavel_contact_id:
            return None, None, "Template sem pessoa responsável."
        return tpl.responsavel_contact_id, tpl.responsavel_nome, None
    alvo = nome_normalizado(exe.responsavel_nome)
    if not alvo:
        return None, None, "A execução não tem advogado responsável (relatório do L1)."
    for u in db.query(LegalOneUser).filter(LegalOneUser.is_active.is_(True)).all():
        if nome_normalizado(u.name) == alvo:
            return u.external_id, u.name, None
    return None, None, f"'{exe.responsavel_nome}' não está no cadastro de usuários do Flow."


def _tarefa_aberta_no_incidente(l1_client, incidente_id: int, subtipo_id: int) -> Optional[int]:
    try:
        tarefas = l1_client.find_tasks_for_lawsuit(
            incidente_id, subtype_id=subtipo_id, status_ids=_STATUS_ABERTOS, top=30,
        )
    except Exception:  # noqa: BLE001 — conferência é proteção, não bloqueio
        logger.warning("Embargos: não deu pra conferir tarefas abertas no incidente %s.", incidente_id)
        return None
    for t in tarefas or []:
        if t.get("id"):
            return int(t["id"])
    return None


def _data_hora_prazo(prazo: date) -> str:
    return f"{prazo.isoformat()}T18:00:00-03:00"


def previa(db: Session, execucao_id: int, l1_client, *, hoje: Optional[date] = None,
           localizar: bool = True) -> dict[str, Any]:
    hoje = hoje or service.hoje_brt()
    exe = db.get(EmbExecucao, execucao_id)
    if exe is None:
        raise LookupError("Execução não encontrada.")
    if exe.estado not in ESTADOS_QUE_DISPARAM:
        raise ValueError("Confirme o vínculo dos embargos antes de disparar as tarefas.")
    incidente = None
    if localizar and l1_client is not None:
        incidente = localizar_incidente(db, exe, l1_client)
    elif exe.incidente_id:
        incidente = {"id": exe.incidente_id, "folder": exe.incidente_folder}

    cand = _candidato_confirmado(db, exe)
    contexto = _contexto(exe, cand)
    ja = {
        d.template_id: d for d in db.query(EmbTarefaDisparo)
        .filter(EmbTarefaDisparo.execucao_id == exe.id, EmbTarefaDisparo.status == DISPARO_CRIADA)
    }
    tarefas = []
    for tpl in db.query(EmbTarefaTemplate).filter(EmbTarefaTemplate.ativo.is_(True)).order_by(
        EmbTarefaTemplate.ordem.asc(), EmbTarefaTemplate.id.asc()
    ):
        contato_id, contato_nome, erro = _responsavel(db, tpl, exe)
        prazo = add_business_days(hoje, tpl.prazo_dias_uteis or 0)
        tarefas.append({
            "template_id": tpl.id,
            "nome": tpl.nome,
            "tipo_id": tpl.tipo_id,
            "subtipo_id": tpl.subtipo_id,
            "subtipo_nome": tpl.subtipo_nome,
            "responsavel_contact_id": contato_id,
            "responsavel_nome": contato_nome,
            "prazo": prazo.isoformat(),
            "prioridade": tpl.prioridade,
            "descricao": renderizar(tpl.descricao_template, contexto),
            "observacoes": renderizar(tpl.observacoes_template, contexto) or None,
            "erro": erro,
            "ja_disparada": tpl.id in ja,
            "l1_task_id": ja[tpl.id].l1_task_id if tpl.id in ja else None,
        })
    return {
        "incidente": incidente and {
            "id": exe.incidente_id, "folder": exe.incidente_folder,
            "cnj": exe.incidente_cnj, "office_id": exe.incidente_office_id,
        },
        "tarefas": tarefas,
        "placeholders": PLACEHOLDERS,
    }


def disparar(db: Session, execucao_id: int, l1_client, *, user_id: Optional[int] = None,
             hoje: Optional[date] = None) -> dict[str, Any]:
    from app.services.legal_one_vinculo_tarefa import TarefaSemVinculoError, criar_tarefa_na_pasta

    hoje = hoje or service.hoje_brt()
    dados = previa(db, execucao_id, l1_client, hoje=hoje)
    exe = db.get(EmbExecucao, execucao_id)
    if not dados["incidente"]:
        raise ValueError(
            f"Não achei incidente de embargos na pasta {exe.pasta} no Legal One. "
            "Cadastre o incidente (Proc - X/00N) e tente de novo."
        )
    if not dados["tarefas"]:
        raise ValueError("Nenhum template de tarefa ativo — configure na tela de templates.")

    office = exe.incidente_office_id or 22
    criadas = falhas = puladas = 0
    for t in dados["tarefas"]:
        if t["ja_disparada"]:
            puladas += 1
            continue
        registro = EmbTarefaDisparo(
            execucao_id=exe.id, template_id=t["template_id"], template_nome=t["nome"],
            incidente_id=exe.incidente_id, subtipo_id=t["subtipo_id"],
            responsavel_contact_id=t["responsavel_contact_id"], prazo=date.fromisoformat(t["prazo"]),
            descricao=t["descricao"], user_id=user_id,
        )
        if t["erro"]:
            registro.status, registro.erro = DISPARO_FALHA, t["erro"]
        else:
            aberta = _tarefa_aberta_no_incidente(l1_client, exe.incidente_id, t["subtipo_id"])
            if aberta:
                # Já existe (feita à mão?): registra como criada pra não duplicar no próximo clique.
                registro.status, registro.l1_task_id = DISPARO_CRIADA, aberta
                registro.erro = "Já havia tarefa aberta desse subtipo no incidente — não criei outra."
            else:
                quando = _data_hora_prazo(date.fromisoformat(t["prazo"]))
                payload = {
                    "description": t["descricao"][:1000],
                    "notes": t["observacoes"],
                    "priority": t["prioridade"],
                    "startDateTime": quando,
                    "endDateTime": quando,
                    "status": {"id": 0},
                    "typeId": t["tipo_id"],
                    "subTypeId": t["subtipo_id"],
                    "responsibleOfficeId": office,
                    "originOfficeId": office,
                    "participants": [{
                        "contact": {"id": t["responsavel_contact_id"]},
                        "isResponsible": True, "isExecuter": True, "isRequester": True,
                    }],
                }
                try:
                    criada = criar_tarefa_na_pasta(l1_client, payload, exe.incidente_id)
                    if criada and criada.get("id"):
                        registro.status, registro.l1_task_id = DISPARO_CRIADA, int(criada["id"])
                    else:
                        formatar = getattr(l1_client, "format_last_create_task_error", None)
                        registro.status = DISPARO_FALHA
                        registro.erro = (formatar() if callable(formatar) else None) or "O Legal One recusou a criação da tarefa."
                except TarefaSemVinculoError as exc:
                    registro.status, registro.erro = DISPARO_FALHA, str(exc)
                except Exception as exc:  # noqa: BLE001
                    registro.status, registro.erro = DISPARO_FALHA, f"Erro ao criar a tarefa: {exc}"
        registro.erro = (registro.erro or None) and str(registro.erro)[:1000]
        db.add(registro)
        if registro.status == DISPARO_CRIADA:
            criadas += 1
        else:
            falhas += 1
        service.registrar_evento(
            db, SECAO_TAREFAS,
            (f"Tarefa '{t['nome']}' ({t['subtipo_nome']}) no incidente {exe.incidente_folder}: "
             + (f"criada, id {registro.l1_task_id}, prazo {date.fromisoformat(t['prazo']):%d/%m/%Y}, "
                f"responsável {t['responsavel_nome']}." if registro.status == DISPARO_CRIADA and not registro.erro
                else (registro.erro or "falhou."))),
            nivel=EVT_INFO if registro.status == DISPARO_CRIADA else EVT_ERRO,
            execucao_id=exe.id, user_id=user_id,
        )
        db.commit()

    pendentes = db.query(EmbTarefaTemplate).filter(EmbTarefaTemplate.ativo.is_(True)).count()
    feitas = (
        db.query(EmbTarefaDisparo.template_id)
        .filter(EmbTarefaDisparo.execucao_id == exe.id, EmbTarefaDisparo.status == DISPARO_CRIADA)
        .distinct().count()
    )
    if feitas >= pendentes and exe.estado != ESTADO_CONCLUIDO:
        exe.estado = ESTADO_CONCLUIDO
        exe.proxima_consulta = None
        service.registrar_evento(
            db, SECAO_TAREFAS,
            "Tarefas disparadas no incidente — fluxo fechado. As próximas intimações chegam pela pasta do incidente.",
            execucao_id=exe.id, user_id=user_id,
        )
    elif falhas:
        service.registrar_evento(
            db, SECAO_TAREFAS, f"{falhas} tarefa(s) não saíram — corrija e dispare de novo (as criadas não repetem).",
            nivel=EVT_AVISO, execucao_id=exe.id, user_id=user_id,
        )
    db.commit()
    return {"criadas": criadas, "falhas": falhas, "puladas": puladas, "estado": exe.estado}


def disparos(db: Session, execucao_id: int) -> list[dict[str, Any]]:
    return [
        {
            "id": d.id, "template_nome": d.template_nome, "subtipo_id": d.subtipo_id,
            "incidente_id": d.incidente_id, "responsavel_contact_id": d.responsavel_contact_id,
            "prazo": d.prazo.isoformat() if d.prazo else None, "descricao": d.descricao,
            "status": d.status, "l1_task_id": d.l1_task_id, "erro": d.erro,
            "criado_em": d.criado_em.isoformat() if d.criado_em else None,
        }
        for d in db.query(EmbTarefaDisparo)
        .filter(EmbTarefaDisparo.execucao_id == execucao_id)
        .order_by(EmbTarefaDisparo.id.desc())
    ]
