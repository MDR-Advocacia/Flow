# -*- coding: utf-8 -*-
"""Processo que não virou pasta no L1 TEM que ter o motivo (passagem 251, 12/09/2026).

Dois processos com ciência dada no BB, planilha enviada, e o import respondeu
"nada novo a cadastrar" — as linhas nem tinham voltado da revisão do L1. Sem
descarte e sem CNJ pra casar (pré-processo), nenhum motivo foi gravado, a
passagem ficou verde com 0 pastas e ninguém tentou de novo.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.distribuidos_bb import (
    CLIENTE_BB,
    NIVEL_ERRO,
    NIVEL_SUCESSO,
    POOL_CADASTRADO_L1,
    POOL_PENDENTE_CADASTRO,
    PROC_DISTRIBUIDO,
    BbEvento,
    BbPlanilha,
    BbProcesso,
    BbRun,
)
from app.services.distribuidos_bb import cadastro_descartes as descartes
from app.services.distribuidos_bb import import_l1_service as imp

AGORA = datetime.now(timezone.utc)
TOK = {"token": "t", "subscriptionKey": "k", "tenancy": "x", "distribution": "y"}
_seq = iter(range(1, 100_000))


# ── infra ──────────────────────────────────────────────────────────────────


def _run(db):
    r = BbRun(status="CONCLUIDO", iniciado_em=AGORA - timedelta(hours=2),
              total_coletados=2, total_ciencia=2, total_distribuidos=2)
    db.add(r)
    db.flush()
    return r


def _planilha(db, run=None, *, subida=False, criada_ha=timedelta(hours=2), subida_ha=None,
              total=2, nome="PLANILHA_MIGRACAO_DISTRIBUIDOS_BB_20260912_1201.xlsx"):
    pl = BbPlanilha(
        run_id=run.id if run else None, nome_arquivo=nome, conteudo=b"xlsx",
        total_processos=total, tamanho_bytes=4, subido_legalone=subida,
        subido_em=(AGORA - subida_ha) if subida_ha is not None else None,
        created_at=AGORA - criada_ha,
    )
    db.add(pl)
    db.flush()
    return pl


def _proc(db, pl, run=None, *, status_pool=POOL_PENDENTE_CADASTRO, erro=None, raw=None):
    n = next(_seq)
    p = BbProcesso(
        fingerprint=f"npj:teste-{n}", cliente=CLIENTE_BB, status=PROC_DISTRIBUIDO,
        planilha_status=status_pool, planilha_id=pl.id if pl else None,
        run_id=run.id if run else None, npj=f"2025/03{n:05d}-000", cnj=None,
        erro=erro, raw=raw, ciencia_dada_em=AGORA - timedelta(hours=2),
        planilha_gerada_em=pl.created_at if pl else None,
    )
    db.add(p)
    db.flush()
    return p


def _eventos(db, processo_id=None, acao=None):
    q = db.query(BbEvento)
    if processo_id is not None:
        q = q.filter(BbEvento.processo_id == processo_id)
    if acao:
        q = q.filter(BbEvento.acao == acao)
    return q.all()


def _rel_nada_voltou(esperadas=2):
    rel = {"novos": 0, "descartadas": [], "incompleto": True, "dry_run": False,
           "linhas_esperadas": esperadas, "linhas_encontradas": 0,
           "linhas_nao_encontradas": esperadas}
    rel["resultado"] = imp.motivo_nao_cadastro(rel)
    return rel


# ── import: relatório honesto ──────────────────────────────────────────────


class _Staging:
    """Revisão do import: baseline na 1ª leitura; as linhas desta planilha
    aparecem a partir da leitura `aparece_na` depois do upload."""

    def __init__(self, baseline, novas, aparece_na=1):
        self.baseline, self.novas, self.aparece_na = baseline, novas, aparece_na
        self.chamadas = 0

    def __call__(self, sess, h):
        self.chamadas += 1
        if self.chamadas == 1:
            return list(self.baseline)
        chegou = self.chamadas - 1 >= self.aparece_na
        return list(self.baseline) + (list(self.novas) if chegou else [])


@pytest.fixture
def l1(monkeypatch):
    salvos = []
    monkeypatch.setattr(imp, "_import_status", lambda sess, h: {"isLoadingData": False,
                                                                "revisingLitigationsCount": 0})
    monkeypatch.setattr(imp, "_get_sas", lambda *a, **k: "https://blob/x?sig=1")
    monkeypatch.setattr(imp, "_upload_blob", lambda *a, **k: None)
    monkeypatch.setattr(imp, "_already_processing", lambda *a, **k: False)
    monkeypatch.setattr(imp, "_spreadsheet_load", lambda *a, **k: {"message": "iniciado"})
    monkeypatch.setattr(imp, "_save",
                        lambda sess, h, selected_ids=None: salvos.append(list(selected_ids)) or {"message": "ok"})
    monkeypatch.setattr(imp, "_ESPERA_LINHAS_S", 0)
    return salvos


def test_linha_que_nao_volta_da_revisao_nao_vira_nada_novo(monkeypatch, l1):
    staging = _Staging(baseline=[{"id": 900}], novas=[{"id": 1}, {"id": 2}], aparece_na=99)
    monkeypatch.setattr(imp, "_listar_staging", staging)

    rel = imp._cadastrar_once(b"x", "p.xlsx", firm_id=1, dry_run=False, poll_max_s=5,
                              tok=TOK, esperadas=2)

    assert rel["novos"] == 0 and rel["incompleto"] is True
    assert (rel["linhas_esperadas"], rel["linhas_encontradas"], rel["linhas_nao_encontradas"]) == (2, 0, 2)
    assert "já existem" not in rel["resultado"]
    assert "não devolveu 2 de 2" in rel["resultado"]
    assert l1 == []
    assert staging.chamadas == 2 + imp._ESPERA_LINHAS_TENTATIVAS


def test_espera_as_linhas_que_demoram_a_aparecer(monkeypatch, l1):
    staging = _Staging(baseline=[{"id": 900}], novas=[{"id": 1}, {"id": 2}], aparece_na=3)
    monkeypatch.setattr(imp, "_listar_staging", staging)

    rel = imp._cadastrar_once(b"x", "p.xlsx", firm_id=1, dry_run=False, poll_max_s=5,
                              tok=TOK, esperadas=2)

    assert rel["novos"] == 2 and rel["incompleto"] is False
    assert l1 == [[1, 2]]


def test_parcial_diz_quantas_linhas_faltaram(monkeypatch, l1):
    monkeypatch.setattr(imp, "_listar_staging", _Staging(baseline=[], novas=[{"id": 1}]))

    rel = imp._cadastrar_once(b"x", "p.xlsx", firm_id=1, dry_run=False, poll_max_s=5,
                              tok=TOK, esperadas=2)

    assert rel["novos"] == 1 and rel["incompleto"] is True and rel["linhas_nao_encontradas"] == 1
    assert "1 linha(s) da planilha não voltaram" in rel["resultado"]


@pytest.mark.parametrize("rel, trecho", [
    ({"linhas_esperadas": 2, "linhas_nao_encontradas": 2, "incompleto": True}, "não devolveu 2 de 2"),
    ({"incompleto": True}, "nenhuma linha desta planilha voltou"),
    ({"descartadas": [{"id": 1}], "incompleto": False}, "1 linha(s) recusada(s)"),
])
def test_motivo_diz_o_que_aconteceu_e_nunca_que_ja_existe(rel, trecho):
    motivo = imp.motivo_nao_cadastro(rel)
    assert trecho in motivo and "já existem" not in motivo


# ── motivo gravado em cada processo ────────────────────────────────────────


def test_planilha_que_nao_enviou_nada_grava_motivo_em_todos_os_pendentes(db_session):
    run = _run(db_session)
    pl = _planilha(db_session, run)
    a, b = _proc(db_session, pl, run), _proc(db_session, pl, run)   # pré-processo: sem CNJ
    outra = _proc(db_session, _planilha(db_session, run, nome="OUTRA.xlsx"), run)
    ja_cadastrado = _proc(db_session, pl, run, status_pool=POOL_CADASTRADO_L1)
    db_session.commit()

    assert descartes.registrar_descartes(db_session, _rel_nada_voltou(), run_id=run.id,
                                         planilha_id=pl.id) == 2

    for p in (a, b):
        db_session.refresh(p)
        assert "não devolveu 2 de 2" in p.erro
        assert [e.acao for e in _eventos(db_session, p.id)] == ["Não cadastrado"]
    db_session.refresh(outra)
    db_session.refresh(ja_cadastrado)
    assert outra.erro is None and ja_cadastrado.erro is None


def test_sem_varredura_quando_algo_foi_enviado_ou_e_simulacao(db_session):
    run = _run(db_session)
    pl = _planilha(db_session, run)
    p = _proc(db_session, pl, run)
    db_session.commit()

    assert descartes.registrar_descartes(db_session, {"novos": 1, "descartadas": []}, planilha_id=pl.id) == 0
    assert descartes.registrar_descartes(
        db_session, {"novos": 0, "descartadas": [], "dry_run": True}, planilha_id=pl.id) == 0
    db_session.refresh(p)
    assert p.erro is None


def test_rede_de_seguranca_da_motivo_a_quem_ficou_mudo(db_session):
    run = _run(db_session)
    enviada = _planilha(db_session, run, subida=True, subida_ha=timedelta(hours=2), nome="ENVIADA.xlsx")
    recente = _planilha(db_session, run, subida=True, subida_ha=timedelta(minutes=10), nome="RECENTE.xlsx")
    nunca = _planilha(db_session, run, subida=False, nome="NUNCA.xlsx")
    p_enviada = _proc(db_session, enviada, run)
    p_recente = _proc(db_session, recente, run)
    p_nunca = _proc(db_session, nunca, run)
    p_com_motivo = _proc(db_session, enviada, run, erro="motivo anterior")
    p_tombamento = _proc(db_session, enviada, run, raw={"tombamento": {"lote": 1}})
    p_confirmado = _proc(db_session, enviada, run, status_pool=POOL_CADASTRADO_L1)
    db_session.commit()

    assert descartes.motivar_pendentes_sem_motivo(db_session, AGORA) == 2

    for p in (p_enviada, p_recente, p_nunca, p_com_motivo, p_tombamento, p_confirmado):
        db_session.refresh(p)
    assert "ENVIADA.xlsx foi enviada ao Legal One" in p_enviada.erro and "não apareceu" in p_enviada.erro
    assert p_recente.erro is None                      # o monitor ainda está confirmando
    assert "NUNCA.xlsx foi gerada" in p_nunca.erro and "retentativas" in p_nunca.erro
    assert p_com_motivo.erro == "motivo anterior"
    assert p_tombamento.erro is None and p_confirmado.erro is None
    assert descartes.motivar_pendentes_sem_motivo(db_session, AGORA) == 0   # não repete


def test_confirmacao_no_l1_limpa_o_motivo(db_session, monkeypatch):
    from app.services.distribuidos_bb import cadastro_l1
    from app.services.distribuidos_bb import cadastro_monitor_worker as worker

    run = _run(db_session)
    pl = _planilha(db_session, run, subida=True, subida_ha=timedelta(hours=2))
    p = _proc(db_session, pl, run, erro="Nenhuma pasta criada: ...")
    db_session.commit()
    monkeypatch.setattr(cadastro_l1, "resolver_office_por_path", lambda client, path: 2)
    monkeypatch.setattr(cadastro_l1, "buscar_lawsuit_por_npj",
                        lambda client, npj: [{"id": 99068, "folder": "Proc - 0093132", "office": 2}])
    monkeypatch.setattr("app.services.distribuidos_bb.etiqueta_nerc_service.etiquetar_nerc_pendentes",
                        lambda db: None)

    assert worker.verificar_pendentes(db_session, client=object())["confirmados"] == 1
    db_session.refresh(p)
    assert p.planilha_status == POOL_CADASTRADO_L1 and p.erro is None and p.l1_folder == "Proc - 0093132"


# ── quem envia: retry e auto-cadastro ──────────────────────────────────────


def test_retry_sem_linha_na_revisao_nao_marca_subida_nem_sucesso(db_session, monkeypatch):
    from app.services.distribuidos_bb import cadastro_monitor_worker as worker

    run = _run(db_session)
    pl = _planilha(db_session, run, subida=False, criada_ha=timedelta(minutes=30))
    a, b = _proc(db_session, pl, run), _proc(db_session, pl, run)
    db_session.commit()
    monkeypatch.setattr("app.services.distribuidos_bb.import_l1_service.cadastrar_planilha",
                        lambda *args, **k: _rel_nada_voltou(k.get("esperadas")))
    monkeypatch.setattr("app.services.distribuidos_bb.planilha_service.cnjs_liberados_da_planilha",
                        lambda *args, **k: set())
    monkeypatch.setattr("app.services.distribuidos_bb.alertas.alertar_falha_cadastro", lambda **k: None)

    worker.retentar_planilhas_orfas(db_session)

    db_session.refresh(pl)
    db_session.refresh(run)
    assert pl.subido_legalone is False                 # volta pra retentativa
    assert run.total_cadastrados == 0
    acoes = [e.acao for e in _eventos(db_session)]
    assert "Retry do auto-cadastro sem pasta" in acoes and "Retry do auto-cadastro OK" not in acoes
    for p in (a, b):
        db_session.refresh(p)
        assert "não devolveu 2 de 2" in p.erro


def _auto_cadastro_falso(monkeypatch, pl, rel):
    monkeypatch.setattr("app.services.distribuidos_bb.planilha_service.gerar_e_persistir",
                        lambda db, **k: pl)
    monkeypatch.setattr("app.services.distribuidos_bb.planilha_service.cnjs_liberados_da_planilha",
                        lambda *args, **k: set())
    monkeypatch.setattr("app.services.distribuidos_bb.import_l1_service.cadastrar_planilha",
                        lambda *args, **k: rel)
    monkeypatch.setattr("app.services.distribuidos_bb.cadastro_conferencia.conferir_duplicacao",
                        lambda *args, **k: None)


def test_auto_cadastro_que_nao_cria_pasta_nao_fica_verde(db_session, monkeypatch):
    from app.services.distribuidos_bb import coleta_service

    run = _run(db_session)
    pl = _planilha(db_session, None, criada_ha=timedelta(0))
    a, b = _proc(db_session, pl, run), _proc(db_session, pl, run)
    db_session.commit()
    _auto_cadastro_falso(monkeypatch, pl, _rel_nada_voltou())

    coleta_service._auto_cadastrar(db_session, run)

    db_session.refresh(pl)
    db_session.refresh(run)
    assert pl.subido_legalone is False and pl.run_id == run.id
    assert run.total_cadastrados == 0
    evento = _eventos(db_session, acao="Auto-cadastro incompleto")
    assert len(evento) == 1 and evento[0].nivel == NIVEL_ERRO and "0 de 2" in evento[0].mensagem
    assert not _eventos(db_session, acao="Auto-cadastro enviado")
    for p in (a, b):
        db_session.refresh(p)
        assert "não devolveu 2 de 2" in p.erro


def test_auto_cadastro_completo_continua_sucesso(db_session, monkeypatch):
    from app.services.distribuidos_bb import coleta_service

    run = _run(db_session)
    pl = _planilha(db_session, None, criada_ha=timedelta(0))
    _proc(db_session, pl, run)
    _proc(db_session, pl, run)
    db_session.commit()
    _auto_cadastro_falso(monkeypatch, pl, {"novos": 2, "descartadas": [], "incompleto": False,
                                           "resultado": "2 linha(s) enviada(s)", "dry_run": False})

    coleta_service._auto_cadastrar(db_session, run)

    db_session.refresh(pl)
    db_session.refresh(run)
    assert pl.subido_legalone is True and run.total_cadastrados == 2
    evento = _eventos(db_session, acao="Auto-cadastro enviado")
    assert len(evento) == 1 and evento[0].nivel == NIVEL_SUCESSO


# ── onde o operador vê ─────────────────────────────────────────────────────


def test_passagem_mostra_quantos_ficaram_sem_pasta_e_por_que(db_session):
    from app.api.v1.endpoints.distribuidos_bb import _pendencias_de_cadastro

    run, outra = _run(db_session), _run(db_session)
    pl = _planilha(db_session, run)
    _proc(db_session, pl, run, erro="Nenhuma pasta criada: X")
    _proc(db_session, pl, run)                                   # ainda sem motivo
    _proc(db_session, pl, run, status_pool=POOL_CADASTRADO_L1)
    modo_seguro = _proc(db_session, pl, run)
    modo_seguro.ciencia_dada_em = None                           # sem ciência não conta
    db_session.commit()

    assert _pendencias_de_cadastro(db_session, [run.id, outra.id]) == {
        run.id: {"sem_cadastro": 2, "motivos_sem_cadastro": ["Nenhuma pasta criada: X"]},
    }


def test_vigia_acusa_ciencia_sem_pasta_depois_de_3h(db_session):
    from app.services import vigia_filas as vf

    run = _run(db_session)
    velha = _planilha(db_session, run, criada_ha=timedelta(hours=5))
    _proc(db_session, velha, run, erro="Nenhuma pasta criada: o Legal One não devolveu 2 de 2 linha(s)")
    _proc(db_session, _planilha(db_session, run, criada_ha=timedelta(minutes=30)), run)
    db_session.commit()

    achados = vf._ciencia_sem_pasta(db_session, AGORA)

    assert len(achados) == 1 and achados[0].gravidade == vf.GRAVE
    assert "1 processo" in achados[0].mensagem and "não devolveu 2 de 2" in achados[0].mensagem


# ── envio que estoura (passagem 257, 14/09/2026) ───────────────────────────


@pytest.fixture
def db_real():
    """Sessão que commita de verdade: o caminho de erro faz rollback, e na sessão
    transacional do conftest o rollback apaga as linhas do próprio teste."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.session import Base

    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def test_envio_que_estoura_grava_motivo_na_hora(db_real, monkeypatch):
    from app.services.distribuidos_bb import coleta_service

    run = _run(db_real)
    pl = _planilha(db_real, None, criada_ha=timedelta(0))
    p = _proc(db_real, pl, run)
    db_real.commit()
    _auto_cadastro_falso(monkeypatch, pl, None)

    def _explode(*args, **k):
        raise imp.ImportL1Error('GetImportDataPaginated 401 na página 0: {"error":{"code":"Unauthorized"}}')

    monkeypatch.setattr("app.services.distribuidos_bb.import_l1_service.cadastrar_planilha", _explode)

    with pytest.raises(imp.ImportL1Error):
        coleta_service._auto_cadastrar(db_real, run)

    db_real.refresh(p)
    db_real.refresh(pl)
    assert "falhou" in p.erro and "401" in p.erro and "tenta de novo" in p.erro
    assert pl.subido_legalone is False
    assert [e.acao for e in _eventos(db_real, p.id)] == ["Não cadastrado"]


def test_retry_que_estoura_grava_motivo(db_real, monkeypatch):
    from app.services.distribuidos_bb import cadastro_monitor_worker as worker

    run = _run(db_real)
    pl = _planilha(db_real, run, subida=False, criada_ha=timedelta(minutes=30))
    p = _proc(db_real, pl, run)
    db_real.commit()

    def _explode(*args, **k):
        raise imp.ImportL1Error("GetStorageSas 401: Unauthorized")

    monkeypatch.setattr("app.services.distribuidos_bb.import_l1_service.cadastrar_planilha", _explode)
    monkeypatch.setattr("app.services.distribuidos_bb.planilha_service.cnjs_liberados_da_planilha",
                        lambda *args, **k: set())
    monkeypatch.setattr("app.services.distribuidos_bb.alertas.alertar_falha_cadastro", lambda **k: None)

    worker.retentar_planilhas_orfas(db_real)

    db_real.refresh(p)
    assert "GetStorageSas 401" in p.erro


class _Token:
    def __init__(self):
        self.pedidos = []

    def __call__(self, forcar=False):
        self.pedidos.append(forcar)
        return dict(TOK)


def test_401_de_sessao_derrubada_espera_e_recaptura_mais_uma_vez(monkeypatch, tmp_path):
    token, dormidas, chamadas = _Token(), [], []
    monkeypatch.setattr(imp, "obter_token", token)
    monkeypatch.setattr(imp, "_dormir", dormidas.append)
    monkeypatch.setattr(imp, "_TOKEN_CACHE", tmp_path / "token.json")

    def _once(*args, **k):
        chamadas.append(k["tok"])
        if len(chamadas) < 3:
            raise imp.ImportL1Error("GetImportDataPaginated 401 na página 0: Unauthorized")
        return {"novos": 1}

    monkeypatch.setattr(imp, "_cadastrar_once", _once)

    assert imp.cadastrar_planilha(b"x", "p.xlsx", dry_run=False, esperadas=1) == {"novos": 1}
    assert token.pedidos == [False, True, True]
    assert dormidas == [s for s in imp._ESPERAS_APOS_401_S if s]


def test_401_que_nao_passa_desiste_e_apaga_o_token(monkeypatch, tmp_path):
    cache = tmp_path / "token.json"
    cache.write_text("{}")
    token = _Token()
    monkeypatch.setattr(imp, "obter_token", token)
    monkeypatch.setattr(imp, "_dormir", lambda s: None)
    monkeypatch.setattr(imp, "_TOKEN_CACHE", cache)

    def _once(*args, **k):
        raise imp.ImportL1Error("save 401: Unauthorized")

    monkeypatch.setattr(imp, "_cadastrar_once", _once)

    with pytest.raises(imp.ImportL1Error):
        imp.cadastrar_planilha(b"x", "p.xlsx", dry_run=False, esperadas=1)
    assert not cache.exists()
    assert len(token.pedidos) == 1 + len(imp._ESPERAS_APOS_401_S)


def test_erro_que_nao_e_401_nao_repete(monkeypatch):
    chamadas = []
    monkeypatch.setattr(imp, "obter_token", _Token())
    monkeypatch.setattr(imp, "_dormir", lambda s: None)

    def _once(*args, **k):
        chamadas.append(1)
        raise imp.ImportL1Error("Fila de revisão do Legal One com 5000 linha(s)")

    monkeypatch.setattr(imp, "_cadastrar_once", _once)

    with pytest.raises(imp.ImportL1Error):
        imp.cadastrar_planilha(b"x", "p.xlsx", dry_run=False)
    assert chamadas == [1]
