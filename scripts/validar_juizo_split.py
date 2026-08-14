# -*- coding: utf-8 -*-
"""
A/B: os exemplos ricos tornam o desmembramento do `juizo` confiável?

POR QUE ESTE EXPERIMENTO
------------------------
No lote de validação de 10/08 (2.781 publicações contra gabarito humano), as
células `juizo` eram 44% do volume com ~35% de pureza — moeda ao ar, e por
isso a regra r6 as excluiu por completo ("juizo NUNCA descarta"). A pub010
desmembrou o enum em `juizo_expediente` (conclusos, mero expediente — ninguém
age) e `juizo_determina` (o juízo mandou alguém fazer) como HIPÓTESE, com
exemplos enxutos. Dentro do antigo balde `juizo` moram 422 erros do
r5_default — o maior alvo restante.

A pergunta deste A/B é UMA: exemplos ricos melhoram a pureza das células do
desmembramento em relação aos exemplos enxutos que já estão em produção?

    Lote A = enum de 5 valores + exemplos ENXUTOS (o que produção usa hoje)
    Lote B = enum de 5 valores + exemplos RICOS (candidato a substituir)

Mesmos registros, mesma ordem, submetidos em sequência — a única variável é o
bloco de exemplos. Medição com a mesma liturgia da r6: células escolhidas na
metade de treino (paridade do record_id), medidas cegas na validação.

CRITÉRIO PRÉ-REGISTRADO (antes de olhar resultado)
--------------------------------------------------
Uma célula nova só vira candidata a regra se, na METADE DE VALIDAÇÃO:
  - pureza de ignorar ≥ 80% (a régua que a célula da r6 estabeleceu), e
  - falso-ignorar adicional ≤ 3% sobre os acertos atuais.
B "vence" A se entregar célula candidata que A não entrega, ou a mesma célula
com pureza materialmente maior (≥5 pontos). Empate → produção fica como está
(peso de prompt sem ganho medido é só peso).

USO
---
    python scripts/validar_juizo_split.py submit A|B
    python scripts/validar_juizo_split.py status <batch_id>
    python scripts/validar_juizo_split.py score  <batch_id> <rotulo>
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter, defaultdict
from typing import Any, Optional

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.classifier.ai_client import AnthropicClassifierClient

MAX_CHARS = 40_000

_BASE = """Você analisa publicações de diários oficiais brasileiros para um escritório de advocacia.

Sua ÚNICA tarefa é responder duas perguntas objetivas sobre a publicação. Você NÃO classifica o assunto dela.

PERGUNTA 1 — Quem deve praticar o ato que a publicação determina ou noticia?
  "nos"               = a parte que representamos precisa fazer algo
  "parte_adversa"     = quem precisa agir é a parte contrária, não nós
  "juizo_expediente"  = conclusos, mero expediente, ato interno do cartório;
                        ninguém precisa agir agora
  "juizo_determina"   = o juízo determinou algo que alguém deve cumprir
                        (serventia, perito, oficial, banco, terceiro)
  "indeterminado"     = o texto não permite dizer

PERGUNTA 2 — A publicação exige providência NOSSA, com prazo?
  true  = precisamos peticionar, recorrer, cumprir determinação, comparecer, manifestar
  false = é informativa (mera ciência, andamento, juntada, expediente que não abre prazo para nós)

COMO DECIDIR DE QUEM É O ATO
Use o POLO DO ESCRITÓRIO RESPONSÁVEL informado no início da mensagem. Ele vem
do cadastro e é CONFIÁVEL — não o deduza do texto:
  POLO ATIVO   = nós somos autor / exequente / credor; a adversa é ré/executada
  POLO PASSIVO = nós somos réu / executado; a adversa é autora/exequente

Portanto: intimação dirigida ao "autor" num escritório de polo PASSIVO é ato
da PARTE ADVERSA. Intimação ao "executado" num escritório de polo ATIVO é ato
da PARTE ADVERSA. Prazo aberto "às partes" ou "a ambas" é ato NOSSO também.

ASSIMETRIA OBRIGATÓRIA
Na dúvida real entre "nos" e qualquer outro valor, responda "indeterminado".
NUNCA chute "parte_adversa" nem "juizo_expediente": essas respostas podem
fazer a publicação ser descartada, e prazo perdido não se recupera.

Responda SOMENTE com o JSON abaixo, sem nenhum texto antes ou depois:

{"quem_pratica_ato": "nos|parte_adversa|juizo_expediente|juizo_determina|indeterminado", "exige_providencia_nossa": true|false, "justificativa": "uma frase curta"}"""

# ── Lote A: exemplos ENXUTOS — réplica do addendum de produção (pub010) ────
EXEMPLOS_A = """

Exemplos (escritório de polo PASSIVO, nós somos o réu):
  - "Intime-se o autor para manifestar em 15 dias"
        -> quem_pratica_ato: "parte_adversa", exige_providencia_nossa: false
  - "Intimem-se as partes da perícia designada"
        -> quem_pratica_ato: "nos", exige_providencia_nossa: true
  - "Cite-se o réu para contestar"
        -> quem_pratica_ato: "nos", exige_providencia_nossa: true
  - "Conclusos para sentença"
        -> quem_pratica_ato: "juizo_expediente", exige_providencia_nossa: false
  - "Junte-se aos autos. Publique-se."
        -> quem_pratica_ato: "juizo_expediente", exige_providencia_nossa: false"""

# ── Lote B: exemplos RICOS — foco na fronteira expediente × determina ──────
# Desenhados a partir dos erros reais do lote de 10/08: o que derruba a
# pureza do `juizo` é (a) expediente que na verdade sinaliza fase que o
# operador acompanha, (b) determinação a terceiro que abre prazo NOSSO por
# tabela, (c) decurso de prazo — que muda de dono conforme de quem ERA o
# prazo. Cada bloco ataca uma dessas fronteiras.
EXEMPLOS_B = """

EXEMPLOS — a fronteira que decide é "expediente" × "determinação" × "de quem é o prazo".

Mero expediente / ninguém age agora (juizo_expediente):
  - "Conclusos para sentença." -> "juizo_expediente", false
  - "Autos conclusos ao relator." -> "juizo_expediente", false
  - "Junte-se aos autos. Publique-se." -> "juizo_expediente", false
  - "Vista ao Ministério Público." -> "juizo_expediente", false
      (o MP age, não nós — e não abre prazo nosso)
  - "Remetam-se os autos ao contador judicial para atualização do débito."
        -> "juizo_expediente", false (auxiliar do juízo age; nós aguardamos)
  - "Suspendo o processo pelo prazo de 90 dias." -> "juizo_expediente", false

O juízo determinou algo que ALGUÉM cumpre (juizo_determina):
  - "Expeça-se mandado de penhora." -> "juizo_determina", false
      (a serventia cumpre; não abre prazo nosso AGORA)
  - "Cumpra-se o acórdão. Oficie-se ao banco depositário." -> "juizo_determina", false
  - "Intime-se o perito para entrega do laudo em 30 dias." -> "juizo_determina", false
      (o prazo é do PERITO, não nosso)
  - "Defiro a pesquisa via SISBAJUD. Aguarde-se o resultado." -> "juizo_determina", false

CUIDADO 1 — determinação que abre prazo NOSSO por tabela é "nos":
  - "Expeça-se alvará em favor do exequente" com POLO ATIVO -> "nos", true
      (o levantamento é ato NOSSO)
  - "Cumpra-se a decisão, intimando-se o réu para pagamento em 15 dias" com
    POLO PASSIVO -> "nos", true (a determinação tem a NÓS como destinatário)

CUIDADO 2 — decurso de prazo muda de dono conforme de quem ERA o prazo:
  - "Certifico o decurso do prazo sem manifestação do autor" com POLO PASSIVO
        -> "parte_adversa", false (o prazo perdido era DELES)
  - "Certifico o decurso do prazo sem manifestação do executado" com POLO
    PASSIVO -> "indeterminado", false (o executado somos NÓS — algo ficou sem
    resposta; não descarte: deixe o operador ver)

CUIDADO 3 — expediente que sinaliza fase de resultado NÃO é descartável às cegas:
  - "Transitado em julgado. Arquivem-se." -> "juizo_expediente", false
  - "Iniciada a fase de cumprimento de sentença. Intimem-se." -> "nos", true
      (o "intimem-se" alcança as partes — nós inclusive)"""


PROMPTS = {"A": _BASE + EXEMPLOS_A, "B": _BASE + EXEMPLOS_B}

SQL_CONJUNTO = """
SELECT s.record_id, s.real,
       coalesce(s.real_motivo, r.ignore_reason, '(sem)') AS motivo,
       coalesce(r.description, '') AS description,
       coalesce(r.notes, '')       AS notes,
       coalesce(r.linked_lawsuit_cnj, 's/ CNJ') AS cnj,
       o.path AS office_path, o.polo_scope AS polo,
       coalesce(r.category, '')    AS category,
       coalesce(r.subcategory, '') AS subcategory
  FROM publicacao_shadow_decisao s
  JOIN publicacao_registros r ON r.id = s.record_id
  JOIN legal_one_offices o ON o.external_id = r.linked_office_id
 WHERE s.regra = 'r5_default' AND s.real IS NOT NULL
   AND o.polo_scope IN ('ativo', 'passivo')
 ORDER BY s.record_id
"""


def _carregar() -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        return [dict(l) for l in db.execute(text(SQL_CONJUNTO)).mappings().all()]
    finally:
        db.close()


def _user_message(linha: dict[str, Any]) -> str:
    texto = (linha["description"] + "\n" + linha["notes"]).strip()
    if len(texto) > MAX_CHARS:
        texto = texto[:MAX_CHARS] + "\n[...texto truncado para esta validação...]"
    classif = linha["category"] + (f" / {linha['subcategory']}" if linha["subcategory"] else "")
    return (
        f"ESCRITÓRIO RESPONSÁVEL: {linha['office_path']}\n"
        f"POLO DO ESCRITÓRIO RESPONSÁVEL: {(linha['polo'] or '').upper()}\n"
        f"Classificação já atribuída: {classif or '(não classificada)'}\n"
        f"Processo: {linha['cnj']}\n\n"
        f"Texto da publicação:\n{texto}"
    )


async def _submit(rotulo: str) -> None:
    prompt = PROMPTS[rotulo]
    linhas = _carregar()
    client = AnthropicClassifierClient()
    reqs = [
        client.build_batch_request(
            custom_id=str(l["record_id"]), system_prompt=prompt,
            user_message=_user_message(l),
        )
        for l in linhas
    ]
    dist = Counter(l["real"] for l in linhas)
    print(f"Lote {rotulo}: {len(linhas)} publicações "
          f"(AGENDADO={dist['AGENDADO']}, IGNORADO={dist['IGNORADO']}) "
          f"| prompt {len(prompt)} chars | modelo {client.model}")
    resp = await client.submit_batch(reqs)
    print(f"BATCH_{rotulo}={resp.get('id')}")


async def _status(batch_id: str) -> None:
    st = await AnthropicClassifierClient().get_batch_status(batch_id)
    print(f"status={st.get('processing_status')} counts={st.get('request_counts')}")


def _extrair_json(texto: str) -> Optional[dict[str, Any]]:
    ini, fim = texto.find("{"), texto.rfind("}")
    if ini < 0 or fim <= ini:
        return None
    try:
        return json.loads(texto[ini:fim + 1])
    except json.JSONDecodeError:
        return None


async def _score(batch_id: str, rotulo: str) -> None:
    client = AnthropicClassifierClient()
    st = await client.get_batch_status(batch_id)
    if st.get("processing_status") != "ended":
        print(f"Batch ainda em {st.get('processing_status')} — {st.get('request_counts')}")
        return
    resultados = await client.get_batch_results(st["results_url"])
    gab = {str(l["record_id"]): l for l in _carregar()}

    dados = []
    sem_parse = 0
    for item in resultados:
        cid = str(item.get("custom_id"))
        res = item.get("result") or {}
        if res.get("type") != "succeeded" or cid not in gab:
            continue
        blocos = ((res.get("message") or {}).get("content") or [])
        d = _extrair_json("".join(b.get("text", "") for b in blocos if b.get("type") == "text"))
        if not d:
            sem_parse += 1
            continue
        dados.append({
            "id": int(cid),
            "quem": str(d.get("quem_pratica_ato", "?")).lower(),
            "exige": bool(d.get("exige_providencia_nossa", True)),
            "real": gab[cid]["real"], "motivo": gab[cid]["motivo"],
        })
    print(f"[Lote {rotulo}] respostas válidas: {len(dados)} | sem parse: {sem_parse}\n")

    cel: dict[tuple[str, bool], list[dict]] = defaultdict(list)
    for d in dados:
        cel[(d["quem"], d["exige"])].append(d)
    print(f"{'quem_pratica_ato':<18} {'exige':<7} {'n':>5} {'ignorou':>8} {'pureza':>8}")
    for (q, e), itens in sorted(cel.items(), key=lambda kv: -len(kv[1])):
        ign = sum(1 for i in itens if i["real"] == "IGNORADO")
        print(f"{q:<18} {str(e):<7} {len(itens):>5} {ign:>8} {100.0*ign/len(itens):>7.1f}%")

    treino = [d for d in dados if d["id"] % 2 == 0]
    valid = [d for d in dados if d["id"] % 2 == 1]
    pureza_t: dict[tuple[str, bool], float] = {}
    for k in cel:
        its = [d for d in treino if (d["quem"], d["exige"]) == k]
        if len(its) >= 20:
            pureza_t[k] = sum(1 for i in its if i["real"] == "IGNORADO") / len(its)

    base_err = sum(1 for d in valid if d["real"] == "IGNORADO")
    base_ok = sum(1 for d in valid if d["real"] == "AGENDADO")
    print(f"\nsplit: treino={len(treino)} validação={len(valid)} "
          f"(baseline {100.0*base_ok/max(len(valid),1):.1f}%)")
    print(f"{'limiar':>7} {'células':>8} {'quais':<46} {'recup.':>14} {'falso-ign.':>12}")
    for limiar in (0.70, 0.75, 0.80, 0.85):
        alvo = {k for k, p in pureza_t.items() if p >= limiar}
        rec = sum(1 for d in valid if d["real"] == "IGNORADO" and (d["quem"], d["exige"]) in alvo)
        fal = sum(1 for d in valid if d["real"] == "AGENDADO" and (d["quem"], d["exige"]) in alvo)
        nomes = ",".join(f"{q}/{'F' if not e else 'T'}" for q, e in sorted(alvo)) or "-"
        print(f"{limiar:>7.2f} {len(alvo):>8} {nomes:<46} "
              f"{rec:>5}/{base_err:<4} ({100.0*rec/max(base_err,1):>4.1f}%) "
              f"{fal:>4}/{base_ok:<4} ({100.0*fal/max(base_ok,1):>5.2f}%)")

    with open(f"/tmp/juizo_split_{rotulo}.json", "w", encoding="utf-8") as fh:
        json.dump(dados, fh, ensure_ascii=False)
    print(f"\nRespostas cruas em /tmp/juizo_split_{rotulo}.json")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "submit" and len(sys.argv) >= 3 and sys.argv[2] in PROMPTS:
        asyncio.run(_submit(sys.argv[2]))
    elif cmd == "status" and len(sys.argv) >= 3:
        asyncio.run(_status(sys.argv[2]))
    elif cmd == "score" and len(sys.argv) >= 4:
        asyncio.run(_score(sys.argv[2], sys.argv[3]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
