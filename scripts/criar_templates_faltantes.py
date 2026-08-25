# -*- coding: utf-8 -*-
"""Cria os templates que faltam para as classificações que a operação recebe.

MOTIVO
------
Auditoria de 24/08/2026: 221 das 1.073 publicações aguardando tratamento (21%)
não tinham proposta de tarefa. Rodando a mesma lógica de casamento do
`_build_task_proposals`, o motivo foi medido, não suposto:

    E) escritório tem a categoria, falta ESSA subcategoria .... 177 (80%)
    D) categoria não configurada pra esse escritório .......... 29 (13%)
    B) o reparo trocou a categoria (polo) .................... 12 (5%)
    G) template existe mas está INATIVO ....................... 2 (1%)

Ou seja: era falta de template mesmo, não casamento quebrado.

COMO ESTE SCRIPT DECIDE
-----------------------
Para cada (escritório, categoria, subcategoria) que APARECE em publicação nos
últimos 120 dias e não casa com nenhum template ativo:

  1. herda o padrão do próprio escritório NAQUELA categoria — o subtipo mais
     usado entre os templates irmãos. É o "mais próximo que faz sentido
     lógico": reflete como a carteira já trabalha;
  2. se o escritório não tem nada na categoria, cai no subtipo de
     MANIFESTAÇÃO do escritório, que existe em todas as carteiras.

Responsável fica NULO de propósito: é o default do fluxo, que resolve o
responsável da PASTA no Legal One na hora de montar a proposta
(`prefetch_lawsuit_responsibles_cache`). Fixar nome aqui congelaria
roteamento que hoje é dinâmico.

POR QUE NÃO CURINGA (subcategoria NULL)
---------------------------------------
Curinga casaria JUNTO com os templates específicos que já existem — o match
devolve TODOS os que batem e cada um vira uma tarefa. Numa carteira como a
BB/Réu isso criaria tarefa duplicada em milhares de publicações que já
funcionam. Curinga só é seguro em escritório que não tem nenhum específico
(foi o caso da Recuperação de Honorários).

FORA DE ESCOPO (decisão do operador em 24/08)
---------------------------------------------
Escritórios sem template nenhum (Banese 42/44, BB Interessado 40) ficam de
fora. Continuam aparecendo como "Sem template" na tela, de propósito.

E "PARA ANÁLISE" NÃO GANHA TEMPLATE — nunca
-------------------------------------------
Regra do operador, e ela conserta um erro que a primeira versão deste script
cometeu em produção. Quando a publicação não é classificada numa categoria
definida, o padrão é impossibilidade de leitura por insuficiência textual:
isso tem que ir para o operador, não virar tarefa automática.

Havia também um motivo mecânico para o estrago: `repair_classification` faz
TODA subcategoria que não existe na árvore convergir para "Para Análise". Sem
deduplicar o plano pela chave FINAL (depois do reparo), 13 subcategorias cruas
diferentes viraram 13 templates IDÊNTICOS na mesma classificação — e o match
devolve todos, então a tela mostrava 13 tarefas repetidas. Foram criados 335
templates, 126 eram "Para Análise" e 13 eram duplicatas por convergência;
todos removidos, sobraram 196.

USO
---
    python scripts/criar_templates_faltantes.py            # dry-run
    python scripts/criar_templates_faltantes.py --aplicar  # cria
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict

from sqlalchemy import text

from app.db.session import SessionLocal
from app.models.task_template import TaskTemplate
from app.services.classifier.taxonomy import repair_classification
from app.services.publication_search_service import PublicationSearchService

JANELA_DIAS = 120


def montar_plano(db) -> tuple[list[dict], list[tuple]]:
    svc = PublicationSearchService.__new__(PublicationSearchService)
    svc.db = db

    combos = db.execute(text(
        "SELECT linked_office_id AS esc, category AS cat, "
        "       coalesce(subcategory, '-') AS sub, count(*) AS n "
        "  FROM publicacao_registros "
        " WHERE category IS NOT NULL AND is_duplicate = false "
        "   AND linked_office_id IS NOT NULL "
        "   AND created_at > now() - interval '%d days' "
        " GROUP BY 1, 2, 3" % JANELA_DIAS
    )).mappings().all()

    manifesta: dict[int, tuple[int, str]] = {}
    for row in db.execute(text(
        "SELECT t.office_external_id AS esc, "
        "       t.task_subtype_external_id AS sub_id, s.name, count(*) AS usos "
        "  FROM task_templates t "
        "  JOIN legal_one_task_subtypes s ON s.external_id = t.task_subtype_external_id "
        " WHERE t.is_active AND s.name ILIKE '%%manifesta%%' "
        " GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC"
    )).mappings():
        manifesta.setdefault(row["esc"], (row["sub_id"], row["name"]))

    padrao_cat: dict[tuple, list] = {}
    for t in db.query(TaskTemplate).filter(TaskTemplate.is_active == True).all():  # noqa: E712
        if t.office_external_id:
            padrao_cat.setdefault((t.office_external_id, t.category), []).append(t)

    def _moda(templates):
        c = Counter(t.task_subtype_external_id for t in templates)
        alvo = c.most_common(1)[0][0]
        return next(t for t in templates if t.task_subtype_external_id == alvo)

    plano: list[dict] = []
    fora: list[tuple] = []
    # Chave FINAL (depois do reparo). Sem isto, subcategorias cruas distintas
    # que o reparo colapsa na mesma viram templates idênticos — e cada um
    # gera uma tarefa na mesma publicação.
    ja_planejado: set[tuple] = set()
    for row in combos:
        oid, raw_cat, raw_sub, n = row["esc"], row["cat"], row["sub"], row["n"]
        polo = svc._resolve_office_polo(oid)
        try:
            rc, rs = repair_classification(raw_cat, raw_sub, polo_scope=polo,
                                           taxonomy_version="v2")
        except Exception:  # noqa: BLE001
            rc, rs = raw_cat, raw_sub

        subs = {s for s in (raw_sub, rs) if s and s != "-"}
        q = (db.query(TaskTemplate)
               .filter(TaskTemplate.is_active == True)  # noqa: E712
               .filter(TaskTemplate.needs_taxonomy_review == False)  # noqa: E712
               .filter(TaskTemplate.category == rc)
               .filter((TaskTemplate.office_external_id == oid)
                       | (TaskTemplate.office_external_id.is_(None))))
        q = (q.filter(TaskTemplate.subcategory.in_(subs)
                      | TaskTemplate.subcategory.is_(None))
             if subs else q.filter(TaskTemplate.subcategory.is_(None)))
        if q.count() > 0:
            continue

        sub_alvo = rs if rs and rs != "-" else (raw_sub if raw_sub != "-" else None)
        if not sub_alvo:
            fora.append((oid, rc, raw_sub, n, "classificação sem subcategoria"))
            continue
        # "Para Análise" é o balde do que a IA não conseguiu ler. Vai pro
        # operador de propósito — e é onde o reparo faz tudo convergir.
        if sub_alvo.lower().startswith("para an"):
            fora.append((oid, rc, sub_alvo, n, "Para Análise vai pro operador"))
            continue

        chave = (oid, rc, sub_alvo)
        if chave in ja_planejado:
            continue
        ja_planejado.add(chave)

        irmaos = padrao_cat.get((oid, rc))
        if irmaos:
            base = _moda(irmaos)
            plano.append({
                "office": oid, "category": rc, "subcategory": sub_alvo,
                "subtype": base.task_subtype_external_id,
                "dias": base.due_business_days or 1,
                "ref": base.due_date_reference or "today",
                "prioridade": base.priority or "Normal",
                "origem": "irmão %s da mesma categoria" % base.id,
                "publicacoes": n,
            })
        elif oid in manifesta:
            sub_id, nome = manifesta[oid]
            plano.append({
                "office": oid, "category": rc, "subcategory": sub_alvo,
                "subtype": sub_id, "dias": 1, "ref": "today",
                "prioridade": "Normal",
                "origem": "manifestação do escritório (%s)" % nome,
                "publicacoes": n,
            })
        else:
            fora.append((oid, rc, sub_alvo, n, "escritório sem template nenhum"))
    return plano, fora


def main() -> None:
    aplicar = "--aplicar" in sys.argv
    db = SessionLocal()
    try:
        plano, fora = montar_plano(db)
        print("templates a criar: %d | fora de escopo: %d" % (len(plano), len(fora)))

        nomes = {
            s.external_id: s.name for s in db.execute(text(
                "SELECT external_id, name FROM legal_one_task_subtypes"
            )).mappings()
        } if False else {}
        for r in db.execute(text(
            "SELECT external_id, name FROM legal_one_task_subtypes"
        )).mappings():
            nomes[r["external_id"]] = r["name"]

        por_esc = defaultdict(int)
        for p in plano:
            por_esc[p["office"]] += 1
        print("por escritório:", dict(sorted(por_esc.items())))

        if not aplicar:
            print("\n(dry-run — rode com --aplicar para criar)")
            for p in sorted(plano, key=lambda x: -x["publicacoes"])[:8]:
                print("  esc=%-4s %-38s | %-32s -> %s"
                      % (p["office"], p["category"][:38], p["subcategory"][:32],
                         nomes.get(p["subtype"], p["subtype"])))
            return

        criados = 0
        for p in plano:
            sub_nome = nomes.get(p["subtype"], str(p["subtype"]))
            db.add(TaskTemplate(
                name="%s / %s — %s" % (p["category"], p["subcategory"], sub_nome),
                category=p["category"],
                subcategory=p["subcategory"],
                office_external_id=p["office"],
                task_subtype_external_id=p["subtype"],
                # NULO de propósito: o fluxo resolve o responsável da PASTA.
                responsible_user_external_id=None,
                priority=p["prioridade"],
                due_business_days=p["dias"],
                due_date_reference=p["ref"],
                description_template="%s — processo {cnj}, publicado em {publication_date}."
                                     % p["subcategory"],
                target_role="principal",
                taxonomy_version="v2",
                needs_taxonomy_review=False,
                is_active=True,
            ))
            criados += 1
        db.commit()
        print("CRIADOS: %d templates" % criados)
    finally:
        db.close()


if __name__ == "__main__":
    main()
