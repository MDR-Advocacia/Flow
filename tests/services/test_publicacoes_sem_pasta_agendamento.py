"""Agendamento AUTOMÁTICO da fila sem pasta (1º motor: Embargos à Execução).

Decisão do operador em 08/09/2026: para os tipos críticos, identificar não
basta — a tarefa de saneamento nasce sozinha, sem passar pela mesa. A tarefa
é AVULSA (sem pasta) de propósito: ela existe justamente para a equipe
CADASTRAR a pasta, e a partir daí a publicação seguinte cai na fila comum.

O que estes testes protegem, em ordem de importância:

  1. O LADO SEGURO. Interruptor desligado, tipo fora do mapa, mapa
     malformado, template ausente — em nenhum desses casos sai tarefa no
     Legal One. Deixar de agendar devolve trabalho ao operador; agendar
     errado cria tarefa de verdade na carteira de alguém.
  2. A publicação NÃO SE PERDE quando o agendamento não acontece: fica
     CLASSIFICADA, com a identificação salva, na fila do operador.
  3. Os três parâmetros que o operador ditou chegam inteiros ao payload:
     subtipo "Verificar novo Embargo - BB Autor", responsável Carolina, e o
     vencimento no DIA SEGUINTE.
"""
from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models as _models  # noqa: F401 - registra as tabelas em Base.metadata
from app.db.session import Base
from app.models.legal_one import (
    LegalOneOffice,
    LegalOneTaskSubType,
    LegalOneTaskType,
    LegalOneUser,
)
from app.models.publication_search import (
    RECORD_STATUS_CLASSIFIED,
    SEARCH_STATUS_COMPLETED,
    PublicationRecord,
    PublicationSearch,
)
from app.models.task_template import TaskTemplate
from app.services import publication_sem_pasta as compartilhado
from app.services import publication_sem_pasta_motor as motor

TIPO = "Embargos à Execução"
SUBTIPO_EMBARGO = 1404      # "Verificar novo Embargo - BB Autor"
CAROLINA = 1805             # Ana Carolina Viana Nascimento
BB_AUTOR = 22               # MDR Advocacia / Área operacional / Banco do Brasil / Autor
TIPO_PAI = 28               # Ativos e BB - Recuperação de Crédito

MAPA_PADRAO = '{"Embargos à Execução": {"office_l1": 22}}'


def _sessao():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)()


def _seed(db, com_template=True):
    db.add_all([
        # O fictício (-1) é o que casa o template; o 22 é para onde a tarefa vai.
        LegalOneOffice(external_id=-1, name="Publicações sem pasta", is_active=True),
        LegalOneOffice(external_id=BB_AUTOR, name="Autor", is_active=True),
        LegalOneUser(external_id=CAROLINA, name="Ana Carolina Viana Nascimento",
                     email="carolina@example.test", is_active=True),
        LegalOneTaskType(external_id=TIPO_PAI, name="Ativos e BB", is_active=True),
        LegalOneTaskSubType(external_id=SUBTIPO_EMBARGO,
                            name="Verificar novo Embargo - BB Autor",
                            parent_type_external_id=TIPO_PAI, is_active=True),
    ])
    if com_template:
        db.add(TaskTemplate(
            name="Verificar novo Embargo - BB Autor (sem pasta)",
            category=TIPO, subcategory=None, office_external_id=-1,
            task_subtype_external_id=SUBTIPO_EMBARGO,
            responsible_user_external_id=CAROLINA,
            priority="Normal", due_business_days=1, due_date_reference="today",
            description_template="Embargos a Execucao sem pasta - cadastrar a pasta.",
            is_active=True, taxonomy_version="v2", needs_taxonomy_review=False,
        ))
    busca = PublicationSearch(status=SEARCH_STATUS_COMPLETED, date_from="2026-09-08",
                             origin_type="OfficialJournalsCrawler")
    db.add(busca)
    db.flush()
    return busca


def _registro(db, busca, categoria=TIPO):
    rec = PublicationRecord(
        search_id=busca.id, legal_one_update_id=7001,
        linked_lawsuit_id=None,      # é isso que faz dela "sem pasta"
        linked_office_id=None,
        publication_date="2026-09-08T00:00:00Z",
        description="EMBARGOS A EXECUCAO opostos em face do Banco do Brasil.",
        category=categoria, subcategory="-", status=RECORD_STATUS_CLASSIFIED,
        is_duplicate=False,
    )
    db.add(rec)
    db.commit()
    return rec


class _L1Falso:
    """Registra o que teria ido para o Legal One. Nada sai daqui."""

    def __init__(self):
        self.payloads = []

    def create_task(self, payload):
        self.payloads.append(payload)
        return {"id": 4242}

    def format_last_create_task_error(self):
        return None


def _montar(monkeypatch, ligado, mapa=MAPA_PADRAO):
    """Prende os settings (que leem do DB real) e o cliente do L1."""
    l1 = _L1Falso()
    valores = {
        compartilhado.SETTING_AGENDAR_AUTO: "true" if ligado else "false",
        compartilhado.SETTING_AGENDAMENTO_AUTO: mapa,
    }
    monkeypatch.setattr(
        "app.services.app_settings.get_setting",
        lambda key, default=None: valores.get(key, default),
    )
    monkeypatch.setattr("app.services.legal_one_client.LegalOneApiClient", lambda: l1)
    return l1


# ── o lado seguro ────────────────────────────────────────────────────
def test_interruptor_desligado_nao_cria_tarefa(monkeypatch):
    """Default de deploy: nada sai sozinho enquanto o operador não liga."""
    db = _sessao()
    l1 = _montar(monkeypatch, ligado=False)
    rec = _registro(db, _seed(db))

    assert motor.agendar_automatico(db, rec) is None
    assert l1.payloads == []
    assert rec.status == RECORD_STATUS_CLASSIFIED


def test_tipo_fora_do_mapa_nao_cria_tarefa(monkeypatch):
    """Ligado é ligado para os tipos CONFIGURADOS, não para a fila inteira."""
    db = _sessao()
    l1 = _montar(monkeypatch, ligado=True)
    rec = _registro(db, _seed(db), categoria="Agravo de Instrumento")

    assert motor.agendar_automatico(db, rec) is None
    assert l1.payloads == []


def test_mapa_malformado_nao_cria_tarefa_para_ninguem(monkeypatch):
    """JSON quebrado no setting derruba o agendamento, nunca a rodada."""
    db = _sessao()
    l1 = _montar(monkeypatch, ligado=True, mapa="{isso nao e json")
    rec = _registro(db, _seed(db))

    assert motor.agendar_automatico(db, rec) is None
    assert l1.payloads == []


def test_sem_template_a_publicacao_fica_para_o_operador(monkeypatch):
    """Área -1 sem template configurado: não inventa tarefa e não perde a
    identificação — a publicação continua CLASSIFICADA na fila."""
    db = _sessao()
    l1 = _montar(monkeypatch, ligado=True)
    rec = _registro(db, _seed(db, com_template=False))

    assert motor.agendar_automatico(db, rec) is None
    assert l1.payloads == []
    assert rec.status == RECORD_STATUS_CLASSIFIED
    assert rec.category == TIPO


# ── o caminho feliz ──────────────────────────────────────────────────
def test_agenda_com_subtipo_responsavel_e_escritorio_ditados(monkeypatch):
    from app.services.prazos_iniciais.prazo_calculator import add_business_days
    from app.services.publication_search_service import RECORD_STATUS_SCHEDULED

    db = _sessao()
    l1 = _montar(monkeypatch, ligado=True)
    rec = _registro(db, _seed(db))

    task_id = motor.agendar_automatico(db, rec)

    assert task_id == 4242
    assert len(l1.payloads) == 1
    p = l1.payloads[0]
    assert p["subTypeId"] == SUBTIPO_EMBARGO
    assert p["typeId"] == TIPO_PAI
    assert [x["contact"]["id"] for x in p["participants"]] == [CAROLINA]
    # O fictício não existe no L1: a tarefa nasce no escritório REAL do mapa.
    assert p["responsibleOfficeId"] == BB_AUTOR
    assert p["originOfficeId"] == BB_AUTOR
    # A tarefa nasce AVULSA: nenhuma pasta vinculada no payload.
    assert "litigationId" not in p and "lawsuitId" not in p
    # "logo no dia seguinte": 1 dia útil a partir de HOJE, às 23:59:59 BRT.
    # Vai como 02:59:59Z do dia seguinte porque o L1 lê o número literal
    # como UTC e renderiza em BRT (subtrai 3h) — 23:59 local, na tela dele.
    vencimento = add_business_days(date.today(), 1)
    assert p["endDateTime"] == f"{vencimento + timedelta(days=1)}T02:59:59Z"

    assert rec.status == RECORD_STATUS_SCHEDULED
    assert rec.scheduled_by_name == "Motor sem pasta (agendamento automático)"
    trilha = (rec.raw_relationships or {}).get("_sem_pasta", {}).get("agendamento_automatico")
    assert trilha and trilha["task_id"] == 4242 and trilha["tipo"] == TIPO


# ── o escritório por tipo ────────────────────────────────────────────
def test_escritorio_do_tipo_vence_o_default_global(monkeypatch):
    """Embargos vai para BB Autor; tipo sem escritório próprio cai no default
    global (raiz MDR), que é o comportamento de sempre."""
    monkeypatch.setattr(
        "app.services.app_settings.get_setting",
        lambda key, default=None: {
            compartilhado.SETTING_AGENDAMENTO_AUTO: MAPA_PADRAO,
            compartilhado.SETTING_OFFICE_L1_TAREFA: "1",
        }.get(key, default),
    )
    assert compartilhado.office_l1_para_tarefa(TIPO) == BB_AUTOR
    # sem acento e em caixa alta é o mesmo tipo
    assert compartilhado.office_l1_para_tarefa("EMBARGOS A EXECUCAO") == BB_AUTOR
    assert compartilhado.office_l1_para_tarefa("Obrigação de Fazer / Astreintes") == 1
    assert compartilhado.office_l1_para_tarefa(None) == 1


# ── uma rodada por vez ───────────────────────────────────────────────
def test_rodada_simultanea_nao_reprocessa_nem_reagenda(monkeypatch):
    """Duas rodadas ao mesmo tempo criariam DUAS tarefas para o mesmo embargo.

    Antes do agendamento automático isso era só desperdício (a última
    classificação gravava por cima). Agora as duas agendariam, e o operador
    acharia a tarefa em dobro no Legal One. A trava é advisory lock do
    Postgres — banco, não memória — porque uma das portas da corrida é dois
    workers/containers com scheduler próprio, que `max_instances` não cobre.
    """
    db = _sessao()
    l1 = _montar(monkeypatch, ligado=True)
    rec = _registro(db, _seed(db))
    # A trava está com outra rodada.
    monkeypatch.setattr(motor, "_tomar_trava", lambda: (None, False))

    r = motor.executar(db, requested_by="teste")

    assert r["pulada"] is True
    assert l1.payloads == [], "agendou mesmo sem a trava"
    assert rec.status == RECORD_STATUS_CLASSIFIED, "mexeu na publicação sem a trava"
    from app.models.publication_sem_pasta import PublicacaoSemPastaRun
    run = db.query(PublicacaoSemPastaRun).filter_by(id=r["run_id"]).first()
    assert run.status == "skipped" and run.finished_at is not None

