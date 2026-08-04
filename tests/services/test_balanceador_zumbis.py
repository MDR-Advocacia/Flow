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

from app.api.v1.endpoints.balanceador import _ZUMBI_APOS_MINUTOS, _recuperar_zumbis
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
         total=58, reatribuidas=0, wf=0, falhas=0, jid=None):
    j = BalanceadorReatribuirJob(
        id=jid or f"job{minutos_atras}{status}{total}",
        team=team, status=status, total=total, feito=total,
        reatribuidas=reatribuidas, workflow_bloqueadas=wf, falhas=falhas,
        iniciado_em=datetime.now(timezone.utc) - timedelta(minutes=minutos_atras),
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


def test_execucao_recente_nao_e_tocada(db):
    """A de 04/08 seguia progredindo aos 46 min — não pode ser morta."""
    j = _job(db, minutos_atras=_ZUMBI_APOS_MINUTOS - 30, total=541)
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
