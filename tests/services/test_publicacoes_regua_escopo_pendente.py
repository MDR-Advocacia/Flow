# -*- coding: utf-8 -*-
"""A régua de envelhecimento abre exatamente o que ela conta.

Reportado pelo operador em 08/09/2026, com print: o chip "+30d · 9" abria uma
lista com dezenas de milhares de publicações, quase todas já IGNORADAS —
"essas publicações foram marcadas com IGNORADAS, ou seja, elas estão
tratadas".

A causa era uma discordância de universo entre as duas pontas da mesma régua:

    número do chip  →  aging_summary()  →  conta só NOVO/CLASSIFICADO/ERRO
    clique no chip  →  listagem         →  mandava só o corte de idade,
                                           com o STATUS em "Todos"

Nove pendentes viravam 48.874 na tela. Alarme que promete uma coisa e mostra
outra é pior que alarme nenhum, porque ensina o operador a não olhar.

O contrato testado aqui é esse: *filtro de envelhecimento implica fila
pendente* — e vale no backend, então vale pra tela clássica e pra Triagem sem
depender de nenhuma das duas lembrar de mandar o status.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.publication_search import (
    PublicationRecord,
    PublicationSearch,
    RECORD_STATUS_CLASSIFIED,
    RECORD_STATUS_IGNORED,
    RECORD_STATUS_NEW,
    RECORD_STATUS_SCHEDULED,
    SEARCH_STATUS_COMPLETED,
)
from app.services.publication_search_service import PublicationSearchService


VELHO = 40          # dias na fila — cai em qualquer bucket da régua
NOVINHO = 1


@pytest.fixture
def cenario(db_session):
    """9 pendentes velhas afogadas em 200 já tratadas, na mesma idade.

    A proporção é a do incidente: o que envelheceu e foi tratado é ordens de
    grandeza maior que o que envelheceu e ficou parado.
    """
    busca = PublicationSearch(
        status=SEARCH_STATUS_COMPLETED, date_from="2026-07-01", date_to="2026-09-08"
    )
    db_session.add(busca)
    db_session.commit()

    agora = datetime.now(timezone.utc)
    uid = 0

    def _add(status, dias):
        nonlocal uid
        uid += 1
        db_session.add(PublicationRecord(
            search_id=busca.id,
            legal_one_update_id=900000 + uid,
            status=status,
            linked_office_id=61,
            created_at=agora - timedelta(days=dias),
        ))

    for _ in range(5):
        _add(RECORD_STATUS_NEW, VELHO)
    for _ in range(4):
        _add(RECORD_STATUS_CLASSIFIED, VELHO)
    for _ in range(150):
        _add(RECORD_STATUS_IGNORED, VELHO)
    for _ in range(50):
        _add(RECORD_STATUS_SCHEDULED, VELHO)
    # ruído recente: não pode aparecer em nenhum recorte da régua
    for _ in range(7):
        _add(RECORD_STATUS_NEW, NOVINHO)
    db_session.commit()
    return busca


def _svc(db):
    """Nada aqui toca o L1: a régua é 100% consulta ao banco."""
    return PublicationSearchService(db, client=None)


def _conta(db, **kwargs):
    return _svc(db)._base_publication_query(**kwargs).count()


def test_chip_de_idade_abre_o_mesmo_numero_que_mostra(db_session, cenario):
    """O caso do print: o número e a lista têm que bater."""
    resumo = _svc(db_session).aging_summary()

    prometido = resumo["faixas"]["d31_mais"]
    entregue = _conta(db_session, idade_min_dias=31)

    assert prometido == 9, "o cenário precisa ter 9 pendentes velhas"
    assert entregue == prometido, (
        "o chip prometeu %d e a lista abriu %d — é o bug de 08/09"
        % (prometido, entregue)
    )


def test_tratadas_ficam_de_fora_mesmo_sendo_velhas(db_session, cenario):
    """200 velhas já tratadas existem no banco e não podem entrar."""
    assert _conta(db_session, idade_min_dias=31) == 9
    # a prova pelo avesso: sem o escopo, seriam 209
    assert _conta(db_session, status=None, idade_min_dias=None) == 216


def test_status_explicito_manda_mais_que_a_regua(db_session, cenario):
    """Auditar "o que envelheceu e acabou ignorado" continua possível.

    O escopo é um DEFAULT, não uma trava: quem pede um status recebe aquele
    status. Sem isso a régua deixaria de ser filtro e viraria censura.
    """
    assert _conta(
        db_session, status=RECORD_STATUS_IGNORED, idade_min_dias=31
    ) == 150


def test_sem_filtro_de_regua_nada_muda(db_session, cenario):
    """Listagem comum não é afetada: o escopo só entra com a régua ligada."""
    assert _conta(db_session) == 216
    assert _conta(db_session, linked_office_id=61) == 216
