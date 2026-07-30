"""Contingência de publicações via DJEN/Comunica.

O fluxo é acionado somente quando o endpoint ``/Updates`` do Legal One encerra
suas tentativas com HTTP 502:

1. baixa o relatório Agenda Analytics já existente no L1 Web;
2. monta o universo de CNJs e escritórios responsáveis;
3. baixa os cadernos dos tribunais/estados relacionados à carteira;
4. usa a consulta por OAB como suplemento rápido se algum caderno não estiver
   disponível (por exemplo, antes das 03h);
5. filtra pelos CNJs do relatório e resolve a pasta atual no cache/API L1;
6. adapta as comunicações ao contrato consumido por PublicationSearchService.

A API Comunica é pública, mas bloqueia o datacenter AWS/EUA. Produção deve
configurar ``DJEN_PROXY`` com saída brasileira.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

import openpyxl
import requests
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.lawsuit_cache import LAWSUIT_CACHE_TTL, LawsuitCache
from app.models.legal_one import LegalOneOffice
from app.models.performance import PerfTarefa
from app.services.djen_caderno_source import (
    DjenCadernoClient,
    DjenCadernoDbCache,
)

logger = logging.getLogger(__name__)

DJEN_SOURCE_PROVIDER = "DJEN"
LEGAL_ONE_SOURCE_PROVIDER = "LEGAL_ONE"
_CNJ_RE = re.compile(r"\D+")
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BREAK_RE = re.compile(r"(?i)<(?:br\s*/?|/p|/div|/li|/tr)>")


class DjenFallbackError(RuntimeError):
    """Falha explícita da contingência; nunca deve virar falso sucesso com zero."""


@dataclass
class PortfolioProcess:
    cnj_digits: str
    office_external_ids: set[int] = field(default_factory=set)
    ufs: set[str] = field(default_factory=set)
    folders: set[str] = field(default_factory=set)
    folder_office_ids: dict[str, set[int]] = field(default_factory=dict)


@dataclass
class DjenFallbackResult:
    publications: list[dict[str, Any]]
    metadata: dict[str, Any]


def cnj_digits(value: Any) -> str:
    digits = _CNJ_RE.sub("", str(value or ""))
    return digits if len(digits) == 20 else ""


def format_cnj(digits: str) -> str:
    if len(digits) != 20:
        return digits
    return (
        f"{digits[0:7]}-{digits[7:9]}.{digits[9:13]}."
        f"{digits[13]}.{digits[14:16]}.{digits[16:20]}"
    )


def text_fingerprint(value: str) -> str:
    normalized = " ".join((value or "").split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def plain_text(value: Any) -> str:
    raw = str(value or "")
    with_breaks = _BREAK_RE.sub("\n", raw)
    without_tags = _TAG_RE.sub(" ", with_breaks)
    decoded = html.unescape(without_tags)
    lines = [_SPACE_RE.sub(" ", line).strip() for line in decoded.splitlines()]
    return "\n".join(line for line in lines if line)


def _normalized_label(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(char for char in raw if not unicodedata.combining(char))
    return " ".join(raw.casefold().split())


def _folder_value(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(str(value or "").split())


def _folder_key(value: Any) -> str:
    return _folder_value(value).casefold()


def _is_auth_redirect(response: Any) -> bool:
    final_url = str(getattr(response, "url", "") or "")
    host = (urlparse(final_url).hostname or "").casefold()
    return bool(host and "legalone" not in host and "thomsonreuters" in host)


def _response_url(response: Any) -> str:
    url = getattr(response, "url", None)
    if url:
        return str(url)
    request = getattr(response, "request", None)
    return str(getattr(request, "url", "") or "")


def is_legal_one_updates_502(exc: BaseException) -> bool:
    """Confirma que o 502 veio especificamente do GET /Updates do L1."""
    if not isinstance(exc, requests.exceptions.HTTPError):
        return False
    response = getattr(exc, "response", None)
    if response is None or getattr(response, "status_code", None) != 502:
        return False
    url = _response_url(response)
    return bool(re.search(r"/Updates(?:[/?]|$)", url, flags=re.IGNORECASE))


def parse_oabs(raw: str | None) -> list[tuple[str, str]]:
    parsed: list[tuple[str, str]] = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        number, uf = item.rsplit(":", 1)
        number = _CNJ_RE.sub("", number)
        uf = uf.strip().upper()
        if number and len(uf) == 2:
            pair = (number, uf)
            if pair not in parsed:
                parsed.append(pair)
    return parsed


def _period_date(value: str, *, end: bool = False) -> date:
    raw = (value or "").strip()
    if not raw:
        raise DjenFallbackError("Período DJEN sem data.")
    try:
        # O filtro do L1 é serializado em UTC, mas a data selecionada pelo
        # operador continua sendo o prefixo YYYY-MM-DD. Converter meia-noite
        # UTC para BRT deslocaria indevidamente a janela para o dia anterior.
        return date.fromisoformat(raw[:10])
    except ValueError as exc:
        label = "final" if end else "inicial"
        raise DjenFallbackError(f"Data {label} inválida para o DJEN: {value}") from exc


class DjenComunicaClient:
    """Cliente paginado/retry-safe, derivado do conector validado no Lake."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        proxy: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        delay_seconds: Optional[float] = None,
        max_pages: Optional[int] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = (base_url or settings.comunica_base_url).rstrip("/")
        self.endpoint = f"{self.base_url}/api/v1/comunicacao"
        self.proxy = (proxy if proxy is not None else settings.djen_proxy) or ""
        self.timeout_seconds = int(
            timeout_seconds or settings.comunica_timeout_seconds or 30
        )
        self.delay_seconds = max(
            0.0,
            float(
                delay_seconds
                if delay_seconds is not None
                else settings.djen_fallback_request_delay_seconds
            ),
        )
        self.max_pages = max(
            1,
            int(max_pages or settings.djen_fallback_max_pages or 200),
        )
        self.session = session or requests.Session()
        if self.proxy:
            self.session.proxies.update(
                {"http": self.proxy, "https": self.proxy}
            )

    def _get_json(self, params: dict[str, Any], tries: int = 4) -> dict[str, Any]:
        last_error: Optional[BaseException] = None
        for attempt in range(1, tries + 1):
            if self.delay_seconds:
                time.sleep(self.delay_seconds)
            response = None
            try:
                response = self.session.get(
                    self.endpoint,
                    params=params,
                    timeout=self.timeout_seconds,
                )
                if response.status_code == 200:
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise DjenFallbackError(
                            "DJEN respondeu JSON em formato inesperado."
                        )
                    status = str(payload.get("status") or "").strip().casefold()
                    if status and status not in {"success", "sucesso"}:
                        raise DjenFallbackError(
                            "DJEN respondeu HTTP 200 com status semântico "
                            f"inválido: {payload.get('status')!r}."
                        )
                    return payload

                if response.status_code == 429 and attempt < tries:
                    try:
                        retry_after = int(response.headers.get("Retry-After") or 0)
                    except (TypeError, ValueError):
                        retry_after = 0
                    # A documentação oficial orienta aguardar um minuto quando
                    # não há Retry-After, evitando um loop de 429.
                    time.sleep(max(0, retry_after) or 60)
                    continue

                if response.status_code in {500, 502, 503, 504} and attempt < tries:
                    time.sleep((2 ** (attempt - 1)) + 1)
                    continue

                body = (response.text or "").strip()[:500]
                raise DjenFallbackError(
                    f"DJEN HTTP {response.status_code} em {self.endpoint}: {body}"
                )
            except DjenFallbackError:
                raise
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt >= tries:
                    break
                time.sleep(2 ** (attempt - 1))

        raise DjenFallbackError(
            f"DJEN indisponível após {tries} tentativas: {last_error or 'erro desconhecido'}"
        ) from last_error

    def fetch_by_oabs(
        self,
        *,
        oabs: Iterable[tuple[str, str]],
        date_from: date,
        date_to: date,
        meio: str = "D",
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        all_items: list[dict[str, Any]] = []
        pages = 0
        capped: list[str] = []
        query_limit_reached: list[str] = []

        for number, uf in oabs:
            finished = False
            for page in range(1, self.max_pages + 1):
                body = self._get_json(
                    {
                        "numeroOab": number,
                        "ufOab": uf,
                        "dataDisponibilizacaoInicio": date_from.isoformat(),
                        "dataDisponibilizacaoFim": date_to.isoformat(),
                        "meio": meio,
                        "itensPorPagina": 100,
                        "pagina": page,
                    }
                )
                pages += 1
                if page == 1:
                    try:
                        reported_count = int(body.get("count"))
                    except (TypeError, ValueError):
                        reported_count = 0
                    # Consultas por OAB têm teto oficial de 10.000 resultados.
                    # Preserva o lote recebido, mas sinaliza que pode haver
                    # truncamento e que a reconciliação L1 é indispensável.
                    if reported_count >= 10_000:
                        query_limit_reached.append(f"{number}/{uf}")
                items = body.get("items") or []
                if not isinstance(items, list):
                    raise DjenFallbackError(
                        f"DJEN retornou items inválido para OAB {number}/{uf}."
                    )
                if not items:
                    finished = True
                    break
                all_items.extend(item for item in items if isinstance(item, dict))
                if len(items) < 100:
                    finished = True
                    break
            if not finished:
                capped.append(f"{number}/{uf}")

        if capped:
            raise DjenFallbackError(
                "DJEN atingiu o limite de paginação sem cobrir todo o período "
                f"para: {', '.join(capped)}."
            )

        return all_items, {
            "pages": pages,
            "raw_items": len(all_items),
            "oabs": [f"{number}/{uf}" for number, uf in oabs],
            "query_limit_reached": query_limit_reached,
        }


class DjenPublicationFallback:
    def __init__(
        self,
        db: Session,
        legal_one_client: Any,
        *,
        comunica_client: Optional[DjenComunicaClient] = None,
        caderno_client: Optional[DjenCadernoClient] = None,
    ) -> None:
        self.db = db
        self.legal_one_client = legal_one_client
        self.comunica_client = comunica_client or DjenComunicaClient()
        self.caderno_client = caderno_client or DjenCadernoClient(
            cache=DjenCadernoDbCache(db)
        )

    def _office_paths(self) -> dict[str, int]:
        rows = self.db.query(
            LegalOneOffice.external_id,
            LegalOneOffice.path,
            LegalOneOffice.name,
        ).all()
        result: dict[str, int] = {}
        leaf_names: dict[str, int] = {}
        ambiguous_leaf_names: set[str] = set()
        for external_id, path, name in rows:
            external_id = int(external_id)
            normalized_path = " ".join(str(path or "").split()).casefold()
            if normalized_path:
                result.setdefault(normalized_path, external_id)

            normalized_name = " ".join(str(name or "").split()).casefold()
            if not normalized_name:
                continue
            previous = leaf_names.get(normalized_name)
            if previous is None:
                leaf_names[normalized_name] = external_id
            elif previous != external_id:
                ambiguous_leaf_names.add(normalized_name)

        # Só aceita o nome-folha quando ele identifica um único escritório.
        # Caminhos completos sempre têm prioridade; nomes repetidos não podem
        # cair arbitrariamente no primeiro registro encontrado.
        for normalized_name, external_id in leaf_names.items():
            if (
                normalized_name not in ambiguous_leaf_names
                and normalized_name not in result
            ):
                result[normalized_name] = external_id
        return result

    @staticmethod
    def _add_portfolio_row(
        portfolio: dict[str, PortfolioProcess],
        office_paths: dict[str, int],
        *,
        cnj: Any,
        office_path: Any,
        uf: Any,
        folder: Any,
    ) -> None:
        digits = cnj_digits(cnj)
        if not digits:
            return
        entry = portfolio.setdefault(digits, PortfolioProcess(cnj_digits=digits))
        normalized_path = " ".join(str(office_path or "").split()).casefold()
        office_id = office_paths.get(normalized_path)
        if office_id is not None:
            entry.office_external_ids.add(office_id)
        uf_text = str(uf or "").strip()
        if uf_text:
            entry.ufs.add(uf_text)
        folder_text = _folder_value(folder)
        if folder_text:
            entry.folders.add(folder_text)
            if office_id is not None:
                entry.folder_office_ids.setdefault(
                    _folder_key(folder_text),
                    set(),
                ).add(office_id)

    def _portfolio_from_latest_report(
        self,
        office_paths: dict[str, int],
    ) -> tuple[dict[str, PortfolioProcess], dict[str, Any]]:
        from app.services.performance import report_ingest
        from app.services.performance.seed import CNJ, ESC, PASTA, UF
        from app.services.prazos_iniciais.legacy_task_helpers import web_base_url

        base = web_base_url()
        session = None
        report = None
        for auth_attempt in range(2):
            session = report_ingest._session()
            report = report_ingest._find_latest(session, base)
            if report:
                break
            if auth_attempt == 0:
                from app.services.prazos_iniciais.legacy_task_http_cancellation_service import (
                    LegacyTaskHttpCancellationService,
                )

                logger.warning(
                    "Agenda Analytics não apareceu com a sessão atual; "
                    "invalidando o cookie e autenticando novamente."
                )
                LegacyTaskHttpCancellationService._invalidate_session(None)
        if not report:
            raise DjenFallbackError(
                "Agenda Analytics não encontrado no Legal One."
            )
        try:
            report_date = datetime.strptime(
                str(report.get("data") or ""),
                "%d/%m/%Y",
            ).replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise DjenFallbackError(
                f"Agenda Analytics sem data válida: {report.get('data')!r}."
            ) from exc
        maximum_age = timedelta(
            hours=max(
                1,
                int(settings.djen_fallback_max_report_age_hours or 168),
            )
        )
        if datetime.now(timezone.utc) - report_date > maximum_age:
            raise DjenFallbackError(
                "Agenda Analytics está vencido para contingência: "
                f"{report.get('data')} (limite {int(maximum_age.total_seconds() // 3600)}h)."
            )
        response = session.get(
            f"{base}/shared/ReportShared/GetFile/{report['id']}",
            timeout=300,
        )
        try:
            response.raise_for_status()
            if _is_auth_redirect(response):
                raise DjenFallbackError(
                    "Download do Agenda Analytics redirecionou para a autenticação."
                )
            if not bytes(response.content or b"").startswith(b"PK"):
                raise DjenFallbackError(
                    "Download do Agenda Analytics não retornou um XLSX válido."
                )
        except Exception as first_download_error:
            # A listagem pode responder mesmo quando o cookie do GetFile
            # venceu. Renova a sessão web e repete uma vez antes de falhar.
            from app.services.prazos_iniciais.legacy_task_http_cancellation_service import (
                LegacyTaskHttpCancellationService,
            )

            logger.warning(
                "Download do Agenda Analytics falhou; renovando a sessão web "
                "e tentando novamente: %s",
                first_download_error,
            )
            LegacyTaskHttpCancellationService._invalidate_session(None)
            session = report_ingest._session()
            response = session.get(
                f"{base}/shared/ReportShared/GetFile/{report['id']}",
                timeout=300,
            )
            response.raise_for_status()
            if _is_auth_redirect(response):
                raise DjenFallbackError(
                    "Download do Agenda Analytics continuou redirecionando "
                    "para a autenticação após renovar a sessão."
                ) from first_download_error
            if not bytes(response.content or b"").startswith(b"PK"):
                raise DjenFallbackError(
                    "Download do Agenda Analytics não retornou um XLSX "
                    "válido após renovar a sessão."
                ) from first_download_error

        workbook = openpyxl.load_workbook(
            BytesIO(response.content),
            read_only=True,
            data_only=True,
        )
        portfolio: dict[str, PortfolioProcess] = {}
        try:
            sheet = workbook.active
            rows = sheet.iter_rows(values_only=True)
            header = next(rows, None)
            if not header:
                raise DjenFallbackError("Agenda Analytics sem cabeçalho.")
            expected_headers = {
                ESC: ("escritorio",),
                PASTA: ("pasta",),
                CNJ: ("cnj", "processo"),
                UF: ("uf",),
            }
            invalid_headers = []
            for index, expected in expected_headers.items():
                actual = _normalized_label(
                    header[index] if len(header) > index else ""
                )
                if not any(token in actual for token in expected):
                    invalid_headers.append(
                        f"coluna {index + 1}={actual or '<vazia>'}"
                    )
            if invalid_headers:
                raise DjenFallbackError(
                    "Layout do Agenda Analytics mudou: "
                    + ", ".join(invalid_headers)
                )
            for row in rows:
                if not row:
                    continue
                self._add_portfolio_row(
                    portfolio,
                    office_paths,
                    cnj=row[CNJ] if len(row) > CNJ else None,
                    office_path=row[ESC] if len(row) > ESC else None,
                    uf=row[UF] if len(row) > UF else None,
                    folder=row[PASTA] if len(row) > PASTA else None,
                )
        finally:
            workbook.close()

        return portfolio, {
            "source": "agenda_analytics_report",
            "report_id": str(report["id"]),
            "report_title": report.get("title"),
            "report_date": report.get("data"),
            "report_bytes": len(response.content),
        }

    def _portfolio_from_snapshot(
        self,
        office_paths: dict[str, int],
    ) -> tuple[dict[str, PortfolioProcess], dict[str, Any]]:
        portfolio: dict[str, PortfolioProcess] = {}
        rows = self.db.query(
            PerfTarefa.cnj,
            PerfTarefa.escritorio,
            PerfTarefa.uf,
            PerfTarefa.pasta,
        ).yield_per(5000)
        for cnj, office_path, uf, folder in rows:
            self._add_portfolio_row(
                portfolio,
                office_paths,
                cnj=cnj,
                office_path=office_path,
                uf=uf,
                folder=folder,
            )
        return portfolio, {
            "source": "perf_l1_tarefa_snapshot",
            "report_id": None,
            "report_title": "Agenda Analytics (snapshot local)",
            "report_date": None,
        }

    def _load_portfolio(
        self,
    ) -> tuple[dict[str, PortfolioProcess], dict[str, Any]]:
        office_paths = self._office_paths()
        portfolio: dict[str, PortfolioProcess] = {}
        metadata: dict[str, Any] = {}
        report_error = None

        if settings.djen_fallback_refresh_report:
            try:
                portfolio, metadata = self._portfolio_from_latest_report(
                    office_paths
                )
            except Exception as exc:  # noqa: BLE001
                report_error = str(exc)
                logger.exception(
                    "Fallback DJEN: falha ao baixar/ler Agenda Analytics; "
                    "tentando snapshot local."
                )

        if not portfolio and settings.djen_fallback_allow_filtered_snapshot:
            portfolio, metadata = self._portfolio_from_snapshot(office_paths)
            if report_error:
                metadata["report_refresh_error"] = report_error[:1000]
        elif not portfolio:
            raise DjenFallbackError(
                "Não foi possível obter a carteira bruta do Agenda Analytics; "
                f"snapshot filtrado desabilitado. Erro: {report_error or 'relatório vazio'}"
            )

        minimum = max(
            1,
            int(settings.djen_fallback_min_portfolio_processes or 100),
        )
        if len(portfolio) < minimum:
            raise DjenFallbackError(
                "Universo de processos insuficiente para fallback DJEN: "
                f"{len(portfolio)} CNJs (mínimo {minimum})."
            )
        metadata["portfolio_cnjs"] = len(portfolio)
        return portfolio, metadata

    def _resolve_lawsuits(
        self,
        cnjs: set[str],
        portfolio: dict[str, PortfolioProcess],
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
        resolved: dict[str, dict[int, dict[str, Any]]] = {}
        if not cnjs:
            return {}, {
                "folder_hits": 0,
                "cache_hits": 0,
                "api_hits": 0,
                "unresolved": 0,
                "multiple_lawsuit_cnjs": 0,
            }

        def add_match(
            digits: str,
            payload: dict[str, Any],
            *,
            report_office_ids: Optional[set[int]] = None,
        ) -> bool:
            if digits not in cnjs or not payload:
                return False
            try:
                lawsuit_id = int(payload.get("id"))
            except (TypeError, ValueError):
                return False
            enriched = {**payload, "id": lawsuit_id}
            if report_office_ids:
                enriched["_report_office_ids"] = sorted(report_office_ids)
            bucket = resolved.setdefault(digits, {})
            if lawsuit_id in bucket:
                existing_report_ids = set(
                    bucket[lawsuit_id].get("_report_office_ids") or []
                )
                existing_report_ids.update(report_office_ids or set())
                if existing_report_ids:
                    bucket[lawsuit_id]["_report_office_ids"] = sorted(
                        existing_report_ids
                    )
                return False
            bucket[lawsuit_id] = enriched
            return True

        # O relatório contém o número da pasta, que é a chave mais segura para
        # CNJs repetidos em mais de um processo/escritório. Resolve todas as
        # pastas candidatas em lote antes de recorrer ao CNJ.
        folder_to_cnjs: dict[str, set[str]] = {}
        folder_originals: dict[str, str] = {}
        for digits in cnjs:
            for folder in portfolio[digits].folders:
                key = _folder_key(folder)
                if key:
                    folder_to_cnjs.setdefault(key, set()).add(digits)
                    folder_originals.setdefault(key, folder)

        folder_hits = 0
        if folder_originals:
            try:
                folder_matches = (
                    self.legal_one_client.search_lawsuits_by_folder_numbers(
                        list(folder_originals.values())
                    )
                    or {}
                )
                for returned_key, payload in folder_matches.items():
                    key = _folder_key(returned_key)
                    target_cnjs = folder_to_cnjs.get(key, set())
                    payload_digits = cnj_digits(
                        (payload or {}).get("identifierNumber")
                    )
                    if payload_digits in cnjs:
                        target_cnjs = {payload_digits}
                    for digits in target_cnjs:
                        report_ids = portfolio[
                            digits
                        ].folder_office_ids.get(key, set())
                        if add_match(
                            digits,
                            payload or {},
                            report_office_ids=report_ids,
                        ):
                            folder_hits += 1
            except Exception:
                logger.exception(
                    "Fallback DJEN: lookup por %s pastas no L1 falhou.",
                    len(folder_originals),
                )

        # Cache só é aceito dentro do TTL oficial de 24h. Mantemos todas as
        # pastas de um mesmo CNJ; nunca escolhemos a primeira arbitrariamente.
        cutoff = datetime.now(timezone.utc) - LAWSUIT_CACHE_TTL
        cache_hits = 0
        for lawsuit_id, payload in self.db.query(
            LawsuitCache.lawsuit_id,
            LawsuitCache.payload,
        ).filter(LawsuitCache.fetched_at >= cutoff).all():
            payload = payload or {}
            digits = cnj_digits(payload.get("identifierNumber"))
            payload = {**payload, "id": payload.get("id") or lawsuit_id}
            if add_match(digits, payload):
                cache_hits += 1

        missing = sorted(cnjs - set(resolved))
        api_hits = 0
        if missing:
            try:
                api_matches = self.legal_one_client.search_lawsuits_by_cnj_numbers(
                    missing
                )
                for requested, payload in (api_matches or {}).items():
                    digits = cnj_digits(requested) or cnj_digits(
                        (payload or {}).get("identifierNumber")
                    )
                    if digits and add_match(digits, payload or {}):
                        api_hits += 1
            except Exception:
                logger.exception(
                    "Fallback DJEN: lookup complementar de %s CNJs no L1 falhou; "
                    "seguindo apenas com o cache.",
                    len(missing),
                )

        normalized_resolved = {
            digits: list(matches.values())
            for digits, matches in resolved.items()
        }
        return normalized_resolved, {
            "folder_hits": folder_hits,
            "cache_hits": cache_hits,
            "api_hits": api_hits,
            "unresolved": len(cnjs - set(resolved)),
            "multiple_lawsuit_cnjs": sum(
                1 for matches in resolved.values() if len(matches) > 1
            ),
        }

    @staticmethod
    def _logical_deduplicate(
        raw_items: Iterable[dict[str, Any]],
        portfolio: dict[str, PortfolioProcess],
    ) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, int]]:
        seen_hashes: set[str] = set()
        seen_content: set[tuple[str, str, str]] = set()
        kept: list[tuple[str, dict[str, Any]]] = []
        stats = Counter()

        for item in raw_items:
            if item.get("motivo_cancelamento") or item.get("ativo") is False:
                stats["cancelled"] += 1
                continue
            digits = cnj_digits(
                item.get("numero_processo")
                or item.get("numeroProcesso")
                or item.get("numeroprocessocommascara")
            )
            if not digits or digits not in portfolio:
                stats["outside_portfolio"] += 1
                continue
            source_id = str(item.get("hash") or item.get("id") or "").strip()
            if not source_id:
                stats["without_id"] += 1
                continue
            if source_id in seen_hashes:
                stats["duplicate_hash"] += 1
                continue
            publication_date = str(
                item.get("data_disponibilizacao")
                or item.get("dataDisponibilizacao")
                or ""
            )[:10]
            text = plain_text(item.get("texto"))
            logical_key = (
                (digits, publication_date, text_fingerprint(text))
                if text
                else None
            )
            if logical_key is not None and logical_key in seen_content:
                stats["duplicate_content"] += 1
                continue
            seen_hashes.add(source_id)
            if logical_key is not None:
                seen_content.add(logical_key)
            kept.append((digits, item))

        stats["kept"] = len(kept)
        return kept, dict(stats)

    @staticmethod
    def _adapt_publication(
        digits: str,
        raw: dict[str, Any],
        lawsuit: dict[str, Any],
    ) -> dict[str, Any]:
        source_id = str(raw.get("hash") or raw.get("id"))
        lawsuit_id = int(lawsuit["id"])
        office_id = lawsuit.get("responsibleOfficeId")
        publication_date = str(
            raw.get("data_disponibilizacao")
            or raw.get("dataDisponibilizacao")
            or ""
        )[:10]
        tribunal = str(raw.get("siglaTribunal") or "").strip()
        communication_type = str(raw.get("tipoComunicacao") or "").strip()
        organ = str(raw.get("nomeOrgao") or "").strip()
        title_parts = [
            part for part in (communication_type, tribunal, organ) if part
        ]
        publication_text = plain_text(raw.get("texto"))
        source_title = " · ".join(title_parts) or "Publicação DJEN"
        relationships = [
            {"linkType": "Litigation", "linkId": lawsuit_id},
            {
                "linkType": "Source",
                "linkId": str(raw.get("id") or source_id),
                "source": DJEN_SOURCE_PROVIDER,
                "hash": source_id,
            },
        ]

        return {
            "id": None,
            "originType": DJEN_SOURCE_PROVIDER,
            "typeId": None,
            # O classificador usa `description` como corpo jurídico.
            "description": publication_text,
            "notes": source_title,
            "date": publication_date,
            "creationDate": (
                f"{publication_date}T00:00:00Z" if publication_date else None
            ),
            "relationships": relationships,
            "_source_provider": DJEN_SOURCE_PROVIDER,
            "_source_external_id": source_id,
            "_ingestion_key": f"DJEN:{source_id}:{lawsuit_id}",
            "_raw_source": raw,
            "_content_text": publication_text,
            "_djen_fallback": True,
            "_cnj": format_cnj(digits),
            "_responsible_office_id": (
                int(office_id) if office_id is not None else None
            ),
            "_lawsuit_creation_date": lawsuit.get("creationDate"),
        }

    def fetch(
        self,
        *,
        date_from: str,
        date_to: str,
        responsible_office_ids: Optional[list[int]] = None,
    ) -> DjenFallbackResult:
        portfolio, report_metadata = self._load_portfolio()
        start = _period_date(date_from)
        end = _period_date(date_to, end=True)
        if end < start:
            raise DjenFallbackError(
                f"Período DJEN invertido: {start}..{end}."
            )

        raw_items: list[dict[str, Any]] = []
        caderno_metadata: dict[str, Any] = {}
        caderno_error: Optional[str] = None
        caderno_succeeded = False
        if settings.djen_fallback_cadernos_enabled:
            try:
                caderno_items, caderno_metadata = (
                    self.caderno_client.fetch_by_portfolio(
                        portfolio=portfolio,
                        date_from=start,
                        date_to=end,
                        meio=settings.djen_default_meio,
                    )
                )
                raw_items.extend(caderno_items)
                caderno_succeeded = True
            except Exception as exc:  # noqa: BLE001
                caderno_error = str(exc)[:2000]
                logger.exception(
                    "Fallback DJEN: cobertura por cadernos falhou; "
                    "tentando suplemento por OAB."
                )

        coverage_complete = bool(
            caderno_succeeded
            and caderno_metadata.get("coverage_complete") is True
        )
        oab_metadata: dict[str, Any] = {}
        oab_error: Optional[str] = None
        oab_succeeded = False
        oabs = parse_oabs(settings.djen_fallback_oabs)
        if not coverage_complete and oabs:
            try:
                oab_items, oab_metadata = self.comunica_client.fetch_by_oabs(
                    oabs=oabs,
                    date_from=start,
                    date_to=end,
                    meio=settings.djen_default_meio,
                )
                raw_items.extend(oab_items)
                oab_succeeded = True
            except Exception as exc:  # noqa: BLE001
                oab_error = str(exc)[:2000]
                logger.exception(
                    "Fallback DJEN: suplemento por OAB também falhou."
                )

        covered_cadernos = int(
            caderno_metadata.get("covered_cadernos") or 0
        )
        if (
            not coverage_complete
            and covered_cadernos == 0
            and not oab_succeeded
        ):
            reasons = [
                reason
                for reason in (
                    f"cadernos: {caderno_error}" if caderno_error else None,
                    f"OAB: {oab_error}" if oab_error else None,
                    (
                        "consulta por OAB não configurada"
                        if not oabs
                        else None
                    ),
                )
                if reason
            ]
            raise DjenFallbackError(
                "Nenhuma fonte DJEN conseguiu cobrir o período. "
                + "; ".join(reasons)
            )

        candidates, dedup_metadata = self._logical_deduplicate(
            raw_items,
            portfolio,
        )
        candidate_cnjs = {digits for digits, _ in candidates}
        lawsuits, lookup_metadata = self._resolve_lawsuits(
            candidate_cnjs,
            portfolio,
        )

        selected = {
            int(value)
            for value in (responsible_office_ids or [])
            if value is not None
        }
        publications: list[dict[str, Any]] = []
        skipped_unresolved = 0
        skipped_office = 0
        jurisdictions = Counter()

        for digits, raw in candidates:
            lawsuit_options = lawsuits.get(digits) or []
            if not lawsuit_options:
                skipped_unresolved += 1
                continue
            for lawsuit in lawsuit_options:
                office_id = lawsuit.get("responsibleOfficeId")
                try:
                    office_id = (
                        int(office_id) if office_id is not None else None
                    )
                except (TypeError, ValueError):
                    office_id = None
                if office_id is None:
                    mapped = set(lawsuit.get("_report_office_ids") or [])
                    if not mapped:
                        mapped = portfolio[digits].office_external_ids
                    allowed = mapped & selected if selected else mapped
                    if len(allowed) == 1:
                        lawsuit = {
                            **lawsuit,
                            "responsibleOfficeId": next(iter(allowed)),
                        }
                        office_id = lawsuit["responsibleOfficeId"]
                if selected and office_id not in selected:
                    skipped_office += 1
                    continue

                adapted = self._adapt_publication(digits, raw, lawsuit)
                publications.append(adapted)
                jurisdictions[
                    str(raw.get("siglaTribunal") or "SEM_TRIBUNAL")
                ] += 1

        query_limit_reached = oab_metadata.get("query_limit_reached") or []
        expected_cadernos = int(
            caderno_metadata.get("expected_cadernos") or 0
        )
        incomplete_cadernos = int(
            caderno_metadata.get("incomplete_cadernos_count") or 0
        )
        if coverage_complete:
            coverage_mode = "portfolio_cadernos"
            coverage_note = (
                "Cadernos DJEN integralmente verificados para os tribunais, "
                "estados e CNJs da carteira do Agenda Analytics; "
                "reconciliação Legal One obrigatória."
            )
        else:
            coverage_mode = (
                "portfolio_cadernos_plus_oab_partial"
                if caderno_succeeded
                else "oab_portfolio_filter"
            )
            coverage_note = (
                "Cobertura DJEN parcial: "
                f"{covered_cadernos}/{expected_cadernos or '?'} cadernos "
                "verificados"
            )
            if oab_succeeded:
                coverage_note += ", complementada pela OAB configurada"
            coverage_note += "; reconciliação Legal One obrigatória."
        if incomplete_cadernos:
            coverage_note += (
                f" {incomplete_cadernos} caderno(s) ainda indisponível(is) "
                "ou inválido(s)."
            )
        if caderno_error:
            coverage_note += " A etapa de cadernos terminou com erro."
        if oab_error:
            coverage_note += " A consulta suplementar por OAB terminou com erro."
        if query_limit_reached:
            coverage_note += (
                " A consulta alcançou o teto oficial de 10.000 resultados "
                f"para: {', '.join(query_limit_reached)}."
            )

        metadata = {
            "provider": DJEN_SOURCE_PROVIDER,
            "fallback_used": True,
            "coverage_complete": coverage_complete,
            "coverage_mode": coverage_mode,
            "coverage_note": coverage_note,
            "period_from": start.isoformat(),
            "period_to": end.isoformat(),
            "publications": len(publications),
            "raw_items": len(raw_items),
            "jurisdictions": dict(sorted(jurisdictions.items())),
            "skipped_unresolved": skipped_unresolved,
            "skipped_office": skipped_office,
            "cadernos": caderno_metadata,
            "caderno_error": caderno_error,
            "oab": oab_metadata,
            "oab_error": oab_error,
            "query_limit_reached": query_limit_reached,
            **report_metadata,
            "dedup": dedup_metadata,
            "lawsuit_lookup": lookup_metadata,
        }
        logger.warning(
            "Fallback DJEN concluído: %s publicações, %s CNJs no relatório, "
            "%s itens brutos, tribunais=%s.",
            len(publications),
            metadata.get("portfolio_cnjs"),
            metadata.get("raw_items"),
            metadata.get("jurisdictions"),
        )
        return DjenFallbackResult(publications=publications, metadata=metadata)
