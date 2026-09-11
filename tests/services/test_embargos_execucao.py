# -*- coding: utf-8 -*-
"""Fluxo Embargos à Execução (11/09/2026).

Cobre o que decide o fluxo sem rede: leitura do relatório real do L1 (colunas
por título, corte de casos antigos), agenda em dias úteis, partes demandadas do
portal, confirmação de vínculo pelo texto do DJEN (textos reais da sonda) e o
monitor com DataJud/DJEN/L1 de mentira.
"""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.embargos_execucao import (
    DECISAO_RECUSADO,
    ESTADO_AGUARDANDO_JANELA,
    ESTADO_ENCONTRADO,
    ESTADO_JA_CADASTRADO,
    ESTADO_MONITORANDO,
    ESTADO_SEM_CNJ,
    ESTADO_SEM_EMBARGOS,
    NIVEL_CONFIRMADO_DJEN,
    NIVEL_DESCARTADO,
    NIVEL_FRACO,
    NIVEL_PROVAVEL,
    ORIGEM_PLANILHA,
    ORIGEM_RELATORIO,
    PARTES_NAO_APLICA,
    PARTES_PENDENTE,
    PARTES_SEM_NPJ,
    EmbCandidato,
    EmbExecucao,
    EmbParte,
)
from app.services.embargos_execucao import (
    agenda,
    djen_embargos,
    importacao,
    monitor,
    partes_bb,
    relatorio_l1,
    service,
)
from app.services.embargos_execucao.datajud_embargos import CandidatoDataJud, CapaExecucao
from app.services.embargos_execucao.normaliza import nomes_batem

# Cabeçalho EXATO do relatório "ROBÔ - EMBARGOS À EXECUÇÃO" baixado em 11/09/2026.
CABECALHO_L1 = (
    "ESCRITÓRIO RESPONSÁVEL", "ADOVOGADO(A) RESPONSÁVEL", "EXECUTANTE", "SUBTIPO",
    "CONCLUSÃO PREVISTA", "NÚMERO DO PROCESO", "UF", "DESCRIÇÃO DA TAREFA", "NÚMERO DA PASTA",
    "INÍCIO PREVISTO", "TIPO", "STATUS", "USUÁRIO QUE CADASTROU", "DATA/HORA DE CADASTRO",
    "OBSERVAÇÕES", "Compromissos gerados / Envolvidos / É executante", "PROC",
    "Vínculos com processo / Título", "Id", "Data/hora conclusão efetiva",
)
BB_AUTOR = "MDR Advocacia / Área operacional / Banco do Brasil / Autor"


def _linha_l1(pasta, cnj, npj, task_id, concluida, status="Cumprido", escritorio=BB_AUTOR):
    return (
        escritorio, "Izabele Roberta", "João Vinícius", "Protocolar Inicial - BB Autor",
        (concluida + timedelta(days=1)) if concluida else None, cnj, "", "Ajuizamento", pasta, concluida,
        "Ativos e BB - Recuperação de Crédito", status, "Sistema", concluida, "", "Não",
        pasta, npj, task_id, concluida,
    )


@pytest.fixture
def params(monkeypatch):
    """app_settings em memória (o helper real abre sessão própria no banco)."""
    store = {}
    monkeypatch.setattr(service, "get_setting", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(service, "set_setting", lambda k, v, description=None: store.__setitem__(k, v))
    monkeypatch.setattr(relatorio_l1, "get_setting", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(relatorio_l1, "set_setting", lambda k, v, description=None: store.__setitem__(k, v))
    return store


# ── leitura do relatório ─────────────────────────────────────────────
def test_cabecalho_real_do_l1_casa_pelo_titulo_e_evita_as_armadilhas():
    mapa = importacao.mapear_cabecalho(CABECALHO_L1)
    assert mapa["data_ajuizamento"] == 19   # conclusão EFETIVA, não a "CONCLUSÃO PREVISTA" (4)
    assert mapa["l1_task_id"] == 18
    assert mapa["executante_nome"] == 2     # não "Compromissos gerados / ... / É executante" (15)
    assert mapa["npj"] == 17
    assert mapa["pasta"] == 8
    assert mapa["cnj"] == 5
    assert mapa["responsavel_nome"] == 1    # "ADOVOGADO" com o erro de digitação do modelo
    assert mapa["escritorio"] == 0
    assert mapa["status"] == 11


def test_linhas_da_tabela_ignora_nao_cumprida_e_sem_data():
    t = datetime(2026, 9, 10, 15, 0)
    rows = [
        CABECALHO_L1,
        _linha_l1("Proc - 0000001", "7029693-10.2026.8.22.0001", "2025/0342918-000", 1, t),
        _linha_l1("Proc - 0000002", "7029693-10.2026.8.22.0002", "", 2, t, status="Pendente"),
        _linha_l1("Proc - 0000003", "", "", 3, None),
    ]
    linhas, stats = importacao.linhas_da_tabela(rows)
    assert [l["pasta"] for l in linhas] == ["Proc - 0000001"]
    assert linhas[0]["data_ajuizamento"] == date(2026, 9, 10)
    assert linhas[0]["l1_task_id"] == 1
    assert stats["status_ignorado"] == 1 and stats["sem_data"] == 1


def test_relatorio_so_traz_caso_novo_e_corte_nasce_na_primeira_passagem(db_session, params, monkeypatch):
    import openpyxl
    import io

    hoje = date(2026, 9, 11)
    monkeypatch.setattr(service, "hoje_brt", lambda: hoje)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(CABECALHO_L1)
    ws.append(_linha_l1("Proc - 0029647", "7009003-88.2025.8.22.0002", "2025/0056987-000", 11421, datetime(2025, 5, 8, 17, 39)))
    ws.append(_linha_l1("Proc - 0092941", "7050390-52.2026.8.22.0001", "2026/0100000-000", 480001, datetime(2026, 9, 11, 10, 0)))
    buf = io.BytesIO()
    wb.save(buf)

    r = importacao.importar_bytes(db_session, buf.getvalue(), "relatorio.xlsx", origem=ORIGEM_RELATORIO)
    assert r["corte"] == "2026-09-11"
    assert r["antes_do_corte"] == 1 and r["novas"] == 1
    assert [e.pasta for e in db_session.query(EmbExecucao).all()] == ["Proc - 0092941"]

    # Planilha do legado NÃO passa pelo corte.
    r2 = importacao.importar_bytes(db_session, buf.getvalue(), "legado.xlsx", origem=ORIGEM_PLANILHA)
    assert r2["corte"] is None and r2["novas"] == 1


# ── entrada ──────────────────────────────────────────────────────────
def test_upsert_agrupa_por_pasta_com_a_primeira_conclusao(db_session, params):
    hoje = date(2026, 9, 11)
    base = dict(cnj="7009003-88.2025.8.22.0002", npj="2025/0056987-000", escritorio=BB_AUTOR)
    r = service.upsert_execucoes(db_session, [
        {**base, "pasta": "Proc - 0029647", "l1_task_id": 11432, "data_ajuizamento": date(2026, 9, 9)},
        {**base, "pasta": "Proc - 0029647", "l1_task_id": 11418, "data_ajuizamento": date(2026, 9, 8)},
    ], origem=ORIGEM_RELATORIO, hoje=hoje)
    assert r["novas"] == 1
    exe = db_session.query(EmbExecucao).one()
    assert exe.data_ajuizamento == date(2026, 9, 8)
    assert exe.l1_task_ids == [11418, 11432]
    assert exe.estado == ESTADO_AGUARDANDO_JANELA
    assert exe.partes_status == PARTES_PENDENTE
    assert exe.proxima_consulta == agenda.inicio_monitoramento(date(2026, 9, 8), 15)

    # Reimportar não duplica e só acrescenta o id novo.
    service.upsert_execucoes(db_session, [
        {**base, "pasta": "Proc - 0029647", "l1_task_id": 11500, "data_ajuizamento": date(2026, 9, 10)},
    ], origem=ORIGEM_RELATORIO, hoje=hoje)
    assert db_session.query(EmbExecucao).count() == 1
    assert exe.l1_task_ids == [11418, 11432, 11500]


def test_upsert_sem_cnj_banese_e_adocao_da_pasta(db_session, params):
    hoje = date(2026, 9, 11)
    service.upsert_execucoes(db_session, [
        {"pasta": "Proc - 0052611", "data_ajuizamento": hoje, "escritorio": BB_AUTOR},
        {"pasta": "Proc - 0070000", "cnj": "0801173-28.2026.8.20.5128", "data_ajuizamento": hoje,
         "escritorio": "MDR Advocacia / Área operacional / Banese / Autor", "npj": "2026/0000001-000"},
        {"cnj": "0803370-37.2026.8.20.5101", "data_ajuizamento": hoje},
    ], origem=ORIGEM_PLANILHA, hoje=hoje)
    sem_cnj = db_session.query(EmbExecucao).filter_by(pasta="Proc - 0052611").one()
    assert sem_cnj.estado == ESTADO_SEM_CNJ and sem_cnj.partes_status == PARTES_SEM_NPJ
    banese = db_session.query(EmbExecucao).filter_by(pasta="Proc - 0070000").one()
    assert banese.cliente == "BANESE" and banese.partes_status == PARTES_NAO_APLICA

    service.upsert_execucoes(db_session, [
        {"pasta": "Proc - 0061461", "cnj": "0803370-37.2026.8.20.5101", "data_ajuizamento": hoje},
    ], origem=ORIGEM_RELATORIO, hoje=hoje)
    assert db_session.query(EmbExecucao).filter(EmbExecucao.pasta.like("CNJ %")).count() == 0
    assert db_session.query(EmbExecucao).filter_by(pasta="Proc - 0061461").count() == 1


# ── agenda ───────────────────────────────────────────────────────────
def test_janela_em_dias_uteis_pula_fim_de_semana_e_feriado():
    # 04/09/2026 (sexta) + 15 úteis, com 07/09 (feriado) no caminho = 28/09.
    assert agenda.inicio_monitoramento(date(2026, 9, 4), 15) == date(2026, 9, 28)
    # Janela já vencida (legado) consulta no próximo dia útil a partir de hoje.
    assert agenda.primeira_consulta(date(2025, 1, 2), 15, date(2026, 9, 12)) == date(2026, 9, 14)


# ── partes do BB ─────────────────────────────────────────────────────
def test_demandados_sao_o_polo_oposto_ao_banco():
    linhas = partes_bb.separar_demandadas([
        {"polo": "POLO ATIVO", "nome": "BANCO DO BRASIL S/A", "cpf_cnpj": "00.000.000/0001-91"},
        {"polo": "POLO PASSIVO", "nome": "SIDNEY OLIVEIRA RIBEIRO", "cpf_cnpj": "123.456.789-01"},
        {"polo": "POLO PASSIVO", "nome": "J. H. M. CORDEIRO - ME", "cpf_cnpj": "12.345.678/0001-90"},
        {"polo": "INTERESSADOS", "nome": "ADVOGADO ADVERSO", "cpf_cnpj": ""},
    ])
    assert [l["nome"] for l in linhas if l["demandada"]] == ["SIDNEY OLIVEIRA RIBEIRO", "J. H. M. CORDEIRO - ME"]
    assert linhas[2]["tipo_pessoa"] == "PJ"


# ── DJEN ─────────────────────────────────────────────────────────────
TEXTO_EMBARGOS_TJRO = (
    "PODER JUDICIÁRIO DO ESTADO DE RONDÔNIA Tribunal de Justiça de Rondônia Porto Velho - 7ª Vara Cível "
    "Processo n. 7036270-04.2026.8.22.0001 Embargos à Execução EMBARGANTE: SIDNEY OLIVEIRA RIBEIRO "
    "ADVOGADO DO EMBARGANTE: CLARISSA GARCIA DE ARAUJO BRANDAO, OAB nº MG186046 EMBARGADO: BANCO DO BRASIL "
    "ADVOGADOS DO EMBARGADO: MARCOS DELLI RIBEIRO RODRIGUES"
)
TEXTO_EMBARGOS_TJRN = (
    "Processo nº 0803370-37.2026.8.20.5101 - EMBARGOS À EXECUÇÃO (172) EMBARGANTE: MARCIO HENRIQUE DOS "
    "SANTOS MOREIRA - ME, MARCIO HENRIQUE DOS SANTOS MOREIRA EMBARGADO: BANCO DO BRASIL S/A DECISÃO I - RELATÓRIO"
)


def test_djen_confirma_quando_o_embargante_e_parte_da_execucao():
    r = djen_embargos.avaliar_comunicacoes(
        [{"texto": TEXTO_EMBARGOS_TJRO, "data_disponibilizacao": "2026-07-29"}],
        ["Sidney Oliveira Ribeiro"],
    )
    assert r.status == djen_embargos.DJEN_CONFIRMADO
    assert r.embargantes == ["SIDNEY OLIVEIRA RIBEIRO"]
    assert r.embargado_cliente is True and r.embargados == ["BANCO DO BRASIL"]
    assert "EMBARGANTE" in r.trecho


def test_djen_sufixo_societario_nao_impede_o_casamento():
    r = djen_embargos.avaliar_comunicacoes([{"texto": TEXTO_EMBARGOS_TJRN}], ["MARCIO HENRIQUE DOS SANTOS MOREIRA - ME"])
    assert r.status == djen_embargos.DJEN_CONFIRMADO
    assert len(r.embargantes) == 2


def test_djen_descarta_embargante_de_outra_execucao_e_espera_quando_nao_ha_publicacao():
    r = djen_embargos.avaliar_comunicacoes([{"texto": TEXTO_EMBARGOS_TJRO}], ["TEREZA MOREIRA REZENDE"])
    assert r.status == djen_embargos.DJEN_NAO_BATEU
    assert djen_embargos.avaliar_comunicacoes([], ["X Y"]).status == djen_embargos.DJEN_SEM_COMUNICACAO
    assert djen_embargos.avaliar_comunicacoes([{"texto": TEXTO_EMBARGOS_TJRO}], []).status == djen_embargos.DJEN_SEM_REFERENCIA


def test_djen_sem_rotulo_usa_destinatarios_que_nao_sao_advogado_nem_banco():
    item = {
        "texto": "Intime-se a parte embargada para impugnar.",
        "destinatarios": [{"nome": "BANCO DO BRASIL SA"}, {"nome": "JULIANO NOGUEIRA DA SILVA"},
                          {"nome": "ISABELLE NONATO DE OLIVEIRA MOURA"}],
        "destinatarioadvogados": [{"advogado": {"nome": "ISABELLE NONATO DE OLIVEIRA MOURA"}}],
    }
    r = djen_embargos.avaliar_comunicacoes([item], ["Juliano Nogueira da Silva"])
    assert r.status == djen_embargos.DJEN_CONFIRMADO and r.embargantes == ["JULIANO NOGUEIRA DA SILVA"]


def test_nomes_batem_nao_confunde_primeiro_nome():
    assert nomes_batem("MARIA DA SILVA", "Maria  da Silva")
    assert not nomes_batem("MARIA", "MARIA DA SILVA")
    assert not nomes_batem("JOSE CARLOS SOUZA", "JOSE PEREIRA SOUZA")


# ── monitor ──────────────────────────────────────────────────────────
HOJE = date(2026, 9, 14)


def _card(db, *, estado=ESTADO_MONITORANDO, ajuizada=date(2026, 5, 22), proxima=HOJE, pasta="Proc - 0068696"):
    exe = EmbExecucao(
        pasta=pasta, cnj="7007135-32.2026.8.22.0005", cnj_digitos="70071353220268220005",
        npj="2025/0342918-000", cliente="BB", origem=ORIGEM_RELATORIO, data_ajuizamento=ajuizada,
        estado=estado, dias_uteis_janela=15, proxima_consulta=proxima, consultas_feitas=0,
        partes_status="OK", partes_tentativas=0,
    )
    exe.partes.append(EmbParte(origem="BB", polo="Passivo", nome="TEREZA MOREIRA REZENDE", demandada=True))
    db.add(exe)
    db.commit()
    return exe


def _datajud(candidatos, *, capa=True, peticao=False):
    c = CapaExecucao(alias="api_publica_tjro", orgao_codigo=4440, orgao_nome="JI-PARANÁ - 1ª VARA CÍVEL",
                     classe_codigo=12154, classe_nome="Execução de Título Extrajudicial", grau="G1",
                     data_ajuizamento=datetime(2026, 5, 22), data_ajuizamento_raw="20260522124038")
    return SimpleNamespace(
        consultar_capa=lambda cnj: c if capa else None,
        buscar_candidatos=lambda capa, cnj_execucao, desde_raw: candidatos,
        peticao_na_execucao_no_dia=lambda capa, dia: peticao,
    )


def _cand(num="70116033920268220005", dependencia=False):
    return CandidatoDataJud(cnj_digitos=num, data_ajuizamento=datetime(2026, 8, 7, tzinfo=timezone.utc),
                            classe_codigo=172, classe_nome="Embargos à Execução",
                            orgao_nome="JI-PARANÁ - 1ª VARA CÍVEL", distribuicao_dependencia=dependencia)


def _djen(status_por_cnj):
    chamadas = []

    def confirmar(cnj, refs, client=None, cliente="BB"):
        chamadas.append((cnj, list(refs)))
        st = status_por_cnj.get(cnj, djen_embargos.DJEN_SEM_COMUNICACAO)
        return djen_embargos.ResultadoDjen(
            status=st, embargantes=["TEREZA MOREIRA REZENDE"] if st != djen_embargos.DJEN_SEM_COMUNICACAO else [],
            nomes_casados=["TEREZA MOREIRA REZENDE"] if st == djen_embargos.DJEN_CONFIRMADO else [],
            erro="403 geo-bloqueio" if st == djen_embargos.DJEN_INDISPONIVEL else None,
        )

    return SimpleNamespace(confirmar_candidato=confirmar, executados_da_execucao=lambda cnj, c=None, cliente="BB": [],
                           chamadas=chamadas)


def test_embargante_confirmado_no_djen_para_o_monitoramento_e_avisa_uma_vez(db_session, params):
    params[service.P_EMAILS] = "controladoria@mdradvocacia.com"
    exe = _card(db_session)
    enviados = []
    djen = _djen({"7011603-39.2026.8.22.0005": djen_embargos.DJEN_CONFIRMADO})
    r = monitor.processar(db_session, exe, HOJE, datajud=_datajud([_cand()]), djen=djen,
                          enviar_email=lambda *a: enviados.append(a) or True)
    db_session.commit()
    assert r == "encontrado"
    assert exe.estado == ESTADO_ENCONTRADO and exe.proxima_consulta is None
    assert exe.candidatos[0].nivel == NIVEL_CONFIRMADO_DJEN
    assert djen.chamadas[0][1] == ["TEREZA MOREIRA REZENDE"]
    assert len(enviados) == 1 and enviados[0][3] == ["controladoria@mdradvocacia.com"]
    assert exe.aviso_enviado_em is not None


def test_candidato_fraco_segue_monitorando_a_cada_cinco_dias_uteis(db_session, params):
    exe = _card(db_session)
    r = monitor.processar(db_session, exe, HOJE, datajud=_datajud([_cand()]), djen=_djen({}))
    assert r == "monitorando"
    assert exe.estado == ESTADO_MONITORANDO
    assert exe.candidatos[0].nivel == NIVEL_FRACO
    assert exe.proxima_consulta == date(2026, 9, 21)
    assert exe.consultas_feitas == 1


def test_djen_de_outra_parte_descarta_e_recusado_nao_volta(db_session, params):
    exe = _card(db_session)
    outro, nosso = "70000000020268220005", "70116033920268220005"
    datajud = _datajud([_cand(outro), _cand(nosso, dependencia=True)], peticao=True)
    monitor.processar(db_session, exe, HOJE, datajud=datajud,
                      djen=_djen({"7000000-00.2026.8.22.0005": djen_embargos.DJEN_NAO_BATEU}))
    db_session.commit()
    niveis = {c.cnj_digitos: c.nivel for c in exe.candidatos}
    assert niveis == {outro: NIVEL_DESCARTADO, nosso: NIVEL_PROVAVEL}
    assert exe.estado == ESTADO_ENCONTRADO

    forte = next(c for c in exe.candidatos if c.cnj_digitos == nosso)
    service.decidir_candidato(db_session, exe.id, forte.id, DECISAO_RECUSADO, hoje=HOJE)
    assert exe.estado == ESTADO_MONITORANDO and exe.proxima_consulta == date(2026, 9, 21)

    monitor.processar(db_session, exe, date(2026, 9, 21), datajud=datajud, djen=_djen({}))
    assert db_session.query(EmbCandidato).filter_by(execucao_id=exe.id).count() == 2
    assert exe.estado == ESTADO_MONITORANDO


def test_djen_fora_do_ar_so_alerta_com_dependencia_e_peticao_no_dia(db_session, params):
    exe = _card(db_session)
    djen = _djen({"7011603-39.2026.8.22.0005": djen_embargos.DJEN_INDISPONIVEL})
    monitor.processar(db_session, exe, HOJE, datajud=_datajud([_cand()], peticao=True), djen=djen)
    assert exe.estado == ESTADO_MONITORANDO and "DJEN indisponível" in exe.ultimo_erro

    exe2 = _card(db_session, pasta="Proc - 0043772")
    monitor.processar(db_session, exe2, HOJE, datajud=_datajud([_cand(dependencia=True)], peticao=True), djen=djen)
    assert exe2.estado == ESTADO_ENCONTRADO and exe2.candidatos[0].nivel == NIVEL_PROVAVEL


def test_sem_conferencia_do_djen_os_sinais_do_datajud_nao_bastam(db_session, params, monkeypatch):
    # DJEN no ar, mas o teto de chamadas da consulta já foi: o candidato com
    # dependência + petição no dia espera a conferência (falso provável do teste real).
    monkeypatch.setattr(monitor, "MAX_DJEN_POR_CONSULTA", 0)
    exe = _card(db_session)
    monitor.processar(db_session, exe, HOJE, datajud=_datajud([_cand(dependencia=True)], peticao=True), djen=_djen({}))
    assert exe.candidatos[0].nivel == NIVEL_FRACO and exe.estado == ESTADO_MONITORANDO


def test_djen_confere_primeiro_dependencia_e_mais_recente(db_session, params, monkeypatch):
    monkeypatch.setattr(monitor, "MAX_DJEN_POR_CONSULTA", 1)
    exe = _card(db_session)
    antigo = CandidatoDataJud(cnj_digitos="70000010020268220005", data_ajuizamento=datetime(2026, 6, 1, tzinfo=timezone.utc),
                              classe_codigo=172, classe_nome="Embargos à Execução", orgao_nome="x", distribuicao_dependencia=False)
    djen = _djen({})
    monitor.processar(db_session, exe, HOJE, datajud=_datajud([antigo, _cand(dependencia=True)]), djen=djen)
    assert [c for c, _ in djen.chamadas] == ["7011603-39.2026.8.22.0005"]


def test_teto_fecha_sem_embargos_e_capa_ausente_nao_quebra(db_session, params):
    velho = _card(db_session, ajuizada=HOJE - timedelta(days=400))
    monitor.processar(db_session, velho, HOJE, datajud=_datajud([]), djen=_djen({}))
    assert velho.estado == ESTADO_SEM_EMBARGOS and velho.proxima_consulta is None

    sem_capa = _card(db_session, pasta="Proc - 0000009")
    assert monitor.processar(db_session, sem_capa, HOJE, datajud=_datajud([], capa=False), djen=_djen({})) == "sem_capa"
    assert sem_capa.estado == ESTADO_MONITORANDO and sem_capa.proxima_consulta == date(2026, 9, 21)


def test_janela_cumprida_entra_em_monitoramento_e_incidente_no_l1_encerra(db_session, params):
    exe = _card(db_session, estado=ESTADO_AGUARDANDO_JANELA)

    class L1:
        base_url = "https://l1"

        def _request_with_retry(self, method, url, params=None):
            assert params["$filter"] == "startswith(folder,'Proc - 0068696/')"
            return SimpleNamespace(json=lambda: {"value": [
                {"id": 98774, "folder": "Proc - 0068696/001", "title": "EMBARGOS À EXECUÇÃO",
                 "identifierNumber": "70116033920268220005"},
            ]})

    r = monitor.processar(db_session, exe, HOJE, datajud=_datajud([_cand()]), djen=_djen({}), l1_client=L1())
    assert r == "ja_cadastrado"
    assert exe.estado == ESTADO_JA_CADASTRADO
    assert exe.incidente_folder == "Proc - 0068696/001"
    assert exe.incidente_cnj == "7011603-39.2026.8.22.0005"


# ── lista de relatórios do L1 (HTML do HAR) ──────────────────────────
HTML_LISTA = """
<tr class="webgrid-row-style"><td><input class="grid_check" data-val="16785" id="grid_check_16785"/></td>
<td><span id="report_title_16785"> ROB&#212; - EMBARGOS &#192; EXECU&#199;&#195;O </span></td>
<td> 11/09/2026 </td><td><span id="gerando_16785" data-val-status="7"></span></td></tr>
<tr class="webgrid-alternating-row"><td><span id="report_title_16790"> ROBÔ - EMBARGOS À EXECUÇÃO </span></td>
<td> 11/09/2026 </td><td><a href="/shared/ReportShared/GetFile/16790">Download</a></td></tr>
<tr><td><span id="report_title_16791"> AGENDA - CADASTRO BB </span></td><td><a href="/shared/ReportShared/GetFile/16791">x</a></td></tr>
"""


def test_lista_de_relatorios_identifica_gerando_e_pronto_pelo_titulo():
    linhas = relatorio_l1.relatorios_na_lista(HTML_LISTA, "ROBÔ - EMBARGOS À EXECUÇÃO")
    assert [(l["id"], l["pronto"], l["gerando"]) for l in linhas] == [(16785, False, True), (16790, True, False)]


def test_listar_kpis_e_paginacao(db_session, params):
    for i in range(3):
        _card(db_session, pasta=f"Proc - 000010{i}")
    dados = service.listar(db_session, limit=2, offset=0)
    assert dados["total"] == 3 and len(dados["items"]) == 2
    assert dados["kpis"]["por_estado"][ESTADO_MONITORANDO] == 3
    assert dados["items"][0]["partes_demandadas"] == 1


# ── embargado que não é o cliente (decisão do operador, 11/09/2026) ─────
TEXTO_EMBARGADO_SICREDI = (
    "Processo : 7008156-43.2026.8.22.0005 Classe : EMBARGOS À EXECUÇÃO (172) EMBARGANTE: COMERCIAL CASTRO VERAS "
    "COMERCIO VAREJISTA PRODUTOS DE LIMPEZA LTDA e outros (2) Advogado do(a) EMBARGANTE: YURI ELIAS ALBERTINO - BA87402 "
    "EMBARGADO: COOPERATIVA DE CRÉDITO DE LIVRE ADMISSÃO DE ASSOCIADOS DO VALE DO JURUENA SICREDI UNIVALES MT "
    "INTIMAÇÃO AUTOR - PROMOVER ANDAMENTO Fica a parte AUTORA intimada a promover o regular andamento"
)


def test_embargado_que_nao_e_o_cliente_descarta_mesmo_com_o_nome_batendo():
    r = djen_embargos.avaliar_comunicacoes(
        [{"texto": TEXTO_EMBARGADO_SICREDI}],
        ["COMERCIAL CASTRO VERAS COMERCIO VAREJISTA PRODUTOS DE LIMPEZA LTDA"],
    )
    assert r.status == djen_embargos.DJEN_EMBARGADO_OUTRO
    assert r.embargados == ["COOPERATIVA DE CRÉDITO DE LIVRE ADMISSÃO DE ASSOCIADOS DO VALE DO JURUENA SICREDI UNIVALES MT"]
    assert r.embargantes[0].startswith("COMERCIAL CASTRO VERAS")
    # Nome do embargado não arrasta o endereço/documento que vem colado no texto.
    com_endereco = djen_embargos.avaliar_comunicacoes(
        [{"texto": "EMBARGANTE: FULANO DE TAL EMBARGADO: BANCO BRADESCO S.A Endereço: AL. RIO NEGRO, 585"}], ["FULANO DE TAL"],
    )
    assert com_endereco.embargados == ["BANCO BRADESCO S.A"]


def test_embargado_esperado_segue_o_cliente_da_execucao():
    banese = TEXTO_EMBARGOS_TJRO.replace("EMBARGADO: BANCO DO BRASIL", "EMBARGADO: BANCO DO ESTADO DE SERGIPE S/A - BANESE")
    r = djen_embargos.avaliar_comunicacoes([{"texto": banese}], ["SIDNEY OLIVEIRA RIBEIRO"], cliente="BANESE")
    assert r.status == djen_embargos.DJEN_CONFIRMADO and r.embargado_cliente
    assert djen_embargos.avaliar_comunicacoes(
        [{"texto": TEXTO_EMBARGOS_TJRO}], ["SIDNEY OLIVEIRA RIBEIRO"], cliente="BANESE",
    ).status == djen_embargos.DJEN_EMBARGADO_OUTRO


def test_monitor_descarta_embargado_de_outro_credor_e_nao_conta_no_board(db_session, params):
    exe = _card(db_session)
    djen = _djen({"7011603-39.2026.8.22.0005": djen_embargos.DJEN_EMBARGADO_OUTRO})
    monitor.processar(db_session, exe, HOJE, datajud=_datajud([_cand(dependencia=True)], peticao=True), djen=djen)
    db_session.commit()
    assert exe.candidatos[0].nivel == NIVEL_DESCARTADO and exe.estado == ESTADO_MONITORANDO
    item = service.listar(db_session)["items"][0]
    assert item["candidatos"] == 0 and item["candidatos_fortes_pendentes"] == 0


# ── candidato forte já cadastrado no L1 (pedido do operador, 11/09/2026) ─
class L1ComCnj:
    base_url = "https://l1"

    def __init__(self, achado):
        self.achado = achado
        self.buscas = []

    def _request_with_retry(self, method, url, params=None):
        return SimpleNamespace(json=lambda: {"value": []})  # nada na pasta da execução

    def search_lawsuit_by_cnj(self, cnj):
        self.buscas.append(cnj)
        return self.achado

    def get_lawsuit_by_id(self, lawsuit_id, params=None):
        return {"id": lawsuit_id, "folder": "Proc - 0099999/002", "responsibleOfficeId": 22}


def test_embargos_forte_ja_cadastrado_no_l1_encerra_sem_avisar(db_session, params):
    params[service.P_EMAILS] = "controladoria@mdradvocacia.com"
    exe = _card(db_session)
    enviados = []
    l1 = L1ComCnj({"id": 98298, "identifierNumber": "70116033920268220005", "responsibleOfficeId": 22})
    r = monitor.processar(
        db_session, exe, HOJE, datajud=_datajud([_cand()]),
        djen=_djen({"7011603-39.2026.8.22.0005": djen_embargos.DJEN_CONFIRMADO}),
        l1_client=l1, enviar_email=lambda *a: enviados.append(a) or True,
    )
    db_session.commit()
    assert r == "ja_cadastrado" and exe.estado == ESTADO_JA_CADASTRADO
    assert l1.buscas == ["7011603-39.2026.8.22.0005"] and enviados == []
    assert exe.incidente_id == 98298 and exe.incidente_folder == "Proc - 0099999/002"
    assert exe.candidatos[0].l1_folder == "Proc - 0099999/002"


def test_embargos_forte_que_nao_esta_no_l1_segue_para_verificacao(db_session, params):
    exe = _card(db_session)
    l1 = L1ComCnj(None)
    r = monitor.processar(
        db_session, exe, HOJE, datajud=_datajud([_cand()]),
        djen=_djen({"7011603-39.2026.8.22.0005": djen_embargos.DJEN_CONFIRMADO}), l1_client=l1,
    )
    assert r == "encontrado" and exe.estado == ESTADO_ENCONTRADO and l1.buscas == ["7011603-39.2026.8.22.0005"]


def test_candidato_fraco_nao_consulta_o_l1(db_session, params):
    exe = _card(db_session)
    l1 = L1ComCnj({"id": 1})
    monitor.processar(db_session, exe, HOJE, datajud=_datajud([_cand()]), djen=_djen({}), l1_client=l1)
    assert l1.buscas == [] and exe.estado == ESTADO_MONITORANDO
