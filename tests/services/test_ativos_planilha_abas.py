# -*- coding: utf-8 -*-
"""Importação da Ativos: a aba certa entra, qualquer que seja a grafia do nome.

Caso de 11/09/2026: a planilha de 09.09 veio com as abas "PARA CADASTRAR",
"PROCESSOS CADASTRADOS" e "HABILITAÇÃO ANTERIOR". O teste por texto exato
("PARA CADASTRO") ignorou as 60 linhas a cadastrar, não usou os já cadastrados
para dedupe e cadastrou no L1 o único processo da HABILITAÇÃO ANTERIOR.
"""
import io

import pytest

openpyxl = pytest.importorskip("openpyxl")

from app.services.distribuidos_bb.ativos_service import _tipo_da_aba, parse_planilha_ativos  # noqa: E402

HDR = ["DATA", "TIPO.", "REMETENTE.", "Nº CONTROLE", "CLIENTE", "UF", "PROC.N", "MOTIVO", "ESCRITORIO"]
A = "0000426-33.2025.8.25.0044"
B = "0000093-79.2003.8.17.0560"
C = "0000072-24.2003.8.10.0035"
D = "0000056-53.2014.8.05.0058"
E = "0001099-63.2014.8.05.0110"


def _dig(cnj):
    return "".join(ch for ch in cnj if ch.isdigit())


def _xlsx(abas: dict) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for titulo, cnjs in abas.items():
        ws = wb.create_sheet(titulo)
        if cnjs is None:  # aba vazia
            continue
        ws.append(HDR)
        for cnj in cnjs:
            ws.append(["2026-09-02", "PJE", None, None, None, "BA", cnj, None, "MDR"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _cnjs(linhas):
    return [linha["cnj"] for linha in linhas]


def test_planilha_de_09_09_so_a_para_cadastrar_entra():
    conteudo = _xlsx({
        "PARA CADASTRAR": [A, B, B, E],
        "PROCESSOS CADASTRADOS": [E, C],
        "HABILITAÇÃO ANTERIOR": [D],
    })
    linhas, ja = parse_planilha_ativos(conteudo, "09.09 MARCOS DELLI.xlsx")

    assert _cnjs(linhas) == [A, B, E]           # E sai no dedupe da ingestão (está em `ja`)
    assert ja == {_dig(E), _dig(C)}
    assert D not in _cnjs(linhas)


def test_planilha_de_10_09_aguardando_cadastro_nao_entra():
    # decisão do operador em 11/09/2026: AGUARDANDO CADASTRO não é cadastro
    conteudo = _xlsx({
        "PARA CADASTRO": [A, B],
        "PROCESSOS CADASTRADOS": [C],
        "AGUARDANDO CADASTRO": [D],
    })
    linhas, ja = parse_planilha_ativos(conteudo, "10.09 MARCOS DELLI.xlsx")

    assert _cnjs(linhas) == [A, B] and ja == {_dig(C)}


@pytest.mark.parametrize("titulo, tipo", [
    ("PARA CADASTRO", "para"),
    ("PARA CADASTRAR", "para"),
    ("  Para cadastrar ", "para"),
    ("PARA_CADASTRO", "para"),
    ("A CADASTRAR", "para"),
    ("PARA CADASTRO (2)", "para"),
    ("AGUARDANDO CADASTRO", "controle"),
    ("Pendentes de cadastro", "controle"),
    ("JÁ CADASTRADO", "ja"),
    ("JA CADASTRADOS", "ja"),
    ("PROCESSOS CADASTRADOS", "ja"),
    ("cadastrados", "ja"),
    ("SEM CADASTRO", "controle"),
    ("NÃO CADASTRADOS", "controle"),
    ("HABILITAÇÃO ANTERIOR", "controle"),
    ("CADASTRO ESPAIDER", "controle"),
    ("Planilha1", "sem_rotulo"),
])
def test_tipo_da_aba_tolera_a_grafia(titulo, tipo):
    assert _tipo_da_aba(titulo) == tipo


def test_nomes_de_julho_continuam_funcionando():
    linhas, ja = parse_planilha_ativos(_xlsx({"PARA CADASTRO": [A, B], "JÁ CADASTRADO": [C]}), "x.xlsx")
    assert _cnjs(linhas) == [A, B] and ja == {_dig(C)}


def test_aba_sem_cadastro_nao_vaza_pra_fila():
    # lote de 21/07: a "SEM CADASTRO" (controle da Ativos) entrou na fila
    linhas, _ = parse_planilha_ativos(_xlsx({"PARA CADASTRO": [A], "SEM CADASTRO": [B, C]}), "x.xlsx")
    assert _cnjs(linhas) == [A]


def test_com_aba_a_cadastrar_a_aba_de_nome_neutro_nao_entra():
    linhas, _ = parse_planilha_ativos(_xlsx({"Para Cadastrar": [A], "Planilha2": [B]}), "x.xlsx")
    assert _cnjs(linhas) == [A]


def test_aba_unica_de_nome_neutro_vira_lista():
    linhas, ja = parse_planilha_ativos(_xlsx({"Planilha1": [A, B]}), "x.xlsx")
    assert _cnjs(linhas) == [A, B] and ja == set()


def test_aba_vazia_a_mais_nao_atrapalha():
    linhas, _ = parse_planilha_ativos(_xlsx({"Planilha1": [A], "Planilha2": None}), "x.xlsx")
    assert _cnjs(linhas) == [A]


def test_sem_aba_a_cadastrar_e_duas_abas_com_dados_recusa_em_vez_de_adivinhar():
    with pytest.raises(ValueError) as erro:
        parse_planilha_ativos(_xlsx({"Planilha1": [A], "Planilha2": [B]}), "x.xlsx")
    assert "Planilha1" in str(erro.value) and "Planilha2" in str(erro.value)


def test_so_habilitacao_e_ja_cadastrados_nao_cadastra_nada():
    linhas, ja = parse_planilha_ativos(
        _xlsx({"HABILITAÇÃO ANTERIOR": [D], "PROCESSOS CADASTRADOS": [C]}), "x.xlsx",
    )
    assert linhas == [] and ja == {_dig(C)}


def test_csv_continua_lista_seca():
    linhas, ja = parse_planilha_ativos(f"{A}\n{B}\n".encode(), "lista.csv")
    assert _cnjs(linhas) == [A, B] and ja == set()
