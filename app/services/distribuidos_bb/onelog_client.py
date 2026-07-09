"""Cliente do OneLog — broker de sessão do portal BB (fluxo zerocore).

Porta do `onelog_client.py` dos repos MDR-Advocacia/Cadastro e OneCost,
adaptada às configs do Flow. O OneLog centraliza o login pesado do PAJ
(certificado/2FA ficam do lado dele) e devolve COOKIES prontos pra injetar
num Chromium. É HTTPS público (`api-onelog.mdradvocacia.com`) — alcançável
de qualquer container, sem rede Docker especial.

Fluxo:
  1. POST /api/zerocore/login  → devolve `setor` (+ cookies se já pronto)
  2. GET  /api/zerocore/status → poll até `concluido` (ou `erro`)
  3. POST /api/zerocore/session→ cookies + user_agent finais
  4. POST /api/zerocore/renew  → marcapasso (manter a sessão viva)
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests

from app.core.config import settings

logger = logging.getLogger("distribuidos_bb.onelog")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

POLL_ATTEMPTS = 150
POLL_INTERVAL_SECONDS = 2
INTERVALO_MARCAPASSO_SEGUNDOS = 15 * 60


class OneLogError(RuntimeError):
    """Falha ao obter/renovar sessão no OneLog."""


class OneLogClient:
    """Wrapper stateful do OneLog (guarda o `setor` pra status/renew)."""

    def __init__(
        self,
        *,
        api_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ):
        self.api_url = (api_url or settings.distribuidos_bb_onelog_api_url or "").rstrip("/")
        self.username = username or settings.distribuidos_bb_onelog_username
        self.password = password or settings.distribuidos_bb_onelog_password
        self.user_agent = user_agent
        self._setor: Optional[str] = None
        self._ultimo_marcapasso = 0.0

    @property
    def configurado(self) -> bool:
        return bool(self.api_url and self.username and self.password)

    # ── HTTP helpers ──────────────────────────────────────────────────
    def _post(self, path: str, payload: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
        resp = requests.post(f"{self.api_url}{path}", json=payload, timeout=timeout)
        if resp.status_code in {401, 403}:
            msg = "acesso negado"
            try:
                msg = resp.json().get("mensagem", msg)
            except ValueError:
                pass
            raise OneLogError(f"OneLog negou acesso: {msg}")
        resp.raise_for_status()
        return resp.json()

    def _get(self, path: str, params: dict[str, Any], timeout: int = 10) -> dict[str, Any]:
        resp = requests.get(f"{self.api_url}{path}", params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    # ── Fluxo principal ───────────────────────────────────────────────
    def obter_sessao(self) -> dict[str, Any]:
        """Devolve {'cookies': [...], 'user_agent': '...'} autenticado no BB."""
        if not self.configurado:
            raise OneLogError(
                "OneLog não configurado: defina distribuidos_bb_onelog_username/"
                "password (e api_url) no ambiente."
            )

        logger.info("OneLog: solicitando sessão para o portal BB.")
        data_login = self._post(
            "/api/zerocore/login",
            {"username": self.username, "password": self.password, "user_agent": self.user_agent},
        )
        self._setor = data_login.get("setor")

        if data_login.get("status") == "sucesso":
            logger.info("OneLog: sessão já estava pronta.")
            return {
                "cookies": data_login.get("cookies", []),
                "user_agent": data_login.get("user_agent", self.user_agent),
            }

        if not self._setor:
            raise OneLogError("OneLog enfileirou o login mas não devolveu o setor para consulta de status.")

        logger.info("OneLog: login enfileirado (setor=%s). Aguardando processamento…", self._setor)
        for tentativa in range(1, POLL_ATTEMPTS + 1):
            time.sleep(POLL_INTERVAL_SECONDS)
            status = self._get("/api/zerocore/status", {"setor": self._setor})
            if status.get("erro"):
                raise OneLogError("OneLog: worker falhou ao autenticar no Banco do Brasil.")
            if status.get("concluido"):
                logger.info("OneLog: login concluído; resgatando cookies (tentativa %s).", tentativa)
                sessao = self._post(
                    "/api/zerocore/session",
                    {"username": self.username, "password": self.password, "setor": self._setor},
                )
                if sessao.get("status") != "sucesso":
                    raise OneLogError("OneLog concluiu, mas não liberou a sessão final.")
                return {
                    "cookies": sessao.get("cookies", []),
                    "user_agent": sessao.get("user_agent", self.user_agent),
                }

        raise OneLogError("OneLog: tempo limite esgotado aguardando a sessão.")

    def marcapasso(self, *, force: bool = False) -> bool:
        """Mantém a sessão viva (chamar periodicamente durante runs longos)."""
        if not self.configurado or not self._setor:
            return False
        agora = time.time()
        if not force and agora - self._ultimo_marcapasso < INTERVALO_MARCAPASSO_SEGUNDOS:
            return True
        try:
            self._post(
                "/api/zerocore/renew",
                {
                    "username": self.username,
                    "password": self.password,
                    "setor": self._setor,
                    "user_agent": self.user_agent,
                },
                timeout=10,
            )
            self._ultimo_marcapasso = agora
            logger.info("OneLog: marcapasso enviado.")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("OneLog: falha no marcapasso: %s", exc)
            return False
