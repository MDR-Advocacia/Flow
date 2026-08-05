"""
Configuração da NEGOCIAÇÃO DO CONTRATO DE HONORÁRIO nas pastas do Legal One.

Por que existe
--------------
O tenant da MDR tem a config "negociação do contrato de honorário obrigatória"
ligada. Pasta sem negociação preenchida faz o L1 recusar operações de escrita
(`PATCH /Lawsuits` devolve 400 Validation) e trava fluxos que dependem de
alterar a pasta. Descoberto quando o arquivamento em massa falhou com:

    "Não foi possível salvar a pasta de Processo pela API, pois a
     configuração que torna a negociação do contrato de honorário
     obrigatória está selecionada."

A API REST **não expõe** esse campo (nem leitura nem escrita) — não está no
schema `LegalOne.LawsuitModel`. Todo o tratamento é via web (cookie .ASPXAUTH),
capturado do DevTools em 2026-08-04.

Como funciona
-------------
ESCRITA — mesmo endpoint do arquivamento (`ModalAlterarEmLote`), trocando o
campo alvo de Status (CampoId=3) para Negociação (CampoId=1):

    POST /processos/Processos/ModalAlterarEmLote
    CampoText=Negociação do contrato de honorário & CampoId=1
    NegociacaoText=<Hon - XXXXXXX/YYY> & NegociacaoId=<id>
    selectionViewModel[SelectedIds][]=<lawsuit_id>   (repetível, lote)

Resposta `{"Success":true,...iniciada}` é ASSÍNCRONA — confirmar depois com
`read_negociacao()`.

IMPORTANTE — seleção explícita por ID:
    O HAR original usava `SelectFirsts=true` + `SearchModelSerialized` (aplica
    em TODOS os resultados de um filtro de busca). Aqui usamos sempre
    `SelectedIds[]` explícito: o alvo é exatamente a lista que o chamador
    passou, sem depender de filtro remoto que pode pegar pasta indevida.

LEITURA — a página de edição da pasta traz o valor atual num bloco JS do
lookup (`lookupgrid_negociacao` → `"value":[{"Id":24,"Value":"Hon - ..."}]`).
`read_negociacao()` faz o parse disso.

Catálogo (2026-08-04): 24 negociações. O Banco Master tem UMA só —
id=24 `Hon - 0000007/001` "Réu", vigente 17/03/2026–31/12/2031.

Ver [[reference_l1_status_pasta_arquivar_ativar]] para o irmão desse fluxo
(Arquivar/Ativar), que compartilha o mesmo endpoint e a mesma sessão web.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable, Optional

import requests

from app.services.prazos_iniciais.legacy_task_http_cancellation_service import (
    LegacyTaskHttpCancellationService,
)

logger = logging.getLogger(__name__)

WEB_BASE_URL = "https://mdradvocacia.novajus.com.br"
ALTERAR_EM_LOTE_PATH = "/processos/Processos/ModalAlterarEmLote"
LOOKUP_NEGOCIACOES_PATH = "/contratos/Negociacoes/LookupNegociacoesGrid"
EDIT_PATH = "/processos/processos/edit/{lawsuit_id}"

# CampoId do modal "Alterando processo(s)": 1 = Negociação, 3 = Status.
CAMPO_ID_NEGOCIACAO = 1
CAMPO_TEXT_NEGOCIACAO = "Negociação do contrato de honorário"

# Negociação única do Banco Master (contratante "Banco Master S.A. - Em
# Liquidação Extrajudicial"). Toda pasta do office 61 usa esta.
NEGOCIACAO_BANCO_MASTER_ID = 24
NEGOCIACAO_BANCO_MASTER_TEXT = "Hon - 0000007/001"

_WEB_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": WEB_BASE_URL,
    "Referer": f"{WEB_BASE_URL}/processos/processos/Search",
}

# Bloco JS do lookup que carrega o valor atual da negociação na tela de edição.
_RE_LOOKUP_NEGOCIACAO = re.compile(
    r'inputHiddenName":"NegociacaoContratoHonorarioId".*?"value":\s*(\[.*?\])',
    re.S,
)


class HonorarioService:
    """Lê e grava a negociação do contrato de honorário nas pastas do L1."""

    def __init__(self, *, session_provider: Optional[Any] = None) -> None:
        # Reusa o login OnePass/Playwright + cache de cookie do serviço de
        # cancelamento — é a mesma sessão web (.ASPXAUTH) para todo o L1.
        self._auth = session_provider or LegacyTaskHttpCancellationService()
        self._http = requests.Session()
        self._authenticated = False

    # ── sessão ────────────────────────────────────────────────────────

    def _ensure_cookies(self, *, force: bool = False) -> None:
        if force:
            self._auth._invalidate_session()
            self._http.cookies.clear()
            self._authenticated = False
        if not self._authenticated:
            self._http.cookies.update(self._auth._ensure_session())
            self._authenticated = True

    # ── catálogo ──────────────────────────────────────────────────────

    def listar_negociacoes(self, *, page_size: int = 50) -> list[dict[str, Any]]:
        """Catálogo de negociações (id, pasta `Hon - ...`, descrição, contratante)."""
        self._ensure_cookies()
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            resp = self._http.get(
                f"{WEB_BASE_URL}{LOOKUP_NEGOCIACOES_PATH}",
                params={"parentId": "", "pageIndex": page, "pageSize": page_size},
                headers={"X-Requested-With": "XMLHttpRequest"},
                timeout=60,
            )
            resp.raise_for_status()
            payload = resp.json()
            rows = payload.get("Rows") or []
            out.extend(rows)
            if len(out) >= int(payload.get("Count") or 0) or not rows:
                break
            page += 1
        return out

    # ── leitura ───────────────────────────────────────────────────────

    @staticmethod
    def _is_login_page(html: str) -> bool:
        """
        Sessão expirada devolve a tela de login com **HTTP 200** — sem isso a
        leitura vira falso-negativo silencioso (toda pasta parece "sem
        negociação"). Detectado em 2026-08-04 numa verificação em massa: até a
        pasta de controle, que comprovadamente tinha negociação, voltou vazia.
        A página real de edição passa de 100 KB; a de login não chega a 10 KB.
        """
        if len(html) > 50_000:
            return False
        low = html.lower()
        return any(
            marker in low
            for marker in ("signon.thomsonreuters", "onepass", "novajus.com.br/conta/login")
        )

    def read_negociacao(self, lawsuit_id: int) -> Optional[dict[str, Any]]:
        """
        Negociação atual da pasta, ou None se não houver.

        A API REST não expõe esse campo — a leitura sai do bloco JS do
        lookup na página de edição.

        Levanta RuntimeError se a página não puder ser lida (sessão morta,
        pasta inexistente). Isso é deliberado: devolver None nesse caso faria
        "não consegui ler" se confundir com "não tem negociação".
        """
        url = f"{WEB_BASE_URL}{EDIT_PATH.format(lawsuit_id=lawsuit_id)}"
        for tentativa in (1, 2):
            self._ensure_cookies(force=(tentativa == 2))
            resp = self._http.get(url, timeout=60)
            if resp.status_code in (401, 403) or self._is_login_page(resp.text):
                continue  # sessão morreu → renova e tenta de novo
            resp.raise_for_status()
            break
        else:
            raise RuntimeError(
                f"Não foi possível ler a pasta {lawsuit_id}: sessão web inválida "
                "mesmo após renovar o login."
            )

        match = _RE_LOOKUP_NEGOCIACAO.search(resp.text)
        if not match:
            if "lookupgrid_negociacao" not in resp.text:
                raise RuntimeError(
                    f"Página da pasta {lawsuit_id} não tem o campo de negociação "
                    f"(HTTP {resp.status_code}, {len(resp.text)} bytes) — "
                    "conteúdo inesperado, não é 'sem negociação'."
                )
            return None
        try:
            valores = json.loads(match.group(1))
        except ValueError:
            logger.warning("honorario.read: JSON do lookup ilegível (lw=%s)", lawsuit_id)
            return None
        for item in valores:
            if item.get("Id"):
                return {"id": item.get("Id"), "text": item.get("Value")}
        return None

    # ── escrita ───────────────────────────────────────────────────────

    def aplicar_negociacao(
        self,
        lawsuit_ids: Iterable[int],
        *,
        negociacao_id: int = NEGOCIACAO_BANCO_MASTER_ID,
        negociacao_text: str = NEGOCIACAO_BANCO_MASTER_TEXT,
    ) -> dict[str, Any]:
        """
        Grava a negociação nas pastas informadas (um POST por lote).

        Seleção sempre explícita por `SelectedIds[]`. Idempotente: regravar a
        mesma negociação devolve Success. A alteração é assíncrona no L1 —
        confirme depois com `read_negociacao()`.
        """
        ids = [int(x) for x in lawsuit_ids]
        if not ids:
            return {"success": True, "http_status": None, "count": 0, "message": "lista vazia"}

        payload: list[tuple[str, str]] = [
            # A trava do tenant não se aplica a este POST — é o próprio
            # preenchimento do campo que ela exige.
            ("RequirirNegociacaoDeHonorarioPreenchida", "False"),
            ("ShowJustificationModal", "False"),
            ("CampoText", CAMPO_TEXT_NEGOCIACAO),
            ("CampoId", str(CAMPO_ID_NEGOCIACAO)),
            ("NegociacaoText", negociacao_text),
            ("NegociacaoId", str(negociacao_id)),
            ("selectionViewModel[SelectAll]", "false"),
            ("selectionViewModel[SelectFirsts]", "false"),
            ("selectionViewModel[UseStringIds]", "false"),
            ("selectionViewModel[UnselectedIds]", ""),
        ]
        payload.extend(("selectionViewModel[SelectedIds][]", str(i)) for i in ids)

        self._ensure_cookies()
        url = f"{WEB_BASE_URL}{ALTERAR_EM_LOTE_PATH}"
        resp = self._http.post(url, data=payload, headers=_WEB_HEADERS, timeout=90)
        if resp.status_code in (401, 403) or "não está autenticado" in resp.text:
            self._ensure_cookies(force=True)
            resp = self._http.post(url, data=payload, headers=_WEB_HEADERS, timeout=90)

        ok = resp.status_code == 200 and '"Success":true' in resp.text
        logger.info(
            "honorario.aplicar count=%s negociacao=%s http=%s ok=%s",
            len(ids), negociacao_id, resp.status_code, ok,
        )
        return {
            "success": ok,
            "http_status": resp.status_code,
            "count": len(ids),
            "message": resp.text[:300],
        }
