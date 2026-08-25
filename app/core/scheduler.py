"""
Singleton do APScheduler compartilhado entre main.py e endpoints.

Mantido aqui (em vez de main.py) pra evitar import circular quando os endpoints
precisam injetar o scheduler via Depends().
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler

# O executor do APScheduler loga em INFO CADA disparo e CADA conclusão de job.
# Com ticks de 10s no ar, isso são ~5.500 linhas/hora no container — 90% do
# volume, e o suficiente pra afogar o que importa: em 24/08 o diagnóstico de
# um bug de template teve que ser garimpado no meio dessa enxurrada, e uma
# falha silenciosa da classificação passou 3 noites despercebida num log que
# ninguém consegue ler.
#
# WARNING mantém o que interessa (job que estourou exceção, job perdido por
# atraso) e descarta o "executed successfully" de cada minuto. Os logs dos
# nossos próprios workers não são afetados: eles usam logger próprio.
logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)

# Singleton process-wide. Iniciado/parado no lifespan do FastAPI (main.py).
scheduler: BackgroundScheduler = BackgroundScheduler()


def get_scheduler() -> BackgroundScheduler:
    """FastAPI dependency que devolve o scheduler singleton."""
    return scheduler
