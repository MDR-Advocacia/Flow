"""Processo FILHO da coleta Distribuídos BB: roda UMA coleta e sai.

Existe pra coleta ter PID próprio. Dentro do uvicorn ela era uma thread
daemon carregando Playwright, OneLog e um Chrome do undetected-chromedriver —
e thread não se mata. Em 08/09/2026 a run 240 travou dentro do Playwright com
o navegador vivo e ficou 59 minutos em silêncio absoluto: o reaper só podia
virar o status no banco, a árvore de Chrome (220 threads) ficou órfã até o
watchdog de PIDs a derrubar às 21:34, e nada no processo tinha como encerrar
a thread.

Como PROCESSO em sessão própria, o supervisor (`coleta_supervisor.py`) espera
com teto de relógio e, se estourar, mata o GRUPO — node, chrome, chromedriver,
tudo — de fora, sem depender de o Playwright cooperar. É o mesmo padrão que o
login do cancel-legacy-task usa há um mês (180 s + killpg) e que nunca vazou.

Este módulo é deliberadamente burro: parseia argumentos, configura log pra
stdout (o docker captura) e chama `executar_coleta_background`, que já abre a
própria sessão, faz as retentativas e fecha o run. Toda a inteligência
continua onde estava; só mudou quem a hospeda.

Uso (é o que o supervisor monta):
    python -m app.services.distribuidos_bb.coleta_runner --run-id 241 \
        --data-inicial 05/09/2026 --data-final 08/09/2026
"""

from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Roda uma coleta Distribuídos BB (processo filho).")
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--data-inicial", default=None)
    parser.add_argument("--data-final", default=None)
    parser.add_argument("--sem-envolvidos", action="store_true",
                        help="não captura envolvidos da capa do NPJ")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s - %(levelname)s - [coleta-filho run=%s] %%(message)s" % args.run_id,
    )
    log = logging.getLogger("distribuidos_bb.coleta_runner")
    log.info("iniciando (pid %s)", __import__("os").getpid())

    from app.services.distribuidos_bb.coleta_service import executar_coleta_background

    executar_coleta_background(
        args.run_id,
        data_inicial=args.data_inicial,
        data_final=args.data_final,
        coletar_envolvidos=not args.sem_envolvidos,
    )
    log.info("terminou")
    return 0


if __name__ == "__main__":  # pragma: no cover - entrada do processo
    sys.exit(main())
