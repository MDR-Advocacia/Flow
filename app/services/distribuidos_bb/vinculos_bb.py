"""Pesquisa de VÍNCULOS no portal do BB — processos em comum por parte envolvida.

No ato da distribuição/cadastro, pra cada parte do processo capturado (exceto o
próprio Banco do Brasil) a gente pergunta ao portal: essa pessoa é parte em
OUTRAS ações? Quais estão ativas e conduzidas pelo NOSSO escritório (MDR)?

Fluxo (3 chamadas JSON do PAJ, decodificadas dos HARs reais):
  1) doc → numeroPessoa:
       GET  {base}/resources/app/v2/portal/cadastro/processo/pessoas/
            pesquisa-avancada/{cpf | cnpj-alfanumerico}/{doc}?inicioBusca=0&somente=paj
  2) processos da pessoa:
       POST {base}/resources/app/v1/processo/consulta/consulta-parte-envolvida
            body {tipoEnvolvimento:"P", numeroPessoaParte, ajuizado:"T",
                  estadoNPJ:"T", tipoVariacao:"T", inicioPesquisa:1}
  3) polo (só dos vínculos confirmados):
       GET  {base}/resources/app/v1/processo/consulta/{numeroProcesso}
            → indicadorPoloBanco ('A'=Ativo/Autor lado banco, 'P'=Passivo/Réu)

Regra do vínculo (campos decodificados, sem heurística):
  - ATIVO   = indicadorProcessoAtivo == 'A'  (cancelados vêm 'I')
  - NOSSO   = numeroAdvogadoProcesso == ADVOGADO_MDR  (outros vêm 'Não Cadastrado'/0)

⚠️ POR QUE NÃO É `requests` (2026-08-31)
O código nasceu com `requests.Session` + cookies do OneLog e funcionava. Hoje o
WAF do BB devolve **403 com HTML** ("Ops! Erro no acesso", com ID de segurança e
o IP) pra qualquer cliente que não seja um navegador de verdade. Medido:

    requests (direto ou por proxy BR)           -> 403 até na raiz /paj
    Playwright/Chromium (cookies+headers iguais)-> 200 na raiz, 403 nas SPAs e API
    undetected-chromedriver                     -> 200 em tudo

Não é IP nem permissão do usuário: a MESMA chave (C1330195) e o MESMO servidor
respondem 200 pelo Chrome undetected. É detecção de automação (o chromedriver
comum deixa rastro que o `uc` remove). Por isso as chamadas saem de dentro do
navegador, via `fetch(credentials:'include')` na página da SPA de consulta —
que também resolve o `Referer` que a API espera.

O navegador é caro de abrir (~10s), então é UM por coleta, reusado entre os
processos (`obter_browser` / `fechar_browser`).
"""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Optional

import requests

from app.core.config import settings

logger = logging.getLogger("distribuidos_bb.vinculos")

# Código do advogado do BB que identifica o nosso escritório (MARCOS DELLI
# RIBEIRO) na lista de partes. Editável via config bbd_config `vinculo_advogado_mdr`.
ADVOGADO_MDR_DEFAULT = 8706512
# CNPJ do próprio Banco do Brasil — nunca pesquisar como "parte" (é o cliente).
CNPJ_BB = "00000000000191"

# Situações que NÃO contam como vínculo: a pasta existe no portal mas o processo
# ainda não foi distribuído pra gente (montagem de dossiê = provável recuperação
# de crédito futura). Casa por substring, minúsculo e sem acento.
# Editável via config `vinculo_situacoes_excluidas` (lista separada por vírgula).
SITUACOES_EXCLUIDAS_DEFAULT = "montagem"

_BASE_DEFAULT = "https://juridico.bb.com.br/paj"
# Página da SPA de consulta: as fetches partem dela pra herdar o Referer que a
# API do PAJ espera.
_SPA_CONSULTA = (
    "/app/paj-cadastro/spas/processo/consulta/processo-consulta.app.html"
)


class VinculoAcessoNegado(RuntimeError):
    """O portal recusou a consulta (403/401/HTML de erro) — NÃO é 'sem vínculo'.

    Existe para separar dois estados que o código antigo confundia: "pesquisei e
    a parte não tem outra ação nossa" (resultado legítimo, vazio) de "não
    consegui nem pesquisar" (acesso caiu). Sem essa distinção o motor devolve
    zero em silêncio e a operação lê isso como se fosse resposta do portal.
    """


def apenas_digitos(v: Optional[str]) -> str:
    return re.sub(r"\D", "", v or "")


REFERER_SPA = (
    "https://juridico.bb.com.br/paj/app/paj-cadastro/spas/processo/consulta/"
    "processo-consulta.app.html"
)


def montar_sessao(cookies_onelog: list[dict[str, Any]], user_agent: str) -> "requests.Session":
    """requests.Session com os cookies autenticados do OneLog.

    COMPATIBILIDADE: o modulo de vinculos migrou pro navegador undetected
    (c11e48e) e parou de usar isto — mas o portal_verify_worker da ANALISE DE
    RISCO ainda importa daqui, e a remocao derrubou o job dele com ImportError
    a cada 10 minutos (madrugada de 01/09/2026). Fica ate a analise de risco
    migrar tambem.
    """
    sess = requests.Session()
    for c in cookies_onelog or []:
        nome = c.get("name")
        valor = c.get("value")
        if not nome:
            continue
        sess.cookies.set(
            nome, valor,
            domain=c.get("domain") or "juridico.bb.com.br",
            path=c.get("path") or "/",
        )
    # Headers do HAR real do portal (31/08/2026): sem Referer da SPA (e com
    # X-Requested-With) a borda do BB devolve 403 em HTML.
    b = _base(None)
    partes = b.split("/")
    origem = "//".join([partes[0], partes[2]]) if len(partes) > 2 else b
    sess.headers.update({
        "User-Agent": user_agent or "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": REFERER_SPA,
        "Origin": origem,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    })
    return sess


def _base(base_url: Optional[str] = None) -> str:
    return (base_url or getattr(settings, "distribuidos_bb_paj_base", _BASE_DEFAULT)).rstrip("/")


class VinculosBrowser:
    """Chromium undetected com a sessão do OneLog, parado na SPA de consulta.

    Só sabe fazer uma coisa: `fetch_json`, que executa a chamada DENTRO da
    página (mesma origem, cookies e Referer da SPA). Toda a decodificação do
    PAJ fica nas funções abaixo — isto aqui é transporte.
    """

    def __init__(self, sessao_onelog: dict[str, Any], base_url: Optional[str] = None):
        self.sessao = sessao_onelog or {}
        self.base = _base(base_url)
        self._driver = None

    # ── ciclo de vida ────────────────────────────────────────────────

    def abrir(self) -> None:
        import undetected_chromedriver as uc

        chrome = self._chrome_path()
        opts = uc.ChromeOptions()
        for arg in (
            "--headless=new",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-size=1920,1080",
        ):
            opts.add_argument(arg)
        kwargs: dict[str, Any] = {"options": opts, "use_subprocess": True}
        if chrome:
            kwargs["browser_executable_path"] = chrome
            versao = self._chrome_major(chrome)
            if versao:
                kwargs["version_main"] = versao
        self._driver = uc.Chrome(**kwargs)
        self._driver.set_page_load_timeout(
            settings.distribuidos_bb_vinculos_timeout_seg
        )
        self._injetar_sessao()
        # Aterrissa na SPA de consulta: é daqui que as fetches saem.
        self._driver.get(self.base + _SPA_CONSULTA)
        logger.info("Vínculos: navegador pronto em %s", self._driver.current_url[:80])

    def fechar(self) -> None:
        if self._driver is None:
            return
        try:
            self._driver.quit()
        except Exception:  # noqa: BLE001
            logger.warning("Vínculos: falha ao fechar o navegador (ignorado).", exc_info=True)
        finally:
            self._driver = None

    def vivo(self) -> bool:
        if self._driver is None:
            return False
        try:
            _ = self._driver.current_url
            return True
        except Exception:  # noqa: BLE001
            return False

    # ── infra ────────────────────────────────────────────────────────

    @staticmethod
    def _chrome_path() -> Optional[str]:
        """Reusa o Chromium que o Playwright já traz na imagem (sem download novo)."""
        import glob
        import os
        import shutil

        for env in ("DISTRIBUIDOS_BB_CHROME_PATH", "CHROME_PATH"):
            caminho = os.getenv(env)
            if caminho and os.path.exists(caminho):
                return caminho
        for padrao in (
            "/ms-playwright/chromium-*/chrome-linux64/chrome",
            "/ms-playwright/chromium-*/chrome-linux/chrome",
            "/root/.cache/ms-playwright/chromium-*/chrome-linux64/chrome",
        ):
            achados = sorted(glob.glob(padrao))
            if achados:
                return achados[-1]
        return shutil.which("google-chrome") or shutil.which("chromium")

    @staticmethod
    def _chrome_major(caminho: str) -> Optional[int]:
        import subprocess

        try:
            saida = subprocess.run(
                [caminho, "--version"], capture_output=True, text=True, timeout=20
            ).stdout
            achado = re.search(r"(\d+)\.", saida or "")
            return int(achado.group(1)) if achado else None
        except Exception:  # noqa: BLE001
            return None

    def _injetar_sessao(self) -> None:
        """Cookies do OneLog via CDP (antes de qualquer navegação)."""
        ua = self.sessao.get("user_agent")
        if ua:
            try:
                self._driver.execute_cdp_cmd(
                    "Network.setUserAgentOverride", {"userAgent": ua}
                )
            except Exception:  # noqa: BLE001
                logger.warning("Vínculos: user-agent do OneLog não aplicado.")
        cookies = []
        for c in self.sessao.get("cookies") or []:
            nome = c.get("name")
            if not nome:
                continue
            dominio = c.get("domain") or ".bb.com.br"
            caminho = c.get("path") or "/"
            cookies.append({
                "name": nome,
                "value": str(c.get("value")),
                "domain": dominio,
                "path": caminho,
                "secure": True,
                "url": f"https://{dominio.lstrip('.')}{caminho}",
            })
        if not cookies:
            raise VinculoAcessoNegado("OneLog não devolveu cookies pra injetar no navegador.")
        self._driver.execute_cdp_cmd("Network.enable", {})
        self._driver.execute_cdp_cmd("Network.setCookies", {"cookies": cookies})
        logger.info("Vínculos: %s cookies do OneLog injetados.", len(cookies))

    # ── a única operação ─────────────────────────────────────────────

    _JS = """
    var cb = arguments[arguments.length - 1];
    var opcoes = {
        method: arguments[1],
        credentials: 'include',
        headers: {'Accept': 'application/json, text/plain, */*'}
    };
    if (arguments[2]) {
        opcoes.headers['Content-Type'] = 'application/json;charset=UTF-8';
        opcoes.body = arguments[2];
    }
    fetch(arguments[0], opcoes)
        .then(function (r) {
            return r.text().then(function (t) {
                cb(JSON.stringify({status: r.status, body: t}));
            });
        })
        .catch(function (e) { cb(JSON.stringify({status: 0, body: String(e)})); });
    """

    def fetch_json(self, url: str, method: str = "GET", body: Any = None) -> dict[str, Any]:
        if self._driver is None:
            raise VinculoAcessoNegado("Navegador de vínculos não está aberto.")
        timeout = settings.distribuidos_bb_vinculos_timeout_seg
        try:
            self._driver.set_script_timeout(timeout)
            bruto = self._driver.execute_async_script(
                self._JS, url, method, json.dumps(body) if body else None
            )
        except Exception as exc:  # noqa: BLE001
            raise VinculoAcessoNegado(f"fetch falhou no navegador: {exc}") from exc

        envelope = json.loads(bruto)
        status = envelope.get("status")
        texto = envelope.get("body") or ""
        if status == 0:
            raise VinculoAcessoNegado(f"fetch rejeitado pelo navegador: {texto[:120]}")
        if status in (401, 403) or (status == 200 and texto.lstrip().startswith("<")):
            raise VinculoAcessoNegado(
                f"portal recusou a consulta (HTTP {status}, corpo HTML) — "
                "sessão do BB caiu ou o WAF barrou o cliente."
            )
        if status != 200:
            raise VinculoAcessoNegado(f"portal respondeu HTTP {status}: {texto[:150]}")
        try:
            return json.loads(texto)
        except (json.JSONDecodeError, TypeError) as exc:
            raise VinculoAcessoNegado(f"resposta não é JSON: {texto[:150]}") from exc


# ── navegador compartilhado pela coleta ──────────────────────────────
# Um por coleta, não por processo: abrir custa ~10s. A coleta roda num worker
# só (advisory lock), mas o lock aqui protege contra chamada manual paralela.
_browser: Optional[VinculosBrowser] = None
_browser_lock = threading.Lock()


def obter_browser(sessao_onelog: dict[str, Any]) -> VinculosBrowser:
    """Devolve o navegador da coleta, abrindo (ou reabrindo se morreu)."""
    global _browser
    with _browser_lock:
        if _browser is not None and not _browser.vivo():
            logger.info("Vínculos: navegador anterior morreu — reabrindo.")
            _browser.fechar()
            _browser = None
        if _browser is None:
            novo = VinculosBrowser(sessao_onelog)
            novo.abrir()
            _browser = novo
        return _browser


def fechar_browser() -> None:
    """Fecha o navegador da coleta (chamado no fim de `executar_coleta`)."""
    global _browser
    with _browser_lock:
        if _browser is not None:
            _browser.fechar()
            _browser = None


# ── as 3 chamadas do PAJ ─────────────────────────────────────────────


def _resolver_numero_pessoa(browser: VinculosBrowser, doc: str) -> Optional[int]:
    """Passo 1: documento (CPF/CNPJ) → numeroPessoa do cadastro do BB."""
    digs = apenas_digitos(doc)
    if len(digs) == 14:
        rota = f"cnpj-alfanumerico/{digs}"
    elif len(digs) == 11:
        rota = f"cpf/{digs}"
    else:
        return None
    url = (
        f"{browser.base}/resources/app/v2/portal/cadastro/processo/pessoas/"
        f"pesquisa-avancada/{rota}?inicioBusca=0&somente=paj"
    )
    lista = (browser.fetch_json(url).get("data") or {}).get("listaOcorrencia") or []
    if not lista:
        return None
    return lista[0].get("numeroPessoa")


def _listar_processos_da_parte(browser: VinculosBrowser, numero_pessoa: int) -> list[dict[str, Any]]:
    """Passo 2: todos os processos em que a pessoa é parte envolvida."""
    url = f"{browser.base}/resources/app/v1/processo/consulta/consulta-parte-envolvida"
    body = {
        "tipoEnvolvimento": "P",
        "numeroPessoaParte": numero_pessoa,
        "ajuizado": "T",
        "estadoNPJ": "T",
        "tipoVariacao": "T",
        "inicioPesquisa": 1,
    }
    dados = browser.fetch_json(url, method="POST", body=body)
    return (dados.get("data") or {}).get("listaOcorrencia") or []


def _consultar_polo(browser: VinculosBrowser, numero_processo: Any) -> Optional[str]:
    """Passo 3: polo do banco no processo. 'A'=Ativo (Autor) / 'P'=Passivo (Réu)."""
    url = f"{browser.base}/resources/app/v1/processo/consulta/{numero_processo}"
    try:
        dados = browser.fetch_json(url)
    except VinculoAcessoNegado as exc:
        # Tolerado de propósito: sem o polo o vínculo ainda é válido e útil.
        logger.info("Vínculos: polo indisponível pro processo %s (%s).", numero_processo, exc)
        return None
    return (dados.get("data") or {}).get("indicadorPoloBanco")


def _fmt_npj(numero_processo: Any) -> str:
    """20260034965 → '2026/0034965-000' (máscara NPJ da casa)."""
    d = apenas_digitos(str(numero_processo))
    if len(d) >= 11:
        return f"{d[:4]}/{d[4:11]}-000"
    return str(numero_processo)


def _sem_acento(v: Optional[str]) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKD", str(v or "").strip().lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def situacao_excluida(situacao: Optional[str], excluidas: str = SITUACOES_EXCLUIDAS_DEFAULT) -> bool:
    """True quando a situação indica que o processo NÃO foi distribuído pra nós."""
    s = _sem_acento(situacao)
    if not s:
        return False
    return any(t.strip() and _sem_acento(t) in s for t in (excluidas or "").split(","))


def _polo_texto(indicador: Optional[str]) -> Optional[str]:
    return {"A": "Ativo", "P": "Passivo"}.get((indicador or "").strip().upper())


def _posicao_texto(indicador: Optional[str]) -> Optional[str]:
    # Lado do BANCO: Ativo → BB é Autor; Passivo → BB é Réu.
    return {"A": "Autor", "P": "Réu"}.get((indicador or "").strip().upper())


def pesquisar_vinculos_parte(
    browser: VinculosBrowser,
    doc: str,
    *,
    advogado_mdr: int = ADVOGADO_MDR_DEFAULT,
    incluir_polo: bool = True,
    situacoes_excluidas: str = SITUACOES_EXCLUIDAS_DEFAULT,
) -> dict[str, Any]:
    """Pesquisa os processos da parte e devolve os VÍNCULOS ativos-nossos.

    Devolve {numero_pessoa, total, ativos_mdr:[...], todos:[...]}. Cada item de
    `ativos_mdr` tem npj, cnj, cliente, advogado_bb, situacao, natureza, polo,
    posicao_banco. `todos` é a lista bruta (útil pra auditoria).
    """
    digs = apenas_digitos(doc)
    if not digs or digs == CNPJ_BB:
        return {"numero_pessoa": None, "total": 0, "ativos_mdr": [], "todos": []}

    numero_pessoa = _resolver_numero_pessoa(browser, digs)
    if not numero_pessoa:
        return {"numero_pessoa": None, "total": 0, "ativos_mdr": [], "todos": []}

    ocorrencias = _listar_processos_da_parte(browser, numero_pessoa)
    ativos_mdr: list[dict[str, Any]] = []
    for o in ocorrencias:
        ativo = (o.get("indicadorProcessoAtivo") or "").strip().upper() == "A"
        try:
            nosso = int(o.get("numeroAdvogadoProcesso") or 0) == int(advogado_mdr)
        except (TypeError, ValueError):
            nosso = False
        if not (ativo and nosso):
            continue
        # Ainda não distribuído pra nós (montagem de dossiê) → não é vínculo.
        if situacao_excluida(o.get("textoEstadoProcesso"), situacoes_excluidas):
            continue
        numero_proc = o.get("numeroProcesso")
        cnj = apenas_digitos(o.get("textoNumeroInventario")) or None
        polo = _consultar_polo(browser, numero_proc) if incluir_polo else None
        ativos_mdr.append({
            "npj": _fmt_npj(numero_proc),
            "numero_processo": numero_proc,
            "cnj": cnj,
            "cliente": o.get("nomeContrarioPrincipal"),
            "advogado_bb": o.get("nomeAdvogadoProcesso"),
            "numero_advogado": o.get("numeroAdvogadoProcesso"),
            "situacao": o.get("textoEstadoProcesso"),
            "natureza": o.get("textoNaturezaProcesso"),
            "uja": o.get("codigoPrefixoDependencia"),
            "polo": _polo_texto(polo),
            "posicao_banco": _posicao_texto(polo),
        })

    return {
        "numero_pessoa": numero_pessoa,
        "total": len(ocorrencias),
        "ativos_mdr": ativos_mdr,
        "todos": ocorrencias,
    }
