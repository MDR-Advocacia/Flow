# -*- coding: utf-8 -*-
"""
Valida a hipótese que saiu da medição do shadow em 10/08/2026.

MEDIÇÃO QUE MOTIVOU ISTO
------------------------
A regra `r5_default` (nenhuma regra dispara → agenda por omissão) responde por
80% do volume do shadow, com 68,6% de acerto. Os 869 erros dela, quebrados pelo
motivo que o operador DECLAROU ao ignorar (motivo é obrigatório, então o dado é
confiável):

    parte_adversa   388  (45%)  ─┐ compreensão de texto
    informativa     315  (36%)  ─┘  = 83% dos erros
    ja_agendado     147  (17%)     consulta ao L1
    outros           19

Ou seja: o gargalo NÃO é falta de contexto que exija investigação. É que
ninguém pergunta ao modelo QUEM tem que praticar o ato. Ele classifica o
assunto e para aí.

A prova pelo avesso está na própria política: a regra `r2_parte_adversa` acerta
96,3% — mas disparou 27 vezes enquanto 388 casos de parte adversa vazaram pro
default. O problema dela nunca foi precisão, foi ALCANCE.

O QUE ESTE SCRIPT MEDE
----------------------
Se acrescentar duas perguntas à chamada de classificação que já pagamos
recupera esses erros SEM criar erro novo na direção perigosa.

    1) quem_pratica_ato          — nós, a parte adversa, o juízo?
    2) exige_providencia_nossa   — isso pede ação nossa ou é informativo?

Predição derivada: IGNORAR se o ato não é nosso OU não exige providência.

MÉTRICA QUE DECIDE
------------------
Não é a acurácia global. São duas taxas assimétricas:

  - RECUPERAÇÃO: dos 869 que erramos, quantos passam a acertar.
    Teto estrutural = 83% (os 147 `ja_agendado` são invisíveis ao texto —
    o modelo não tem como saber o que já está agendado na pasta).
  - FALSO-IGNORAR: dos 1.906 que o humano AGENDOU e nós acertamos, quantos
    a mudança passaria a ignorar. Esta é a direção que perde prazo, e é a
    única que pode reprovar a mudança sozinha.

Sem gabarito não haveria teste: ele existe porque o shadow gravou a decisão
real do humano em `publicacao_shadow_decisao.real`.

USO
---
    python scripts/validar_quem_pratica_ato.py submit
    python scripts/validar_quem_pratica_ato.py status <batch_id>
    python scripts/validar_quem_pratica_ato.py score  <batch_id>
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from typing import Any, Optional

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.classifier.ai_client import AnthropicClassifierClient

# Teto por publicação. Produção não trunca, mas a cauda tem publicação de
# 384k chars (~107k tokens) que só queima token — o ato a decidir está no
# começo. Registramos quantas foram cortadas pra ninguém confundir corte
# com erro do modelo.
MAX_CHARS = 40_000

ARQ_LOTE = "/tmp/validacao_quem_pratica_lote.json"


SYSTEM_PROMPT = """Você analisa publicações de diários oficiais brasileiros para um escritório de advocacia.

Sua ÚNICA tarefa é responder duas perguntas objetivas sobre a publicação. Você NÃO classifica o assunto dela.

PERGUNTA 1 — Quem deve praticar o ato que a publicação determina ou noticia?
  "nos"            = o escritório responsável (a parte que representamos) precisa fazer algo
  "parte_adversa"  = quem precisa fazer algo é a parte contrária, não nós
  "juizo"          = o ato cabe ao juízo, à serventia ou a auxiliar (perito, oficial, contador)
  "indeterminado"  = o texto não permite dizer

PERGUNTA 2 — A publicação exige providência NOSSA, com prazo?
  true  = precisamos peticionar, recorrer, cumprir determinação, comparecer, manifestar
  false = é informativa (mera ciência, andamento, juntada, publicação de expediente
          que não abre prazo para nós)

COMO DECIDIR DE QUEM É O ATO
Você recebe o POLO do escritório responsável, que é informação de cadastro e é
CONFIÁVEL — não a deduza do texto:
  POLO ATIVO   = nós somos autor / exequente / credor. A parte adversa é ré/executada.
  POLO PASSIVO = nós somos réu / executado. A parte adversa é autora/exequente.

Portanto: intimação dirigida ao "autor" com polo PASSIVO é ato da PARTE ADVERSA.
Intimação dirigida ao "executado" com polo ATIVO é ato da PARTE ADVERSA.
Prazo aberto para "as partes" ou para "ambas" é ato NOSSO também.

Na dúvida real entre "nos" e "parte_adversa", responda "indeterminado" e marque
confianca "baixa" — nunca chute "parte_adversa", porque essa resposta faz a
publicação ser descartada e um prazo pode ser perdido.

Responda SOMENTE com o JSON abaixo, sem nenhum texto antes ou depois:

{"quem_pratica_ato": "nos|parte_adversa|juizo|indeterminado", "exige_providencia_nossa": true|false, "confianca": "alta|media|baixa", "justificativa": "uma frase curta"}"""


SQL_CONJUNTO = """
SELECT s.record_id,
       s.real,
       coalesce(r.description, '')            AS description,
       coalesce(r.notes, '')                  AS notes,
       coalesce(r.linked_lawsuit_cnj, 's/ CNJ') AS cnj,
       o.path                                 AS office_path,
       o.polo_scope                           AS polo,
       coalesce(r.category, '')               AS category,
       coalesce(r.subcategory, '')            AS subcategory
  FROM publicacao_shadow_decisao s
  JOIN publicacao_registros r ON r.id = s.record_id
  JOIN legal_one_offices o ON o.external_id = r.linked_office_id
 WHERE s.regra = 'r5_default'
   AND s.real IS NOT NULL
   AND o.polo_scope IN ('ativo', 'passivo')
 ORDER BY s.record_id
"""


def _carregar_conjunto() -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        linhas = db.execute(text(SQL_CONJUNTO)).mappings().all()
        return [dict(l) for l in linhas]
    finally:
        db.close()


def _montar_user_message(linha: dict[str, Any]) -> tuple[str, bool]:
    """Retorna (mensagem, foi_truncada)."""
    texto = (linha["description"] + "\n" + linha["notes"]).strip()
    truncou = len(texto) > MAX_CHARS
    if truncou:
        texto = texto[:MAX_CHARS] + "\n[...texto truncado para esta validação...]"

    polo = (linha["polo"] or "").upper()
    classif = linha["category"]
    if linha["subcategory"]:
        classif += f" / {linha['subcategory']}"

    return (
        f"""ESCRITÓRIO RESPONSÁVEL: {linha['office_path']}
POLO DO ESCRITÓRIO RESPONSÁVEL: {polo}
Classificação já atribuída: {classif or '(não classificada)'}
Processo: {linha['cnj']}

Texto da publicação:
{texto}""",
        truncou,
    )


async def _submit() -> None:
    linhas = _carregar_conjunto()
    if not linhas:
        print("Conjunto vazio — nada a validar.")
        return

    client = AnthropicClassifierClient()
    requests: list[dict[str, Any]] = []
    truncadas = 0
    for linha in linhas:
        msg, truncou = _montar_user_message(linha)
        truncadas += int(truncou)
        requests.append(
            client.build_batch_request(
                custom_id=str(linha["record_id"]),
                system_prompt=SYSTEM_PROMPT,
                user_message=msg,
            )
        )

    dist = Counter(l["real"] for l in linhas)
    print(f"Conjunto: {len(linhas)} publicações "
          f"(AGENDADO={dist['AGENDADO']}, IGNORADO={dist['IGNORADO']})")
    print(f"Truncadas em {MAX_CHARS} chars: {truncadas}")
    print(f"Modelo: {client.model}")

    resp = await client.submit_batch(requests)
    batch_id = resp.get("id")
    print(f"\nBATCH_ID={batch_id}")

    with open(ARQ_LOTE, "w", encoding="utf-8") as fh:
        json.dump({"batch_id": batch_id, "total": len(linhas)}, fh)


async def _status(batch_id: str) -> None:
    client = AnthropicClassifierClient()
    st = await client.get_batch_status(batch_id)
    print(f"status={st.get('processing_status')} counts={st.get('request_counts')}")
    if st.get("results_url"):
        print(f"results_url={st['results_url']}")


def _extrair_json(texto: str) -> Optional[dict[str, Any]]:
    """O modelo às vezes embrulha o JSON — pega do primeiro { ao último }."""
    ini, fim = texto.find("{"), texto.rfind("}")
    if ini < 0 or fim <= ini:
        return None
    try:
        return json.loads(texto[ini:fim + 1])
    except json.JSONDecodeError:
        return None


async def _score(batch_id: str) -> None:
    client = AnthropicClassifierClient()
    st = await client.get_batch_status(batch_id)
    if st.get("processing_status") != "ended":
        print(f"Batch ainda em {st.get('processing_status')} — {st.get('request_counts')}")
        return

    resultados = await client.get_batch_results(st["results_url"])
    gabarito = {str(l["record_id"]): l for l in _carregar_conjunto()}

    # previsto_novo por record_id
    previsto: dict[str, str] = {}
    respostas: dict[str, dict[str, Any]] = {}
    sem_parse = 0
    for item in resultados:
        cid = str(item.get("custom_id"))
        res = (item.get("result") or {})
        if res.get("type") != "succeeded":
            continue
        blocos = ((res.get("message") or {}).get("content") or [])
        texto = "".join(b.get("text", "") for b in blocos if b.get("type") == "text")
        dados = _extrair_json(texto)
        if not dados:
            sem_parse += 1
            continue
        respostas[cid] = dados
        quem = str(dados.get("quem_pratica_ato", "")).lower()
        exige = bool(dados.get("exige_providencia_nossa", True))
        # Assimetria deliberada: só ignora com afirmação POSITIVA de que o ato
        # não é nosso. "indeterminado" agenda — errar agendando custa uma
        # tarefa a mais; errar ignorando custa um prazo.
        previsto[cid] = "IGNORADO" if (quem in ("parte_adversa", "juizo") or not exige) else "AGENDADO"

    print(f"Respostas válidas: {len(previsto)} | sem parse: {sem_parse}\n")

    # ── as duas taxas que decidem ──────────────────────────────────────────
    recuperados = perdidos = 0
    base_err = base_ok = 0
    motivo_recuperado: Counter = Counter()
    motivo_perdido: Counter = Counter()

    db = SessionLocal()
    try:
        motivos = dict(db.execute(text(
            "SELECT s.record_id, coalesce(s.real_motivo, r.ignore_reason, '(sem)') "
            "  FROM publicacao_shadow_decisao s "
            "  JOIN publicacao_registros r ON r.id = s.record_id "
            " WHERE s.regra='r5_default' AND s.real='IGNORADO'"
        )).all())
    finally:
        db.close()

    for cid, linha in gabarito.items():
        novo = previsto.get(cid)
        if novo is None:
            continue
        real = linha["real"]
        # A política velha (r5_default) prevê SEMPRE agendar.
        if real == "IGNORADO":
            base_err += 1
            if novo == "IGNORADO":
                recuperados += 1
                motivo_recuperado[motivos.get(int(cid), "(sem)")] += 1
            else:
                motivo_perdido[motivos.get(int(cid), "(sem)")] += 1
        else:
            base_ok += 1
            if novo == "IGNORADO":
                perdidos += 1

    print("=" * 62)
    print("RECUPERAÇÃO — dos que a política velha ERRAVA")
    print(f"  base de erros ............. {base_err}")
    print(f"  recuperados ............... {recuperados} "
          f"({100.0*recuperados/max(base_err,1):.1f}%)")
    print(f"  teto estrutural ........... 83% (ja_agendado é invisível ao texto)")
    print("  por motivo declarado:")
    for m, n in motivo_recuperado.most_common():
        tot = n + motivo_perdido.get(m, 0)
        print(f"    {m:<26} {n:>4}/{tot:<4} ({100.0*n/max(tot,1):.0f}%)")
    print()
    print("FALSO-IGNORAR — dos que a política velha ACERTAVA (direção perigosa)")
    print(f"  base de acertos ........... {base_ok}")
    print(f"  passariam a ser ignorados . {perdidos} "
          f"({100.0*perdidos/max(base_ok,1):.2f}%)")
    print()
    liquido = recuperados - perdidos
    acerto_velho = base_ok
    acerto_novo = base_ok - perdidos + recuperados
    tot = base_ok + base_err
    print(f"ACURÁCIA no r5_default: {100.0*acerto_velho/max(tot,1):.1f}% "
          f"→ {100.0*acerto_novo/max(tot,1):.1f}%  (líquido {liquido:+d})")
    print("=" * 62)

    # distribuição das respostas, pra enxergar viés do modelo
    print("\nDistribuição de quem_pratica_ato:")
    for k, n in Counter(str(r.get("quem_pratica_ato")) for r in respostas.values()).most_common():
        print(f"  {k:<16} {n}")
    print("Distribuição de confianca:")
    for k, n in Counter(str(r.get("confianca")) for r in respostas.values()).most_common():
        print(f"  {k:<16} {n}")

    with open("/tmp/validacao_quem_pratica_respostas.json", "w", encoding="utf-8") as fh:
        json.dump(respostas, fh, ensure_ascii=False)
    print("\nRespostas cruas em /tmp/validacao_quem_pratica_respostas.json")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "submit":
        asyncio.run(_submit())
    elif cmd in ("status", "score"):
        if len(sys.argv) < 3:
            print(f"uso: {sys.argv[0]} {cmd} <batch_id>")
            return
        asyncio.run(_status(sys.argv[2]) if cmd == "status" else _score(sys.argv[2]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
