"""Agendamento automático de publicações — só onde a equipe agenda COM CERTEZA.

POR QUE EXISTE
--------------
Estudo de 14/09/2026 sobre 30 dias de decisões da equipe no BB Réu (5.711
publicações, sem "Para Análise", sem robô): em algumas classificações a equipe
agenda exatamente as tarefas que a proposta traz em 91–98% das vezes. Nessas, a
mesa do operador só atrasa. O operador liberou as 10 classificações de
`REGRAS_PADRAO` e pediu a função DORMENTE: ligada, ela pega a publicação logo
depois da classificação (job de 10 min); por ora a rodada é de teste, disparada
à mão sobre o estoque.

REGRAS (combinadas com o operador)
----------------------------------
- "Para Análise" nunca — nem na classificação principal, nem numa extra, nem por
  configuração. É justamente o que a equipe precisa ver.
- Não ignora nada: ou agenda, ou deixa pendente para a equipe.
- Só age se a proposta tiver SÓ tarefas da classificação principal. Tarefa vinda
  de classificação extra da IA foi a principal causa de falha no estudo.
- Acórdão Não Provido entra com a Inclusão de Resultado 2º GRAU sempre (88% no
  estudo: a equipe tirou essa tarefa 26 vezes; o operador decidiu que ela fica).
- Subtipo já aberto no L1 para o processo não é recriado. Se todos já estão
  abertos, a publicação é marcada como tratada (AGENDADO) com a auditoria
  apontando as tarefas existentes — não pode ficar pendência no painel.
- Conclusão prevista vencida ou vencendo no dia (atraso na fila) é recalculada:
  hoje + os dias úteis do template.
- Uma publicação por agendamento (`record_ids`): o grupo sem recorte levaria
  junto as outras pendentes e as IGNORADAS do processo.
- Motivos na auditoria são CÓDIGOS curtos: as colunas de motivo de
  `publicacao_tarefa_audit` são varchar(40) e o estouro acontece no commit,
  DEPOIS de a tarefa existir no L1 (aconteceu no lote de 14/09/2026).
- Publicação que ficou com a equipe não é reavaliada enquanto a classificação e
  a proposta forem as mesmas (marca em `raw_relationships`). Erro e consulta ao
  L1 que falhou tentam de novo, até `TENTATIVAS` vezes.

LIGAR / CONFIGURAR (sem deploy)
-------------------------------
- `publicacoes_agendamento_automatico_ativo` = "true" liga o job (padrão: desligado).
- `publicacoes_agendamento_automatico_regras` = JSON
  {"<office_id>": [["categoria", "subcategoria"], ...]} substitui a lista padrão.
  Setting malformado = nenhuma classificação sai sozinha (lado seguro).

RODADA MANUAL (roda mesmo com o job dormente)
---------------------------------------------
    db = SessionLocal()
    executar_rodada(db, simular=True, respeitar_interruptor=False)  # não grava nada
    executar_rodada(db, respeitar_interruptor=False)                # agenda
"""
from __future__ import annotations

import copy
import json
import logging
import unicodedata
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

SETTING_ATIVO = "publicacoes_agendamento_automatico_ativo"
SETTING_REGRAS = "publicacoes_agendamento_automatico_regras"

NOME_DO_ROBO = "Robô — agendamento automático"
TAMANHO_MOTIVO = 40
MOTIVO_DATA = "atraso_conclusao_vencida"
MOTIVO_JA_EXISTE = "ja_existe"  # mesmo código da tela (REMOVE_TASK_REASONS)
MARCA = "_agendamento_automatico"
INTERVALO_MINUTOS = 10
LIMITE_POR_RODADA = 200
TENTATIVAS = 3
BRT = ZoneInfo("America/Sao_Paulo")

# Namespace próprio: o 4243 já é dividido pelo motor sem pasta e pelo autorun
# do Tratamento Web.
_TRAVA_NS = 4244
_TRAVA_CHAVE = 1

# Estudo de 14/09/2026: % em que a equipe agendou exatamente a proposta, e volume
# em 30 dias no BB Réu. Strings conferidas contra o gravado em produção.
REGRAS_PADRAO: dict[str, list[list[str]]] = {
    "23": [
        ["Sentença e Extinção", "Sentença Improcedente"],  # 95%, 505
        ["Sentença e Extinção", "Sentença Parcialmente Procedente"],  # 97%, 118
        ["Cumprimento de Sentença / Execução", "Intimação para Pagamento Voluntário (15 dias úteis)"],  # 98%, 105
        ["Cumprimento de Sentença / Execução", "Sentença de Extinção da Execução"],  # 96%, 35
        ["Trânsito em Julgado e Arquivamento", "Arquivamento Definitivo"],  # 95%, 27
        ["Sentença e Extinção", "Sentença Extinção sem Resolução de Mérito"],  # 94%, 252
        ["Decisões Interlocutórias e Despachos Relevantes", "Suspensão / Sobrestamento"],  # 95%, 41
        ["Sentença e Extinção", "Sentença Homologação de Transação"],  # 91%, 105
        ["Trânsito em Julgado e Arquivamento", "Trânsito em Julgado Certificado"],  # 91%, 38
        ["Recursos e Julgamentos em 2º Grau", "Acórdão / Decisão Monocrática — Não Provido"],  # 88%, 432
    ],
}

AGENDADA = "agendada"
TRATADA = "tratada_com_tarefas_existentes"
# Ficam com a equipe até a classificação ou a proposta mudarem.
PULOS_DEFINITIVOS = frozenset({"sem_proposta", "tarefa_de_outra_classificacao", "proposta_desatualizada"})
# Tentam de novo nas rodadas seguintes, até TENTATIVAS.
PULOS_COM_TETO = frozenset({"erro", "consulta_l1_falhou"})

_TRACOS = str.maketrans({"—": "-", "–": "-"})


class Robo:
    """Quem assina: ninguém clicou — sem id/e-mail, só o nome do robô."""

    id = None
    email = None
    name = NOME_DO_ROBO


# ── configuração ──────────────────────────────────────────────────────────


def _chave(texto: Optional[str]) -> str:
    """Forma comparável: sem acento, sem caixa, travessão = hífen, espaços colapsados."""
    s = unicodedata.normalize("NFD", (texto or "").strip().translate(_TRACOS))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.casefold().split())


def ativo() -> bool:
    """Interruptor geral (padrão DESLIGADO)."""
    from app.services.app_settings import get_setting

    return (get_setting(SETTING_ATIVO) or "false").strip().lower() in ("1", "true", "sim", "on", "yes")


def regras() -> dict[int, frozenset]:
    """{office_id: {(categoria, subcategoria) normalizadas}} liberadas."""
    from app.services.app_settings import get_setting

    bruto: Any = REGRAS_PADRAO
    texto = get_setting(SETTING_REGRAS)
    if texto:
        try:
            bruto = json.loads(texto)
            if not isinstance(bruto, dict):
                raise ValueError("esperava um objeto JSON")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Agendamento automático: setting %s inválido (%s) — nenhuma classificação sai sozinha.",
                SETTING_REGRAS, exc,
            )
            return {}
    saida: dict[int, frozenset] = {}
    for office, pares in bruto.items():
        try:
            saida[int(office)] = frozenset(
                (_chave(cat), _chave(sub)) for cat, sub in pares
                if not eh_para_analise(cat, sub)
            )
        except Exception:  # noqa: BLE001
            logger.warning("Agendamento automático: regra inválida para o escritório %r — ignorada.", office)
    return saida


def eh_para_analise(categoria: Optional[str], subcategoria: Optional[str]) -> bool:
    return _chave(categoria).startswith("para analise") or _chave(subcategoria).startswith("para analise")


def _tem_para_analise(categoria, subcategoria, classificacoes) -> bool:
    if eh_para_analise(categoria, subcategoria):
        return True
    extras = classificacoes if isinstance(classificacoes, list) else []
    return any(
        isinstance(c, dict) and eh_para_analise(c.get("categoria"), c.get("subcategoria"))
        for c in extras
    )


def _liberada(office, categoria, subcategoria, classificacoes, regras_: dict) -> bool:
    if _tem_para_analise(categoria, subcategoria, classificacoes):
        return False
    try:
        office = int(office or 0)
    except (TypeError, ValueError):
        return False
    return (_chave(categoria), _chave(subcategoria)) in regras_.get(office, frozenset())


def classificacao_liberada(rec, regras_: dict) -> bool:
    return _liberada(rec.linked_office_id, rec.category, rec.subcategory, rec.classifications, regras_)


# ── proposta ──────────────────────────────────────────────────────────────


def _lista_gravada(rec) -> list:
    raw = rec.raw_relationships if isinstance(rec.raw_relationships, dict) else {}
    lista = raw.get("_proposed_tasks")
    if not isinstance(lista, list):
        unica = raw.get("_proposed_task")
        lista = [unica] if isinstance(unica, dict) else []
    return lista


def propostas_de(rec) -> list[dict]:
    from app.services.publication_search_service import PublicationSearchService

    validas = [
        p for p in _lista_gravada(rec)
        if isinstance(p, dict) and isinstance(p.get("payload"), dict)
        and p["payload"].get("subTypeId") is not None
    ]
    return PublicationSearchService._sem_subtipo_repetido(validas)


def templates_desatualizados(propostas: list[dict], templates: dict) -> list:
    """A proposta precisa refletir o template de HOJE: subtipo, papel e squad
    (o roteamento lê a squad da proposta gravada)."""
    fora = []
    for p in propostas:
        t = templates.get(p.get("template_id"))
        if (
            t is None
            or not t.is_active
            or getattr(t, "needs_taxonomy_review", False)
            or str(t.task_subtype_external_id) != str(p["payload"].get("subTypeId"))
            or (t.target_squad_id or None) != (p.get("target_squad_id") or None)
            or (t.target_role or "principal") != (p.get("target_role") or "principal")
        ):
            fora.append(p.get("template_id"))
    return fora


def _formas_da_principal(svc, rec) -> tuple[set, set]:
    """A classificação gravada e a reparada: o template casa pela reparada
    (ver `_build_task_proposals`), então as duas contam como principal."""
    categorias, subcategorias = {_chave(rec.category)}, {_chave(rec.subcategory)}
    try:
        from app.services.classifier.taxonomy import repair_classification

        polo = svc._resolve_office_polo(rec.linked_office_id) or getattr(rec, "polo", None)
        cat, sub = repair_classification(
            rec.category or "", rec.subcategory or "-", polo_scope=polo, taxonomy_version="v2",
        )
        categorias.add(_chave(cat))
        subcategorias.add(_chave(sub))
    except Exception:  # noqa: BLE001 — sem o reparo compara só a gravada: na dúvida, pula
        pass
    return categorias, subcategorias


def templates_de_outra_classificacao(svc, rec, propostas: list[dict], templates: dict) -> list[dict]:
    categorias, subcategorias = _formas_da_principal(svc, rec)
    fora = []
    for p in propostas:
        t = templates.get(p.get("template_id"))
        if t is None:
            continue
        if _chave(t.category) not in categorias or (
            t.subcategory and _chave(t.subcategory) not in subcategorias
        ):
            fora.append({"template": t.id, "classificacao": f"{t.category} / {t.subcategory or '(qualquer)'}"})
    return fora


def _impressao(rec) -> str:
    """Classificação + templates da proposta: muda quando vale reavaliar."""
    ids = sorted(str(p.get("template_id")) for p in _lista_gravada(rec) if isinstance(p, dict))
    return f"{_chave(rec.category)}|{_chave(rec.subcategory)}|{','.join(ids)}"


def _marcar(rec, resultado: str, **detalhe) -> bool:
    if not isinstance(rec.raw_relationships, dict):
        # Proposta nunca montada: é por aqui que a repassada de propostas
        # (publication_propostas_worker) acha o registro. Não mexe.
        return False
    raw = dict(rec.raw_relationships)
    anterior = raw.get(MARCA) if isinstance(raw.get(MARCA), dict) else {}
    impressao = _impressao(rec)
    tentativas = 1
    if anterior.get("resultado") == resultado and anterior.get("impressao") == impressao:
        tentativas = int(anterior.get("tentativas") or 0) + 1
    raw[MARCA] = {
        "resultado": resultado,
        "em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "impressao": impressao,
        "tentativas": tentativas,
        **detalhe,
    }
    rec.raw_relationships = raw
    return True


def ja_avaliada_sem_mudanca(rec) -> bool:
    raw = rec.raw_relationships if isinstance(rec.raw_relationships, dict) else {}
    marca = raw.get(MARCA)
    if not isinstance(marca, dict) or marca.get("impressao") != _impressao(rec):
        return False
    if marca.get("resultado") in PULOS_DEFINITIVOS:
        return True
    return marca.get("resultado") in PULOS_COM_TETO and int(marca.get("tentativas") or 0) >= TENTATIVAS


# ── datas e marcadores ────────────────────────────────────────────────────


def _dia_brt(iso: Any) -> Optional[date]:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(BRT).date()
    except (TypeError, ValueError):
        return None


def _fim_do_dia_utc(dia: date) -> str:
    local = datetime(dia.year, dia.month, dia.day, 23, 59, 59, tzinfo=BRT)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ajustar_conclusao(rec, payload: dict, template, hoje: date) -> Optional[dict]:
    """Conclusão vencida ou vencendo hoje -> hoje + N dias úteis do template.
    Audiência não mexe: a data dela é a do ato, não um prazo interno."""
    from app.services.prazos_iniciais.prazo_calculator import add_business_days

    if getattr(rec, "audiencia_data", None):
        return None
    conclusao = _dia_brt(payload.get("endDateTime"))
    if conclusao is not None and conclusao > hoje:
        return None
    dias = getattr(template, "due_business_days", None) or 5
    nova = add_business_days(hoje, dias)
    payload["startDateTime"] = _fim_do_dia_utc(nova)
    payload["endDateTime"] = _fim_do_dia_utc(nova)
    payload["_data_troca_motivo"] = MOTIVO_DATA
    return {"de": conclusao.isoformat() if conclusao else None, "para": nova.isoformat(), "dias_uteis": dias}


def conferir_marcadores(payloads: list[dict]) -> None:
    for p in payloads:
        for chave in ("_data_troca_motivo", "_tarefa_removida_motivo",
                      "_agendou_com_tarefa_aberta_motivo", "_subtipo_troca_motivo"):
            valor = p.get(chave)
            if valor is not None and len(str(valor)) > TAMANHO_MOTIVO:
                raise ValueError(f"marcador {chave} com {len(str(valor))} caracteres (máximo {TAMANHO_MOTIVO})")


# ── ações ─────────────────────────────────────────────────────────────────


def marcar_tratada_com_tarefas_existentes(svc, db: Session, rec, propostas: list[dict],
                                          abertas: dict, nomes: dict) -> list[dict]:
    """Todas as tarefas já estão abertas no L1: AGENDADO pelo robô, com a
    auditoria apontando as tarefas existentes."""
    from app.models.publication_search import RECORD_STATUS_SCHEDULED
    from app.services.publication_treatment_service import PublicationTreatmentService

    agora = datetime.now(timezone.utc)
    enviados, ids, ajustes, vinculos = [], [], [], []
    for p in propostas:
        sub = int(p["payload"]["subTypeId"])
        tarefa = (abertas.get(sub) or [{}])[0].get("task_id")
        nome = nomes.get(sub, str(sub))
        payload = copy.deepcopy(p["payload"])
        payload["_tarefa_removida_motivo"] = MOTIVO_JA_EXISTE
        enviados.append(payload)
        ids.append(tarefa)
        ajustes.append({"tarefa_existente": {
            "antes": None, "depois": tarefa,
            "motivo": f"Robô: a tarefa {nome} já estava aberta no L1; publicação marcada como tratada sem criar outra.",
        }})
        vinculos.append({"subtipo": nome, "tarefa": tarefa})
    conferir_marcadores(enviados)
    rec.status = RECORD_STATUS_SCHEDULED
    rec.updated_at = agora
    rec.scheduled_by_user_id = None
    rec.scheduled_by_email = None
    rec.scheduled_by_name = NOME_DO_ROBO
    rec.scheduled_at = agora
    PublicationTreatmentService(db).sync_item_from_record(rec, commit=False)
    svc._record_scheduled_task_audit(
        lawsuit_id=rec.linked_lawsuit_id, records=[rec], proposals=propostas,
        sent_payloads=enviados, created_task_ids=ids,
        sb_user_id=None, sb_email=None, sb_name=NOME_DO_ROBO, when=agora,
        system_adjustments=ajustes,
    )
    db.commit()
    return vinculos


def tratar_publicacao(svc, db: Session, rec, *, simular: bool, regras_: dict, templates: dict,
                      nomes: dict, hoje: Optional[date] = None) -> dict[str, Any]:
    from app.models.publication_search import RECORD_STATUS_CLASSIFIED

    db.refresh(rec)  # a equipe pode ter tratado enquanto a rodada andava
    base = {"publicacao": rec.id, "processo": rec.linked_lawsuit_id, "cnj": rec.linked_lawsuit_cnj,
            "classificacao": f"{rec.category} / {rec.subcategory or '-'}"}
    if rec.status != RECORD_STATUS_CLASSIFIED:
        return {"resultado": "ja_tratada", "status": rec.status, **base}
    if rec.is_duplicate or not rec.linked_lawsuit_id or not classificacao_liberada(rec, regras_):
        return {"resultado": "fora_das_regras", **base}

    def pular(resultado: str, **detalhe) -> dict[str, Any]:
        if not simular and _marcar(rec, resultado, **detalhe):
            db.commit()
        return {"resultado": resultado, **detalhe, **base}

    propostas = propostas_de(rec)
    if propostas and templates_desatualizados(propostas, templates) and not simular:
        # Template mudou depois da proposta: refaz esta, uma vez, antes de decidir.
        svc._build_task_proposals([rec])
        db.refresh(rec)
        propostas = propostas_de(rec)
    if not propostas:
        return pular("sem_proposta")
    desatualizados = templates_desatualizados(propostas, templates)
    if desatualizados:
        return pular("proposta_desatualizada", templates=desatualizados)
    extras = templates_de_outra_classificacao(svc, rec, propostas, templates)
    if extras:
        return pular("tarefa_de_outra_classificacao", tarefas_extras=extras)

    subtipos = [int(p["payload"]["subTypeId"]) for p in propostas]
    dup = svc.check_duplicates_for_lawsuit(rec.linked_lawsuit_id, subtipos)
    if dup.get("check_failed"):
        return pular("consulta_l1_falhou", erro=str(dup.get("check_error") or "")[:300])
    abertas = {int(k): v for k, v in (dup.get("duplicates_by_subtype") or {}).items()}
    ja_abertas = [{"subtipo": nomes.get(s, str(s)), "tarefas": [d.get("task_id") for d in v]}
                  for s, v in abertas.items()]
    novas = [p for p in propostas if int(p["payload"]["subTypeId"]) not in abertas]

    if not novas:
        if simular:
            return {"resultado": "marcaria_tratada", "ja_abertas": ja_abertas, **base}
        try:
            vinculos = marcar_tratada_com_tarefas_existentes(svc, db, rec, propostas, abertas, nomes)
            return {"resultado": TRATADA, "vinculos": vinculos, **base}
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            return pular("erro", etapa="marcar_tratada", erro=str(exc)[:300])

    hoje = hoje or datetime.now(BRT).date()
    payloads = [copy.deepcopy(p["payload"]) for p in novas]
    datas = []
    for p, payload in zip(novas, payloads):
        troca = ajustar_conclusao(rec, payload, templates[p["template_id"]], hoje)
        if troca:
            datas.append({"subtipo": nomes.get(int(payload["subTypeId"]), str(payload["subTypeId"])), **troca})
    if ja_abertas:
        payloads[0]["_tarefa_removida_motivo"] = MOTIVO_JA_EXISTE
    conferir_marcadores(payloads)
    plano = [nomes.get(int(pl["subTypeId"]), str(pl["subTypeId"])) for pl in payloads]

    if simular:
        return {"resultado": "agendaria", "tarefas": plano, "datas": datas, "ja_abertas": ja_abertas, **base}
    try:
        out = svc.schedule_group(
            rec.linked_lawsuit_id,
            payload_overrides=payloads,
            scheduled_by=Robo(),
            force_duplicate=False,
            record_ids=[rec.id],
        )
        return {"resultado": AGENDADA, "tarefas": plano, "criadas": out.get("created_task_ids"),
                "datas": datas, "ja_abertas": ja_abertas, **base}
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return pular("erro", etapa="agendar", erro=str(exc)[:300])


# ── rodada ────────────────────────────────────────────────────────────────


def candidatos(db: Session, regras_: dict, limite: Optional[int] = None) -> list:
    from app.models.publication_search import RECORD_STATUS_CLASSIFIED, PublicationRecord as PR

    if not regras_:
        return []
    # Varredura leve (620 classificadas pendentes no BB Réu em 14/09/2026): só
    # as colunas da classificação; o registro inteiro, com propostas e texto,
    # carrega só para quem passou.
    linhas = (
        db.query(PR.id, PR.linked_office_id, PR.category, PR.subcategory, PR.classifications)
        .filter(
            PR.status == RECORD_STATUS_CLASSIFIED,
            PR.linked_office_id.in_(list(regras_)),
            PR.linked_lawsuit_id.isnot(None),
        )
        .order_by(PR.id)
        .all()
    )
    ids = [
        linha.id for linha in linhas
        if _liberada(linha.linked_office_id, linha.category, linha.subcategory, linha.classifications, regras_)
    ]
    saida = []
    for inicio in range(0, len(ids), 100):
        for rec in db.query(PR).filter(PR.id.in_(ids[inicio:inicio + 100])).order_by(PR.id).all():
            if rec.is_duplicate or ja_avaliada_sem_mudanca(rec):
                continue
            saida.append(rec)
            if limite and len(saida) >= limite:
                return saida
    return saida


def _tomar_trava() -> tuple[Any, bool]:
    """Uma rodada por vez no cluster (dois workers agendariam a mesma
    publicação duas vezes). Erro ao pedir a trava = não roda neste tick."""
    from sqlalchemy import text as _sql_text

    from app.db.session import engine as _engine

    if _engine.dialect.name != "postgresql":
        return None, True
    try:
        conn = _engine.connect()
    except Exception:  # noqa: BLE001
        logger.exception("Agendamento automático: sem conexão para a trava; pulo esta rodada.")
        return None, False
    try:
        got = conn.execute(
            _sql_text("SELECT pg_try_advisory_lock(:k1, :k2)"),
            {"k1": _TRAVA_NS, "k2": _TRAVA_CHAVE},
        ).scalar()
    except Exception:  # noqa: BLE001
        conn.close()
        logger.exception("Agendamento automático: não consegui pedir a trava; pulo esta rodada.")
        return None, False
    if not got:
        conn.close()
        return None, False
    return conn, True


def _soltar_trava(conn: Any) -> None:
    if conn is None:
        return
    from sqlalchemy import text as _sql_text

    try:
        conn.execute(_sql_text("SELECT pg_advisory_unlock(:k1, :k2)"), {"k1": _TRAVA_NS, "k2": _TRAVA_CHAVE})
    except Exception:  # noqa: BLE001
        logger.exception("Agendamento automático: falha ao liberar a trava (a conexão fecha a seguir).")
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def executar_rodada(db: Session, *, simular: bool = False, respeitar_interruptor: bool = True,
                    limite: Optional[int] = LIMITE_POR_RODADA, client: Any = None) -> dict[str, Any]:
    """Uma rodada sobre as publicações CLASSIFICADAS nas classificações liberadas.

    `respeitar_interruptor=False` é a rodada manual (teste sobre o estoque):
    roda mesmo com o job dormente."""
    if respeitar_interruptor and not ativo():
        return {"executou": False, "motivo": "desligado"}
    conn, conseguiu = _tomar_trava()
    if not conseguiu:
        return {"executou": False, "motivo": "outra_rodada_em_andamento"}
    try:
        from app.models.legal_one import LegalOneTaskSubType
        from app.models.task_template import TaskTemplate
        from app.services.publication_search_service import PublicationSearchService

        regras_ = regras()
        recs = candidatos(db, regras_, limite)
        resumo: Counter = Counter()
        resultados: list[dict[str, Any]] = []
        if recs:
            if client is None:
                from app.services.legal_one_client import LegalOneApiClient

                client = LegalOneApiClient()
            svc = PublicationSearchService(db, client)
            templates = {t.id: t for t in db.query(TaskTemplate).all()}
            nomes = {s.external_id: s.name for s in db.query(LegalOneTaskSubType).all()}
            # O id sai antes do laço: depois de um rollback o objeto expira, e
            # ler rec.id de uma linha que sumiu estouraria dentro do except.
            for rec_id, rec in [(r.id, r) for r in recs]:
                try:
                    r = tratar_publicacao(svc, db, rec, simular=simular, regras_=regras_,
                                          templates=templates, nomes=nomes)
                except Exception as exc:  # noqa: BLE001 — uma publicação não derruba a rodada
                    db.rollback()
                    logger.exception("Agendamento automático: publicação %s estourou.", rec_id)
                    r = {"resultado": "erro", "publicacao": rec_id, "erro": str(exc)[:300]}
                resumo[r["resultado"]] += 1
                resultados.append(r)
                logger.info("agendamento_automatico %s", json.dumps(r, ensure_ascii=False, default=str))
        return {"executou": True, "simular": simular, "candidatos": len(recs),
                "resumo": dict(resumo), "resultados": resultados}
    finally:
        _soltar_trava(conn)


# ── job ───────────────────────────────────────────────────────────────────


def _job_tick() -> None:
    if not ativo():
        return  # dormente: nem abre sessão
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        saida = executar_rodada(db)
        if saida.get("resumo"):
            logger.info("Agendamento automático de publicações: %s", saida["resumo"])
    except Exception:  # noqa: BLE001
        logger.exception("Tick do agendamento automático de publicações estourou (ignorado).")
    finally:
        db.close()


def register_publication_agendamento_automatico_job(scheduler) -> None:
    """Job periódico (10 min) — só no worker líder. Dormente até o setting ligar."""
    scheduler.add_job(
        _job_tick,
        trigger="interval",
        minutes=INTERVALO_MINUTOS,
        id="publication_agendamento_automatico",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Job de agendamento automático de publicações registrado (%d min, %s).",
                INTERVALO_MINUTOS, "LIGADO" if ativo() else "dormente")
