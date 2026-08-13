# -*- coding: utf-8 -*-
"""
Reanálise do lote de validação, SEM gastar token novo.

POR QUE ISTO EXISTE
-------------------
A primeira derivação foi reprovada: recuperação de 79,3% (ótima, perto do teto
de 83%) mas FALSO-IGNORAR de 46,6% — a acurácia do `r5_default` cairia de 68,7%
para 61,5%. A taxa perigosa sozinha reprova, como combinado antes de rodar.

O diagnóstico está na distribuição das respostas: o modelo respondeu "juizo"
para 1.231 publicações (44% do conjunto), e eu havia colocado "juizo" na
condição de ignorar. Isso foi erro MEU de derivação, não do modelo — ele
respondeu a pergunta que eu fiz; eu é que traduzi "o ato cabe ao juízo" para
"então descarta", quando na prática o operador agenda boa parte desses
(conclusos, designação, expediente que ainda exige acompanhamento nosso).

As respostas cruas continuam válidas. O que muda é a REGRA DE DECISÃO em cima
delas — e isso se testa offline.

MÉTODO
------
1. Tabela de células (quem_pratica_ato × exige_providencia) com a taxa REAL de
   ignorar do operador em cada uma. É a leitura descritiva: quais combinações
   são de fato descartáveis.
2. Split treino/validação 50/50 determinístico (paridade do record_id). As
   células são escolhidas SÓ no treino e aplicadas cegas na validação — sem
   isso eu estaria escolhendo o corte e medindo no mesmo dado, que é como se
   fabrica um número bonito e falso.
3. Varredura de limiar de pureza, reportando as duas taxas assimétricas em
   cada um, pra a escolha ser sua e não minha.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from sqlalchemy import text

from app.db.session import SessionLocal

ARQ = "/tmp/validacao_quem_pratica_respostas.json"

SQL = """
SELECT s.record_id, s.real,
       coalesce(s.real_motivo, r.ignore_reason, '(sem)') AS motivo
  FROM publicacao_shadow_decisao s
  JOIN publicacao_registros r ON r.id = s.record_id
  JOIN legal_one_offices o ON o.external_id = r.linked_office_id
 WHERE s.regra = 'r5_default' AND s.real IS NOT NULL
   AND o.polo_scope IN ('ativo','passivo')
"""


def _celula(resp: dict[str, Any]) -> tuple[str, bool]:
    quem = str(resp.get("quem_pratica_ato", "?")).lower()
    exige = bool(resp.get("exige_providencia_nossa", True))
    return quem, exige


def main() -> None:
    respostas: dict[str, dict[str, Any]] = json.load(open(ARQ, encoding="utf-8"))

    db = SessionLocal()
    try:
        linhas = db.execute(text(SQL)).all()
    finally:
        db.close()
    gab = {str(r[0]): {"real": r[1], "motivo": r[2]} for r in linhas}

    dados = []
    for cid, resp in respostas.items():
        if cid not in gab:
            continue
        quem, exige = _celula(resp)
        dados.append({
            "id": int(cid), "quem": quem, "exige": exige,
            "real": gab[cid]["real"], "motivo": gab[cid]["motivo"],
        })

    # ── 1) tabela descritiva de células ────────────────────────────────────
    print("=" * 72)
    print("CÉLULAS — taxa REAL de ignorar do operador em cada resposta")
    print("=" * 72)
    print(f"{'quem_pratica_ato':<16} {'exige':<7} {'n':>5} {'ignorou':>8} {'pureza':>8}")
    cel: dict[tuple[str, bool], list[dict]] = defaultdict(list)
    for d in dados:
        cel[(d["quem"], d["exige"])].append(d)
    for (quem, exige), itens in sorted(cel.items(), key=lambda kv: -len(kv[1])):
        ign = sum(1 for i in itens if i["real"] == "IGNORADO")
        print(f"{quem:<16} {str(exige):<7} {len(itens):>5} {ign:>8} "
              f"{100.0*ign/len(itens):>7.1f}%")

    # ── 2) split determinístico ────────────────────────────────────────────
    treino = [d for d in dados if d["id"] % 2 == 0]
    valid = [d for d in dados if d["id"] % 2 == 1]
    print(f"\nSplit: treino={len(treino)}  validação={len(valid)}")

    pureza_treino: dict[tuple[str, bool], float] = {}
    for (quem, exige), itens in defaultdict(
        list, {k: [d for d in treino if (d["quem"], d["exige"]) == k] for k in cel}
    ).items():
        if len(itens) >= 20:  # célula rala não vira regra
            pureza_treino[(quem, exige)] = (
                sum(1 for i in itens if i["real"] == "IGNORADO") / len(itens)
            )

    # ── 3) varredura de limiar, medida SÓ na validação ─────────────────────
    print("\n" + "=" * 72)
    print("LIMIAR DE PUREZA — células escolhidas no TREINO, medidas na VALIDAÇÃO")
    print("=" * 72)
    print(f"{'limiar':>7} {'células':>8} {'recup.':>16} {'falso-ignorar':>18} {'acurácia':>18}")

    base_err = sum(1 for d in valid if d["real"] == "IGNORADO")
    base_ok = sum(1 for d in valid if d["real"] == "AGENDADO")
    acur_velha = 100.0 * base_ok / max(len(valid), 1)

    for limiar in (0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95):
        alvo = {k for k, p in pureza_treino.items() if p >= limiar}
        rec = sum(1 for d in valid
                  if d["real"] == "IGNORADO" and (d["quem"], d["exige"]) in alvo)
        fal = sum(1 for d in valid
                  if d["real"] == "AGENDADO" and (d["quem"], d["exige"]) in alvo)
        acur_nova = 100.0 * (base_ok - fal + rec) / max(len(valid), 1)
        print(f"{limiar:>7.2f} {len(alvo):>8} "
              f"{rec:>6}/{base_err:<4} ({100.0*rec/max(base_err,1):>4.1f}%) "
              f"{fal:>6}/{base_ok:<4} ({100.0*fal/max(base_ok,1):>5.2f}%) "
              f"{acur_velha:>6.1f}% → {acur_nova:>5.1f}%")

    print(f"\nBaseline da validação: {acur_velha:.1f}% "
          f"({base_ok} agendados / {base_err} ignorados)")

    # ── 4) a célula mais promissora, aberta por motivo ─────────────────────
    print("\n" + "=" * 72)
    print("CÉLULA 'parte_adversa' — a candidata mais limpa, aberta por motivo real")
    print("=" * 72)
    por_motivo: dict[str, list[str]] = defaultdict(list)
    for d in dados:
        if d["quem"] == "parte_adversa":
            por_motivo[d["motivo"] if d["real"] == "IGNORADO" else "AGENDOU (erro nosso)"].append(d["id"])
    for m, ids in sorted(por_motivo.items(), key=lambda kv: -len(kv[1])):
        print(f"  {m:<30} {len(ids):>4}")


if __name__ == "__main__":
    main()
