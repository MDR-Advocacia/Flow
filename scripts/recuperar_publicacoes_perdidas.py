"""Recupera publicações que a captura nunca viu por causa do escritório errado.

Contexto (05/08/2026). O motor de Publicações filtra por escritório: pasta
parada no nó raiz ("MDR Advocacia", id 1) não entra em busca nenhuma, então a
publicação some sem deixar rastro — nem registro, nem descarte, nem auditoria.
633 pastas estavam nessa situação e foram corrigidas
(`scripts/corrigir_escritorio_responsavel.py`). Corrigir o escritório, porém,
não traz o passado de volta: a janela de captura daqueles dias já passou.

Este script varre o histórico DESSAS pastas especificamente, via
`/Updates?$filter=relationships/any(r: r/linkId eq <lawsuit_id>)`, e injeta o
que faltava pelo mesmo funil de sempre — `create_and_run_search(
prefetched_publications=...)`. Nada é gravado direto em `publicacao_registros`:
a publicação recuperada passa por enriquecimento, dedupe e classificação
exatamente como uma capturada no dia.

Por padrão entra só a publicação MAIS RECENTE de cada pasta (decisão da
operação em 05/08/2026): é a que ainda tem prazo vivo; as anteriores do mesmo
processo só inflariam a fila de classificação. `--todas` traz o histórico
inteiro do recorte.

O dedupe é o `legal_one_update_id` (unique): publicação que já entrou por outro
caminho é ignorada sozinha, então rodar de novo é seguro.

Uso:
    # levantamento: não escreve nada, só conta o que existe
    python scripts/recuperar_publicacoes_perdidas.py --plano /tmp/plano.json \
        --desde 2026-06-01

    # injeta de verdade na fila de classificação
    python scripts/recuperar_publicacoes_perdidas.py --plano /tmp/plano.json \
        --desde 2026-06-01 --aplicar
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from urllib.parse import quote

from app.db.session import SessionLocal
from app.models.publication_search import PublicationRecord
from app.services.legal_one_client import LegalOneApiClient
from app.services.publication_search_service import PublicationSearchService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("recuperar-publicacoes")

ORIGEM = "OfficialJournalsCrawler"


def _publicacoes_do_processo(client: LegalOneApiClient, lawsuit_id: int) -> list[dict]:
    """Todas as publicações vinculadas a UMA pasta.

    `relationships/any` é filtrável em /Updates (testado em prod 05/08/2026;
    devolve 200 e o count bate com a tela). É o único jeito de olhar o passado
    de uma pasta específica — a busca normal é por janela de data.
    """
    filtro = (
        f"originType eq '{ORIGEM}' and "
        f"relationships/any(r: r/linkId eq {int(lawsuit_id)})"
    )
    itens: list[dict] = []
    skip = 0
    while True:
        url = (
            f"{client.base_url}/Updates?$filter={quote(filtro, safe='')}"
            f"&$expand=relationships&$top=30&$skip={skip}"
        )
        resp = client._request_with_retry("GET", url)
        pagina = resp.json().get("value", [])
        if not pagina:
            break
        itens.extend(pagina)
        skip += 30
        if skip > 900:  # 30 páginas por pasta já é muito além do normal
            log.warning("pasta %s passou de 900 publicações — truncando", lawsuit_id)
            break
    return itens


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plano", required=True, help="JSON com os lawsuit_id corrigidos")
    ap.add_argument(
        "--desde", required=True,
        help="data mínima da publicação (AAAA-MM-DD). Publicação mais velha que "
             "isto é ignorada — prazo já morreu e o volume polui a fila.",
    )
    ap.add_argument("--aplicar", action="store_true", help="injeta na fila de verdade")
    ap.add_argument(
        "--todas", action="store_true",
        help="injeta TODAS as publicações do recorte. Sem esta flag entra só a "
             "MAIS RECENTE de cada pasta — decisão da operação: o que ainda tem "
             "prazo vivo é a última; as anteriores só inflam a fila.",
    )
    ap.add_argument(
        "--classificar", action="store_true",
        help="dispara a classificação automática junto com a injeção",
    )
    args = ap.parse_args()

    with open(args.plano, encoding="utf-8") as f:
        plano = json.load(f)
    lawsuit_ids = sorted({int(p["lawsuit_id"]) for p in plano if p.get("lawsuit_id")})
    log.info("pastas no plano: %s", len(lawsuit_ids))

    client = LegalOneApiClient()
    db = SessionLocal()
    try:
        ja_temos = {
            r[0] for r in db.query(PublicationRecord.legal_one_update_id).all()
        }
        log.info("publicações já registradas no Flow: %s", len(ja_temos))

        novas: list[dict] = []
        por_mes: Counter = Counter()
        vistas = 0
        for i, lid in enumerate(lawsuit_ids, 1):
            try:
                pubs = _publicacoes_do_processo(client, lid)
            except Exception as exc:
                log.error("pasta %s: falha ao ler publicações: %s", lid, exc)
                continue
            vistas += len(pubs)
            for p in pubs:
                data = (p.get("date") or "")[:10]
                if data < args.desde:
                    continue
                if p.get("id") in ja_temos:
                    continue
                novas.append(dict(p, _lawsuit_id=lid, _data=data))
            if i % 50 == 0:
                log.info(
                    "  %s/%s pastas lidas — %s publicações vistas, %s novas no recorte",
                    i, len(lawsuit_ids), vistas, len(novas),
                )

        candidatas = len(novas)
        if not args.todas:
            # Uma por pasta: a mais recente. Empate de data resolve pelo id do
            # andamento (o L1 e' crescente), pra escolha nao virar sorteio.
            ultima_por_pasta: dict[int, dict] = {}
            for p in novas:
                atual = ultima_por_pasta.get(p["_lawsuit_id"])
                chave = (p["_data"], int(p.get("id") or 0))
                if atual is None or chave > (atual["_data"], int(atual.get("id") or 0)):
                    ultima_por_pasta[p["_lawsuit_id"]] = p
            novas = list(ultima_por_pasta.values())
        for p in novas:
            por_mes[p["_data"][:7]] += 1
        for p in novas:
            p.pop("_lawsuit_id", None)
            p.pop("_data", None)

        log.info("=" * 60)
        log.info("publicações vistas nas pastas:      %s", vistas)
        log.info("novas no recorte (a partir de %s): %s", args.desde, candidatas)
        log.info(
            "a injetar: %s  (%s)",
            len(novas),
            "todas" if args.todas else "só a mais recente de cada pasta",
        )
        log.info("distribuição por mês:")
        for mes, n in sorted(por_mes.items()):
            log.info("   %s : %s", mes, n)

        if not args.aplicar:
            log.info("LEVANTAMENTO — nada foi injetado. Use --aplicar.")
            return 0
        if not novas:
            log.info("nada a injetar.")
            return 0

        datas = sorted((p.get("date") or "")[:10] for p in novas)
        svc = PublicationSearchService(db, client)
        resultado = svc.create_and_run_search(
            date_from=datas[0],
            date_to=datas[-1],
            origin_type=ORIGEM,
            auto_classify=args.classificar,
            requested_by="recuperacao-escritorio-raiz",
            prefetched_publications=novas,
        )
        log.info("busca criada: %s", json.dumps(resultado, ensure_ascii=False, default=str)[:600])
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
