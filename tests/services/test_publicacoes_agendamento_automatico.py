# -*- coding: utf-8 -*-
"""Agendamento automático com certeza — dormente (regras do operador, 14/09/2026).

Protege:
  1. nasce DESLIGADO — o job não faz nada até o setting ligar;
  2. só as classificações liberadas; "Para Análise" nunca (principal, extra ou configurada);
  3. tarefa de classificação extra deixa a publicação com a equipe, sem reavaliar a
     cada rodada; extra sem template não atrapalha;
  4. subtipo aberto no L1 não é recriado; tudo aberto = tratada apontando as existentes;
  5. conclusão vencida é recalculada a partir de hoje, com código curto na auditoria;
  6. simular não grava nem cria tarefa;
  7. proposta nunca montada não ganha marca (a repassada de propostas depende disso);
  8. erro tenta de novo, com teto.
"""
from datetime import datetime
from itertools import count
from zoneinfo import ZoneInfo

import pytest

from app.models.legal_one import LegalOneOffice, LegalOneTaskSubType, LegalOneTaskType, LegalOneUser
from app.models.publication_search import PublicationRecord, PublicationSearch
from app.models.publication_task_audit import PublicationTaskAudit
from app.models.task_template import TaskTemplate
from app.services import publication_agendamento_automatico as auto
from app.services.prazos_iniciais.prazo_calculator import add_business_days
from app.services.publication_search_service import PublicationSearchService

SENTENCA = "Sentença e Extinção"
IMPROCEDENTE = "Sentença Improcedente"
TRANSITO = "Trânsito em Julgado e Arquivamento"
ARQUIVAMENTO = "Arquivamento Definitivo"
_IDS_DE_ATUALIZACAO = count(71000)


class _L1:
    def __init__(self, recusa=False):
        self.criadas = []
        self.recusa = recusa
        self._proximo = 800000

    def create_task(self, payload):
        if self.recusa:
            return None
        self._proximo += 1
        self.criadas.append(payload["subTypeId"])
        return {"id": self._proximo}

    def format_last_create_task_error(self):
        return "O Legal One recusou a tarefa." if self.recusa else None

    def link_task_to_lawsuit(self, task_id, link_payload):
        return True


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.setattr(auto, "_tomar_trava", lambda: (None, True))
    monkeypatch.setattr(auto, "_soltar_trava", lambda conn: None)
    # A taxonomia real mora no banco da aplicação; aqui a classificação já é válida.
    monkeypatch.setattr(
        "app.services.classifier.taxonomy.repair_classification",
        lambda cat, sub, **kwargs: (cat, sub),
    )


@pytest.fixture
def config(monkeypatch):
    valores = {}
    monkeypatch.setattr(
        "app.services.app_settings.get_setting",
        lambda chave, default=None: valores.get(chave, default),
    )
    return valores


@pytest.fixture
def abertas_no_l1(monkeypatch):
    """{lawsuit_id: {subtipo: task_id}} — o que o L1 responde como já aberto."""
    por_processo = {}

    def falso(self, lawsuit_id, subtype_ids):
        mapa = por_processo.get(lawsuit_id, {})
        dup = {s: [{"task_id": t}] for s, t in mapa.items() if s in subtype_ids}
        return {"duplicates_by_subtype": dup, "total_duplicates": len(dup)}

    monkeypatch.setattr(PublicationSearchService, "check_duplicates_for_lawsuit", falso)
    return por_processo


def _catalogo(db):
    db.add_all([
        LegalOneOffice(external_id=23, name="Réu", is_active=True),
        LegalOneOffice(external_id=22, name="Autor", is_active=True),
        LegalOneUser(external_id=10, name="Responsável", email="resp@exemplo.test", is_active=True),
        LegalOneTaskType(external_id=15, name="BB Defesa", is_active=True),
        LegalOneTaskSubType(external_id=900, name="Inclusão de Resultado de Improcedência",
                            parent_type_external_id=15, is_active=True),
        LegalOneTaskSubType(external_id=901, name="Acompanhar Trânsito", parent_type_external_id=15, is_active=True),
        LegalOneTaskSubType(external_id=902, name="Análise de Encerramento", parent_type_external_id=15, is_active=True),
    ])
    busca = PublicationSearch(date_from="2026-09-11T00:00:00Z", status="CONCLUIDO")
    db.add(busca)
    db.commit()
    return busca


def _template(db, categoria, subcategoria, subtipo, office=23):
    t = TaskTemplate(
        name=f"{subcategoria} -> {subtipo}", category=categoria, subcategory=subcategoria,
        office_external_id=office, task_subtype_external_id=subtipo, responsible_user_external_id=10,
        priority="Normal", due_business_days=5, due_date_reference="today",
    )
    db.add(t)
    db.commit()
    return t


def _publicacao(db, busca, *, categoria=SENTENCA, subcategoria=IMPROCEDENTE, office=23, processo=501,
                extras=()):
    """Classificada e com a proposta montada pelo builder de verdade."""
    rec = PublicationRecord(
        search_id=busca.id, legal_one_update_id=next(_IDS_DE_ATUALIZACAO), status="CLASSIFICADO",
        is_duplicate=False, linked_lawsuit_id=processo, linked_office_id=office,
        linked_lawsuit_cnj="0800001-00.2026.8.14.0001", publication_date="2026-09-11T00:00:00Z",
        description="Sentença.", category=categoria, subcategory=subcategoria,
        classifications=[{"categoria": categoria, "subcategoria": subcategoria}]
        + [{"categoria": c, "subcategoria": s} for c, s in extras],
    )
    db.add(rec)
    db.commit()
    PublicationSearchService(db, _L1())._build_task_proposals([rec], skip_responsible_lookup=True)
    db.refresh(rec)
    return rec


def _auditoria(db, rec):
    return (
        db.query(PublicationTaskAudit)
        .filter(PublicationTaskAudit.publication_record_id == rec.id)
        .order_by(PublicationTaskAudit.id)
        .all()
    )


# ── interruptor e regras ───────────────────────────────────────────────────


def test_nasce_dormente_e_nao_faz_nada(db_session, config, abertas_no_l1, monkeypatch):
    busca = _catalogo(db_session)
    _template(db_session, SENTENCA, IMPROCEDENTE, 900)
    rec = _publicacao(db_session, busca)
    l1 = _L1()

    assert auto.executar_rodada(db_session, client=l1) == {"executou": False, "motivo": "desligado"}
    monkeypatch.setattr("app.db.session.SessionLocal", lambda: pytest.fail("dormente não abre sessão"))
    auto._job_tick()

    db_session.refresh(rec)
    assert rec.status == "CLASSIFICADO" and l1.criadas == []


def test_regras_padrao_sao_as_10_do_bb_reu_sem_para_analise(config):
    regras = auto.regras()
    assert set(regras) == {23}
    assert len(regras[23]) == 10
    assert not any(cat.startswith("para analise") or sub.startswith("para analise") for cat, sub in regras[23])
    # travessão e hífen valem o mesmo
    assert (
        auto._chave("Recursos e Julgamentos em 2º Grau"),
        auto._chave("Acórdão / Decisão Monocrática - Não Provido"),
    ) in regras[23]


def test_setting_malformado_desliga_tudo_e_para_analise_nao_entra_nem_configurado(config):
    config[auto.SETTING_REGRAS] = "{isso não é json"
    assert auto.regras() == {}

    config[auto.SETTING_REGRAS] = (
        '{"23": [["Sentença e Extinção", "Sentença Improcedente"], ["Para Análise", "-"]]}'
    )
    assert auto.regras() == {23: frozenset({(auto._chave(SENTENCA), auto._chave(IMPROCEDENTE))})}


def test_para_analise_e_classificacao_nao_liberada_ficam_com_a_equipe(db_session, config, abertas_no_l1):
    config[auto.SETTING_ATIVO] = "true"
    busca = _catalogo(db_session)
    _template(db_session, SENTENCA, IMPROCEDENTE, 900)
    _template(db_session, SENTENCA, "Sentença Procedente", 901)
    _template(db_session, SENTENCA, IMPROCEDENTE, 900, office=22)
    recs = [
        _publicacao(db_session, busca, categoria="Para Análise", subcategoria="-"),
        _publicacao(db_session, busca, extras=[("Para Análise", "-")]),
        _publicacao(db_session, busca, subcategoria="Sentença Procedente"),
        _publicacao(db_session, busca, office=22),
    ]
    l1 = _L1()

    saida = auto.executar_rodada(db_session, client=l1)

    assert saida["candidatos"] == 0 and l1.criadas == []
    for rec in recs:
        db_session.refresh(rec)
        assert rec.status == "CLASSIFICADO"


# ── agendamento ────────────────────────────────────────────────────────────


def test_agenda_classificacao_liberada_com_as_tarefas_da_proposta(db_session, config, abertas_no_l1):
    config[auto.SETTING_ATIVO] = "true"
    busca = _catalogo(db_session)
    _template(db_session, SENTENCA, IMPROCEDENTE, 900)
    _template(db_session, SENTENCA, IMPROCEDENTE, 901)
    rec = _publicacao(db_session, busca)
    l1 = _L1()

    saida = auto.executar_rodada(db_session, client=l1)

    assert saida["resumo"] == {auto.AGENDADA: 1}
    assert sorted(l1.criadas) == [900, 901]
    db_session.refresh(rec)
    assert rec.status == "AGENDADO"
    assert rec.scheduled_by_name == auto.NOME_DO_ROBO and rec.scheduled_by_user_id is None
    assert len(_auditoria(db_session, rec)) == 2


def test_tarefa_de_classificacao_extra_deixa_com_a_equipe_e_nao_reavalia(db_session, config, abertas_no_l1):
    config[auto.SETTING_ATIVO] = "true"
    busca = _catalogo(db_session)
    _template(db_session, SENTENCA, IMPROCEDENTE, 900)
    _template(db_session, TRANSITO, ARQUIVAMENTO, 902)
    rec = _publicacao(db_session, busca, extras=[(TRANSITO, ARQUIVAMENTO)])
    l1 = _L1()

    primeira = auto.executar_rodada(db_session, client=l1)
    segunda = auto.executar_rodada(db_session, client=l1)

    assert primeira["resumo"] == {"tarefa_de_outra_classificacao": 1}
    assert segunda["candidatos"] == 0, "mesma classificação e mesma proposta: não reavalia"
    assert l1.criadas == []
    db_session.refresh(rec)
    assert rec.status == "CLASSIFICADO"
    assert rec.raw_relationships[auto.MARCA]["resultado"] == "tarefa_de_outra_classificacao"


def test_classificacao_extra_sem_template_nao_impede(db_session, config, abertas_no_l1):
    config[auto.SETTING_ATIVO] = "true"
    busca = _catalogo(db_session)
    _template(db_session, SENTENCA, IMPROCEDENTE, 900)
    _publicacao(db_session, busca, extras=[(TRANSITO, ARQUIVAMENTO)])
    l1 = _L1()

    assert auto.executar_rodada(db_session, client=l1)["resumo"] == {auto.AGENDADA: 1}
    assert l1.criadas == [900]


def test_subtipo_ja_aberto_no_l1_nao_e_recriado(db_session, config, abertas_no_l1):
    config[auto.SETTING_ATIVO] = "true"
    busca = _catalogo(db_session)
    _template(db_session, SENTENCA, IMPROCEDENTE, 900)
    _template(db_session, SENTENCA, IMPROCEDENTE, 901)
    rec = _publicacao(db_session, busca)
    abertas_no_l1[501] = {900: 555}
    l1 = _L1()

    saida = auto.executar_rodada(db_session, client=l1)

    assert saida["resumo"] == {auto.AGENDADA: 1}
    assert l1.criadas == [901]
    (linha,) = _auditoria(db_session, rec)
    assert linha.tarefa_removida_motivo == auto.MOTIVO_JA_EXISTE


def test_todas_abertas_marca_tratada_apontando_as_existentes(db_session, config, abertas_no_l1):
    config[auto.SETTING_ATIVO] = "true"
    busca = _catalogo(db_session)
    _template(db_session, SENTENCA, IMPROCEDENTE, 900)
    _template(db_session, SENTENCA, IMPROCEDENTE, 901)
    rec = _publicacao(db_session, busca)
    abertas_no_l1[501] = {900: 555, 901: 556}
    l1 = _L1()

    saida = auto.executar_rodada(db_session, client=l1)

    assert saida["resumo"] == {auto.TRATADA: 1}
    assert l1.criadas == []
    db_session.refresh(rec)
    assert rec.status == "AGENDADO" and rec.scheduled_by_name == auto.NOME_DO_ROBO
    linhas = _auditoria(db_session, rec)
    assert sorted(a.created_task_id for a in linhas) == [555, 556]
    assert all("tarefa_existente" in (a.system_adjustments or {}) for a in linhas)
    assert all(a.tarefa_removida_motivo == auto.MOTIVO_JA_EXISTE for a in linhas)


def test_conclusao_vencida_e_recalculada_a_partir_de_hoje(db_session, config, abertas_no_l1):
    config[auto.SETTING_ATIVO] = "true"
    busca = _catalogo(db_session)
    _template(db_session, SENTENCA, IMPROCEDENTE, 900)
    rec = _publicacao(db_session, busca)
    raw = dict(rec.raw_relationships)
    proposta = dict(raw["_proposed_task"])
    proposta["payload"] = {**proposta["payload"], "startDateTime": "2026-09-01T02:59:59Z",
                           "endDateTime": "2026-09-01T02:59:59Z"}
    rec.raw_relationships = {**raw, "_proposed_task": proposta, "_proposed_tasks": [proposta]}
    db_session.commit()

    auto.executar_rodada(db_session, client=_L1())

    (linha,) = _auditoria(db_session, rec)
    hoje = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    assert linha.data_troca_motivo == auto.MOTIVO_DATA
    assert auto._dia_brt(linha.sent_payload["endDateTime"]) == add_business_days(hoje, 5)


def test_simular_nao_grava_nem_cria_tarefa(db_session, config, abertas_no_l1):
    busca = _catalogo(db_session)
    _template(db_session, SENTENCA, IMPROCEDENTE, 900)
    rec = _publicacao(db_session, busca)
    l1 = _L1()

    saida = auto.executar_rodada(db_session, simular=True, respeitar_interruptor=False, client=l1)

    assert saida["resumo"] == {"agendaria": 1}
    assert l1.criadas == []
    db_session.refresh(rec)
    assert rec.status == "CLASSIFICADO" and auto.MARCA not in rec.raw_relationships


# ── marcas ─────────────────────────────────────────────────────────────────


def test_proposta_nunca_montada_nao_ganha_marca(db_session, config, abertas_no_l1):
    """A repassada de propostas acha o registro por raw_relationships não-dict."""
    config[auto.SETTING_ATIVO] = "true"
    busca = _catalogo(db_session)
    rec = PublicationRecord(
        search_id=busca.id, legal_one_update_id=next(_IDS_DE_ATUALIZACAO), status="CLASSIFICADO",
        is_duplicate=False, linked_lawsuit_id=503, linked_office_id=23,
        publication_date="2026-09-11T00:00:00Z", category=SENTENCA, subcategory=IMPROCEDENTE,
        raw_relationships=[{"relacionamento": 1}],
    )
    db_session.add(rec)
    db_session.commit()

    saida = auto.executar_rodada(db_session, client=_L1())

    assert saida["resumo"] == {"sem_proposta": 1}
    db_session.refresh(rec)
    assert rec.raw_relationships == [{"relacionamento": 1}]


def test_erro_tenta_de_novo_ate_o_teto(db_session, config, abertas_no_l1, monkeypatch):
    config[auto.SETTING_ATIVO] = "true"
    busca = _catalogo(db_session)
    _template(db_session, SENTENCA, IMPROCEDENTE, 900)
    rec = _publicacao(db_session, busca)
    l1 = _L1(recusa=True)
    # O db_session do conftest amarra a sessão numa transação externa: o rollback
    # dele desfaz o teste inteiro. Na sessão real o rollback descarta só o que não
    # foi commitado — aqui, nada: o L1 recusou antes de qualquer escrita.
    monkeypatch.setattr(db_session, "rollback", db_session.expire_all)

    resumos = [auto.executar_rodada(db_session, client=l1)["resumo"] for _ in range(auto.TENTATIVAS + 1)]

    assert resumos == [{"erro": 1}] * auto.TENTATIVAS + [{}]
    db_session.refresh(rec)
    assert rec.status == "CLASSIFICADO"
    assert rec.raw_relationships[auto.MARCA]["tentativas"] == auto.TENTATIVAS


def test_marcador_maior_que_a_coluna_da_auditoria_e_barrado_antes_do_l1():
    auto.conferir_marcadores([{"_data_troca_motivo": auto.MOTIVO_DATA,
                               "_tarefa_removida_motivo": auto.MOTIVO_JA_EXISTE}])
    with pytest.raises(ValueError):
        auto.conferir_marcadores([{"_tarefa_removida_motivo": "x" * 41}])
