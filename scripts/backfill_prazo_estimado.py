# -*- coding: utf-8 -*-
"""Recalcula `publicacao_registros.prazo_estimado` (pub012) em massa.

QUANDO RODAR
------------
1. Uma vez depois do deploy da pub012 (o estoque nasce todo NULL);
2. SEMPRE que preencher/alterar `default_prazo_dias`/`default_prazo_tipo`
   na taxonomia — o hook de classificação só cobre registro novo; o estoque
   já classificado precisa deste recálculo.

Seguro re-rodar quantas vezes quiser: a conta é determinística e escreve o
mesmo valor. Percorre TODOS os registros com categoria (tratados inclusive —
o histórico serve de base pros relatórios de conformidade de prazo).

Uso (no container da API):
    python scripts/backfill_prazo_estimado.py            # tudo
    python scripts/backfill_prazo_estimado.py --pendentes  # só NOVO/CLASSIFICADO/ERRO
"""
import sys

from sqlalchemy import or_

from app.db.session import SessionLocal
from app.models.publication_search import PublicationRecord
from app.services.publication_prazo_estimado import PrazoEstimadoResolver

LOTE = 2000

so_pendentes = "--pendentes" in sys.argv

db = SessionLocal()
resolver = PrazoEstimadoResolver(db)
print("defaults de prazo carregados da taxonomia: %d de categoria, %d de subcategoria"
      % (len(resolver._cat), len(resolver._sub)))
if not resolver._cat and not resolver._sub:
    print("AVISO: nenhum default preenchido — tudo vai ficar/continuar NULL. "
          "Preencha default_prazo_dias na taxonomia e rode de novo.")

query = db.query(PublicationRecord).filter(PublicationRecord.category.isnot(None))
if so_pendentes:
    query = query.filter(
        PublicationRecord.status.in_(["NOVO", "CLASSIFICADO", "ERRO"]))

total = query.count()
print("registros a recalcular: %d%s" % (total, " (só pendentes)" if so_pendentes else ""))

vistos = alterados = com_prazo = 0
ultimo_id = 0
while True:
    lote = (query.filter(PublicationRecord.id > ultimo_id)
            .order_by(PublicationRecord.id).limit(LOTE).all())
    if not lote:
        break
    for rec in lote:
        novo = resolver.calcular(rec.publication_date, rec.category, rec.subcategory)
        if novo != rec.prazo_estimado:
            rec.prazo_estimado = novo
            alterados += 1
        if novo is not None:
            com_prazo += 1
        vistos += 1
    ultimo_id = lote[-1].id
    db.commit()
    if vistos % 10000 < LOTE:
        print("   %d/%d (alterados %d)" % (vistos, total, alterados), flush=True)

print("\nfim: %d vistos | %d alterados | %d com prazo estimado | %d sem default"
      % (vistos, alterados, com_prazo, vistos - com_prazo))
db.close()
