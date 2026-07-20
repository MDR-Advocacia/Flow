"""Interação com o portal BB (PAJ) via Playwright — coleta + ciência.

Porta fiel do `rpa_login_bb.py` do repo Cadastro, adaptada:
  - login automático via OneLog (cookies injetados no Chromium);
  - Chromium headless (config), como o AJUS já roda no container `api`;
  - extração dos mesmos 11 campos + CNJ por notificação;
  - ciência ("SIM") isolada num método, chamada só quando o gate permite.

A orquestração (persistência write-ahead, gate de ciência, distribuição,
log) vive no `coleta_service.py` e NÃO conhece Playwright — recebe um
"coletor" com esta interface, o que permite testar tudo com um fake.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Iterator, Optional

from app.core.config import settings
from app.services.distribuidos_bb.onelog_client import OneLogClient

logger = logging.getLogger("distribuidos_bb.portal")

# Campos extraídos da notificação (rótulos do portal → chaves normalizadas
# depois pelo service). Mantidos EXATAMENTE como o RPA legado.
CAMPOS_DESEJADOS = [
    "NPJ", "Valor da Causa", "Tramitação", "Natureza", "Polo",
    "Ação", "Data ajuizamento", "Situação", "Advogado", "Adverso principal",
]
_CNJ_REGEX = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")


def _formatar_cookies(cookies_onelog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normaliza cookies do OneLog pro formato do Playwright."""
    def samesite(value: Any) -> str:
        v = str(value or "").strip().lower()
        if v == "strict":
            return "Strict"
        if v == "none":
            return "None"
        return "Lax"

    formatados = []
    for c in cookies_onelog:
        if not all(c.get(k) for k in ("name", "value", "domain")):
            continue
        item = {
            "name": str(c["name"]),
            "value": str(c["value"]),
            "domain": str(c["domain"]),
            "path": str(c.get("path") or "/"),
            "secure": bool(c.get("secure", True)),
            "httpOnly": bool(c.get("httpOnly", c.get("http_only", False))),
            "sameSite": samesite(c.get("sameSite", c.get("same_site"))),
        }
        expires = c.get("expires", c.get("expirationDate"))
        if isinstance(expires, (int, float)):
            item["expires"] = expires
        formatados.append(item)
    return formatados


class NotificacaoBB:
    """Uma notificação aberta no portal: dados extraídos + ação de ciência."""

    def __init__(self, dados: dict[str, Any], portal: "PortalBBColetor"):
        self.dados = dados
        self._portal = portal

    def confirmar_ciencia(self) -> bool:
        """Clica 'SIM' pra dar ciência desta notificação. Irreversível."""
        return self._portal._dar_ciencia_atual()

    def cancelar(self) -> bool:
        """Clica 'NÃO' pra fechar o detalhe SEM dar ciência (modo seguro).

        Provado ao vivo: preserva a lista (a notificação continua pendente) e
        libera a próxima. Sem isto, o modal fica aberto e trava a varredura.
        """
        return self._portal._cancelar_atual()


class PortalBBColetor:
    """Context manager que abre o portal autenticado e itera notificações."""

    def __init__(
        self,
        *,
        onelog: Optional[OneLogClient] = None,
        portal_url: Optional[str] = None,
        headless: Optional[bool] = None,
    ):
        self.onelog = onelog or OneLogClient()
        self.portal_url = portal_url or settings.distribuidos_bb_portal_url
        self.headless = settings.distribuidos_bb_headless if headless is None else headless
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._frame = None

    # ── ciclo de vida ────────────────────────────────────────────────
    def __enter__(self) -> "PortalBBColetor":
        from playwright.sync_api import sync_playwright

        sessao = self.onelog.obter_sessao()
        # Guardada pra reuso fora do navegador (pesquisa de vínculos via HTTP
        # direto usa a MESMA sessão autenticada, sem novo login no OneLog).
        self.sessao_onelog = sessao
        cookies = _formatar_cookies(sessao.get("cookies", []))
        if not cookies:
            raise RuntimeError("OneLog não devolveu cookies válidos para injetar no navegador.")

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
            ],
        )
        self._context = self._browser.new_context(
            user_agent=sessao.get("user_agent"),
            viewport={"width": 1920, "height": 1080},
            locale="pt-BR",
            timezone_id="America/Fortaleza",
        )
        self._context.add_cookies(cookies)
        self._page = self._context.new_page()
        logger.info("Portal BB: abrindo %s", self.portal_url)
        self._page.goto(self.portal_url, wait_until="domcontentloaded", timeout=60000)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for fechar in (
            lambda: self._context and self._context.close(),
            lambda: self._browser and self._browser.close(),
            lambda: self._pw and self._pw.stop(),
        ):
            try:
                fechar()
            except Exception:  # noqa: BLE001
                pass

    def manter_sessao(self) -> None:
        self.onelog.marcapasso()

    # ── localização do app (frame por URL, não por nome) ──────────────
    # O PAJ v2 aninha apps em iframes; o `name` legado ("WIDGET_ID_1") não é
    # confiável (pode apontar pra outro app). O frame certo é o cujo URL
    # contém o caminho do app de notificações. Provado ao vivo 2026-07-09.
    _FRAME_URL_MARK = "consultar-receber-notificacoes"

    def _procurar_frame(self, timeout_ms: int):
        """Uma tentativa: espera até timeout_ms o frame do app montar. None se não veio."""
        import time as _t

        limite = _t.monotonic() + timeout_ms / 1000.0
        while _t.monotonic() < limite:
            for f in self._page.frames:
                if self._FRAME_URL_MARK in (f.url or ""):
                    # confirma que o formulário montou (botão select-all presente)
                    try:
                        if f.locator("button[aria-label='mover todos os itens para a direita']").count() > 0:
                            return f
                    except Exception:  # noqa: BLE001
                        pass
            self._page.wait_for_timeout(1000)
        return None

    def _localizar_frame(self, timeout_ms: Optional[int] = None):
        """Espera o SPA montar e devolve o frame do app de notificações.

        O PAJ é intermitente: às vezes o SPA simplesmente não monta na primeira
        carga (provado em prod 2026-07-16 — run falhou 09:56 e o mesmo run manual
        passou 09:57). Então em vez de abortar na primeira, RECARREGA a página e
        tenta de novo N vezes. Isso acontece ANTES de qualquer ciência, então
        repetir aqui é inócuo (nada irreversível foi feito ainda).
        """
        tentativas = max(1, int(settings.distribuidos_bb_frame_tentativas or 3))
        espera = int(timeout_ms or settings.distribuidos_bb_frame_timeout_ms or 30000)

        for tentativa in range(1, tentativas + 1):
            frame = self._procurar_frame(espera)
            if frame is not None:
                if tentativa > 1:
                    logger.info("Portal BB: app de notificações montou na tentativa %s.", tentativa)
                return frame
            if tentativa < tentativas:
                logger.warning(
                    "Portal BB: app de notificações não montou em %ss (tentativa %s/%s) — "
                    "recarregando a página e tentando de novo.",
                    espera / 1000, tentativa, tentativas,
                )
                try:
                    self._page.reload(wait_until="domcontentloaded", timeout=60000)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Portal BB: falha ao recarregar a página: %s", exc)

        raise RuntimeError(
            f"App de notificações do BB não montou após {tentativas} tentativa(s) de "
            f"{espera / 1000:.0f}s (com reload entre elas). Portal instável/lento no momento "
            f"— headless={self.headless}."
        )

    # ── consulta ─────────────────────────────────────────────────────
    def consultar(self, data_inicial: Optional[str], data_final: Optional[str]) -> int:
        """Preenche o intervalo, seleciona todas e pesquisa. Devolve a contagem."""
        import time

        self._frame = self._localizar_frame()

        if data_inicial:
            try:
                self._frame.locator("input[placeholder='__/__/____']").nth(0).fill(data_inicial)
                if data_final:
                    self._frame.locator("input[placeholder='__/__/____']").nth(1).fill(data_final)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Portal BB: falha ao preencher datas: %s", exc)

        # Seleciona todos os contratos/escritórios e pesquisa (aba "Escritório",
        # Pendente=Sim já vem marcado por padrão).
        self._frame.click("button[aria-label='mover todos os itens para a direita']")
        time.sleep(1)
        self._frame.click("button:has-text('PESQUISAR')")
        time.sleep(4)
        return self._frame.locator("i.mi--event-note").count()

    # ── iteração ─────────────────────────────────────────────────────
    def iterar(self) -> Iterator[NotificacaoBB]:
        """Percorre as notificações abrindo cada uma e extraindo os dados.

        Robusto pros dois modos: se a ciência for dada (SIM), o item sai da
        lista e o próximo desce pra mesma posição (mantém o índice); em modo
        seguro (sem SIM) o item permanece, então o índice avança. Trava de
        segurança contra loop caso a contagem oscile de forma inesperada.
        """
        if self._frame is None:
            raise RuntimeError("Chame consultar() antes de iterar().")
        i = 0
        seq = 0
        limite = self._frame.locator("i.mi--event-note").count() * 2 + 20
        while True:
            botoes = self._frame.locator("i.mi--event-note")
            count = botoes.count()
            if count == 0 or i >= count:
                break
            if seq >= limite:
                logger.warning("Portal BB: trava de segurança da iteração acionada (seq=%s).", seq)
                break
            antes = count
            botoes.nth(i).click()
            self._frame.wait_for_timeout(2000)
            yield NotificacaoBB(self._extrair(seq), self)
            seq += 1
            depois = self._frame.locator("i.mi--event-note").count()
            if depois >= antes:  # nada removido (sem ciência) → avança índice
                i += 1
            # senão: item removido (ciência dada) → mantém i, o próximo desceu

    def _extrair(self, indice: int) -> dict[str, Any]:
        dados: dict[str, Any] = {
            "Notificação": indice + 1,
            "Data e Hora de Extração": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        for campo in CAMPOS_DESEJADOS:
            try:
                el = self._frame.query_selector(f"[bb-title='{campo}'] .chip__desc")
                valor = el.inner_text().strip() if el else ""
            except Exception:  # noqa: BLE001
                valor = ""
            chave = "Adverso Principal" if campo == "Adverso principal" else campo
            dados[chave] = valor

        try:
            html = self._frame.content()
            if "Processo A cadastrar" in html:
                dados["Processo"] = "A cadastrar"
            else:
                achado = _CNJ_REGEX.search(html)
                dados["Processo"] = achado.group(0) if achado else ""
        except Exception:  # noqa: BLE001
            dados["Processo"] = ""
        return dados

    # ── capa do NPJ (Pessoas do Processo / envolvidos) ────────────────
    _DETALHES_URL = (
        "https://juridico.bb.com.br/paj/juridico/v2?app=processoEditApp"
        "&numeroProcesso={numero}&variacao={variacao}&menu=1"
    )
    # Walk do DOM: agrupa cada linha da tabela ao seu polo (cabeçalho anterior),
    # detecta os checkmarks de parte/contrário principal por ícone.
    _WALK_ENVOLVIDOS = r"""() => {
      const KEYS=/POLO ATIVO|POLO PASSIVO|INTERESSADOS|INTERVENIENTES/i;
      const out=[]; let polo=null; const seen=new Set();
      const w=document.createTreeWalker(document.body,NodeFilter.SHOW_ELEMENT);
      let n=w.currentNode;
      while(n){
        if(n.childElementCount===0){const t=(n.innerText||'').trim(); if(t&&t.length<45&&KEYS.test(t)) polo=t;}
        if(n.tagName==='TABLE'&&!seen.has(n)){seen.add(n);
          const heads=[...n.querySelectorAll('thead th,thead td')].map(e=>e.innerText.trim());
          if(heads.includes('CPF/CNPJ')){
            for(const tr of n.querySelectorAll('tbody tr')){
              const tds=[...tr.querySelectorAll('td,th')]; if(!tds.length) continue;
              const cell=i=>tds[i]?tds[i].innerText.trim():'';
              const chk=i=>!!(tds[i]&&tds[i].querySelector('i,svg,[class*="check"],[class*="mi--"]'));
              if(!cell(0)) continue;
              out.push({polo, nome:cell(0), mci:cell(1), cpf_cnpj:cell(2), relacao:cell(3),
                        parte_principal:chk(4), contrario_principal:chk(5)});
            }
          }
        }
        n=w.nextNode();
      }
      return out;
    }"""

    def extrair_envolvidos(self, npj: Optional[str]) -> list[dict[str, Any]]:
        """Abre a capa do NPJ numa página separada e lê as Pessoas do Processo.

        Best-effort: devolve [] se não conseguir (não deve derrubar a coleta).
        Não mexe na página da lista de notificações.
        """
        from app.services.distribuidos_bb import normalizacao as norm

        par = norm.npj_para_numero_variacao(npj)
        if not par or self._context is None:
            return []
        numero, variacao = par
        page = self._context.new_page()
        try:
            page.goto(
                self._DETALHES_URL.format(numero=numero, variacao=variacao),
                wait_until="domcontentloaded", timeout=60000,
            )
            import time as _t

            limite = _t.monotonic() + 30
            frame = None
            while _t.monotonic() < limite:
                for f in page.frames:
                    if "processo-consulta" in (f.url or ""):
                        try:
                            if f.get_by_text("Pessoas do Processo", exact=False).count() > 0:
                                frame = f
                                break
                        except Exception:  # noqa: BLE001
                            pass
                if frame:
                    break
                page.wait_for_timeout(1000)
            if frame is None:
                logger.warning("Portal BB: capa do NPJ %s não carregou (Pessoas do Processo).", npj)
                return []

            frame.get_by_text("Pessoas do Processo", exact=False).first.click()
            page.wait_for_timeout(3000)
            envolvidos = frame.evaluate(self._WALK_ENVOLVIDOS) or []
            return envolvidos
        except Exception as exc:  # noqa: BLE001
            logger.warning("Portal BB: falha ao extrair envolvidos do NPJ %s: %s", npj, exc)
            return []
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass

    def _dar_ciencia_atual(self) -> bool:
        """Clica 'SIM' no modal 'Recebimento da notificação'. Irreversível."""
        try:
            botao = self._frame.locator("button:has-text('SIM')")
            if botao.count() and botao.first.is_visible():
                botao.first.click()
                self._frame.wait_for_timeout(2000)
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Portal BB: falha ao dar ciência: %s", exc)
        return False

    def _cancelar_atual(self) -> bool:
        """Clica 'NÃO' no modal — fecha sem dar ciência, preserva a lista."""
        try:
            botao = self._frame.locator("button:has-text('NÃO')")
            if botao.count() and botao.first.is_visible():
                botao.first.click()
                self._frame.wait_for_timeout(1500)
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Portal BB: falha ao cancelar (NÃO): %s", exc)
        return False
