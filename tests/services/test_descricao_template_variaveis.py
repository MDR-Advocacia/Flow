"""Variáveis da descrição padrão dos templates (10/09/2026).

O operador pediu descrição de tarefa com "subtipo + classificação + data da
publicação": o pessoal configurou os templates e deixou a descrição de
fábrica ("Publicação judicial referente ao processo {cnj} em
{publication_date}."), que no L1 não diz nada — e com a data em ISO.

Estes testes protegem o contrato das variáveis novas E a compatibilidade das
antigas (há templates em produção usando `{cnj}` e `{publication_date}` ISO).
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models as _models  # noqa: F401 - registra as tabelas
from app.db.session import Base
from app.models.legal_one import LegalOneOffice, LegalOneTaskSubType, LegalOneTaskType
from app.models.publication_search import (
    RECORD_STATUS_CLASSIFIED,
    SEARCH_STATUS_COMPLETED,
    PublicationRecord,
    PublicationSearch,
)
from app.models.task_template import TaskTemplate
from app.services.publication_search_service import PublicationSearchService

PADRAO = "{subtipo} — {classificacao} — publicação de {data_publicacao}"


def _cenario(*, descricao, subcategoria="Apelação", audiencia_data=None):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    db.add_all([
        LegalOneOffice(external_id=22, name="Autor", is_active=True),
        LegalOneTaskType(external_id=1, name="Ativos e BB - Recuperação de Crédito", is_active=True),
        LegalOneTaskSubType(
            external_id=100, name="Contrarrazões - Ativos e BB Autor",
            parent_type_external_id=1, is_active=True,
        ),
    ])
    busca = PublicationSearch(
        status=SEARCH_STATUS_COMPLETED, date_from="2026-09-02",
        origin_type="OfficialJournalsCrawler",
    )
    db.add(busca)
    db.flush()
    rec = PublicationRecord(
        search_id=busca.id, legal_one_update_id=1, linked_lawsuit_id=501,
        linked_lawsuit_cnj="0801011-26.2022.8.20.5111", linked_office_id=22,
        publication_date="2026-09-02T00:00:00Z", description="Intimação para contrarrazões.",
        category="Recursos", subcategory=subcategoria, status=RECORD_STATUS_CLASSIFIED,
        is_duplicate=False, audiencia_data=audiencia_data,
    )
    tmpl = TaskTemplate(
        name="t", category="Recursos", subcategory=None, office_external_id=22,
        task_subtype_external_id=100, priority="Normal", due_business_days=5,
        description_template=descricao,
    )
    db.add_all([rec, tmpl])
    db.commit()
    svc = PublicationSearchService.__new__(PublicationSearchService)
    svc.db = db
    return svc, rec, tmpl


def _descricao(svc, rec, tmpl):
    return svc._render_proposal(rec, tmpl)["payload"]["description"]


def test_padrao_da_casa_monta_subtipo_classificacao_e_data_br():
    svc, rec, tmpl = _cenario(descricao=PADRAO)

    assert _descricao(svc, rec, tmpl) == (
        "Contrarrazões - Ativos e BB Autor — Recursos / Apelação — publicação de 02/09/2026"
    )


def test_classificacao_sem_subcategoria_nao_deixa_barra_sobrando():
    svc, rec, tmpl = _cenario(descricao=PADRAO, subcategoria=None)

    assert _descricao(svc, rec, tmpl) == (
        "Contrarrazões - Ativos e BB Autor — Recursos — publicação de 02/09/2026"
    )


def test_variaveis_antigas_continuam_iguais():
    """`{publication_date}` segue ISO de propósito: há template usando assim."""
    svc, rec, tmpl = _cenario(
        descricao="Publicação judicial referente ao processo {cnj} em {publication_date}.",
    )

    assert _descricao(svc, rec, tmpl) == (
        "Publicação judicial referente ao processo 0801011-26.2022.8.20.5111 em 2026-09-02."
    )


def test_data_da_audiencia_tambem_sai_no_formato_brasileiro():
    svc, rec, tmpl = _cenario(
        descricao="Audiência em {audiencia_data_br} ({audiencia_data})",
        audiencia_data="2026-10-15",
    )

    assert _descricao(svc, rec, tmpl) == "Audiência em 15/10/2026 (2026-10-15)"
