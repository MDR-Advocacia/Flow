"""Import da planilha de migração pela API INTERNA do Legal One (app novo).

Reconstruído do HAR de um import manual (firm.legalone.com.br →
legalone-prod-webapp-eastus2-api.azure-api.net). O `save` do fim é o que CRIA as
pastas e DISPARA O WORKFLOW — coisa que o POST /Lawsuits (API REST pública) não faz.

Auth: mesmo padrão do legacy_task_http, mas app novo. O runner Playwright
`capture-l1-token.js` loga via OnePass (novajus) → SSO no firm → intercepta o
Bearer JWT + a Ocp-Apim-Subscription-Key. Aqui a gente cacheia o token e faz os
6 passos por HTTP puro (requests):

  1. GET  GetStorageSas?fileName=...            → SAS URL do blob temporário
  2. PUT  <sas>/OabLawsuitImport/<file>.xlsx    → sobe os bytes (201)
  3. GET  IsSpreadsheetAlreadyBeingProcessed    → dedup
  4. POST LitigationLoader/SpreadSheetLoad/     → inicia o parse (staging)
  5. GET  getLitigationImportData / paginated   → poll até parsear
  6. POST LitigationOperations/save             → COMMIT (cria pastas + workflow)

`cadastrar_planilha(..., dry_run=True)` faz 1–5 e PARA antes do save.
"""
from __future__ import annotations

import base64
import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

logger = logging.getLogger("distribuidos_bb.import_l1")

_GATEWAY = "https://legalone-prod-webapp-eastus2-api.azure-api.net/prod//webapi/api"
_ORIGIN = "https://firm.legalone.com.br"
_BLOB_FOLDER = "OabLawsuitImport"
_TOKEN_CACHE = Path("/app/data/distribuidos_bb_l1_token.json")


class ImportL1Error(Exception):
    pass


# ─── Token (captura via runner + cache com TTL do próprio exp do JWT) ─────

def _decode_jwt(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:  # noqa: BLE001
        return {}


def _runner_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "runners" / "legalone"


def _sync_web_session(web_cookies: dict[str, Any]) -> None:
    """Regrava os cookies da sessão web no cache compartilhado do legacy_task_http.

    O L1 é single-session por usuário: quando a captura precisou de LOGIN NOVO,
    a sessão anterior (que o resto do sistema usa) morreu. Gravar os cookies
    novos aqui faz todo mundo herdar a sessão em vez de tomar 403 e re-logar —
    o que derrubaria o NOSSO token. Best-effort."""
    try:
        from filelock import FileLock

        from app.services.prazos_iniciais.legacy_task_http_cancellation_service import (
            _SESSION_CACHE_PATH,
            _SESSION_LOCK_PATH,
        )

        cookies = {str(k): str(v) for k, v in (web_cookies or {}).items()}
        if ".ASPXAUTH" not in cookies:
            return
        with FileLock(str(_SESSION_LOCK_PATH), timeout=30):
            _SESSION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _SESSION_CACHE_PATH.write_text(
                json.dumps(
                    {"cookies": cookies, "obtained_at": datetime.now(timezone.utc).isoformat()},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        logger.info("Sessão web compartilhada atualizada com os cookies do login da captura.")
    except Exception:  # noqa: BLE001
        logger.warning("Não consegui sincronizar a sessão web compartilhada.", exc_info=True)


def _capturar_token() -> dict[str, Any]:
    script = _runner_dir() / "capture-l1-token.js"
    if not script.exists():
        raise ImportL1Error(f"Runner de captura não encontrado: {script}")
    # Sessão compartilhada do legacy_task_http: o runner tenta SSO silencioso
    # com esses cookies antes de fazer login com credencial (que derrubaria as
    # sessões dos outros robôs — L1 é single-session por usuário).
    from app.services.prazos_iniciais.legacy_task_http_cancellation_service import (
        _SESSION_CACHE_PATH as _WEB_SESSION_FILE,
    )

    try:
        completed = subprocess.run(
            ["node", script.name, "--session-file", str(_WEB_SESSION_FILE)],
            cwd=str(_runner_dir()),
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired as exc:
        raise ImportL1Error("Timeout (>5min) capturando o token do Legal One.") from exc

    # A última linha do stdout é o JSON.
    linha = ""
    for ln in (completed.stdout or "").splitlines():
        ln = ln.strip()
        if ln.startswith("{"):
            linha = ln
    if not linha:
        raise ImportL1Error(
            f"Captura de token não devolveu JSON (exit {completed.returncode}). "
            f"stderr: {(completed.stderr or '')[-400:]}"
        )
    data = json.loads(linha)
    if not data.get("ok") or not data.get("token"):
        raise ImportL1Error(f"Captura de token falhou: {data.get('error') or data}")
    # Login novo aconteceu → sincroniza o cache compartilhado (senão o próximo
    # _ensure_session dos outros fluxos re-loga e mata o token que acabamos de pegar).
    if data.get("didFullLogin") and data.get("webCookies"):
        _sync_web_session(data["webCookies"])
    return data


def obter_token(forcar: bool = False) -> dict[str, Any]:
    """Token válido (cacheado em disco até ~5min antes do exp do JWT)."""
    if not forcar and _TOKEN_CACHE.exists():
        try:
            cache = json.loads(_TOKEN_CACHE.read_text())
            exp = int(cache.get("exp", 0))
            if exp - 300 > time.time():
                return cache
        except Exception:  # noqa: BLE001
            pass

    data = _capturar_token()
    claims = _decode_jwt(data["token"])
    cache = {
        "token": data["token"],
        "subscriptionKey": data.get("subscriptionKey") or "b1159d90df8d45148b4f5721e2752efc",
        "tenancy": data.get("tenancy") or claims.get("tenant") or "mdradvocacia",
        "distribution": claims.get("distribution") or "FirmsBrazil",
        "user_id": int(claims.get("user_id") or claims.get("nameid") or 0) or None,
        "user_name": claims.get("user_full_name") or "Sistema",
        "exp": int(claims.get("exp") or (time.time() + 1800)),
    }
    try:
        _TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_CACHE.write_text(json.dumps(cache))
    except Exception:  # noqa: BLE001
        logger.warning("Não consegui cachear o token do L1 em %s.", _TOKEN_CACHE, exc_info=True)
    return cache


def _headers(tok: dict[str, Any]) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {tok['token']}",
        "Ocp-Apim-Subscription-Key": tok["subscriptionKey"],
        "tenancy": tok["tenancy"],
        "distribution": tok["distribution"],
        "authenticationMethod": "ASYMMETRIC_JWT_TOKEN",
        "Content-Type": "application/json",
        "Origin": _ORIGIN,
        "Referer": _ORIGIN + "/",
        "Accept": "application/json, text/plain, */*",
    }


# ─── Os 6 passos ─────────────────────────────────────────────────────────

def _get_sas(sess: requests.Session, h: dict, file_name: str) -> str:
    r = sess.get(
        f"{_GATEWAY}/general/document/GetStorageSas",
        params={"fileName": file_name}, headers=h, timeout=60,
    )
    if r.status_code != 200:
        raise ImportL1Error(f"GetStorageSas {r.status_code}: {r.text[:200]}")
    return r.json() if isinstance(r.json(), str) else r.json().get("data") or r.text.strip('"')


def _upload_blob(sas_container_url: str, file_name: str, conteudo: bytes) -> None:
    # SAS vem como https://.../legalonetemp?<query>; o arquivo vai em /OabLawsuitImport/<file>
    base, _, query = sas_container_url.partition("?")
    put_url = f"{base}/{_BLOB_FOLDER}/{file_name}?{query}"
    r = requests.put(
        put_url, data=conteudo,
        headers={
            "x-ms-blob-type": "BlockBlob",
            "x-ms-version": "2021-12-02",
            "Content-Type": "application/octet-stream",
            "Origin": _ORIGIN,
        },
        timeout=180,
    )
    if r.status_code not in (200, 201):
        raise ImportL1Error(f"Upload do blob {r.status_code}: {r.text[:200]}")


def _already_processing(sess, h, file_name: str, size: int) -> bool:
    r = sess.get(
        f"{_GATEWAY}/litigationImport/LitigationLoader/IsSpreadsheetAlreadyBeingProcessed/",
        params={"fileName": file_name, "fileSize": str(size)}, headers=h, timeout=60,
    )
    return bool(r.status_code == 200 and (r.json() or {}).get("data"))


def _spreadsheet_load(sess, h, tok, file_name: str, size: int, firm_id: int) -> dict:
    body = {
        "excelSpreadsheetBlobName": file_name,
        "excelSpreadsheetFolder": _BLOB_FOLDER,
        "isToImportOnlyMainInvolved": True,
        "importCounterPartLawyer": False,
        "mainResponsibleName": tok.get("user_name") or "Sistema",
        "associatedFirmName": "MDR Advocacia",
        "associatedFirmId": firm_id,
        "mainResponsibleId": tok.get("user_id"),
        "setLawsuitStatus": True,
        "originFirmName": "MDR Advocacia",
        "originFirmId": firm_id,
        "lawsuitType": "Judicial",
        "email": "",
        "applicantId": tok.get("user_id"),
        "applicantName": tok.get("user_name") or "Sistema",
        "legalDepartmentId": None,
        "legalDepartmentName": "",
        "excelSpreadsheetLength": size,
    }
    r = sess.post(
        f"{_GATEWAY}/litigationImport/LitigationLoader/SpreadSheetLoad/",
        json=body, headers=h, timeout=120,
    )
    if r.status_code != 200:
        raise ImportL1Error(f"SpreadSheetLoad {r.status_code}: {r.text[:200]}")
    js = r.json()
    if not js.get("success"):
        raise ImportL1Error(f"SpreadSheetLoad recusado: {js.get('message')}")
    return js


def _import_status(sess, h) -> dict:
    r = sess.get(
        f"{_GATEWAY}/litigationImport/LitigationOperations/getLitigationImportData",
        params={"filterUserId": "null"}, headers=h, timeout=60,
    )
    return (r.json() or {}).get("data") or {} if r.status_code == 200 else {}


def _save(sess, h, selected_ids=None) -> dict:
    model = {
        "ignoredIds": [],
        "selectedIds": list(selected_ids or []),
        "searchModel": {
            "importValidationStatus": "0", "filterEmptyNature": "false",
            "importLitigationOriginOffice": "", "importLitigationResponsibleOffice": "",
            "importLitigationStatus": "", "importLitigationNature": "",
            "importLitigationNumber": "", "startLoadDate": "", "endLoadDate": "",
            "userId": None, "searchStatus": 0, "contactsSearchFilter": "",
            "checkedContacts": "", "contactPosition": "",
        },
    }
    body = {
        "excelSpreadsheetBlobName": "", "excelSpreadsheetFolder": "", "email": "",
        "excelSpreadsheetLength": 0, "litigationBatchOperationApiModel": model,
    }
    r = sess.post(
        f"{_GATEWAY}/litigationImport/LitigationOperations/save",
        json=body, headers=h, timeout=120,
    )
    if r.status_code != 200:
        raise ImportL1Error(f"save {r.status_code}: {r.text[:200]}")
    return r.json()


def _listar_staging(sess, h) -> list[dict]:
    """Todas as linhas na revisão do import (paginado)."""
    rows: list[dict] = []
    for page in range(0, 40):
        r = sess.get(
            f"{_GATEWAY}/litigationImport/LitigationData/GetImportDataPaginated",
            params={"page": page, "count": 30, "filterUserId": "null"}, headers=h, timeout=60,
        )
        d = (r.json() or {}).get("data") or {} if r.status_code == 200 else {}
        rows += d.get("data") or []
        if (page + 1) * 30 >= (d.get("total") or 0):
            break
    return rows


def _linhas_novas(rows: list[dict]) -> list[dict]:
    """Linhas a cadastrar = sem erro real, descartando SÓ as duplicatas COM CNJ.

    A flag `duplicated` do L1 só é confiável quando há CNJ. Em BB Autor/pré-judicial
    (SEM CNJ) o L1 acusa "duplicado" comparando apenas o nome do autor — falso
    positivo que o fluxo manual ignora e cadastra assim mesmo. Então: dup COM CNJ =
    duplicata real (fora); dup SEM CNJ = falso positivo (entra)."""
    out = []
    for x in rows:
        if x.get("errors") or x.get("errorMessage"):
            continue  # erro real sempre fora
        tem_cnj = bool((x.get("identifierNumber") or "").strip())
        if x.get("duplicated") and tem_cnj:
            continue  # duplicata confiável (com CNJ) → fora
        out.append(x)
    return out


def _is_unauthorized(exc: Exception) -> bool:
    s = str(exc)
    return "401" in s or "Unauthorized" in s or "invalid credentials" in s


def cadastrar_planilha(
    conteudo: bytes,
    file_name: str,
    *,
    firm_id: int = 1,
    dry_run: bool = True,
    poll_max_s: int = 180,
) -> dict[str, Any]:
    """Sobe a planilha e importa via API interna, commitando SÓ as linhas novas
    (não-duplicadas) via selectedIds — NUNCA varre o staging inteiro. Retry
    automático 1x se o token tiver expirado (401). Devolve relatório por passo."""
    try:
        return _cadastrar_once(
            conteudo, file_name, firm_id=firm_id, dry_run=dry_run,
            poll_max_s=poll_max_s, tok=obter_token(),
        )
    except ImportL1Error as exc:
        if not _is_unauthorized(exc):
            raise
        logger.warning("Import L1: 401 — recapturando token e tentando de novo.")
        try:
            return _cadastrar_once(
                conteudo, file_name, firm_id=firm_id, dry_run=dry_run,
                poll_max_s=poll_max_s, tok=obter_token(forcar=True),
            )
        except ImportL1Error as exc2:
            # 401 MESMO com token fresco = credencial/gateway inválido nessa
            # janela (aconteceu 2026-07-23: SSO do L1 instável). O token ruim
            # ficou cacheado com TTL de ~11h e envenenava as próximas rodadas —
            # apaga o cache pra próxima tentativa começar do zero.
            if _is_unauthorized(exc2):
                try:
                    _TOKEN_CACHE.unlink(missing_ok=True)
                    logger.warning(
                        "Import L1: 401 persistiu após recaptura — cache de token "
                        "apagado (próxima tentativa recaptura do zero)."
                    )
                except Exception:  # noqa: BLE001
                    pass
            raise


def _cadastrar_once(conteudo, file_name, *, firm_id, dry_run, poll_max_s, tok) -> dict[str, Any]:
    rel: dict[str, Any] = {"passos": [], "dry_run": dry_run, "file": file_name}
    size = len(conteudo)
    rel["importado_por"] = {"user_id": tok.get("user_id"), "nome": tok.get("user_name")}
    sess = requests.Session()
    h = _headers(tok)

    # BASELINE: linhas já no staging ANTES do nosso upload (dupes antigos, lixo de
    # outros imports). Depois pegamos só o que ENTROU com esta planilha (diff) —
    # senão re-commitaríamos linhas sem-CNJ velhas e duplicaríamos.
    baseline_ids = {r.get("id") for r in _listar_staging(sess, h)}

    sas = _get_sas(sess, h, file_name)
    rel["passos"].append({"passo": "GetStorageSas", "ok": True})
    _upload_blob(sas, file_name, conteudo)
    rel["passos"].append({"passo": "upload_blob", "ok": True, "bytes": size})
    if _already_processing(sess, h, file_name, size):
        raise ImportL1Error("Essa planilha já está sendo processada no L1 (mesmo nome/tamanho).")
    load = _spreadsheet_load(sess, h, tok, file_name, size, firm_id)
    rel["passos"].append({"passo": "SpreadSheetLoad", "ok": True, "message": load.get("message")})

    # Poll até o parse terminar.
    inicio = time.time()
    status: dict = {}
    while time.time() - inicio < poll_max_s:
        status = _import_status(sess, h)
        if status and not status.get("isLoadingData", True):
            break
        time.sleep(4)
    rel["status_import"] = status

    # Só as linhas DESTA planilha (id não estava no baseline) e cadastráveis.
    desta_planilha = [r for r in _listar_staging(sess, h) if r.get("id") not in baseline_ids]
    novos = _linhas_novas(desta_planilha)
    novos_ids = [x["id"] for x in novos]
    rel["novos"] = len(novos_ids)
    rel["passos"].append({"passo": "match_novos", "ok": True, "novos": len(novos_ids)})

    if dry_run:
        rel["resultado"] = f"DRY_RUN — {len(novos_ids)} linha(s) nova(s) prontas (nada criado)."
        return rel
    if not novos_ids:
        rel["resultado"] = "Nada novo a cadastrar (todas as linhas já existem no L1)."
        return rel

    saved = _save(sess, h, selected_ids=novos_ids)
    rel["passos"].append({
        "passo": "save", "ok": bool(saved.get("success")),
        "selecionados": len(novos_ids), "message": saved.get("message"),
    })
    rel["resultado"] = saved.get("message") or "save concluído"
    rel["salvo_em"] = datetime.now(timezone.utc).isoformat()
    return rel
