"""UF derivada do CNJ — o mapa TR→UF não pode ter estado trocado.

`.8.26.` (TJSP) é o código mais comum do país e estava mapeado para Sergipe,
com `.8.25.` (TJSE) mapeado para São Paulo. Com a inversão, todo CNJ paulista
consultava o índice DataJud de Sergipe: a busca voltava vazia e o processo
passava como "sem citação".
"""
import pytest

from app.services.citacoes_bm.tribunal_alias import uf_do_cnj


@pytest.mark.parametrize("cnj, uf", [
    ("1234567-89.2020.8.26.0100", "SP"),   # TJSP — Foro Central
    ("0002832-97.2026.8.25.0074", "SE"),   # TJSE — o par que estava trocado
    ("8159909-50.2025.8.05.0001", "BA"),
    ("0803476-45.2025.8.14.0010", "PA"),
    ("0739827-69.2025.8.02.0001", "AL"),
    ("0008144-95.2025.8.17.8227", "PE"),
    ("0201398-50.2024.8.06.0119", "CE"),
    ("0840086-60.2025.8.23.0010", "RR"),
])
def test_uf_vem_do_tribunal_no_cnj(cnj, uf):
    assert uf_do_cnj(cnj) == uf


def test_todos_os_27_codigos_sao_distintos():
    """Estado repetido no mapa significa que algum par está trocado."""
    from app.services.citacoes_bm.tribunal_alias import _UF_POR_TR

    assert len(_UF_POR_TR) == 27
    assert len(set(_UF_POR_TR.values())) == 27, "UF duplicada — há par trocado"


@pytest.mark.parametrize("cnj", [
    "0017449-29.2025.4.05.8500",   # federal: TR é região, não estado
    "0000967-85.2024.5.05.0019",   # trabalhista: idem
    None, "", "123",
])
def test_so_responde_para_justica_estadual(cnj):
    assert uf_do_cnj(cnj) is None
