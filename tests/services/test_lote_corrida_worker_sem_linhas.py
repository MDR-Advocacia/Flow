# -*- coding: utf-8 -*-
"""Um lote nunca fica visível pro worker antes das linhas dele existirem.

O INCIDENTE (lote 6019, 08/09/2026)
-----------------------------------
Sthefanie subiu `agendamento_planilha_distribuido.xlsx` com 2.756
agendamentos. A tela mostrou **Concluído**, duração **1s**, `0 / 0 de 2756` —
e as 2.756 linhas continuavam PENDENTE no banco. Nenhum erro em lugar nenhum:
nem log, nem alerta, nem item marcado como falha. Só um visto verde por cima
de um trabalho que ninguém fez.

A causa era a ordem dos commits na criação:

    add(execucao); commit()      <-- publica a execução como PENDENTE
    for linha in 2756: add(...)  <-- ~1,5s inserindo
    commit()                     <-- só agora as linhas existem

O worker varre a fila de 5 em 5 segundos. Entre os dois commits ele
reivindicou o lote, consultou os itens PENDENTE, achou **zero**, concluiu que
não havia nada a fazer e fechou como CONCLUIDO.

A janela é do tamanho do tempo de inserir as linhas — ou seja, o defeito
castiga exatamente o lote grande e desaparece no lote pequeno. Foi por isso
que passou despercebido: a planilha de 321 linhas do mesmo dia rodou inteira.

O contrato aqui tem duas camadas, e as duas importam: a transação única
(elimina a janela) e a trava do laço (nunca carimba CONCLUIDO num lote que
declara linhas e não tem nenhuma), porque um visto verde falso é pior que uma
falha — a operação segue em frente achando que agendou.
"""
from datetime import timedelta

import pytest

from app.models.batch_execution import (
    BATCH_STATUS_CANCELLED,
    BATCH_STATUS_COMPLETED,
    BATCH_STATUS_PENDING,
    BATCH_STATUS_PROCESSING,
    BatchExecution,
    BatchExecutionItem,
)
from app.services import batch_task_creation_service as mod
from app.services.batch_task_creation_service import BatchTaskCreationService


LINHAS = [
    {"CNJ": "000%04d-11.2026.8.20.5106" % i, "SUBTIPO": "Manifestação"}
    for i in range(50)
]


def _svc(db):
    return BatchTaskCreationService(db=db, client=None)


# ── camada 1: a transação ──────────────────────────────────────────────


def test_nenhum_commit_publica_lote_sem_as_linhas(db_session, monkeypatch):
    """O invariante da corrida, medido onde ela acontece: no commit.

    Commit é a única coisa que outra transação enxerga. Se em ALGUM commit a
    execução já existe e as linhas dela não, existe um instante em que o
    worker pode reivindicar um lote vazio — que é o bug 6019.
    """
    monkeypatch.setattr(
        mod.SpreadsheetStrategy, "extract_rows_for_queue",
        lambda self, conteudo: {"rows": LINHAS},
    )

    fotos = []
    commit_real = db_session.commit

    def commit_espiao():
        commit_real()
        fotos.append((
            db_session.query(BatchExecution).count(),
            db_session.query(BatchExecutionItem).count(),
        ))

    monkeypatch.setattr(db_session, "commit", commit_espiao)

    _svc(db_session).create_spreadsheet_execution(
        file_content=b"", source_filename="x.xlsx", requested_by_email="a@b.c",
    )

    assert fotos, "a criação precisa commitar pelo menos uma vez"
    orfas = [(e, i) for e, i in fotos if e > 0 and i == 0]
    assert not orfas, (
        "houve commit com lote visível e ZERO linhas %s — é a janela do 6019"
        % orfas
    )
    assert fotos[-1] == (1, len(LINHAS))


# ── camada 2: a trava do laço ──────────────────────────────────────────


def _execucao_sem_linhas(db, *, total, idade_min=0):
    ex = BatchExecution(
        source="Planilha",
        processor_type="SPREADSHEET_UPLOAD",
        source_filename="agendamento_planilha_distribuido.xlsx",
        status=BATCH_STATUS_PROCESSING,
        total_items=total,
        start_time=BatchTaskCreationService._utcnow()
        - timedelta(minutes=idade_min),
        worker_id="w1",
    )
    db.add(ex)
    db.commit()
    db.refresh(ex)
    return ex


@pytest.mark.asyncio
async def test_lote_que_declara_linhas_e_nao_tem_nenhuma_volta_pra_fila(db_session):
    """O caso 6019 exato: 2.756 declaradas, zero gravadas, recém-criado.

    Concluir aqui é mentir. O certo é soltar a garra e deixar outro giro
    pegar — quando as linhas chegarem, o lote roda inteiro.
    """
    ex = _execucao_sem_linhas(db_session, total=2756)

    await _svc(db_session)._process_items_loop(ex.id, "w1", item_handler=None)

    db_session.refresh(ex)
    assert ex.status == BATCH_STATUS_PENDING, (
        "marcou %s num lote que não processou nada" % ex.status
    )
    assert ex.worker_id is None, "precisa soltar a garra pra outro giro pegar"
    assert ex.end_time is None, "não terminou coisa nenhuma"


@pytest.mark.asyncio
async def test_lote_velho_e_vazio_morre_como_cancelado_nunca_concluido(db_session):
    """Passada a espera, as linhas não vêm mais — mas segue proibido dizer
    CONCLUIDO. Devolver pra fila pra sempre seria trocar o visto falso por um
    lote imortal; o desfecho honesto é cancelado."""
    ex = _execucao_sem_linhas(db_session, total=2756, idade_min=30)

    await _svc(db_session)._process_items_loop(ex.id, "w1", item_handler=None)

    db_session.refresh(ex)
    assert ex.status == BATCH_STATUS_CANCELLED
    assert ex.status != BATCH_STATUS_COMPLETED


@pytest.mark.asyncio
async def test_lote_legitimamente_vazio_ainda_conclui(db_session):
    """Planilha sem linha nenhuma (total_items=0) não é anomalia: conclui.

    Sem esta, a trava viraria um lote imortal pra todo upload vazio.
    """
    ex = _execucao_sem_linhas(db_session, total=0)

    await _svc(db_session)._process_items_loop(ex.id, "w1", item_handler=None)

    db_session.refresh(ex)
    assert ex.status == BATCH_STATUS_COMPLETED
