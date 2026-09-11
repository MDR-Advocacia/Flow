# -*- coding: utf-8 -*-
"""Fluxo Embargos à Execução — templates e disparo das tarefas do incidente.

Decisão do operador (11/09/2026): o incidente é cadastrado À MÃO no L1 e no
portal do BB; o Flow localiza o incidente ("Proc - X/00N") e dispara as tarefas
dos templates configuráveis, vinculadas ao INCIDENTE (como a 476538 → 98774).
"""
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.models.embargos_execucao import (
    DISPARO_CRIADA,
    DISPARO_FALHA,
    ESTADO_CONCLUIDO,
    ESTADO_CONFIRMADO,
    ESTADO_MONITORANDO,
    RESP_ADVOGADO_CARD,
    RESP_FIXO,
    EmbCandidato,
    EmbExecucao,
    EmbTarefaDisparo,
)
from app.models.legal_one import LegalOneTaskSubType, LegalOneTaskType, LegalOneUser
from app.services.embargos_execucao import service, tarefas

HOJE = date(2026, 9, 14)
SUBTIPO_IMPUGNACAO = 1295


@pytest.fixture
def catalogo(db_session):
    db_session.add_all([
        LegalOneTaskType(external_id=28, name="Ativos e BB - Recuperação de Crédito"),
        LegalOneTaskSubType(external_id=SUBTIPO_IMPUGNACAO, parent_type_external_id=28,
                            name="Impugnação aos Embargos à Execução - Ativos e BB Autor"),
        LegalOneUser(external_id=56388, name="Andrehelly Amanda Oleinik dos Santos", email="andrehelly@teste", is_active=True),
        LegalOneUser(external_id=50001, name="Izabele Roberta da Cruz Bezerra", email="izabele@teste", is_active=True),
    ])
    db_session.commit()


def _confirmado(db, *, responsavel="Izabele Roberta da Cruz Bezerra", estado=ESTADO_CONFIRMADO, pasta="Proc - 0068694"):
    exe = EmbExecucao(
        pasta=pasta, cnj="7029693-10.2026.8.22.0001", cnj_digitos="70296931020268220001",
        npj="2025/0342918-000", cliente="BB", origem="RELATORIO", data_ajuizamento=date(2026, 5, 22),
        estado=estado, dias_uteis_janela=15, consultas_feitas=1, partes_status="OK", partes_tentativas=0,
        responsavel_nome=responsavel,
    )
    cand = EmbCandidato(
        cnj="7050390-52.2026.8.22.0001", cnj_digitos="70503905220268220001",
        data_ajuizamento=datetime(2026, 9, 9, tzinfo=timezone.utc), nivel="CONFIRMADO_DJEN",
        decisao="CONFIRMADO", djen_nomes_casados=["FULANO DE TAL"],
        distribuicao_dependencia=True, peticao_mesmo_dia=False,
    )
    exe.candidatos.append(cand)
    db.add(exe)
    db.commit()
    exe.confirmado_candidato_id = cand.id
    db.commit()
    return exe


class L1Falso:
    base_url = "https://l1"

    def __init__(self, incidentes=None, abertas=None, recusar=False):
        self.incidentes = incidentes if incidentes is not None else [{
            "id": 98774, "folder": "Proc - 0068694/001", "title": "EMBARGOS À EXECUÇÃO",
            "identifierNumber": "70503905220268220001", "responsibleOfficeId": 22,
        }]
        self.abertas = abertas or {}
        self.recusar = recusar
        self.criadas, self.vinculos, self.filtros = [], [], []
        self._proximo = 500000

    def _request_with_retry(self, method, url, params=None):
        self.filtros.append(params["$filter"])
        return SimpleNamespace(json=lambda: {"value": self.incidentes})

    def find_tasks_for_lawsuit(self, lawsuit_id, subtype_id=None, status_ids=None, top=50):
        return self.abertas.get(subtype_id, [])

    def create_task(self, payload):
        if self.recusar:
            return None
        self._proximo += 1
        self.criadas.append(payload)
        return {"id": self._proximo}

    def link_task_to_lawsuit(self, task_id, relacao):
        self.vinculos.append((task_id, relacao))
        return True

    def format_last_create_task_error(self):
        return "subtipo inativo no L1"


def _template(db, **extra):
    dados = {
        "nome": "Impugnar embargos", "subtipo_id": SUBTIPO_IMPUGNACAO, "responsavel_modo": RESP_FIXO,
        "responsavel_contact_id": 56388, "prazo_dias_uteis": 5, "prioridade": "Normal",
        "descricao_template": "Impugnar embargos {cnj_embargos} de {embargante} — execução {pasta_execucao}",
    }
    dados.update(extra)
    return tarefas.salvar_template(db, dados)


def test_template_valida_e_guarda_o_tipo_do_subtipo(db_session, catalogo):
    t = _template(db_session)
    assert t["tipo_id"] == 28 and t["subtipo_nome"].startswith("Impugnação")
    assert t["responsavel_nome"] == "Andrehelly Amanda Oleinik dos Santos"
    with pytest.raises(ValueError):
        _template(db_session, descricao_template="  ")
    with pytest.raises(ValueError):
        _template(db_session, subtipo_id=999999)
    with pytest.raises(ValueError):
        _template(db_session, responsavel_contact_id=None)


def test_renderizar_mantem_chave_desconhecida_e_nao_quebra_com_chave_torta():
    assert tarefas.renderizar("CNJ {cnj_embargos} {xyz}", {"cnj_embargos": "1"}) == "CNJ 1 {xyz}"
    assert tarefas.renderizar("abre { sem fechar", {}) == "abre { sem fechar"


def test_previa_localiza_incidente_resolve_advogado_e_monta_descricao(db_session, catalogo):
    exe = _confirmado(db_session)
    _template(db_session, responsavel_modo=RESP_ADVOGADO_CARD, responsavel_contact_id=None)
    l1 = L1Falso()
    p = tarefas.previa(db_session, exe.id, l1, hoje=HOJE)
    assert l1.filtros == ["startswith(folder,'Proc - 0068694/')"]
    assert p["incidente"]["id"] == 98774 and exe.incidente_folder == "Proc - 0068694/001"
    t = p["tarefas"][0]
    assert t["responsavel_contact_id"] == 50001 and t["erro"] is None
    assert t["prazo"] == "2026-09-21"
    assert t["descricao"] == "Impugnar embargos 7050390-52.2026.8.22.0001 de FULANO DE TAL — execução Proc - 0068694"


def test_disparo_cria_no_incidente_fecha_o_fluxo_e_nao_repete(db_session, catalogo):
    exe = _confirmado(db_session)
    _template(db_session)
    _template(db_session, nome="Monitorar", responsavel_modo=RESP_ADVOGADO_CARD, responsavel_contact_id=None, prazo_dias_uteis=0)
    l1 = L1Falso()
    r = tarefas.disparar(db_session, exe.id, l1, hoje=HOJE)
    assert r["criadas"] == 2 and exe.estado == ESTADO_CONCLUIDO
    assert [rel for _, rel in l1.vinculos] == [{"linkType": "Litigation", "linkId": 98774}] * 2
    assert l1.criadas[0]["endDateTime"] == "2026-09-21T18:00:00-03:00"
    assert l1.criadas[0]["responsibleOfficeId"] == 22 and l1.criadas[0]["typeId"] == 28
    assert l1.criadas[1]["participants"][0]["contact"]["id"] == 50001

    r2 = tarefas.disparar(db_session, exe.id, l1, hoje=HOJE)
    assert r2["criadas"] == 0 and r2["puladas"] == 2 and len(l1.criadas) == 2


def test_disparo_exige_vinculo_confirmado_e_incidente_cadastrado(db_session, catalogo):
    _template(db_session)
    monitorando = _confirmado(db_session, estado=ESTADO_MONITORANDO, pasta="Proc - 0000001")
    with pytest.raises(ValueError, match="Confirme o vínculo"):
        tarefas.disparar(db_session, monitorando.id, L1Falso(), hoje=HOJE)
    exe = _confirmado(db_session)
    with pytest.raises(ValueError, match="Cadastre o incidente"):
        tarefas.disparar(db_session, exe.id, L1Falso(incidentes=[]), hoje=HOJE)


def test_falha_de_responsavel_nao_fecha_e_tarefa_aberta_nao_duplica(db_session, catalogo):
    exe = _confirmado(db_session, responsavel="Pessoa Que Saiu Do Escritorio")
    _template(db_session, nome="Pelo advogado", responsavel_modo=RESP_ADVOGADO_CARD, responsavel_contact_id=None)
    _template(db_session, nome="Fixa")
    l1 = L1Falso(abertas={SUBTIPO_IMPUGNACAO: [{"id": 471939, "statusId": 0}]})
    r = tarefas.disparar(db_session, exe.id, l1, hoje=HOJE)
    assert r == {"criadas": 1, "falhas": 1, "puladas": 0, "estado": ESTADO_CONFIRMADO}
    assert l1.criadas == []
    status = {d.template_nome: (d.status, d.l1_task_id) for d in db_session.query(EmbTarefaDisparo)}
    assert status == {"Pelo advogado": (DISPARO_FALHA, None), "Fixa": (DISPARO_CRIADA, 471939)}


def test_l1_recusando_a_criacao_registra_o_motivo(db_session, catalogo):
    exe = _confirmado(db_session)
    _template(db_session)
    tarefas.disparar(db_session, exe.id, L1Falso(recusar=True), hoje=HOJE)
    d = db_session.query(EmbTarefaDisparo).one()
    assert d.status == DISPARO_FALHA and d.erro == "subtipo inativo no L1"


def test_incidente_com_titulo_vazio_e_reconhecido_pelo_tipo_ou_pelo_cnj(db_session, catalogo):
    # Caso real (11/09/2026): "Proc - 0068696/001" está no L1 com title nulo.
    from app.services.embargos_execucao.monitor import incidentes_de_embargos

    sem_titulo = {"id": 98298, "folder": "Proc - 0068696/001", "title": None,
                  "identifierNumber": "7011603-39.2026.8.22.0005", "actionTypeId": None}
    pelo_tipo = {**sem_titulo, "id": 1, "identifierNumber": None, "actionTypeId": 67}
    agravo = {"id": 2, "folder": "Proc - 0068696/002", "title": "AGRAVO DE INSTRUMENTO",
              "identifierNumber": "0000001-00.2026.8.22.0000", "actionTypeId": 12}
    assert incidentes_de_embargos([sem_titulo, agravo], ["70116033920268220005"]) == [sem_titulo]
    assert incidentes_de_embargos([pelo_tipo, agravo]) == [pelo_tipo]

    exe = _confirmado(db_session, pasta="Proc - 0068696")
    exe.candidatos[0].cnj, exe.candidatos[0].cnj_digitos = "7011603-39.2026.8.22.0005", "70116033920268220005"
    db_session.commit()
    _template(db_session)
    p = tarefas.previa(db_session, exe.id, L1Falso(incidentes=[sem_titulo, agravo]), hoje=HOJE)
    assert p["incidente"]["id"] == 98298 and p["incidente"]["folder"] == "Proc - 0068696/001"


def test_fila_de_partes_por_execucao_e_agenda_de_madrugada(db_session):
    a = _confirmado(db_session, pasta="Proc - 0000010")
    b = _confirmado(db_session, pasta="Proc - 0000011")
    for exe in (a, b):
        exe.partes_status = "PENDENTE"
    db_session.commit()
    assert [e.id for e in service.fila_partes(db_session, 10, execucao_id=b.id)] == [b.id]
    assert settings.embargos_execucao_relatorio_horarios == "7"
    assert settings.embargos_execucao_partes_hora == "3"
