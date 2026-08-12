"""
Testes da recuperação de execuções presas do Balanceador e da captura do
motivo de recusa do Legal One.

Os dois nasceram do mesmo diagnóstico, em 04/08/2026:

  - a execução de 31/07 08:00 aparecia "Em andamento" havia QUATRO DIAS, com
    58/58 e 39 tarefas presas — ninguém ia terminá-la;
  - havia 619 recusas do L1 com HTTP 400 e o campo `erro` VAZIO em todas: o
    código guardava só o `reason` e a mensagem morria no log, que já tinha
    rotacionado. Sem ela não dava pra saber por que o Legal One recusou.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.v1.endpoints.balanceador import _SEM_PROGRESSO_MINUTOS, _recuperar_zumbis
from app.db.session import Base
from app.models.performance import BalanceadorReatribuirJob


@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _job(db, *, minutos_atras, status="running", team="bb-reu",
         total=58, reatribuidas=0, wf=0, falhas=0, jid=None,
         iniciado_min=None):
    """`minutos_atras` = há quanto tempo o job parou de progredir.

    `iniciado_min` permite simular execução LONGA que segue viva — o caso que o
    critério antigo (tempo desde o início) matava injustamente.
    """
    agora = datetime.now(timezone.utc)
    j = BalanceadorReatribuirJob(
        id=jid or f"job{minutos_atras}{status}{total}{iniciado_min or 0}",
        team=team, status=status, total=total, feito=total,
        reatribuidas=reatribuidas, workflow_bloqueadas=wf, falhas=falhas,
        iniciado_em=agora - timedelta(minutes=iniciado_min or minutos_atras),
        atualizado_em=agora - timedelta(minutes=minutos_atras),
    )
    db.add(j)
    db.commit()
    return j


def test_execucao_presa_ha_dias_e_encerrada(db):
    """O caso real: 31/07 08:00 preso desde então."""
    j = _job(db, minutos_atras=60 * 24 * 4, total=58, reatribuidas=19)
    assert _recuperar_zumbis(db, "bb-reu") == 1
    db.refresh(j)
    assert j.status == "done"
    assert j.terminado_em is not None


def test_o_que_ficou_sem_desfecho_vai_pro_bucket_manual(db):
    """Não pode virar sucesso — as tarefas não foram reatribuídas."""
    j = _job(db, minutos_atras=60 * 24, total=58, reatribuidas=19, wf=0, falhas=0)
    _recuperar_zumbis(db, "bb-reu")
    db.refresh(j)
    assert j.workflow_bloqueadas == 39, "58 - 19 reatribuídas = 39 sem conclusão"
    assert j.reatribuidas == 19, "o que deu certo continua contando como certo"


def test_execucao_progredindo_agora_nao_e_tocada(db):
    """Commitou há 2 min: está viva."""
    j = _job(db, minutos_atras=2, iniciado_min=46, total=541)
    assert _recuperar_zumbis(db, "bb-reu") == 0
    db.refresh(j)
    assert j.status == "running"


def test_execucao_ja_concluida_nao_e_tocada(db):
    j = _job(db, minutos_atras=60 * 24, status="done", total=16, reatribuidas=16)
    assert _recuperar_zumbis(db, "bb-reu") == 0
    db.refresh(j)
    assert j.workflow_bloqueadas == 0


def test_aborting_preso_tambem_e_encerrado(db):
    j = _job(db, minutos_atras=60 * 5, status="aborting", total=10)
    assert _recuperar_zumbis(db, "bb-reu") == 1
    db.refresh(j)
    assert j.status == "done"


def test_so_mexe_no_time_pedido(db):
    outro = _job(db, minutos_atras=60 * 24, team="master-reu", jid="outro-time")
    _job(db, minutos_atras=60 * 24, team="bb-reu", jid="meu-time")
    assert _recuperar_zumbis(db, "bb-reu") == 1
    db.refresh(outro)
    assert outro.status == "running", "time diferente não pode ser tocado"


def test_job_sem_nada_pendente_nao_ganha_bucket_manual(db):
    j = _job(db, minutos_atras=60 * 24, total=10, reatribuidas=10)
    _recuperar_zumbis(db, "bb-reu")
    db.refresh(j)
    assert j.workflow_bloqueadas == 0


def test_falha_no_recuperador_nao_derruba_a_listagem(db):
    """A tela tem que abrir mesmo se a limpeza quebrar."""
    db.close()
    assert _recuperar_zumbis(db, "bb-reu") == 0


def test_execucao_longa_mas_viva_nao_e_morta(db):
    """O erro do critério antigo: matava pelo tempo desde o início.

    Uma redistribuição de 541 tarefas leva ~68 min no throttle real. Se ela
    segue commitando progresso, está viva — não importa há quanto tempo começou.
    """
    j = _job(db, minutos_atras=1, iniciado_min=200, total=541)
    assert _recuperar_zumbis(db, "bb-reu") == 0
    db.refresh(j)
    assert j.status == "running"


def test_execucao_recem_iniciada_que_ja_morreu_e_pega_rapido(db):
    """E o outro lado: morreu aos 5 min, não precisa esperar 2h pra sumir."""
    j = _job(db, minutos_atras=_SEM_PROGRESSO_MINUTOS + 5, iniciado_min=25, total=541)
    assert _recuperar_zumbis(db, "bb-reu") == 1
    db.refresh(j)
    assert j.status == "done"


def test_linha_antiga_sem_atualizado_em_cai_no_inicio(db):
    """Linha anterior à perf013: o início é o melhor sinal disponível."""
    j = _job(db, minutos_atras=1, total=10)
    j.atualizado_em = None
    j.iniciado_em = datetime.now(timezone.utc) - timedelta(hours=6)
    db.commit()
    assert _recuperar_zumbis(db, "bb-reu") == 1
