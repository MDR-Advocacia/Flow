# -*- coding: utf-8 -*-
"""Uma tarefa por subtipo na proposta — e o lote agenda só a publicação pedida.

O ACHADO (14/09/2026, olhando as sentenças e acórdãos pendentes do BB Réu)
--------------------------------------------------------------------------
8 de 126 publicações propunham o mesmo subtipo duas ou três vezes: a
classificação extra da IA trazia template com o mesmo subtipo da primária (ex.:
"Análise de Encerramento Réu" por "Arquivamento Definitivo" e por "Trânsito em
Julgado Certificado"). Quem agendava tinha de remover na mão.

E, para o robô agendar só o recorte: `schedule_group` marca como AGENDADO toda
publicação pendente ou ignorada do processo. Nos 125 processos do recorte, 72
IGNORADAS e 2 classificadas de outra providência iriam junto. `record_ids`
restringe; sem ele, a tela segue igual.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.legal_one import LegalOneOffice, LegalOneTaskSubType, LegalOneTaskType, LegalOneUser
from app.models.publication_search import PublicationRecord, PublicationSearch
from app.models.task_template import TaskTemplate
from app.services.publication_search_service import PublicationSearchService

sem_repetir = PublicationSearchService._sem_subtipo_repetido
FUTURO = (datetime.now(timezone.utc) + timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── a regra ────────────────────────────────────────────────────────────────


def test_fica_a_primeira_de_cada_subtipo_na_ordem():
    propostas = [
        {"template_id": 88, "payload": {"subTypeId": 900}},
        {"template_id": 89, "payload": {"subTypeId": 901}},
        {"template_id": 78, "payload": {"subTypeId": 902}},
        {"template_id": 57, "payload": {"subTypeId": 902}},
        {"template_id": 90, "payload": {"subTypeId": 900}},
    ]
    assert [p["template_id"] for p in sem_repetir(propostas)] == [88, 89, 78]


def test_aceita_payload_solto_e_mantem_o_que_nao_tem_subtipo():
    itens = [{"subTypeId": 5}, {"subTypeId": "5"}, {"description": "a"}, {"description": "b"}]
    assert sem_repetir(itens) == [{"subTypeId": 5}, {"description": "a"}, {"description": "b"}]


def test_lista_vazia_ou_nula():
    assert sem_repetir([]) == []
    assert sem_repetir(None) == []


# ── montagem da proposta ───────────────────────────────────────────────────


class _L1:
    def __init__(self):
        self.criadas = []
        self._proximo = 700000

    def create_task(self, payload):
        self._proximo += 1
        self.criadas.append(payload["subTypeId"])
        return {"id": self._proximo}

    def format_last_create_task_error(self):
        return None

    def link_task_to_lawsuit(self, task_id, link_payload):
        return True


class _Robo:
    id = None
    email = None
    name = "Robô — teste"


@pytest.fixture(autouse=True)
def _taxonomia_nao_interfere(monkeypatch):
    # A taxonomia real mora no banco da aplicação; aqui a categoria já é válida.
    monkeypatch.setattr(
        "app.services.classifier.taxonomy.repair_classification",
        lambda cat, sub, **kwargs: (cat, sub),
    )


def _catalogo(db):
    db.add_all([
        LegalOneOffice(external_id=23, name="Réu", is_active=True),
        LegalOneUser(external_id=10, name="Responsável", email="resp@exemplo.test", is_active=True),
        LegalOneTaskType(external_id=15, name="BB Defesa", is_active=True),
        LegalOneTaskSubType(external_id=900, name="Inclusão de Resultado", parent_type_external_id=15, is_active=True),
        LegalOneTaskSubType(external_id=901, name="Acompanhar Trânsito", parent_type_external_id=15, is_active=True),
        LegalOneTaskSubType(external_id=902, name="Análise de Encerramento", parent_type_external_id=15, is_active=True),
    ])
    busca = PublicationSearch(date_from="2026-09-11T00:00:00Z", status="CONCLUIDO")
    db.add(busca)
    db.commit()
    return busca


def _template(db, categoria, subcategoria, subtipo):
    t = TaskTemplate(
        name=f"{subcategoria} -> {subtipo}", category=categoria, subcategory=subcategoria,
        office_external_id=23, task_subtype_external_id=subtipo, responsible_user_external_id=10,
        priority="Normal", due_business_days=5,
    )
    db.add(t)
    db.flush()
    return t


def test_classificacao_extra_nao_repete_subtipo_da_primaria(db_session):
    busca = _catalogo(db_session)
    sentenca, transito = "Sentença e Extinção", "Trânsito em Julgado e Arquivamento"
    primaria = _template(db_session, sentenca, "Sentença Homologação de Transação", 900)
    _template(db_session, sentenca, "Sentença Homologação de Transação", 901)
    _template(db_session, transito, "Arquivamento Definitivo", 902)
    _template(db_session, transito, "Trânsito em Julgado Certificado", 902)
    _template(db_session, transito, "Trânsito em Julgado Certificado", 900)
    rec = PublicationRecord(
        search_id=busca.id, legal_one_update_id=1, status="CLASSIFICADO", is_duplicate=False,
        linked_lawsuit_id=501, linked_office_id=23, linked_lawsuit_cnj="0800001-00.2026.8.14.0001",
        publication_date="2026-09-11T00:00:00Z", description="Sentença homologando transação.",
        category=sentenca, subcategory="Sentença Homologação de Transação",
        classifications=[
            {"categoria": sentenca, "subcategoria": "Sentença Homologação de Transação"},
            {"categoria": transito, "subcategoria": "Arquivamento Definitivo"},
            {"categoria": transito, "subcategoria": "Trânsito em Julgado Certificado"},
        ],
    )
    db_session.add(rec)
    db_session.commit()

    PublicationSearchService(db_session, _L1())._build_task_proposals([rec], skip_responsible_lookup=True)
    db_session.refresh(rec)

    propostas = rec.raw_relationships["_proposed_tasks"]
    subtipos = [p["payload"]["subTypeId"] for p in propostas]
    assert sorted(subtipos) == [900, 901, 902]
    # a "Inclusão de Resultado" que fica é a da classificação principal
    assert propostas[subtipos.index(900)]["template_id"] == primaria.id
    assert rec.raw_relationships["_proposed_task"] == propostas[0]


def test_tela_nao_mostra_subtipo_repetido_de_proposta_antiga(db_session):
    busca = _catalogo(db_session)
    antiga = [
        {"template_id": 1, "template_name": "Inclusão", "payload": {"subTypeId": 900, "description": "a"}},
        {"template_id": 2, "template_name": "Encerramento", "payload": {"subTypeId": 902, "description": "b"}},
        {"template_id": 3, "template_name": "Encerramento de novo", "payload": {"subTypeId": 902, "description": "c"}},
    ]
    rec = PublicationRecord(
        search_id=busca.id, legal_one_update_id=2, status="CLASSIFICADO", is_duplicate=False,
        linked_lawsuit_id=502, linked_office_id=23,
        raw_relationships={"_proposed_task": antiga[0], "_proposed_tasks": antiga},
    )
    db_session.add(rec)
    db_session.commit()

    grupo = PublicationSearchService._build_group([rec])

    assert [t["subTypeId"] for t in grupo["proposed_tasks"]] == [900, 902]
    assert [t["description"] for t in grupo["proposed_tasks"]] == ["a", "b"]


# ── agendamento: recorte por publicação ───────────────────────────────────


def _tarefa(subtipo):
    return {
        "description": f"tarefa {subtipo}", "typeId": 15, "subTypeId": subtipo, "priority": "Normal",
        "startDateTime": FUTURO, "endDateTime": FUTURO, "publishDate": FUTURO, "status": {"id": 0},
        "responsibleOfficeId": 23, "originOfficeId": 23,
        "participants": [{"contact": {"id": 10}, "isResponsible": True,
                          "isExecuter": True, "isRequester": True}],
    }


def _publicacao(busca, update_id, status, subtipos):
    propostas = [{"payload": _tarefa(s)} for s in subtipos]
    return PublicationRecord(
        search_id=busca.id, legal_one_update_id=update_id, status=status, is_duplicate=False,
        linked_lawsuit_id=80663, linked_office_id=23, linked_lawsuit_cnj="0806727-97.2026.8.10.0026",
        raw_relationships=(
            {"_proposed_task": propostas[0], "_proposed_tasks": propostas} if propostas else {}
        ),
    )


def test_lote_agenda_so_a_publicacao_informada_e_sem_subtipo_repetido(db_session):
    busca = _catalogo(db_session)
    alvo = _publicacao(busca, 11, "CLASSIFICADO", [900, 901, 900])
    outra = _publicacao(busca, 12, "CLASSIFICADO", [902])
    ignorada = _publicacao(busca, 13, "IGNORADO", [])
    db_session.add_all([alvo, outra, ignorada])
    db_session.commit()
    l1 = _L1()

    out = PublicationSearchService(db_session, l1).schedule_group(
        lawsuit_id=80663, record_ids=[alvo.id], scheduled_by=_Robo(), force_duplicate=True,
    )

    assert l1.criadas == [900, 901]
    assert out["scheduled_publication_ids"] == [alvo.id]
    for r in (alvo, outra, ignorada):
        db_session.refresh(r)
    assert alvo.status == "AGENDADO"
    assert alvo.scheduled_by_name == "Robô — teste"
    assert alvo.scheduled_by_user_id is None
    assert outra.status == "CLASSIFICADO", "publicação fora do recorte não pode ir junto"
    assert ignorada.status == "IGNORADO", "ignorada do mesmo processo não pode virar agendada"


def test_sem_recorte_o_grupo_segue_levando_o_processo_inteiro(db_session):
    """Comportamento da tela, preservado de propósito."""
    busca = _catalogo(db_session)
    alvo = _publicacao(busca, 21, "CLASSIFICADO", [900])
    outra = _publicacao(busca, 22, "CLASSIFICADO", [902])
    ignorada = _publicacao(busca, 23, "IGNORADO", [])
    db_session.add_all([alvo, outra, ignorada])
    db_session.commit()

    PublicationSearchService(db_session, _L1()).schedule_group(lawsuit_id=80663, force_duplicate=True)

    for r in (alvo, outra, ignorada):
        db_session.refresh(r)
    assert {alvo.status, outra.status, ignorada.status} == {"AGENDADO"}
