"""Exceção de roteamento por ETIQUETA do processo (sqd005).

O PEDIDO
--------
No Autor, tarefa de publicação é distribuída por rodízio entre um grupo de
assistentes e um grupo de advogados (squads de suporte nos templates). A
Equipe Mista — etiqueta NERC no L1 — é exceção: processo NERC fica com o
advogado da equipe e com o assistente dele.

O QUE ESTES TESTES PROTEGEM
---------------------------
1. A squad é escolhida pela ETIQUETA, não pelo desempate do resolvedor comum.
   Em produção uma das advogadas NERC também é membro de outra squad (uma
   CELULA de outro escritório), e o desempate por escritório/menor id mandaria
   a tarefa de assistente para a pessoa errada. O cenário abaixo reproduz
   essa forma.
2. O papel NA EQUIPE vem do template (`excecao_papel`), não do target_role:
   no BB Autor o grupo de advogados roda como "assistente" numa squad de
   suporte, e deduzir pelo target_role mandaria tarefa de advogado para o
   assistente da equipe.
3. Nada muda para processo sem a etiqueta, nem para template sem exceção.
4. Responsável fixo e squad de suporte do template NÃO seguram processo
   etiquetado ("por enquanto, tudo pro NERC").
5. A escolha manual do operador vence a exceção.
6. Etiqueta presente sem squad marcada falha ALTO, com mensagem — nunca cai
   em silêncio no rodízio geral.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models as _models  # noqa: F401 - registra as tabelas
from app.db.session import Base
from app.models.legal_one import LegalOneUser
from app.models.publication_search import PublicationL1EtiquetaCache
from app.models.rules import Squad, SquadMember
from app.models.task_template import TaskTemplate
from app.services import excecao_etiqueta as modulo
from app.services.excecao_etiqueta import aplicar_excecao_etiqueta, normalizar_etiqueta

ESCRITORIO_AUTOR, ESCRITORIO_REU, OUTRO_ESCRITORIO = 22, 23, 26

# external_id fictícios
ADV_A, ASS_A, ASS_A2 = 9001, 9002, 9003
ADV_B, ASS_B = 9011, 9012
ASS_CELULA = 9021
ASS_GERAL_1, ASS_GERAL_2 = 9031, 9032
ADV_GERAL_1, ADV_GERAL_2 = 9041, 9042
ADV_FORA = 9051

PASTA_NERC_A = 900
PASTA_NERC_B = 901          # responsável é membro de DUAS squads
PASTA_SEM_ETIQUETA = 902
PASTA_OUTRA_ETIQUETA = 903
PASTA_NERC_FORA = 904       # etiquetada, mas o responsável não passou pra equipe
PASTA_NERC_MINUSCULA = 905
PASTA_FORA_DO_CACHE = 906

RESPONSAVEIS = {
    PASTA_NERC_A: ADV_A,
    PASTA_NERC_B: ADV_B,
    PASTA_SEM_ETIQUETA: ADV_A,
    PASTA_OUTRA_ETIQUETA: ADV_A,
    PASTA_NERC_FORA: ADV_FORA,
    PASTA_NERC_MINUSCULA: ADV_A,
    PASTA_FORA_DO_CACHE: ADV_A,
}


def _buscar(db, lawsuit_id):
    return RESPONSAVEIS.get(lawsuit_id)


def _nerc(nome="NERC"):
    return [{"id": 83, "name": nome, "class_name": "tag", "color_id": 4}]


@pytest.fixture
def c():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()

    def usuario(ext, nome):
        u = LegalOneUser(external_id=ext, name=nome, email=f"{ext}@exemplo.test", is_active=True)
        db.add(u)
        db.flush()
        return u

    def squad(nome, *, office, kind="principal", etiqueta=None, membros=()):
        s = Squad(name=nome, office_external_id=office, kind=kind, etiqueta=etiqueta, is_active=True)
        db.add(s)
        db.flush()
        for membro, lider, assistente in membros:
            db.add(SquadMember(
                squad_id=s.id, legal_one_user_id=membro.id,
                is_leader=lider, is_assistant=assistente,
            ))
        db.flush()
        return s

    def template(nome, **campos):
        t = TaskTemplate(
            name=nome, category="Categoria", office_external_id=ESCRITORIO_AUTOR,
            task_subtype_external_id=100, priority="Normal", due_business_days=3,
            **campos,
        )
        db.add(t)
        db.flush()
        return t

    u = {ext: usuario(ext, nome) for ext, nome in [
        (ADV_A, "Advogada NERC A"), (ASS_A, "Assistente NERC A"),
        (ASS_A2, "Assistente NERC A2"),
        (ADV_B, "Advogada NERC B"), (ASS_B, "Assistente NERC B"),
        (ASS_CELULA, "Assistente da Celula"),
        (ASS_GERAL_1, "Assistente Geral 1"), (ASS_GERAL_2, "Assistente Geral 2"),
        (ADV_GERAL_1, "Advogado Geral 1"), (ADV_GERAL_2, "Advogado Geral 2"),
        (ADV_FORA, "Advogado Fora da Equipe"),
    ]}
    # Criada ANTES das squads NERC: menor id, é quem o desempate comum escolhe.
    celula = squad("CELULA 2", office=OUTRO_ESCRITORIO, membros=[
        (u[ADV_B], True, True), (u[ASS_CELULA], False, True)])
    nerc_a = squad("[NERC] A", office=ESCRITORIO_REU, etiqueta="NERC", membros=[
        (u[ADV_A], True, False), (u[ASS_A], False, True)])
    nerc_b = squad("[NERC] B", office=ESCRITORIO_REU, etiqueta="NERC", membros=[
        (u[ADV_B], True, False), (u[ASS_B], False, True)])
    assistentes_geral = squad(
        "Assistentes Processual", office=ESCRITORIO_AUTOR, kind="support",
        membros=[(u[ASS_GERAL_1], False, True), (u[ASS_GERAL_2], False, True)])
    # Como no BB Autor: o grupo de ADVOGADOS é marcado como assistente, porque
    # só o papel de assistente tem rodízio no resolvedor.
    advogados_geral = squad(
        "Lideres de Squad", office=ESCRITORIO_AUTOR, kind="support",
        membros=[(u[ADV_GERAL_1], False, True), (u[ADV_GERAL_2], False, True)])

    tpl_assistente = template(
        "assistente com excecao", target_role="assistente",
        target_squad_id=assistentes_geral.id, excecao_etiqueta="NERC")
    tpl_grupo_advogados = template(
        "grupo de advogados com excecao", target_role="assistente",
        target_squad_id=advogados_geral.id,
        excecao_etiqueta="NERC", excecao_papel="principal")
    tpl_fixo = template(
        "responsavel fixo com excecao", target_role="principal",
        responsible_user_external_id=ADV_GERAL_1, excecao_etiqueta="NERC")
    tpl_sem_excecao = template(
        "assistente sem excecao", target_role="assistente",
        target_squad_id=assistentes_geral.id)

    for lawsuit_id, etiquetas in [
        (PASTA_NERC_A, _nerc()),
        (PASTA_NERC_B, _nerc()),
        (PASTA_SEM_ETIQUETA, []),
        (PASTA_OUTRA_ETIQUETA, [{"id": 5, "name": "Outra"}]),
        (PASTA_NERC_FORA, _nerc()),
        (PASTA_NERC_MINUSCULA, _nerc(" nerc ")),
    ]:
        db.add(PublicationL1EtiquetaCache(lawsuit_id=lawsuit_id, etiquetas=etiquetas))
    db.commit()

    yield SimpleNamespace(
        db=db, u=u, celula=celula, nerc_a=nerc_a, nerc_b=nerc_b,
        assistentes_geral=assistentes_geral, advogados_geral=advogados_geral,
        tpl_assistente=tpl_assistente, tpl_grupo_advogados=tpl_grupo_advogados,
        tpl_fixo=tpl_fixo, tpl_sem_excecao=tpl_sem_excecao,
    )
    db.close()


def _aplicar(c, template, lawsuit_id, *, commit=False, buscar=_buscar):
    """Como os chamadores reais: o target_role passado é o do próprio template."""
    return aplicar_excecao_etiqueta(
        c.db, template_id=template.id, lawsuit_id=lawsuit_id,
        target_role=template.target_role, commit=commit, buscar_responsavel=buscar,
    )


# ─── a regra ──────────────────────────────────────────────────────────

def test_normalizacao_ignora_caixa_acento_e_espacos():
    assert normalizar_etiqueta("  nerc ") == "NERC"
    assert normalizar_etiqueta("Exceção   Mista") == "EXCECAO MISTA"
    assert normalizar_etiqueta(None) == ""


def test_assistente_vai_para_a_squad_da_etiqueta_e_nao_para_o_desempate_comum(c):
    """O caso que obrigou a squad a declarar a etiqueta. A advogada B é membro
    da CELULA e da squad NERC B; numa tarefa do Autor o resolvedor comum não
    casa escritório nenhum e fica com a de menor id — a CELULA."""
    from app.services.squad_assistant_resolver import resolve_assistant

    comum = resolve_assistant(
        c.db, responsible_user_external_id=ADV_B, office_external_id=ESCRITORIO_AUTOR,
    )
    assert comum.squad_id == c.celula.id  # o erro que a marcação evita

    resultado, motivo = _aplicar(c, c.tpl_assistente, PASTA_NERC_B)

    assert resultado.user_external_id == ASS_B
    assert resultado.squad_id == c.nerc_b.id
    assert "NERC" in motivo and "[NERC] B" in motivo


def test_papel_na_equipe_vem_do_template_e_nao_do_target_role(c):
    """Grupo de advogados roda como 'assistente' na squad de suporte. Se o
    papel na equipe viesse do target_role, a tarefa de ADVOGADO iria para a
    assistente da equipe NERC."""
    resultado, motivo = _aplicar(c, c.tpl_grupo_advogados, PASTA_NERC_A)

    assert resultado.user_external_id == ADV_A
    assert resultado.user_external_id != ASS_A
    assert "advogado" in motivo


def test_papel_vazio_segue_o_target_role_do_template(c):
    assert c.tpl_assistente.excecao_papel is None
    resultado, _ = _aplicar(c, c.tpl_assistente, PASTA_NERC_A)
    assert resultado.user_external_id == ASS_A

    assert c.tpl_fixo.excecao_papel is None
    resultado, _ = _aplicar(c, c.tpl_fixo, PASTA_NERC_A)
    assert resultado.user_external_id == ADV_A


def test_advogado_vai_para_o_responsavel_da_pasta_e_diz_a_squad(c):
    resultado, motivo = _aplicar(c, c.tpl_grupo_advogados, PASTA_NERC_B)

    assert resultado.user_external_id == ADV_B
    assert resultado.squad_id == c.nerc_b.id
    assert "Advogada NERC B" in motivo and "[NERC] B" in motivo


def test_responsavel_fixo_do_template_nao_segura_processo_etiquetado(c):
    resultado, _ = _aplicar(c, c.tpl_fixo, PASTA_NERC_A)

    assert resultado.user_external_id == ADV_A


@pytest.mark.parametrize(
    "lawsuit_id", [PASTA_SEM_ETIQUETA, PASTA_OUTRA_ETIQUETA, PASTA_FORA_DO_CACHE],
)
def test_processo_sem_a_etiqueta_segue_a_regra_do_template(c, lawsuit_id):
    assert _aplicar(c, c.tpl_assistente, lawsuit_id) is None
    assert _aplicar(c, c.tpl_grupo_advogados, lawsuit_id) is None
    assert _aplicar(c, c.tpl_fixo, lawsuit_id) is None


def test_template_sem_excecao_ignora_a_etiqueta(c):
    assert _aplicar(c, c.tpl_sem_excecao, PASTA_NERC_A) is None


def test_sem_processo_ou_sem_template_nao_ha_excecao(c):
    assert aplicar_excecao_etiqueta(
        c.db, template_id=None, lawsuit_id=PASTA_NERC_A, target_role="assistente",
    ) is None
    assert aplicar_excecao_etiqueta(
        c.db, template_id=c.tpl_assistente.id, lawsuit_id=None, target_role="assistente",
    ) is None


def test_etiqueta_comparada_pelo_nome_sem_caixa_nem_espacos(c):
    """O id da NERC já mudou no L1 (7 → 83); o NOME é o que se mantém."""
    c.tpl_assistente.excecao_etiqueta = "Nerc"
    c.db.commit()

    resultado, motivo = _aplicar(c, c.tpl_assistente, PASTA_NERC_MINUSCULA)

    assert resultado.user_external_id == ASS_A
    assert "nerc" in motivo


@pytest.mark.parametrize("nome_template", ["tpl_assistente", "tpl_grupo_advogados", "tpl_fixo"])
def test_responsavel_fora_de_squad_marcada_falha_alto_nos_dois_papeis(c, nome_template):
    with pytest.raises(ValueError) as erro:
        _aplicar(c, getattr(c, nome_template), PASTA_NERC_FORA)

    mensagem = str(erro.value)
    assert "Advogado Fora da Equipe" in mensagem
    assert "NERC" in mensagem
    assert "manualmente" in mensagem


def test_responsavel_ilegivel_falha_alto_em_vez_de_adivinhar(c):
    with pytest.raises(ValueError, match="responsável da pasta"):
        _aplicar(c, c.tpl_fixo, PASTA_NERC_A, buscar=lambda db, lid: None)


def test_squad_marcada_inativa_nao_conta(c):
    c.nerc_a.is_active = False
    c.db.commit()

    with pytest.raises(ValueError):
        _aplicar(c, c.tpl_assistente, PASTA_NERC_A)


def test_rodizio_da_squad_da_etiqueta_so_avanca_no_claim(c):
    c.db.add(SquadMember(
        squad_id=c.nerc_a.id, legal_one_user_id=c.u[ASS_A2].id,
        is_leader=False, is_assistant=True,
    ))
    c.db.commit()

    def quem(commit):
        resultado, _ = _aplicar(c, c.tpl_assistente, PASTA_NERC_A, commit=commit)
        return resultado.user_external_id

    assert [quem(True), quem(True), quem(True)] == [ASS_A, ASS_A2, ASS_A]
    proximo = quem(False)
    assert quem(False) == proximo, "preview não pode avançar o rodízio"
    assert quem(True) == proximo, "o claim entrega quem o preview mostrou"


# ─── o agendamento (Triagem e tela clássica chegam aqui) ─────────────────

def _servico(c, monkeypatch):
    from app.services.publication_search_service import PublicationSearchService

    monkeypatch.setattr(modulo, "responsavel_da_pasta", _buscar)
    svc = PublicationSearchService.__new__(PublicationSearchService)
    svc.db = c.db
    return svc


def _proposta(template, subtipo, responsavel):
    return {
        "template_id": template.id,
        "target_role": template.target_role,
        "target_squad_id": template.target_squad_id,
        "payload": {"subTypeId": subtipo, "participants": [{"contact": {"id": responsavel}}]},
    }


def _payload(subtipo, responsavel):
    return {
        "subTypeId": subtipo,
        "responsibleOfficeId": ESCRITORIO_AUTOR,
        "participants": [{"contact": {"id": responsavel}, "isResponsible": True}],
    }


def _destino(payload):
    return payload["participants"][0]["contact"]["id"]


def test_agendamento_aplica_a_excecao_no_servidor(c, monkeypatch):
    """A Triagem não resolve nada no navegador: é este método que decide."""
    svc = _servico(c, monkeypatch)
    propostas = [
        _proposta(c.tpl_fixo, 100, ADV_GERAL_1),
        _proposta(c.tpl_assistente, 200, ADV_A),
        _proposta(c.tpl_grupo_advogados, 300, ADV_A),
    ]
    payloads = [_payload(100, ADV_GERAL_1), _payload(200, ADV_A), _payload(300, ADV_A)]
    notas = [None, None, None]

    svc._apply_squad_routing_server_side(
        payloads=payloads, proposals=propostas, lawsuit_id=PASTA_NERC_A,
        operator_locked=[False, False, False], routing_notes=notas,
    )

    assert _destino(payloads[0]) == ADV_A
    assert _destino(payloads[1]) == ASS_A
    assert _destino(payloads[2]) == ADV_A
    assert notas[0]["motivo"].startswith("Exceção por etiqueta NERC")
    assert notas[1]["depois"] == ASS_A


def test_sem_etiqueta_o_agendamento_segue_a_regra_de_sempre(c, monkeypatch):
    svc = _servico(c, monkeypatch)
    propostas = [
        _proposta(c.tpl_fixo, 100, ADV_GERAL_1),
        _proposta(c.tpl_assistente, 200, ADV_A),
        _proposta(c.tpl_grupo_advogados, 300, ADV_A),
    ]
    payloads = [_payload(100, ADV_GERAL_1), _payload(200, ADV_A), _payload(300, ADV_A)]

    svc._apply_squad_routing_server_side(
        payloads=payloads, proposals=propostas, lawsuit_id=PASTA_SEM_ETIQUETA,
        operator_locked=[False, False, False], routing_notes=[None, None, None],
    )

    assert _destino(payloads[0]) == ADV_GERAL_1                      # cenário 1, intocado
    assert _destino(payloads[1]) in (ASS_GERAL_1, ASS_GERAL_2)       # rodízio de assistentes
    assert _destino(payloads[2]) in (ADV_GERAL_1, ADV_GERAL_2)       # rodízio de advogados


def test_escolha_do_operador_vence_a_excecao(c, monkeypatch):
    svc = _servico(c, monkeypatch)
    propostas = [_proposta(c.tpl_fixo, 100, ADV_GERAL_1), _proposta(c.tpl_assistente, 200, ADV_A)]
    # 1ª: operador trocou a pessoa; 2ª: re-escolheu a mesma (trava do modal).
    payloads = [_payload(100, ADV_FORA), _payload(200, ADV_A)]

    svc._apply_squad_routing_server_side(
        payloads=payloads, proposals=propostas, lawsuit_id=PASTA_NERC_A,
        operator_locked=[False, True], routing_notes=[None, None],
    )

    assert _destino(payloads[0]) == ADV_FORA
    assert _destino(payloads[1]) == ADV_A


def test_erro_da_excecao_aborta_o_agendamento_do_grupo(c, monkeypatch):
    svc = _servico(c, monkeypatch)

    with pytest.raises(ValueError, match="NERC"):
        svc._apply_squad_routing_server_side(
            payloads=[_payload(200, ADV_FORA)],
            proposals=[_proposta(c.tpl_assistente, 200, ADV_FORA)],
            lawsuit_id=PASTA_NERC_FORA, operator_locked=[False], routing_notes=[None],
        )


# ─── endpoints e cadastro ──────────────────────────────────────────────

def test_claim_da_tela_classica_aplica_a_excecao(c, monkeypatch):
    from app.api.v1.endpoints.squads import ResolveTargetRequest, _resolver_destino

    monkeypatch.setattr(modulo, "responsavel_da_pasta", _buscar)
    corpo = ResolveTargetRequest(
        target_role="assistente", target_squad_id=c.assistentes_geral.id,
        responsible_user_external_id=ADV_B,
        lawsuit_id=PASTA_NERC_B, template_id=c.tpl_assistente.id,
    )

    resposta = _resolver_destino(c.db, corpo, commit=False)

    assert resposta.user_external_id == ASS_B
    assert resposta.motivo and "NERC" in resposta.motivo


def test_claim_sem_processo_e_template_continua_como_antes(c):
    from app.api.v1.endpoints.squads import ResolveTargetRequest, _resolver_destino

    resposta = _resolver_destino(
        c.db,
        ResolveTargetRequest(target_role="assistente", target_squad_id=c.assistentes_geral.id),
        commit=False,
    )
    assert resposta.user_external_id in (ASS_GERAL_1, ASS_GERAL_2)
    assert resposta.motivo is None

    with pytest.raises(HTTPException) as erro:
        _resolver_destino(c.db, ResolveTargetRequest(target_role="principal"), commit=False)
    assert erro.value.status_code == 422


def test_api_de_template_so_aceita_papel_conhecido():
    from app.api.v1.endpoints.task_templates import TaskTemplateUpdate

    assert TaskTemplateUpdate(excecao_papel="assistente").excecao_papel == "assistente"
    assert TaskTemplateUpdate(excecao_papel=None).excecao_papel is None
    with pytest.raises(ValidationError):
        TaskTemplateUpdate(excecao_papel="advogado")


def test_seletor_da_tela_nao_oferece_nome_antigo_que_so_o_cache_guarda(c):
    """Em 10/09/2026 o cache ainda mostrava "BASE NERC" (nome antigo da própria
    NERC) em 333 processos, acima de "NERC". Configurar a exceção com esse nome
    faria ela nunca disparar."""
    from datetime import datetime, timedelta, timezone

    from app.services.excecao_etiqueta import etiquetas_em_uso

    lido_em_agosto = datetime.now(timezone.utc) - timedelta(days=30)
    for lawsuit_id in (950, 951, 952, 953):
        c.db.add(PublicationL1EtiquetaCache(
            lawsuit_id=lawsuit_id, etiquetas=_nerc("BASE NERC"), fetched_at=lido_em_agosto,
        ))
    c.db.commit()

    nomes = etiquetas_em_uso(c.db)

    assert nomes[0] == "NERC"
    assert "BASE NERC" not in nomes


def test_etiqueta_da_squad_define_e_limpa_sem_mexer_no_resto(c):
    from app.api.v1 import schemas
    from app.services.squad_service import SquadService

    servico = SquadService(c.db)

    servico.update_squad(c.celula.id, schemas.SquadUpdateSchema(etiqueta="  NERC "))
    assert c.celula.etiqueta == "NERC"

    servico.update_squad(c.celula.id, schemas.SquadUpdateSchema(name="CELULA 2 renomeada"))
    assert c.celula.etiqueta == "NERC", "renomear não pode apagar a etiqueta"

    servico.update_squad(c.celula.id, schemas.SquadUpdateSchema(etiqueta=None))
    assert c.celula.etiqueta is None
