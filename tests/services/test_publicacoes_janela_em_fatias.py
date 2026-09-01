"""A janela da captura é buscada em fatias de UM DIA.

Quando a captura fica dias sem fechar, o cursor não avança e a janela cresce.
Em 31/08/2026 ela chegou a 5 dias / 11.547 publicações / ~385 páginas numa
única chamada contínua — a execução passava de uma hora sem terminar, e três
madrugadas seguidas terminaram sem NENHUMA publicação capturada.
"""
import pytest

from app.services.publication_search_service import PublicationSearchService


class _ClientFake:
    def __init__(self, por_dia=3, explode_em=None):
        self.chamadas = []
        self._por_dia = por_dia
        self._explode_em = explode_em or set()
        self._seq = 0

    def fetch_all_publications(self, *, date_from, date_to=None, origin_type=None, on_page=None):
        self.chamadas.append((date_from, date_to))
        dia = str(date_from)[:10]
        if dia in self._explode_em:
            raise RuntimeError(f"L1 fora no dia {dia}")
        out = []
        for _ in range(self._por_dia):
            self._seq += 1
            out.append({"id": self._seq, "dia": dia})
        return out


def _monta(client):
    svc = PublicationSearchService.__new__(PublicationSearchService)
    svc.client = client
    svc.db = None
    return svc


def test_janela_de_5_dias_vira_5_chamadas_de_1_dia():
    c = _ClientFake(por_dia=3)
    svc = _monta(c)

    pubs = svc.fetch_publications_for_window(
        date_from="2026-08-26T00:00:00Z", date_to="2026-08-31T00:00:00Z")

    assert len(c.chamadas) == 5, "a janela tem que ser fatiada, nao ir inteira"
    assert len(pubs) == 15
    assert [x["dia"] for x in pubs].count("2026-08-26") == 3


def test_janela_de_um_dia_nao_fatia():
    """O caminho normal (D-1 → hoje) não pode ficar mais lento por causa disto."""
    c = _ClientFake()
    svc = _monta(c)

    svc.fetch_publications_for_window(
        date_from="2026-08-30T01:00:00Z", date_to="2026-08-31T01:00:00Z")

    assert len(c.chamadas) == 1


def test_fatia_que_falha_nao_derruba_as_outras():
    """Melhor trazer 4 dias de 5 do que perder os 5 porque um deu erro."""
    c = _ClientFake(por_dia=2, explode_em={"2026-08-28"})
    svc = _monta(c)

    pubs = svc.fetch_publications_for_window(
        date_from="2026-08-26T00:00:00Z", date_to="2026-08-31T00:00:00Z")

    assert len(c.chamadas) == 5
    assert len(pubs) == 8, "perdeu só o dia que falhou"


def test_publicacao_repetida_na_borda_nao_duplica():
    """Fatias vizinhas podem se tocar; id repetido não pode virar 2 registros."""
    class _Repete(_ClientFake):
        def fetch_all_publications(self, *, date_from, date_to=None, origin_type=None, on_page=None):
            self.chamadas.append((date_from, date_to))
            return [{"id": 777, "dia": str(date_from)[:10]}]

    c = _Repete()
    svc = _monta(c)
    pubs = svc.fetch_publications_for_window(
        date_from="2026-08-26T00:00:00Z", date_to="2026-08-31T00:00:00Z")

    assert len(c.chamadas) == 5
    assert len(pubs) == 1, "mesmo id em fatias diferentes é a MESMA publicação"
