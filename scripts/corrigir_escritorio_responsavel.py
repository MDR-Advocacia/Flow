"""Corrige em lote o ESCRITÓRIO RESPONSÁVEL de pastas paradas no escritório raiz.

Contexto (05/08/2026). O motor de Publicações busca no L1 filtrando por
escritório. Pasta cadastrada sem escritório responsável fica no nó raiz
("MDR Advocacia", id 1), que não está em nenhuma busca — então publicação
desses processos NUNCA é capturada, e não deixa rastro nenhum na auditoria
(o Flow não descartou: ele não viu). Foi assim que os processos
7000978-37.2026.8.22.0007 e 5000010-29.2026.8.01.0006 passaram batido, um
deles com prazo de réplica já decorrido.

O plano de destino é calculado fora daqui e chega como JSON: uma linha por
pasta, com `lawsuit_id`, `office_id` e o caminho do escritório. A regra que
gerou o plano de 05/08/2026 foi derivada dos próprios dados e validada por
backtest nas 61.414 pastas sadias (99,62% de acerto): herança do escritório da
pasta-pai quando é incidente/recurso, senão grupo do cliente + posição, com
"Trabalhista" vindo da natureza. Cliente ou posição fora do padrão não vira
palpite — sai do lote e vai pra decisão humana.

Escrita via endpoint web `ModalAlterarEmLote` — o PATCH REST de pasta esbarra
na trava de tenant, mesma história do Arquivar/Ativar.

Uso:
    # simulação (padrão): não escreve nada, só mostra o que faria
    python scripts/corrigir_escritorio_responsavel.py --plano /tmp/plano.json

    # canário: aplica de verdade em N pastas e confere uma a uma
    python scripts/corrigir_escritorio_responsavel.py --plano /tmp/plano.json \
        --aplicar --limite 1

    # lote completo
    python scripts/corrigir_escritorio_responsavel.py --plano /tmp/plano.json --aplicar
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import defaultdict

from app.services.legal_one_client import LegalOneApiClient
from app.services.prazos_iniciais.legacy_task_http_cancellation_service import (
    LegacyTaskHttpCancellationService,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("corrigir-escritorio")

# O L1 processa o lote de forma assíncrona. Lotes grandes num POST só aumentam
# a janela em que uma falha deixa o estado ambíguo — 25 mantém o raio pequeno
# sem transformar a correção em 600 requisições.
TAMANHO_LOTE = 25


def _ler_plano(caminho: str) -> list[dict]:
    with open(caminho, encoding="utf-8") as f:
        plano = json.load(f)
    faltando = [
        p for p in plano
        if not p.get("lawsuit_id") or not p.get("office_id")
    ]
    if faltando:
        raise SystemExit(
            f"plano inválido: {len(faltando)} linha(s) sem lawsuit_id ou office_id"
        )
    return plano


def _escritorio_atual(client: LegalOneApiClient, lawsuit_id: int) -> int | None:
    """Lê o escritório responsável direto da API (fonte da verdade)."""
    for endpoint in ("/Lawsuits", "/Litigations"):
        try:
            url = f"{client.base_url}{endpoint}({int(lawsuit_id)})"
            resp = client._request_with_retry(
                "GET", url, params={"$select": "id,responsibleOfficeId"}
            )
            if resp.status_code == 200:
                return resp.json().get("responsibleOfficeId")
        except Exception:
            continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plano", required=True, help="JSON com lawsuit_id/office_id")
    ap.add_argument(
        "--aplicar", action="store_true",
        help="escreve de verdade no L1 (sem isto é só simulação)",
    )
    ap.add_argument("--limite", type=int, default=0, help="processa só as N primeiras")
    args = ap.parse_args()

    plano = _ler_plano(args.plano)
    if args.limite:
        plano = plano[: args.limite]

    por_escritorio: dict[int, list[dict]] = defaultdict(list)
    for linha in plano:
        por_escritorio[int(linha["office_id"])].append(linha)

    log.info(
        "plano: %s pasta(s) em %s escritório(s) de destino",
        len(plano), len(por_escritorio),
    )
    for oid, linhas in sorted(por_escritorio.items(), key=lambda kv: -len(kv[1])):
        log.info("  %4d pasta(s) -> escritório %s (%s)",
                 len(linhas), oid, linhas[0].get("office_path", ""))

    if not args.aplicar:
        log.info("SIMULAÇÃO — nada foi escrito. Use --aplicar para valer.")
        return 0

    svc = LegacyTaskHttpCancellationService()
    client = LegalOneApiClient()
    enviados = 0
    falhas: list[str] = []

    for oid, linhas in sorted(por_escritorio.items()):
        caminho = linhas[0].get("office_path", "")
        ids = [int(x["lawsuit_id"]) for x in linhas]
        for i in range(0, len(ids), TAMANHO_LOTE):
            fatia = ids[i : i + TAMANHO_LOTE]
            try:
                svc.post_alterar_escritorio_responsavel(
                    lawsuit_ids=fatia, office_id=oid, office_text=caminho,
                )
                enviados += len(fatia)
                log.info(
                    "enviado: %s pasta(s) -> escritório %s (%s/%s do destino)",
                    len(fatia), oid, min(i + TAMANHO_LOTE, len(ids)), len(ids),
                )
            except Exception as exc:
                falhas.append(f"escritório {oid} fatia {i}: {exc}")
                log.error("FALHA no lote do escritório %s: %s", oid, exc)

    # A alteração é assíncrona do lado do L1: confirmar relendo, não confiar no 200.
    log.info("aguardando o L1 processar a fila antes de conferir...")
    time.sleep(30)

    amostra = plano if len(plano) <= 40 else plano[:: max(1, len(plano) // 40)]
    ok = divergentes = indefinidos = 0
    for linha in amostra:
        atual = _escritorio_atual(client, int(linha["lawsuit_id"]))
        if atual is None:
            indefinidos += 1
        elif int(atual) == int(linha["office_id"]):
            ok += 1
        else:
            divergentes += 1
            log.warning(
                "pasta %s (id %s): esperado %s, L1 diz %s",
                linha.get("pasta"), linha["lawsuit_id"], linha["office_id"], atual,
            )

    log.info("=" * 60)
    log.info("enviados: %s de %s", enviados, len(plano))
    log.info(
        "conferência por amostra (%s pastas): %s corretas, %s divergentes, %s sem leitura",
        len(amostra), ok, divergentes, indefinidos,
    )
    if falhas:
        log.error("%s falha(s):", len(falhas))
        for f in falhas:
            log.error("  - %s", f)
    return 1 if (falhas or divergentes) else 0


if __name__ == "__main__":
    sys.exit(main())
