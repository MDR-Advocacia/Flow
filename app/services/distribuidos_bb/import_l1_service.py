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
import os
import re
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


# Freio contra `total` absurdo do servidor — 2.000 páginas de 30 = 60 mil
# linhas. Antes disso o limite era 40 páginas (1.200), o que não era freio:
# era um truncamento silencioso que escondia a fila real. Ver _listar_staging.
_PAGINAS_MAX = 2000

# Teto de linhas na FILA DE REVISAO do L1 acima do qual este modulo se recusa a
# subir planilha nova.
#
# Existe por causa do tombamento do Master (26 a 28/08/2026). Cada planilha
# respeitava o limite de 500 por arquivo, mas o driver emendava um lote no
# outro SEM esperar a fila drenar — e lote que falhava deixava as linhas la'.
# A fila foi somando 1.216 -> 4.255 -> 5.826 -> 8.882 -> 12.094, o processador
# de import do L1 comecou a estourar por tempo (4.176 linhas com "O processo de
# importacao expirou!") e o cadastro do fluxo diario parou junto: 86 processos
# do BB e do Ativos ficaram sem pasta, publicacao atrasou, e o tenant inteiro
# ficou lento por dois dias.
#
# O limite util nao e' o tamanho do arquivo, e' o quanto a fila aguenta digerir.
# Isso aqui e' a trava que faltava — nao adianta depender de alguem lembrar de
# conferir antes de mandar o proximo lote.
_FILA_REVISAO_MAX = int(os.environ.get("BBD_IMPORT_FILA_MAX", "1000"))

# Quantos ids por chamada de `save`. O L1 processa o commit em background e
# job grande demais morre no meio sem avisar (500 de uma vez criou 4 pastas em
# 26/08/2026). 50 é o tamanho que dá pra conferir e refazer sem estrago.
_SAVE_CHUNK = 50
_SAVE_PAUSA_S = 20


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


def _fila_revisao(sess, h) -> Optional[int]:
    """Quantas linhas estão hoje na fila de revisão do import do L1.

    `None` quando não deu pra ler — nesse caso o chamador NÃO bloqueia: uma
    leitura falha não pode virar impedimento de cadastrar. A trava serve pra
    barrar excesso conhecido, não pra travar na dúvida.
    """
    try:
        st = _import_status(sess, h) or {}
        valor = st.get("revisingLitigationsCount")
        return int(valor) if valor is not None else None
    except Exception:  # noqa: BLE001
        logger.warning("Não foi possível medir a fila de revisão do L1.", exc_info=True)
        return None


def _listar_staging(sess, h) -> list[dict]:
    """Todas as linhas na revisão do import (paginado).

    Pagina até o `total` que o próprio L1 declara. O teto fixo de 40 páginas
    que existia aqui (1.200 linhas) cegava o import inteiro: no tombamento do
    Master (26/08/2026) o staging tinha 1.216 linhas encalhadas de um job que
    o L1 expirou, então as 471 linhas recém-subidas caíam FORA da janela, o
    diff contra o baseline dava vazio e a função devolvia "Nada novo a
    cadastrar (todas as linhas já existem no L1)" — sucesso mentiroso, com
    zero pasta criada e nenhum erro em lugar nenhum.

    O `_PAGINAS_MAX` continua existindo como freio contra `total` absurdo do
    servidor, mas em 60 mil linhas, não em 1.200.

    Status != 200 agora ESTOURA em vez de virar lista vazia. O `or {}` de
    antes transformava um 401 de token vencido em "o staging está vazio", que
    é a leitura mais perigosa possível: some a fila inteira sem avisar.
    """
    rows: list[dict] = []
    for page in range(0, _PAGINAS_MAX):
        r = sess.get(
            f"{_GATEWAY}/litigationImport/LitigationData/GetImportDataPaginated",
            params={"page": page, "count": 30, "filterUserId": "null"}, headers=h, timeout=60,
        )
        if r.status_code != 200:
            raise ImportL1Error(
                f"GetImportDataPaginated {r.status_code} na página {page}: "
                f"{r.text[:200]}"
            )
        d = (r.json() or {}).get("data") or {}
        lote = d.get("data") or []
        rows += lote
        if not lote or (page + 1) * 30 >= (d.get("total") or 0):
            break
    return rows


def _digitos_cnj(s: Optional[str]) -> str:
    import re as _re

    return _re.sub(r"\D", "", s or "")


# Erros que o L1 devolve por congestionamento da PRÓPRIA infraestrutura dele
# (Azure Service Bus), não por problema no dado. A mensagem oficial diz
# textualmente "Please wait 10 seconds and try again" — ou seja, é retentável.
#
# Caso real 31/07/2026: das 15 linhas da planilha 57, 14 entraram e UMA pegou a
# janela de throttling (`ServiceBusy`, código 50002). Ela foi descartada como se
# fosse erro de validação, o processo ficou "Pendente cadastro" para sempre e
# ninguém soube o motivo — a coluna `erro` ficou NULL. Reenviada à mão em
# 03/08, o L1 aceitou na hora e criou a pasta: o dado sempre esteve certo.
_ERRO_TRANSITORIO = re.compile(
    r"throttl|ServiceBusy|ServerBusy|\b50002\b|temporarily unavailable"
    r"|timed?\s?out|\b503\b",
    re.IGNORECASE,
)


def _mensagem_de_erro(row: dict) -> str:
    """Junta o que o L1 reportou de errado nessa linha, em texto único."""
    partes: list[str] = []
    for e in (row.get("errors") or []):
        if isinstance(e, dict):
            m = str(e.get("message") or "").strip()
            if m:
                partes.append(m)
        elif e:
            partes.append(str(e))
    msg = str(row.get("errorMessage") or "").strip()
    if msg:
        partes.append(msg)
    return " | ".join(partes)


def classificar_linha(row: dict, liberados: set) -> tuple[bool, str]:
    """Decide se a linha vai pro cadastro. Devolve (cadastrar, motivo).

    `motivo` só é preenchido quando a linha NÃO vai — é o que o operador precisa
    ver na tela em vez de um "Pendente cadastro" mudo.
    """
    erro = _mensagem_de_erro(row)
    if erro:
        if _ERRO_TRANSITORIO.search(erro):
            # Congestionamento do L1, não problema do dado: reenviar resolve.
            # Validado em produção — o save aceitou a linha com esse erro.
            return True, ""
        return False, f"O Legal One recusou a linha: {erro[:400]}"

    tem_cnj = bool((row.get("identifierNumber") or "").strip())
    if row.get("duplicated") and tem_cnj:
        if _digitos_cnj(row.get("identifierNumber")) in liberados:
            return True, ""  # dup de OUTRO cliente — cadastra mesmo assim
        return False, (
            "O Legal One apontou pasta já existente para esse CNJ "
            "(duplicata no tenant)."
        )
    return True, ""


def _linhas_novas(
    rows: list[dict], cnjs_liberados: Optional[set] = None,
) -> tuple[list[dict], list[dict]]:
    """Separa o que vai pro cadastro do que fica de fora, COM o motivo.

    Devolve `(novas, descartadas)`. Cada descartada é
    `{"id", "cnj", "motivo"}` — o caller grava isso no processo, senão a linha
    some sem explicação (foi o que aconteceu em 31/07/2026).

    Regras:

    - **erro transitório** (throttling do L1) → ENTRA. É retentável e o save
      aceita, validado em produção;
    - **erro real** → fora, com o texto do L1 no motivo;
    - **duplicata COM CNJ** → fora, salvo se o CNJ estiver em `cnjs_liberados`.

    A flag `duplicated` do L1 só é confiável quando há CNJ. Em BB
    Autor/pré-judicial (SEM CNJ) o L1 acusa "duplicado" comparando apenas o nome
    do autor — falso positivo que o fluxo manual ignora e cadastra assim mesmo.

    `cnjs_liberados` (dígitos): CNJs que o CALLER garante não terem pasta do
    MESMO cliente. A dedup do import do L1 é TENANT-WIDE — o mesmo CNJ pode
    existir legitimamente pra outro cliente (BB×Master×Ativos, caso real
    2026-07-24: CNJ do BB descartado por dup de pasta do Master).
    """
    liberados = cnjs_liberados or set()
    novas: list[dict] = []
    descartadas: list[dict] = []
    for x in rows:
        cadastrar, motivo = classificar_linha(x, liberados)
        if cadastrar:
            novas.append(x)
        else:
            descartadas.append({
                "id": x.get("id"),
                "cnj": (x.get("identifierNumber") or "").strip() or None,
                "motivo": motivo,
            })
    return novas, descartadas


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
    cnjs_liberados: Optional[set] = None,
) -> dict[str, Any]:
    """Sobe a planilha e importa via API interna, commitando SÓ as linhas novas
    (não-duplicadas) via selectedIds — NUNCA varre o staging inteiro. Retry
    automático 1x se o token tiver expirado (401). Devolve relatório por passo.

    `cnjs_liberados` (dígitos): resgata linhas dup-com-CNJ cuja pasta existente é
    de OUTRO cliente (ver _linhas_novas)."""
    try:
        return _cadastrar_once(
            conteudo, file_name, firm_id=firm_id, dry_run=dry_run,
            poll_max_s=poll_max_s, tok=obter_token(), cnjs_liberados=cnjs_liberados,
        )
    except ImportL1Error as exc:
        if not _is_unauthorized(exc):
            raise
        logger.warning("Import L1: 401 — recapturando token e tentando de novo.")
        try:
            return _cadastrar_once(
                conteudo, file_name, firm_id=firm_id, dry_run=dry_run,
                poll_max_s=poll_max_s, tok=obter_token(forcar=True),
                cnjs_liberados=cnjs_liberados,
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


def _cadastrar_once(conteudo, file_name, *, firm_id, dry_run, poll_max_s, tok,
                    cnjs_liberados: Optional[set] = None) -> dict[str, Any]:
    rel: dict[str, Any] = {"passos": [], "dry_run": dry_run, "file": file_name}
    size = len(conteudo)
    rel["importado_por"] = {"user_id": tok.get("user_id"), "nome": tok.get("user_name")}
    sess = requests.Session()
    h = _headers(tok)

    # TRAVA DE FILA: nao empilhar em cima de uma revisao ja' entupida. Ver
    # `_FILA_REVISAO_MAX`.
    fila = _fila_revisao(sess, h)
    if fila is not None and fila > _FILA_REVISAO_MAX:
        raise ImportL1Error(
            f"Fila de revisão do Legal One com {fila} linha(s), acima do teto de "
            f"{_FILA_REVISAO_MAX}. Subir mais agora empilha em cima do que o L1 "
            f"ainda não digeriu — foi assim que a fila chegou a 12.094 linhas em "
            f"27/08/2026 e o cadastro parou por dois dias. Esvazie a revisão no "
            f"L1 (ou espere ela drenar) antes de importar."
        )

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
    novos, descartadas = _linhas_novas(desta_planilha, cnjs_liberados)
    novos_ids = [x["id"] for x in novos]
    resgatadas = sum(
        1 for x in novos
        if x.get("duplicated") and (x.get("identifierNumber") or "").strip()
    )
    # Linhas que entraram APESAR de erro — throttling do L1. Contadas à parte
    # pra dar visibilidade: se esse número crescer, o L1 está congestionado.
    retentadas = sum(1 for x in novos if _mensagem_de_erro(x))
    rel["novos"] = len(novos_ids)
    rel["resgatadas_dup_outro_cliente"] = resgatadas
    rel["retentadas_erro_transitorio"] = retentadas
    # O caller grava esses motivos nos processos — sem isso a linha some sem
    # explicação e o operador fica com "Pendente cadastro" mudo.
    rel["descartadas"] = descartadas
    rel["passos"].append({"passo": "match_novos", "ok": True, "novos": len(novos_ids),
                          "resgatadas_dup": resgatadas,
                          "retentadas_transitorio": retentadas,
                          "descartadas": len(descartadas)})
    if retentadas:
        logger.warning(
            "Import L1: %s linha(s) entraram apesar de erro TRANSITÓRIO do L1 "
            "(throttling). Antes elas eram descartadas em silêncio.", retentadas,
        )

    if dry_run:
        rel["resultado"] = f"DRY_RUN — {len(novos_ids)} linha(s) nova(s) prontas (nada criado)."
        return rel
    if not novos_ids:
        rel["resultado"] = "Nada novo a cadastrar (todas as linhas já existem no L1)."
        return rel

    # COMMIT EM BLOCOS. Mandar todos os ids num `save` só faz o L1 enfileirar
    # um job gigante que morre no meio: no tombamento do Master (26/08/2026),
    # 500 ids num único save resultaram em 4 pastas criadas e um e-mail de
    # "importação não concluída" — e o nosso lado reportou sucesso, porque o
    # POST devolve 200 assim que enfileira. Blocos menores dão ao L1 um job
    # que ele termina, e um bloco que falha não leva os outros junto.
    saved = None
    blocos_ok = blocos_falha = 0
    enviados = 0
    for i in range(0, len(novos_ids), _SAVE_CHUNK):
        bloco = novos_ids[i:i + _SAVE_CHUNK]
        try:
            saved = _save(sess, h, selected_ids=bloco)
            blocos_ok += 1
            enviados += len(bloco)
        except Exception as exc:  # noqa: BLE001
            blocos_falha += 1
            logger.error(
                "Import L1: bloco %s-%s do save falhou (%s).",
                i, i + len(bloco), exc,
            )
            rel["passos"].append({
                "passo": "save_bloco", "ok": False,
                "de": i, "ate": i + len(bloco), "erro": str(exc)[:200],
            })
            continue
        if i + _SAVE_CHUNK < len(novos_ids):
            time.sleep(_SAVE_PAUSA_S)
    rel["passos"].append({
        "passo": "save", "ok": blocos_falha == 0,
        "selecionados": len(novos_ids), "enviados": enviados,
        "blocos_ok": blocos_ok, "blocos_falha": blocos_falha,
        "chunk": _SAVE_CHUNK,
        "message": (saved or {}).get("message"),
    })
    rel["enviados_ao_l1"] = enviados
    rel["blocos_falha"] = blocos_falha
    rel["resultado"] = (
        f"{enviados} linha(s) enviada(s) em {blocos_ok} bloco(s) de {_SAVE_CHUNK}"
        + (f"; {blocos_falha} bloco(s) FALHARAM" if blocos_falha else "")
    )
    rel["salvo_em"] = datetime.now(timezone.utc).isoformat()
    return rel
