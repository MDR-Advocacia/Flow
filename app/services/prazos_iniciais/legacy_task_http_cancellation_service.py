"""
Cancelamento da legacy task "Agendar Prazos" via HTTP direto.

Substitui o subprocess Node + clickflow (LegacyTaskHelper (legacy_task_helpers))
por um POST direto no endpoint `ModalEnvolvimentoEmLote` do Legal One web.

Descobertas validadas em produção (2026-05-07):
  - O endpoint `/processos/CompromissoTarefa/ModalEnvolvimentoEmLote` aceita
    POST com 9 campos + N `selectionViewModel[SelectedIds][]` repetidos.
  - Sem antiforgery token. Auth 100% via cookie `.ASPXAUTH`.
  - `parentId` (no body e na query) e' decorativo — backend nao valida.
  - Body retorna `{Success: true, SuccessMessage: "...iniciada"}` em
    ~250-300ms; o cancel real e' assincrono. Verificacao autoritativa
    fica com a API L1 (`get_task_by_id` -> `statusId == 3`).
  - Idempotente: re-cancelar task ja cancelada -> 200 Success no-op.
  - Auth invalida -> 403 + body "You do not have permission..." + header
    `razao-falha: O request nao esta autenticado` (canonical).

Login `.ASPXAUTH` continua via Playwright Node em modo `--login-only`
(reusa o fluxo OnePass existente). Cookie cacheado em memoria do worker
(single APScheduler max_instances=1, single container Coolify). TTL
configuravel; refresh sob demanda quando POST retorna 403.

Interface compativel com `LegacyTaskHelper (legacy_task_helpers).cancel_task()`:
  - mesma assinatura
  - mesmo formato de retorno (dict com success/reason/runner_state/etc.)
  - mesmas categorias de erro pro circuit breaker
Plugado no `PrazosIniciaisLegacyTaskQueueService` via factory direta —
desde 2026-05-08 a estrategia "playwright" (clickflow) foi removida e
agora e' sempre HTTP.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import requests
from filelock import FileLock, Timeout as FileLockTimeout

from app.core.config import settings
from app.services.legal_one_client import LegalOneApiClient
from app.services.prazos_iniciais.legacy_task_helpers import (
    DEFAULT_CANCELLED_STATUS_ID,
    DEFAULT_CANCELLED_STATUS_TEXT,
    DEFAULT_LEGACY_TASK_CANDIDATE_STATUS_IDS,
    DEFAULT_LEGACY_TASK_SUBTYPE_EXTERNAL_ID,
    DEFAULT_LEGACY_TASK_TYPE_EXTERNAL_ID,
    DEFAULT_LEGAL_ONE_WEB_BASE_URL,
    LegacyTaskResolver,
    build_task_urls,
    resolve_node_binary,
    resolve_output_root,
    resolve_runner_script,
    resolve_web_credentials,
    web_base_url,
)

logger = logging.getLogger(__name__)


CANCEL_ENDPOINT_PATH = "/processos/CompromissoTarefa/ModalEnvolvimentoEmLote"
# Alteracao em lote de campos da PASTA (nao da tarefa). Mesma familia de modal
# do ModalEnvolvimentoEmLote, outro controller. CampoId identifica o campo a
# alterar; 10 = "Escritorio responsavel" (capturado do HAR da tela de processos
# em 05/08/2026). Assincrono: Success=true significa "enfileirado", nao "feito".
PROCESSOS_LOTE_ENDPOINT_PATH = "/processos/Processos/ModalAlterarEmLote"
CAMPO_ID_ESCRITORIO_RESPONSAVEL = 10
CAMPO_ID_STATUS = 3
# Status de PASTA no L1 (o mesmo do painel): 1 Ativo, 2 Suspenso, 3 Baixado,
# 4 Arquivado. Reativar = voltar pra 1.
STATUS_PASTA_TEXTO = {1: "Ativo", 2: "Suspenso", 3: "Baixado", 4: "Arquivado"}

# 9 campos minimos validados como suficientes pelo Teste 2.4 (2026-05-07).
# `parentId` e' decorativo (Teste 2.3); 0 evita expor um id real por engano.
_BASE_BODY_FIELDS = (
    ("ParentId", "0"),
    ("TipoVinculo", "1"),
    ("CampoText", "Status"),
    ("CampoId", "0"),
    ("StatusText", DEFAULT_CANCELLED_STATUS_TEXT),
    # StatusId entra dinamico (target_status_id da chamada).
    ("selectionViewModel[SelectAll]", "false"),
    ("selectionViewModel[UseStringIds]", "false"),
)


class _CancelHttpError(Exception):
    """Erro do POST HTTP de cancelamento (transporte ou Success=false)."""

    def __init__(self, message: str, *, category: str = "runner_error") -> None:
        super().__init__(message)
        self.category = category


# Caminhos do cache compartilhado entre workers. /app/data e' o volume
# Docker montado em todos os 4 workers Uvicorn — todos veem o mesmo
# arquivo. O .lock pareia com o .json e serializa logins entre os
# workers (sem isso, 4 workers tentam logar em paralelo, o L1 rotaciona
# session a cada novo login, e os 3 que perdem a corrida ficam com
# cookie morto -> 403 em massa).
_SESSION_CACHE_PATH = Path("/app/data/legacy_task_http_session.json")
_SESSION_LOCK_PATH = Path("/app/data/legacy_task_http_session.lock")

# ── Guarda-corpos da sessão web (incidente de 04/08/2026) ──────────────
#
# O login via Node custa ~1 min. Naquele dia, um erro NÃO TRATADO no JS deixou
# o subprocess vivo e pendurado por MAIS DE UMA HORA — e como o subprocess não
# tinha timeout, o worker ficou preso segurando o filelock. Todo caller
# seguinte estourava os 120s de espera e falhava com "web_erro": 335 tarefas
# numa única execução do Balanceador, e o mesmo padrão explica as 619 recusas
# em bloco de 31/07 e 02/08. A requisição nem chegava ao Legal One.
#
# Três defesas, em camadas:
#   1. o subprocess de login tem TETO (_LOGIN_TIMEOUT_S) e o grupo de
#      processos é morto no estouro — o lock NUNCA fica preso indefinidamente;
#   2. login que falhou deixa um marcador com cooldown: os próximos callers
#      falham RÁPIDO e explicando, em vez de cada um tentar o próprio login
#      de 1-3 min em série segurando o lock (tempestade serializada);
#   3. a espera do lock (_LOCK_TIMEOUT_S) é maior que o teto do login, pra
#      um caller não desistir no meio de um login legítimo de outro worker.
_LOGIN_TIMEOUT_S = 180
_LOCK_TIMEOUT_S = 240
_LOGIN_FAILURE_COOLDOWN_S = 120
_LOGIN_FAILURE_MARKER = _SESSION_LOCK_PATH.with_name(
    "legacy_task_http_login_failure.json"
)


class SessionIndisponivelError(RuntimeError):
    """A sessão web do L1 não pôde ser obtida (login falhou/travou ou lock
    ocupado). Subclasse de RuntimeError pra não quebrar quem captura amplo;
    quem quiser tratar diferente (ex.: abortar o lote em vez de tarefa a
    tarefa) captura esta."""


class LegacyTaskHttpCancellationService:
    """
    Cancela a legacy task via POST HTTP. Drop-in para
    `LegacyTaskHelper (legacy_task_helpers)` no `PrazosIniciaisLegacyTaskQueueService`.
    """

    def __init__(
        self,
        *,
        client: Optional[LegalOneApiClient] = None,
        resolver: Optional[LegacyTaskResolver] = None,
    ):
        self.client = client or LegalOneApiClient()
        # Resolver — encapsula o fluxo CNJ -> lawsuit_id -> task selection
        # via API L1 REST. Helpers de paths/credenciais sao funcoes
        # module-level no `legacy_task_helpers`.
        self._resolver = resolver or LegacyTaskResolver(client=self.client)
        self._http = requests.Session()
        self.logger = logging.getLogger(__name__)

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _web_base_url(self) -> str:
        return web_base_url()

    def _build_task_urls(
        self, task_id: int, lawsuit_id: Optional[int] = None
    ) -> dict[str, str]:
        return build_task_urls(task_id, lawsuit_id=lawsuit_id)

    # ── Sessao HTTP ───────────────────────────────────────────────────

    def _session_ttl(self) -> timedelta:
        minutes = max(
            1,
            int(getattr(settings, "prazos_iniciais_legacy_task_session_ttl_minutes", 30) or 30),
        )
        return timedelta(minutes=minutes)

    def _read_session_file(self) -> Optional[dict[str, str]]:
        """Le o cache de cookies do disco. None se nao existe ou expirou."""
        if not _SESSION_CACHE_PATH.exists():
            return None
        try:
            data = json.loads(_SESSION_CACHE_PATH.read_text(encoding="utf-8"))
            obtained_at = datetime.fromisoformat(data["obtained_at"])
            cookies = data.get("cookies") or {}
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if self._utcnow() - obtained_at >= self._session_ttl():
            return None
        if not cookies or ".ASPXAUTH" not in cookies:
            return None
        return dict(cookies)

    def _write_session_file(self, cookies: dict[str, str]) -> None:
        """Persiste cookies no disco com timestamp."""
        _SESSION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SESSION_CACHE_PATH.write_text(
            json.dumps(
                {
                    "cookies": cookies,
                    "obtained_at": self._utcnow().isoformat(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _ensure_session(self) -> dict[str, str]:
        """
        Retorna cookies validos pra requisicoes no L1 web. Cacheia em
        arquivo no volume `/app/data` compartilhado entre os 4 workers
        Uvicorn (UVICORN_WORKERS=4 no docker-api-start.sh).

        Por que arquivo + filelock e nao memoria + threading.Lock?
        Porque cada worker Uvicorn e' um processo Python separado — cada
        um teria seu proprio cache em memoria + lock e disparariam
        logins em paralelo. O servidor L1 ROTACIONA session a cada novo
        login (cookie do login anterior vira invalido), entao 4 logins
        simultaneos = 3 falham com 403 e so o ultimo passa.

        Reproduzido em prod (2026-05-08): worker tick disparou em
        worker_0/1/2/3 quase ao mesmo tempo, todos chamaram login.start,
        e a maioria dos POSTs subsequentes caiu em auth_failure.

        Solucao: cookie em arquivo no volume compartilhado. Filelock
        serializa logins entre os 4 workers — quem chega primeiro loga,
        os outros esperam, depois leem o cache (DCL pattern) e nao
        re-logam. Login real acontece ~1x a cada `session_ttl_minutes`.
        """
        # Fast path — arquivo ja' tem cookie valido (sem precisar de lock).
        cached = self._read_session_file()
        if cached:
            return cached

        # Falha de login recente? Falha RAPIDO em vez de tentar o proprio
        # login: durante uma janela de SSO fora do ar, N callers tentando em
        # serie (1-3 min cada, segurando o lock) e' exatamente a tempestade
        # que derrubou o Balanceador em 04/08/2026.
        recente = self._read_login_failure_marker()
        if recente is not None:
            idade, erro = recente
            raise SessionIndisponivelError(
                f"Login web do L1 falhou ha {int(idade)}s (cooldown de "
                f"{_LOGIN_FAILURE_COOLDOWN_S}s antes de tentar de novo): {erro}"
            )

        lock = FileLock(str(_SESSION_LOCK_PATH), timeout=_LOCK_TIMEOUT_S)
        try:
            with lock:
                # Re-check apos o lock: outro worker pode ter logado e
                # escrito o arquivo enquanto estavamos esperando.
                cached = self._read_session_file()
                if cached:
                    return cached
                recente = self._read_login_failure_marker()
                if recente is not None:
                    idade, erro = recente
                    raise SessionIndisponivelError(
                        f"Login web do L1 falhou ha {int(idade)}s (cooldown): {erro}"
                    )

                # Login efetivo. Custa ~1 min (subprocess Node + SSO L1),
                # com teto duro de _LOGIN_TIMEOUT_S. Outros workers ficam
                # parados no `with lock` esperando (a espera e' maior que o
                # teto, entao ninguem desiste no meio de um login legitimo).
                try:
                    cookies = self._login_via_node()
                except Exception as exc:  # noqa: BLE001
                    self._write_login_failure_marker(str(exc))
                    raise SessionIndisponivelError(
                        f"Login web do L1 falhou: {exc}"
                    ) from exc
                self._clear_login_failure_marker()
                self._write_session_file(cookies)
                return cookies
        except FileLockTimeout as exc:
            raise SessionIndisponivelError(
                f"Timeout (>{_LOCK_TIMEOUT_S}s) esperando o lock de login do "
                "legacy_task_http. Outro worker esta' logando (ou travado) — "
                "com o teto de login em vigor o lock se liberta sozinho; "
                "verifique os run_dirs em /app/output/playwright/legalone/ "
                "se persistir."
            ) from exc

    # ── Marcador de falha de login (cooldown anti-tempestade) ─────────

    def _read_login_failure_marker(self):
        """(idade_em_segundos, erro) se houve falha dentro do cooldown; None
        caso contrario (sem marcador, vencido ou ilegivel)."""
        try:
            data = json.loads(_LOGIN_FAILURE_MARKER.read_text(encoding="utf-8"))
            at = datetime.fromisoformat(data["at"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        idade = (self._utcnow() - at).total_seconds()
        if idade >= _LOGIN_FAILURE_COOLDOWN_S:
            return None
        return idade, str(data.get("erro") or "?")[:300]

    def _write_login_failure_marker(self, erro: str) -> None:
        try:
            _LOGIN_FAILURE_MARKER.parent.mkdir(parents=True, exist_ok=True)
            _LOGIN_FAILURE_MARKER.write_text(
                json.dumps(
                    {"at": self._utcnow().isoformat(), "erro": erro[:1000]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            logger.warning("Nao consegui gravar o marcador de falha de login.")

    def _clear_login_failure_marker(self) -> None:
        try:
            _LOGIN_FAILURE_MARKER.unlink(missing_ok=True)
        except OSError:
            pass

    def _invalidate_session(self) -> None:
        """Apaga o cache de cookies (forca proximo _ensure_session a relogar)."""
        try:
            _SESSION_CACHE_PATH.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "legacy_task_http: nao foi possivel apagar cache de sessao: %s",
                exc,
            )

    def _resolve_login_paths(self) -> Path:
        run_dir = (
            resolve_output_root()
            / "login-only"
            / self._utcnow().strftime("%Y%m%d-%H%M%S-%f")
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def _login_via_node(self) -> dict[str, str]:
        """
        Invoca `cancel-legacy-task.js --login-only --output <path>` pra
        obter o cookie .ASPXAUTH (e companhia) sem depender do clickflow.
        Re-usa o fluxo OnePass/Thomson Reuters/key selection do JS — o
        unico residuo do Playwright que sobrevive na pivotagem HTTP.
        """
        runner_script = resolve_runner_script()
        if not runner_script.exists():
            raise RuntimeError(
                f"Runner Playwright nao encontrado em {runner_script}"
            )

        node_binary = resolve_node_binary()
        credentials = resolve_web_credentials()

        run_dir = self._resolve_login_paths()
        output_path = run_dir / "cookies.json"
        log_path = run_dir / "login.log"
        err_log_path = run_dir / "login.err.log"

        command = [
            node_binary,
            str(runner_script),
            "--login-only",
            "--output",
            str(output_path),
        ]
        env = {**os.environ, **credentials}
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        logger.info(
            "legacy_task_http.login.start run_dir=%s",
            run_dir.name,
        )
        # Popen + wait(timeout) em vez de subprocess.run: o run sem timeout
        # foi o que segurou o filelock por 1h+ em 04/08/2026 (erro nao tratado
        # no JS deixou o node vivo e pendurado). `start_new_session` poe o
        # node num process group proprio, pra o kill do estouro levar junto
        # os chrome que o Playwright dispara.
        popen_kwargs: dict = {}
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        with log_path.open("ab") as stdout, err_log_path.open("ab") as stderr:
            proc = subprocess.Popen(  # noqa: S603
                command,
                cwd=str(runner_script.parent),
                env=env,
                stdout=stdout,
                stderr=stderr,
                creationflags=creation_flags,
                **popen_kwargs,
            )
            try:
                proc.wait(timeout=_LOGIN_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                logger.error(
                    "legacy_task_http.login.timeout run_dir=%s (> %ss) — "
                    "matando o grupo de processos.",
                    run_dir.name, _LOGIN_TIMEOUT_S,
                )
                try:
                    if os.name == "posix":
                        os.killpg(proc.pid, signal.SIGKILL)
                    else:  # pragma: no cover - so' em dev Windows
                        proc.kill()
                except OSError:
                    proc.kill()
                proc.wait(timeout=10)
                raise RuntimeError(
                    f"Login Playwright excedeu {_LOGIN_TIMEOUT_S}s e foi morto "
                    f"(run_dir={run_dir.name}). Sem esse teto, o processo "
                    "pendurado segurava o lock de sessao indefinidamente."
                )
        completed = proc

        if completed.returncode != 0 or not output_path.exists():
            err_preview = ""
            try:
                err_preview = err_log_path.read_text(
                    encoding="utf-8", errors="ignore"
                )[-2000:]
            except OSError:
                err_preview = ""
            raise RuntimeError(
                "Login Playwright falhou em modo --login-only "
                f"(exit_code={completed.returncode}). {err_preview}".strip()
            )

        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Login Playwright gerou output invalido: {exc}"
            ) from exc

        cookies = payload.get("cookies") or {}
        if not isinstance(cookies, dict) or ".ASPXAUTH" not in cookies:
            raise RuntimeError(
                "Login Playwright nao retornou .ASPXAUTH no payload."
            )

        logger.info(
            "legacy_task_http.login.ok run_dir=%s cookies=%d",
            run_dir.name, len(cookies),
        )
        return cookies

    # ── Detector de sessao invalida ───────────────────────────────────

    @staticmethod
    def _is_session_invalid(response: requests.Response) -> bool:
        """
        Detecta sessao expirada/inválida pelos sinais canonicos do L1.
        Baseado no Teste A (2026-05-07): header `razao-falha` e' o sinal
        primario; body "You do not have permission..." e' fallback.
        """
        razao = response.headers.get("razao-falha", "") or ""
        if "autenticado" in razao.lower() or "authenticated" in razao.lower():
            return True
        if response.status_code == 403:
            text = (response.text or "")[:512]
            if "You do not have permission" in text:
                return True
        return False

    # ── POST do cancelamento ──────────────────────────────────────────

    def _build_post_body(
        self, *, task_id: int, target_status_id: int
    ) -> list[tuple[str, str]]:
        body: list[tuple[str, str]] = list(_BASE_BODY_FIELDS)
        body.append(("StatusId", str(int(target_status_id))))
        body.append(("selectionViewModel[SelectedIds][]", str(int(task_id))))
        return body

    def _post_cancel(
        self,
        *,
        task_id: int,
        target_status_id: int,
    ) -> dict[str, Any]:
        """
        Faz UM POST de cancelamento. Re-tenta uma vez se a sessao for
        invalidada no meio (cookie expirado entre o ensure_session e o
        POST chegando no servidor).
        """
        url = (
            f"{self._web_base_url()}{CANCEL_ENDPOINT_PATH}"
            f"?parentId=0&tipoVinculo=1"
        )
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "*/*",
        }

        last_error: Optional[Exception] = None
        for attempt in range(2):
            cookies = self._ensure_session()
            body = self._build_post_body(
                task_id=task_id, target_status_id=target_status_id
            )
            try:
                response = self._http.post(
                    url,
                    data=body,
                    cookies=cookies,
                    headers=headers,
                    timeout=10,
                )
            except requests.exceptions.Timeout as exc:
                last_error = exc
                raise _CancelHttpError(
                    f"timeout no POST cancel: {exc}",
                    category="timeout",
                ) from exc
            except requests.exceptions.RequestException as exc:
                last_error = exc
                raise _CancelHttpError(
                    f"erro de rede no POST cancel: {exc}",
                    category="timeout",
                ) from exc

            if self._is_session_invalid(response):
                self._invalidate_session()
                if attempt == 0:
                    logger.info(
                        "legacy_task_http.session_invalid: re-login e retry "
                        "(task_id=%s)",
                        task_id,
                    )
                    continue
                raise _CancelHttpError(
                    "sessao invalida persistente apos re-login (403)",
                    category="auth_failure",
                )

            if response.status_code >= 500:
                raise _CancelHttpError(
                    f"L1 retornou {response.status_code}: "
                    f"{(response.text or '')[:256]}",
                    category="timeout",
                )
            if response.status_code != 200:
                raise _CancelHttpError(
                    f"L1 retornou {response.status_code}: "
                    f"{(response.text or '')[:256]}",
                    category="runner_error",
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise _CancelHttpError(
                    f"resposta L1 nao e JSON: {(response.text or '')[:256]}",
                    category="runner_error",
                ) from exc

            if not payload.get("Success"):
                err_msg = (
                    payload.get("ErrorMessage")
                    or payload.get("Message")
                    or "L1 retornou Success=false sem mensagem."
                )
                raise _CancelHttpError(
                    f"L1 rejeitou: {err_msg}",
                    category="runner_error",
                )

            return {
                "ok": True,
                "success_message": payload.get("SuccessMessage"),
                "elapsed_ms": int(response.elapsed.total_seconds() * 1000),
                "raw": payload,
            }

        # Nao deveria chegar — _ensure_session sempre retorna ou levanta.
        raise _CancelHttpError(
            f"loop de retry HTTP esgotado: {last_error}",
            category="runner_error",
        )

    def _build_post_body_batch(
        self, *, task_ids: list[int], target_status_id: int
    ) -> list[tuple[str, str]]:
        body: list[tuple[str, str]] = list(_BASE_BODY_FIELDS)
        body.append(("StatusId", str(int(target_status_id))))
        for tid in task_ids:
            body.append(("selectionViewModel[SelectedIds][]", str(int(tid))))
        return body

    def post_cancel_batch(
        self,
        *,
        task_ids: list[int],
        target_status_id: int = DEFAULT_CANCELLED_STATUS_ID,
    ) -> dict[str, Any]:
        """POST de cancelamento em LOTE: N ids num único request (doc §5 — o
        endpoint ModalEnvolvimentoEmLote aceita N `selectionViewModel[SelectedIds][]`).
        NÃO faz pré-check nem verify por tarefa — o caller faz isso EM LOTE pela
        API REST (muito mais rápido que 1 POST+verify por tarefa). 200 = fila
        aceita; a confirmação real é a verificação de statusId via API. Reusa a
        mesma sessão/retry de re-login do `_post_cancel`."""
        if not task_ids:
            return {"ok": True, "count": 0, "raw": None}
        url = f"{self._web_base_url()}{CANCEL_ENDPOINT_PATH}?parentId=0&tipoVinculo=1"
        headers = {"X-Requested-With": "XMLHttpRequest", "Accept": "*/*"}
        for attempt in range(2):
            cookies = self._ensure_session()
            body = self._build_post_body_batch(
                task_ids=task_ids, target_status_id=target_status_id
            )
            try:
                response = self._http.post(
                    url, data=body, cookies=cookies, headers=headers, timeout=30
                )
            except requests.exceptions.RequestException as exc:
                raise _CancelHttpError(
                    f"erro de rede no POST batch: {exc}", category="timeout"
                ) from exc
            if self._is_session_invalid(response):
                self._invalidate_session()
                if attempt == 0:
                    logger.info("legacy_task_http.session_invalid: re-login (batch n=%s)", len(task_ids))
                    continue
                raise _CancelHttpError(
                    "sessao invalida persistente apos re-login (403)", category="auth_failure"
                )
            if response.status_code >= 500:
                raise _CancelHttpError(
                    f"L1 retornou {response.status_code}", category="timeout"
                )
            if response.status_code != 200:
                raise _CancelHttpError(
                    f"L1 retornou {response.status_code}: {(response.text or '')[:256]}",
                    category="runner_error",
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise _CancelHttpError(
                    f"resposta L1 nao e JSON: {(response.text or '')[:256]}",
                    category="runner_error",
                ) from exc
            if not payload.get("Success"):
                err = payload.get("ErrorMessage") or payload.get("Message") or "Success=false"
                raise _CancelHttpError(f"L1 rejeitou batch: {err}", category="runner_error")
            logger.info(
                "legacy_task_http.post_batch_ok n=%s elapsed_ms=%s",
                len(task_ids), int(response.elapsed.total_seconds() * 1000),
            )
            return {"ok": True, "count": len(task_ids), "raw": payload}
        raise _CancelHttpError("loop de retry HTTP (batch) esgotado", category="runner_error")

    # ── Reatribuição de envolvidos (Balanceador) — mesmo endpoint, CampoId 3/4 ──

    def post_reassign(
        self,
        *,
        task_ids: list[int],
        campo_id: int,
        campo_text: str,
        de_id: int,
        de_text: str,
        para_id: int,
        para_text: str,
    ) -> dict[str, Any]:
        """POST de troca de envolvido DE→PARA em lote no ModalEnvolvimentoEmLote.

        Fura o lock de Workflow (a API REST trava o PATCH; o endpoint web não).
        CampoId: 3=Executante, 4=Responsável, 5=Solicitante (nunca enviamos o 5 —
        o solicitante fica intocado). Payload validado em prod 2026-06-26
        (change+revert real na task 339218, ver
        docs/legalone-reatribuir-responsavel-executante-tarefa.md e memória
        reference_l1_reatribuir_workflow). Assíncrono como o cancelamento:
        200 Success = enfileirado; a confirmação real é GET participants depois.
        Reusa a mesma sessão `.ASPXAUTH`/retry de re-login do cancelamento.
        """
        if not task_ids:
            return {"ok": True, "count": 0, "raw": None}
        url = f"{self._web_base_url()}{CANCEL_ENDPOINT_PATH}?parentId=0&tipoVinculo=1"
        headers = {"X-Requested-With": "XMLHttpRequest", "Accept": "*/*"}
        body_base: list[tuple[str, str]] = [
            ("ParentId", "0"),
            ("TipoVinculo", "1"),
            ("CampoText", campo_text),
            ("CampoId", str(int(campo_id))),
            ("DeEnvolvidoId", str(int(de_id))),
            ("DeEnvolvidoText", de_text or ""),
            ("ParaEnvolvidoId", str(int(para_id))),
            ("ParaEnvolvidoText", para_text or ""),
            ("IsRecalculateDeadline", "false"),
            ("ShouldOpenReschedulingRequest", "false"),
            ("TipoLote", "0"),
            ("SubstituirLembretesDeEnvolvidos", "false"),
            ("selectionViewModel[SelectAll]", "false"),
            ("selectionViewModel[UseStringIds]", "false"),
        ]
        for attempt in range(2):
            cookies = self._ensure_session()
            body = list(body_base)
            for tid in task_ids:
                body.append(("selectionViewModel[SelectedIds][]", str(int(tid))))
            try:
                response = self._http.post(
                    url, data=body, cookies=cookies, headers=headers, timeout=30
                )
            except requests.exceptions.RequestException as exc:
                raise _CancelHttpError(
                    f"erro de rede no POST reassign: {exc}", category="timeout"
                ) from exc
            if self._is_session_invalid(response):
                self._invalidate_session()
                if attempt == 0:
                    logger.info(
                        "legacy_task_http.session_invalid: re-login (reassign n=%s)",
                        len(task_ids),
                    )
                    continue
                raise _CancelHttpError(
                    "sessao invalida persistente apos re-login (403)", category="auth_failure"
                )
            if response.status_code >= 500:
                raise _CancelHttpError(
                    f"L1 retornou {response.status_code}", category="timeout"
                )
            if response.status_code != 200:
                raise _CancelHttpError(
                    f"L1 retornou {response.status_code}: {(response.text or '')[:256]}",
                    category="runner_error",
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise _CancelHttpError(
                    f"resposta L1 nao e JSON: {(response.text or '')[:256]}",
                    category="runner_error",
                ) from exc
            if not payload.get("Success"):
                err = payload.get("ErrorMessage") or payload.get("Message") or "Success=false"
                raise _CancelHttpError(f"L1 rejeitou reassign: {err}", category="runner_error")
            logger.info(
                "legacy_task_http.post_reassign_ok campo=%s n=%s de=%s para=%s elapsed_ms=%s",
                campo_text, len(task_ids), de_id, para_id,
                int(response.elapsed.total_seconds() * 1000),
            )
            return {"ok": True, "count": len(task_ids), "raw": payload}
        raise _CancelHttpError("loop de retry HTTP (reassign) esgotado", category="runner_error")

    def post_alterar_escritorio_responsavel(
        self,
        *,
        lawsuit_ids: list[int],
        office_id: int,
        office_text: str = "",
    ) -> dict[str, Any]:
        """Troca o ESCRITORIO RESPONSAVEL de um lote de pastas de processo.

        Endpoint web (`ModalAlterarEmLote`) porque o PATCH REST de pasta esbarra
        na trava de tenant — mesma razao do Arquivar/Ativar. Payload capturado
        do HAR de 05/08/2026, onde a operacao trocou uma pasta na mao.

        SEGURANCA: `SelectAll=false` + `SelectedIds` explicitos. So' as pastas
        listadas mudam; nao existe caminho onde o filtro da tela vaze pro lote.
        Mesmo contrato que o `post_reassign` ja usa em producao pra tarefas.

        Assincrono: 200 com Success=true quer dizer "alteracao iniciada". A
        confirmacao real vem de reler `responsibleOfficeId` na API depois.
        """
        if not lawsuit_ids:
            return {"ok": True, "count": 0, "raw": None}
        url = f"{self._web_base_url()}{PROCESSOS_LOTE_ENDPOINT_PATH}"
        headers = {"X-Requested-With": "XMLHttpRequest", "Accept": "*/*"}
        # Os campos vazios NAO sao decorativos: o modal posta o formulario
        # inteiro e o binder do lado do L1 le' cada um. Campo ausente ja' deu
        # 200 com Success=false em outros modais da casa.
        body_base: list[tuple[str, str]] = [
            ("RequirirNegociacaoDeHonorarioPreenchida", "False"),
            ("ShowJustificationModal", "False"),
            ("CampoText", "Escritório responsável"),
            ("CampoId", str(CAMPO_ID_ESCRITORIO_RESPONSAVEL)),
            ("NegociacaoText", ""), ("NegociacaoId", ""),
            ("ResponsavelText", ""), ("ResponsavelId", ""),
            ("StatusText", ""), ("StatusId", ""),
            ("TituloText", ""),
            ("OriginOfficeText", ""), ("OriginOfficeId", ""),
            ("ResponsibleOfficeText", office_text or ""),
            ("ResponsibleOfficeId", str(int(office_id))),
            ("DischargeDate", ""),
            ("PhaseText", ""), ("PhaseId", ""),
            ("NatureText", ""), ("NatureId", ""),
            ("ClosingDate", ""), ("DecisionDate", ""), ("ResultDate", ""),
            ("ReasonForClosing", ""), ("Value", ""), ("Id", ""),
            ("selectionViewModel[SelectAll]", "false"),
            ("selectionViewModel[SelectFirsts]", "false"),
            ("selectionViewModel[UseStringIds]", "false"),
            ("selectionViewModel[UnselectedIds]", ""),
        ]
        for attempt in range(2):
            cookies = self._ensure_session()
            body = list(body_base)
            for lid in lawsuit_ids:
                body.append(("selectionViewModel[SelectedIds][]", str(int(lid))))
            try:
                response = self._http.post(
                    url, data=body, cookies=cookies, headers=headers, timeout=60
                )
            except requests.exceptions.RequestException as exc:
                raise _CancelHttpError(
                    f"erro de rede no POST de escritorio: {exc}", category="timeout"
                ) from exc
            if self._is_session_invalid(response):
                self._invalidate_session()
                if attempt == 0:
                    logger.info(
                        "legacy_task_http.session_invalid: re-login (escritorio n=%s)",
                        len(lawsuit_ids),
                    )
                    continue
                raise _CancelHttpError(
                    "sessao invalida persistente apos re-login (403)",
                    category="auth_failure",
                )
            if response.status_code >= 500:
                raise _CancelHttpError(
                    f"L1 retornou {response.status_code}", category="timeout"
                )
            if response.status_code != 200:
                raise _CancelHttpError(
                    f"L1 retornou {response.status_code}: {(response.text or '')[:256]}",
                    category="runner_error",
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise _CancelHttpError(
                    f"resposta L1 nao e JSON: {(response.text or '')[:256]}",
                    category="runner_error",
                ) from exc
            if not payload.get("Success"):
                err = (
                    payload.get("ErrorMessage")
                    or payload.get("Message")
                    or "Success=false"
                )
                raise _CancelHttpError(
                    f"L1 rejeitou alteracao de escritorio: {err}",
                    category="runner_error",
                )
            logger.info(
                "legacy_task_http.post_escritorio_ok office=%s n=%s elapsed_ms=%s",
                office_id, len(lawsuit_ids),
                int(response.elapsed.total_seconds() * 1000),
            )
            return {"ok": True, "count": len(lawsuit_ids), "raw": payload}
        raise _CancelHttpError(
            "loop de retry HTTP (escritorio) esgotado", category="runner_error"
        )

    def post_alterar_status_pasta(
        self,
        *,
        lawsuit_ids: list[int],
        status_id: int,
    ) -> dict[str, Any]:
        """Muda o STATUS de um lote de pastas (usado pra REATIVAR: status_id=1).

        Mesmo modal do escritorio responsavel, trocando o `CampoId` — o
        `ModalAlterarEmLote` e' generico e o campo escolhe o que muda. Payload
        validado em ~6.900 pastas no arquivamento em massa de 08/2026.

        Por que nao usar so' o PATCH REST: ele funciona apenas nas pastas
        "limpas". Nas travadas pela config de honorario obrigatorio do tenant
        (a maioria) devolve 400 Validation reclamando de custom fields que o
        proprio schema OData nao aceita no PATCH — paradoxo sem saida via REST.
        Por isso o caminho web e' o fallback obrigatorio, nao um luxo.

        LIMITACAO CONHECIDA: o modal web so' mexe no status — NAO limpa a
        `closingDate`. Pasta reativada por aqui fica com a data de baixa
        residual (cosmetico: `closed=False` e `statusId=1` ficam corretos).
        Quem consegue passar pelo PATCH limpa as duas coisas de uma vez.

        SEGURANCA: `SelectAll=false` + `SelectedIds` explicitos — so' as pastas
        listadas mudam, sem caminho pro filtro da tela vazar pro lote.

        Assincrono: 200 com Success=true quer dizer "alteracao iniciada"; a
        confirmacao real vem de reler `statusId` na API depois.
        """
        if not lawsuit_ids:
            return {"ok": True, "count": 0, "raw": None}
        status_id = int(status_id)
        status_text = STATUS_PASTA_TEXTO.get(status_id, "")
        url = f"{self._web_base_url()}{PROCESSOS_LOTE_ENDPOINT_PATH}"
        headers = {"X-Requested-With": "XMLHttpRequest", "Accept": "*/*"}
        # Os campos vazios NAO sao decorativos: o modal posta o formulario
        # inteiro e o binder do L1 le' cada um. Campo ausente ja' devolveu
        # 200 com Success=false em outros modais da casa.
        body_base: list[tuple[str, str]] = [
            ("RequirirNegociacaoDeHonorarioPreenchida", "True"),
            ("ShowJustificationModal", "False"),
            ("CampoText", "Status"),
            ("CampoId", str(CAMPO_ID_STATUS)),
            ("NegociacaoText", ""), ("NegociacaoId", ""),
            ("ResponsavelText", ""), ("ResponsavelId", ""),
            ("StatusText", status_text), ("StatusId", str(status_id)),
            ("TituloText", ""),
            ("OriginOfficeText", ""), ("OriginOfficeId", ""),
            ("ResponsibleOfficeText", ""), ("ResponsibleOfficeId", ""),
            ("DischargeDate", ""),
            ("PhaseText", ""), ("PhaseId", ""),
            ("NatureText", ""), ("NatureId", ""),
            ("ClosingDate", ""), ("DecisionDate", ""), ("ResultDate", ""),
            ("ReasonForClosing", ""), ("Value", ""), ("Id", ""),
            ("selectionViewModel[SelectAll]", "false"),
            ("selectionViewModel[SelectFirsts]", "false"),
            ("selectionViewModel[UseStringIds]", "false"),
            ("selectionViewModel[UnselectedIds]", ""),
        ]
        for attempt in range(2):
            cookies = self._ensure_session()
            body = list(body_base)
            for lid in lawsuit_ids:
                body.append(("selectionViewModel[SelectedIds][]", str(int(lid))))
            try:
                response = self._http.post(
                    url, data=body, cookies=cookies, headers=headers, timeout=60
                )
            except requests.exceptions.RequestException as exc:
                raise _CancelHttpError(
                    f"erro de rede no POST de status: {exc}", category="timeout"
                ) from exc
            if self._is_session_invalid(response):
                self._invalidate_session()
                if attempt == 0:
                    logger.info(
                        "legacy_task_http.session_invalid: re-login (status n=%s)",
                        len(lawsuit_ids),
                    )
                    continue
                raise _CancelHttpError(
                    "sessao invalida persistente apos re-login (403)",
                    category="auth_failure",
                )
            if response.status_code >= 500:
                raise _CancelHttpError(
                    f"L1 retornou {response.status_code}", category="timeout"
                )
            if response.status_code != 200:
                raise _CancelHttpError(
                    f"L1 retornou {response.status_code}: {(response.text or '')[:256]}",
                    category="runner_error",
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise _CancelHttpError(
                    f"resposta L1 nao e JSON: {(response.text or '')[:256]}",
                    category="runner_error",
                ) from exc
            if not payload.get("Success"):
                err = (
                    payload.get("ErrorMessage")
                    or payload.get("Message")
                    or "Success=false"
                )
                raise _CancelHttpError(
                    f"L1 rejeitou alteracao de status: {err}",
                    category="runner_error",
                )
            logger.info(
                "legacy_task_http.post_status_ok status=%s n=%s elapsed_ms=%s",
                status_id, len(lawsuit_ids),
                int(response.elapsed.total_seconds() * 1000),
            )
            return {"ok": True, "count": len(lawsuit_ids), "raw": payload}
        raise _CancelHttpError(
            "loop de retry HTTP (status) esgotado", category="runner_error"
        )

    # ── Interface publica (compat com LegacyTaskHelper (legacy_task_helpers)) ──

    def cancel_task(
        self,
        *,
        cnj_number: Optional[str] = None,
        lawsuit_id: Optional[int] = None,
        task_id: Optional[int] = None,
        task_type_external_id: int = DEFAULT_LEGACY_TASK_TYPE_EXTERNAL_ID,
        task_subtype_external_id: int = DEFAULT_LEGACY_TASK_SUBTYPE_EXTERNAL_ID,
        candidate_status_ids: Optional[list[int]] = None,
        target_status_id: int = DEFAULT_CANCELLED_STATUS_ID,
        target_status_text: str = DEFAULT_CANCELLED_STATUS_TEXT,
        max_attempts: int = 2,  # nao usado no HTTP (POST e' atomico) — mantido por compat
    ) -> dict[str, Any]:
        candidate_status_ids = list(
            candidate_status_ids or DEFAULT_LEGACY_TASK_CANDIDATE_STATUS_IDS
        )
        # Resolucao via API L1 REST (CNJ -> lawsuit_id -> task_id).
        # Branches possiveis: task_selected, task_not_found, lawsuit_not_found.
        resolution = self._resolver.resolve_target_task(
            cnj_number=cnj_number,
            lawsuit_id=lawsuit_id,
            task_id=task_id,
            task_type_external_id=task_type_external_id,
            task_subtype_external_id=task_subtype_external_id,
            candidate_status_ids=candidate_status_ids,
        )

        selected_task = resolution.get("selected_task")
        resolved_task_id = resolution.get("task_id")
        resolved_lawsuit_id = resolution.get("lawsuit_id")
        normalized_cnj = resolution.get("cnj_number")
        urls = (
            self._build_task_urls(resolved_task_id, lawsuit_id=resolved_lawsuit_id)
            if resolved_task_id is not None
            else {"edit_url": None, "details_url": None}
        )

        if not resolution.get("success"):
            return {
                "success": False,
                "reason": resolution["reason"],
                "cnj_number": normalized_cnj,
                "lawsuit_id": resolved_lawsuit_id,
                "task_id": resolved_task_id,
                "candidate_count": resolution.get("candidate_count"),
                "selected_task": None,
                "current_status_id": None,
                "target_status_id": int(target_status_id),
                "target_status_text": target_status_text,
                "runner_state": None,
                "runner_item_status": None,
                "runner_response": None,
                "runner_error": None,
                "process_exit_code": None,
                "status_file_path": None,
                "log_file_path": None,
                "error_log_file_path": None,
                "artifacts_dir": None,
                "edit_url": urls["edit_url"],
                "details_url": urls["details_url"],
            }

        current_status_id = self._to_int(selected_task.get("statusId"))
        TERMINAL_STATUS_IDS = {1, 2, 3}
        if current_status_id == int(target_status_id):
            logger.info(
                "legacy_task_http.skip_already_target task_id=%s status=%s "
                "(memory pre-check; ja' cancelada — sem POST)",
                resolved_task_id, current_status_id,
            )
            return self._build_skip_payload(
                reason="already_in_target_status",
                normalized_cnj=normalized_cnj,
                resolved_lawsuit_id=resolved_lawsuit_id,
                resolved_task_id=resolved_task_id,
                resolution=resolution,
                selected_task=selected_task,
                current_status_id=current_status_id,
                target_status_id=target_status_id,
                target_status_text=target_status_text,
                urls=urls,
                runner_item_status="already_cancelled",
            )
        if current_status_id in TERMINAL_STATUS_IDS:
            logger.info(
                "legacy_task_http.skip_terminal task_id=%s current=%s target=%s "
                "(memory pre-check; estado terminal != target — sem POST)",
                resolved_task_id, current_status_id, target_status_id,
            )
            return self._build_skip_payload(
                reason="already_in_terminal_state",
                normalized_cnj=normalized_cnj,
                resolved_lawsuit_id=resolved_lawsuit_id,
                resolved_task_id=resolved_task_id,
                resolution=resolution,
                selected_task=selected_task,
                current_status_id=current_status_id,
                target_status_id=target_status_id,
                target_status_text=target_status_text,
                urls=urls,
                runner_item_status="already_in_terminal_state",
            )

        # POST HTTP — coracao novo.
        runner_state = "completed"
        runner_item_status: Optional[str] = None
        runner_error: Optional[str] = None
        runner_response: Optional[dict[str, Any]] = None
        runner_error_category = "runner_error"
        try:
            post_result = self._post_cancel(
                task_id=int(resolved_task_id),
                target_status_id=int(target_status_id),
            )
            runner_response = {
                "successMessage": post_result.get("success_message"),
                "elapsedMs": post_result.get("elapsed_ms"),
            }
            runner_item_status = "cancelled"
            logger.info(
                "legacy_task_http.post_ok task_id=%s elapsed_ms=%s",
                resolved_task_id, post_result.get("elapsed_ms"),
            )
        except _CancelHttpError as exc:
            runner_state = "error"
            runner_item_status = "error"
            runner_error = str(exc)
            runner_error_category = exc.category
            logger.warning(
                "legacy_task_http.post_failed task_id=%s category=%s err=%s",
                resolved_task_id, exc.category, exc,
            )

        # Verificacao autoritativa via API L1 — fonte da verdade. O 200
        # do POST significa "fila aceita", nao "executado" (Teste 2.1
        # provou: StatusId invalido tambem retorna 200 silencioso).
        #
        # Retry curto: o L1 web aceita o POST instantaneamente, mas o
        # backend deles processa de forma assincrona — observado em
        # producao (2026-05-08) levando ~5-10s pra `statusId` refletir o
        # cancelamento na API REST. Verify imediato = falso negativo
        # (`statusId=0 ainda Pendente`) -> item marcado FAILED -> tick
        # seguinte detecta terminal e marca COMPLETED, mas o painel
        # pisca uma falha falsa por ~1 min. Com retry curto, declaramos
        # falha so' apos confirmar mesmo.
        api_verified_status: Optional[int] = None
        VERIFY_RETRIES = 3
        VERIFY_SLEEP_S = 2.0
        for attempt in range(VERIFY_RETRIES):
            try:
                task_after = self.client.get_task_by_id(int(resolved_task_id))
                api_verified_status = self._to_int(task_after.get("statusId"))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "legacy_task_queue.cancel_task.api_verify_failed "
                    "task_id=%s attempt=%s err=%s",
                    resolved_task_id, attempt + 1, exc,
                )
                api_verified_status = None
                break  # erro de rede/auth da API L1 — nao vale tentar de novo

            if api_verified_status == int(target_status_id):
                # Confirmou — para imediatamente.
                break
            # Ainda nao confirmou. Se runner falhou, nao adianta esperar
            # (POST nao foi aceito).
            if runner_state == "error":
                break
            # Mais uma tentativa? Espera e re-busca.
            if attempt < VERIFY_RETRIES - 1:
                time.sleep(VERIFY_SLEEP_S)

        logger.info(
            "legacy_task_queue.cancel_task.api_verify task_id=%s "
            "api_statusId=%s target=%s runner_reports=%s",
            resolved_task_id,
            api_verified_status,
            target_status_id,
            runner_item_status,
        )

        api_confirms_target = (
            api_verified_status is not None
            and int(api_verified_status) == int(target_status_id)
        )
        api_says_not_target = (
            api_verified_status is not None
            and int(api_verified_status) != int(target_status_id)
        )

        if api_confirms_target:
            success = True
        elif api_says_not_target:
            success = False
        else:
            success = runner_state == "completed" and runner_item_status == "cancelled"

        if success:
            reason = "cancelled"
        else:
            if api_says_not_target:
                api_msg = (
                    f"API L1 confirma statusId={api_verified_status} "
                    f"(esperado {target_status_id}). POST nao persistiu."
                )
                runner_error = (
                    f"{runner_error} | {api_msg}" if runner_error else api_msg
                )
            # Categorias compativeis com `INFRASTRUCTURE_FAILURE_REASONS`
            # do circuit breaker: auth_failure, timeout, runner_error.
            reason = runner_error_category if runner_state == "error" else "verification_failed"

        return {
            "success": success,
            "reason": reason,
            "cnj_number": normalized_cnj,
            "lawsuit_id": resolved_lawsuit_id,
            "task_id": resolved_task_id,
            "candidate_count": resolution.get("candidate_count"),
            "selected_task": selected_task,
            "current_status_id": current_status_id,
            "target_status_id": int(target_status_id),
            "target_status_text": target_status_text,
            "runner_state": runner_state,
            "runner_item_status": runner_item_status,
            "runner_response": runner_response,
            "runner_error": runner_error,
            "process_exit_code": 0 if runner_state == "completed" else 1,
            # Caminhos dos artefatos do legado nao se aplicam aqui — None
            # explicito pra UI/painel saber distinguir "via http" de
            # "via playwright" (artifacts_dir = null sinaliza http).
            "status_file_path": None,
            "log_file_path": None,
            "error_log_file_path": None,
            "artifacts_dir": None,
            "edit_url": urls["edit_url"],
            "details_url": urls["details_url"],
        }

    # ── helpers internos ──────────────────────────────────────────────

    @staticmethod
    def _build_skip_payload(
        *,
        reason: str,
        normalized_cnj: Optional[str],
        resolved_lawsuit_id: Optional[int],
        resolved_task_id: Optional[int],
        resolution: dict[str, Any],
        selected_task: dict[str, Any],
        current_status_id: Optional[int],
        target_status_id: int,
        target_status_text: str,
        urls: dict[str, Any],
        runner_item_status: str,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "reason": reason,
            "cnj_number": normalized_cnj,
            "lawsuit_id": resolved_lawsuit_id,
            "task_id": resolved_task_id,
            "candidate_count": resolution.get("candidate_count"),
            "selected_task": selected_task,
            "current_status_id": current_status_id,
            "target_status_id": int(target_status_id),
            "target_status_text": target_status_text,
            "runner_state": "completed",
            "runner_item_status": runner_item_status,
            "runner_response": {
                "verifiedStatusId": current_status_id,
                "verifiedStatusText": (
                    target_status_text if reason == "already_in_target_status" else "(terminal)"
                ),
            },
            "runner_error": None,
            "process_exit_code": 0,
            "status_file_path": None,
            "log_file_path": None,
            "error_log_file_path": None,
            "artifacts_dir": None,
            "edit_url": urls["edit_url"],
            "details_url": urls["details_url"],
        }
