"""Relatório de Entradas e Tratamento por Escritório (Publicações).

Pedido do operador (14/09/2026): um PDF executivo e conciso com o volume de
entrada de publicações por escritório responsável e o volume tratado — só
números, sem narrativa. Reusa o renderizador do Relatório Crítico de
Performance (Chromium do Playwright que já vive na imagem da API).

Definições — as mesmas do Dashboard de Publicações, para os números baterem:

- escritório responsável: o ramo do path ("Banco do Brasil / Réu"), mesmo recorte
  do card "Entradas por dia"; publicação sem escritório vira "Sem escritório";
- entrada: publicação não duplicada, no dia da CAPTURA (created_at em Brasília)
  ou da PUBLICAÇÃO (publication_date), conforme a base escolhida;
- tratada: ação humana dentro do período — agendamento (scheduled_at) ou ciência
  (ignored_at), sem duplicadas; pode ser de publicação que entrou antes;
- pendente agora: NOVO, CLASSIFICADO ou ERRO no momento da geração (o backlog
  da triagem).
"""

from __future__ import annotations

import html
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional

# Brasil sem horário de verão desde 2019 — offset fixo evita depender de tzdata.
_BRT = timezone(timedelta(hours=-3))
SEM_ESCRITORIO = "Sem escritório"
BASES = ("captura", "publicacao")
MAX_DIAS = 370


def rotulo_escritorio(path: Optional[str]) -> str:
    """"MDR Advocacia / Área operacional / Banco do Brasil / Réu" → "Banco do Brasil / Réu".

    Mesma regra do SQL do card "Entradas por dia" (3º e 4º níveis do path).
    """
    partes = (path or "").split(" / ")
    ramo = partes[2].strip() if len(partes) > 2 else ""
    posicao = partes[3].strip() if len(partes) > 3 else ""
    rotulo = ramo + (f" / {posicao}" if posicao else "")
    return rotulo or SEM_ESCRITORIO


def _utc(ts: Optional[datetime]) -> Optional[datetime]:
    """SQLite devolve naive (UTC); Postgres, aware."""
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _pct(parte: int, todo: int) -> Optional[int]:
    return round(100 * parte / todo) if todo else None


def compute_entradas_escritorio(
    db,
    date_from: date,
    date_to: date,
    base: str = "captura",
    *,
    agora: Optional[datetime] = None,
) -> dict[str, Any]:
    from sqlalchemy import func

    from app.models.legal_one import LegalOneOffice
    from app.models.publication_search import (
        RECORD_STATUS_CLASSIFIED,
        RECORD_STATUS_ERROR,
        RECORD_STATUS_NEW,
    )
    from app.models.publication_search import PublicationRecord as PR

    if base not in BASES:
        raise ValueError(f"Base inválida: {base!r} (use captura ou publicacao).")
    if date_to < date_from:
        raise ValueError("A data final deve ser igual ou posterior à inicial.")
    if (date_to - date_from).days + 1 > MAX_DIAS:
        raise ValueError(f"O período máximo é de {MAX_DIAS} dias.")

    agora = _utc(agora) or datetime.now(timezone.utc)
    ini_utc = datetime.combine(date_from, time.min, tzinfo=_BRT).astimezone(timezone.utc)
    fim_utc = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=_BRT).astimezone(timezone.utc)
    dias = [date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)]

    nomes = {ext: rotulo_escritorio(path) for ext, path in db.query(LegalOneOffice.external_id, LegalOneOffice.path)}

    def escritorio(office_id) -> str:
        return nomes.get(office_id, SEM_ESCRITORIO) if office_id is not None else SEM_ESCRITORIO

    nao_dup = PR.is_duplicate.is_(False)
    linhas: dict[str, dict[str, Any]] = {}

    def linha(nome: str) -> dict[str, Any]:
        return linhas.setdefault(nome, {
            "escritorio": nome, "entradas": 0, "_por_dia": Counter(),
            "agendadas": 0, "ciencias": 0, "pendentes": 0, "_mais_antiga": None,
        })

    entradas_dia: Counter = Counter()
    tratadas_dia: Counter = Counter()

    # ── entradas ──
    q = db.query(PR.linked_office_id, PR.created_at, PR.publication_date).filter(nao_dup)
    if base == "captura":
        q = q.filter(PR.created_at >= ini_utc, PR.created_at < fim_utc)
    else:
        # publication_date é string ISO herdada do L1: comparação de texto basta.
        q = q.filter(
            PR.publication_date >= date_from.isoformat(),
            PR.publication_date < (date_to + timedelta(days=1)).isoformat(),
        )
    for office_id, criada, publicada in q:
        if base == "captura":
            dia = _utc(criada).astimezone(_BRT).date()
        else:
            try:
                dia = date.fromisoformat(str(publicada)[:10])
            except ValueError:
                continue
        item = linha(escritorio(office_id))
        item["entradas"] += 1
        item["_por_dia"][dia] += 1
        entradas_dia[dia] += 1

    # ── tratadas (ação humana no período) ──
    for coluna, chave in ((PR.scheduled_at, "agendadas"), (PR.ignored_at, "ciencias")):
        for office_id, quando in (
            db.query(PR.linked_office_id, coluna)
            .filter(nao_dup, coluna >= ini_utc, coluna < fim_utc)
        ):
            linha(escritorio(office_id))[chave] += 1
            tratadas_dia[_utc(quando).astimezone(_BRT).date()] += 1

    # ── pendentes agora ──
    for office_id, n, mais_antiga in (
        db.query(PR.linked_office_id, func.count(PR.id), func.min(PR.created_at))
        .filter(nao_dup, PR.status.in_((RECORD_STATUS_NEW, RECORD_STATUS_CLASSIFIED, RECORD_STATUS_ERROR)))
        .group_by(PR.linked_office_id)
    ):
        item = linha(escritorio(office_id))
        item["pendentes"] += int(n)
        mais_antiga = _utc(mais_antiga)
        if mais_antiga is not None and (item["_mais_antiga"] is None or mais_antiga < item["_mais_antiga"]):
            item["_mais_antiga"] = mais_antiga

    total_entradas = sum(x["entradas"] for x in linhas.values())
    n_dias = len(dias)
    escritorios = []
    for item in linhas.values():
        pico_dia, pico_n = (max(item["_por_dia"].items(), key=lambda kv: (kv[1], kv[0])) if item["_por_dia"] else (None, 0))
        tratadas = item["agendadas"] + item["ciencias"]
        escritorios.append({
            "escritorio": item["escritorio"],
            "entradas": item["entradas"],
            "pct_entradas": _pct(item["entradas"], total_entradas),
            "media_dia": round(item["entradas"] / n_dias, 1),
            "pico_dia": pico_dia.isoformat() if pico_dia else None,
            "pico_n": pico_n,
            "tratadas": tratadas,
            "agendadas": item["agendadas"],
            "ciencias": item["ciencias"],
            "taxa_tratamento": _pct(tratadas, item["entradas"]),
            "pendentes": item["pendentes"],
            "pendente_mais_antiga_dias": (agora - item["_mais_antiga"]).days if item["_mais_antiga"] else None,
        })
    escritorios.sort(key=lambda x: (-x["entradas"], -x["tratadas"], -x["pendentes"], x["escritorio"]))

    agendadas = sum(x["agendadas"] for x in escritorios)
    ciencias = sum(x["ciencias"] for x in escritorios)
    pendentes = sum(x["pendentes"] for x in escritorios)
    idades = [x["pendente_mais_antiga_dias"] for x in escritorios if x["pendente_mais_antiga_dias"] is not None]
    pico_total = max(entradas_dia.items(), key=lambda kv: (kv[1], kv[0])) if entradas_dia else (None, 0)

    return {
        "periodo": {"de": date_from.isoformat(), "ate": date_to.isoformat(), "dias": n_dias, "base": base},
        "gerado_em": agora.isoformat(),
        "totais": {
            "entradas": total_entradas,
            "media_dia": round(total_entradas / n_dias, 1),
            "pico_dia": pico_total[0].isoformat() if pico_total[0] else None,
            "pico_n": pico_total[1],
            "tratadas": agendadas + ciencias,
            "agendadas": agendadas,
            "ciencias": ciencias,
            "taxa_tratamento": _pct(agendadas + ciencias, total_entradas),
            "pendentes": pendentes,
            "pendente_mais_antiga_dias": max(idades) if idades else None,
        },
        "serie": [
            {"dia": d.isoformat(), "entradas": entradas_dia.get(d, 0), "tratadas": tratadas_dia.get(d, 0)}
            for d in dias
        ],
        "escritorios": escritorios,
    }


# ── HTML ───────────────────────────────────────────────────────────────────


def _e(valor) -> str:
    return html.escape("" if valor is None else str(valor))


def _n(valor, sufixo: str = "", vazio: str = "—") -> str:
    if valor is None:
        return vazio
    if isinstance(valor, float) and not valor.is_integer():
        return f"{valor:.1f}".replace(".", ",") + sufixo
    return f"{int(round(valor)):,}".replace(",", ".") + sufixo


def _dec(valor: float) -> str:
    """Média/dia sempre com uma casa: 0,1 · 661,9 · 1.234,5."""
    return f"{valor:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _data_br(iso: Optional[str], com_ano: bool = True) -> str:
    if not iso:
        return "—"
    d = date.fromisoformat(iso[:10])
    return d.strftime("%d/%m/%Y" if com_ano else "%d/%m")


_CSS = """
<style>
  :root{--ink:#16181d;--muted:#5b6170;--hint:#8a8f9c;--line:#dfe2e8;--bg:#f4f6f9;--blue:#2f7fd8;--blue-l:#b9d6f5;--navy:#0f2f57;--teal:#138a6a;--amber:#b7791f;}
  *{box-sizing:border-box;} html,body{margin:0;padding:0;}
  body{font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--ink);font-size:11px;line-height:1.45;-webkit-print-color-adjust:exact;print-color-adjust:exact;}
  @page{size:A4;margin:0;}
  .page{width:210mm;min-height:297mm;padding:15mm 14mm 16mm;margin:0 auto;position:relative;}
  .head{display:flex;justify-content:space-between;align-items:flex-end;border-bottom:3px solid var(--navy);padding-bottom:9px;}
  .tag{font-size:9px;font-weight:600;letter-spacing:.5px;text-transform:uppercase;color:var(--muted);}
  h1{font-size:21px;font-weight:650;margin:2px 0 0;letter-spacing:-.3px;color:var(--navy);}
  .sub{font-size:12px;color:var(--muted);}
  .logo{font-size:15px;font-weight:700;text-align:right;} .logo span{color:var(--blue);}
  .meta{font-size:9.5px;color:var(--hint);text-align:right;}
  .kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:13px 0 12px;}
  .kpi{background:var(--bg);border-radius:8px;padding:10px 11px;border-top:3px solid var(--blue);}
  .kpi.t{border-top-color:var(--teal);} .kpi.p{border-top-color:var(--amber);} .kpi.r{border-top-color:var(--navy);}
  .kpi .l{font-size:9px;font-weight:600;text-transform:uppercase;letter-spacing:.4px;color:var(--muted);}
  .kpi .v{font-size:22px;font-weight:650;line-height:1.1;margin-top:3px;font-variant-numeric:tabular-nums;}
  .kpi .h{font-size:9.5px;color:var(--hint);margin-top:2px;}
  h2{font-size:12px;font-weight:650;text-transform:uppercase;letter-spacing:.4px;color:var(--navy);margin:14px 0 6px;}
  .legend{display:flex;gap:14px;font-size:9.5px;color:var(--muted);margin:0 0 4px;}
  .legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px;}
  .legend i.line{height:3px;border-radius:2px;vertical-align:2px;}
  table{width:100%;border-collapse:collapse;font-size:10.5px;}
  thead th{text-align:left;font-size:8.5px;font-weight:650;text-transform:uppercase;letter-spacing:.3px;color:var(--muted);border-bottom:1.5px solid var(--navy);padding:5px 5px;}
  td{padding:5px 5px;border-bottom:1px solid var(--line);vertical-align:middle;}
  th.n,td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;}
  td.esc{font-weight:600;white-space:nowrap;}
  tbody tr:nth-child(even) td{background:#fafbfc;}
  tr.tot td{font-weight:700;border-top:1.5px solid var(--navy);border-bottom:none;background:#fff;}
  .share{display:flex;align-items:center;gap:6px;justify-content:flex-end;}
  .share .bar{width:40px;height:7px;background:var(--bg);border-radius:4px;overflow:hidden;}
  .share .bar i{display:block;height:100%;background:var(--blue);border-radius:4px;}
  .ok{color:var(--teal);font-weight:650;} .baixo{color:var(--amber);font-weight:650;}
  .dim{color:var(--hint);font-weight:400;}
  .def{margin-top:12px;font-size:8.5px;color:var(--hint);line-height:1.5;}
  .foot{position:absolute;bottom:7mm;left:14mm;right:14mm;display:flex;justify-content:space-between;font-size:8.5px;color:var(--hint);border-top:1px solid var(--line);padding-top:4px;}
</style>
"""


def _grafico(serie: list[dict[str, Any]]) -> str:
    """Barras = entradas; linha = tratadas. SVG inline, sem dependência."""
    largura, altura = 680.0, 150.0
    esq, dir_, topo, base = 34.0, 8.0, 8.0, 20.0
    area_l, area_a = largura - esq - dir_, altura - topo - base
    maximo = max([1] + [p["entradas"] for p in serie] + [p["tratadas"] for p in serie])
    # Escala "redonda" para a grade.
    passo = 10 ** max(0, len(str(maximo)) - 1)
    teto = ((maximo + passo - 1) // passo) * passo if maximo > 10 else max(maximo, 1)
    n = max(1, len(serie))
    faixa = area_l / n
    larg_barra = max(1.5, faixa * 0.62)

    def y(v: float) -> float:
        return topo + area_a - (v / teto) * area_a

    partes = [f'<svg viewBox="0 0 {largura:.0f} {altura:.0f}" width="100%" xmlns="http://www.w3.org/2000/svg">']
    for frac in (0, 0.5, 1):
        gy = y(teto * frac)
        partes.append(f'<line x1="{esq}" x2="{largura - dir_}" y1="{gy:.1f}" y2="{gy:.1f}" stroke="#dfe2e8" stroke-width="1"/>')
        partes.append(
            f'<text x="{esq - 5}" y="{gy + 3:.1f}" font-size="9" fill="#8a8f9c" text-anchor="end">'
            f'{_n(teto * frac)}</text>'
        )
    pontos = []
    # Rótulo a cada k dias contado a partir do último: o dia mais recente sempre
    # aparece e nenhum rótulo encavala no fim do eixo.
    rotulo_a_cada = max(1, -(-n // 15))
    for i, p in enumerate(serie):
        cx = esq + faixa * i + faixa / 2
        if p["entradas"]:
            partes.append(
                f'<rect x="{cx - larg_barra / 2:.1f}" y="{y(p["entradas"]):.1f}" width="{larg_barra:.1f}" '
                f'height="{topo + area_a - y(p["entradas"]):.1f}" rx="1.5" fill="#b9d6f5"/>'
            )
        pontos.append(f"{cx:.1f},{y(p['tratadas']):.1f}")
        if (n - 1 - i) % rotulo_a_cada == 0:
            partes.append(
                f'<text x="{cx:.1f}" y="{altura - 6:.0f}" font-size="8.5" fill="#8a8f9c" text-anchor="middle">'
                f'{_data_br(p["dia"], com_ano=False)}</text>'
            )
    if len(pontos) > 1:
        partes.append(f'<polyline points="{" ".join(pontos)}" fill="none" stroke="#0f2f57" stroke-width="2" stroke-linejoin="round"/>')
    elif pontos:
        cx, cy = pontos[0].split(",")
        partes.append(f'<circle cx="{cx}" cy="{cy}" r="3" fill="#0f2f57"/>')
    partes.append("</svg>")
    return "".join(partes)


def _taxa(valor: Optional[int]) -> str:
    if valor is None:
        return '<span class="dim">—</span>'
    classe = "ok" if valor >= 100 else ("baixo" if valor < 70 else "")
    return f'<span class="{classe}">{valor}%</span>'


def render_entradas_escritorio_html(dados: dict[str, Any]) -> str:
    per, tot = dados["periodo"], dados["totais"]
    periodo_txt = f"{_data_br(per['de'])} a {_data_br(per['ate'])}"
    gerado = _utc(datetime.fromisoformat(dados["gerado_em"])).astimezone(_BRT)
    base_txt = "data de captura" if per["base"] == "captura" else "data de publicação"
    pico_txt = f' · pico {_n(tot["pico_n"])} em {_data_br(tot["pico_dia"], False)}' if tot["pico_n"] else ""
    out = [
        '<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">',
        "<title>Entradas e Tratamento por Escritório — Publicações</title>", _CSS, "</head><body>",
        '<section class="page">',
        '<div class="head"><div><div class="tag">Relatório executivo · Publicações</div>'
        "<h1>Entradas e Tratamento</h1>"
        '<div class="sub">por escritório responsável</div></div>'
        '<div><div class="logo">Duna<span>Flow</span></div>'
        f'<div class="meta">MDR Advocacia · {_e(periodo_txt)}</div>'
        f'<div class="meta">{per["dias"]} dia(s) · entradas por {base_txt}</div></div></div>',
        '<div class="kpis">',
        f'<div class="kpi"><div class="l">Entradas</div><div class="v">{_n(tot["entradas"])}</div>'
        f'<div class="h">{_dec(tot["media_dia"])}/dia{pico_txt}</div></div>',
        f'<div class="kpi t"><div class="l">Tratadas</div><div class="v">{_n(tot["tratadas"])}</div>'
        f'<div class="h">{_n(tot["agendadas"])} agendadas · {_n(tot["ciencias"])} ciências</div></div>',
        f'<div class="kpi r"><div class="l">Tratadas ÷ entradas</div><div class="v">'
        f'{_n(tot["taxa_tratamento"], "%")}</div><div class="h">no período</div></div>',
        f'<div class="kpi p"><div class="l">Pendentes agora</div><div class="v">{_n(tot["pendentes"])}</div>'
        f'<div class="h">mais antiga: {_n(tot["pendente_mais_antiga_dias"], " dia(s)")}</div></div>',
        "</div>",
        "<h2>Entradas × tratadas por dia</h2>",
        '<div class="legend"><span><i style="background:#b9d6f5"></i>Entradas</span>'
        '<span><i class="line" style="background:#0f2f57"></i>Tratadas</span></div>',
        _grafico(dados["serie"]),
        "<h2>Por escritório responsável</h2>",
        "<table><thead><tr><th>Escritório</th><th class='n'>Entradas</th><th class='n'>Participação</th>"
        "<th class='n'>Média/dia</th><th class='n'>Pico</th><th class='n'>Tratadas</th>"
        "<th class='n'>Agend.</th><th class='n'>Ciências</th><th class='n'>Trat. ÷ entr.</th>"
        "<th class='n'>Pendentes</th></tr></thead><tbody>",
    ]
    for x in dados["escritorios"]:
        pct = x["pct_entradas"] or 0
        pct_txt = "&lt;1%" if x["entradas"] and not pct else f"{pct}%"
        pico = f'{_n(x["pico_n"])} <span class="dim">{_data_br(x["pico_dia"], False)}</span>' if x["pico_n"] else "—"
        out.append(
            f"<tr><td class='esc'>{_e(x['escritorio'])}</td>"
            f"<td class='n'>{_n(x['entradas'])}</td>"
            f"<td class='n'><div class='share'><div class='bar'><i style='width:{pct}%'></i></div>{pct_txt}</div></td>"
            f"<td class='n'>{_dec(x['media_dia'])}</td><td class='n'>{pico}</td>"
            f"<td class='n'>{_n(x['tratadas'])}</td><td class='n'>{_n(x['agendadas'])}</td>"
            f"<td class='n'>{_n(x['ciencias'])}</td><td class='n'>{_taxa(x['taxa_tratamento'])}</td>"
            f"<td class='n'>{_n(x['pendentes'])}</td></tr>"
        )
    out.append(
        f"<tr class='tot'><td>Total</td><td class='n'>{_n(tot['entradas'])}</td><td class='n'>100%</td>"
        f"<td class='n'>{_dec(tot['media_dia'])}</td><td class='n'>{_n(tot['pico_n'])}</td>"
        f"<td class='n'>{_n(tot['tratadas'])}</td><td class='n'>{_n(tot['agendadas'])}</td>"
        f"<td class='n'>{_n(tot['ciencias'])}</td><td class='n'>{_taxa(tot['taxa_tratamento'])}</td>"
        f"<td class='n'>{_n(tot['pendentes'])}</td></tr>"
    )
    out.append("</tbody></table>")
    out.append(
        f'<div class="def">Entrada: publicação não duplicada, pela {base_txt}. Tratada: agendada ou com ciência '
        "dentro do período, inclusive de publicações que entraram antes. Pendente: aguardando classificação ou "
        f"tratamento no momento da geração ({gerado.strftime('%d/%m/%Y %H:%M')}, horário de Brasília).</div>"
    )
    out.append(
        '<div class="foot"><span>DunaFlow · Entradas e Tratamento por Escritório — Publicações</span>'
        f"<span>Confidencial — MDR Advocacia</span><span>Gerado em {gerado.strftime('%d/%m/%Y %H:%M')}</span></div>"
    )
    out.append("</section></body></html>")
    return "".join(out)
