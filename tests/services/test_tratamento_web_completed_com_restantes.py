# -*- coding: utf-8 -*-
"""Tratamento Web: "completed" com itens restantes não vira CONCLUÍDO.

O runner Node escreve `state: completed` no status.json quando o laço dele
acaba — e o laço pode acabar sem ter passado por tudo (fila reduzida no meio,
página que não abriu, item pulado). Até 08/09/2026 a regra só olhava
`failedCount` e `retryPendingCount`: com os dois em zero, o run virava
CONCLUÍDO mesmo com `remainingItems > 0`. É o mesmo visto verde falso do lote
6019 (2.756 declarados, 0 processados, "Concluído"): a contagem tem que
mandar mais que a palavra do runner.
"""
from app.models.publication_treatment import (
    RUN_STATUS_COMPLETED,
    RUN_STATUS_COMPLETED_WITH_ERRORS,
    RUN_STATUS_RUNNING,
)
from app.services.publication_treatment_service import PublicationTreatmentService as S


def _mapa(**payload):
    # O método só lê o payload; `self` não é tocado — chamada desamarrada.
    return S._map_runner_state_to_run_status(object(), payload)


def test_completed_com_itens_restantes_e_conclusao_com_falhas():
    assert _mapa(state="completed", failedCount=0, retryPendingCount=0,
                 remainingItems=37) == RUN_STATUS_COMPLETED_WITH_ERRORS


def test_completed_limpo_continua_concluido():
    assert _mapa(state="completed", failedCount=0, retryPendingCount=0,
                 remainingItems=0) == RUN_STATUS_COMPLETED


def test_completed_com_falhas_segue_com_falhas():
    """Regressão: o critério antigo não pode ter sido perdido."""
    assert _mapa(state="completed", failedCount=3, retryPendingCount=0,
                 remainingItems=0) == RUN_STATUS_COMPLETED_WITH_ERRORS


def test_running_nao_e_afetado():
    assert _mapa(state="running", remainingItems=500) == RUN_STATUS_RUNNING
