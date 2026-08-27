"""Publicação gigante não derruba mais a busca inteira.

Caso real 27/08/2026: o texto da publicação não tem teto de tamanho e algumas
pesam ~350 KB. Na janela do dia 26, `$top=20` devolveu 7 MB e passou; `$top=30`
(~10,5 MB) devolveu 502 no gateway do L1, SEMPRE no mesmo offset — offsets
vizinhos e mais fundos respondiam 200, e cada registro daquela página, pedido
sozinho, também. Não era instabilidade: era volume de resposta.

A busca inteira morria em "Máximo de tentativas excedido" depois de 8
retentativas contra 502 e nenhuma publicação do dia entrava — de fora, o job
simplesmente "não pegava nada".
"""
import pytest
import requests

from app.services.legal_one_client import LegalOneApiClient


class _L1(LegalOneApiClient):
    """Cliente sem __init__ real: só o que a paginação usa."""

    def __init__(self, paginas, estoura_acima_de):
        self._paginas = paginas                # skip -> lista de registros
        self._estoura = estoura_acima_de       # $top acima disso => 502
        self.chamadas = []
        import logging
        self.logger = logging.getLogger("teste")

    def fetch_publications(self, *, date_from, date_to=None, origin_type=None,
                           top=30, skip=0, count=False):
        self.chamadas.append((skip, top))
        if top > self._estoura:
            raise requests.exceptions.RequestException(
                "Maximo de tentativas excedido sem sucesso."
            )
        itens = self._paginas[skip:skip + top]
        out = {"value": itens}
        if count:
            out["@odata.count"] = len(self._paginas)
        return out


def test_pagina_gigante_nao_perde_publicacao():
    """O que o $top=30 não entrega, a página menor entrega — sem faltar nada."""
    registros = [{"id": i} for i in range(70)]
    c = _L1(registros, estoura_acima_de=15)

    tudo = c.fetch_all_publications(date_from="2026-08-26")

    assert len(tudo) == 70, "perdeu publicação ao encolher a página"
    assert [x["id"] for x in tudo] == list(range(70)), "ordem/duplicata errada"
    assert all(top <= 15 for _, top in c.chamadas if top <= 15)


def test_erro_que_pagina_menor_nao_resolve_sobe_na_hora():
    """400/401 não melhoram encolhendo — não podem virar 6 tentativas inúteis."""
    class _Ruim(_L1):
        def fetch_publications(self, **kw):
            self.chamadas.append((kw.get("skip"), kw.get("top")))
            resp = requests.Response()
            resp.status_code = 400
            raise requests.exceptions.HTTPError("query invalida", response=resp)

    c = _Ruim([{"id": 1}], estoura_acima_de=99)
    with pytest.raises(requests.exceptions.HTTPError):
        c.fetch_all_publications(date_from="2026-08-26")
    assert len(c.chamadas) == 1, "não pode insistir em erro que é da query"


def test_sem_estouro_continua_em_paginas_de_30():
    """Sem problema, nada muda: o caminho normal segue rápido."""
    registros = [{"id": i} for i in range(65)]
    c = _L1(registros, estoura_acima_de=99)

    tudo = c.fetch_all_publications(date_from="2026-08-26")

    assert len(tudo) == 65
    assert all(top == 30 for _, top in c.chamadas)
