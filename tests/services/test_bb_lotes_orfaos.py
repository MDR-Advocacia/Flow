# -*- coding: utf-8 -*-
"""Lote de upload órfão (Ativos / Banco Master) não fica EM_ANDAMENTO pra sempre.

Caso real (15/09/2026): lote 40 do Master, 23 de 23 processados, planilha 232
gerada e o import no L1 no meio quando o supervisor do uvicorn matou o worker
(08:49). A thread da ingestão morreu junto, o lote ficou EM_ANDAMENTO e a tela
seguiu consultando o progresso a cada 1,5 s. O cadastro se resolveu sozinho
pelo retry do monitor; o lote não.

A thread segura uma trava de arquivo por lote enquanto roda; trava livre com
lote EM_ANDAMENTO = ninguém conduz o lote. O reaper manda pro cadastro o que
ficou no pool, fecha calado quando tudo foi processado e só fala com o
operador quando precisa do arquivo de novo.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.distribuidos_bb import (
    CLIENTE_ATIVOS,
    CLIENTE_MASTER,
    LOTE_CONCLUIDO,
    LOTE_EM_ANDAMENTO,
    LOTE_ERRO,
    POOL_NOVO,
    POOL_PENDENTE_CADASTRO,
    PROC_COLETADO,
    PROC_DISTRIBUIDO,
    BbAtivosLote,
    BbEvento,
    BbProcesso,
)
from app.services.distribuidos_bb import ativos_service, lotes_orfaos, master_service
from app.services.distribuidos_bb.import_l1_service import ImportL1Error
from app.services.distribuidos_bb.lotes_orfaos import (
    lote_em_execucao,
    lote_esta_vivo,
    reapear_lotes_orfaos,
)

AGORA = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _travas_isoladas(tmp_path, monkeypatch):
    monkeypatch.setattr(lotes_orfaos, "_DIR_TRAVAS", str(tmp_path))


@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


@pytest.fixture
def cadastros(monkeypatch):
    """O cadastro de verdade vai ao Legal One; aqui só registra o que seria enviado."""
    chamadas = []
    monkeypatch.setattr(
        master_service, "_cadastrar_lote",
        lambda db, lote_id, ids: chamadas.append((CLIENTE_MASTER, lote_id, list(ids))),
    )
    monkeypatch.setattr(
        ativos_service, "_cadastrar_lote",
        lambda db, lote_id, ids: chamadas.append((CLIENTE_ATIVOS, lote_id, list(ids))),
    )
    return chamadas


def _lote(db, lote_id, *, cliente=CLIENTE_MASTER, ha_min=60, total=23, processados=23):
    lote = BbAtivosLote(
        id=lote_id, cliente=cliente, nome_arquivo="Listagem_de_Prazos (74).xlsx",
        total=total, processados=processados, criados=processados,
        status=LOTE_EM_ANDAMENTO, iniciado_em=AGORA - timedelta(minutes=ha_min),
    )
    db.add(lote)
    db.commit()
    return lote


def _proc(db, cnj, *, cliente=CLIENTE_MASTER, ha_min=59, raw=None,
          planilha_status=POOL_NOVO, status=PROC_DISTRIBUIDO):
    marca = "master_listagem" if cliente == CLIENTE_MASTER else "ativos_planilha"
    p = BbProcesso(
        cliente=cliente, cnj=cnj, fingerprint=f"{cliente.lower()}:cnj:{cnj}",
        status=status, planilha_status=planilha_status,
        raw={marca: {"Processo": cnj}} if raw is None else raw,
        created_at=AGORA - timedelta(minutes=ha_min),
    )
    db.add(p)
    db.commit()
    return p


def test_lote_do_incidente_fecha_concluido_sem_mensagem(db, cadastros):
    _lote(db, 40)
    # A planilha 232 já tinha sido gerada: o processo não está mais no pool.
    _proc(db, "7054679-28.2026.8.22.0001", planilha_status=POOL_PENDENTE_CADASTRO)

    out = reapear_lotes_orfaos(db)

    lote = db.get(BbAtivosLote, 40)
    assert out == {"fechados": [40], "retomados": {}}
    assert lote.status == LOTE_CONCLUIDO and lote.concluido_em is not None
    assert lote.erro is None
    assert cadastros == []
    assert db.query(BbEvento).count() == 0


def test_lote_com_execucao_viva_nao_e_tocado(db, cadastros):
    _lote(db, 41, processados=5)

    with lote_em_execucao(41):
        assert lote_esta_vivo(41) is True
        assert reapear_lotes_orfaos(db)["fechados"] == []
        assert db.get(BbAtivosLote, 41).status == LOTE_EM_ANDAMENTO

    assert lote_esta_vivo(41) is False
    assert reapear_lotes_orfaos(db)["fechados"] == [41]


def test_lote_recem_criado_espera_a_graca(db, cadastros):
    _lote(db, 42, ha_min=2)

    assert reapear_lotes_orfaos(db)["fechados"] == []
    assert reapear_lotes_orfaos(db, graca_min=1)["fechados"] == [42]


def test_processos_do_lote_que_ficaram_no_pool_vao_pro_cadastro(db, cadastros):
    _lote(db, 43)
    a = _proc(db, "0000001-00.2026.8.12.0001")
    b = _proc(db, "0000002-00.2026.8.12.0001")
    _proc(db, "0000003-00.2026.8.12.0001", planilha_status=POOL_PENDENTE_CADASTRO)
    _proc(db, "0000004-00.2026.8.12.0001", ha_min=120)                      # antes do lote
    _proc(db, "0000005-00.2026.8.12.0001", raw={"origem": "pasta_avulsa"})  # pasta avulsa
    _proc(db, "0000006-00.2026.8.12.0001", cliente=CLIENTE_ATIVOS)          # outra carteira
    _proc(db, "0000007-00.2026.8.12.0001", status=PROC_COLETADO)            # sem distribuição

    out = reapear_lotes_orfaos(db)

    assert cadastros == [(CLIENTE_MASTER, 43, [a.id, b.id])]
    assert out["retomados"] == {43: [a.id, b.id]}
    assert db.get(BbAtivosLote, 43).status == LOTE_CONCLUIDO


def test_o_lote_seguinte_do_mesmo_cliente_fica_com_o_que_nasceu_depois_dele(db, cadastros):
    _lote(db, 44, ha_min=60)
    _lote(db, 45, ha_min=30)
    do_44 = _proc(db, "0000011-00.2026.8.12.0001", ha_min=50)
    _proc(db, "0000012-00.2026.8.12.0001", ha_min=20)

    with lote_em_execucao(45):
        out = reapear_lotes_orfaos(db)

    assert cadastros == [(CLIENTE_MASTER, 44, [do_44.id])]
    assert out["fechados"] == [44]
    assert db.get(BbAtivosLote, 45).status == LOTE_EM_ANDAMENTO


def test_importacao_interrompida_no_meio_pede_o_arquivo_de_novo(db, cadastros):
    _lote(db, 46, total=23, processados=10)

    reapear_lotes_orfaos(db)

    lote = db.get(BbAtivosLote, 46)
    assert lote.status == LOTE_ERRO
    assert "10 de 23" in lote.erro and "Suba o arquivo de novo" in lote.erro
    assert len(lote.erro) < 250


def test_falha_na_retomada_nao_segura_o_lote_nem_vira_mensagem(db, monkeypatch):
    def estoura(db, lote_id, ids):
        raise ImportL1Error("GetImportDataPaginated 401 na página 0")

    monkeypatch.setattr(master_service, "_cadastrar_lote", estoura)
    _lote(db, 47)
    _proc(db, "0000021-00.2026.8.12.0001")

    out = reapear_lotes_orfaos(db)

    lote = db.get(BbAtivosLote, 47)
    assert out["fechados"] == [47]
    assert lote.status == LOTE_CONCLUIDO and lote.erro is None


def test_lote_da_ativos_retoma_pelo_cadastro_da_ativos(db, cadastros):
    _lote(db, 48, cliente=CLIENTE_ATIVOS, total=56, processados=56)
    p = _proc(db, "0000031-00.2026.8.26.0001", cliente=CLIENTE_ATIVOS)

    reapear_lotes_orfaos(db)

    assert cadastros == [(CLIENTE_ATIVOS, 48, [p.id])]


def test_thread_do_master_segura_a_trava_enquanto_ingere(db, monkeypatch):
    import app.db.session as sessao

    fabrica = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(sessao, "SessionLocal", fabrica)
    monkeypatch.setattr(master_service, "distribuir_processo", lambda db, proc: None)
    vivo_durante_o_cadastro = []
    monkeypatch.setattr(
        master_service, "_cadastrar_lote",
        lambda db, lote_id, ids: vivo_durante_o_cadastro.append(lote_esta_vivo(lote_id)),
    )
    lote = BbAtivosLote(cliente=CLIENTE_MASTER, nome_arquivo="Listagem.xlsx", total=1)
    db.add(lote)
    db.commit()
    cnj = "7054679-28.2026.8.22.0001"
    linhas = [{
        "cnj": cnj, "digitos": "70546792820268220001",
        "raw": {
            "Processo": cnj, "Autores": "FULANO DE TAL", "Ação": "Procedimento Comum Cível",
            "UF": "RO", "Comarca": "Porto Velho", "Data ajuizamento": "10/09/2026",
            "Valor da Causa": "1.000,00",
        },
    }]

    master_service.ingerir_lote_background(lote.id, linhas)

    db.expire_all()
    assert vivo_durante_o_cadastro == [True]
    assert lote_esta_vivo(lote.id) is False
    lote = db.get(BbAtivosLote, lote.id)
    assert lote.status == LOTE_CONCLUIDO and lote.criados == 1
