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

# Teto de espera pelo login do OneLog. Era 5 min (150 x 2s) e isso mordia:
# a "renovacao de seguranca" do BB leva minutos, e em 11/08/2026 ela so'
# concluiu na tentativa 97 (3min14s) — dentro do teto por pouco. Em 12/08
# passou de 9 minutos e TODAS as tentativas falharam, deixando a coleta parada
# o dia inteiro. 15 min cobre a renovacao com folga; o custo de esperar mais
# e' baixo (a coleta roda em background, 3x ao dia) e o de desistir cedo e'
# alto (nao entra nenhum processo ate' a proxima janela).
POLL_TIMEOUT_SECONDS = 15 * 60
POLL_INTERVAL_SECONDS = 2
# De quanto em quanto tempo repetir no log o que o OneLog esta' dizendo.
# Antes o log ficava MUDO durante toda a espera e nao dava pra distinguir
# "progredindo devagar" de "morto".
LOG_PROGRESSO_SEGUNDOS = 60
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
        # Ultima `mensagem` devolvida pelo /status. E' o que explica a espera
        # ("Aguardando renovacao de seguranca...") e o que precisa chegar no
        # erro e no alerta — antes era descartada.
        self.ultima_mensagem: Optional[str] = None
        # A sessao veio do cache do OneLog ou de um login feito agora? O teste
        # de configuracao precisa disso: sessao em cache responde OK mesmo
        # quando o login do zero esta' quebrado (foi o falso verde de 12/08).
        self.sessao_em_cache: Optional[bool] = None

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
    def obter_sessao(
        self, *, timeout_segundos: Optional[int] = None
    ) -> dict[str, Any]:
        """Devolve {'cookies': [...], 'user_agent': '...'} autenticado no BB.

        `timeout_segundos` sobrescreve o teto de espera. A coleta usa o teto
        cheio (a renovacao de seguranca do BB leva minutos e vale esperar); o
        diagnostico pos-coleta usa um teto curto, porque ali o objetivo nao e'
        conseguir a sessao e sim descobrir DEPRESSA se o login esta' travado.
        """
        teto = int(timeout_segundos or POLL_TIMEOUT_SECONDS)
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
            logger.info("OneLog: sessão já estava pronta (cache).")
            self.sessao_em_cache = True
            self.ultima_mensagem = None
            return {
                "cookies": data_login.get("cookies", []),
                "user_agent": data_login.get("user_agent", self.user_agent),
            }
        self.sessao_em_cache = False

        if not self._setor:
            raise OneLogError("OneLog enfileirou o login mas não devolveu o setor para consulta de status.")

        logger.info(
            "OneLog: login enfileirado (setor=%s). Aguardando até %ss…",
            self._setor, teto,
        )
        inicio = time.time()
        proximo_log = inicio + LOG_PROGRESSO_SEGUNDOS
        tentativa = 0
        while time.time() - inicio < teto:
            tentativa += 1
            time.sleep(POLL_INTERVAL_SECONDS)
            status = self._get("/api/zerocore/status", {"setor": self._setor})
            # A mensagem e' a unica pista do que o OneLog esta' esperando —
            # guardar SEMPRE, inclusive pro erro final.
            self.ultima_mensagem = status.get("mensagem") or self.ultima_mensagem
            if status.get("erro"):
                raise OneLogError(
                    "OneLog: worker falhou ao autenticar no Banco do Brasil"
                    + (f" ({self.ultima_mensagem})" if self.ultima_mensagem else ".")
                )
            agora = time.time()
            if agora >= proximo_log:
                logger.info(
                    "OneLog: aguardando há %s min — %s",
                    int((agora - inicio) // 60),
                    self.ultima_mensagem or "sem mensagem do OneLog",
                )
                proximo_log = agora + LOG_PROGRESSO_SEGUNDOS
            if status.get("concluido"):
                logger.info(
                    "OneLog: login concluído em %ss (tentativa %s); resgatando cookies.",
                    int(agora - inicio), tentativa,
                )
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

        # O motivo vai JUNTO: sem ele o operador so' via "tempo limite esgotado"
        # e nao tinha como saber que a trava era a renovacao de seguranca do BB
        # (que e' resolvida do lado do OneLog, nao aqui).
        motivo = self.ultima_mensagem or "o OneLog não informou o motivo"
        raise OneLogError(
            f"OneLog: tempo limite esgotado ({teto}s) aguardando a sessão. "
            f"Último retorno do OneLog: \"{motivo}\"."
        )

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
