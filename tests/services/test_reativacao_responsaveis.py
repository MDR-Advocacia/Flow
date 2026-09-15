# -*- coding: utf-8 -*-
"""Reativar pastas e agendar tarefa — responsável nulo (15/09/2026).

O seletor de responsáveis do modal usa o `id` de /task-templates/meta/users, que
só devolvia `external_id`: o id ia como null e a reativação estourava 500 em
int(None). Caso real: 0852179-88.2025.8.12.0001 e 0805073-12.2025.8.12.0008
(Banco Master, pastas arquivadas reenviadas pelo cliente).
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import distribuidos_bb as ep
from app.api.v1.endpoints.task_templates import list_users
from app.models.distribuidos_bb import REATIV_PENDENTE, BbProcesso
from app.models.legal_one import LegalOneUser

ADMIN = SimpleNamespace(id=1, role="admin")


@pytest.fixture
def cenario(db_session):
    enzo = LegalOneUser(external_id=9440, name="Enzo Teste", email="enzo.teste@example.com", is_active=True)
    outra = LegalOneUser(external_id=9441, name="Outra Pessoa", email="outra.teste@example.com", is_active=True)
    db_session.add_all([enzo, outra])
    db_session.flush()
    procs = [
        BbProcesso(
            cliente="MASTER", cnj=cnj, fingerprint=f"master:cnj:{cnj}", l1_lawsuit_id=pasta,
            l1_status_id=4, reativacao_status=REATIV_PENDENTE, responsavel_user_id=enzo.id,
        )
        for cnj, pasta in (("0852179-88.2025.8.12.0001", 67089), ("0805073-12.2025.8.12.0008", 67076))
    ]
    db_session.add_all(procs)
    db_session.commit()
    return enzo, outra, procs


def test_lista_de_usuarios_do_seletor_traz_o_id_interno(db_session, cenario):
    enzo, _, _ = cenario

    item = next(u for u in list_users(db=db_session) if u["external_id"] == 9440)

    assert item == {"id": enzo.id, "external_id": 9440, "name": "Enzo Teste", "email": "enzo.teste@example.com"}


def test_preview_com_o_id_do_seletor_manda_a_tarefa_pra_pessoa_escolhida(db_session, cenario):
    _, outra, procs = cenario

    r = ep.preview_reativacoes(
        payload={"processo_ids": [p.id for p in procs], "responsavel_ids": [outra.id], "dividir_igual": False},
        db=db_session, current_user=ADMIN,
    )

    assert r["total"] == 2 and r["sem_responsavel"] == 0
    assert r["por_responsavel"] == [{"responsavel_id": outra.id, "responsavel_nome": "Outra Pessoa", "total": 2}]
    assert {i["responsavel_external_id"] for i in r["itens"]} == {9441}


@pytest.mark.parametrize("endpoint", ["preview", "executar"])
def test_responsavel_nulo_vira_mensagem_e_nao_erro_500(db_session, cenario, endpoint):
    _, _, procs = cenario
    payload = {
        "processo_ids": [p.id for p in procs], "responsavel_ids": [None], "dividir_igual": True,
        "config": {"subtype_id": 1}, "dry_run": True,
    }
    funcao = ep.preview_reativacoes if endpoint == "preview" else ep.executar_reativacoes

    with pytest.raises(HTTPException) as erro:
        funcao(payload=payload, db=db_session, current_user=ADMIN)

    assert erro.value.status_code == 422
    assert "responsáveis" in erro.value.detail


def test_processo_nao_numerico_na_dispensa_vira_mensagem(db_session, cenario):
    with pytest.raises(HTTPException) as erro:
        ep.dispensar_reativacoes(payload={"processo_ids": ["abc"]}, db=db_session, current_user=ADMIN)

    assert erro.value.status_code == 422 and "processos" in erro.value.detail
