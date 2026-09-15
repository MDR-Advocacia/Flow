"""Sobe o uvicorn com --workers tolerando host lento no health check dos workers.

O supervisor multiprocess do uvicorn manda um ping pra cada worker a cada
0,5 s e espera o pong por 5 s. Sem resposta, mata o worker com SIGKILL e sobe
outro. No uvicorn 0.34 esses 5 s são fixos (a opção
`--timeout-worker-healthcheck` só existe a partir do 0.37).

Incidente de 15/09/2026, 08:49: o host (16 GB, swap de 4 GB cheia) entrou em
OOM global e ficou uns 40 s praticamente parado. Log escrito às 08:49:05 só
chegou ao Docker às 08:49:38, e o próprio dump do OOM no kernel levou 36 s. Nenhum
worker da API foi morto pelo kernel: quem matou dois deles (08:49:38 e
08:49:44) foi o supervisor, porque o pong não voltou em 5 s. Junto foram as
requisições em andamento, o scheduler (o líder era um dos dois) e a thread
do lote 40 do Master, que estava no meio do import no Legal One.

Worker que só está lento não deve morrer: o reinício custa mais do que a espera
e, com o host sem memória, subir um worker novo (importar a aplicação inteira)
piora a situação. A thread do pong é separada do event loop, então endpoint
bloqueando o loop NÃO faz o worker falhar no ping. O que faz é o processo inteiro
parado (host sem memória ou CPU) ou o GIL preso numa chamada C.

Por isso a espera pelo pong passa a ser `UVICORN_TIMEOUT_WORKER_HEALTHCHECK`
segundos (padrão 120; o maior travamento medido foi ~50 s, no OOM de
15/09 01:01). Worker que não responde nem assim continua sendo reiniciado.

Uso (scripts/docker-api-start.sh):
    python -m app.core.uvicorn_supervisor main:app --workers 4 ...

Os argumentos são os do próprio uvicorn. Com uvicorn >= 0.37 usa a opção
nativa; antes disso, troca a espera do `Process.is_alive` do supervisor.
"""
from __future__ import annotations

import inspect
import logging
import os
import sys
import time
from typing import Optional, Sequence

# Mesmo logger das mensagens "Child process [N] died" do supervisor.
logger = logging.getLogger("uvicorn.error")

ENV_TIMEOUT = "UVICORN_TIMEOUT_WORKER_HEALTHCHECK"
TIMEOUT_PADRAO_S = 120.0
# O que o uvicorn 0.34 espera hoje, fixo. Nunca esperamos menos que isso.
PING_UVICORN_S = 5.0
FLAG_NATIVA = "--timeout-worker-healthcheck"


def timeout_health_check_s() -> float:
    """Espera pelo pong em segundos, lida do ambiente (painel do Coolify)."""
    bruto = (os.environ.get(ENV_TIMEOUT) or "").strip()
    try:
        valor = float(bruto) if bruto else TIMEOUT_PADRAO_S
    except ValueError:
        logger.warning("%s=%r inválido; usando %.0f s.", ENV_TIMEOUT, bruto, TIMEOUT_PADRAO_S)
        valor = TIMEOUT_PADRAO_S
    return max(PING_UVICORN_S, valor)


def uvicorn_tem_opcao_nativa() -> bool:
    from uvicorn.config import Config

    return "timeout_worker_healthcheck" in inspect.signature(Config.__init__).parameters


def instalar_tolerancia(timeout_s: float) -> None:
    """Troca o `is_alive` do supervisor do uvicorn por um que espera `timeout_s`.

    Mantém o resto do contrato: processo que já morreu volta False na hora
    (e é substituído); worker que não responde nem em `timeout_s` volta False
    e é morto como antes. Idempotente.
    """
    from uvicorn.supervisors import multiprocess as mp

    if getattr(mp.Process.is_alive, "_flow_tolerante", False):
        return

    def is_alive(self, timeout: float = PING_UVICORN_S) -> bool:
        if not self.process.is_alive():
            return False
        espera = max(timeout, timeout_s)
        inicio = time.monotonic()
        respondeu = self.ping(espera)
        demora = time.monotonic() - inicio
        if not respondeu:
            logger.error(
                "Worker [%s] não respondeu ao health check em %.0f s; vai ser reiniciado.",
                self.pid, demora,
            )
        elif demora > PING_UVICORN_S:
            logger.warning(
                "Worker [%s] levou %.0f s para responder ao health check (host lento: "
                "memória ou CPU?). Mantido; antes seria reiniciado aos %.0f s.",
                self.pid, demora, PING_UVICORN_S,
            )
        return respondeu

    is_alive._flow_tolerante = True  # type: ignore[attr-defined]
    mp.Process.is_alive = is_alive  # type: ignore[method-assign]


def montar_argv(argv: Sequence[str], timeout_s: float, nativo: bool) -> list[str]:
    """Argumentos do uvicorn; com a opção nativa, acrescenta o timeout se faltar."""
    args = list(argv)
    ja_tem = any(a == FLAG_NATIVA or a.startswith(FLAG_NATIVA + "=") for a in args)
    if nativo and not ja_tem:
        args += [FLAG_NATIVA, str(int(timeout_s))]
    return args


def main(argv: Optional[Sequence[str]] = None) -> None:
    from uvicorn.main import main as uvicorn_main

    timeout_s = timeout_health_check_s()
    nativo = uvicorn_tem_opcao_nativa()
    if not nativo:
        instalar_tolerancia(timeout_s)
    args = montar_argv(sys.argv[1:] if argv is None else argv, timeout_s, nativo)
    uvicorn_main(args=args, prog_name="uvicorn")


# Os workers do uvicorn nascem por spawn e reimportam este módulo como
# __mp_main__: nada pode rodar fora deste if.
if __name__ == "__main__":
    main()
