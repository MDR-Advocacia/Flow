"""Bateria de consistência dos números do Balanceador (rodar em produção).

    docker exec -w /app <api> python scripts/validar_numeros_balanceador.py

Por que existe
--------------
Número que não fecha entre telas destrói a confiança no painel inteiro — e o
operador só descobre depois de tomar uma decisão errada com ele. Esta bateria
checa, para TODAS as equipes, as invariantes que sustentam a tabela do
Balanceador e o recorte "origem Publicações".

Cada verificação é independente da anterior e usa uma fonte diferente da que
gerou o número: o objetivo é discordar do serviço, não confirmá-lo.

Saída: relatório por invariante. Código de saída != 0 se qualquer uma falhar,
pra poder virar gate de deploy.
"""
from __future__ import annotations

import sys

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.performance.balanceador import BalanceadorService

falhas: list[str] = []
avisos: list[str] = []


def checar(nome: str, ok: bool, detalhe: str = "") -> None:
    print(f"  [{'OK ' if ok else 'FALHA'}] {nome}{(' — ' + detalhe) if detalhe else ''}")
    if not ok:
        falhas.append(f"{nome}: {detalhe}")


def avisar(nome: str, detalhe: str) -> None:
    print(f"  [aviso] {nome} — {detalhe}")
    avisos.append(f"{nome}: {detalhe}")


db = SessionLocal()
try:
    svc = BalanceadorService(db)
    equipes = [
        r.equipe for r in db.execute(
            text("SELECT DISTINCT equipe FROM perf_pessoa WHERE ativo AND equipe IS NOT NULL ORDER BY equipe")
        ).fetchall()
    ]
    print(f"Equipes ativas: {len(equipes)} — {', '.join(equipes)}\n")

    todas: list[dict] = []
    for eq in equipes:
        for linha in svc.diagnostico(eq):
            linha["_equipe"] = eq
            todas.append(linha)
    print(f"Colaboradores avaliados: {len(todas)}\n")

    # ── 1. O recorte nunca pode ser MAIOR que o número que ele recorta ──
    print("1. Recorte <= carga (por pessoa, por balde)")
    ruins = [
        (l["_equipe"], l["nome"], campo)
        for l in todas
        for campo in ("atrasado", "fatal_hoje", "futuro", "total")
        if l[f"{campo}_pub"] > l[campo]
    ]
    checar(
        "nenhum parêntese maior que o número ao lado",
        not ruins,
        f"{len(ruins)} violação(ões): {ruins[:3]}" if ruins else "",
    )

    # ── 2. Os baldes somam o total (o que a TELA mostra) ───────────────
    # A tabela exibe Atrasadas + Fatais + Futuras + Total. Se houver pendente
    # SEM PRAZO, o Total não fecha com a soma das três — e o operador vê isso
    # como "número errado". Hoje sem_prazo=0 em produção, mas é latente.
    print("\n2. Atrasadas + Fatais + Futuras == Total (carga cheia)")
    nao_fecha = [
        (l["_equipe"], l["nome"], l["atrasado"] + l["fatal_hoje"] + l["futuro"], l["total"], l["sem_prazo"])
        for l in todas
        if l["atrasado"] + l["fatal_hoje"] + l["futuro"] != l["total"]
    ]
    if nao_fecha:
        avisar(
            "soma das 3 colunas != Total",
            f"{len(nao_fecha)} pessoa(s) com pendente SEM PRAZO — a tela não mostra "
            f"essa coluna, então o Total parece errado. Ex.: {nao_fecha[:2]}",
        )
    else:
        checar("soma fecha para todos", True)

    print("\n3. Atrasadas(p) + Fatais(p) + Futuras(p) == Total(p) (recorte)")
    nao_fecha_pub = [
        (l["_equipe"], l["nome"], l["atrasado_pub"] + l["fatal_hoje_pub"] + l["futuro_pub"], l["total_pub"])
        for l in todas
        if l["atrasado_pub"] + l["fatal_hoje_pub"] + l["futuro_pub"] != l["total_pub"]
    ]
    if nao_fecha_pub:
        avisar(
            "soma dos parênteses != parêntese do Total",
            f"{len(nao_fecha_pub)} pessoa(s) — tarefa de Publicações SEM PRAZO. "
            f"Ex.: {nao_fecha_pub[:2]}",
        )
    else:
        checar("soma dos parênteses fecha para todos", True)

    # ── 4. Conferência contra query INDEPENDENTE (não passa pelo serviço) ──
    print("\n4. Total do serviço == contagem direta no banco")
    for eq in equipes[:6]:
        do_servico = sum(l["total"] for l in todas if l["_equipe"] == eq)
        direto = db.execute(
            text(
                "SELECT count(*) c FROM perf_l1_tarefa t "
                "JOIN perf_pessoa p ON p.id = t.pessoa_id "
                "WHERE p.equipe = :eq AND p.ativo AND t.status = 'Pendente'"
            ),
            {"eq": eq},
        ).scalar()
        checar(f"{eq}: serviço={do_servico} banco={direto}", do_servico == direto)

    print("\n5. Recorte do serviço == contagem direta do vínculo")
    for eq in equipes[:6]:
        do_servico = sum(l["total_pub"] for l in todas if l["_equipe"] == eq)
        direto = db.execute(
            text(
                "SELECT count(*) c FROM perf_l1_tarefa t "
                "JOIN perf_pessoa p ON p.id = t.pessoa_id "
                "WHERE p.equipe = :eq AND p.ativo AND t.status = 'Pendente' "
                "  AND t.l1_task_id IN (SELECT created_task_id FROM publicacao_tarefa_audit "
                "                        WHERE created_task_id IS NOT NULL)"
            ),
            {"eq": eq},
        ).scalar()
        checar(f"{eq}: serviço={do_servico} banco={direto}", do_servico == direto)

    # ── 6. Dedup: tarefa agendada N vezes conta 1 ──────────────────────
    print("\n6. Dedup do vínculo (tarefa reagendada não conta dobrado)")
    dup = db.execute(
        text(
            "SELECT count(*) FROM (SELECT created_task_id FROM publicacao_tarefa_audit "
            "WHERE created_task_id IS NOT NULL GROUP BY 1 HAVING count(*) > 1) x"
        )
    ).scalar()
    total_pub_global = sum(l["total_pub"] for l in todas)
    distinto = db.execute(
        text(
            "SELECT count(DISTINCT t.l1_task_id) FROM perf_l1_tarefa t "
            "JOIN perf_pessoa p ON p.id = t.pessoa_id "
            "WHERE p.ativo AND t.status='Pendente' "
            "  AND t.l1_task_id IN (SELECT created_task_id FROM publicacao_tarefa_audit "
            "                        WHERE created_task_id IS NOT NULL)"
        )
    ).scalar()
    checar(
        f"soma dos parênteses ({total_pub_global}) == tarefas distintas ({distinto})",
        total_pub_global == distinto,
        f"{dup} tarefa(s) têm mais de um agendamento na auditoria — o DISTINCT precisa segurar",
    )

    # ── 7. O limite do recorte é conhecido e exposto ───────────────────
    print("\n7. Limite do recorte")
    desde = svc.origem_publicacoes_desde()
    checar("data do agendamento mais antigo é conhecida", bool(desde), f"desde {desde}")
    orfas = db.execute(
        text(
            "SELECT count(*) FROM publicacao_tarefa_audit a "
            "WHERE a.created_task_id IS NOT NULL AND NOT EXISTS ("
            "  SELECT 1 FROM perf_l1_tarefa t WHERE t.l1_task_id = a.created_task_id)"
        )
    ).scalar()
    avisar(
        "auditoria sem correspondência no snapshot",
        f"{orfas} agendamento(s) apontam pra tarefa que não está no snapshot "
        "(concluída e fora da janela do relatório, ou cancelada) — esperado, não é erro",
    )

    # ── 8. Nada de negativo ────────────────────────────────────────────
    print("\n8. Sanidade")
    negativos = [
        (l["_equipe"], l["nome"], c)
        for l in todas
        for c in ("atrasado", "fatal_hoje", "futuro", "total",
                  "atrasado_pub", "fatal_hoje_pub", "futuro_pub", "total_pub")
        if l[c] < 0
    ]
    checar("nenhum número negativo", not negativos, str(negativos[:3]) if negativos else "")

finally:
    db.close()

print("\n" + "=" * 62)
if falhas:
    print(f"RESULTADO: {len(falhas)} FALHA(S)")
    for f in falhas:
        print("  -", f)
if avisos:
    print(f"Avisos (não bloqueiam): {len(avisos)}")
    for a in avisos:
        print("  -", a)
if not falhas:
    print("RESULTADO: todas as invariantes fecharam.")
sys.exit(1 if falhas else 0)
