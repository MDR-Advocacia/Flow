"""Cobertura DJEN por cadernos diários de tribunais.

O endpoint de comunicações por OAB é rápido, mas tem teto de resultados e não
prova cobertura de toda a carteira. Os cadernos fornecem um ZIP por
``(tribunal, data, meio)`` com páginas JSON e hash SHA-256. Este módulo:

* seleciona os tribunais relacionados às UFs e aos CNJs da carteira;
* inclui os órgãos nacionais, pois o número CNJ é preservado nas instâncias;
* baixa e valida cada caderno sem extrair arquivos no filesystem;
* filtra localmente apenas os CNJs presentes no Agenda Analytics;
* informa explicitamente qualquer combinação ainda não processada ou inválida.
"""

from __future__ import annotations

import hashlib
import gzip
import io
import json
import logging
import re
import tempfile
import time
import unicodedata
import zipfile
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.djen_capture import DjenCadernoShardCache

logger = logging.getLogger(__name__)

_NON_DIGITS = re.compile(r"\D+")
_PAGE_NUMBER = re.compile(r"_(\d+)\.json$", re.IGNORECASE)

_STATE_UF_BY_TR = {
    "01": "AC",
    "02": "AL",
    "03": "AP",
    "04": "AM",
    "05": "BA",
    "06": "CE",
    "07": "DF",
    "08": "ES",
    "09": "GO",
    "10": "MA",
    "11": "MT",
    "12": "MS",
    "13": "MG",
    "14": "PA",
    "15": "PB",
    "16": "PR",
    "17": "PE",
    "18": "PI",
    "19": "RJ",
    "20": "RN",
    "21": "RS",
    "22": "RO",
    "23": "RR",
    "24": "SC",
    "25": "SP",
    "26": "SE",
    "27": "TO",
}


class DjenCadernoError(RuntimeError):
    """Erro explícito ao obter ou validar a cobertura por cadernos."""


def _cnj_digits(value: Any) -> str:
    digits = _NON_DIGITS.sub("", str(value or ""))
    return digits if len(digits) == 20 else ""


def _normalized_status(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(char for char in raw if not unicodedata.combining(char))
    return " ".join(raw.casefold().split())


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _state_uf_from_cnj(digits: str) -> Optional[str]:
    if len(digits) != 20:
        return None
    justice = digits[13]
    tribunal = digits[14:16]
    if justice in {"6", "8", "9"}:
        return _STATE_UF_BY_TR.get(tribunal)
    return None


def origin_tribunal_from_cnj(value: Any) -> Optional[str]:
    """Deriva a sigla do órgão de origem a partir de ``J.TR`` do CNJ."""
    digits = _cnj_digits(value)
    if not digits:
        return None
    justice = digits[13]
    tribunal = digits[14:16]
    tribunal_number = _safe_int(tribunal, -1)

    if justice == "1":
        return "STF"
    if justice == "2":
        return "CNJ"
    if justice == "3":
        return "STJ"
    if justice == "4":
        return "CJF" if tribunal_number == 0 else f"TRF{tribunal_number}"
    if justice == "5":
        return "TST" if tribunal_number == 0 else f"TRT{tribunal_number}"
    if justice == "6":
        uf = _STATE_UF_BY_TR.get(tribunal)
        return "TSE" if tribunal_number == 0 else (f"TRE-{uf}" if uf else None)
    if justice == "7":
        return "STM"
    if justice == "8":
        uf = _STATE_UF_BY_TR.get(tribunal)
        if not uf:
            return None
        return "TJDFT" if uf == "DF" else f"TJ{uf}"
    if justice == "9":
        uf = _STATE_UF_BY_TR.get(tribunal)
        return f"TJM{uf}" if uf else None
    return None


def _page_sort_key(name: str) -> tuple[int, str]:
    match = _PAGE_NUMBER.search(name)
    return (_safe_int(match.group(1), 0) if match else 0, name)


class _SeekableZipReader:
    """Compatibilidade com ``SpooledTemporaryFile`` do Python 3.10.

    Nesse runtime o objeto possui ``read/seek/tell``, mas não expõe
    ``seekable()``; o ``zipfile`` consulta explicitamente esse método.
    """

    def __init__(self, source: Any) -> None:
        self.source = source

    def read(self, size: int = -1) -> bytes:
        return self.source.read(size)

    def seek(self, offset: int, whence: int = 0) -> int:
        return self.source.seek(offset, whence)

    def tell(self) -> int:
        return self.source.tell()

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True


class DjenCadernoDbCache:
    """Checkpoint de shards concluídos, reaproveitável após restart/retry."""

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def portfolio_fingerprint(portfolio_cnjs: Iterable[str]) -> str:
        canonical = "\n".join(sorted(set(portfolio_cnjs)))
        return hashlib.sha256(canonical.encode("ascii")).hexdigest()

    def load(
        self,
        *,
        portfolio_fingerprint: str,
        tribunal: str,
        reference_date: date,
        meio: str,
        version: str,
        archive_hash: str,
    ) -> Optional[list[dict[str, Any]]]:
        row = (
            self.db.query(DjenCadernoShardCache)
            .filter(
                DjenCadernoShardCache.portfolio_fingerprint
                == portfolio_fingerprint,
                DjenCadernoShardCache.tribunal == tribunal,
                DjenCadernoShardCache.reference_date == reference_date,
                DjenCadernoShardCache.meio == meio,
            )
            .one_or_none()
        )
        if (
            row is None
            or row.status != "completed"
            or str(row.archive_hash or "").casefold()
            != str(archive_hash or "").casefold()
            or str(row.version or "") != str(version or "")
        ):
            return None
        if int(row.matched_count or 0) == 0:
            return []
        try:
            raw = gzip.decompress(bytes(row.matched_payload_gzip or b""))
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, list):
                raise ValueError("payload não é lista")
            items = [item for item in payload if isinstance(item, dict)]
            if len(items) != int(row.matched_count or 0):
                raise ValueError("contagem do payload divergente")
            return items
        except Exception:  # noqa: BLE001
            logger.exception(
                "Checkpoint DJEN corrompido para %s/%s/%s; refazendo download.",
                tribunal,
                reference_date,
                meio,
            )
            return None

    def store(
        self,
        *,
        portfolio_fingerprint: str,
        tribunal: str,
        reference_date: date,
        meio: str,
        version: str,
        archive_hash: str,
        total_comunicacoes: int,
        numero_paginas: int,
        download_bytes: int,
        items: list[dict[str, Any]],
    ) -> bool:
        try:
            row = (
                self.db.query(DjenCadernoShardCache)
                .filter(
                    DjenCadernoShardCache.portfolio_fingerprint
                    == portfolio_fingerprint,
                    DjenCadernoShardCache.tribunal == tribunal,
                    DjenCadernoShardCache.reference_date == reference_date,
                    DjenCadernoShardCache.meio == meio,
                )
                .one_or_none()
            )
            if row is None:
                row = DjenCadernoShardCache(
                    portfolio_fingerprint=portfolio_fingerprint,
                    tribunal=tribunal,
                    reference_date=reference_date,
                    meio=meio,
                )
                self.db.add(row)
            encoded = json.dumps(
                items,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            row.version = str(version or "") or None
            row.archive_hash = str(archive_hash or "") or None
            row.status = "completed"
            row.total_comunicacoes = int(total_comunicacoes)
            row.numero_paginas = int(numero_paginas)
            row.matched_count = len(items)
            row.download_bytes = int(download_bytes)
            row.matched_payload_gzip = (
                gzip.compress(encoded, compresslevel=6) if items else None
            )
            self.db.commit()
            return True
        except Exception:  # noqa: BLE001
            self.db.rollback()
            logger.exception(
                "Não foi possível persistir checkpoint DJEN %s/%s/%s.",
                tribunal,
                reference_date,
                meio,
            )
            return False

    def cleanup(self, max_age_days: int) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=max(1, int(max_age_days))
        )
        try:
            (
                self.db.query(DjenCadernoShardCache)
                .filter(DjenCadernoShardCache.updated_at < cutoff)
                .delete(synchronize_session=False)
            )
            self.db.commit()
        except Exception:  # noqa: BLE001
            self.db.rollback()
            logger.exception("Falha ao limpar checkpoints DJEN antigos.")


class DjenCadernoClient:
    """Cliente dos endpoints públicos ``/tribunal`` e ``/caderno``."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        proxy: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        request_delay_seconds: Optional[float] = None,
        max_period_days: Optional[int] = None,
        max_total_download_mb: Optional[int] = None,
        api_session: Optional[requests.Session] = None,
        download_session: Optional[requests.Session] = None,
        cache: Optional[DjenCadernoDbCache] = None,
    ) -> None:
        self.base_url = (base_url or settings.comunica_base_url).rstrip("/")
        self.proxy = (proxy if proxy is not None else settings.djen_proxy) or ""
        self.timeout_seconds = max(
            5,
            int(timeout_seconds or settings.comunica_timeout_seconds or 30),
        )
        self.request_delay_seconds = max(
            0.0,
            float(
                request_delay_seconds
                if request_delay_seconds is not None
                else settings.djen_fallback_caderno_request_delay_seconds
            ),
        )
        self.max_period_days = max(
            1,
            int(
                max_period_days
                or settings.djen_fallback_caderno_max_period_days
                or 7
            ),
        )
        self.max_total_download_bytes = max(
            1,
            int(
                max_total_download_mb
                or settings.djen_fallback_caderno_max_total_download_mb
                or 8192
            ),
        ) * 1024 * 1024
        self.api_session = api_session or requests.Session()
        if self.proxy:
            self.api_session.proxies.update(
                {"http": self.proxy, "https": self.proxy}
            )
        self._rate_limit_resume_at = 0.0
        # O ZIP fica em S3 e não sofre o geo-block da API. Baixá-lo diretamente
        # evita trafegar arquivos grandes pelo proxy brasileiro.
        self.download_session = download_session or requests.Session()
        self.cache = cache

    def _api_json(
        self,
        url: str,
        *,
        context: str,
        tries: int = 4,
    ) -> Any:
        last_error: Optional[BaseException] = None
        for attempt in range(1, tries + 1):
            rate_limit_wait = max(
                0.0,
                self._rate_limit_resume_at - time.monotonic(),
            )
            if rate_limit_wait:
                time.sleep(rate_limit_wait)
            if self.request_delay_seconds:
                time.sleep(self.request_delay_seconds)
            response = None
            try:
                response = self.api_session.get(
                    url,
                    timeout=self.timeout_seconds,
                )
                if response.status_code == 200:
                    remaining_header = response.headers.get(
                        "x-ratelimit-remaining"
                    )
                    if (
                        remaining_header is not None
                        and _safe_int(remaining_header, -1) <= 0
                    ):
                        self._rate_limit_resume_at = time.monotonic() + 60
                    return response.json()
                if response.status_code == 429 and attempt < tries:
                    retry_after = _safe_int(
                        response.headers.get("Retry-After"),
                        0,
                    )
                    time.sleep(max(0, retry_after) or 60)
                    continue
                if (
                    response.status_code in {500, 502, 503, 504}
                    and attempt < tries
                ):
                    time.sleep((2 ** (attempt - 1)) + 1)
                    continue
                body = str(getattr(response, "text", "") or "").strip()[:500]
                raise DjenCadernoError(
                    f"DJEN HTTP {response.status_code} ao consultar {context}: {body}"
                )
            except DjenCadernoError:
                raise
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt >= tries:
                    break
                time.sleep(2 ** (attempt - 1))
        raise DjenCadernoError(
            f"DJEN indisponível ao consultar {context}: "
            f"{last_error or 'erro desconhecido'}"
        ) from last_error

    def fetch_tribunal_registry(self) -> list[dict[str, Any]]:
        payload = self._api_json(
            f"{self.base_url}/api/v1/comunicacao/tribunal",
            context="lista de tribunais",
        )
        if not isinstance(payload, list):
            raise DjenCadernoError(
                "DJEN retornou a lista de tribunais em formato inesperado."
            )
        return [item for item in payload if isinstance(item, dict)]

    @staticmethod
    def select_tribunals(
        portfolio: dict[str, Any],
        registry: Iterable[dict[str, Any]],
    ) -> tuple[list[str], dict[str, Any]]:
        """Seleciona órgãos locais, regionais e nacionais da carteira."""
        groups: dict[str, set[str]] = {}
        national: set[str] = set()
        registry_siglas: dict[str, str] = {}

        for group in registry:
            uf = str(group.get("uf") or "").strip().upper()
            siglas: set[str] = set()
            for institution in group.get("instituicoes") or []:
                if not isinstance(institution, dict):
                    continue
                # A rota de cadernos é sensível a maiúsculas/minúsculas para
                # algumas siglas (PJeCor funciona, PJECOR retorna HTTP 400).
                # Preserva a grafia canônica fornecida pelo próprio catálogo.
                sigla = str(institution.get("sigla") or "").strip()
                if sigla:
                    siglas.add(sigla)
                    registry_siglas.setdefault(sigla.casefold(), sigla)
            if uf:
                groups.setdefault(uf, set()).update(siglas)
            else:
                national.update(siglas)

        selected: dict[str, str] = {
            sigla.casefold(): sigla for sigla in national
        }

        def add_selected(sigla: str) -> None:
            canonical = registry_siglas.get(sigla.casefold(), sigla)
            selected.setdefault(canonical.casefold(), canonical)

        # O endpoint aceita STF mesmo quando ele não aparece no registro.
        add_selected("STF")
        portfolio_ufs: set[str] = set()
        origin_siglas: set[str] = set()
        unresolved_cnjs: list[str] = []

        for digits, process in portfolio.items():
            valid_ufs: set[str] = set()
            for value in getattr(process, "ufs", set()) or set():
                uf = str(value or "").strip().upper()
                if len(uf) == 2 and uf.isalpha():
                    valid_ufs.add(uf)
            derived_uf = _state_uf_from_cnj(digits)
            if derived_uf:
                valid_ufs.add(derived_uf)
            portfolio_ufs.update(valid_ufs)

            origin = origin_tribunal_from_cnj(digits)
            if origin:
                origin_siglas.add(origin)
            if not origin and not any(uf in groups for uf in valid_ufs):
                unresolved_cnjs.append(digits)

        for uf in portfolio_ufs:
            for sigla in groups.get(uf, set()):
                add_selected(sigla)
        for sigla in origin_siglas:
            add_selected(sigla)

        if not selected:
            raise DjenCadernoError(
                "Nenhum tribunal pôde ser relacionado à carteira do relatório."
            )
        return sorted(selected.values(), key=str.casefold), {
            "portfolio_ufs": sorted(portfolio_ufs),
            "origin_tribunals": sorted(origin_siglas, key=str.casefold),
            "registry_tribunals": len(registry_siglas),
            "unresolved_tribunal_cnjs": unresolved_cnjs[:100],
            "unresolved_tribunal_cnjs_count": len(unresolved_cnjs),
        }

    def _download_archive(
        self,
        url: str,
        *,
        expected_sha256: str,
        remaining_bytes: int,
        context: str,
        tries: int = 3,
    ) -> tuple[tempfile.SpooledTemporaryFile, int]:
        last_error: Optional[BaseException] = None
        for attempt in range(1, tries + 1):
            response = None
            spool = None
            try:
                response = self.download_session.get(
                    url,
                    stream=True,
                    timeout=(self.timeout_seconds, max(120, self.timeout_seconds * 10)),
                )
                if response.status_code == 429 and attempt < tries:
                    retry_after = _safe_int(
                        response.headers.get("Retry-After"),
                        0,
                    )
                    time.sleep(max(0, retry_after) or 60)
                    continue
                if (
                    response.status_code in {500, 502, 503, 504}
                    and attempt < tries
                ):
                    time.sleep((2 ** (attempt - 1)) + 1)
                    continue
                if response.status_code != 200:
                    body = str(getattr(response, "text", "") or "")[:500]
                    raise DjenCadernoError(
                        f"Download do caderno {context} retornou HTTP "
                        f"{response.status_code}: {body}"
                    )

                declared_size = _safe_int(
                    response.headers.get("Content-Length"),
                    0,
                )
                if declared_size and declared_size > remaining_bytes:
                    raise DjenCadernoError(
                        f"Caderno {context} excede o limite total de download."
                    )

                spool = tempfile.SpooledTemporaryFile(
                    max_size=32 * 1024 * 1024,
                    mode="w+b",
                )
                digest = hashlib.sha256()
                downloaded = 0
                chunks = (
                    response.iter_content(chunk_size=1024 * 1024)
                    if hasattr(response, "iter_content")
                    else [bytes(getattr(response, "content", b"") or b"")]
                )
                for chunk in chunks:
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > remaining_bytes:
                        raise DjenCadernoError(
                            f"Caderno {context} excedeu o limite total de download."
                        )
                    digest.update(chunk)
                    spool.write(chunk)

                actual_hash = digest.hexdigest().casefold()
                expected_hash = str(expected_sha256 or "").strip().casefold()
                if not expected_hash:
                    raise DjenCadernoError(
                        f"Caderno {context} não informou hash SHA-256."
                    )
                if actual_hash != expected_hash:
                    raise DjenCadernoError(
                        f"Hash SHA-256 divergente no caderno {context}."
                    )
                spool.seek(0)
                return spool, downloaded
            except DjenCadernoError:
                if spool is not None:
                    spool.close()
                raise
            except (requests.RequestException, OSError) as exc:
                last_error = exc
                if spool is not None:
                    spool.close()
                if attempt >= tries:
                    break
                time.sleep(2 ** (attempt - 1))
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()

        raise DjenCadernoError(
            f"Falha ao baixar o caderno {context}: "
            f"{last_error or 'erro desconhecido'}"
        ) from last_error

    def _read_archive(
        self,
        archive_file: tempfile.SpooledTemporaryFile,
        *,
        portfolio_cnjs: set[str],
        expected_pages: int,
        expected_total: int,
        expected_tribunal: str,
        expected_date: date,
        expected_meio: str,
        context: str,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        selected: list[dict[str, Any]] = []
        scanned = 0
        declared = 0
        try:
            with zipfile.ZipFile(_SeekableZipReader(archive_file)) as archive:
                entries = sorted(
                    (
                        item
                        for item in archive.infolist()
                        if not item.is_dir()
                    ),
                    key=lambda item: _page_sort_key(item.filename),
                )
                if not entries or any(
                    not item.filename.casefold().endswith(".json")
                    for item in entries
                ):
                    raise DjenCadernoError(
                        f"ZIP do caderno {context} não contém apenas páginas JSON."
                    )
                if expected_pages > 0 and len(entries) != expected_pages:
                    raise DjenCadernoError(
                        f"Caderno {context} declarou {expected_pages} páginas, "
                        f"mas o ZIP contém {len(entries)}."
                    )

                maximum_uncompressed = min(
                    16 * 1024 * 1024 * 1024,
                    max(256 * 1024 * 1024, self.max_total_download_bytes * 4),
                )
                if sum(item.file_size for item in entries) > maximum_uncompressed:
                    raise DjenCadernoError(
                        f"Conteúdo descompactado do caderno {context} excede "
                        "o limite de segurança."
                    )

                for entry in entries:
                    with archive.open(entry) as raw_page:
                        with io.TextIOWrapper(
                            raw_page,
                            encoding="utf-8-sig",
                        ) as text_page:
                            payload = json.load(text_page)
                    if not isinstance(payload, dict):
                        raise DjenCadernoError(
                            f"Página {entry.filename} do caderno {context} "
                            "tem formato inválido."
                        )
                    items = payload.get("items")
                    if not isinstance(items, list):
                        raise DjenCadernoError(
                            f"Página {entry.filename} do caderno {context} "
                            "não possui lista de items."
                        )
                    page_count = _safe_int(payload.get("count"), len(items))
                    if page_count != len(items):
                        raise DjenCadernoError(
                            f"Página {entry.filename} do caderno {context} "
                            "tem contagem divergente."
                        )
                    scanned += len(items)
                    declared += page_count
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        item_tribunal = str(
                            item.get("siglaTribunal") or ""
                        ).strip().upper()
                        item_date = str(
                            item.get("data_disponibilizacao")
                            or item.get("dataDisponibilizacao")
                            or ""
                        )[:10]
                        item_meio = str(
                            item.get("meio") or ""
                        ).strip().upper()
                        if (
                            item_tribunal
                            and item_tribunal.casefold()
                            != expected_tribunal.casefold()
                        ):
                            raise DjenCadernoError(
                                f"Caderno {context} contém item do tribunal "
                                f"{item_tribunal}."
                            )
                        if item_date and item_date != expected_date.isoformat():
                            raise DjenCadernoError(
                                f"Caderno {context} contém item da data "
                                f"{item_date}."
                            )
                        if item_meio and item_meio != expected_meio:
                            raise DjenCadernoError(
                                f"Caderno {context} contém item do meio "
                                f"{item_meio}."
                            )
                        digits = _cnj_digits(
                            item.get("numero_processo")
                            or item.get("numeroProcesso")
                            or item.get("numeroprocessocommascara")
                        )
                        if digits in portfolio_cnjs:
                            selected.append(item)
        except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeError) as exc:
            raise DjenCadernoError(
                f"ZIP/JSON inválido no caderno {context}: {exc}"
            ) from exc

        if expected_total >= 0 and scanned != expected_total:
            raise DjenCadernoError(
                f"Caderno {context} declarou {expected_total} comunicações, "
                f"mas as páginas contêm {scanned}."
            )
        return selected, {
            "scanned": scanned,
            "declared": declared,
            "selected": len(selected),
        }

    def fetch_by_portfolio(
        self,
        *,
        portfolio: dict[str, Any],
        date_from: date,
        date_to: date,
        meio: str = "D",
        tribunal_siglas: Optional[Iterable[str]] = None,
        now_brazil: Optional[datetime] = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if date_to < date_from:
            raise DjenCadernoError(
                f"Período de cadernos invertido: {date_from}..{date_to}."
            )
        period_days = (date_to - date_from).days + 1
        if period_days > self.max_period_days:
            raise DjenCadernoError(
                f"Período de {period_days} dias excede o limite de "
                f"{self.max_period_days} dias para contingência por cadernos."
            )
        portfolio_cnjs = {
            digits for digits in portfolio if _cnj_digits(digits)
        }
        if not portfolio_cnjs:
            raise DjenCadernoError(
                "Carteira sem CNJs válidos para filtrar os cadernos."
            )
        portfolio_fingerprint = DjenCadernoDbCache.portfolio_fingerprint(
            portfolio_cnjs
        )
        if self.cache is not None:
            self.cache.cleanup(
                settings.djen_fallback_caderno_cache_days or 14
            )

        selection_metadata: dict[str, Any] = {}
        if tribunal_siglas is None:
            registry = self.fetch_tribunal_registry()
            tribunals, selection_metadata = self.select_tribunals(
                portfolio,
                registry,
            )
        else:
            explicit_tribunals: dict[str, str] = {}
            for value in tribunal_siglas:
                sigla = str(value or "").strip()
                if sigla:
                    explicit_tribunals.setdefault(sigla.casefold(), sigla)
            tribunals = sorted(
                explicit_tribunals.values(),
                key=str.casefold,
            )
        if not tribunals:
            raise DjenCadernoError(
                "Nenhum tribunal selecionado para a contingência por cadernos."
            )

        all_items: list[dict[str, Any]] = []
        incomplete: list[dict[str, str]] = []
        stats = Counter()
        downloaded_bytes = 0
        current = date_from
        meio = str(meio or "D").strip().upper()
        brazil_tz = ZoneInfo("America/Sao_Paulo")
        current_brazil_time = now_brazil or datetime.now(brazil_tz)
        if current_brazil_time.tzinfo is None:
            current_brazil_time = current_brazil_time.replace(tzinfo=brazil_tz)
        else:
            current_brazil_time = current_brazil_time.astimezone(brazil_tz)

        while current <= date_to:
            # O CNJ documenta a disponibilização do caderno do dia a partir
            # das 03:00. Antes disso, "Sem comunicações" não é prova de vazio:
            # o shard fica pendente e a OAB atende provisoriamente.
            if (
                current == current_brazil_time.date()
                and current_brazil_time.hour < 3
            ):
                for tribunal in tribunals:
                    incomplete.append(
                        {
                            "caderno": (
                                f"{tribunal}/{current.isoformat()}/{meio}"
                            ),
                            "reason": "Aguardando disponibilização após 03:00.",
                        }
                    )
                stats["unavailable_cadernos"] += len(tribunals)
                current += timedelta(days=1)
                continue
            for tribunal in tribunals:
                context = f"{tribunal}/{current.isoformat()}/{meio}"
                metadata_url = (
                    f"{self.base_url}/api/v1/caderno/"
                    f"{quote(tribunal, safe='-')}/{current.isoformat()}/"
                    f"{quote(meio, safe='')}"
                )
                try:
                    metadata = self._api_json(
                        metadata_url,
                        context=f"caderno {context}",
                    )
                    if not isinstance(metadata, dict):
                        raise DjenCadernoError(
                            f"Metadados do caderno {context} têm formato inválido."
                        )
                    status = _normalized_status(metadata.get("status"))
                    if status == "sem comunicacoes":
                        stats["empty_cadernos"] += 1
                        stats["covered_cadernos"] += 1
                        continue
                    if status != "processado":
                        incomplete.append(
                            {
                                "caderno": context,
                                "reason": (
                                    str(metadata.get("status") or "status ausente")
                                )[:300],
                            }
                        )
                        stats["unavailable_cadernos"] += 1
                        continue
                    archive_url = str(metadata.get("url") or "").strip()
                    if not archive_url:
                        raise DjenCadernoError(
                            f"Caderno processado {context} não informou URL."
                        )
                    archive_hash = str(metadata.get("hash") or "").strip()
                    version = str(metadata.get("versao") or "")
                    expected_pages = _safe_int(
                        metadata.get("numero_paginas"),
                        0,
                    )
                    expected_total = _safe_int(
                        metadata.get("total_comunicacoes"),
                        -1,
                    )
                    cached_items: Optional[list[dict[str, Any]]] = None
                    if self.cache is not None:
                        try:
                            cached_items = self.cache.load(
                                portfolio_fingerprint=portfolio_fingerprint,
                                tribunal=tribunal,
                                reference_date=current,
                                meio=meio,
                                version=version,
                                archive_hash=archive_hash,
                            )
                        except Exception:  # noqa: BLE001
                            logger.exception(
                                "Falha ao consultar checkpoint DJEN %s; "
                                "seguindo com download.",
                                context,
                            )
                    if cached_items is not None:
                        all_items.extend(cached_items)
                        stats["cached_cadernos"] += 1
                        stats["covered_cadernos"] += 1
                        stats["scanned_items"] += max(0, expected_total)
                        stats["selected_items"] += len(cached_items)
                        continue

                    archive, archive_bytes = self._download_archive(
                        archive_url,
                        expected_sha256=archive_hash,
                        remaining_bytes=(
                            self.max_total_download_bytes - downloaded_bytes
                        ),
                        context=context,
                    )
                    try:
                        selected, archive_stats = self._read_archive(
                            archive,
                            portfolio_cnjs=portfolio_cnjs,
                            expected_pages=expected_pages,
                            expected_total=expected_total,
                            expected_tribunal=tribunal,
                            expected_date=current,
                            expected_meio=meio,
                            context=context,
                        )
                    finally:
                        archive.close()
                    downloaded_bytes += archive_bytes
                    if self.cache is not None:
                        self.cache.store(
                            portfolio_fingerprint=portfolio_fingerprint,
                            tribunal=tribunal,
                            reference_date=current,
                            meio=meio,
                            version=version,
                            archive_hash=archive_hash,
                            total_comunicacoes=max(0, expected_total),
                            numero_paginas=max(0, expected_pages),
                            download_bytes=archive_bytes,
                            items=selected,
                        )
                    all_items.extend(selected)
                    stats["processed_cadernos"] += 1
                    stats["covered_cadernos"] += 1
                    stats["scanned_items"] += archive_stats["scanned"]
                    stats["selected_items"] += archive_stats["selected"]
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Falha no caderno DJEN %s.", context)
                    incomplete.append(
                        {
                            "caderno": context,
                            "reason": str(exc)[:500],
                        }
                    )
                    stats["failed_cadernos"] += 1
            current += timedelta(days=1)

        expected_cadernos = len(tribunals) * period_days
        unresolved_count = _safe_int(
            selection_metadata.get("unresolved_tribunal_cnjs_count"),
            0,
        )
        coverage_complete = (
            not incomplete
            and unresolved_count == 0
            and stats["covered_cadernos"] == expected_cadernos
        )
        return all_items, {
            "source": "djen_cadernos",
            "coverage_complete": coverage_complete,
            "period_days": period_days,
            "tribunals": tribunals,
            "tribunals_count": len(tribunals),
            "expected_cadernos": expected_cadernos,
            "covered_cadernos": stats["covered_cadernos"],
            "processed_cadernos": stats["processed_cadernos"],
            "cached_cadernos": stats["cached_cadernos"],
            "empty_cadernos": stats["empty_cadernos"],
            "unavailable_cadernos": stats["unavailable_cadernos"],
            "failed_cadernos": stats["failed_cadernos"],
            "scanned_items": stats["scanned_items"],
            "raw_items": len(all_items),
            "downloaded_bytes": downloaded_bytes,
            "portfolio_fingerprint": portfolio_fingerprint,
            "incomplete_cadernos": incomplete[:100],
            "incomplete_cadernos_count": len(incomplete),
            **selection_metadata,
        }
