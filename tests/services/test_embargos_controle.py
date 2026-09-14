# -*- coding: utf-8 -*-
"""Controle de Embargos — visão única das filas de embargos à execução (14/09/2026).

Decisões do operador: um caso por execução + embargos; pasta incidental existe
no L1 = trabalho feito; publicação caída na pasta da execução sem incidente é
"embargos identificados" (circunstância, não falha); só BB Autor; mesmo embargo nas duas filas vira um caso só
(a 1ª fonte cria a tarefa, a 2ª só vincula). Casos baseados nos dados medidos
em produção em 14/09 (publicações 95719, 88190, 98213 etc.).
"""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.embargos_execucao import (
    CASO_CADASTRADO,
    CASO_DESCARTADO,
    CASO_PENDENTE,
    DECISAO_RECUSADO,
    ESTADO_ENCONTRADO,
    ESTADO_JA_CADASTRADO,
    ESTADO_MONITORANDO,
    NIVEL_CONFIRMADO_DJEN,
    NIVEL_PUBLICACAO,
    ORIG_PUB_INCIDENTE,
    ORIG_PUB_NA_PASTA,
    ORIG_PUB_SEM_PASTA,
    ORIG_TRIBUNAL,
    PUB_IGNORADA_MONITORIA,
    EmbCandidato,
    EmbCaso,
    EmbCasoPublicacao,
    EmbEvento,
    EmbExecucao,
    EmbParte,
)
from app.models.publication_search import (
    RECORD_STATUS_CLASSIFIED,
    RECORD_STATUS_IGNORED,
    RECORD_STATUS_SCHEDULED,
    SEARCH_STATUS_COMPLETED,
    PublicationRecord,
    PublicationSearch,
)
from app.services.embargos_execucao import controle, monitor, service
from app.services.embargos_execucao.datajud_embargos import CapaExecucao

AGORA = datetime.now(timezone.utc)
EXEC_CNJ = "0811047-19.2024.8.14.0005"
EMB_CNJ = "0800486-96.2025.8.14.0005"
_uid = iter(range(700000, 799999))


@pytest.fixture(autouse=True)
def params(monkeypatch):
    store = {}
    for mod in (service, controle):
        monkeypatch.setattr(mod, "get_setting", lambda k, d=None: store.get(k, d), raising=False)
        monkeypatch.setattr(mod, "set_setting", lambda k, v, description=None: store.__setitem__(k, v), raising=False)
    return store


@pytest.fixture
def busca(db_session):
    b = PublicationSearch(date_from="2026-09-01T00:00:00Z", status=SEARCH_STATUS_COMPLETED)
    db_session.add(b)
    db_session.commit()
    return b


def _exe(db, *, pasta="Proc - 0018810", cnj=EXEC_CNJ, estado=ESTADO_MONITORANDO, orgao=None, partes=()):
    exe = EmbExecucao(
        pasta=pasta, cnj=cnj, cnj_digitos="".join(ch for ch in cnj if ch.isdigit()), cliente="BB",
        origem="RELATORIO", data_ajuizamento=date(2026, 5, 1), estado=estado, dias_uteis_janela=15,
        proxima_consulta=date(2026, 9, 20), consultas_feitas=1, partes_status="OK", partes_tentativas=0,
        orgao_codigo=orgao,
    )
    for nome in partes:
        exe.partes.append(EmbParte(origem="BB", polo="Passivo", nome=nome, demandada=True))
    db.add(exe)
    db.commit()
    return exe


def _pub_sem_pasta(db, busca, *, cnj=EMB_CNJ, cnj_execucao=EXEC_CNJ, cliente="Banco do Brasil", task=474939,
                   status=RECORD_STATUS_SCHEDULED, nossos=None, embargante="A. DE L. P. CORRADI LTDA"):
    ctx = {
        "tipo": "Embargos à Execução", "cliente": cliente, "cnjs": [cnj],
        "nossos": nossos if nossos is not None else ([{"cnj": cnj_execucao, "lawsuit_id": 19000}] if cnj_execucao else []),
        "ficha": {"cnj": cnj, "cnj_execucao": cnj_execucao, "embargante": embargante, "embargado": f"{cliente} S.A."},
    }
    if task:
        ctx["agendamento_automatico"] = {"task_id": task}
    r = PublicationRecord(
        search_id=busca.id, legal_one_update_id=next(_uid), status=status, category="Embargos à Execução",
        subcategory="-", linked_lawsuit_id=None, raw_relationships={"_sem_pasta": ctx},
        description=f"Processo {cnj} EMBARGOS À EXECUÇÃO (172) EMBARGANTE: {embargante} EMBARGADO: BANCO DO BRASIL",
        publication_date="2026-09-09T00:00:00Z", created_at=AGORA,
    )
    db.add(r)
    db.commit()
    return r


def _pub_na_pasta(db, busca, *, lawsuit_id, cnj_pasta, texto, office=22, status=RECORD_STATUS_CLASSIFIED):
    r = PublicationRecord(
        search_id=busca.id, legal_one_update_id=next(_uid), status=status,
        category="Defesa do Devedor e Incidentes", subcategory="Embargos à execução / monitórios",
        linked_lawsuit_id=lawsuit_id, linked_lawsuit_cnj=cnj_pasta, linked_office_id=office,
        description=texto, publication_date="2026-09-12T00:00:00Z", created_at=AGORA,
    )
    db.add(r)
    db.commit()
    return r


class L1Falso:
    base_url = "https://l1"

    def __init__(self, *, incidentes=None, pastas=None, por_cnj=None, incidentes_da_pasta=None):
        self.incidentes = incidentes or {}          # id -> ProceduralIssue
        self.pastas = pastas or {}                  # id -> {folder, identifierNumber}
        self.por_cnj = por_cnj or {}                # digitos -> {id}
        self.incidentes_da_pasta = incidentes_da_pasta or {}  # pasta -> [..]
        self.chamadas = []

    def _request_with_retry(self, method, url, params=None):
        f = params["$filter"]
        self.chamadas.append(f)
        if f.startswith("id eq "):
            item = self.incidentes.get(int(f[6:]))
            valor = [item] if item else []
        else:
            pasta = f.split("'")[1].rstrip("/")
            valor = self.incidentes_da_pasta.get(pasta, [])
        return SimpleNamespace(json=lambda: {"value": valor})

    def get_lawsuit_by_id(self, lawsuit_id, params=None):
        return self.pastas.get(lawsuit_id, {})

    def search_lawsuit_by_cnj(self, cnj):
        self.chamadas.append(f"cnj {cnj}")
        return self.por_cnj.get("".join(ch for ch in cnj if ch.isdigit()))


def _datajud(classes=None, orgaos=None):
    classes, orgaos = classes or {}, orgaos or {}

    def capa(cnj):
        d = "".join(ch for ch in cnj if ch.isdigit())
        if d not in classes and d not in orgaos:
            return None
        cod, nome = classes.get(d, (12154, "Execução de Título Extrajudicial"))
        return CapaExecucao(alias="api_publica_tjpa", orgao_codigo=orgaos.get(d), orgao_nome="VARA",
                            classe_codigo=cod, classe_nome=nome, grau="G1",
                            data_ajuizamento=None, data_ajuizamento_raw=None)

    return SimpleNamespace(consultar_capa=capa)


def _dig(c):
    return "".join(ch for ch in c if ch.isdigit())


# ── publicação sem pasta ─────────────────────────────────────────────
def test_publicacao_sem_pasta_vira_caso_vincula_a_execucao_e_encerra_o_monitor_sem_aviso(db_session, busca):
    exe = _exe(db_session)
    _pub_sem_pasta(db_session, busca)
    resumo = controle.sincronizar(db_session, None, _datajud())
    caso = db_session.query(EmbCaso).one()
    assert caso.estado == CASO_PENDENTE and caso.origem_pub_sem_pasta and caso.primeira_origem == ORIG_PUB_SEM_PASTA
    assert caso.execucao_id == exe.id and caso.tarefa_l1_id == 474939 and caso.pasta_execucao == "Proc - 0018810"
    assert exe.estado == ESTADO_ENCONTRADO and exe.aviso_enviado_em is not None and exe.proxima_consulta is None
    assert [c.nivel for c in exe.candidatos] == [NIVEL_PUBLICACAO]
    assert resumo["monitores_encerrados"] == 1

    # Idempotente: a mesma publicação não entra de novo.
    controle.sincronizar(db_session, None, _datajud())
    assert db_session.query(EmbCaso).count() == 1 and db_session.query(EmbCasoPublicacao).count() == 1


def test_sem_pasta_de_outro_cliente_ou_ignorada_fica_de_fora(db_session, busca):
    _pub_sem_pasta(db_session, busca, cliente="Banco Master", cnj="7000001-00.2026.8.22.0001")
    _pub_sem_pasta(db_session, busca, status=RECORD_STATUS_IGNORED, cnj="7000002-00.2026.8.22.0001")
    resumo = controle.sincronizar(db_session, None, _datajud())
    assert db_session.query(EmbCaso).count() == 0 and resumo["sem_pasta_outro_cliente"] == 1


# ── publicação com pasta ─────────────────────────────────────────────
def test_publicacao_na_pasta_do_incidente_e_caso_cadastrado(db_session, busca):
    # Caso real 88190: publicação na "Proc - 0016326/001", mãe 16526.
    _pub_na_pasta(db_session, busca, lawsuit_id=90001, cnj_pasta="0800414-98.2026.8.14.0062",
                  texto="Processo 0800414-98.2026.8.14.0062 EMBARGOS À EXECUÇÃO intimação")
    l1 = L1Falso(
        incidentes={90001: {"id": 90001, "folder": "Proc - 0016326/001", "title": "Embargos à Execução",
                            "identifierNumber": "08004149820268140062", "relatedLitigationId": 16526}},
        pastas={16526: {"folder": "Proc - 0016326", "identifierNumber": "0004008-05.2013.8.14.0097"}},
    )
    controle.sincronizar(db_session, l1, _datajud())
    caso = db_session.query(EmbCaso).one()
    assert caso.estado == CASO_CADASTRADO and caso.origem_pub_incidente
    assert caso.incidente_folder == "Proc - 0016326/001" and caso.pasta_execucao == "Proc - 0016326"
    assert not caso.falha_cadastro


def test_publicacao_na_pasta_da_execucao_e_falha_de_cadastro_com_cnj_dos_embargos_pelo_datajud(db_session, busca):
    # Caso real 98213: publicação na pasta da execução citando outro CNJ.
    exe = _exe(db_session, pasta="Proc - 0053919", cnj="7006952-65.2025.8.22.0015")
    _pub_na_pasta(db_session, busca, lawsuit_id=57000, cnj_pasta="7006952-65.2025.8.22.0015",
                  texto="Processo 7003319-17.2023.8.22.0015 EMBARGOS À EXECUÇÃO — execução 7006952-65.2025.8.22.0015; "
                        "ver também 1234567-00.2020.8.22.0015")
    l1 = L1Falso(pastas={57000: {"folder": "Proc - 0053919"}})
    dj = _datajud(classes={_dig("7003319-17.2023.8.22.0015"): (172, "Embargos à Execução"),
                           _dig("1234567-00.2020.8.22.0015"): (1116, "Execução Fiscal"),
                           _dig("7006952-65.2025.8.22.0015"): (12154, "Execução de Título Extrajudicial")})
    controle.sincronizar(db_session, l1, dj)
    caso = db_session.query(EmbCaso).one()
    assert caso.falha_cadastro and caso.estado == CASO_PENDENTE and caso.origem_pub_na_pasta
    assert caso.cnj_embargos == "7003319-17.2023.8.22.0015" and caso.execucao_id == exe.id
    assert "cnj 7003319-17.2023.8.22.0015" in l1.chamadas  # conferiu no L1 antes de cobrar


def test_cnj_de_jurisprudencia_de_outra_comarca_nao_vira_embargos(db_session, busca):
    # Caso real do teste local (publicação #4516): decisão numa execução do TJRN
    # cita embargos do TJAL (0705825-83.2019.8.02.0001) como jurisprudência e diz
    # que os embargos foram opostos nos próprios autos.
    _pub_na_pasta(db_session, busca, lawsuit_id=61000, cnj_pasta="0803893-84.2024.8.20.5112",
                  texto="Intimado, o executado opôs embargos à execução nos próprios autos. "
                        "Nesse sentido: TJAL, 0705825-83.2019.8.02.0001.")
    dj = _datajud(classes={_dig("0705825-83.2019.8.02.0001"): (172, "Embargos à Execução")})
    r = controle.sincronizar(db_session, L1Falso(pastas={61000: {"folder": "Proc - 0018783"}}), dj)
    assert db_session.query(EmbCaso).count() == 0 and r["proprios_autos_ignorada"] == 1


def test_monitoria_fica_de_fora_e_nao_e_reconsultada(db_session, busca):
    _pub_na_pasta(db_session, busca, lawsuit_id=58000, cnj_pasta="0868243-55.2026.8.20.5001",
                  texto="Classe: MONITÓRIA — embargos monitórios")
    dj = _datajud(classes={_dig("0868243-55.2026.8.20.5001"): (40, "Monitória")})
    r = controle.sincronizar(db_session, L1Falso(), dj)
    assert r["monitoria_ignorada"] == 1 and db_session.query(EmbCaso).count() == 0
    assert db_session.query(EmbCasoPublicacao).one().origem == PUB_IGNORADA_MONITORIA
    assert "monitoria_ignorada" not in controle.sincronizar(db_session, L1Falso(), dj)


def test_na_pasta_da_execucao_declaracao_e_proprios_autos_ficam_de_fora(db_session, busca):
    # Casos reais do teste local de 14/09: subcategoria mistura embargos de
    # declaração; e embargos opostos nos próprios autos não têm processo apartado.
    l1 = L1Falso(pastas={60000: {"folder": "Proc - 0021593"}, 60001: {"folder": "Proc - 0018783"},
                         60002: {"folder": "Proc - 0052610"}})
    _pub_na_pasta(db_session, busca, lawsuit_id=60000, cnj_pasta="7007642-39.2025.8.22.0001",
                  texto="INTIMAÇÃO AUTOR - EMBARGOS DE DECLARAÇÃO Fica a parte AUTORA intimada")
    _pub_na_pasta(db_session, busca, lawsuit_id=60001, cnj_pasta="0700000-00.2019.8.02.0001",
                  texto="Intimado, o executado opôs embargos à execução nos próprios autos (ID 189537946)")
    _pub_na_pasta(db_session, busca, lawsuit_id=60002, cnj_pasta="0092586-19.2025.8.03.0001",
                  texto="Consta a juntada errônea de \"Embargos à Execução\" pela parte executada.")
    r = controle.sincronizar(db_session, l1, _datajud())
    assert r["nao_e_embargos_execucao"] == 1 and r["proprios_autos_ignorada"] == 1 and r["a_verificar"] == 1
    caso = db_session.query(EmbCaso).one()
    assert caso.pasta_execucao == "Proc - 0052610" and not caso.falha_cadastro and caso.cnj_embargos is None
    kpis = controle.listar(db_session)["kpis"]
    assert kpis["publicacoes_ignoradas"] == {"IGNORADA_NAO_EMBARGOS_EXECUCAO": 1, "IGNORADA_PROPRIOS_AUTOS": 1}


def test_publicacao_de_outro_escritorio_nao_entra(db_session, busca):
    _pub_na_pasta(db_session, busca, lawsuit_id=59000, cnj_pasta="0836021-08.2024.8.15.0001",
                  texto="EMBARGOS À EXECUÇÃO", office=61)
    controle.sincronizar(db_session, L1Falso(), _datajud())
    assert db_session.query(EmbCaso).count() == 0


# ── duas filas, um caso ──────────────────────────────────────────────
def test_mesmo_embargo_pelo_tribunal_e_pela_publicacao_vira_um_caso_so(db_session, busca):
    exe = _exe(db_session, estado=ESTADO_ENCONTRADO)
    exe.candidatos.append(EmbCandidato(cnj=EMB_CNJ, cnj_digitos=_dig(EMB_CNJ), nivel=NIVEL_CONFIRMADO_DJEN,
                                       decisao="PENDENTE", distribuicao_dependencia=True, peticao_mesmo_dia=False,
                                       djen_nomes_casados=["A. DE L. P. CORRADI LTDA"]))
    db_session.commit()
    controle.sincronizar(db_session, None, _datajud())
    _pub_sem_pasta(db_session, busca)
    controle.sincronizar(db_session, None, _datajud())
    caso = db_session.query(EmbCaso).one()
    assert caso.origem_tribunal and caso.origem_pub_sem_pasta and caso.primeira_origem == ORIG_TRIBUNAL
    assert controle.origens_do_caso(caso) == [ORIG_TRIBUNAL, ORIG_PUB_SEM_PASTA]
    msgs = [e.mensagem for e in db_session.query(EmbEvento).filter(EmbEvento.caso_id == caso.id)]
    assert any("chegou também por publicação sem pasta" in m for m in msgs)


def test_monitor_nao_repete_aviso_de_embargos_que_chegaram_por_publicacao(db_session, busca, params):
    from app.services.embargos_execucao import djen_embargos
    from app.services.embargos_execucao.datajud_embargos import CandidatoDataJud

    params[service.P_EMAILS] = "controladoria@mdradvocacia.com"
    emb = "7011603-39.2026.8.22.0005"
    # Publicação sem execução identificada: o caso existe, mas sem vínculo.
    _pub_sem_pasta(db_session, busca, cnj=emb, cnj_execucao=None, nossos=[])
    controle.sincronizar(db_session, None, _datajud())
    exe = _exe(db_session, pasta="Proc - 0068696", cnj="7007135-32.2026.8.22.0005", partes=["TEREZA MOREIRA REZENDE"])
    capa = CapaExecucao(alias="api_publica_tjro", orgao_codigo=4440, orgao_nome="VARA", classe_codigo=12154,
                        classe_nome="Execução", grau="G1", data_ajuizamento=datetime(2026, 5, 22),
                        data_ajuizamento_raw="20260522")
    datajud = SimpleNamespace(
        consultar_capa=lambda cnj: capa,
        buscar_candidatos=lambda capa, cnj_execucao, desde_raw: [CandidatoDataJud(
            cnj_digitos=_dig(emb), data_ajuizamento=datetime(2026, 8, 7, tzinfo=timezone.utc), classe_codigo=172,
            classe_nome="Embargos à Execução", orgao_nome="VARA", distribuicao_dependencia=False)],
        peticao_na_execucao_no_dia=lambda capa, dia: False,
    )
    djen = SimpleNamespace(
        confirmar_candidato=lambda cnj, refs, client=None, cliente="BB": djen_embargos.ResultadoDjen(
            status=djen_embargos.DJEN_CONFIRMADO, embargantes=["TEREZA MOREIRA REZENDE"],
            nomes_casados=["TEREZA MOREIRA REZENDE"]),
        executados_da_execucao=lambda cnj, c=None, cliente="BB": [],
    )
    enviados = []
    r = monitor.processar(db_session, exe, date(2026, 9, 14), datajud=datajud, djen=djen,
                          enviar_email=lambda *a: enviados.append(a) or True)
    db_session.commit()
    assert r == "encontrado" and enviados == [] and exe.aviso_enviado_em is not None
    assert any("já tinham chegado por publicação" in e.mensagem for e in db_session.query(EmbEvento))


def test_candidato_recusado_no_monitor_descarta_caso_so_do_tribunal(db_session):
    exe = _exe(db_session, estado=ESTADO_ENCONTRADO)
    cand = EmbCandidato(cnj=EMB_CNJ, cnj_digitos=_dig(EMB_CNJ), nivel=NIVEL_CONFIRMADO_DJEN, decisao="PENDENTE",
                        distribuicao_dependencia=False, peticao_mesmo_dia=False)
    exe.candidatos.append(cand)
    db_session.commit()
    controle.sincronizar(db_session, None, _datajud())
    cand.decisao = DECISAO_RECUSADO
    db_session.commit()
    controle.sincronizar(db_session, None, _datajud())
    assert db_session.query(EmbCaso).one().estado == CASO_DESCARTADO


# ── fim do caso: pasta existe ────────────────────────────────────────
def test_pasta_dos_embargos_no_l1_encerra_o_caso_e_o_monitor(db_session, busca):
    exe = _exe(db_session)
    _pub_sem_pasta(db_session, busca)
    l1 = L1Falso(por_cnj={_dig(EMB_CNJ): {"id": 70001}}, pastas={70001: {"folder": "Proc - 0018810/001"}})
    controle.sincronizar(db_session, l1, _datajud())
    caso = db_session.query(EmbCaso).one()
    assert caso.estado == CASO_CADASTRADO and caso.incidente_folder == "Proc - 0018810/001"
    assert exe.estado == ESTADO_JA_CADASTRADO and exe.incidente_folder == "Proc - 0018810/001"


def test_incidente_com_outro_cnj_na_pasta_nao_encerra_embargo_diferente(db_session, busca):
    _exe(db_session)
    _pub_sem_pasta(db_session, busca)
    l1 = L1Falso(incidentes_da_pasta={"Proc - 0018810": [
        {"id": 1, "folder": "Proc - 0018810/001", "title": "EMBARGOS À EXECUÇÃO", "identifierNumber": "0000001-11.2026.8.14.0005"},
    ]})
    controle.sincronizar(db_session, l1, _datajud())
    assert db_session.query(EmbCaso).one().estado == CASO_PENDENTE


def test_pasta_da_execucao_fora_do_monitor_vem_do_l1_e_deteccao_e_a_data_da_publicacao(db_session, busca):
    # Casos reais 95728/95682: CNJ da execução citado, execução fora do monitor.
    rec = _pub_sem_pasta(db_session, busca, cnj="0801117-42.2025.8.14.0069", cnj_execucao="0000421-20.2017.8.14.0069")
    rec.created_at = AGORA - timedelta(days=5)
    db_session.commit()
    l1 = L1Falso(por_cnj={_dig("0000421-20.2017.8.14.0069"): {"id": 15300}},
                 pastas={15300: {"folder": "Proc - 0015129"}})
    controle.sincronizar(db_session, l1, _datajud())
    caso = db_session.query(EmbCaso).one()
    assert caso.pasta_execucao == "Proc - 0015129" and caso.lawsuit_id_execucao in (15300, 19000)
    assert controle._utc(caso.detectado_em).date() == (AGORA - timedelta(days=5)).date()
    assert "startswith(folder,'Proc - 0015129/')" in l1.chamadas  # procurou o incidente na pasta achada


def test_verificacao_respeita_intervalo(db_session, busca):
    _exe(db_session)
    _pub_sem_pasta(db_session, busca)
    l1 = L1Falso()
    controle.sincronizar(db_session, l1, _datajud())
    n = len(l1.chamadas)
    controle.sincronizar(db_session, l1, _datajud())
    assert len(l1.chamadas) == n  # conferido há menos de 6 h


# ── casamento pela vara + embargante ─────────────────────────────────
def test_sem_cnj_da_execucao_casa_pela_vara_e_pela_parte(db_session, busca):
    exe = _exe(db_session, orgao=4440, partes=["TEREZA MOREIRA REZENDE"])
    _exe(db_session, pasta="Proc - 0099999", cnj="7000000-00.2026.8.22.0005", orgao=4440, partes=["OUTRA PESSOA SILVA"])
    _pub_sem_pasta(db_session, busca, cnj="7011603-39.2026.8.22.0005", cnj_execucao=None, nossos=[],
                   embargante="Tereza Moreira Rezende")
    controle.sincronizar(db_session, None, _datajud(orgaos={_dig("7011603-39.2026.8.22.0005"): 4440}))
    assert db_session.query(EmbCaso).one().execucao_id == exe.id


# ── leitura ──────────────────────────────────────────────────────────
def test_listar_etapas_kpis_e_filtros(db_session, busca):
    _exe(db_session, pasta="Proc - 0000001", cnj="7000001-00.2026.8.22.0005")
    _exe(db_session, pasta="Proc - 0018810")
    _pub_sem_pasta(db_session, busca)
    _pub_na_pasta(db_session, busca, lawsuit_id=57000, cnj_pasta="7006952-65.2025.8.22.0015", texto="EMBARGOS À EXECUÇÃO")
    controle.sincronizar(db_session, L1Falso(pastas={57000: {"folder": "Proc - 0053919"}}), _datajud())
    dados = controle.listar(db_session, etapa="pendente")
    assert dados["kpis"]["pendente"] == 2 and dados["kpis"]["pendente_a_verificar"] == 1
    assert dados["kpis"]["pendente_falha_cadastro"] == 0  # sem processo apartado identificado
    assert dados["kpis"]["vigilancia"] == 1  # a outra execução foi encerrada pela publicação
    assert controle.listar(db_session, etapa="pendente", a_verificar=True)["total"] == 1
    assert controle.listar(db_session, etapa="pendente", origem=ORIG_PUB_NA_PASTA)["items"][0]["pasta_execucao"] == "Proc - 0053919"
    vig = controle.listar(db_session, etapa="vigilancia")
    assert vig["items"][0]["tipo"] == "execucao" and vig["items"][0]["pasta_execucao"] == "Proc - 0000001"
    caso = controle.listar(db_session, etapa="pendente", busca="0800486")["items"][0]
    det = controle.detalhe(db_session, caso["id"])
    assert det["publicacoes"][0]["origem"] == ORIG_PUB_SEM_PASTA and det["execucao"]["pasta"] == "Proc - 0018810"


def test_descartar_exige_motivo_e_reabrir(db_session, busca):
    _pub_sem_pasta(db_session, busca, cnj_execucao=None, nossos=[])
    controle.sincronizar(db_session, None, _datajud())
    caso = db_session.query(EmbCaso).one()
    with pytest.raises(ValueError):
        controle.descartar(db_session, caso.id, motivo=" ")
    controle.descartar(db_session, caso.id, motivo="Embargos de outro credor")
    assert caso.estado == CASO_DESCARTADO
    controle.reabrir(db_session, caso.id)
    assert caso.estado == CASO_PENDENTE and caso.verificado_l1_em is None
