#!/usr/bin/env python3
"""RPA de Análise de Risco BB Réu — roda no servidor AWS (fora do Coolify).

Ciclo: busca a fila no Flow (tarefas cumpridas no L1 aguardando conferência),
autentica no portal BB via OneLog, consulta a pendência de análise de cada NPJ
e devolve os vereditos pro Flow. Pendência aberta = análise NÃO feita
(divergente). Endpoints do portal decodificados do HAR de 2026-08-18.

Uso:
  python3 rpa_analise_risco.py           # loop contínuo (systemd)
  python3 rpa_analise_risco.py --once    # um ciclo e sai (cron)

Env obrigatórias:
  FLOW_API_URL                  ex.: https://flow.dunatecnologia.com
  ANALISE_RISCO_INTAKE_API_KEY  mesma chave setada no Coolify do Flow
  ONELOG_API_URL                ex.: http://127.0.0.1:8788
  ONELOG_USERNAME / ONELOG_PASSWORD

Env opcionais:
  PAJ_BASE            default https://juridico.bb.com.br/paj
  LOTE                default 50 (máx. 200)
  INTERVALO_SEGUNDOS  default 600 (só no modo loop)
  HTTPS_PROXY         se o IP do servidor levar 403 do portal (proxy BR)
"""

import logging
import os
import re
import sys
import time

import requests

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("rpa-analise-risco")

FLOW = (os.environ.get("FLOW_API_URL") or "").rstrip("/")
API_KEY = os.environ.get("ANALISE_RISCO_INTAKE_API_KEY") or ""
ONELOG = (os.environ.get("ONELOG_API_URL") or "").rstrip("/")
ONELOG_USER = os.environ.get("ONELOG_USERNAME") or ""
ONELOG_PASS = os.environ.get("ONELOG_PASSWORD") or ""
PAJ = (os.environ.get("PAJ_BASE") or "https://juridico.bb.com.br/paj").rstrip("/")
LOTE = min(int(os.environ.get("LOTE") or 50), 200)
INTERVALO = int(os.environ.get("INTERVALO_SEGUNDOS") or 600)
UA = os.environ.get(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36",
)

# Cadência de polling do OneLog (contrato: poll_after_seconds > Retry-After > 5s).
POLL_MIN, POLL_MAX, POLL_TIMEOUT = 5, 60, 15 * 60


def _digitos(v):
    return re.sub(r"\D", "", v or "")


def npj_sem_mascara(valor):
    """"2024/0116713-000" -> "20240116713" (11 dígitos; variação descartada)."""
    digs = _digitos(valor)
    if len(digs) == 14:
        digs = digs[:11]
    return digs if len(digs) == 11 else None


# ── Flow (intake) ──────────────────────────────────────────────────────
def flow_fila():
    r = requests.get(
        f"{FLOW}/api/v1/analise-risco/intake/fila",
        params={"limit": LOTE},
        headers={"X-AnaliseRisco-Api-Key": API_KEY},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def flow_enviar(resultados):
    r = requests.post(
        f"{FLOW}/api/v1/analise-risco/intake/resultados",
        json={"resultados": resultados},
        headers={"X-AnaliseRisco-Api-Key": API_KEY},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


# ── OneLog (sessão autenticada do portal BB) ───────────────────────────
def onelog_sessao():
    """{'cookies': [...], 'user_agent': '...'} — login com polling (contrato)."""
    r = requests.post(
        f"{ONELOG}/api/zerocore/login",
        json={"username": ONELOG_USER, "password": ONELOG_PASS, "user_agent": UA},
        timeout=60,
    )
    r.raise_for_status()
    corpo = r.json()
    if corpo.get("status") == "sucesso":
        log.info("OneLog: sessão em cache.")
        return {"cookies": corpo.get("cookies", []), "user_agent": corpo.get("user_agent", UA)}

    setor = corpo.get("setor")
    if not setor:
        raise RuntimeError("OneLog enfileirou o login mas não devolveu o setor.")
    log.info("OneLog: login enfileirado (setor=%s), aguardando…", setor)

    inicio, espera = time.time(), POLL_MIN
    while time.time() - inicio < POLL_TIMEOUT:
        time.sleep(espera)
        s = requests.get(f"{ONELOG}/api/zerocore/status", params={"setor": setor}, timeout=60)
        st = s.json() if s.text.strip() else {}
        bruto = st.get("poll_after_seconds") or s.headers.get("Retry-After")
        try:
            espera = max(POLL_MIN, min(POLL_MAX, int(float(bruto))))
        except (TypeError, ValueError):
            espera = POLL_MIN
        if st.get("erro"):
            raise RuntimeError(f"OneLog falhou: {st.get('mensagem') or st}")
        if st.get("concluido"):
            fim = requests.post(
                f"{ONELOG}/api/zerocore/session",
                json={"username": ONELOG_USER, "password": ONELOG_PASS, "setor": setor},
                timeout=60,
            )
            corpo = fim.json()
            if corpo.get("status") != "sucesso":
                raise RuntimeError("OneLog concluiu mas não liberou a sessão final.")
            return {"cookies": corpo.get("cookies", []), "user_agent": corpo.get("user_agent", UA)}
    raise RuntimeError(f"OneLog: timeout de {POLL_TIMEOUT}s aguardando o login.")


def montar_sessao(sessao):
    sess = requests.Session()
    for c in sessao.get("cookies", []):
        if c.get("name"):
            sess.cookies.set(
                c["name"], c.get("value"),
                domain=c.get("domain") or "juridico.bb.com.br",
                path=c.get("path") or "/",
            )
    sess.headers.update({
        "User-Agent": sessao.get("user_agent") or UA,
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    })
    return sess


# ── Portal BB (seção Análise de Risco) ─────────────────────────────────
def consultar_pendencia(sess, numero):
    """POST pendencia/consultar (body = NPJ cru). data null = sem pendência."""
    r = sess.post(
        f"{PAJ}/resources/app/v1/processo/analise/risco/pendencia/consultar",
        data=str(int(numero)),
        headers={"Content-Type": "application/json;charset=UTF-8"},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"pendencia/consultar HTTP {r.status_code}: {r.text[:200]}")
    data = r.json().get("data")
    itens = []
    if isinstance(data, list):
        itens = [x for x in data if x]
    elif isinstance(data, dict) and data:
        itens = [data]
    if not itens:
        return {"pendencia_aberta": False, "estado": None, "exito": None}

    def _desc(no, *chaves):
        for ch in chaves:
            v = (no or {}).get(ch)
            if isinstance(v, dict) and (v.get("descricao") or v.get("nome")):
                return str(v.get("descricao") or v.get("nome"))
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None

    p = itens[0]
    return {
        "pendencia_aberta": True,
        "estado": _desc(p, "estado", "estadoAnalise", "situacao"),
        "exito": _desc(p, "possibilidadeExitoAutor", "possibilidadeExito"),
    }


def resolver_npj_por_cnj(sess, cnj):
    digs = _digitos(cnj)
    if not digs:
        return None
    r = sess.get(
        f"{PAJ}/resources/app/portal/cadastro/processo/pesquisa-avancada/numero-processo/{digs}",
        params={"numeroPosicaoLista": 1},
        timeout=30,
    )
    if r.status_code != 200 or not r.text.strip():
        return None
    procs = ((r.json().get("data") or {}).get("processos")) or []
    return str(procs[0]["numeroProcesso"]) if procs and procs[0].get("numeroProcesso") else None


# ── Ciclo ──────────────────────────────────────────────────────────────
def ciclo():
    fila = flow_fila()
    itens = fila.get("itens", [])
    log.info("Fila do Flow: %s itens (total %s).", len(itens), fila.get("total"))
    if not itens:
        return

    sess = montar_sessao(onelog_sessao())
    resultados = []
    for item in itens:
        rid = item["id"]
        try:
            numero = npj_sem_mascara(item.get("npj"))
            npj_resolvido = None
            if not numero and item.get("cnj"):
                numero = resolver_npj_por_cnj(sess, item["cnj"])
                if numero:
                    npj_resolvido = f"{numero[:4]}/{numero[4:]}-000"
            if not numero:
                raise RuntimeError(
                    f"sem NPJ utilizável (npj={item.get('npj')!r}, cnj={item.get('cnj')!r})"
                )
            veredito = consultar_pendencia(sess, numero)
            resultados.append({"id": rid, "npj": npj_resolvido, **veredito})
        except Exception as e:  # noqa: BLE001 — item falho volta pra fila
            log.warning("item %s falhou: %s", rid, e)
            resultados.append({"id": rid, "erro": str(e)[:500]})

    resp = flow_enviar(resultados)
    log.info("Enviado ao Flow: %s", resp)


def main():
    faltando = [n for n, v in (
        ("FLOW_API_URL", FLOW), ("ANALISE_RISCO_INTAKE_API_KEY", API_KEY),
        ("ONELOG_API_URL", ONELOG), ("ONELOG_USERNAME", ONELOG_USER),
        ("ONELOG_PASSWORD", ONELOG_PASS),
    ) if not v]
    if faltando:
        log.error("Env obrigatórias faltando: %s", ", ".join(faltando))
        sys.exit(2)

    once = "--once" in sys.argv
    while True:
        try:
            ciclo()
        except Exception:
            log.exception("Ciclo falhou — %s.", "saindo" if once else "tenta de novo no próximo intervalo")
            if once:
                sys.exit(1)
        if once:
            return
        time.sleep(INTERVALO)


if __name__ == "__main__":
    main()
