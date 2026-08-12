"""
Testes do recorte "origem Publicações" no Balanceador.

Desenho combinado com a operação (05/08/2026):

  - a TABELA mostra sempre os dois números — a carga cheia e, entre
    parênteses, quanto dela veio de Publicações. O recorte NUNCA substitui a
    carga: quem decide "quem está sobrecarregado" precisa da fila inteira,
    senão manda mais trabalho pra quem já está afogado;
  - o FILTRO age só na hora de redistribuir, recortando o que vai ser movido.

O vínculo é exato (`publicacao_tarefa_audit.created_task_id` = id da tarefa no
L1), não heurístico: medido em produção, os subtipos mais "de Publicações"
ficam entre 88% e 99% e há casos de 65% e 54% — inferir por subtipo erraria de
5% a 45%.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.performance import PerfPessoa, PerfTarefa
from app.models.publication_task_audit import PublicationTaskAudit
from app.services.performance.balanceador import (
    BalanceadorService,
    marcar_origem_publicacoes,
)


@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _pessoa(db, nome="Rebeca", equipe="bb-reu"):
    p = PerfPessoa(nome=nome, nome_norm=nome.lower(), equipe=equipe, ativo=True)
    db.add(p)
    db.commit()
    return p


def _tarefa(db, pessoa, *, l1_id, dias_prazo, subtipo="Contrarrazões - BB Defesa"):
    """`dias_prazo` relativo a hoje: negativo = atrasada, 0 = fatal hoje."""
    db.add(PerfTarefa(
        l1_task_id=l1_id, pessoa_id=pessoa.id, status="Pendente", subtipo=subtipo,
        prazo_previsto=datetime.now(timezone.utc) + timedelta(days=dias_prazo),
    ))
    db.commit()


def _veio_de_publicacoes(db, l1_id, *, quando=None):
    db.add(PublicationTaskAudit(
        lawsuit_id=1, publication_record_id=1, subtype_id=1,
        created_task_id=l1_id,
        created_at=quando or datetime.now(timezone.utc),
    ))
    db.commit()


# ── O filtro da redistribuição (opção B) ──────────────────────────────
#
# A contagem por pessoa (diagnostico) é SQL Postgres puro — `AT TIME ZONE` não
# roda no SQLite da suíte, então ela é validada contra o banco de produção. O
# que dá pra fixar aqui é a REGRA que decide o que entra na redistribuição.

def _t(l1_id, subtipo="Contrarrazões - BB Defesa"):
    return {"l1_task_id": l1_id, "subtipo": subtipo, "situacao": "atrasado"}


def test_sem_o_recorte_nada_e_removido_mas_tudo_fica_marcado():
    """Desligado (o default), o supervisor vê a fila inteira — com o selo."""
    tarefas = [_t(1), _t(2), _t(3)]
    saida = marcar_origem_publicacoes(tarefas, {1, 3}, apenas=False)
    assert len(saida) == 3, "não pode sumir tarefa com o recorte desligado"
    assert [t["de_publicacoes"] for t in saida] == [True, False, True]


def test_com_o_recorte_so_sobram_as_de_publicacoes():
    saida = marcar_origem_publicacoes([_t(1), _t(2), _t(3)], {1, 3}, apenas=True)
    assert [t["l1_task_id"] for t in saida] == [1, 3]


def test_recorte_sem_nenhuma_de_publicacoes_devolve_vazio():
    """Melhor mover nada do que mover o que não foi pedido."""
    assert marcar_origem_publicacoes([_t(1), _t(2)], set(), apenas=True) == []


def test_tarefa_sem_id_nao_quebra_e_nao_entra_no_recorte():
    tarefas = [{"l1_task_id": None, "subtipo": "x"}, _t(5)]
    saida = marcar_origem_publicacoes(tarefas, {5}, apenas=True)
    assert [t["l1_task_id"] for t in saida] == [5]


def test_id_como_string_ainda_casa():
    """O L1 às vezes devolve o id como string — o join não pode depender disso."""
    saida = marcar_origem_publicacoes([{"l1_task_id": "7"}], {7}, apenas=True)
    assert len(saida) == 1


# ── O limite do recorte é exposto, não escondido ──────────────────────

def test_limite_do_recorte_e_a_data_do_agendamento_mais_antigo(db):
    """Tarefa de Publicações anterior à auditoria é invisível — a tela precisa
    dizer desde quando o número é confiável."""
    p = _pessoa(db)
    _tarefa(db, p, l1_id=1, dias_prazo=-1)
    _veio_de_publicacoes(db, 1, quando=datetime(2026, 6, 19, tzinfo=timezone.utc))
    _tarefa(db, p, l1_id=2, dias_prazo=-1)
    _veio_de_publicacoes(db, 2, quando=datetime(2026, 8, 5, tzinfo=timezone.utc))

    assert BalanceadorService(db).origem_publicacoes_desde() == "2026-06-19"


def test_sem_auditoria_nenhuma_o_limite_e_nulo(db):
    _pessoa(db)
    assert BalanceadorService(db).origem_publicacoes_desde() is None
