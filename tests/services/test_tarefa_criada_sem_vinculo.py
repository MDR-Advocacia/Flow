# -*- coding: utf-8 -*-
"""Tarefa agendada em pasta sai vinculada — sozinha, sem incomodar o operador.

Caso de 11/09/2026 (agenda da Supervisão Master, 13 tarefas pendentes sem
pasta): todo ponto do Flow que cria tarefa em pasta ignorava a falha do
vínculo (POST /tasks/{id}/relationships) e marcava sucesso. Regra decidida
pelo operador: tarefa sem vínculo é cancelada e reenviada até vincular — com
teto, sem reenviar para pasta que não existe mais, e parando se nem cancelar
der (reenviar criaria outra solta).
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests

from app.models.publication_search import PublicationRecord, PublicationSearch
from app.models.publication_task_audit import PublicationTaskAudit
from app.services import legal_one_vinculo_tarefa as vinculo
from app.services.batch_strategies.spreadsheet_strategy import SpreadsheetStrategy
from app.services.legal_one_vinculo_tarefa import TarefaSemVinculoError, criar_tarefa_na_pasta
from app.services.prazos_iniciais.scheduling_service import PrazosIniciaisSchedulingService
from app.services.publication_search_service import PublicationSearchService


class L1Falso:
    """
    `vinculo_falha[i]` diz como responde o vínculo da i-ésima tarefa criada:
    "ok" vincula; "recusa" devolve False (HTTP 4xx); "rede" levanta exceção;
    "perdida" levanta exceção mas o vínculo foi feito no L1.
    """

    def __init__(self, *, vinculo_falha=(), processo_existe=True, cancela=True):
        self.vinculo_falha = list(vinculo_falha)
        self._existe = processo_existe
        self.cancela = cancela
        self.criadas = []
        self.links = []
        self.canceladas = []
        self.vinculadas = {}
        self._last_link_error = None
        self._proximo = 456870

    def create_task(self, payload):
        self._proximo += 1
        self.criadas.append(self._proximo)
        return {"id": self._proximo}

    def format_last_create_task_error(self):
        return None

    def link_task_to_lawsuit(self, task_id, link_payload):
        self.links.append((task_id, link_payload))
        ordem = self.criadas.index(task_id)
        modo = self.vinculo_falha[ordem] if ordem < len(self.vinculo_falha) else "ok"
        if modo == "ok":
            self.vinculadas[task_id] = link_payload["linkId"]
            return True
        if modo == "recusa":
            self._last_link_error = 'HTTP 404: {"error":{"code":"NotFound"}}'
            return False
        if modo == "perdida":
            self.vinculadas[task_id] = link_payload["linkId"]
        raise requests.exceptions.RequestException("Maximo de tentativas excedido sem sucesso.")

    def get_task_relationships(self, task_id):
        if task_id in self.vinculadas:
            return [{"id": 1, "linkId": self.vinculadas[task_id], "linkType": "Litigation"}]
        return []

    def update_task_status(self, task_id, status_id):
        if not self.cancela:
            return False
        self.canceladas.append((task_id, status_id))
        return True

    def processo_existe(self, lawsuit_id):
        return self._existe


@pytest.fixture(autouse=True)
def esperas(monkeypatch):
    dormidas = []
    monkeypatch.setattr(vinculo, "_dormir", dormidas.append)
    return dormidas


PAYLOAD = {"description": "contestação - audiência - 09/10/2026 15:40", "subTypeId": 1245}


# ── regra ──────────────────────────────────────────────────────────────────


def test_vinculou_de_primeira_nao_cancela_nem_reenvia(esperas):
    l1 = L1Falso()
    assert criar_tarefa_na_pasta(l1, PAYLOAD, 80663) == {"id": 456871}
    assert l1.criadas == [456871]
    assert l1.canceladas == []
    assert esperas == []


def test_sem_vinculo_cancela_e_reenvia_ate_vincular(esperas):
    # 28/08: o vínculo falhou algumas vezes seguidas e depois voltou.
    l1 = L1Falso(vinculo_falha=["rede", "recusa", "ok"])
    assert criar_tarefa_na_pasta(l1, PAYLOAD, 80663) == {"id": 456873}
    assert l1.criadas == [456871, 456872, 456873]
    assert l1.canceladas == [(456871, 3), (456872, 3)]
    assert l1.vinculadas == {456873: 80663}
    assert esperas == [2, 5]


def test_resposta_perdida_com_vinculo_feito_nao_cancela(esperas):
    l1 = L1Falso(vinculo_falha=["perdida"])
    assert criar_tarefa_na_pasta(l1, PAYLOAD, 80663) == {"id": 456871}
    assert l1.criadas == [456871]
    assert l1.canceladas == []


def test_pasta_que_nao_existe_mais_nao_e_reenviada(esperas):
    # 02/09: a publicação apontava para a pasta 67080, que sumiu do L1.
    l1 = L1Falso(vinculo_falha=["recusa"] * 10, processo_existe=False)
    with pytest.raises(TarefaSemVinculoError) as erro:
        criar_tarefa_na_pasta(l1, PAYLOAD, 67080)
    assert l1.criadas == [456871]
    assert l1.canceladas == [(456871, 3)]
    assert erro.value.tarefas_soltas == []
    assert esperas == []


def test_nunca_vincula_para_no_teto_com_todas_canceladas(esperas):
    l1 = L1Falso(vinculo_falha=["recusa"] * 10)
    with pytest.raises(TarefaSemVinculoError) as erro:
        criar_tarefa_na_pasta(l1, PAYLOAD, 80663)
    assert len(l1.criadas) == vinculo.TENTATIVAS == 5
    assert [t for t, _ in l1.canceladas] == l1.criadas
    assert esperas == list(vinculo.ESPERAS_ENTRE_TENTATIVAS)
    assert erro.value.tarefas_soltas == []


def test_se_nem_cancelar_deu_nao_reenvia(esperas):
    # Reenviar com o L1 recusando até o cancelamento só criaria outra tarefa solta.
    l1 = L1Falso(vinculo_falha=["recusa"] * 10, cancela=False)
    with pytest.raises(TarefaSemVinculoError) as erro:
        criar_tarefa_na_pasta(l1, PAYLOAD, 80663)
    assert l1.criadas == [456871]
    assert erro.value.tarefas_soltas == [456871]


def test_criacao_recusada_volta_pro_caminho_de_erro_de_sempre():
    l1 = L1Falso()
    l1.create_task = lambda payload: None
    assert criar_tarefa_na_pasta(l1, PAYLOAD, 80663) is None
    assert l1.links == []


def test_cliente_sem_conferencia_nem_cancelamento_nao_quebra():
    class Minimo:
        def create_task(self, payload):
            return {"id": 1}

        def link_task_to_lawsuit(self, task_id, link_payload):
            return False

    with pytest.raises(TarefaSemVinculoError) as erro:
        criar_tarefa_na_pasta(Minimo(), PAYLOAD, 80663)
    assert erro.value.tarefas_soltas == [1]


# ── agendamento em planilha ────────────────────────────────────────────────

CNJ = "0001234-56.2026.8.26.0001"


class _DB:
    def commit(self):
        pass


def _linha_da_planilha(l1):
    office = SimpleNamespace(external_id=11, path="Escritorio Centro")
    user = SimpleNamespace(external_id=22, name="Maria")
    subtype = SimpleNamespace(external_id=44, name="Subtipo Teste", parent_type=SimpleNamespace(external_id=33))
    caches = {
        "offices": {"escritorio centro": office},
        "users": {"maria": user},
        "subtypes": {"subtipo teste": subtype},
    }
    row = {
        "ESCRITORIO": "Escritorio Centro", "CNJ": CNJ, "PUBLISH_DATE": "2026-03-18",
        "SUBTIPO": "Subtipo Teste", "EXECUTANTE": "Maria", "PRAZO": "2026-03-20",
        "DATA_TAREFA": "2026-03-20", "HORARIO": "10:30", "OBSERVACAO": None, "DESCRICAO": None,
    }
    log_item = SimpleNamespace(status="PENDENTE", fingerprint=None, created_task_id=None, error_message=None)
    fingerprints = set()
    ok = asyncio.run(SpreadsheetStrategy(_DB(), l1).process_single_item(
        log_item, row, caches,
        known_fingerprints=fingerprints,
        lawsuit_lookup={CNJ: {"id": 777, "identifierNumber": CNJ, "responsibleOfficeId": 55}},
        prefetched_cnj_numbers={CNJ},
    ))
    return ok, log_item, fingerprints


def test_planilha_reenvia_e_a_linha_fica_com_a_tarefa_vinculada():
    l1 = L1Falso(vinculo_falha=["recusa", "ok"])
    ok, log_item, _ = _linha_da_planilha(l1)
    assert ok is True and log_item.status == "SUCESSO"
    assert log_item.created_task_id == 456872
    assert l1.canceladas == [(456871, 3)]


def test_planilha_sem_pasta_viva_vira_falha_sem_tarefa_solta():
    l1 = L1Falso(vinculo_falha=["recusa"] * 10, processo_existe=False)
    ok, log_item, fingerprints = _linha_da_planilha(l1)
    assert ok is False and log_item.status == "FALHA"
    assert log_item.created_task_id is None
    assert l1.canceladas == [(456871, 3)]
    assert fingerprints == set()


# ── prazos iniciais ────────────────────────────────────────────────────────


def test_prazos_iniciais_reenvia_ate_vincular():
    l1 = L1Falso(vinculo_falha=["rede", "ok"])
    svc = PrazosIniciaisSchedulingService.__new__(PrazosIniciaisSchedulingService)
    svc._l1_client = l1
    svc._build_l1_task_payload = lambda **kw: dict(PAYLOAD)
    task_id = svc._create_task_in_legal_one(
        sugestao=SimpleNamespace(id=1, task_subtype_id=1245),
        intake=SimpleNamespace(id=9, lawsuit_id=80663, cnj_number="0806727-97.2026.8.10.0026"),
    )
    assert task_id == 456872
    assert l1.canceladas == [(456871, 3)]


# ── publicações: agendamento do grupo ──────────────────────────────────────


def _grupo(db_session):
    busca = PublicationSearch(date_from="2026-08-27T00:00:00Z", status="CONCLUIDO")
    db_session.add(busca)
    db_session.commit()
    rec = PublicationRecord(
        search_id=busca.id, legal_one_update_id=76417, status="CLASSIFICADO",
        is_duplicate=False, linked_lawsuit_id=80663, linked_office_id=61,
        linked_lawsuit_cnj="0806727-97.2026.8.10.0026",
    )
    db_session.add(rec)
    db_session.commit()
    fim = (datetime.now(timezone.utc) + timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def tarefa(sub, descricao):
        return {
            "description": descricao, "typeId": 33, "subTypeId": sub, "priority": "Normal",
            "startDateTime": fim, "endDateTime": fim, "publishDate": fim, "status": {"id": 0},
            "responsibleOfficeId": 61, "originOfficeId": 61,
            "participants": [{"contact": {"id": 61461}, "isResponsible": True,
                              "isExecuter": True, "isRequester": True}],
        }

    return rec, [
        tarefa(1241, "audiência de conciliação - 09/10/2026 15:40"),
        tarefa(1245, "contestação - audiência - 09/10/2026 15:40"),
    ]


def test_grupo_reenvia_sozinho_e_audita_as_tarefas_que_ficaram(db_session):
    l1 = L1Falso(vinculo_falha=["ok", "rede", "ok"])
    rec, payloads = _grupo(db_session)
    out = PublicationSearchService(db_session, l1).schedule_group(
        lawsuit_id=80663, payload_overrides=payloads, force_duplicate=True,
    )
    assert out["created_task_ids"] == [456871, 456873]
    assert l1.canceladas == [(456872, 3)]
    db_session.refresh(rec)
    assert rec.status == "AGENDADO"
    auditadas = sorted(a.created_task_id for a in db_session.query(PublicationTaskAudit).all())
    assert auditadas == [456871, 456873]


def test_grupo_e_tudo_ou_nada_quando_uma_tarefa_nao_vincula(db_session):
    # A contestação nunca vincula: a audiência, já vinculada, também é
    # cancelada — senão o banco volta atrás e ela sobra sem auditoria.
    l1 = L1Falso(vinculo_falha=["ok"] + ["recusa"] * 10)
    rec, payloads = _grupo(db_session)
    with pytest.raises(ValueError):
        PublicationSearchService(db_session, l1).schedule_group(
            lawsuit_id=80663, payload_overrides=payloads, force_duplicate=True,
        )
    assert len(l1.criadas) == 1 + vinculo.TENTATIVAS
    assert {t for t, _ in l1.canceladas} == set(l1.criadas)
    db_session.refresh(rec)
    assert rec.status == "CLASSIFICADO"
    assert db_session.query(PublicationTaskAudit).count() == 0
