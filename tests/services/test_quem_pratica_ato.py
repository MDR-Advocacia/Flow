# -*- coding: utf-8 -*-
"""pub010 — de quem é o ato: schema, regra r6 do shadow e prompt.

Cada teste trava uma decisão que custou medição real (lote de 2.781
publicações contra gabarito humano, 10/08/2026) — não estilo. Se um destes
quebrar, a mudança está desfazendo uma lição paga:

  - `juizo` descartável derrubou a 1ª versão da regra (falso-ignorar 46,6%);
  - a célula `parte_adversa` + exige=true tinha 50% de pureza (moeda ao ar);
  - coerção frouxa de booleano inventaria um `false` que descarta publicação.
"""
from app.services.classifier.response_schema import validate_response
from app.services.publication_shadow import ShadowService


# ── schema ──────────────────────────────────────────────────────────────────

def _clean(**extra):
    payload = {"categoria": "Sentença e Extinção", "subcategoria": "-"}
    payload.update(extra)
    return validate_response(payload)


def test_schema_aceita_valores_do_enum():
    c = _clean(quem_pratica_ato="parte_adversa", exige_providencia_nossa=False)
    assert c.quem_pratica_ato == "parte_adversa"
    assert c.exige_providencia_nossa is False
    assert not c.warnings


def test_schema_normaliza_caixa():
    assert _clean(quem_pratica_ato="Parte_Adversa").quem_pratica_ato == "parte_adversa"


def test_schema_descarta_fora_do_enum_com_warning():
    # "juizo" cru NÃO está no enum — a separação expediente/determina é
    # deliberada; aceitar o balde único reintroduziria a moeda ao ar.
    c = _clean(quem_pratica_ato="juizo")
    assert c.quem_pratica_ato is None
    assert any("quem_pratica_ato" in w for w in c.warnings)


def test_schema_nao_coage_booleano_de_string():
    # "sim"/"true" como string viram None, nunca True/False: um false
    # inventado por coerção descartaria publicação — e prazo não volta.
    c = _clean(exige_providencia_nossa="sim")
    assert c.exige_providencia_nossa is None
    assert any("exige_providencia_nossa" in w for w in c.warnings)


def test_schema_campos_ausentes_ficam_none_sem_warning():
    c = _clean()
    assert c.quem_pratica_ato is None
    assert c.exige_providencia_nossa is None
    assert not c.warnings


# ── regra r6 no shadow ──────────────────────────────────────────────────────

_BASE = {"texto_len": 5000, "tem_template": True}


def _decidir(**sinais):
    # `decidir` não usa self — chamada direta evita montar sessão de banco.
    return ShadowService.decidir(None, dict(_BASE, **sinais))


def test_r6_dispara_so_com_as_duas_condicoes():
    prev, motivo, _conf, regra = _decidir(
        quem_pratica_ato="parte_adversa", exige_providencia_nossa=False)
    assert (prev, regra) == ("IGNORADO", "r6_parte_adversa_sem_providencia")
    assert motivo == "parte_adversa"


def test_r6_nao_dispara_com_providencia_nossa():
    # Célula parte_adversa + exige=true: 18 casos, 50% de pureza — fora.
    prev, _m, _c, regra = _decidir(
        quem_pratica_ato="parte_adversa", exige_providencia_nossa=True)
    assert (prev, regra) == ("AGENDADO", "r5_default")


def test_r6_juizo_nunca_descarta():
    # A lição mais cara do lote: juizo era 44% do volume com 35% de pureza.
    for quem in ("juizo_expediente", "juizo_determina"):
        prev, _m, _c, regra = _decidir(
            quem_pratica_ato=quem, exige_providencia_nossa=False)
        assert (prev, regra) == ("AGENDADO", "r5_default"), quem


def test_r6_null_cai_no_default():
    # Publicação classificada antes da pub010 (campos NULL): r6 não dispara.
    prev, _m, _c, regra = _decidir(
        quem_pratica_ato=None, exige_providencia_nossa=None)
    assert (prev, regra) == ("AGENDADO", "r5_default")


def test_r6_vem_depois_das_regras_existentes():
    # A validação mediu a r6 sobre quem CHEGAVA ao default; se ela passar a
    # roubar população de regra anterior (ex.: pauta), o número medido deixa
    # de valer. Pauta continua vencendo.
    prev, _m, _c, regra = _decidir(
        pauta=True, quem_pratica_ato="parte_adversa",
        exige_providencia_nossa=False)
    assert regra == "r2b_pauta"


# ── prompt ──────────────────────────────────────────────────────────────────

def test_prompt_carrega_o_bloco_antes_do_feedback():
    from app.services.classifier.prompts import build_system_prompt_for_office

    p = build_system_prompt_for_office(
        polo_scope="passivo", taxonomy_version="v2",
        feedback_examples="MARCADOR_FEEDBACK")
    assert "DE QUEM E O ATO" in p
    # Antes dos feedback_examples: eles variam por escritório, e o que vem
    # depois deles sai do prefixo estável do prompt caching.
    assert p.index("DE QUEM E O ATO") < p.index("MARCADOR_FEEDBACK")
