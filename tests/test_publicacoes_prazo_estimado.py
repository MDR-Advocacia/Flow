# -*- coding: utf-8 -*-
"""Régua de envelhecimento das publicações (pub012).

Cobre o que o caso 7000755-49 expôs: publicação capturada em 11/07 só foi
agendada em 28/08 porque NADA na listagem distinguia velho de novo — ordenação
por group_key (arbitrária) e nenhuma noção de prazo. Aqui:

  1. resolver: default da SUBcategoria vence o da categoria; sem default =>
     None; delega a conta ao calculador oficial (útil/corrido);
  2. atualizar_prazo_estimado não explode com dado ruim;
  3. aging_summary conta certo (estados de prazo + faixas de idade + mais
     antiga) e ignora duplicatas/tratadas;
  4. filtro estado_prazo na query base;
  5. list_records_grouped ordena por urgência (vencida primeiro, sem prazo no
     fim) — a correção estrutural do enterro.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.classification_taxonomy import (
    ClassificationCategory,
    ClassificationSubcategory,
)
from app.models.publication_search import PublicationRecord, PublicationSearch
from app.services.prazos_iniciais.prazo_calculator import calcular_prazo_final
from app.services.publication_prazo_estimado import (
    PrazoEstimadoResolver,
    atualizar_prazo_estimado,
    estado_prazo,
    hoje_brt,
)
from app.services.publication_search_service import PublicationSearchService


# ── infra ──────────────────────────────────────────────────────────────


@pytest.fixture()
def busca(db_session):
    b = PublicationSearch(date_from="2026-07-01T00:00:00Z", status="CONCLUIDO")
    db_session.add(b)
    db_session.commit()
    return b


def _rec(db, busca, uid, *, status="CLASSIFICADO", cnj=None, lawsuit=None,
         pub=None, prazo=None, criada_ha_dias=0, dup=False,
         category=None, subcategory=None):
    agora = datetime.now(timezone.utc)
    r = PublicationRecord(
        search_id=busca.id,
        legal_one_update_id=uid,
        status=status,
        is_duplicate=dup,
        linked_lawsuit_id=lawsuit,
        linked_lawsuit_cnj=cnj,
        publication_date=pub,
        prazo_estimado=prazo,
        category=category,
        subcategory=subcategory,
        created_at=agora - timedelta(days=criada_ha_dias),
    )
    db.add(r)
    db.commit()
    return r


@pytest.fixture()
def taxonomia(db_session):
    cat = ClassificationCategory(
        name="Manifestações, Prazos e Providências",
        default_prazo_dias=5,
        default_prazo_tipo="util",
    )
    db_session.add(cat)
    db_session.commit()
    sub = ClassificationSubcategory(
        category_id=cat.id,
        name="Pagamento Voluntário",
        default_prazo_dias=15,
        default_prazo_tipo="corrido",
    )
    cat2 = ClassificationCategory(name="Informativa")  # sem default
    db_session.add_all([sub, cat2])
    db_session.commit()
    return cat, sub, cat2


# ── 1/2: resolver ──────────────────────────────────────────────────────


def test_resolver_subcategoria_vence_categoria(db_session, taxonomia):
    r = PrazoEstimadoResolver(db_session)
    base = date(2026, 7, 10)

    # só a categoria: 5 dias úteis a partir de 10/07 — delega ao calculador
    p_cat = r.calcular("2026-07-10T00:00:00Z", taxonomia[0].name, None)
    assert p_cat == calcular_prazo_final(base, 5, "util")

    # subcategoria com default próprio (15 corridos) VENCE o da categoria
    p_sub = r.calcular("2026-07-10T00:00:00Z", taxonomia[0].name,
                       "Pagamento Voluntário")
    assert p_sub == calcular_prazo_final(base, 15, "corrido")

    # subcategoria desconhecida cai no default da categoria
    p_outra = r.calcular("2026-07-10T00:00:00Z", taxonomia[0].name, "Outra")
    assert p_outra == p_cat


def test_resolver_sem_default_e_dado_ruim(db_session, taxonomia):
    r = PrazoEstimadoResolver(db_session)
    assert r.calcular("2026-07-10T00:00:00Z", "Informativa", None) is None
    assert r.calcular("2026-07-10T00:00:00Z", None, None) is None
    assert r.calcular(None, taxonomia[0].name, None) is None
    assert r.calcular("data-podre", taxonomia[0].name, None) is None
    assert r.calcular("2026-07-10T00:00:00Z", "Categoria Fantasma", None) is None


def test_atualizar_prazo_estimado_no_record(db_session, taxonomia, busca):
    rec = _rec(db_session, busca, 9001, pub="2026-07-10T00:00:00Z",
               category=taxonomia[0].name)
    atualizar_prazo_estimado(db_session, rec)
    assert rec.prazo_estimado == calcular_prazo_final(date(2026, 7, 10), 5, "util")

    # categoria sem default zera de volta
    rec.category = "Informativa"
    atualizar_prazo_estimado(db_session, rec)
    assert rec.prazo_estimado is None


def test_estado_prazo():
    hoje = date(2026, 8, 31)
    assert estado_prazo(None, hoje) == "SEM_PRAZO"
    assert estado_prazo(date(2026, 8, 30), hoje) == "VENCIDA"
    assert estado_prazo(hoje, hoje) == "VENCE_HOJE"
    assert estado_prazo(date(2026, 9, 2), hoje) == "NO_PRAZO"


# ── 3: aging_summary ───────────────────────────────────────────────────


def test_aging_summary(db_session, busca):
    hoje = hoje_brt()
    svc = PublicationSearchService(db_session, None)

    _rec(db_session, busca, 1, prazo=hoje - timedelta(days=3),
         criada_ha_dias=40, cnj="0000001-11.2026.8.05.0001")   # vencida, >30d
    _rec(db_session, busca, 2, prazo=hoje)                       # vence hoje
    _rec(db_session, busca, 3, prazo=hoje + timedelta(days=5),
         criada_ha_dias=10)                                      # no prazo, 8-15d
    _rec(db_session, busca, 4)                                   # sem prazo, 0-2d
    _rec(db_session, busca, 5, status="AGENDADO",
         prazo=hoje - timedelta(days=9))                         # tratada: fora
    _rec(db_session, busca, 6, dup=True)                         # duplicata: fora

    s = svc.aging_summary()
    assert s["total_pendentes"] == 4
    assert s["vencidas"] == 1
    assert s["vence_hoje"] == 1
    assert s["no_prazo"] == 1
    assert s["sem_prazo"] == 1
    assert s["faixas"]["d31_mais"] == 1
    assert s["faixas"]["d8_15"] == 1
    assert s["faixas"]["d0_2"] == 2
    assert s["mais_antiga"]["id"] is not None
    assert s["mais_antiga"]["dias_captura"] >= 39
    assert s["mais_antiga"]["cnj"] == "0000001-11.2026.8.05.0001"


# ── 4: filtro estado_prazo ─────────────────────────────────────────────


def test_filtro_estado_prazo(db_session, busca):
    hoje = hoje_brt()
    svc = PublicationSearchService(db_session, None)
    _rec(db_session, busca, 11, prazo=hoje - timedelta(days=1))
    _rec(db_session, busca, 12, prazo=hoje)
    _rec(db_session, busca, 13, prazo=hoje + timedelta(days=3))
    _rec(db_session, busca, 14)

    def conta(estado):
        return svc._base_publication_query(estado_prazo=estado).count()

    assert conta("vencida") == 1
    assert conta("vence_hoje") == 1
    assert conta("no_prazo") == 1
    assert conta("sem_prazo") == 1
    assert conta("vencida,vence_hoje") == 2
    assert conta(None) == 4


def test_filtro_idade_captura(db_session, busca):
    svc = PublicationSearchService(db_session, None)
    _rec(db_session, busca, 21, criada_ha_dias=40)
    _rec(db_session, busca, 22, criada_ha_dias=10)
    _rec(db_session, busca, 23, criada_ha_dias=0)

    assert svc._base_publication_query(idade_min_dias=31).count() == 1
    assert svc._base_publication_query(idade_min_dias=8).count() == 2
    assert svc._base_publication_query(idade_min_dias=8, idade_max_dias=15).count() == 1
    assert svc._base_publication_query(idade_max_dias=2).count() == 1


# ── 5: ordenação por urgência ──────────────────────────────────────────


def test_grouped_ordena_por_urgencia(db_session, busca):
    hoje = hoje_brt()
    svc = PublicationSearchService(db_session, None)

    # grupo A (lawsuit 100): sem prazo estimado, publicação recente
    _rec(db_session, busca, 31, lawsuit=100, pub="2026-08-30T00:00:00Z")
    # grupo B (lawsuit 50): VENCIDA — tem que vir primeiro apesar do id maior
    _rec(db_session, busca, 32, lawsuit=50, pub="2026-07-10T00:00:00Z",
         prazo=hoje - timedelta(days=30))
    # grupo C (lawsuit 200): no prazo
    _rec(db_session, busca, 33, lawsuit=200, pub="2026-08-20T00:00:00Z",
         prazo=hoje + timedelta(days=5))

    out = svc.list_records_grouped(limit=10, offset=0)
    ordem = [g["lawsuit_id"] for g in out["groups"]]
    assert ordem == [50, 200, 100], (
        "vencida primeiro, sem-prazo no fim — veio %s" % ordem)

    # ordem legada continua disponível (por group_key string)
    out2 = svc.list_records_grouped(limit=10, offset=0, order="grupo")
    ordem2 = [g["lawsuit_id"] for g in out2["groups"]]
    assert ordem2 == [100, 200, 50]  # "100" < "200" < "50" como string

    # serializer expõe o prazo estimado pro front
    rec_b = [g for g in out["groups"] if g["lawsuit_id"] == 50][0]["records"][0]
    assert rec_b["prazo_estimado"] == (hoje - timedelta(days=30)).isoformat()
