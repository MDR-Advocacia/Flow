"""Retenção dos artefatos do Playwright (capturas de tela dos runners).

## Por que existe

Medido em 13/08/2026 no container de produção: `/app/output` estava com
**5,8 GB**, dos quais **5,21 GB** eram runs do tratamento de publicações — 164
runs guardados desde sempre, sem nenhuma política de descarte, num disco a 83%.

A composição de um run mostra onde está o peso:

    artifacts/       265 MB   ← 1.230 PNGs + 1.230 JSONs (1 por publicação)
    status.json      1,6 MB   ← é o que a tela lê (histórico e progresso)
    runner.log       1,3 MB
    input.json       552 KB
    runner.err.log     4 KB

Ou seja: **99% do volume são as capturas de diagnóstico**, e todo o resto —
justamente o que a UI e a auditoria usam — cabe em ~3,5 MB por run.

Por isso a retenção apaga SÓ o `artifacts/` dos runs antigos e preserva
status, logs e input. O histórico na tela continua completo, dá pra auditar o
que foi tratado, e o que some é apenas a captura de tela de um item tratado há
mais de uma semana — que na prática ninguém volta a abrir.

Descarte por IDADE, não por quantidade: um dia de pico não pode empurrar pra
fora um run de ontem que ainda interessa.
"""
from __future__ import annotations

import logging
import os
import shutil
import time

logger = logging.getLogger(__name__)

# Raízes varridas. Cada uma tem subpastas por run (`run-000161/artifacts`).
RAIZES = (
    "/app/output/playwright/legalone/publication-treatment",
    "/app/output/playwright/legalone/varredura-andamentos",
)

# Sete dias. A janela foi ESCOLHIDA COM MEDIÇÃO, não por gosto — simulei todas
# contra o disco real em 13/08/2026 e o volume está concentrado nos últimos
# dias, porque o autorun do tratamento passou a rodar 4x/dia sobre filas
# grandes (um run recente sozinho tem 265 MB e 1.230 capturas):
#
#     30 dias -> libera 0,21 GB   |  sobra ~5,00 GB
#     14 dias -> libera 0,39 GB   |  sobra ~4,82 GB
#      7 dias -> libera 1,94 GB   |  sobra ~3,27 GB
#      3 dias -> libera 3,54 GB   |  sobra ~1,67 GB
#
# Reter 14 ou 30 dias seria escolher uma política que quase não faz nada. Sete
# dias dá uma semana inteira pra investigar qualquer tratamento e ainda TRAVA o
# crescimento, que é o ponto — o disco estava em 83%.
#
# O que se perde após 7 dias é só a captura de tela; o `status.json` guarda
# status e erro de cada item tratado e NÃO é apagado nunca.
DIAS_RETENCAO = 7

# Só isto é descartável. O resto do run fica.
PASTA_DESCARTAVEL = "artifacts"


def limpar(dias: int = DIAS_RETENCAO, dry_run: bool = False) -> dict:
    """Apaga `artifacts/` de runs mais velhos que `dias`. Devolve o resumo.

    Best-effort por run: falha em um não interrompe os outros — é limpeza de
    disco, nunca pode derrubar nada.
    """
    corte = time.time() - dias * 86400
    resumo = {"runs_examinados": 0, "runs_limpos": 0, "bytes_liberados": 0, "erros": 0}

    for raiz in RAIZES:
        if not os.path.isdir(raiz):
            continue
        for nome in os.listdir(raiz):
            run_dir = os.path.join(raiz, nome)
            art = os.path.join(run_dir, PASTA_DESCARTAVEL)
            if not os.path.isdir(art):
                continue
            resumo["runs_examinados"] += 1

            # A idade vem do próprio run (mtime da pasta de artefatos): um run
            # ainda em execução tem mtime recente e nunca entra no corte.
            try:
                if os.path.getmtime(art) >= corte:
                    continue
            except OSError:
                continue

            tamanho = 0
            try:
                for sub, _, arquivos in os.walk(art):
                    for a in arquivos:
                        try:
                            tamanho += os.path.getsize(os.path.join(sub, a))
                        except OSError:
                            pass
                if not dry_run:
                    shutil.rmtree(art)
                resumo["runs_limpos"] += 1
                resumo["bytes_liberados"] += tamanho
            except Exception:  # noqa: BLE001
                resumo["erros"] += 1
                logger.warning("Retenção: falha ao limpar %s", art, exc_info=True)

    gb = resumo["bytes_liberados"] / 1073741824
    logger.info(
        "Retenção de artefatos (%s dias%s): %s run(s) limpo(s) de %s examinado(s), "
        "%.2f GB liberados, %s erro(s).",
        dias, " — SIMULAÇÃO" if dry_run else "", resumo["runs_limpos"],
        resumo["runs_examinados"], gb, resumo["erros"],
    )
    return resumo


def _tick() -> None:
    try:
        limpar()
    except Exception:  # noqa: BLE001
        logger.exception("Retenção de artefatos do Playwright falhou.")


def register_playwright_artifacts_cleanup_job(scheduler) -> None:
    """Diário às 04:20 UTC (01:20 BRT) — fora da janela de trabalho e longe
    das coletas agendadas (03h/12h/20h BRT) pra não disputar I/O com elas."""
    scheduler.add_job(
        _tick,
        trigger="cron",
        hour=4,
        minute=20,
        id="playwright.artifacts_cleanup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    logger.info(
        "Retenção de artefatos do Playwright registrada (diária 04:20 UTC, "
        "%s dias).", DIAS_RETENCAO,
    )
