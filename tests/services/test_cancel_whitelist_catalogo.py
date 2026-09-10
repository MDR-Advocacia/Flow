# -*- coding: utf-8 -*-
"""Combobox "Liberar subtipo" do cancelamento de duplicadas lista o catálogo do L1.

Caso real (10/09/2026): o operador criou no L1 o subtipo "Verificar Citação -
Banco Master" (id 1405) e foi liberar a regra de duplicadas ANTES de as tarefas
começarem a nascer — o uso certo, porque a madrugada só cancela o que existe.
O subtipo não aparecia: o catálogo do combobox lia só `perf_l1_tarefa`
(subtipos com tarefa no relatório do Minha Equipe), e ele tinha zero tarefas.

Os testes cobrem também as duas armadilhas medidas em produção ao trocar a
fonte: nome do catálogo com espaço no fim (a rotina casa pelo nome EXATO do
relatório — liberar o nome cru não cancelaria nada, calado) e o mesmo nome
repetido sob tipos pai diferentes (1.012 ativos, 776 nomes distintos).
"""
import pytest

from app.models.legal_one import LegalOneTaskSubType, LegalOneTaskType
from app.models.performance import PerfTarefa
from app.services.performance import cancel_duplicadas as cd


@pytest.fixture
def base(db_session):
    db_session.add_all([
        LegalOneTaskType(external_id=33, name="Banco Master", is_active=True),
        LegalOneTaskType(external_id=28, name="BB Autor", is_active=True),
    ])
    db_session.flush()
    db_session.add_all([
        LegalOneTaskSubType(external_id=1405, name="Verificar Citação - Banco Master",
                            parent_type_external_id=33, is_active=True),
        LegalOneTaskSubType(external_id=900, name="Agendar Prazos - Banco Master",
                            parent_type_external_id=33, is_active=True),
        LegalOneTaskSubType(external_id=901, name="Agravo de Petição (art. 897, b, CLT) ",
                            parent_type_external_id=28, is_active=True),
        LegalOneTaskSubType(external_id=902, name="Manifestação", parent_type_external_id=33, is_active=True),
        LegalOneTaskSubType(external_id=903, name="Manifestação", parent_type_external_id=28, is_active=True),
        LegalOneTaskSubType(external_id=904, name="Subtipo Aposentado", parent_type_external_id=28, is_active=False),
    ])
    for _ in range(5):
        db_session.add(PerfTarefa(subtipo="Agendar Prazos - Banco Master"))
    for _ in range(2):
        db_session.add(PerfTarefa(subtipo="Agravo de Petição (art. 897, b, CLT)"))
    db_session.add(PerfTarefa(subtipo="Subtipo Só No Relatório"))
    db_session.commit()
    return db_session


def _nomes(lista):
    return [c["subtipo"] for c in lista]


def test_subtipo_recem_criado_sem_tarefa_aparece_na_busca(base):
    """O caso do print: zero tarefas, e mesmo assim liberável."""
    assert cd.subtipos_catalogo(base, "verificar cita") == [
        {"subtipo": "Verificar Citação - Banco Master", "volume": 0}
    ]


def test_volume_vem_do_relatorio_e_ordena(base):
    lista = cd.subtipos_catalogo(base)
    assert lista[0] == {"subtipo": "Agendar Prazos - Banco Master", "volume": 5}
    assert {"subtipo": "Verificar Citação - Banco Master", "volume": 0} in lista


def test_nome_com_espaco_no_fim_vira_o_nome_do_relatorio(base):
    assert cd.subtipos_catalogo(base, "agravo") == [
        {"subtipo": "Agravo de Petição (art. 897, b, CLT)", "volume": 2}
    ]


def test_nome_repetido_entre_tipos_pai_aparece_uma_vez(base):
    assert _nomes(cd.subtipos_catalogo(base, "manifesta")).count("Manifestação") == 1


def test_inativo_sem_tarefa_fica_de_fora_mas_o_relatorio_continua_valendo(base):
    nomes = _nomes(cd.subtipos_catalogo(base))
    assert "Subtipo Aposentado" not in nomes
    assert "Subtipo Só No Relatório" in nomes, "regressão: o comportamento antigo é subconjunto"


def test_o_nome_liberado_e_o_que_a_rotina_casa(base):
    """A trava que importa: o nome escolhido no combobox tem que bater com
    perf_l1_tarefa.subtipo, que é por onde a madrugada encontra as duplicadas."""
    escolhido = cd.subtipos_catalogo(base, "agravo")[0]["subtipo"]
    cd.whitelist_add(base, escolhido, por="teste")
    assert base.query(PerfTarefa).filter(PerfTarefa.subtipo == escolhido).count() == 2
