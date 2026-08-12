"""
Testes do fallback manual de publicações (planilha exportada do Legal One).

O que importa garantir aqui:
  - a planilha vira o MESMO contrato que a API do L1 devolve (senão a dedup
    não casa entre as duas fontes e a publicação entra duas vezes);
  - subir o mesmo arquivo de novo NÃO duplica (idempotência via ID sintético
    determinístico);
  - colisão de hash não faz publicação sumir em silêncio.
"""
import io

import openpyxl
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.legal_one import LegalOneOffice
from app.models.publication_search import PublicationRecord
from app.services.publication_spreadsheet_import import (
    ORIGIN_TYPE,
    UPDATE_TYPE_ID,
    ler_planilha,
    montar_publicacoes,
)


CABECALHO = [
    "Escritório responsável", "Nº do processo", "Pasta", "Responsável principal",
    "Comarca/Foro", "UF", "Órgão", "Número do Cliente", "Andamentos / Data/hora",
    "Andamentos / Descrição", "Andamentos / Tipo",
    "Andamentos / Status da Intimação Eletrônica", "Andamentos / Tratamento",
    "SUBTIPO", "EXECUTANTE", "PRAZO", "DATA DA TAREFA", "HORÁRIO",
    "Data do cadastro", "Id",
]

PATH_ESCRITORIO = "MDR Advocacia / Área operacional / Ativos / Trabalhista"


def _linha(lawsuit_id, cnj, data, descricao, tipo="Publicação",
           escritorio=PATH_ESCRITORIO, pasta="Proc - 0003902",
           cadastro="2025-03-10 13:01:44"):
    return [
        escritorio, cnj, pasta, "Antônio Uemerson", "Salvador", "Bahia",
        "TRT5", "1111111.0", data, descricao, tipo, "Pendente de ciência",
        "Não tratado", None, None, None, None, None, cadastro, lawsuit_id,
    ]


def _planilha(linhas, cabecalho=CABECALHO) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(cabecalho)
    for l in linhas:
        ws.append(l)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    session.add(LegalOneOffice(external_id=61, name="Trabalhista", path=PATH_ESCRITORIO))
    session.commit()
    yield session
    session.close()


# ── Contrato ───────────────────────────────────────────────────────────

def test_planilha_vira_contrato_do_legal_one(db):
    conteudo = _planilha([
        _linha(3903, "0000967-85.2024.5.05.0019", "2026-07-30 00:00:00", "PODER JUDICIARIO ..."),
    ])
    resultado = ler_planilha(conteudo, db)
    assert resultado["total_validas"] == 1
    assert resultado["processos_distintos"] == 1

    pub = montar_publicacoes(resultado["validas"])[0]
    # Mesmo formato do fluxo automático — inclusive a data, senão a chave de
    # dedup (lawsuit_id, publication_date) não casa entre planilha e API.
    assert pub["date"] == "2026-07-30T00:00:00Z"
    assert pub["originType"] == ORIGIN_TYPE
    assert pub["typeId"] == UPDATE_TYPE_ID
    assert pub["relationships"] == [{"linkType": "Litigation", "linkId": 3903}]
    assert pub["_cnj"] == "0000967-85.2024.5.05.0019"
    assert pub["_responsible_office_id"] == 61
    assert pub["_lawsuit_creation_date"].startswith("2025-03-10")
    # ID sintético é negativo: nunca colide com ID real do L1, que é positivo.
    assert pub["id"] < 0


def test_escritorio_resolve_pelo_path_completo(db):
    conteudo = _planilha([
        _linha(3903, "0000967-85.2024.5.05.0019", "2026-07-30", "texto"),
        _linha(4152, "0133887-11.2018.8.06.0001", "2026-07-30", "texto 2",
               escritorio="MDR Advocacia / Inexistente"),
    ])
    resultado = ler_planilha(conteudo, db)
    assert resultado["escritorios_nao_encontrados"] == ["MDR Advocacia / Inexistente"]
    por_office = {v["lawsuit_id"]: v["office_id"] for v in resultado["validas"]}
    assert por_office[3903] == 61
    # Escritório que não casa não descarta a linha — o processo já está
    # identificado pelo Id, e o L1 enriquece o escritório depois.
    assert por_office[4152] is None


def test_data_aceita_formato_brasileiro_e_iso(db):
    conteudo = _planilha([
        _linha(1, "0000967-85.2024.5.05.0019", "30/07/2026", "a"),
        _linha(2, "0000967-85.2024.5.05.0020", "2026-07-30 08:15:00", "b"),
    ])
    resultado = ler_planilha(conteudo, db)
    datas = {v["lawsuit_id"]: v["publication_date"] for v in resultado["validas"]}
    assert datas[1] == "2026-07-30T00:00:00Z"
    assert datas[2] == "2026-07-30T00:00:00Z"


# ── Filtros e descartes ────────────────────────────────────────────────

def test_ignora_andamento_que_nao_e_publicacao(db):
    conteudo = _planilha([
        _linha(3903, "0000967-85.2024.5.05.0019", "2026-07-30", "vale"),
        _linha(4152, "0133887-11.2018.8.06.0001", "2026-07-30", "nao vale",
               tipo="Despacho"),
    ])
    resultado = ler_planilha(conteudo, db)
    assert resultado["total_validas"] == 1
    assert "Despacho" in resultado["ignoradas"][0]["motivo"]


def test_linha_repetida_no_mesmo_arquivo_conta_uma_vez(db):
    linha = _linha(3903, "0000967-85.2024.5.05.0019", "2026-07-30", "identica")
    resultado = ler_planilha(_planilha([linha, list(linha)]), db)
    assert resultado["total_validas"] == 1
    assert resultado["ignoradas"][0]["motivo"] == "linha repetida dentro do arquivo"


def test_linha_sem_id_e_descartada_com_motivo(db):
    conteudo = _planilha([
        _linha(None, "0000967-85.2024.5.05.0019", "2026-07-30", "sem id"),
        _linha(3903, "0000967-85.2024.5.05.0019", "2026-07-30", "com id"),
    ])
    resultado = ler_planilha(conteudo, db)
    assert resultado["total_validas"] == 1
    assert "Id" in resultado["ignoradas"][0]["motivo"]


def test_planilha_sem_coluna_id_e_recusada_com_instrucao(db):
    cabecalho = CABECALHO[:-1]
    conteudo = _planilha([_linha(3903, "0000967", "2026-07-30", "x")[:-1]], cabecalho)
    with pytest.raises(ValueError) as exc:
        ler_planilha(conteudo, db)
    assert "Id" in str(exc.value)


def test_colunas_sao_casadas_por_nome_e_nao_por_posicao(db):
    """O operador monta a extração na tela do L1 — a ordem muda."""
    cabecalho = ["Id", "Andamentos / Descrição", "Nº do processo",
                 "Andamentos / Data/hora", "Escritório responsável"]
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(cabecalho)
    ws.append([3903, "texto da publicacao", "0000967-85.2024.5.05.0019",
               "2026-07-30", PATH_ESCRITORIO])
    buf = io.BytesIO(); wb.save(buf)
    resultado = ler_planilha(buf.getvalue(), db)
    assert resultado["total_validas"] == 1
    v = resultado["validas"][0]
    assert v["lawsuit_id"] == 3903 and v["office_id"] == 61
    assert v["descricao"] == "texto da publicacao"


# ── Idempotência e colisão ─────────────────────────────────────────────

def test_mesmo_arquivo_gera_os_mesmos_ids(db):
    """Subir a mesma planilha de novo tem que deduplicar sozinho."""
    conteudo = _planilha([
        _linha(3903, "0000967-85.2024.5.05.0019", "2026-07-30", "texto um"),
        _linha(4152, "0133887-11.2018.8.06.0001", "2026-07-30", "texto dois"),
    ])
    ids_1 = [v["update_id"] for v in ler_planilha(conteudo, db)["validas"]]
    ids_2 = [v["update_id"] for v in ler_planilha(conteudo, db)["validas"]]
    assert ids_1 == ids_2


def test_id_ja_gravado_para_a_mesma_publicacao_e_reaproveitado(db):
    """Reenvio da mesma publicação mantém o ID → vira duplicata, não registro novo."""
    conteudo = _planilha([
        _linha(3903, "0000967-85.2024.5.05.0019", "2026-07-30", "texto um"),
    ])
    primeiro = ler_planilha(conteudo, db)["validas"][0]
    db.add(PublicationRecord(
        search_id=1,
        legal_one_update_id=primeiro["update_id"],
        linked_lawsuit_id=primeiro["lawsuit_id"],
        publication_date=primeiro["publication_date"],
        status="NOVO",
    ))
    db.commit()
    segundo = ler_planilha(conteudo, db)["validas"][0]
    assert segundo["update_id"] == primeiro["update_id"]


def test_colisao_com_publicacao_diferente_nao_perde_linha(db):
    """
    Se o ID sintético já pertence a OUTRA publicação, o ID anda. Sem isso a
    linha seria tratada como duplicata exata e sumiria em silêncio — o oposto
    do que esse fallback existe pra fazer.
    """
    conteudo = _planilha([
        _linha(3903, "0000967-85.2024.5.05.0019", "2026-07-30", "texto um"),
    ])
    alvo = ler_planilha(conteudo, db)["validas"][0]
    # Ocupa o ID com uma publicação de OUTRO processo.
    db.add(PublicationRecord(
        search_id=1,
        legal_one_update_id=alvo["update_id"],
        linked_lawsuit_id=999999,
        publication_date="2020-01-01T00:00:00Z",
        status="NOVO",
    ))
    db.commit()
    depois = ler_planilha(conteudo, db)["validas"][0]
    assert depois["update_id"] != alvo["update_id"]
    assert depois["update_id"] < 0


def test_duas_publicacoes_do_mesmo_processo_no_mesmo_dia_recebem_ids_distintos(db):
    """A dedup por (processo, data) é do pipeline; aqui não podem colidir."""
    conteudo = _planilha([
        _linha(3903, "0000967-85.2024.5.05.0019", "2026-07-30", "primeira publicacao"),
        _linha(3903, "0000967-85.2024.5.05.0019", "2026-07-30", "segunda publicacao"),
    ])
    resultado = ler_planilha(conteudo, db)
    ids = [v["update_id"] for v in resultado["validas"]]
    assert resultado["total_validas"] == 2
    assert len(set(ids)) == 2
