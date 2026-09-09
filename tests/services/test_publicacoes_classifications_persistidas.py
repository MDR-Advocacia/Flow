"""A classificação da IA é gravada INTEIRA, não só as colunas planas.

O BUG (achado em 09/09/2026, a partir de um print do operador)
--------------------------------------------------------------
A Triagem grifa no texto o trecho que a IA citou ao classificar — só que o
grifo nunca aparecia. A causa não estava no grifo: estava na gravação.

`categoria`, `subcategoria` e `polo` têm coluna própria em
`publicacao_registros`. A **justificativa** e a `prazo_fundamentacao` não —
elas vivem dentro do dict da classificação, que é persistido na coluna JSON
`classifications`. E essa coluna era escrita:

  - no LOTE, apenas `if extra` — ou seja, só quando a IA devolvia MAIS DE UMA
    classificação, que é a minoria;
  - na classificação ONLINE, em nenhum caso.

Medido em produção antes da correção: 2.794 das 3.506 publicações pendentes
(80%) sem `classifications`. Ou seja, para 4 de cada 5 publicações o operador
nunca teve como saber POR QUE a IA classificou daquele jeito — e um recurso
inteiro da tela ficou invisível sem ninguém perceber.

O que estes testes protegem é o INVARIANTE, não a implementação: publicação
classificada por IA tem `classifications[0]` com a justificativa dentro.
"""
import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models as _models  # noqa: F401 - registra as tabelas
from app.db.session import Base
from app.models.publication_search import (
    RECORD_STATUS_CLASSIFIED,
    SEARCH_STATUS_COMPLETED,
    PublicationRecord,
    PublicationSearch,
)
from app.services.publication_batch_classifier import PublicationBatchClassifier

CATEGORIA = "Recursos e Julgamentos em 2º Grau"
SUBCATEGORIA = "Embargos de Declaração"
JUSTIFICATIVA = (
    "o texto diz 'foi oposto Recurso de Embargos de Declaração, estando "
    "facultada a apresentação de contrarrazões'"
)


def _sessao():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)()


def _registro(db):
    busca = PublicationSearch(
        status=SEARCH_STATUS_COMPLETED, date_from="2026-09-09",
        origin_type="OfficialJournalsCrawler",
    )
    db.add(busca)
    db.flush()
    rec = PublicationRecord(
        search_id=busca.id, legal_one_update_id=555,
        linked_lawsuit_id=31334, linked_lawsuit_cnj="0801745-38.2022.8.14.0133",
        linked_office_id=22, publication_date="2026-09-02T00:00:00Z",
        description="ATO ORDINATORIO. Foi oposto Recurso de Embargos de Declaracao.",
        status="NOVO", is_duplicate=False,
    )
    db.add(rec)
    db.commit()
    return rec


class _BatchFalso:
    """O mínimo que `apply_batch_results` toca no objeto do lote."""

    id = 1
    anthropic_batch_id = "batch_teste"
    results_url = "https://exemplo.test/resultados"
    total_records = 1
    status = "PRONTO"

    def __setattr__(self, k, v):  # aceita qualquer bookkeeping do método
        object.__setattr__(self, k, v)


def _classificacao(extras=None):
    base = {
        "categoria": CATEGORIA,
        "subcategoria": SUBCATEGORIA,
        "polo": "ambos",
        "confianca": "alta",
        "justificativa": JUSTIFICATIVA,
        "prazo_dias": None,
        "prazo_tipo": None,
        "prazo_fundamentacao": None,
        "audiencia_data": None,
        "audiencia_hora": None,
        "audiencia_link": None,
        "quem_pratica_ato": "juizo_determina",
        "exige_providencia_nossa": True,
        "natureza_processo": None,
    }
    if extras:
        base["_extra_classifications"] = extras
    return base


def _rodar(monkeypatch, db, rec, classificacao):
    """Executa o apply real com o L1/Anthropic e a taxonomia fora do caminho."""
    from app.services import publication_batch_classifier as mod

    async def _resultados(_url):
        return [{"custom_id": str(rec.id)}]

    class _AiFalso:
        get_batch_results = staticmethod(_resultados)

    monkeypatch.setattr(
        mod.AnthropicClassifierClient,
        "extract_classification_from_batch_result",
        staticmethod(lambda item: dict(classificacao)),
    )
    monkeypatch.setattr(
        mod.AnthropicClassifierClient,
        "extract_usage_from_batch_result",
        staticmethod(lambda item: {}),
    )
    # A taxonomia real mora no banco da APLICAÇÃO, não no SQLite do teste —
    # deixá-la no caminho faria o teste depender do conteúdo do banco de quem
    # roda a suíte (foi o que manteve 3 testes vermelhos por meses).
    monkeypatch.setattr(mod, "validate_classification", lambda c, s: True)
    monkeypatch.setattr(mod, "repair_classification", lambda c, s, **k: (c, s))
    monkeypatch.setattr(mod, "atualizar_prazo_estimado", lambda db_, r: None)

    svc = PublicationBatchClassifier.__new__(PublicationBatchClassifier)
    svc.db = db
    svc.ai = _AiFalso()

    asyncio.run(svc.apply_batch_results(_BatchFalso()))
    db.refresh(rec)


def test_classificacao_unica_grava_a_justificativa(monkeypatch):
    """O caso NORMAL — uma classificação só. Era exatamente ele que se perdia:
    a gravação acontecia apenas quando havia classificação EXTRA."""
    db = _sessao()
    rec = _registro(db)

    _rodar(monkeypatch, db, rec, _classificacao())

    assert rec.status == RECORD_STATUS_CLASSIFIED
    assert isinstance(rec.classifications, list) and len(rec.classifications) == 1
    primeira = rec.classifications[0]
    assert primeira["justificativa"] == JUSTIFICATIVA, (
        "sem a justificativa gravada o operador nao sabe POR QUE a IA "
        "classificou assim, e o grifo do trecho fica sem nada pra marcar"
    )
    assert primeira["categoria"] == CATEGORIA


def test_classificacoes_extras_continuam_depois_da_primaria(monkeypatch):
    """O formato não mudou: [0] é a primária, [1:] são as extras. Os
    consumidores (template matching, export, serialização do grupo) contam
    com essa ordem."""
    db = _sessao()
    rec = _registro(db)
    extra = {"categoria": "Manifestações, Prazos e Providências",
             "subcategoria": "Cumprir Determinação", "justificativa": "segunda leitura"}

    _rodar(monkeypatch, db, rec, _classificacao(extras=[extra]))

    assert len(rec.classifications) == 2
    assert rec.classifications[0]["categoria"] == CATEGORIA
    assert rec.classifications[1]["categoria"] == extra["categoria"]


def test_marcador_interno_nao_vaza_para_o_banco(monkeypatch):
    """`_extra_classifications` é transporte, não dado: gravá-lo dentro da
    própria classificação criaria aninhamento infinito na coluna JSON."""
    db = _sessao()
    rec = _registro(db)

    _rodar(monkeypatch, db, rec, _classificacao(extras=[{"categoria": "X"}]))

    for c in rec.classifications:
        assert "_extra_classifications" not in c
