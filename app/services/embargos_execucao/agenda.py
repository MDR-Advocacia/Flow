"""Calendário do monitoramento: janela inicial e cadência em DIAS ÚTEIS.

Regra do operador (11/09/2026): a consulta ao tribunal começa N dias úteis
depois do ajuizamento (15, 20 ou 25, ajustável) e se repete a cada 5 dias
úteis até achar os embargos. Só feriados nacionais — sem recesso local.
"""
from __future__ import annotations

from datetime import date

from app.services.prazos_iniciais.prazo_calculator import (
    add_business_days,
    proximo_dia_util,
)

JANELAS_PERMITIDAS = (15, 20, 25)


def inicio_monitoramento(data_ajuizamento: date, dias_uteis: int) -> date:
    return add_business_days(data_ajuizamento, int(dias_uteis))


def proxima_consulta(apos: date, intervalo_dias_uteis: int) -> date:
    return add_business_days(apos, max(1, int(intervalo_dias_uteis)))


def primeira_consulta(data_ajuizamento: date, dias_uteis: int, hoje: date) -> date:
    """Janela já vencida (legado importado tarde) consulta no próximo dia útil
    a partir de hoje, não numa data do passado."""
    inicio = inicio_monitoramento(data_ajuizamento, dias_uteis)
    return inicio if inicio > hoje else proximo_dia_util(hoje)
