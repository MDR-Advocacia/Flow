"""
Serviço de busca e gestão de publicações do Legal One.

Responsabilidades:
- Disparar buscas de publicações via API
- Enriquecer publicações com escritório responsável (via lookup de processos)
- Auto-classificar cada publicação via IA
- Auto-montar proposta de tarefa baseada em template
- Persistir resultados com deduplicação
- Fornecer CRUD, agrupamento e contagens para o painel de controle
- Ponte para o agendamento (enviar proposta para o LegalOne após revisão do operador)
"""

import asyncio
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy import func as sa_func, literal_column, case, or_
from sqlalchemy.orm import Session, joinedload

from app.models.publication_search import (
    RECORD_STATUS_CLASSIFIED,
    RECORD_STATUS_ERROR,
    RECORD_STATUS_IGNORED,
    RECORD_STATUS_NEW,
    RECORD_STATUS_OBSOLETE,
    RECORD_STATUS_SCHEDULED,
    RECORD_STATUS_DISCARDED_DUPLICATE,
    SEARCH_STATUS_CANCELLED,
    SEARCH_STATUS_COMPLETED,
    SEARCH_STATUS_FAILED,
    SEARCH_STATUS_RUNNING,
    VALID_POLOS,
    PublicationRecord,
    PublicationSearch,
)
from app.services.legal_one_client import LegalOneApiClient

logger = logging.getLogger(__name__)

_METRICS_TZ = ZoneInfo("America/Fortaleza")

# ── Checagem de duplicatas no L1 (agendamento) ─────────────────────────
# Status IDs do Legal One. Mapeamento validado em 2026-04-30 via
# scripts/probe_l1_task_statuses.py (lawsuit 25878, uma task de cada
# status, ids cruzados com a UI web L1):
#   0 = Pendente       (azul)      -> bloqueia
#   1 = Cumprido       (verde-es)  -> terminal
#   2 = Nao cumprido   (laranja)   -> terminal
#   3 = Cancelado      (cinza)     -> terminal
#   4 = Iniciado       (verde-cl)  -> bloqueia
#   5 = Reagendado     (vermelho)  -> terminal
# Bloqueantes = tasks "em curso" (Pendente + Iniciado). Os demais sao
# fechamentos (cumprida/nao cumprida/cancelada/reagendada) e nao impedem
# novo agendamento do mesmo subtipo no processo. Mapeamento anterior
# (0=Pendente, 1=Em Andamento, 2=Aguardando, 3=Concluida, ...) era
# inferido e estava errado: tasks Cumpridas (id=1) apareciam como "Em
# Andamento" no detalhe da publicacao E bloqueavam novos agendamentos.
L1_BLOCKING_STATUS_IDS = (0, 4)
L1_STATUS_LABELS = {0: "Pendente", 4: "Iniciado"}

# Mapeamento completo pro endpoint de "tarefas do processo" (detalhe da
# publicacao. Pra IDs fora do mapa o caller usa fallback "Status N".
L1_STATUS_LABELS_FULL = {
    0: "Pendente",
    1: "Cumprido",
    2: "Não cumprido",
    3: "Cancelado",
    4: "Iniciado",
    5: "Reagendado",
}

# Base URL do painel web do L1 do MDR. Usada pra gerar deep-link que o
# operador abre numa nova aba pra ver a task já existente antes de decidir
# se agenda mesmo assim (force_duplicate=True) ou se remove da lista.
L1_WEB_BASE_URL = "https://mdradvocacia.novajus.com.br"

# Cache in-memory do check-duplicates: evita martelar o L1 quando o usuário
# abre/fecha a modal várias vezes em sequência. TTL curto porque a vida útil
# é literalmente "um session de revisão".
_DUPLICATE_CACHE: dict[tuple, tuple[float, list]] = {}
_DUPLICATE_CACHE_TTL_SECONDS = 15.0

# Cache do "recent tasks" pro detalhe da publicação. Chave = lawsuit_id (sem
# subtipos), TTL idêntico — operador abre/fecha modal de detalhe sequencialmente
# e bater no L1 toda vez é desperdício. Limpeza oportunística junto com
# _DUPLICATE_CACHE no fim de get_recent_tasks_for_lawsuit.
_RECENT_TASKS_CACHE: dict[int, tuple[float, list]] = {}
_RECENT_TASKS_CACHE_TTL_SECONDS = 15.0
_WITHOUT_PROVIDENCE_STATUSES = (
    RECORD_STATUS_IGNORED,
    RECORD_STATUS_DISCARDED_DUPLICATE,
    RECORD_STATUS_OBSOLETE,
)


# ── UF derivation from CNJ ──────────────────────────────────────────────
# Espelha a lógica de frontend/src/pages/PublicationsPage.tsx::ufFromCnj.
# Formato CNJ: NNNNNNN-DD.AAAA.J.TR.OOOO (20 dígitos sem pontuação).
_UF_ESTADUAL = {
    "01": "AC", "02": "AL", "03": "AP", "04": "AM", "05": "BA", "06": "CE",
    "07": "DF", "08": "ES", "09": "GO", "10": "MA", "11": "MT", "12": "MS",
    "13": "MG", "14": "PA", "15": "PB", "16": "PR", "17": "PE", "18": "PI",
    "19": "RJ", "20": "RN", "21": "RS", "22": "RO", "23": "RR", "24": "SC",
    "25": "SP", "26": "SE", "27": "TO",
}


def uf_from_cnj(cnj: Optional[str]) -> Optional[str]:
    """Extrai UF/região a partir do CNJ. Retorna None se padrão não bate."""
    if not cnj:
        return None
    digits = "".join(c for c in cnj if c.isdigit())
    if len(digits) != 20:
        return None
    j = digits[13]
    tr = digits[14:16]
    if j == "8":
        return _UF_ESTADUAL.get(tr)
    if j == "4":
        try:
            return f"TRF{int(tr)}"
        except ValueError:
            return f"TRF{tr}"
    if j == "7":
        try:
            return f"TRT{int(tr)}"
        except ValueError:
            return f"TRT{tr}"
    if j == "5":
        try:
            return f"JME{int(tr)}"
        except ValueError:
            return f"JME{tr}"
    if j == "6":
        base = _UF_ESTADUAL.get(tr, tr)
        return f"TRE-{base}"
    return None


# ── Extração de CNJ a partir do texto da publicação ────────────────────
# Fallback usado quando a publicação não vem com `Litigation` relationship
# no Legal One (publicações avulsas). O DJE quase sempre imprime o CNJ
# no cabeçalho ou no corpo — varremos ambos.
#
# Formato canônico CNJ: NNNNNNN-DD.AAAA.J.TR.OOOO
#   N = número sequencial (7)  D = DV (2)  A = ano (4)
#   J = segmento do Judiciário (1)  T = tribunal (2)  O = origem (4)
_CNJ_FORMATTED_RE = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")
_CNJ_DIGITS_RE = re.compile(r"(?<!\d)\d{20}(?!\d)")
# Aceita variações comuns com espaços estranhos entre os grupos (ex.: PDF mal
# transcrito). Mantemos separado do canônico pra não encarecer o caso comum.
_CNJ_LOOSE_RE = re.compile(
    r"\b\d{7}\s*-\s*\d{2}\s*\.\s*\d{4}\s*\.\s*\d\s*\.\s*\d{2}\s*\.\s*\d{4}\b"
)


def _format_cnj_digits(digits: str) -> str:
    """Formata 20 dígitos no padrão canônico NNNNNNN-DD.AAAA.J.TR.OOOO."""
    return (
        f"{digits[0:7]}-{digits[7:9]}.{digits[9:13]}."
        f"{digits[13]}.{digits[14:16]}.{digits[16:20]}"
    )


def extract_cnj_from_text(text: Optional[str]) -> Optional[str]:
    """
    Tenta extrair um CNJ do texto livre de uma publicação (cabeçalho ou corpo).
    Retorna o CNJ no formato canônico (NNNNNNN-DD.AAAA.J.TR.OOOO) ou None.

    Estratégia em camadas:
      1. Match no formato canônico (mais seguro, menos falso positivo).
      2. Match com whitespace entre os grupos (PDFs com OCR sujo).
      3. 20 dígitos consecutivos — valida que o 14º dígito (segmento J) é
         1-9, eliminando sequências numéricas aleatórias.

    Em todos os casos, retorna-se sempre o primeiro match encontrado, que
    costuma ser o do cabeçalho / referência principal da publicação.
    """
    if not text:
        return None

    m = _CNJ_FORMATTED_RE.search(text)
    if m:
        return m.group(0)

    m = _CNJ_LOOSE_RE.search(text)
    if m:
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) == 20 and digits[13] in "123456789":
            return _format_cnj_digits(digits)

    m = _CNJ_DIGITS_RE.search(text)
    if m:
        digits = m.group(0)
        if digits[13] in "123456789":
            return _format_cnj_digits(digits)

    return None


def _parse_csv_ints(raw) -> list:
    """Aceita int, string CSV ("61,62") ou None; retorna lista de ints."""
    if raw is None:
        return []
    if isinstance(raw, int):
        return [raw]
    if isinstance(raw, str):
        return [int(p.strip()) for p in raw.split(",") if p.strip()]
    if isinstance(raw, (list, tuple)):
        return [int(x) for x in raw]
    return []


def _parse_csv_strs(raw) -> list:
    """Aceita string CSV, lista ou None; retorna lista de strings não-vazias."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw if x]
    return [p.strip() for p in str(raw).split(",") if p.strip()]


class PublicationSearchService:

    def __init__(self, db: Session, client: LegalOneApiClient):
        self.db = db
        self.client = client

    # ──────────────────────────────────────────────
    # Progress helper
    # ──────────────────────────────────────────────

    def _update_search_progress(self, search, step: str, detail: str, pct: int):
        """Atualiza progresso intermediário da busca (commit imediato)."""
        search.progress_step = step
        search.progress_detail = detail
        search.progress_pct = min(pct, 100)
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()

    # ──────────────────────────────────────────────
    # Disparo de busca
    # ──────────────────────────────────────────────

    def fetch_publications_for_window(
        self,
        date_from: str,
        date_to: Optional[str] = None,
        origin_type: str = "OfficialJournalsCrawler",
    ) -> list[dict]:
        """
        Faz a chamada paginada ao Legal One pra trazer todas as publicações
        do período. Usado pelo scheduler em modo batch (1 fetch + fan-out)
        pra evitar paginar o L1 N vezes (uma por escritório).

        O resultado pode ser passado como `prefetched_publications` em
        chamadas subsequentes a `create_and_run_search`.
        """
        return self.client.fetch_all_publications(
            date_from=date_from,
            date_to=date_to,
            origin_type=origin_type,
        )

    def create_and_run_search(
        self,
        date_from: str,
        date_to: Optional[str] = None,
        origin_type: str = "OfficialJournalsCrawler",
        responsible_office_id: Optional[int] = None,
        responsible_office_ids: Optional[list[int]] = None,
        auto_classify: bool = False,
        requested_by: Optional[str] = None,
        only_unlinked: bool = False,
        prefetched_publications: Optional[list[dict]] = None,
    ) -> dict[str, Any]:
        """
        Cria um registro de busca, executa, enriquece e persiste resultados.

        Aceita ambos `responsible_office_id` (int legado, single) e
        `responsible_office_ids` (list[int], multi). Internamente
        unifica em `_office_ids: list[int]` (vazia = sem filtro).

        `prefetched_publications`: quando passado (modo batch do scheduler),
        pula a chamada L1 e usa essa lista. CADA chamada faz uma cópia
        rasa dos dicts pra evitar contaminação entre offices (linha ~370
        muta `_responsible_office_id` no dict).
        """
        # Normaliza pra list[int]. Filtra zeros/duplicados.
        _office_ids: list[int] = []
        if responsible_office_ids:
            _office_ids.extend(int(x) for x in responsible_office_ids if x)
        if responsible_office_id:
            _office_ids.append(int(responsible_office_id))
        _office_ids = list(dict.fromkeys(_office_ids))  # unique preserva ordem
        _office_ids_set: set[int] = set(_office_ids)

        search = PublicationSearch(
            status=SEARCH_STATUS_RUNNING,
            date_from=date_from,
            date_to=date_to,
            origin_type=origin_type,
            office_filter=(
                ",".join(str(x) for x in _office_ids) if _office_ids else None
            ),
            requested_by_email=requested_by,
        )
        self.db.add(search)
        self.db.commit()
        self.db.refresh(search)

        try:
            # 1) Busca TODAS as publicações (paginação automática) ou usa
            # lista pré-fetched do scheduler (modo batch — 1 chamada L1
            # compartilhada entre offices da rodada).
            if prefetched_publications is not None:
                # Cópia rasa por publicação: o pré-filtro mais abaixo muta
                # `_responsible_office_id` no dict, então cada office
                # precisa do seu próprio dicionário pra não contaminar os
                # outros calls da rodada.
                publications = [dict(p) for p in prefetched_publications]
                self._update_search_progress(
                    search, "FETCH",
                    f"{len(publications)} publicações recebidas do scheduler (sem chamada L1)",
                    15,
                )
            else:
                self._update_search_progress(search, "FETCH", "Buscando publicações na API Legal One...", 5)
                publications = self.client.fetch_all_publications(
                    date_from=date_from,
                    date_to=date_to,
                    origin_type=origin_type,
                )
                self._update_search_progress(
                    search, "FETCH",
                    f"{len(publications)} publicações encontradas na API",
                    15,
                )

            # 1.5) Pré-filtro por escritório (otimização):
            # Usa o índice persistente (office_lawsuit_index). Quando há
            # múltiplos escritórios selecionados, faz a UNIÃO dos índices —
            # ou seja, pré-filtra tudo que pertence a QUALQUER um deles.
            # Se algum índice estiver stale, sync é disparado pra esse
            # escritório e o pré-filtro segue com os índices disponíveis.
            if _office_ids:
                merged_ids: set[int] = set()
                lid_to_office: dict[int, int] = {}
                any_index_filled = False
                for off_id in _office_ids:
                    try:
                        from app.services.office_lawsuit_index_service import (
                            OfficeLawsuitIndexService,
                        )
                        idx_svc = OfficeLawsuitIndexService(self.db, self.client)
                        idx_svc.ensure_sync(off_id)
                        ids = idx_svc.get_lawsuit_ids(off_id)
                        if ids:
                            any_index_filled = True
                            for lid in ids:
                                merged_ids.add(lid)
                                # 1ª associação ganha — operador raramente tem
                                # o mesmo processo em 2 escritórios.
                                lid_to_office.setdefault(lid, off_id)
                            logger.info(
                                "Índice persistente: %s processos pro escritório %s.",
                                len(ids), off_id,
                            )
                        else:
                            logger.info(
                                "Índice vazio/em construção pro escritório %s — "
                                "pulando pré-filtro pra esse.",
                                off_id,
                            )
                    except Exception as exc:
                        logger.warning(
                            "Pré-filtro por escritório %s falhou: %s",
                            off_id, exc,
                        )

                if any_index_filled and merged_ids:
                    before_prefilter = len(publications)
                    kept = []
                    for _p in publications:
                        rels = _p.get("relationships") or []
                        lit = next(
                            (r for r in rels if r.get("linkType") == "Litigation"),
                            None,
                        )
                        if not lit:
                            # sem processo vinculado — mantém pra não perder
                            # publicações "avulsas" do período; serão descartadas
                            # no filtro posterior se não baterem
                            kept.append(_p)
                            continue
                        try:
                            lid = int(lit.get("linkId"))
                        except (TypeError, ValueError):
                            continue
                        if lid in merged_ids:
                            # já sabemos o escritório — grava e evita lookup depois
                            _p["_responsible_office_id"] = lid_to_office.get(lid)
                            kept.append(_p)
                    logger.info(
                        "Pré-filtro escritórios %s: %s → %s publicações.",
                        _office_ids, before_prefilter, len(kept),
                    )
                    publications = kept

            # 2) Enriquece com responsibleOfficeId via lookup de processos
            self._update_search_progress(
                search, "ENRICH",
                f"Enriquecendo {len(publications)} publicações com dados de processos...",
                25,
            )
            publications = self._enrich_with_lawsuit_data(publications)
            self._update_search_progress(
                search, "FILTER",
                f"Filtrando {len(publications)} publicações...",
                40,
            )

            # 3) Filtra por escritório responsável (se especificado)
            if _office_ids_set:
                before = len(publications)

                # Diagnóstico: distribuição de responsibleOfficeId nos processos
                # vinculados, para descobrir qual ID realmente bate com o
                # escritório selecionado no frontend.
                from collections import Counter
                office_counter: Counter = Counter()
                sem_processo = 0
                sem_office = 0
                for _p in publications:
                    relationships = _p.get("relationships") or []
                    has_lit = any(r.get("linkType") == "Litigation" for r in relationships)
                    if not has_lit:
                        sem_processo += 1
                        continue
                    oid = _p.get("_responsible_office_id")
                    if oid is None:
                        sem_office += 1
                        continue
                    office_counter[oid] += 1

                top = office_counter.most_common(20)
                logger.info(
                    "Diagnóstico escritórios (procurados=%s) | total=%s | sem processo vinculado=%s | processo sem responsibleOfficeId=%s | top responsibleOfficeId: %s",
                    _office_ids, before, sem_processo, sem_office, top,
                )

                publications = [
                    p for p in publications
                    if p.get("_responsible_office_id") in _office_ids_set
                ]
                logger.info(
                    "Filtro por escritórios %s: %s → %s publicações.",
                    _office_ids, before, len(publications),
                )

            # 3.5) Filtra apenas publicações sem processo vinculado (se solicitado)
            if only_unlinked:
                before_unlinked = len(publications)
                publications = [
                    p for p in publications
                    if not any(
                        r.get("linkType") == "Litigation"
                        for r in (p.get("relationships") or [])
                    )
                ]
                logger.info(
                    "Filtro only_unlinked: %s → %s publicações.",
                    before_unlinked, len(publications),
                )

            # 4) Deduplica e persiste os novos
            self._update_search_progress(
                search, "PERSIST",
                f"Deduplicando e persistindo {len(publications)} publicações...",
                50,
            )
            #
            # Dedup em duas camadas:
            #   (a) legal_one_update_id (id único do Legal One) → duplicata exata
            #   (b) (linked_lawsuit_id, publication_date) → mesma publicação de um
            #       mesmo processo no mesmo dia é tratada uma única vez,
            #       economizando tokens de classificação e chamadas à API do L1.
            existing_ids = set(
                row[0]
                for row in self.db.query(PublicationRecord.legal_one_update_id).all()
            )

            # Chaves (lawsuit_id, publication_date) já presentes em registros
            # "vivos" (não descartados). Mesmo conjunto coberto pelo índice
            # único parcial uq_pub_lawsuit_date.
            existing_keys = set(
                (row[0], row[1])
                for row in self.db.query(
                    PublicationRecord.linked_lawsuit_id,
                    PublicationRecord.publication_date,
                )
                .filter(PublicationRecord.linked_lawsuit_id.isnot(None))
                .filter(PublicationRecord.publication_date.isnot(None))
                .filter(PublicationRecord.publication_date != "")
                .filter(PublicationRecord.status != RECORD_STATUS_DISCARDED_DUPLICATE)
                .filter(PublicationRecord.is_duplicate == False)  # noqa: E712
                .all()
            )

            new_records: List[PublicationRecord] = []
            duplicate_records: List[PublicationRecord] = []
            obsolete_records: List[PublicationRecord] = []
            new_count = 0
            dup_count = 0
            discarded_count = 0
            obsolete_count = 0

            # Commit em lote: evita perder TODO o trabalho se o worker uvicorn
            # for killed (OOM/restart) no meio do PERSIST. Também dá progresso
            # visível na UI via total_new/total_duplicate incrementais e
            # permite o watchdog distinguir "rodando lento" de "morta" pelo
            # MAX(created_at) em publicacao_registros.
            #
            # 500 ≈ sweet spot: inserts agrupados suficientes pra ser rápido
            # e granular o bastante pra não perder muito em caso de crash.
            PERSIST_BATCH_SIZE = 500
            pending_in_batch = 0
            total_processed = 0
            total_pubs = len(publications)

            for pub in publications:
                update_id = pub.get("id")
                if not update_id:
                    continue

                if update_id in existing_ids:
                    dup_count += 1
                    continue

                relationships = pub.get("relationships") or []
                lawsuit_rel = next(
                    (r for r in relationships if r.get("linkType") == "Litigation"),
                    None,
                )
                lawsuit_id = lawsuit_rel.get("linkId") if lawsuit_rel else None
                publication_date = pub.get("date")

                # Guarda (lawsuit_id, publication_date): se já temos uma
                # publicação viva do mesmo processo no mesmo dia, descartamos
                # — insere marcada para rastreabilidade sem consumir recursos
                # de classificação nem colidir com o índice único parcial.
                dedup_key = (
                    (lawsuit_id, publication_date)
                    if lawsuit_id and publication_date
                    else None
                )
                is_lawsuit_date_duplicate = (
                    dedup_key is not None and dedup_key in existing_keys
                )

                # ── Detecção de publicação obsoleta ─────────────────
                # Se a data da publicação é anterior à data de criação
                # da pasta do processo no Legal One, a publicação já
                # foi auditada na esteira de admissão → obsoleta.
                is_obsolete = False
                if not is_lawsuit_date_duplicate and publication_date:
                    lawsuit_creation = pub.get("_lawsuit_creation_date")
                    if lawsuit_creation:
                        try:
                            # Datas podem vir como ISO completo ou só "YYYY-MM-DD"
                            pub_dt = publication_date[:10]
                            law_dt = lawsuit_creation[:10]
                            if pub_dt < law_dt:
                                is_obsolete = True
                        except (TypeError, IndexError):
                            pass

                # Decide o status final do registro
                if is_lawsuit_date_duplicate:
                    record_status = RECORD_STATUS_DISCARDED_DUPLICATE
                elif is_obsolete:
                    record_status = RECORD_STATUS_OBSOLETE
                else:
                    record_status = RECORD_STATUS_NEW

                cnj = pub.get("_cnj")
                # Fallback: se não veio processo vinculado pelo L1, tenta extrair
                # o CNJ direto do texto da publicação (cabeçalho ou corpo).
                if not cnj:
                    fallback_cnj = extract_cnj_from_text(
                        (pub.get("description") or "")
                        + "\n"
                        + (pub.get("notes") or "")
                    )
                    if fallback_cnj:
                        cnj = fallback_cnj
                        logger.debug(
                            "CNJ extraído do texto (publicação #%s): %s",
                            update_id, cnj,
                        )
                record = PublicationRecord(
                    search_id=search.id,
                    legal_one_update_id=update_id,
                    origin_type=pub.get("originType"),
                    update_type_id=pub.get("typeId"),
                    description=pub.get("description"),
                    notes=pub.get("notes"),
                    publication_date=publication_date,
                    creation_date=pub.get("creationDate"),
                    linked_lawsuit_id=lawsuit_id,
                    linked_lawsuit_cnj=cnj,
                    linked_office_id=pub.get("_responsible_office_id"),
                    raw_relationships=relationships,
                    status=record_status,
                    is_duplicate=is_lawsuit_date_duplicate,
                    uf=uf_from_cnj(cnj),
                )
                self.db.add(record)
                existing_ids.add(update_id)

                # Atualiza existing_keys ASSIM QUE registramos a primeira
                # publicação com esse par (lawsuit_id, publication_date) —
                # independente do status final (NOVO, OBSOLETA, etc.). Sem
                # isso, duas publicações diferentes com o mesmo par dentro
                # do MESMO batch geravam UniqueViolation no índice parcial
                # uq_pub_lawsuit_date. Foi a causa real do travamento das
                # Buscas #2 e #3 em 22/04/2026 (a #2 ficou órfã porque o
                # except subsequente não conseguia commitar a marca de FALHA
                # com a session em estado "transaction rolled back").
                if dedup_key is not None and not is_lawsuit_date_duplicate:
                    existing_keys.add(dedup_key)

                if is_lawsuit_date_duplicate:
                    discarded_count += 1
                    duplicate_records.append(record)
                elif is_obsolete:
                    obsolete_count += 1
                    obsolete_records.append(record)
                else:
                    new_records.append(record)
                    new_count += 1

                pending_in_batch += 1
                total_processed += 1

                # Commit parcial: sobrevive a OOM/SIGKILL e dá progresso real
                # pra UI. Progress vai de 50 → 70% proporcional ao processado.
                if pending_in_batch >= PERSIST_BATCH_SIZE:
                    # Cancelamento cooperativo: cancel_search() roda em outra
                    # session (request do endpoint) e apenas seta status=
                    # CANCELADO no DB. O loop aqui não vê essa mudança sem
                    # consultar. Granularidade de 500 registros (~segundos)
                    # é aceitável e evita hit a cada publicação.
                    current_status = (
                        self.db.query(PublicationSearch.status)
                        .filter(PublicationSearch.id == search.id)
                        .scalar()
                    )
                    if current_status == SEARCH_STATUS_CANCELLED:
                        self.db.rollback()  # descarta o lote pendente
                        logger.warning(
                            "Busca #%s cancelada pelo usuário no PERSIST "
                            "(%d/%d processados antes do cancelamento, %d novas commitadas).",
                            search.id, total_processed, total_pubs, new_count,
                        )
                        self.db.refresh(search)
                        return self._search_to_dict(search)

                    search.total_found = new_count + discarded_count + obsolete_count
                    search.total_new = new_count
                    search.total_duplicate = dup_count + discarded_count + obsolete_count
                    search.progress_step = "PERSIST"
                    search.progress_detail = (
                        f"Persistindo... {total_processed}/{total_pubs} "
                        f"({new_count} novas, {discarded_count} duplicatas, "
                        f"{obsolete_count} obsoletas)"
                    )
                    search.progress_pct = 50 + min(
                        20, int(20 * total_processed / max(total_pubs, 1))
                    )
                    self.db.commit()
                    logger.info(
                        "Busca #%s: lote persistido (%d/%d processados, %d novas)",
                        search.id, total_processed, total_pubs, new_count,
                    )
                    pending_in_batch = 0

            # Commit final: pega o resto do último lote (<500 registros).
            self.db.commit()

            self._update_search_progress(
                search, "PERSIST",
                f"{new_count} novas, {dup_count} duplicatas, {discarded_count} descartadas, {obsolete_count} obsoletas",
                70,
            )

            # 5) Classificação agora é EXCLUSIVAMENTE via Batch API
            # (mais barato, sem rate limit). A busca apenas persiste os registros.
            # O operador envia o lote manualmente via painel de classificação em lote.

            # 6) Tenta construir proposta de tarefa para cada publicação classificada
            if new_records:
                self._update_search_progress(
                    search, "PROPOSALS",
                    f"Montando propostas de tarefa para {len(new_records)} publicações...",
                    80,
                )
                self._build_task_proposals(new_records)

            # 7) Duplicatas e obsoletas vão direto pra fila do RPA
            # com target_status="sem providência": o RPA só marca a
            # publicação como tratada no Legal One.
            rpa_records = duplicate_records + obsolete_records
            if rpa_records:
                try:
                    from app.services.publication_treatment_service import (
                        PublicationTreatmentService,
                    )
                    treatment_service = PublicationTreatmentService(self.db)
                    for rec in rpa_records:
                        treatment_service.sync_item_from_record(rec, commit=False)
                    self.db.commit()
                    logger.info(
                        "Busca #%s: %s duplicatas + %s obsoletas enfileiradas pro RPA (sem providência).",
                        search.id, len(duplicate_records), len(obsolete_records),
                    )
                except Exception as exc:
                    logger.exception(
                        "Falha ao enfileirar duplicatas/obsoletas da busca #%s: %s",
                        search.id, exc,
                    )
                    self.db.rollback()

            # `total_found` = registros que ESTA busca efetivamente vinculou
            # ao sistema (novos + descartados + obsoletas).
            search.total_found = new_count + discarded_count + obsolete_count
            search.total_new = new_count
            # total_duplicate agrega: duplicata por update_id +
            # descartadas pelo dedup (lawsuit_id, publication_date) + obsoletas.
            search.total_duplicate = dup_count + discarded_count + obsolete_count
            search.status = SEARCH_STATUS_COMPLETED
            search.progress_step = "DONE"
            search.progress_detail = f"Concluída — {new_count} novas publicações"
            search.progress_pct = 100
            search.finished_at = datetime.now(timezone.utc)
            self.db.commit()

            logger.info(
                "Busca #%s concluida: %s api-total, %s vinculadas, %s novas, "
                "%s dup update_id, %s descartadas (processo/data), %s obsoletas",
                search.id, len(publications),
                new_count + discarded_count + obsolete_count,
                new_count, dup_count, discarded_count, obsolete_count,
            )

            return self._search_to_dict(search)

        except Exception as exc:
            logger.error("Erro na busca #%s: %s", search.id, exc)
            # A session pode estar em "transaction rolled back due to previous
            # exception" (caso típico: UniqueViolation em flush — foi o que
            # aconteceu com as Buscas #2/#3 em 22/04/2026). Sem um rollback
            # explícito antes, o commit abaixo falha silenciosamente e a
            # busca fica eternamente com status='EXECUTANDO' e error_message
            # NULL, exigindo intervenção manual no DB.
            try:
                self.db.rollback()
            except Exception:
                pass
            try:
                fresh_search = (
                    self.db.query(PublicationSearch)
                    .filter(PublicationSearch.id == search.id)
                    .first()
                )
                if fresh_search is not None:
                    fresh_search.status = SEARCH_STATUS_FAILED
                    fresh_search.error_message = str(exc)[:500]
                    fresh_search.finished_at = datetime.now(timezone.utc)
                    fresh_search.progress_step = "FAILED"
                    self.db.commit()
            except Exception:
                logger.exception(
                    "Falha ao marcar busca #%s como FALHA após erro principal.",
                    search.id,
                )
                try:
                    self.db.rollback()
                except Exception:
                    pass
            raise

    # ──────────────────────────────────────────────
    # Enriquecimento via lookup de processos
    # ──────────────────────────────────────────────

    def _enrich_with_lawsuit_data(self, publications: list) -> list:
        """
        Extrai lawsuit_ids das relationships e busca em batch no Legal One para obter
        responsibleOfficeId + identifierNumber (CNJ). Anexa nas publicações como
        _responsible_office_id e _cnj (chaves privadas).
        """
        lawsuit_ids: List[int] = []
        for pub in publications:
            relationships = pub.get("relationships") or []
            for rel in relationships:
                if rel.get("linkType") == "Litigation" and rel.get("linkId"):
                    try:
                        lawsuit_ids.append(int(rel["linkId"]))
                    except (TypeError, ValueError):
                        pass

        if not lawsuit_ids:
            return publications

        try:
            lawsuits_map = self.client.fetch_lawsuits_by_ids(lawsuit_ids)
        except Exception as exc:
            logger.warning("Falha ao enriquecer publicações com dados do processo: %s", exc)
            return publications

        for pub in publications:
            relationships = pub.get("relationships") or []
            lawsuit_rel = next(
                (r for r in relationships if r.get("linkType") == "Litigation"),
                None,
            )
            if not lawsuit_rel:
                continue
            try:
                lid = int(lawsuit_rel.get("linkId"))
            except (TypeError, ValueError):
                continue
            lawsuit = lawsuits_map.get(lid)
            if lawsuit:
                pub["_responsible_office_id"] = lawsuit.get("responsibleOfficeId")
                pub["_cnj"] = lawsuit.get("identifierNumber")
                pub["_lawsuit_creation_date"] = lawsuit.get("creationDate")

        return publications

    # ──────────────────────────────────────────────
    # Auto-classificação
    # ──────────────────────────────────────────────

    def _auto_classify_records(self, records: List[PublicationRecord]) -> None:
        """
        Classifica cada publicação nova via IA (Claude).

        Limita a concorrência com Semaphore para não extrapolar os rate limits
        da Anthropic API (requests e tokens por minuto).
        """
        try:
            from app.services.classifier.ai_client import AnthropicClassifierClient
            from app.services.classifier.prompts import (
                SYSTEM_PROMPT,
                build_feedback_examples,
                build_system_prompt_for_office,
                build_user_message,
                load_office_overrides,
            )
            from app.services.classifier.taxonomy import validate_classification, repair_classification
            from app.services.classifier.response_schema import (
                validate_response,
                ResponseSchemaError,
            )
        except Exception as exc:
            logger.warning("Classifier indisponível: %s", exc)
            return

        try:
            ai = AnthropicClassifierClient()
        except Exception as exc:
            logger.warning("AI client não inicializado (ANTHROPIC_API_KEY?): %s", exc)
            return

        # Classifica apenas NOVO com texto — pula duplicatas/já classificados
        to_classify = [
            r for r in records
            if r.status == RECORD_STATUS_NEW and (r.description or "").strip()
        ]
        if not to_classify:
            logger.info("Nenhum registro novo com texto para classificar.")
            return

        logger.info("Classificando %d registros via IA (sequencial, 12s delay)...", len(to_classify))

        # SEQUENCIAL: máximo 1 chamada por vez
        # 60 segundos ÷ 12 segundos = 5 requests/minuto (exatamente o limite)
        # Garante que NUNCA peguemos 429 por RPM ou token limit
        CONCURRENCY = 1

        # Cache de prompts por escritório (evita recarregar overrides a cada chamada)
        office_prompts: dict[int, str] = {}

        # Pré-carrega exemplos de feedback por escritório
        feedback_cache: dict[int, str] = {}

        def _feedback_for(office_id: int | None) -> str:
            oid = office_id or 0
            if oid not in feedback_cache:
                try:
                    feedback_cache[oid] = build_feedback_examples(
                        self.db, office_id,
                        office_polo=self._resolve_office_polo(office_id),
                    )
                except Exception as exc:
                    logger.warning("Falha ao carregar feedbacks do escritório %s: %s", oid, exc)
                    feedback_cache[oid] = ""
            return feedback_cache[oid]

        def _prompt_for(rec: PublicationRecord) -> str:
            is_unlinked = rec.linked_lawsuit_id is None
            oid = rec.linked_office_id
            # Cache key inclui o flag de unlinked pra não misturar prompts
            cache_key = (oid or 0, is_unlinked)
            if cache_key not in office_prompts:
                try:
                    if oid:
                        excluded, custom = load_office_overrides(self.db, oid)
                    else:
                        excluded, custom = set(), []
                    office_prompts[cache_key] = build_system_prompt_for_office(
                        excluded or None,
                        custom or None,
                        is_unlinked=is_unlinked,
                        feedback_examples=_feedback_for(oid),
                        # Polo do escritorio (Admin > Polo dos Escritorios):
                        # trava a arvore da IA no polo certo (ativo/passivo).
                        # Sem isso a IA via os dois polos e podia gravar a
                        # categoria do lado errado (ver _resolve_office_polo).
                        polo_scope=self._resolve_office_polo(oid),
                        # Modo arvore enxuta (fase 13): quando o setting
                        # template_driven_taxonomy esta on, build_taxonomy_text
                        # filtra a arvore pra so incluir cats com template
                        # ativo do escritorio (ou global). Pra publicacoes
                        # sem escritorio (oid=None) nao passamos office —
                        # caem na arvore global completa.
                        office_external_id=oid,
                    )
                except Exception as exc:
                    logger.warning("Falha ao carregar overrides do escritório %s: %s", oid, exc)
                    office_prompts[cache_key] = build_system_prompt_for_office(
                        is_unlinked=is_unlinked,
                        feedback_examples=_feedback_for(oid),
                        polo_scope=self._resolve_office_polo(oid),
                        office_external_id=oid,
                    )
            return office_prompts[cache_key]

        async def _classify_all():
            sem = asyncio.Semaphore(CONCURRENCY)

            async def _one(rec: PublicationRecord):
                async with sem:
                    text = rec.description or ""
                    try:
                        user_msg = build_user_message(
                            rec.linked_lawsuit_cnj or "", text,
                            office_path=self._resolve_office_path(rec.linked_office_id),
                            office_polo=self._resolve_office_polo(rec.linked_office_id),
                        )
                        result = await ai.classify(_prompt_for(rec), user_msg)

                        # Schema cross-field: zera audiência se categoria não
                        # for "Audiência Agendada", valida formatos de data/hora,
                        # acumula warnings. Estrutural inválido (sem categoria)
                        # vira erro recuperável — marca pra revisão humana.
                        try:
                            clean = validate_response(result)
                        except ResponseSchemaError as exc:
                            logger.warning(
                                "Schema inválido na resposta da IA #%s: %s — payload=%s",
                                rec.id, exc, str(result)[:300],
                            )
                            # `_one` é função (não loop) — usa return pra
                            # abortar este registro. asyncio.gather segue
                            # processando os irmãos. O sleep de 12s lá
                            # embaixo é skipado, OK — só impacta um
                            # registro abortado.
                            return

                        if clean.warnings:
                            logger.warning(
                                "Schema warnings #%s: %s",
                                rec.id, "; ".join(clean.warnings),
                            )

                        # Auto-reparo de inversões de taxonomia (sub virou cat etc.)
                        cat_fixed, sub_fixed = repair_classification(
                            clean.categoria, clean.subcategoria
                        )
                        if (cat_fixed, sub_fixed) != (clean.categoria, clean.subcategoria):
                            logger.info(
                                "Classificação auto-corrigida #%s: (%s/%s) → (%s/%s)",
                                rec.id,
                                clean.categoria, clean.subcategoria,
                                cat_fixed, sub_fixed,
                            )
                        cat, sub = cat_fixed, sub_fixed

                        if cat and validate_classification(cat, sub):
                            rec.category = cat
                            rec.subcategory = sub
                            rec.polo = clean.polo
                            # Audiência: schema garante que só vem preenchido
                            # quando categoria == "Audiência Agendada".
                            rec.audiencia_data = clean.audiencia_data
                            rec.audiencia_hora = clean.audiencia_hora
                            rec.audiencia_link = clean.audiencia_link
                            # Natureza do processo: só pra publicações sem pasta vinculada
                            if rec.linked_lawsuit_id is None:
                                rec.natureza_processo = clean.natureza_processo
                            rec.status = RECORD_STATUS_CLASSIFIED
                            logger.debug(
                                "Classificado #%s → %s / %s (polo=%s, aud=%s %s, nat=%s)",
                                rec.id, cat, sub, clean.polo,
                                rec.audiencia_data, rec.audiencia_hora,
                                rec.natureza_processo if rec.linked_lawsuit_id is None else "-",
                            )
                        else:
                            logger.warning(
                                "Classificação inválida para #%s: cat=%s sub=%s",
                                rec.id, cat, sub,
                            )
                    except Exception as exc:
                        logger.warning("Falha ao classificar publicação #%s: %s", rec.id, exc)
                    # Aguarda 12 segundos entre requisições (garante limite de 5 RPM)
                    # Combinado com CONCURRENCY=1, isso respeita perfeitamente:
                    # - 5 requests/minuto (RPM)
                    # - 10,000 tokens/minuto (TPM)
                    await asyncio.sleep(12.0)

            await asyncio.gather(*[_one(r) for r in to_classify], return_exceptions=True)

        # Executa no loop correto (compatível com background thread e async context)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Estamos dentro de um loop assíncrono — usa run_in_executor via novo loop
                import concurrent.futures
                def _run_in_new_loop():
                    new_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(new_loop)
                    try:
                        new_loop.run_until_complete(_classify_all())
                    finally:
                        new_loop.close()
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(_run_in_new_loop)
                    future.result()  # aguarda conclusão
            else:
                loop.run_until_complete(_classify_all())
        except Exception:
            # Fallback: cria loop limpo
            new_loop = asyncio.new_event_loop()
            try:
                new_loop.run_until_complete(_classify_all())
            finally:
                new_loop.close()

        try:
            self.db.commit()
            classified = sum(1 for r in to_classify if r.status == RECORD_STATUS_CLASSIFIED)
            logger.info(
                "Classificação concluída: %d/%d registros classificados.",
                classified, len(to_classify),
            )
        except Exception as exc:
            logger.error("Erro ao salvar classificações: %s", exc)

    # ──────────────────────────────────────────────
    # Construção de propostas de tarefa
    # ──────────────────────────────────────────────

    def _resolve_office_polo(self, office_external_id: Optional[int]) -> Optional[str]:
        """Polo configurado do escritorio (Admin > Polo dos Escritorios):
        'ativo'/'passivo' viram filtro de arvore da taxonomia; 'ambos' ou
        ausente -> None (sem filtro, comportamento legado). Cacheado por
        instancia pra nao consultar o mesmo escritorio N vezes.

        Wiring central do fix de polo (2026-06): a CLASSIFICACAO (prompt da
        IA) e o MATCH de template passam a respeitar o polo do escritorio,
        em vez de a IA enxergar as duas arvores (ativo+passivo) e gravar a
        categoria do polo errado (ex.: BB/Autor caindo em 'Recursos e
        Julgamentos em 2o Grau', que e passivo e nao tem template do
        escritorio -> 'Sem template')."""
        if not office_external_id:
            return None
        cache = getattr(self, "_office_polo_cache", None)
        if cache is None:
            cache = {}
            self._office_polo_cache = cache
        if office_external_id not in cache:
            polo: Optional[str] = None
            try:
                from app.models.legal_one import LegalOneOffice
                office = (
                    self.db.query(LegalOneOffice)
                    .filter(LegalOneOffice.external_id == office_external_id)
                    .first()
                )
                raw = (getattr(office, "polo_scope", None) or "").strip().lower()
                polo = raw if raw in ("ativo", "passivo") else None
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Falha ao resolver polo do escritorio %s: %s",
                    office_external_id, exc,
                )
                polo = None
            cache[office_external_id] = polo
        return cache[office_external_id]

    def _resolve_office_path(self, office_external_id: Optional[int]) -> Optional[str]:
        """Path hierarquico do escritorio (ex.: 'MDR / ... / Banco do Brasil /
        Autor') pra injetar a linha 'ESCRITORIO RESPONSAVEL' no user message
        do classificador — sinal deterministico por publicacao pro modelo
        rotear o esquema de polo. Cacheado por instancia."""
        if not office_external_id:
            return None
        cache = getattr(self, "_office_path_cache", None)
        if cache is None:
            cache = {}
            self._office_path_cache = cache
        if office_external_id not in cache:
            path = None
            try:
                from app.models.legal_one import LegalOneOffice
                office = (
                    self.db.query(LegalOneOffice)
                    .filter(LegalOneOffice.external_id == office_external_id)
                    .first()
                )
                path = getattr(office, "path", None)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Falha ao resolver path do escritorio %s: %s",
                    office_external_id, exc,
                )
                path = None
            cache[office_external_id] = path
        return cache[office_external_id]

    def _build_task_proposals(
        self,
        records: List[PublicationRecord],
        skip_responsible_lookup: bool = False,
    ) -> None:
        """
        Para cada record classificado, tenta achar um template correspondente
        (categoria/subcategoria + escritório responsável) e monta uma proposta
        de payload de tarefa que ficará pendente de aprovação pelo operador.

        A proposta é armazenada como campo JSON em `raw_relationships` sob a chave
        interna `_proposed_task` (usando o campo já existente para evitar migrações
        extras nesta iteração).

        Parâmetros:
            skip_responsible_lookup: Se True, pula ate a busca limitada de
                responsavel de pasta. No fluxo normal, essa busca so roda para
                templates sem responsavel nominal.
        """
        try:
            from app.models.task_template import TaskTemplate
        except Exception:
            return  # Template model ainda não existe

        def _classification_infos(rec: PublicationRecord) -> list[dict[str, Any]]:
            classifications = [
                {"category": rec.category, "subcategory": rec.subcategory}
            ]
            if rec.classifications and isinstance(rec.classifications, list):
                for clf in rec.classifications[1:]:  # [1:] pois a primeira ja e a primaria
                    cat = clf.get("categoria")
                    sub = clf.get("subcategoria")
                    if cat:
                        classifications.append({"category": cat, "subcategory": sub})
            return classifications

        templates_by_record_id: dict[int, list] = {}
        lawsuit_ids_needing_responsible: set[int] = set()

        for rec in records:
            if not rec.category:
                continue

            matching_templates = []
            # Importa repair aqui dentro pra evitar import cycle no topo
            # do arquivo (taxonomy.py importa coisas pesadas).
            from app.services.classifier.taxonomy import repair_classification
            for clf_info in _classification_infos(rec):
                # Repair: cat/sub legados v1 viram v2 via _CATEGORY_ALIASES
                # (ex.: "2 Grau - Civel" -> "Recursos e Julgamentos em 2 Grau"),
                # case/acento normaliza, e sub invalida cai pra "Para Analise"
                # da cat. Sem isso pubs classificadas antes do seed v2
                # ficavam orfas de template (cat literal v1 nao bate com
                # template v2). Passa polo_scope da pub e forca taxonomy_v2
                # pra alinhar com taxonomy_active_version=v2.
                raw_cat = clf_info.get("category") or ""
                raw_sub = clf_info.get("subcategory") or "-"
                # Polo pra resolver o template: prioriza o polo CONFIGURADO do
                # escritorio (Admin > Polo dos Escritorios); so cai no polo
                # inferido pela IA (rec.polo) quando o escritorio e 'ambos'/
                # desconhecido. Evita o caso de a IA devolver 'ambos' e o
                # repair nao achar a categoria polo-especifica.
                match_polo = (
                    self._resolve_office_polo(rec.linked_office_id)
                    or getattr(rec, "polo", None)
                )
                try:
                    repaired_cat, repaired_sub = repair_classification(
                        raw_cat, raw_sub,
                        polo_scope=match_polo,
                        taxonomy_version="v2",
                    )
                except Exception:
                    repaired_cat, repaired_sub = raw_cat, raw_sub
                # Sub: aceita TANTO a raw quanto a repaired. Motivo: o
                # repair forca a sub pra "Para Análise" quando a sub raw
                # nao existe na taxonomia v2 (ex.: pub veio com sub
                # "Manifestação Avulsa", v2 nao tem ela na cat alvo),
                # MAS o operador pode ter cadastrado o template com
                # exatamente "Manifestação Avulsa" como sub. Tentamos
                # ambas + cat-only (sub NULL) como rede de seguranca.
                sub_candidates: set[str] = set()
                for s in (raw_sub, repaired_sub):
                    if s and s not in ("-",):
                        sub_candidates.add(s)
                # Busca templates correspondentes:
                #   - Se o registro tem escritório: busca templates do escritório OU templates globais (office IS NULL)
                #   - Se o registro não tem escritório (sem processo): busca APENAS templates globais (office IS NULL)
                office_filter = (
                    (
                        (TaskTemplate.office_external_id == rec.linked_office_id)
                        | (TaskTemplate.office_external_id.is_(None))
                    )
                    if rec.linked_office_id
                    else TaskTemplate.office_external_id.is_(None)
                )
                # Taxonomy v2 (tax003/tax007): pula templates pendentes de
                # revisao. Legacy v1 ficam dormentes ate o operador
                # re-apontar pra cat/sub da v2 via POST
                # /task-templates/{id}/migrate. is_active=True ainda
                # respeita desativacao manual feita pelo operador.
                _sub_filter = (
                    TaskTemplate.subcategory.in_(sub_candidates) | TaskTemplate.subcategory.is_(None)
                    if sub_candidates
                    else TaskTemplate.subcategory.is_(None)
                )
                templates = (
                    self.db.query(TaskTemplate)
                    .filter(TaskTemplate.is_active == True)
                    .filter(TaskTemplate.needs_taxonomy_review == False)
                    .filter(TaskTemplate.category == repaired_cat)
                    .filter(office_filter)
                    .filter(_sub_filter)
                    .order_by(TaskTemplate.subcategory.nullslast())
                    .all()
                )
                # Diagnostico: se ZERO templates casarem mesmo a publicacao
                # tendo cat valida, loga o estado de cada filtro pra ajudar
                # a entender onde travou. Ative com env DEBUG_TEMPLATE_MATCH=1
                # ou deixe sempre — tem custo de 4 queries extras so quando
                # o caso "sem template" acontece. Sem essa visibilidade
                # nao da pra distinguir "operador nao criou template"
                # de "template existe mas filtro excluiu" (caso comum
                # de cat/sub com encoding diferente, needs_taxonomy_review,
                # is_active=false, office_external_id nao bate).
                if not templates:
                    try:
                        # Quantos com cat que bate, sem outros filtros
                        n_cat_only = (
                            self.db.query(TaskTemplate)
                            .filter(TaskTemplate.category == repaired_cat)
                            .count()
                        )
                        # Adiciona office_filter
                        n_with_office = (
                            self.db.query(TaskTemplate)
                            .filter(TaskTemplate.category == repaired_cat)
                            .filter(office_filter)
                            .count()
                        )
                        # Adiciona subcategory match
                        n_with_sub = (
                            self.db.query(TaskTemplate)
                            .filter(TaskTemplate.category == repaired_cat)
                            .filter(office_filter)
                            .filter(_sub_filter)
                            .count()
                        )
                        # Adiciona is_active=True
                        n_active = (
                            self.db.query(TaskTemplate)
                            .filter(TaskTemplate.category == repaired_cat)
                            .filter(office_filter)
                            .filter(_sub_filter)
                            .filter(TaskTemplate.is_active == True)
                            .count()
                        )
                        # Loga raw vs repaired pra diagnosticar quando cat
                        # v1 nao tem alias e _find_category_by_normalized
                        # nao acha nada — caso onde precisa adicionar alias
                        # novo em _CATEGORY_ALIASES.
                        logger.warning(
                            "match_template.miss rec=%s raw_cat=%r raw_sub=%r "
                            "-> repaired_cat=%r repaired_sub=%r office=%s "
                            "lawsuit=%s polo=%r | templates: cat-only=%d "
                            "+office=%d +sub=%d +active=%d (final=0; "
                            "filtro needs_review=False removeu %d)",
                            rec.id,
                            raw_cat,
                            raw_sub,
                            repaired_cat,
                            repaired_sub,
                            rec.linked_office_id,
                            rec.linked_lawsuit_id,
                            getattr(rec, "polo", None),
                            n_cat_only,
                            n_with_office,
                            n_with_sub,
                            n_active,
                            n_active,
                        )
                    except Exception as exc:
                        logger.debug("match_template diagnostico falhou: %s", exc)

                # Aviso de divergencia de polo: se o escritorio do template
                # declara polo especifico (ativo/passivo) mas o polo inferido
                # pela IA pra publicacao bate em outro, logamos. Indica que
                # o template foi cadastrado no escritorio errado OU a IA
                # classificou o polo errado — vale investigacao do operador.
                rec_polo = getattr(rec, "polo", None)
                if rec_polo in ("ativo", "passivo"):
                    for t in templates:
                        office_polo = (
                            getattr(t.office, "polo_scope", None)
                            if t.office else None
                        )
                        if (
                            office_polo in ("ativo", "passivo")
                            and office_polo != rec_polo
                        ):
                            logger.warning(
                                "Template %s (escritorio polo=%s) casou com "
                                "publicacao %s polo=%s — divergencia.",
                                t.id, office_polo, rec.id, rec_polo,
                            )
                matching_templates.extend(templates)

            templates_by_record_id[rec.id] = matching_templates
            if (
                not skip_responsible_lookup
                and rec.linked_lawsuit_id
                and any(t.responsible_user_external_id is None for t in matching_templates)
            ):
                lawsuit_ids_needing_responsible.add(rec.linked_lawsuit_id)

        # Resolve responsaveis de pasta (cache + fallback API). Antes era
        # cache-only confiando no prefetch do submit_batch, mas isso falha
        # silenciosamente quando: (a) prefetch do background task quebrou,
        # (b) caminho nao passa pelo prefetch (reclassify_records,
        # rebuild-proposals, reclassificacao automatica). Usar
        # prefetch_lawsuit_responsibles_cache garante que cache miss vira
        # chamada L1 sincrona paralela (max_workers=2) e ja cacheia pra
        # proximas confirmacoes (TTL 24h).
        lawsuit_responsibles: dict = {}
        if lawsuit_ids_needing_responsible:
            try:
                from app.services.legal_one_client import LegalOneApiClient
                lo_client = LegalOneApiClient()
                lawsuit_ids = sorted(lawsuit_ids_needing_responsible)
                lawsuit_responsibles = lo_client.prefetch_lawsuit_responsibles_cache(lawsuit_ids)
                logger.info(
                    "Responsaveis de pasta resolvidos para templates sem responsavel: %d de %d processos.",
                    len(lawsuit_responsibles), len(lawsuit_ids),
                )
            except Exception as exc:
                logger.warning("Falha ao resolver responsaveis de pasta: %s", exc)

        for rec in records:
            if not rec.category:
                continue

            lawsuit_resp = lawsuit_responsibles.get(rec.linked_lawsuit_id) if rec.linked_lawsuit_id else None
            proposals = []
            for tmpl in templates_by_record_id.get(rec.id, []):
                try:
                    proposal = self._render_proposal(rec, tmpl, lawsuit_responsible=lawsuit_resp)
                    proposals.append(proposal)
                except Exception as exc:
                    logger.warning(
                        "Falha ao montar proposta p/ record %s, tmpl %s: %s",
                        rec.id, tmpl.id, exc,
                    )

            # SEMPRE atualiza as chaves de proposta (limpando as antigas), pra
            # que reclassificacao manual / rebuild reflita a classificacao
            # ATUAL. Antes, 'if not proposals: continue' deixava a proposta
            # velha presa quando a nova classificacao nao casava template; e
            # como _proposed_tasks so era setado quando len>1, a lista antiga
            # de extras tambem ficava grudada ao passar de N->1. (fix 2026-06)
            raw = dict(rec.raw_relationships or {}) if isinstance(rec.raw_relationships, dict) else {
                "_relationships": rec.raw_relationships
            }
            raw.pop("_proposed_task", None)
            raw.pop("_proposed_tasks", None)
            if proposals:
                # Primeira proposta mantém compatibilidade; extras ficam em lista
                raw["_proposed_task"] = proposals[0]
                if len(proposals) > 1:
                    raw["_proposed_tasks"] = proposals
            rec.raw_relationships = raw

        self.db.commit()

    # Limite máximo de caracteres aceito pela API Legal One no campo description.
    _DESCRIPTION_MAX_CHARS = 250

    @staticmethod
    def _payload_has_responsible_participant(payload: dict) -> bool:
        for participant in payload.get("participants") or []:
            if not isinstance(participant, dict):
                continue
            contact = participant.get("contact") or participant.get("Contact") or {}
            contact_id = (
                participant.get("contact_id")
                or participant.get("contactId")
                or participant.get("ContactId")
                or contact.get("id")
                or contact.get("Id")
            )
            is_responsible = (
                participant.get("isResponsible")
                or participant.get("is_responsible")
                or participant.get("IsResponsible")
            )
            if is_responsible and contact_id:
                return True
        return False

    @staticmethod
    def _responsible_participant(contact_id: Any) -> dict:
        return {
            "contact": {"id": contact_id},
            "isResponsible": True,
            "isExecuter": True,
            "isRequester": True,
        }

    def _apply_lawsuit_responsible_to_missing_payloads(
        self,
        payloads: list[dict],
        lawsuit_id: int,
    ) -> None:
        """
        Garante que payloads sem participant responsável recebam o
        responsável da pasta (Legal One).

        Estratégia em 2 níveis:
          1. **Cache** (rápido) — `get_cached_lawsuit_responsible_user`.
             Hit comum quando o operador classificou recentemente; cache
             populado pelo motor de classificação.
          2. **Fallback API** (motor de conferência na confirmação) —
             quando o cache vem vazio (processo entrou na fila antes do
             cache ser populado, ou cache foi invalidado), chama a API
             do L1 (`get_lawsuit_responsible_user` → `/Lawsuits/{id}/Participants`)
             pra resolver na hora. Sucesso popula o cache pra próximas
             confirmações no mesmo processo.

        Sem responsável após os 2 níveis: loga warning e segue sem
        aplicar (L1 vai responder 400 com mensagem que o frontend
        humaniza). Mantém compatibilidade com o comportamento antigo.
        """
        missing_payloads = [
            p for p in payloads
            if isinstance(p, dict) and not self._payload_has_responsible_participant(p)
        ]
        if not missing_payloads:
            return

        cached_lookup = getattr(self.client, "get_cached_lawsuit_responsible_user", None)
        if not callable(cached_lookup):
            logger.warning(
                "Client sem leitura de cache de responsavel da pasta %s.",
                lawsuit_id,
            )
            return

        responsible = cached_lookup(lawsuit_id)
        responsible_id = responsible.get("id") if responsible else None

        # ── Fallback: cache vazio → API real (motor de conferência) ──
        if not responsible_id:
            api_lookup = getattr(self.client, "get_lawsuit_responsible_user", None)
            if callable(api_lookup):
                try:
                    fetched = api_lookup(lawsuit_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Falha buscando responsavel da pasta %s na API L1: %s",
                        lawsuit_id, exc,
                    )
                    fetched = None
                if isinstance(fetched, dict) and fetched.get("id"):
                    responsible = fetched
                    responsible_id = fetched["id"]
                    # Popular cache pra próximas confirmações dessa pasta
                    cache_merge = getattr(
                        self.client, "_lawsuit_cache_merge_upsert", None,
                    )
                    if callable(cache_merge):
                        try:
                            cache_merge({lawsuit_id: {"responsibleUser": fetched}})
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "Falha gravando responsavel da pasta %s no cache: %s",
                                lawsuit_id, exc,
                            )
                    logger.info(
                        "Responsavel da pasta %s resolvido via API (fallback) "
                        "durante confirmacao.", lawsuit_id,
                    )

        if not responsible_id:
            logger.warning(
                "Responsavel da pasta %s ausente no cache E na API durante "
                "confirmacao — payloads vao sem participant (L1 deve recusar).",
                lawsuit_id,
            )
            return

        participant = self._responsible_participant(responsible_id)
        for payload in missing_payloads:
            payload["participants"] = [participant]

        logger.info(
            "Responsavel da pasta aplicado em %d payload(s) do processo %s.",
            len(missing_payloads), lawsuit_id,
        )

    def _enforce_description_limit(self, payload: dict) -> None:
        """
        Trava de segurança: garante que `description` ≤ 250 chars (limite da
        API Legal One). NÃO concatena textos de outras publicações nem move
        conteúdo para notes — usa exclusivamente o que o template renderizou.

        Modifica o `payload` in-place.
        """
        desc = (payload.get("description") or "").strip()
        if not desc:
            payload["description"] = "Publicação judicial"
            return
        if len(desc) > self._DESCRIPTION_MAX_CHARS:
            desc = desc[: self._DESCRIPTION_MAX_CHARS - 1].rstrip() + "…"
        payload["description"] = desc

    def _ensure_endtime_in_future(self, payload: dict) -> None:
        """
        Fallback: garante que `endDateTime` (e `startDateTime` quando
        relevante) não fique no passado, o que faria o L1 retornar 400
        com mensagem "O status selecionado não pode ser 'Pendente'
        quando a data de conclusão for anterior à data atual".

        Acontece quando o template foi calculado com base na data da
        publicação (ex.: publish_date + 5 dias úteis), mas o operador só
        confirmou o agendamento *depois* desse vencimento. O L1 rejeita
        a tarefa em status "Pendente" pra data passada na config atual
        do MDR.

        Estratégia: se o `endDateTime` do payload já passou, ajusta
        ambos (start/end) pro fim do **próximo dia útil** (BRT, 23:59:59).
        Logamos warning pra rastreabilidade. Modifica o `payload` in-place.
        """
        end_iso = payload.get("endDateTime")
        if not end_iso or not isinstance(end_iso, str):
            return
        try:
            # endDateTime vem como "...Z" (UTC). Normaliza pra parse.
            end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        except ValueError:
            logger.warning(
                "endDateTime do payload nao parseavel — pulando fallback: %s",
                end_iso,
            )
            return

        now_utc = datetime.now(timezone.utc)
        if end_dt > now_utc:
            return  # ainda no futuro, OK

        # Calcula próximo dia útil em BRT
        from app.services.prazos_iniciais.prazo_calculator import add_business_days
        try:
            br_tz = ZoneInfo("America/Sao_Paulo")
        except Exception:  # noqa: BLE001
            from datetime import timezone as _tz, timedelta as _td
            br_tz = _tz(_td(hours=-3))  # fallback simples
        today_brt = datetime.now(br_tz).date()
        next_busday = add_business_days(today_brt, 1)
        local_dt = datetime(
            next_busday.year, next_busday.month, next_busday.day,
            23, 59, 59, tzinfo=br_tz,
        )
        new_iso = local_dt.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ",
        )

        old_end = payload.get("endDateTime")
        old_start = payload.get("startDateTime")
        payload["endDateTime"] = new_iso
        # Mantém start <= end. Se start também tá no passado, bumpa junto.
        if isinstance(old_start, str):
            try:
                start_dt = datetime.fromisoformat(old_start.replace("Z", "+00:00"))
                if start_dt < now_utc or start_dt > local_dt.astimezone(timezone.utc):
                    payload["startDateTime"] = new_iso
            except ValueError:
                payload["startDateTime"] = new_iso
        else:
            payload["startDateTime"] = new_iso

        logger.warning(
            "Data de conclusao no passado (%s) — bumpada pro proximo dia "
            "util (%s). startDateTime original=%s.",
            old_end, new_iso, old_start,
        )

    # ──────────────────────────────────────────────
    # Checagem de duplicatas (tarefa já pendente no L1)
    # ──────────────────────────────────────────────

    @staticmethod
    def _build_l1_task_url(task_id: int, lawsuit_id: int) -> str:
        """
        Monta a URL de deep-link pro painel web do L1.
        Formato validado com o MDR — inclui parentId e returnUrl pra que
        ao fechar a task o usuário volte pra aba de compromissos do processo.
        """
        from urllib.parse import quote
        return_path = (
            f"/processos/processos/DetailsCompromissosTarefas/{int(lawsuit_id)}"
            "?ajaxnavigation=true&renderOnlySection=True"
        )
        return (
            f"{L1_WEB_BASE_URL}/agenda/tarefas/DetailsCompromissoTarefa/{int(task_id)}"
            f"?parentId={int(lawsuit_id)}&tipoContexto=1&hasNavigation=True"
            f"&currentPage=1&returnUrl={quote(return_path, safe='')}"
        )

    def refresh_today_reference_dates(self, lawsuit_id: int) -> dict:
        """Recalcula a DATA de agendamento (start/endDateTime) das propostas
        cujo template tem due_date_reference='today', usando a DATA ATUAL.

        Motivo: a data da proposta e' congelada quando o template e' aplicado
        (_render_proposal). Pra template com referencia "data atual", uma
        publicacao antiga acaba com data velha. Este metodo — chamado quando o
        operador ABRE o modal de agendamento — reconstroi a sugestao pra hoje +
        N dias uteis. So mexe na DATA (preserva descricao, notas e
        participantes). Pula proposta de audiencia (a data vem do ato, nao do
        "hoje").

        Persiste no _proposed_task/_proposed_tasks (durabilidade) e retorna
        {subtype_id: {"startDateTime", "endDateTime"}} pro frontend aplicar nos
        payloads do modal — uma unica vez, na abertura (respeita edicao manual
        posterior do operador).
        """
        from app.models.task_template import TaskTemplate
        from app.services.prazos_iniciais.prazo_calculator import add_business_days
        from datetime import date as date_cls
        from sqlalchemy.orm.attributes import flag_modified

        records = (
            self.db.query(PublicationRecord)
            .filter(PublicationRecord.linked_lawsuit_id == lawsuit_id)
            .filter(
                PublicationRecord.status.in_(
                    [RECORD_STATUS_NEW, RECORD_STATUS_CLASSIFIED, RECORD_STATUS_IGNORED]
                )
            )
            .all()
        )
        if not records:
            return {}

        BR_TZ = ZoneInfo("America/Sao_Paulo")

        def _brt_to_utc_z(date_str: str, time_str: str) -> str:
            if time_str.count(":") == 1:
                time_str = f"{time_str}:00"
            local_dt = datetime.fromisoformat(
                f"{date_str}T{time_str}"
            ).replace(tzinfo=BR_TZ)
            return local_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        today = date_cls.today()
        refreshed: dict = {}
        tmpl_cache: dict = {}
        dirty = False

        for rec in records:
            raw = rec.raw_relationships if isinstance(rec.raw_relationships, dict) else None
            if not raw:
                continue
            # Audiencia: a data vem do ato (data/hora da audiencia), nao do "hoje".
            if getattr(rec, "audiencia_data", None):
                continue

            proposals = raw.get("_proposed_tasks")
            is_list = isinstance(proposals, list)
            if not is_list:
                single = raw.get("_proposed_task")
                proposals = [single] if isinstance(single, dict) else []
            if not proposals:
                continue

            changed = False
            for prop in proposals:
                if not isinstance(prop, dict):
                    continue
                tmpl_id = prop.get("template_id")
                if not tmpl_id:
                    continue
                if tmpl_id not in tmpl_cache:
                    tmpl_cache[tmpl_id] = (
                        self.db.query(TaskTemplate)
                        .filter(TaskTemplate.id == tmpl_id)
                        .first()
                    )
                tmpl = tmpl_cache[tmpl_id]
                if not tmpl or getattr(tmpl, "due_date_reference", None) != "today":
                    continue

                pl = prop.get("payload")
                if not isinstance(pl, dict):
                    continue
                due_date = add_business_days(today, tmpl.due_business_days or 5)
                due_iso = _brt_to_utc_z(due_date.isoformat(), "23:59:59")
                if pl.get("startDateTime") != due_iso or pl.get("endDateTime") != due_iso:
                    pl["startDateTime"] = due_iso
                    pl["endDateTime"] = due_iso
                    changed = True
                sub = pl.get("subTypeId")
                if sub:
                    refreshed[int(sub)] = {
                        "startDateTime": due_iso,
                        "endDateTime": due_iso,
                    }

            if changed:
                # Pos-reload do DB, _proposed_task e _proposed_tasks[0] sao
                # objetos distintos (JSON nao preserva referencia). Como o
                # modal e o schedule leem _proposed_tasks quando ela existe,
                # mutamos a lista; aqui re-sincronizamos o singular pro
                # invariante _proposed_task == _proposed_tasks[0].
                if is_list and proposals:
                    raw["_proposed_task"] = proposals[0]
                # Mutacao IN-PLACE de coluna JSON nao e' detectada pelo
                # SQLAlchemy sem MutableDict — flag_modified forca o UPDATE.
                flag_modified(rec, "raw_relationships")
                dirty = True

        if dirty:
            self.db.commit()
        return refreshed

    def check_duplicates_for_lawsuit(
        self,
        lawsuit_id: int,
        subtype_ids: list[int],
    ) -> dict[str, Any]:
        """
        Pergunta ao L1 quais tarefas `subTypeId IN subtype_ids` já estão em
        aberto no processo `lawsuit_id`. "Em aberto" = statusId em
        L1_BLOCKING_STATUS_IDS (Pendente/Em Andamento/Aguardando).

        Retorno:
            {
              "duplicates_by_subtype": {
                 <subtype_id>: [
                    { "task_id", "description", "status_id", "status_label",
                      "end_date_time", "l1_url" },
                    ...
                 ],
                 ...
              },
              "total_duplicates": N,
              "checked_subtype_ids": [..],
              "blocking_status_ids": [0,1,2],
            }

        Nunca levanta exceção — em caso de falha ao consultar L1, retorna
        dict com flag `check_failed=True` e segue o fluxo (operador decide
        sem o aviso). A ideia é não travar agendamento legítimo por causa
        de indisponibilidade da API externa.
        """
        subtype_ids_clean = [int(s) for s in subtype_ids if s]
        if not subtype_ids_clean:
            return {
                "duplicates_by_subtype": {},
                "total_duplicates": 0,
                "checked_subtype_ids": [],
                "blocking_status_ids": list(L1_BLOCKING_STATUS_IDS),
            }

        cache_key = (int(lawsuit_id), tuple(sorted(set(subtype_ids_clean))))
        import time
        now_ts = time.monotonic()
        cached = _DUPLICATE_CACHE.get(cache_key)
        if cached and (now_ts - cached[0]) < _DUPLICATE_CACHE_TTL_SECONDS:
            tasks = cached[1]
        else:
            # Monta OData filter: relationships link + subtype IN + status IN
            subtype_filter = " or ".join(
                f"subTypeId eq {sid}" for sid in subtype_ids_clean
            )
            status_filter = " or ".join(
                f"statusId eq {sid}" for sid in L1_BLOCKING_STATUS_IDS
            )
            filter_expr = (
                f"relationships/any(r: r/linkType eq 'Litigation' "
                f"and r/linkId eq {int(lawsuit_id)}) "
                f"and ({subtype_filter}) and ({status_filter})"
            )
            try:
                # L1 impoe limite rigido de $top=30 no endpoint /Tasks. Topo
                # maior retorna HTTP 400 e o _paginated_catalog_loader engole
                # o erro devolvendo lista vazia (falso negativo de duplicata).
                # 30 eh o maximo seguro; paginacao via @odata.nextLink cobre
                # caso o filtro retorne mais que isso — raro, pois o filtro
                # eh estreito (1 processo + N subtipos + 3 status).
                tasks = self.client.search_tasks(
                    filter_expression=filter_expr,
                    top=30,
                )
                logger.info(
                    "check_duplicates lawsuit=%s subtypes=%s retornou %d task(s) do L1",
                    lawsuit_id, subtype_ids_clean, len(tasks or []),
                )
                _DUPLICATE_CACHE[cache_key] = (now_ts, tasks)
                # Limpa entradas velhas oportunisticamente pra não vazar memória
                stale = [k for k, (ts, _) in _DUPLICATE_CACHE.items()
                         if (now_ts - ts) > 300]
                for k in stale:
                    _DUPLICATE_CACHE.pop(k, None)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "check_duplicates falhou no L1 (lawsuit_id=%s subtypes=%s): %s",
                    lawsuit_id, subtype_ids_clean, exc,
                )
                return {
                    "duplicates_by_subtype": {},
                    "total_duplicates": 0,
                    "checked_subtype_ids": subtype_ids_clean,
                    "blocking_status_ids": list(L1_BLOCKING_STATUS_IDS),
                    "check_failed": True,
                    "check_error": str(exc)[:200],
                }

        duplicates_by_subtype: dict[int, list[dict]] = {}
        for t in tasks or []:
            sid = t.get("subTypeId")
            if sid is None:
                continue
            entry = {
                "task_id": t.get("id"),
                "description": (t.get("description") or "")[:200],
                "status_id": t.get("statusId"),
                "status_label": L1_STATUS_LABELS.get(
                    t.get("statusId"), f"status {t.get('statusId')}"
                ),
                "end_date_time": t.get("endDateTime"),
                "l1_url": self._build_l1_task_url(t.get("id"), lawsuit_id),
            }
            duplicates_by_subtype.setdefault(int(sid), []).append(entry)

        return {
            "duplicates_by_subtype": duplicates_by_subtype,
            "total_duplicates": sum(len(v) for v in duplicates_by_subtype.values()),
            "checked_subtype_ids": subtype_ids_clean,
            "blocking_status_ids": list(L1_BLOCKING_STATUS_IDS),
        }

    def get_recent_tasks_for_lawsuit(
        self,
        lawsuit_id: int,
        recent_completed_limit: int = 5,
    ) -> dict[str, Any]:
        """
        Diagnóstico de tarefas no Legal One pra exibir no detalhe da publicação.

        Diferente do `check_duplicates_for_lawsuit` (que filtra subtipo+status
        bloqueante pra evitar duplicar agendamento), este puxa TODAS as
        tarefas do processo e separa em duas listas:
          - `pending`: TODAS com status ∈ L1_BLOCKING_STATUS_IDS (Pendente,
            Em Andamento, Aguardando) — sem limite, operador precisa enxergar
            tudo que está em curso.
          - `recent_completed`: até N (default 5) das tarefas terminadas
            (status fora do bloqueante: Concluída, Cancelada etc.), em ordem
            cronológica decrescente — diagnóstico do que rodou recentemente.

        Estratégia: 1 chamada OData ao L1 com `$top=50` ordenado por
        creationDate desc. O cap de 50 cobre 99% dos processos; se houver
        processo com mais de 50 pendentes (raro), as mais antigas ficariam
        de fora do `pending` — reportamos `truncated=True` pra UI sinalizar.

        Resolve nome de subtipo e tipo via lookup local nas tabelas
        `legal_one_task_subtypes` / `legal_one_task_types` (já populadas pelo
        sync de catálogo). Pra subtipos não catalogados, devolve `null` no
        nome — UI mostra "Subtipo #N".

        Cache: TTL=15s por lawsuit_id (separado do _DUPLICATE_CACHE pq a
        chave é diferente — só lawsuit, sem subtipos).

        Nunca levanta exceção: em caso de falha do L1, retorna estrutura com
        `check_failed=True` pra UI mostrar fallback. Detalhe da publicação
        não pode quebrar por causa de instabilidade externa.
        """
        from app.models.legal_one import LegalOneTaskSubType, LegalOneTaskType
        import time

        lawsuit_id_int = int(lawsuit_id)
        recent_limit = max(1, min(int(recent_completed_limit), 20))

        cache_key = lawsuit_id_int
        now_ts = time.monotonic()
        cached = _RECENT_TASKS_CACHE.get(cache_key)
        if cached and (now_ts - cached[0]) < _RECENT_TASKS_CACHE_TTL_SECONDS:
            tasks = cached[1]
        else:
            filter_expr = (
                f"relationships/any(r: r/linkType eq 'Litigation' "
                f"and r/linkId eq {lawsuit_id_int})"
            )
            try:
                # ATENÇÃO: L1 impõe limite rígido de $top=30 no endpoint
                # /Tasks. top maior retorna HTTP 400 e o
                # _paginated_catalog_loader engole o erro devolvendo lista
                # vazia (visto em prod 2026-04-28: "Total: 0" no log e
                # nenhuma tarefa renderizada na UI). 30 é o máximo seguro;
                # se o processo tiver mais de 30 tarefas, o loader pagina
                # automaticamente via @odata.nextLink.
                tasks = self.client.search_tasks(
                    filter_expression=filter_expr,
                    top=30,
                    orderby="creationDate desc",
                )
                _RECENT_TASKS_CACHE[cache_key] = (now_ts, tasks)
                # Limpeza oportunística (mesma cadência do _DUPLICATE_CACHE)
                stale = [
                    k for k, (ts, _) in _RECENT_TASKS_CACHE.items()
                    if (now_ts - ts) > 300
                ]
                for k in stale:
                    _RECENT_TASKS_CACHE.pop(k, None)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "get_recent_tasks falhou no L1 (lawsuit_id=%s): %s",
                    lawsuit_id_int, exc,
                )
                return {
                    "pending": [],
                    "recent_completed": [],
                    "pending_count": 0,
                    "recent_completed_count": 0,
                    "truncated": False,
                    "check_failed": True,
                    "check_error": str(exc)[:200],
                }

        # Lookups de nome (subtipos + tipos) — uma query só pra cada catálogo,
        # evita N+1.
        subtype_ids_seen = {
            t.get("subTypeId") for t in (tasks or []) if t.get("subTypeId") is not None
        }
        type_ids_seen = {
            t.get("typeId") for t in (tasks or []) if t.get("typeId") is not None
        }
        subtype_name_by_id: dict[int, str] = {}
        type_name_by_id: dict[int, str] = {}
        if subtype_ids_seen:
            rows = (
                self.db.query(LegalOneTaskSubType.external_id, LegalOneTaskSubType.name)
                .filter(LegalOneTaskSubType.external_id.in_(subtype_ids_seen))
                .all()
            )
            subtype_name_by_id = {ext: name for ext, name in rows}
        if type_ids_seen:
            rows = (
                self.db.query(LegalOneTaskType.external_id, LegalOneTaskType.name)
                .filter(LegalOneTaskType.external_id.in_(type_ids_seen))
                .all()
            )
            type_name_by_id = {ext: name for ext, name in rows}

        def _to_entry(t: dict) -> dict:
            sid = t.get("statusId")
            type_id = t.get("typeId")
            sub_id = t.get("subTypeId")
            return {
                "task_id": t.get("id"),
                "description": (t.get("description") or "")[:200],
                "status_id": sid,
                "status_label": L1_STATUS_LABELS_FULL.get(sid, f"Status {sid}"),
                "type_id": type_id,
                "type_name": type_name_by_id.get(type_id) if type_id is not None else None,
                "subtype_id": sub_id,
                "subtype_name": (
                    subtype_name_by_id.get(sub_id) if sub_id is not None else None
                ),
                "creation_date": t.get("creationDate"),
                "end_date_time": t.get("endDateTime"),
                "effective_end_date_time": t.get("effectiveEndDateTime"),
                "l1_url": self._build_l1_task_url(t.get("id"), lawsuit_id_int),
            }

        pending: list[dict] = []
        completed: list[dict] = []
        for t in tasks or []:
            sid = t.get("statusId")
            entry = _to_entry(t)
            if sid in L1_BLOCKING_STATUS_IDS:
                pending.append(entry)
            else:
                completed.append(entry)

        # `tasks` já vem ordenado por creationDate desc (do L1). Trunca o
        # completed na quantidade pedida; mantém pending cheio.
        recent_completed = completed[:recent_limit]

        # Como o `_paginated_catalog_loader` segue `@odata.nextLink`
        # automaticamente, em condição normal recebemos TODAS as tarefas
        # do processo (não só as 30 da primeira página). O truncated só
        # vira True em processos com volume realmente fora do comum
        # (>= 200) — defensivo, não esperado no uso típico.
        truncated = len(tasks or []) >= 200

        return {
            "pending": pending,
            "recent_completed": recent_completed,
            "pending_count": len(pending),
            "recent_completed_count": len(recent_completed),
            "completed_total_in_window": len(completed),
            "truncated": truncated,
            "check_failed": False,
        }

    def _apply_required_task_defaults(
        self,
        payload: dict,
        fallback_office_id: Optional[int] = None,
    ) -> None:
        """
        Garante que todo payload enviado pro L1 tenha os campos que a API
        considera obrigatórios: `status.id`, `responsibleOfficeId`,
        `originOfficeId` e `publishDate`. O proposer padrão (_render_proposal)
        já preenche todos, mas quando o frontend manda `payload_override`
        (modal de confirmar/editar tarefa avulsa) pode acontecer dele vir
        sem esses campos — L1 devolve 400 em cascata (um erro por campo
        faltante).

        Regras:
        - `status.id` ausente ou malformado → default 0 (igual _render_proposal).
        - `responsibleOfficeId` ausente → usa `fallback_office_id` quando
          disponível. O modal de Tarefa Avulsa não expõe esse campo no
          form, então o caller (schedule_group) deriva do processo.
        - `originOfficeId` ausente → herda de `responsibleOfficeId` quando
          presente; depois disso, do `fallback_office_id`.
        - `publishDate` ausente → usa `startDateTime` (quando o SubTypeId
          tá preenchido L1 exige esse campo obrigatoriamente); se nem
          `startDateTime` houver, usa now() em UTC.

        Modifica o `payload` in-place.
        """
        # status — precisa ser objeto {"id": <int>}
        status_val = payload.get("status")
        if not isinstance(status_val, dict) or status_val.get("id") is None:
            payload["status"] = {"id": 0}
        # responsibleOfficeId — ausente: tenta o fallback do processo.
        if not payload.get("responsibleOfficeId") and fallback_office_id:
            payload["responsibleOfficeId"] = int(fallback_office_id)
        # originOfficeId — herda de responsibleOfficeId (que agora pode
        # ter sido preenchido acima) ou do fallback_office_id diretamente.
        if not payload.get("originOfficeId"):
            origin = payload.get("responsibleOfficeId") or fallback_office_id
            if origin:
                payload["originOfficeId"] = int(origin)
        # publishDate — obrigatório quando SubTypeId tem valor. Usa o
        # startDateTime (semantica: "data de publicação" == data-base da
        # tarefa no modal de avulsa); fallback pra now() em UTC com sufixo Z.
        if not payload.get("publishDate"):
            fallback = payload.get("startDateTime")
            if not fallback:
                fallback = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            payload["publishDate"] = fallback

    def _render_proposal(self, rec: PublicationRecord, tmpl, lawsuit_responsible: dict = None) -> dict:
        """Monta o dict de payload de tarefa baseado no template + record."""
        from datetime import date as date_cls, timedelta

        # Calcula prazo a partir da data de publicação (ou hoje)
        base_date = None
        if rec.publication_date:
            try:
                base_date = datetime.fromisoformat(
                    rec.publication_date.replace("Z", "+00:00")
                ).date()
            except Exception:
                pass
        if not base_date:
            base_date = date_cls.today()

        # ── Data/hora da tarefa ─────────────────────────────────────
        # Os horarios extraidos das publicacoes (audiencia_hora) e os
        # prazos (23:59:59) sao sempre no fuso LOCAL brasileiro (BRT).
        # O L1 IGNORA o offset ISO (`-03:00`) ao parsear datetime — trata
        # o numero literal como UTC e depois renderiza em BRT (subtrai 3h).
        # Resultado: mandar "11:30:00-03:00" aparece como 08:30 no L1.
        # Solucao (mesma usada em batch_strategies/spreadsheet_strategy):
        # converter o horario local BRT pra UTC com `Z` ANTES de mandar.
        # Assim 11:30 BRT vira 14:30Z, o L1 grava 14:30 UTC, renderiza
        # 11:30 BRT na tela. Visualmente correto.
        BR_TZ = ZoneInfo("America/Sao_Paulo")

        def _brt_to_utc_z(date_str: str, time_str: str) -> str:
            """Converte 'YYYY-MM-DD' + 'HH:MM[:SS]' BRT em ISO UTC com Z."""
            # garante segundos
            if time_str.count(":") == 1:
                time_str = f"{time_str}:00"
            local_dt = datetime.fromisoformat(f"{date_str}T{time_str}").replace(tzinfo=BR_TZ)
            return local_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if rec.audiencia_data:
            if rec.audiencia_hora:
                due_iso = _brt_to_utc_z(rec.audiencia_data, rec.audiencia_hora)
            else:
                # Sem horario: usa 23:59:59 BRT como fallback (fim do dia)
                due_iso = _brt_to_utc_z(rec.audiencia_data, "23:59:59")
        else:
            # Sem audiencia: usa prazo padrao em DIAS ÚTEIS (CPC).
            # `due_business_days` no template é contado em dias úteis —
            # sábados, domingos e feriados nacionais não incrementam o
            # contador. Antes usávamos `timedelta(days=N)` (dias corridos),
            # o que dava vencimentos no fim de semana e era processualmente
            # incorreto.
            from app.services.prazos_iniciais.prazo_calculator import (
                add_business_days,
            )
            due_ref = getattr(tmpl, "due_date_reference", "publication") or "publication"
            due_base = date_cls.today() if due_ref == "today" else base_date
            due_date = add_business_days(due_base, tmpl.due_business_days or 5)
            due_iso = _brt_to_utc_z(due_date.isoformat(), "23:59:59")

        publish_iso = _brt_to_utc_z(base_date.isoformat(), "00:00:00")

        # Interpolação simples de variáveis na descrição/notas
        ctx = {
            "cnj": rec.linked_lawsuit_cnj or "",
            "publication_date": base_date.isoformat(),
            "description": (rec.description or "")[:300],
            "category": rec.category or "",
            "subcategory": rec.subcategory or "",
            "audiencia_data": rec.audiencia_data or "",
            "audiencia_hora": rec.audiencia_hora or "",
            "audiencia_link": rec.audiencia_link or "",
        }
        description_tpl = tmpl.description_template or "Publicação judicial — processo {cnj} em {publication_date}."
        notes_tpl = tmpl.notes_template or ""
        try:
            description = description_tpl.format(**ctx)
        except Exception:
            description = description_tpl
        try:
            notes = notes_tpl.format(**ctx) if notes_tpl else None
        except Exception:
            notes = notes_tpl or None

        # Adiciona link de audiência nas observações quando presente
        if rec.audiencia_link:
            link_note = f"\n\n🔗 Link da audiência virtual: {rec.audiencia_link}"
            notes = (notes or "") + link_note

        # Campos do template via external_id (os FKs são external_id)
        from app.models.legal_one import LegalOneTaskSubType, LegalOneUser, LegalOneOffice

        subtype = self.db.query(LegalOneTaskSubType).options(
            joinedload(LegalOneTaskSubType.parent_type)
        ).filter(LegalOneTaskSubType.external_id == tmpl.task_subtype_external_id).first()

        if not (subtype and subtype.parent_type):
            raise ValueError("Template com referências inválidas (subtype/user).")

        user = None
        if tmpl.responsible_user_external_id is not None:
            user = self.db.query(LegalOneUser).filter(
                LegalOneUser.external_id == tmpl.responsible_user_external_id
            ).first()
            if not user:
                raise ValueError("Template com referências inválidas (subtype/user).")

        # office pode ser None para templates globais (publicações sem processo)
        office = None
        if tmpl.office_external_id:
            office = self.db.query(LegalOneOffice).filter(
                LegalOneOffice.external_id == tmpl.office_external_id
            ).first()
            if not office:
                raise ValueError(f"Escritório {tmpl.office_external_id} não encontrado.")

        # Para publicações sem escritório vinculado, usa o escritório do próprio record se disponível
        effective_office_id = (
            office.external_id if office
            else rec.linked_office_id  # pode ainda ser None
        )

        participants = []
        if user:
            participants.append(self._responsible_participant(user.external_id))
        elif lawsuit_responsible and lawsuit_responsible.get("id"):
            participants.append(self._responsible_participant(lawsuit_responsible.get("id")))

        payload = {
            "description": description,
            "priority": tmpl.priority or "Normal",
            "startDateTime": due_iso,
            "endDateTime": due_iso,
            "publishDate": publish_iso,
            "notes": notes,
            "status": {"id": 0},
            "typeId": subtype.parent_type.external_id,
            "subTypeId": subtype.external_id,
            "participants": participants,
        }
        # Só inclui escritório se disponível
        if effective_office_id:
            payload["responsibleOfficeId"] = effective_office_id
            payload["originOfficeId"] = effective_office_id

        # Texto da publicacao na observacao da PROPOSTA: entra no notes que o
        # modal de agendamento exibe (operador ve e edita a vontade). O backend
        # NAO re-injeta no schedule — respeita a edicao do operador. Helper
        # idempotente + cap de tamanho.
        self._append_publication_notes(payload, rec.description)

        result = {
            "template_id": tmpl.id,
            "template_name": tmpl.name,
            "target_role": getattr(tmpl, "target_role", None) or "principal",
            "target_squad_id": getattr(tmpl, "target_squad_id", None),
            "payload": payload,
            "built_at": datetime.now(timezone.utc).isoformat(),
        }

        # Inclui sugestão de responsável da pasta (puxado da API Legal One)
        if lawsuit_responsible:
            result["suggested_responsible"] = {
                "id": lawsuit_responsible.get("id"),
                "name": lawsuit_responsible.get("name"),
                "email": lawsuit_responsible.get("email"),
                "source": "Legal One - Responsável da pasta",
            }

        return result

    def list_novo_with_text(
        self, linked_office_id = None  # aceita int, CSV str ou lista
    ) -> List[PublicationRecord]:
        """Retorna todos os registros com status NOVO que têm descrição preenchida."""
        query = (
            self.db.query(PublicationRecord)
            .filter(PublicationRecord.status == RECORD_STATUS_NEW)
            .filter(PublicationRecord.is_duplicate == False)
            .filter(PublicationRecord.description.isnot(None))
            .filter(PublicationRecord.description != "")
        )
        if linked_office_id is not None:
            query = query.filter(PublicationRecord.linked_office_id == linked_office_id)
        return query.order_by(PublicationRecord.id).all()

    # ──────────────────────────────────────────────
    # Listagem e status de buscas
    # ──────────────────────────────────────────────

    def list_searches(self, limit: int = 20) -> list[dict[str, Any]]:
        searches = (
            self.db.query(PublicationSearch)
            .order_by(PublicationSearch.created_at.desc())
            .limit(limit)
            .all()
        )
        return [self._search_to_dict(s) for s in searches]

    def get_search(self, search_id: int) -> dict[str, Any]:
        search = self.db.query(PublicationSearch).filter_by(id=search_id).first()
        if not search:
            raise ValueError(f"Busca #{search_id} não encontrada.")
        return self._search_to_dict(search)

    # ──────────────────────────────────────────────
    # CRUD de registros de publicação
    # ──────────────────────────────────────────────

    def list_records(
        self,
        search_id: Optional[int] = None,
        status: Optional[str] = None,
        linked_office_id: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        query = self.db.query(PublicationRecord).filter(PublicationRecord.is_duplicate == False)

        if search_id:
            query = query.filter(PublicationRecord.search_id == search_id)
        status_list = _parse_csv_strs(status)
        if status_list:
            if len(status_list) == 1:
                query = query.filter(PublicationRecord.status == status_list[0])
            else:
                query = query.filter(PublicationRecord.status.in_(status_list))
        office_ids = _parse_csv_ints(linked_office_id)
        if office_ids:
            if len(office_ids) == 1:
                query = query.filter(PublicationRecord.linked_office_id == office_ids[0])
            else:
                query = query.filter(PublicationRecord.linked_office_id.in_(office_ids))

        total = query.count()
        records = (
            query
            .order_by(PublicationRecord.publication_date.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "records": [self._record_to_dict(r) for r in records],
        }

    # ── Helpers para query base filtrada ──────────────────────────────

    def _base_publication_query(
        self,
        search_id: Optional[int] = None,
        status: Optional[str] = None,
        linked_office_id: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        category: Optional[str] = None,
        uf: Optional[str] = None,
        vinculo: Optional[str] = None,
        natureza: Optional[str] = None,
        polo: Optional[str] = None,
        cnj_search: Optional[str] = None,
        scheduled_by_user_id: Optional[str] = None,
    ):
        """Query base reutilizada por list_records_grouped e contagens."""
        query = self.db.query(PublicationRecord).filter(PublicationRecord.is_duplicate == False)  # noqa: E712
        if search_id is not None:
            query = query.filter(PublicationRecord.search_id == search_id)
        # status aceita CSV ("NOVO,CLASSIFICADO"); se lista com >1, usa IN.
        status_list = _parse_csv_strs(status)
        if status_list:
            if len(status_list) == 1:
                query = query.filter(PublicationRecord.status == status_list[0])
            else:
                query = query.filter(PublicationRecord.status.in_(status_list))
        else:
            # Sem filtro explícito, esconde obsoletas (não poluem a listagem principal).
            query = query.filter(PublicationRecord.status != RECORD_STATUS_OBSOLETE)
        # linked_office_id aceita int, CSV ("61,62") ou lista.
        office_ids = _parse_csv_ints(linked_office_id)
        if office_ids:
            if len(office_ids) == 1:
                query = query.filter(PublicationRecord.linked_office_id == office_ids[0])
            else:
                query = query.filter(PublicationRecord.linked_office_id.in_(office_ids))
        if date_from:
            query = query.filter(PublicationRecord.creation_date >= date_from)
        if date_to:
            query = query.filter(PublicationRecord.creation_date < date_to + "T99")
        # Todos os filtros abaixo aceitam CSV ("ativo,passivo") ou lista.
        # Usa .in_(list) quando >1 valor, == quando 1 (preserva plan do query).
        category_list = _parse_csv_strs(category)
        if category_list:
            if len(category_list) == 1:
                query = query.filter(PublicationRecord.category == category_list[0])
            else:
                query = query.filter(PublicationRecord.category.in_(category_list))
        uf_list = [u.strip().upper() for u in _parse_csv_strs(uf)]
        if uf_list:
            if len(uf_list) == 1:
                query = query.filter(PublicationRecord.uf == uf_list[0])
            else:
                query = query.filter(PublicationRecord.uf.in_(uf_list))
        # Vínculo: com_processo / sem_processo. Se ambos vierem juntos
        # equivale a nenhum filtro (todos os registros cabem).
        vinculo_list = _parse_csv_strs(vinculo)
        if vinculo_list and len(vinculo_list) < 2:
            v = vinculo_list[0]
            if v == "sem_processo":
                query = query.filter(PublicationRecord.linked_lawsuit_id.is_(None))
            elif v == "com_processo":
                query = query.filter(PublicationRecord.linked_lawsuit_id.isnot(None))
        natureza_list = _parse_csv_strs(natureza)
        if natureza_list:
            if len(natureza_list) == 1:
                query = query.filter(PublicationRecord.natureza_processo == natureza_list[0])
            else:
                query = query.filter(PublicationRecord.natureza_processo.in_(natureza_list))
        polo_list = [p.strip().lower() for p in _parse_csv_strs(polo)]
        if polo_list:
            if len(polo_list) == 1:
                query = query.filter(PublicationRecord.polo == polo_list[0])
            else:
                query = query.filter(PublicationRecord.polo.in_(polo_list))
        # Cadastrado por — CSV de user_ids do operador que TRATOU a publicação.
        # Tratar = agendar (scheduled_by_user_id) OU dar ciência/IGNORAR
        # (ignored_by_user_id). Antes só casava scheduled_by_user_id, então ao
        # filtrar por um operador os registros que ele apenas IGNOROU sumiam
        # (status IGNORADO tem scheduled_by NULL). Agora casa qualquer das duas
        # autorias — fecha o par scheduled_by_* / ignored_by_* do model.
        scheduled_by_ids = _parse_csv_ints(scheduled_by_user_id)
        if scheduled_by_ids:
            query = query.filter(
                or_(
                    PublicationRecord.scheduled_by_user_id.in_(scheduled_by_ids),
                    PublicationRecord.ignored_by_user_id.in_(scheduled_by_ids),
                )
            )
        # Busca por CNJ: match tolerante por dígitos (ignora máscara do usuário).
        # Comparamos a forma só-dígitos dos dois lados, assim "0000161-07.2026..."
        # e "000016107202680500" casam sem precisar normalizar o input.
        if cnj_search:
            digits = "".join(c for c in cnj_search if c.isdigit())
            if digits:
                query = query.filter(
                    sa_func.regexp_replace(
                        sa_func.coalesce(PublicationRecord.linked_lawsuit_cnj, ""),
                        r"\D", "", "g",
                    ).like(f"%{digits}%")
                )
        return query

    @staticmethod
    def _build_group(items: list) -> dict[str, Any]:
        """Constrói o dict de um grupo a partir de seus records."""
        first = items[0]

        proposed_task = None
        proposed_tasks: list = []
        if isinstance(first.raw_relationships, dict):
            raw_proposal = first.raw_relationships.get("_proposed_task")
            if raw_proposal:
                payload = raw_proposal.get("payload") if "payload" in raw_proposal else raw_proposal
                if isinstance(payload, dict):
                    if raw_proposal.get("template_name"):
                        payload["template_name"] = raw_proposal["template_name"]
                    if raw_proposal.get("suggested_responsible"):
                        payload["suggested_responsible"] = raw_proposal["suggested_responsible"]
                    if raw_proposal.get("target_role"):
                        payload["target_role"] = raw_proposal["target_role"]
                    if raw_proposal.get("target_squad_id"):
                        payload["target_squad_id"] = raw_proposal["target_squad_id"]
                    if raw_proposal.get("template_id"):
                        payload["template_id"] = raw_proposal["template_id"]
                proposed_task = payload
            raw_proposals = first.raw_relationships.get("_proposed_tasks")
            if raw_proposals and isinstance(raw_proposals, list):
                for rp in raw_proposals:
                    p = rp.get("payload") if isinstance(rp, dict) and "payload" in rp else rp
                    if isinstance(p, dict) and isinstance(rp, dict):
                        if rp.get("template_name"):
                            p["template_name"] = rp["template_name"]
                        if rp.get("suggested_responsible"):
                            p["suggested_responsible"] = rp["suggested_responsible"]
                        if rp.get("target_role"):
                            p["target_role"] = rp["target_role"]
                        if rp.get("target_squad_id"):
                            p["target_squad_id"] = rp["target_squad_id"]
                        if rp.get("template_id"):
                            p["template_id"] = rp["template_id"]
                    if p:
                        proposed_tasks.append(p)
            elif proposed_task:
                proposed_tasks = [proposed_task]

        all_classifications: list = []
        for r in items:
            if isinstance(r.raw_relationships, dict):
                cls_list = r.raw_relationships.get("classifications") or []
                if cls_list:
                    all_classifications.extend(cls_list)
            if hasattr(r, "classifications") and r.classifications:
                for c in r.classifications:
                    if c not in all_classifications:
                        all_classifications.append(c)

        return {
            "lawsuit_id": first.linked_lawsuit_id,
            "lawsuit_cnj": first.linked_lawsuit_cnj,
            "office_id": first.linked_office_id,
            "records": [PublicationSearchService._record_to_dict(r) for r in items],
            "proposed_task": proposed_task,
            "proposed_tasks": proposed_tasks,
            "classifications": all_classifications,
        }

    # ── Endpoint principal ────────────────────────────────────────

    def list_records_grouped(
        self,
        search_id: Optional[int] = None,
        status: Optional[str] = None,
        linked_office_id: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        category: Optional[str] = None,
        uf: Optional[str] = None,
        vinculo: Optional[str] = None,
        natureza: Optional[str] = None,
        polo: Optional[str] = None,
        cnj_search: Optional[str] = None,
        scheduled_by_user_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """
        Lista registros agrupados por processo (linked_lawsuit_id).

        Fase 2: filtro UF via SQL (coluna materializada) e paginação de
        grupos no banco em 2 etapas — primeiro busca os lawsuit_ids da
        página, depois carrega apenas os records desses grupos.
        """
        base = self._base_publication_query(
            search_id=search_id, status=status,
            linked_office_id=linked_office_id,
            date_from=date_from, date_to=date_to,
            category=category, uf=uf,
            vinculo=vinculo, natureza=natureza,
            polo=polo, cnj_search=cnj_search,
            scheduled_by_user_id=scheduled_by_user_id,
        )

        # ─── Etapa 1: contar e paginar grupos (lawsuit_ids distintos) ───
        # Calcula uma group_key: lawsuit_id quando existe, senão uma chave
        # sintética. A paginação é sobre essa chave.
        group_key = case(
            (
                PublicationRecord.linked_lawsuit_id.isnot(None),
                sa_func.cast(PublicationRecord.linked_lawsuit_id, sa.String),
            ),
            else_=(
                literal_column("'no-lawsuit|'")
                + sa_func.cast(PublicationRecord.id, sa.String)
            ),
        ).label("group_key")

        groups_subq = (
            base
            .with_entities(group_key, sa_func.count().label("cnt"))
            .group_by(literal_column("group_key"))
            .order_by(literal_column("group_key"))
            .subquery()
        )

        # Uma query so para as duas contagens (antes eram 2 round-trips):
        # count(*) = grupos distintos; sum(cnt) = total de registros.
        _agg = (
            self.db.query(
                sa_func.count(),
                sa_func.coalesce(sa_func.sum(groups_subq.c.cnt), 0),
            )
            .select_from(groups_subq)
            .one()
        )
        total_groups = int(_agg[0] or 0)
        total_records = int(_agg[1] or 0)

        # UFs disponíveis globalmente — respeita todos os filtros EXCETO
        # o próprio UF (senão sumiriam as outras opções assim que o
        # operador marcasse uma). Usado pra popular o dropdown no
        # frontend sem depender dos records da página atual.
        uf_query = self._base_publication_query(
            search_id=search_id, status=status,
            linked_office_id=linked_office_id,
            date_from=date_from, date_to=date_to,
            category=category,
            uf=None,  # ignora o filtro de UF aqui — é o que queremos descobrir
            vinculo=vinculo, natureza=natureza,
            polo=polo, cnj_search=cnj_search,
            scheduled_by_user_id=scheduled_by_user_id,
        )
        available_ufs = [
            row[0] for row in uf_query
            .with_entities(PublicationRecord.uf)
            .filter(PublicationRecord.uf.isnot(None))
            .distinct()
            .order_by(PublicationRecord.uf)
            .all()
            if row[0]
        ]

        # Operadores que aparecem como "Cadastrado por" — alimenta o
        # multiselect do filtro. Mesma lógica do UF: ignora o próprio
        # filtro pra que as opções não sumam após marcar uma. Devolve
        # tuplas {user_id, name, email} pra o frontend renderizar bonito.
        scheduled_by_query = self._base_publication_query(
            search_id=search_id, status=status,
            linked_office_id=linked_office_id,
            date_from=date_from, date_to=date_to,
            category=category, uf=uf,
            vinculo=vinculo, natureza=natureza,
            polo=polo, cnj_search=cnj_search,
            scheduled_by_user_id=None,  # ignora pra descobrir todos
        )
        available_scheduled_by = [
            {"user_id": r[0], "name": r[1] or "", "email": r[2] or ""}
            for r in scheduled_by_query
            .with_entities(
                PublicationRecord.scheduled_by_user_id,
                PublicationRecord.scheduled_by_name,
                PublicationRecord.scheduled_by_email,
            )
            .filter(PublicationRecord.scheduled_by_user_id.isnot(None))
            .distinct()
            .order_by(PublicationRecord.scheduled_by_name)
            .all()
            if r[0] is not None
        ]
        # Inclui também quem deu ciência/IGNOROU (ignored_by_*) — senão um
        # operador que só ignorou publicações (nunca agendou) jamais apareceria
        # como opção de "Cadastrado por". Mesmo escopo de filtro do bloco acima.
        _seen_by_ids = {d["user_id"] for d in available_scheduled_by}
        for r in (
            scheduled_by_query
            .with_entities(
                PublicationRecord.ignored_by_user_id,
                PublicationRecord.ignored_by_name,
                PublicationRecord.ignored_by_email,
            )
            .filter(PublicationRecord.ignored_by_user_id.isnot(None))
            .distinct()
            .all()
        ):
            if r[0] is not None and r[0] not in _seen_by_ids:
                available_scheduled_by.append(
                    {"user_id": r[0], "name": r[1] or "", "email": r[2] or ""}
                )
                _seen_by_ids.add(r[0])
        available_scheduled_by.sort(key=lambda d: (d["name"] or "").lower())

        # Busca as group_keys da página atual
        page_keys_rows = (
            self.db.query(groups_subq.c.group_key)
            .order_by(groups_subq.c.group_key)
            .offset(offset)
            .limit(limit)
            .all()
        )
        page_keys = {row[0] for row in page_keys_rows}

        if not page_keys:
            return {
                "total_groups": total_groups,
                "total_records": total_records,
                "offset": offset,
                "limit": limit,
                "groups": [],
                "available_ufs": available_ufs,
                "available_scheduled_by": available_scheduled_by,
            }

        # ─── Etapa 2: carrega records só dos grupos da página ───────
        # Separa lawsuit_ids numéricos de chaves sintéticas (no-lawsuit).
        lawsuit_ids: set[int] = set()
        synthetic_keys: set[str] = set()
        for k in page_keys:
            try:
                lawsuit_ids.add(int(k))
            except (ValueError, TypeError):
                synthetic_keys.add(k)

        page_query = self._base_publication_query(
            search_id=search_id, status=status,
            linked_office_id=linked_office_id,
            date_from=date_from, date_to=date_to,
            category=category, uf=uf,
            vinculo=vinculo, natureza=natureza,
            polo=polo, cnj_search=cnj_search,
            scheduled_by_user_id=scheduled_by_user_id,
        )

        # Filtro: records que pertencem aos grupos da página
        conditions = []
        if lawsuit_ids:
            conditions.append(PublicationRecord.linked_lawsuit_id.in_(lawsuit_ids))
        if synthetic_keys:
            # Para grupos sem processo, temos que carregar os registros
            # que NÃO têm lawsuit_id e reconstruir a chave em Python.
            conditions.append(PublicationRecord.linked_lawsuit_id.is_(None))
        if conditions:
            page_query = page_query.filter(or_(*conditions))

        records = (
            page_query
            .order_by(
                PublicationRecord.linked_lawsuit_id,
                PublicationRecord.publication_date.desc(),
            )
            .all()
        )

        # Agrupa em Python (apenas os records da página — conjunto pequeno)
        groups_map: dict = defaultdict(list)
        for r in records:
            if r.linked_lawsuit_id:
                key = str(r.linked_lawsuit_id)
            else:
                key = f"no-lawsuit|{r.id}"

            # Só inclui se pertence à página
            if key in page_keys:
                groups_map[key].append(r)

        # Mantém a ordem da paginação
        grouped_list = []
        for k in sorted(groups_map.keys()):
            items = groups_map[k]
            if items:
                grouped_list.append(self._build_group(items))

        return {
            "total_groups": total_groups,
            "total_records": total_records,
            "offset": offset,
            "limit": limit,
            "groups": grouped_list,
            "available_ufs": available_ufs,
            "available_scheduled_by": available_scheduled_by,
        }

    def get_record(self, record_id: int) -> dict[str, Any]:
        record = self.db.query(PublicationRecord).filter_by(id=record_id).first()
        if not record:
            raise ValueError(f"Registro #{record_id} não encontrado.")
        return self._record_to_dict(record, include_full_text=True)

    # ──────────────────────────────────────────────
    # Busca por CNJ (diagnóstico)
    # ──────────────────────────────────────────────

    def _build_audit_labels(self, task_audits: list) -> dict[str, Any]:
        """Resolve os IDs crus da auditoria de agendamento (subtipo, tipo,
        contato/responsável, escritório, status) em nomes legíveis — o
        operador não sabe o que é "subtipo 932" ou "contact 65696". Varre os
        payloads enviados/propostos + diffs de override/ajuste do sistema e
        faz UMA query por catálogo local (sem tocar no L1)."""
        subtype_ids: set[int] = set()
        type_ids: set[int] = set()
        contact_ids: set[int] = set()
        office_ids: set[int] = set()

        def _add(bucket: set[int], value: Any) -> None:
            if isinstance(value, bool):
                return
            if isinstance(value, int):
                bucket.add(value)
            elif isinstance(value, str) and value.lstrip("-").isdigit():
                bucket.add(int(value))

        def _collect_from_payload(payload: Any) -> None:
            if not isinstance(payload, dict):
                return
            _add(subtype_ids, payload.get("subTypeId"))
            _add(type_ids, payload.get("typeId"))
            _add(office_ids, payload.get("responsibleOfficeId"))
            _add(office_ids, payload.get("originOfficeId"))
            for part in payload.get("participants") or []:
                if isinstance(part, dict):
                    _add(contact_ids, (part.get("contact") or {}).get("id"))

        field_buckets = {
            "subTypeId": subtype_ids,
            "responsavel_contact_id": contact_ids,
            "responsibleOfficeId": office_ids,
            "originOfficeId": office_ids,
        }
        for audit in task_audits or []:
            _add(subtype_ids, audit.get("subtype_id"))
            _collect_from_payload(audit.get("sent_payload"))
            _collect_from_payload(audit.get("proposed_payload"))
            for diff_map in (audit.get("override_fields"), audit.get("system_adjustments")):
                for field, diff in (diff_map or {}).items():
                    bucket = field_buckets.get(field)
                    if bucket is None or not isinstance(diff, dict):
                        continue
                    for side in ("proposto", "enviado", "antes", "depois"):
                        _add(bucket, diff.get(side))

        from app.models.legal_one import (
            LegalOneOffice as _Office,
            LegalOneTaskSubType as _SubType,
            LegalOneTaskType as _TaskType,
            LegalOneUser as _User,
        )

        labels: dict[str, Any] = {
            "subtypes": {},
            "types": {},
            "contacts": {},
            "offices": {},
            "statuses": {str(k): v for k, v in L1_STATUS_LABELS_FULL.items()},
        }
        if subtype_ids:
            rows = (
                self.db.query(_SubType.external_id, _SubType.name)
                .filter(_SubType.external_id.in_(subtype_ids))
                .all()
            )
            labels["subtypes"] = {str(r[0]): r[1] for r in rows}
        if type_ids:
            rows = (
                self.db.query(_TaskType.external_id, _TaskType.name)
                .filter(_TaskType.external_id.in_(type_ids))
                .all()
            )
            labels["types"] = {str(r[0]): r[1] for r in rows}
        if contact_ids:
            rows = (
                self.db.query(_User.external_id, _User.name)
                .filter(_User.external_id.in_(contact_ids))
                .all()
            )
            labels["contacts"] = {str(r[0]): r[1] for r in rows}
        if office_ids:
            # path (hierarquia completa) > name (folha) — convenção da casa.
            rows = (
                self.db.query(_Office.external_id, _Office.path, _Office.name)
                .filter(_Office.external_id.in_(office_ids))
                .all()
            )
            labels["offices"] = {str(r[0]): (r[1] or r[2]) for r in rows}
        return labels

    def lookup_by_cnj(self, cnj: str) -> dict[str, Any]:
        """
        Dado um CNJ (com ou sem formatação), retorna tudo que o sistema tem
        sobre aquele processo: buscas que o alcançaram, publicações encontradas,
        classificações atribuídas e estado atual na fila de tratamento (RPA).

        Serve para o operador diagnosticar "o robô já pegou esse processo?
        como classificou? está na fila pra tratar?".
        """
        from app.models.publication_treatment import PublicationTreatmentItem

        raw = (cnj or "").strip()
        digits = "".join(c for c in raw if c.isdigit())
        if not digits:
            raise ValueError("CNJ inválido: informe ao menos os dígitos.")

        # Match tolerante a formatação: compara a forma só-dígitos dos dois lados.
        records = (
            self.db.query(PublicationRecord)
            .filter(
                sa_func.regexp_replace(
                    sa_func.coalesce(PublicationRecord.linked_lawsuit_cnj, ""),
                    r"\D", "", "g",
                )
                == digits
            )
            .order_by(
                PublicationRecord.publication_date.desc().nullslast(),
                PublicationRecord.id.desc(),
            )
            .all()
        )

        # Recupera treatment_items numa query só (evita N+1)
        record_ids = [r.id for r in records]
        treatment_by_record: dict[int, PublicationTreatmentItem] = {}
        if record_ids:
            items = (
                self.db.query(PublicationTreatmentItem)
                .filter(PublicationTreatmentItem.publication_record_id.in_(record_ids))
                .all()
            )
            treatment_by_record = {it.publication_record_id: it for it in items}

        # Buscas distintas que originaram esses registros
        search_ids = sorted({r.search_id for r in records if r.search_id})
        searches: list[dict[str, Any]] = []
        if search_ids:
            search_rows = (
                self.db.query(PublicationSearch)
                .filter(PublicationSearch.id.in_(search_ids))
                .order_by(PublicationSearch.id.desc())
                .all()
            )
            for s in search_rows:
                searches.append({
                    "id": s.id,
                    "status": s.status,
                    "date_from": s.date_from,
                    "date_to": s.date_to,
                    "office_filter": s.office_filter,
                    "requested_by_email": s.requested_by_email,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                    "finished_at": s.finished_at.isoformat() if s.finished_at else None,
                    "total_found": s.total_found,
                    "total_new": s.total_new,
                    "total_duplicate": s.total_duplicate,
                })

        # Monta payload de cada publicação com detalhe de classificação + RPA
        records_payload: list[dict[str, Any]] = []
        status_counts: dict[str, int] = {}
        category_counts: dict[str, int] = {}
        queue_counts: dict[str, int] = {}
        duplicate_count = 0

        for r in records:
            base = self._record_to_dict(r, include_full_text=True)
            status_counts[r.status] = status_counts.get(r.status, 0) + 1
            if r.category:
                key = r.category + (f" / {r.subcategory}" if r.subcategory and r.subcategory != "-" else "")
                category_counts[key] = category_counts.get(key, 0) + 1
            if r.is_duplicate:
                duplicate_count += 1

            item = treatment_by_record.get(r.id)
            if item is not None:
                queue_counts[item.queue_status] = queue_counts.get(item.queue_status, 0) + 1
                base["treatment"] = {
                    "id": item.id,
                    "queue_status": item.queue_status,
                    "target_status": item.target_status,
                    "source_record_status": item.source_record_status,
                    "attempt_count": item.attempt_count,
                    "last_run_id": item.last_run_id,
                    "last_error": item.last_error,
                    "treated_at": item.treated_at.isoformat() if item.treated_at else None,
                    "last_attempt_at": item.last_attempt_at.isoformat() if item.last_attempt_at else None,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                    "updated_at": item.updated_at.isoformat() if item.updated_at else None,
                }
            else:
                base["treatment"] = None

            base["is_duplicate"] = r.is_duplicate
            records_payload.append(base)

        lawsuit_id = next(
            (r.linked_lawsuit_id for r in records if r.linked_lawsuit_id is not None),
            None,
        )
        cnj_display = next(
            (r.linked_lawsuit_cnj for r in records if r.linked_lawsuit_cnj),
            raw,
        )

        # Auditoria de agendamento (publicacao_tarefa_audit): payload EXATO
        # enviado ao L1 por tarefa criada + quem agendou + diff vs. a proposta
        # automática. A proposta sozinha não conta o que o operador
        # EFETIVAMENTE agendou — é isso que fecha a lacuna de auditoria.
        from app.models.publication_task_audit import PublicationTaskAudit

        audit_filters = []
        if lawsuit_id is not None:
            audit_filters.append(PublicationTaskAudit.lawsuit_id == lawsuit_id)
        if record_ids:
            audit_filters.append(PublicationTaskAudit.publication_record_id.in_(record_ids))
        task_audits: list[dict[str, Any]] = []
        if audit_filters:
            audit_rows = (
                self.db.query(PublicationTaskAudit)
                .filter(or_(*audit_filters))
                .order_by(PublicationTaskAudit.id.desc())
                .all()
            )
            for a in audit_rows:
                task_audits.append({
                    "id": a.id,
                    "publication_record_id": a.publication_record_id,
                    "created_task_id": a.created_task_id,
                    "subtype_id": a.subtype_id,
                    "override_detected": a.override_detected,
                    "override_fields": a.override_fields,
                    "system_adjustments": a.system_adjustments,
                    "scheduled_by_name": a.scheduled_by_name,
                    "scheduled_by_email": a.scheduled_by_email,
                    "scheduled_at": a.scheduled_at.isoformat() if a.scheduled_at else None,
                    "sent_payload": a.sent_payload,
                    "proposed_payload": a.proposed_payload,
                    "l1_task_url": (
                        self._build_l1_task_url(a.created_task_id, lawsuit_id)
                        if a.created_task_id and lawsuit_id
                        else None
                    ),
                })

        # Link direto pro template que gerou cada proposta: casa o subtipo da
        # auditoria com os _proposed_tasks dos registros (template_id vive lá).
        # Deixa o operador pular da auditoria pra configuração do template e
        # conferir se a regra está batendo.
        template_by_subtype: dict[int, dict[str, Any]] = {}
        for r in records:
            rr = r.raw_relationships if isinstance(r.raw_relationships, dict) else {}
            props = rr.get("_proposed_tasks")
            if not isinstance(props, list):
                single = rr.get("_proposed_task")
                props = [single] if single else []
            for p in props:
                if not isinstance(p, dict) or not p.get("template_id"):
                    continue
                sub = (p.get("payload") or {}).get("subTypeId")
                if sub is None:
                    continue
                template_by_subtype.setdefault(int(sub), {
                    "template_id": p.get("template_id"),
                    "template_name": p.get("template_name"),
                })
        for audit in task_audits:
            tpl = (
                template_by_subtype.get(int(audit["subtype_id"]))
                if audit.get("subtype_id") is not None
                else None
            )
            audit["template_id"] = (tpl or {}).get("template_id")
            audit["template_name"] = (tpl or {}).get("template_name")

        # Mapeia search_id → requested_by_email para cada publicação
        search_by_id = {s["id"]: s for s in searches}
        for rec in records_payload:
            sid = rec.get("search_id")
            s_info = search_by_id.get(sid, {})
            rec["requested_by_email"] = s_info.get("requested_by_email")

        # Enriquece com dados do processo no Legal One (se tiver lawsuit_id)
        lawsuit_info: dict[str, Any] | None = None
        if lawsuit_id is not None:
            try:
                lawsuit_map = self.client.fetch_lawsuits_by_ids([lawsuit_id])
                raw_lawsuit = lawsuit_map.get(lawsuit_id)
                if raw_lawsuit:
                    # Resolve nome do escritório
                    office_id = raw_lawsuit.get("responsibleOfficeId")
                    office_name = None
                    if office_id is not None:
                        from app.models.legal_one import LegalOneOffice
                        office = (
                            self.db.query(LegalOneOffice)
                            .filter(LegalOneOffice.external_id == office_id)
                            .first()
                        )
                        office_name = office.name if office else None

                    lawsuit_info = {
                        "id": raw_lawsuit.get("id"),
                        "cnj": raw_lawsuit.get("identifierNumber"),
                        "creation_date": raw_lawsuit.get("creationDate"),
                        "responsible_office_id": office_id,
                        "responsible_office_name": office_name,
                    }
            except Exception as exc:
                logger.warning("Falha ao buscar dados do processo %s no Legal One: %s", lawsuit_id, exc)

        # Constrói timeline de eventos a partir dos timestamps disponíveis
        timeline: list[dict[str, Any]] = []
        for rec in records_payload:
            pub_id = rec.get("id")
            # 1) Captura
            if rec.get("created_at"):
                timeline.append({
                    "timestamp": rec["created_at"],
                    "event": "captura",
                    "label": "Publicação capturada pelo robô",
                    "detail": f"Busca #{rec.get('search_id')} · Publicação de {rec.get('publication_date') or '?'}",
                    "user": rec.get("requested_by_email"),
                    "record_id": pub_id,
                })
            # 2) Classificação
            if rec.get("category") and rec.get("updated_at"):
                cls_detail = rec.get("category", "")
                if rec.get("subcategory") and rec["subcategory"] != "-":
                    cls_detail += f" / {rec['subcategory']}"
                if rec.get("polo"):
                    cls_detail += f" · polo: {rec['polo']}"
                timeline.append({
                    "timestamp": rec["updated_at"],
                    "event": "classificacao",
                    "label": "Classificada pela IA",
                    "detail": cls_detail,
                    "user": None,
                    "record_id": pub_id,
                })
            # 3) Status terminal (agendado, ignorado, obsoleta)
            status = rec.get("status", "")
            if status in ("AGENDADO", "IGNORADO", "DESCARTADO_OBSOLETA") and rec.get("updated_at"):
                label_map = {
                    "AGENDADO": "Tarefa agendada no Legal One",
                    "IGNORADO": "Publicação ignorada pelo operador (ciência)",
                    "DESCARTADO_OBSOLETA": "Descartada como obsoleta (anterior à criação da pasta)",
                }
                # Autoria real (pub002/pub003) + timestamp dedicado da ação —
                # updated_at muda em qualquer edição e mentiria a hora.
                event_user = None
                event_ts = rec["updated_at"]
                if status == "AGENDADO":
                    event_user = rec.get("scheduled_by_name") or rec.get("scheduled_by_email")
                    event_ts = rec.get("scheduled_at") or event_ts
                elif status == "IGNORADO":
                    event_user = rec.get("ignored_by_name") or rec.get("ignored_by_email")
                    event_ts = rec.get("ignored_at") or event_ts
                timeline.append({
                    "timestamp": event_ts,
                    "event": "status_change",
                    "label": label_map.get(status, f"Status → {status}"),
                    "detail": None,
                    "user": event_user,
                    "record_id": pub_id,
                })
            # 4) Tratamento RPA
            treatment = rec.get("treatment")
            if treatment:
                if treatment.get("created_at"):
                    timeline.append({
                        "timestamp": treatment["created_at"],
                        "event": "rpa_enfileirada",
                        "label": f"Enfileirada pro RPA ({treatment.get('target_status') or '?'})",
                        "detail": None,
                        "user": None,
                        "record_id": pub_id,
                    })
                if treatment.get("treated_at"):
                    timeline.append({
                        "timestamp": treatment["treated_at"],
                        "event": "rpa_concluida",
                        "label": "RPA tratou no Legal One",
                        "detail": f"Tentativas: {treatment.get('attempt_count', 0)}",
                        "user": None,
                        "record_id": pub_id,
                    })
                if treatment.get("last_error"):
                    timeline.append({
                        "timestamp": treatment.get("last_attempt_at") or treatment.get("updated_at") or "",
                        "event": "rpa_erro",
                        "label": "Erro no RPA",
                        "detail": treatment["last_error"],
                        "user": None,
                        "record_id": pub_id,
                    })

        # Tarefas efetivamente criadas no L1 (auditoria de agendamento) —
        # inclui o "quem" e sinaliza override humano vs. proposta automática.
        _override_labels = {
            "subTypeId": "tipo de tarefa",
            "responsibleOfficeId": "escritório",
            "responsavel_contact_id": "responsável",
        }
        for audit in task_audits:
            if not audit.get("scheduled_at"):
                continue
            override = audit.get("override_fields") or {}
            adjustments = audit.get("system_adjustments") or {}
            summary_parts = []
            if override:
                summary_parts.append(
                    "Operador alterou "
                    + ", ".join(_override_labels.get(k, k) for k in override)
                    + " em relação à proposta automática."
                )
            if adjustments:
                summary_parts.append(
                    "Sistema ajustou automaticamente: "
                    + ", ".join(sorted(adjustments))
                    + "."
                )
            override_summary = " ".join(summary_parts) or None
            timeline.append({
                "timestamp": audit["scheduled_at"],
                "event": "tarefa_criada",
                "label": (
                    f"Tarefa #{audit['created_task_id']} criada no Legal One"
                    if audit.get("created_task_id")
                    else "Tarefa criada no Legal One"
                ),
                "detail": override_summary,
                "user": audit.get("scheduled_by_name") or audit.get("scheduled_by_email"),
                "record_id": audit.get("publication_record_id"),
            })

        # Ordena timeline cronologicamente (mais recente primeiro)
        timeline.sort(key=lambda e: e.get("timestamp") or "", reverse=True)

        return {
            "cnj_input": raw,
            "cnj_normalized": digits,
            "cnj_display": cnj_display,
            "lawsuit_id": lawsuit_id,
            "lawsuit_info": lawsuit_info,
            "found": len(records) > 0,
            "totals": {
                "records": len(records),
                "duplicates": duplicate_count,
                "by_status": status_counts,
                "by_category": category_counts,
                "by_queue_status": queue_counts,
            },
            "timeline": timeline,
            "searches": searches,
            "records": records_payload,
            "task_audits": task_audits,
            "task_audit_labels": self._build_audit_labels(task_audits),
        }

    def update_record_status(
        self, record_id: int, new_status: str, acted_by: Optional[Any] = None
    ) -> dict[str, Any]:
        valid_statuses = {
            RECORD_STATUS_NEW, RECORD_STATUS_CLASSIFIED,
            RECORD_STATUS_SCHEDULED, RECORD_STATUS_IGNORED, RECORD_STATUS_ERROR,
        }
        if new_status not in valid_statuses:
            raise ValueError(f"Status inválido: {new_status}")

        record = self.db.query(PublicationRecord).filter_by(id=record_id).first()
        if not record:
            raise ValueError(f"Registro #{record_id} não encontrado.")

        now_utc = datetime.now(timezone.utc)
        record.status = new_status
        record.updated_at = now_utc

        # Autoria da ciência: quando o operador move o registro pra IGNORADO
        # ("dar ciência"), registra quem fez e quando. Sobrescreve sempre o
        # ato vigente (mesmo padrão de scheduled_by_* em schedule_group/
        # schedule_records) — assim o placar reflete a ação real e o
        # ignored_at carimba o momento, não um primeiro toque antigo.
        if new_status == RECORD_STATUS_IGNORED and acted_by is not None:
            record.ignored_by_user_id = getattr(acted_by, "id", None)
            record.ignored_by_email = getattr(acted_by, "email", None)
            record.ignored_by_name = getattr(acted_by, "name", None)
            record.ignored_at = now_utc

        from app.services.publication_treatment_service import PublicationTreatmentService
        treatment_service = PublicationTreatmentService(self.db)
        treatment_service.sync_item_from_record(record, commit=False)
        self.db.commit()
        return self._record_to_dict(record)

    def reclassify_records(
        self,
        record_ids: List[int],
        category: str,
        subcategory: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Aplica manualmente uma classificação (category/subcategory) a um conjunto
        de registros. Preserva polo / audiência / classificações secundárias do JSON
        `classifications` — só altera a classificação primária (index 0).

        Em seguida, reconstrói as propostas de tarefa para esses registros usando
        `_build_task_proposals`. A busca no L1 fica limitada aos templates sem
        responsável nominal.
        """
        if not record_ids:
            raise ValueError("Nenhum registro informado.")
        category = (category or "").strip()
        if not category:
            raise ValueError("Categoria obrigatória.")
        subcategory = (subcategory or "").strip() or None

        records = (
            self.db.query(PublicationRecord)
            .filter(PublicationRecord.id.in_(record_ids))
            .all()
        )
        if not records:
            raise ValueError("Nenhum registro encontrado.")

        # Captura feedback implícito: se a classificação mudou, registra
        from app.models.classification_feedback import ClassificationFeedback
        for rec in records:
            if rec.category and rec.category != category or (rec.subcategory or None) != subcategory:
                excerpt = (rec.description or "")[:500]
                if excerpt:
                    fb = ClassificationFeedback(
                        record_id=rec.id,
                        feedback_type="implicit",
                        original_category=rec.category,
                        original_subcategory=rec.subcategory,
                        corrected_category=category,
                        corrected_subcategory=subcategory,
                        corrected_polo=rec.polo,
                        text_excerpt=excerpt,
                        office_external_id=rec.linked_office_id,
                    )
                    self.db.add(fb)

        for rec in records:
            rec.category = category
            rec.subcategory = subcategory
            # Atualiza a classificação primária (index 0) do array JSON mantendo
            # campos adicionais (polo, confianca, justificativa, audiencia_*).
            existing = rec.classifications if isinstance(rec.classifications, list) else []
            if existing:
                primary = dict(existing[0])
                primary["categoria"] = category
                primary["subcategoria"] = subcategory
                # Flag para auditoria
                primary["origem"] = "manual_override"
                new_list = [primary] + list(existing[1:])
            else:
                new_list = [{
                    "categoria": category,
                    "subcategoria": subcategory,
                    "polo": rec.polo,
                    "audiencia_data": rec.audiencia_data,
                    "audiencia_hora": rec.audiencia_hora,
                    "audiencia_link": rec.audiencia_link,
                    "confianca": None,
                    "justificativa": "Classificação manual do operador.",
                    "origem": "manual_override",
                }]
            rec.classifications = new_list
            # Se estiver NOVO, promove para CLASSIFICADO
            if rec.status == RECORD_STATUS_NEW:
                rec.status = RECORD_STATUS_CLASSIFIED
            rec.updated_at = datetime.now(timezone.utc)

        self.db.commit()

        # Reconstrói a proposta de tarefa com o template correspondente à
        # nova classificação (se houver template cadastrado).
        self._build_task_proposals(records)

        return {
            "updated_record_ids": [r.id for r in records],
            "category": category,
            "subcategory": subcategory,
        }

    # ──────────────────────────────────────────────
    # Agendamento (criação de tarefa no LegalOne)
    # ──────────────────────────────────────────────

    def _apply_squad_routing_server_side(
        self,
        *,
        payloads: list[dict],
        proposals: list,
        lawsuit_id: int,
        operator_locked: Optional[list] = None,
        routing_notes: Optional[list] = None,
    ) -> None:
        """
        Aplica `target_role` / `target_squad_id` do template (gravado em
        `_proposed_task` no momento da classificacao/rebuild) substituindo
        `participants[0].contact.id` pelo assistente/lider de support
        squad resolvido server-side via `resolve_target`.

        Pareamento payload ↔ proposal: por `subTypeId`. Tarefa avulsa
        (sem proposal) e' pulada (sem template = sem regra).

        Override: se `payload.participants[0].contact.id` (o que o
        frontend mandou) e' diferente do `proposal.payload.participants
        [0].contact.id` (o que o template gravou), respeita — o operador
        trocou OU o frontend ja' resolveu via /claim.

        Em erro do resolver (ex.: squad sem assistente cadastrado),
        levanta ValueError pra que o caller aborte com mensagem humana
        antes de qualquer task ir pro L1.
        """
        if not proposals:
            return

        # Indexa proposals por subTypeId pra parear com cada payload.
        proposals_by_subtype: dict[int, dict] = {}
        original_responsible_by_subtype: dict[int, int] = {}
        for prop in proposals:
            if not isinstance(prop, dict):
                continue
            payload_of_prop = prop.get("payload") or {}
            sub = payload_of_prop.get("subTypeId")
            if sub is None:
                continue
            proposals_by_subtype[int(sub)] = prop
            parts = payload_of_prop.get("participants") or []
            if parts and isinstance(parts[0], dict):
                contact = parts[0].get("contact") or {}
                if contact.get("id") is not None:
                    original_responsible_by_subtype[int(sub)] = int(contact["id"])

        from app.services.squad_assistant_resolver import resolve_target

        for payload_idx, payload in enumerate(payloads):
            if not isinstance(payload, dict):
                continue
            # Trava do operador: ele mexeu no responsável no modal (mesmo
            # re-escolhendo a mesma pessoa) — decisão final dele, regra
            # automática NUNCA sobrepõe.
            if operator_locked and payload_idx < len(operator_locked) and operator_locked[payload_idx]:
                logger.info(
                    "publications.routing lawsuit=%s payload=%s SKIP "
                    "responsavel travado pelo operador (decisao final).",
                    lawsuit_id, payload_idx,
                )
                continue
            sub = payload.get("subTypeId")
            if sub is None:
                continue
            prop = proposals_by_subtype.get(int(sub))
            if not prop:
                # Tarefa avulsa OU subtipo trocado pelo operador — sem
                # template casado, regra do assistente nao se aplica.
                continue

            target_role = (prop.get("target_role") or "principal")
            target_squad_id = prop.get("target_squad_id")

            # Cenario 1 (principal sem support squad) → nada a fazer.
            if target_role == "principal" and not target_squad_id:
                continue

            parts = payload.get("participants") or []
            if not parts or not isinstance(parts[0], dict):
                continue
            contact = parts[0].get("contact") or {}
            current_id = contact.get("id")
            original_id = original_responsible_by_subtype.get(int(sub))

            # Override manual — operador trocou no modal OU frontend ja'
            # resolveu via /claim. Nao queremos substituir nem rodar o
            # round-robin de novo (cliente que avancou ja avancou).
            if (
                current_id is not None
                and original_id is not None
                and int(current_id) != int(original_id)
            ):
                logger.info(
                    "publications.routing lawsuit=%s subType=%s SKIP override "
                    "current=%s original=%s",
                    lawsuit_id, sub, current_id, original_id,
                )
                continue

            office_external_id = (
                payload.get("responsibleOfficeId")
                or payload.get("originOfficeId")
            )
            try:
                result = resolve_target(
                    self.db,
                    target_role=target_role,
                    responsible_user_external_id=int(original_id) if original_id else 0,
                    target_squad_id=int(target_squad_id) if target_squad_id else None,
                    office_external_id=int(office_external_id) if office_external_id else None,
                    task_subtype_external_id=int(sub),
                    commit=True,
                )
            except ValueError as exc:
                # Squad sem assistente / squad invalida — relevanta pra
                # caller (schedule_group) abortar com mensagem humana
                # antes de criar qualquer task no L1.
                raise ValueError(
                    f"Squad routing falhou (subType={sub}, "
                    f"target_role={target_role!r}): {exc}"
                ) from exc

            payload["participants"] = [{
                "contact": {"id": int(result.user_external_id)},
                "isResponsible": True,
                "isExecuter": True,
                "isRequester": True,
            }]
            if routing_notes is not None and payload_idx < len(routing_notes):
                routing_notes[payload_idx] = {
                    "antes": int(current_id) if current_id is not None else None,
                    "depois": int(result.user_external_id),
                    "motivo": (
                        "Roteado pela regra do template (target_role="
                        + repr(target_role)
                        + (f", squad de suporte #{target_squad_id}" if target_squad_id else "")
                        + ") — não foi alteração do operador."
                    ),
                }
            logger.info(
                "publications.routing lawsuit=%s subType=%s "
                "template_target_role=%s target_squad_id=%s "
                "original=%s final=%s squad=%s fallback=%s",
                lawsuit_id, sub, target_role, target_squad_id,
                original_id, result.user_external_id,
                result.squad_name, result.fallback_reason,
            )

    def _append_publication_notes(self, payload, pub_text):
        """Anexa o texto da publicacao nas observacoes (notes) da tarefa.

        Decisao do operador (2026-06): TODA tarefa a agendar leva o texto da
        publicacao na observacao, independente do notes_template. Idempotente
        (marcador evita duplicar em retry/reagendamento) e com cap de tamanho
        pra nao estourar o campo de notes do Legal One."""
        if not isinstance(payload, dict):
            return
        text = (pub_text or "").strip()
        if not text:
            return
        mark = "--- Texto da Publicação ---"
        current = payload.get("notes") or ""
        if mark in current:
            return
        if len(text) > 4000:
            text = text[:4000] + " [...]"
        nl = chr(10)
        block = nl + nl + mark + nl + text
        payload["notes"] = (current + block) if current else (mark + nl + text)

    _SYSTEM_ADJUSTMENT_REASONS = {
        "endDateTime": "Data de conclusão no passado ao confirmar — sistema moveu pro próximo dia útil (regra do L1).",
        "startDateTime": "Data de início ajustada junto com a conclusão (próximo dia útil).",
        "description": "Descrição ajustada automaticamente ao limite de 250 caracteres do L1.",
        "status": "Status obrigatório preenchido automaticamente.",
        "responsibleOfficeId": "Escritório responsável preenchido automaticamente (campo obrigatório).",
        "originOfficeId": "Escritório de origem herdado automaticamente (campo obrigatório).",
        "publishDate": "Data de publicação preenchida automaticamente (campo obrigatório).",
    }

    @classmethod
    def _diff_system_adjustments(cls, before: dict, after: dict) -> dict:
        """Diff campo a campo entre o payload como o operador confirmou e o
        payload após os ajustes mecânicos do backend (_enforce_description_limit,
        _apply_required_task_defaults, _ensure_endtime_in_future). Alimenta a
        categoria "ajuste automático do sistema" da auditoria — separada do
        override humano, pra diagnosticar divergência operador × Legal One."""
        adjustments: dict = {}
        if not isinstance(before, dict) or not isinstance(after, dict):
            return adjustments
        for key in set(before.keys()) | set(after.keys()):
            antes = before.get(key)
            depois = after.get(key)
            if antes == depois:
                continue
            adjustments[key] = {
                "antes": antes,
                "depois": depois,
                "motivo": cls._SYSTEM_ADJUSTMENT_REASONS.get(
                    key, "Ajuste automático do sistema antes do envio ao L1."
                ),
            }
        return adjustments

    def _record_scheduled_task_audit(
        self,
        *,
        lawsuit_id: Optional[int],
        records: list,
        proposals: list,
        sent_payloads: list,
        created_task_ids: list,
        sb_user_id: Optional[int],
        sb_email: Optional[str],
        sb_name: Optional[str],
        when,
        system_adjustments: Optional[list] = None,
    ) -> None:
        """Persiste auditoria por tarefa criada: payload EXATO enviado ao L1 +
        quem agendou + diff vs. a proposta automática (flag de override humano).
        Best-effort: nunca quebra o agendamento se a auditoria falhar."""
        try:
            from app.models.publication_task_audit import PublicationTaskAudit

            prop_by_sub: dict[int, dict] = {}
            single_prop = None
            for p in (proposals or []):
                pay = (p or {}).get("payload") or {}
                single_prop = single_prop or pay
                sub = pay.get("subTypeId")
                if sub is not None:
                    prop_by_sub[int(sub)] = pay

            def _contact(pl: dict):
                parts = pl.get("participants") or []
                if parts and isinstance(parts[0], dict):
                    return (parts[0].get("contact") or {}).get("id")
                return None

            rec_id = records[0].id if records else None
            for idx, (sent, task_id) in enumerate(zip(sent_payloads, created_task_ids)):
                if not isinstance(sent, dict):
                    continue
                adjustments = None
                if system_adjustments and idx < len(system_adjustments):
                    adjustments = system_adjustments[idx] or None
                sub = sent.get("subTypeId")
                prop = prop_by_sub.get(int(sub)) if sub is not None else None
                if prop is None and len(prop_by_sub) <= 1:
                    prop = single_prop
                override_fields: dict = {}
                if prop:
                    checks = {
                        "subTypeId": (prop.get("subTypeId"), sent.get("subTypeId")),
                        "responsibleOfficeId": (prop.get("responsibleOfficeId"), sent.get("responsibleOfficeId")),
                        "responsavel_contact_id": (_contact(prop), _contact(sent)),
                    }
                    for field, (proposto, enviado) in checks.items():
                        # Campo mudado pelo SISTEMA (ex.: office default) não é
                        # override do operador — fica só em system_adjustments.
                        if adjustments and field in adjustments:
                            continue
                        if proposto is not None and proposto != enviado:
                            override_fields[field] = {"proposto": proposto, "enviado": enviado}
                self.db.add(PublicationTaskAudit(
                    lawsuit_id=lawsuit_id,
                    publication_record_id=rec_id,
                    subtype_id=int(sub) if sub is not None else None,
                    created_task_id=task_id,
                    sent_payload=sent,
                    proposed_payload=prop,
                    override_detected=bool(override_fields),
                    override_fields=override_fields or None,
                    system_adjustments=adjustments,
                    scheduled_by_user_id=sb_user_id,
                    scheduled_by_name=sb_name,
                    scheduled_by_email=sb_email,
                    scheduled_at=when,
                ))
        except Exception as exc:  # noqa: BLE001
            logger.warning("publications: falha ao gravar auditoria de agendamento: %s", exc)

    def schedule_group(
        self,
        lawsuit_id: int,
        payload_override: Optional[dict] = None,
        payload_overrides: Optional[list[dict]] = None,
        scheduled_by: Optional[Any] = None,
        force_duplicate: bool = False,
    ) -> dict[str, Any]:
        """
        Executa o agendamento de um grupo de publicações (mesmo processo) no LegalOne.

        Aceita 1 ou N payloads numa única chamada. N tarefas são criadas e
        vinculadas ao processo; os registros só são marcados como SCHEDULED
        depois de TODAS as tarefas serem criadas com sucesso.
        """
        records = (
            self.db.query(PublicationRecord)
            .filter(PublicationRecord.linked_lawsuit_id == lawsuit_id)
            .filter(PublicationRecord.status.in_([RECORD_STATUS_NEW, RECORD_STATUS_CLASSIFIED, RECORD_STATUS_IGNORED]))
            .all()
        )
        if not records:
            raise ValueError("Nenhuma publicação pendente para este processo.")

        first = records[0]
        proposals = []
        if isinstance(first.raw_relationships, dict):
            pt = first.raw_relationships.get("_proposed_tasks")
            if isinstance(pt, list):
                proposals = pt
            else:
                p = first.raw_relationships.get("_proposed_task")
                if p:
                    proposals = [p]

        # Determina a lista de payloads a criar
        if payload_overrides:
            payloads = list(payload_overrides)
        elif payload_override:
            payloads = [payload_override]
        else:
            payloads = [p.get("payload") for p in proposals if p and p.get("payload")]

        if not payloads:
            raise ValueError("Proposta de tarefa inexistente. Configure um template.")

        # Regra de negócio: a decisão FINAL do operador vence qualquer regra
        # automática. O modal marca `_responsible_overridden` quando o
        # operador mexeu no responsável — inclusive re-escolhendo a MESMA
        # pessoa da proposta (antes esse caso era indistinguível de "não
        # mexeu" e o squad routing atropelava a escolha). O marcador trava o
        # routing abaixo e é removido aqui pra nunca vazar pro L1.
        operator_locked = [
            bool(p.pop("_responsible_overridden", False)) if isinstance(p, dict) else False
            for p in payloads
        ]

        self._apply_lawsuit_responsible_to_missing_payloads(payloads, lawsuit_id)

        # ── Squad routing (target_role/target_squad_id) — server-side ──
        # Espelha o pipeline de prazos_iniciais._build_l1_task_payload.
        # Antes essa resolucao era feita SOMENTE no frontend (chamada
        # /squads/resolve-target/claim antes do submit). Era fragil: se
        # `target_role` nao chegasse no JSON do GET groups, ou se a
        # chamada /claim falhasse silenciosa, ou se o template fosse
        # marcado APOS a publicacao classificada (e rebuild-proposals nao
        # rodasse), a tarefa caia no lider sem aviso. Mover pro backend
        # garante que a fonte da verdade (template no banco) sempre
        # vence — independente do que o frontend mandou.
        #
        # Override manual: comparamos `participants[0].contact.id` do
        # payload atual com o `participants[0].contact.id` do proposal
        # original (do template). Se diferente, respeita — significa que
        # (a) operador trocou no modal OU (b) o frontend ja' resolveu
        # via /claim. Em ambos, queremos manter quem o frontend mandou.
        routing_notes: list[Optional[dict]] = [None] * len(payloads)
        self._apply_squad_routing_server_side(
            payloads=payloads, proposals=proposals, lawsuit_id=lawsuit_id,
            operator_locked=operator_locked, routing_notes=routing_notes,
        )

        # Defesa em profundidade: mesmo que o frontend não tenha feito o
        # check-duplicates (ou alguém tenha chamado o endpoint direto via
        # API), rechecamos aqui. Só bloqueia se force_duplicate=False e o
        # L1 realmente retornou tasks em aberto que colidem.
        if not force_duplicate:
            subtype_ids_to_check = [
                int(p.get("subTypeId")) for p in payloads
                if isinstance(p, dict) and p.get("subTypeId")
            ]
            if subtype_ids_to_check:
                dup_check = self.check_duplicates_for_lawsuit(
                    lawsuit_id, subtype_ids_to_check
                )
                if dup_check.get("total_duplicates", 0) > 0 and not dup_check.get("check_failed"):
                    raise ValueError(
                        "DUPLICATE_BLOCKED:"
                        + str(dup_check.get("total_duplicates", 0))
                        + ": Já existe(m) tarefa(s) pendente(s) no Legal One "
                          "com o mesmo subtipo para este processo. "
                          "Confirme no painel web ou reenvie com force_duplicate=true "
                          "para ignorar."
                    )

        # Fallback de office pro _apply_required_task_defaults. Tarefa avulsa
        # criada no modal não tem campo de escritório no form, então o
        # payload nasce sem responsibleOfficeId/originOfficeId. Usamos o
        # linked_office_id do(s) record(s) do grupo pra preencher.
        office_candidates = {
            r.linked_office_id for r in records if r.linked_office_id
        }
        fallback_office_id = (
            next(iter(office_candidates)) if len(office_candidates) == 1 else None
        )

        import copy as _copy

        created_task_ids: list[int] = []
        system_adjustments_per_payload: list[dict] = []
        for payload_idx, payload in enumerate(payloads):
            # Snapshot do payload como o operador confirmou — o diff contra o
            # payload pós-ajustes vira a trilha "ajuste automático do sistema".
            confirmed_by_operator = _copy.deepcopy(payload)
            self._enforce_description_limit(payload)
            self._apply_required_task_defaults(
                payload, fallback_office_id=fallback_office_id,
            )
            self._ensure_endtime_in_future(payload)
            adjustments_entry = self._diff_system_adjustments(confirmed_by_operator, payload)
            # Roteamento de squad é regra do TEMPLATE — nem ajuste mecânico,
            # nem override do operador. Entra na trilha azul com motivo
            # próprio (e tira o campo da conta de override humano no audit).
            if routing_notes[payload_idx]:
                adjustments_entry["responsavel_contact_id"] = routing_notes[payload_idx]
            system_adjustments_per_payload.append(adjustments_entry)
            created = self.client.create_task(payload)
            if not created or not created.get("id"):
                # Se o client conseguiu extrair o que o L1 reclamou, usa
                # direto a mensagem humana (ex: "Campos obrigatórios não
                # enviados: Data de publicação, Escritório de origem").
                # Senão, cai no genérico pra pelo menos dar feedback.
                l1_detail = self.client.format_last_create_task_error()
                raise ValueError(l1_detail or "Falha ao criar tarefa no Legal One.")
            task_id = created["id"]
            self.client.link_task_to_lawsuit(
                task_id,
                {"linkType": "Litigation", "linkId": lawsuit_id},
            )
            created_task_ids.append(task_id)

        from app.services.publication_treatment_service import PublicationTreatmentService
        treatment_service = PublicationTreatmentService(self.db)
        now_utc = datetime.now(timezone.utc)
        # Snapshot do usuário que agendou (pub002). Usamos getattr pra não
        # explodir caso venha algo que não tenha os atributos (testes, etc.).
        sb_user_id = getattr(scheduled_by, "id", None) if scheduled_by else None
        sb_email = getattr(scheduled_by, "email", None) if scheduled_by else None
        sb_name = getattr(scheduled_by, "name", None) if scheduled_by else None
        for r in records:
            r.status = RECORD_STATUS_SCHEDULED
            r.updated_at = now_utc
            r.scheduled_by_user_id = sb_user_id
            r.scheduled_by_email = sb_email
            r.scheduled_by_name = sb_name
            r.scheduled_at = now_utc
            treatment_service.sync_item_from_record(r, commit=False)
        self._record_scheduled_task_audit(
            lawsuit_id=lawsuit_id, records=records, proposals=proposals,
            sent_payloads=payloads, created_task_ids=created_task_ids,
            sb_user_id=sb_user_id, sb_email=sb_email, sb_name=sb_name, when=now_utc,
            system_adjustments=system_adjustments_per_payload,
        )
        self.db.commit()

        return {
            "created_task_id": created_task_ids[0],
            "created_task_ids": created_task_ids,
            "scheduled_publication_ids": [r.id for r in records],
            "lawsuit_id": lawsuit_id,
        }

    def schedule_records(
        self,
        record_ids: List[int],
        payload_override: Optional[dict] = None,
        payload_overrides: Optional[list[dict]] = None,
        scheduled_by: Optional[Any] = None,
        force_duplicate: bool = False,
    ) -> dict[str, Any]:
        """
        Agenda N tarefas para uma lista explícita de record IDs,
        SEM vincular a um processo. Aceita 1 ou N payloads por chamada.

        Nota sobre `force_duplicate`: aceito apenas por paridade de
        assinatura com schedule_group — nesse fluxo não há lawsuit_id
        pra indexar a busca no L1, então a checagem é pulada.
        """
        if not record_ids:
            raise ValueError("Nenhum registro informado.")

        records = (
            self.db.query(PublicationRecord)
            .filter(PublicationRecord.id.in_(record_ids))
            .filter(PublicationRecord.status.in_([RECORD_STATUS_NEW, RECORD_STATUS_CLASSIFIED, RECORD_STATUS_IGNORED]))
            .all()
        )
        if not records:
            raise ValueError("Nenhuma publicação pendente para os IDs informados.")

        first = records[0]
        proposals = []
        if isinstance(first.raw_relationships, dict):
            pt = first.raw_relationships.get("_proposed_tasks")
            if isinstance(pt, list):
                proposals = pt
            else:
                p = first.raw_relationships.get("_proposed_task")
                if p:
                    proposals = [p]

        if payload_overrides:
            payloads = list(payload_overrides)
        elif payload_override:
            payloads = [payload_override]
        else:
            payloads = [p.get("payload") for p in proposals if p and p.get("payload")]

        if not payloads:
            raise ValueError("Proposta de tarefa inexistente. Configure um template global (sem escritório).")

        # Marcador do modal (decisão final do operador) — fluxo avulso não
        # tem squad routing, mas o campo frontend-only não pode ir pro L1.
        for p in payloads:
            if isinstance(p, dict):
                p.pop("_responsible_overridden", None)

        # Fallback de office pra tarefas avulsas: embora esse fluxo seja
        # explicitamente "sem processo vinculado", alguns records ainda
        # podem ter linked_office_id (quando a publicação tem escritório
        # mas não o processo). Usa esse como fallback.
        office_candidates = {
            r.linked_office_id for r in records if r.linked_office_id
        }
        fallback_office_id = (
            next(iter(office_candidates)) if len(office_candidates) == 1 else None
        )

        import copy as _copy

        created_task_ids: list[int] = []
        system_adjustments_per_payload: list[dict] = []
        for payload in payloads:
            # Snapshot do payload como o operador confirmou — o diff contra o
            # payload pós-ajustes vira a trilha "ajuste automático do sistema".
            confirmed_by_operator = _copy.deepcopy(payload)
            self._enforce_description_limit(payload)
            self._apply_required_task_defaults(
                payload, fallback_office_id=fallback_office_id,
            )
            self._ensure_endtime_in_future(payload)
            system_adjustments_per_payload.append(
                self._diff_system_adjustments(confirmed_by_operator, payload)
            )
            created = self.client.create_task(payload)
            if not created or not created.get("id"):
                # Se o client conseguiu extrair o que o L1 reclamou, usa
                # direto a mensagem humana (ex: "Campos obrigatórios não
                # enviados: Data de publicação, Escritório de origem").
                # Senão, cai no genérico pra pelo menos dar feedback.
                l1_detail = self.client.format_last_create_task_error()
                raise ValueError(l1_detail or "Falha ao criar tarefa no Legal One.")
            created_task_ids.append(created["id"])

        from app.services.publication_treatment_service import PublicationTreatmentService
        treatment_service = PublicationTreatmentService(self.db)
        now_utc = datetime.now(timezone.utc)
        sb_user_id = getattr(scheduled_by, "id", None) if scheduled_by else None
        sb_email = getattr(scheduled_by, "email", None) if scheduled_by else None
        sb_name = getattr(scheduled_by, "name", None) if scheduled_by else None
        for r in records:
            r.status = RECORD_STATUS_SCHEDULED
            r.updated_at = now_utc
            r.scheduled_by_user_id = sb_user_id
            r.scheduled_by_email = sb_email
            r.scheduled_by_name = sb_name
            r.scheduled_at = now_utc
            treatment_service.sync_item_from_record(r, commit=False)
        self._record_scheduled_task_audit(
            lawsuit_id=None, records=records, proposals=proposals,
            sent_payloads=payloads, created_task_ids=created_task_ids,
            sb_user_id=sb_user_id, sb_email=sb_email, sb_name=sb_name, when=now_utc,
            system_adjustments=system_adjustments_per_payload,
        )
        self.db.commit()

        return {
            "created_task_id": created_task_ids[0],
            "created_task_ids": created_task_ids,
            "scheduled_publication_ids": [r.id for r in records],
            "lawsuit_id": None,
        }

    # ──────────────────────────────────────────────
    # Contagens para o dashboard
    # ──────────────────────────────────────────────

    def _status_counts(self) -> dict[str, int]:
        rows = (
            self.db.query(
                PublicationRecord.status,
                sa_func.count(PublicationRecord.id),
            )
            .filter(PublicationRecord.is_duplicate == False)
            .group_by(PublicationRecord.status)
            .all()
        )
        return {str(row[0]): int(row[1]) for row in rows}

    @staticmethod
    def _operational_snapshot(status_counts: dict[str, int]) -> dict[str, int]:
        without_providence = sum(
            status_counts.get(status, 0) for status in _WITHOUT_PROVIDENCE_STATUSES
        )
        return {
            "pendentes": status_counts.get(RECORD_STATUS_NEW, 0),
            "aguardando_confirmacao": status_counts.get(RECORD_STATUS_CLASSIFIED, 0),
            "agendadas": status_counts.get(RECORD_STATUS_SCHEDULED, 0),
            "sem_providencia": without_providence,
            "erros": status_counts.get(RECORD_STATUS_ERROR, 0),
        }

    @staticmethod
    def _localize_datetime(value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(_METRICS_TZ)

    def get_statistics(self) -> dict[str, Any]:
        base = self.db.query(PublicationRecord).filter(PublicationRecord.is_duplicate == False)

        total = base.count()
        by_status = self._status_counts()
        operational = self._operational_snapshot(by_status)

        total_searches = self.db.query(PublicationSearch).count()
        last_search = (
            self.db.query(PublicationSearch)
            .order_by(PublicationSearch.created_at.desc())
            .first()
        )

        # Naturezas distintas para filtro dinâmico
        naturezas = [
            row[0] for row in
            self.db.query(PublicationRecord.natureza_processo)
            .filter(PublicationRecord.is_duplicate == False)
            .filter(PublicationRecord.natureza_processo.isnot(None))
            .filter(PublicationRecord.natureza_processo != "")
            .distinct()
            .order_by(PublicationRecord.natureza_processo)
            .all()
        ]

        return {
            "total_records": total,
            "by_status": {
                "novo": by_status.get(RECORD_STATUS_NEW, 0),
                "classificado": by_status.get(RECORD_STATUS_CLASSIFIED, 0),
                "agendado": by_status.get(RECORD_STATUS_SCHEDULED, 0),
                "ignorado": by_status.get(RECORD_STATUS_IGNORED, 0),
                "erro": by_status.get(RECORD_STATUS_ERROR, 0),
                "descartado_duplicada": by_status.get(RECORD_STATUS_DISCARDED_DUPLICATE, 0),
                "descartado_obsoleta": by_status.get(RECORD_STATUS_OBSOLETE, 0),
                "sem_providencia": operational["sem_providencia"],
            },
            "operational": operational,
            "total_searches": total_searches,
            "last_search": self._search_to_dict(last_search) if last_search else None,
            "available_naturezas": naturezas,
        }

    def get_operational_insights(self, period: str = "week") -> dict[str, Any]:
        normalized_period = (period or "week").strip().lower()
        now_local = datetime.now(_METRICS_TZ)
        bucket_kind = "day"
        period_label = "Semana"

        if normalized_period == "day":
            window_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            bucket_kind = "hour"
            period_label = "Hoje"
        elif normalized_period == "week":
            window_start = (now_local - timedelta(days=6)).replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            period_label = "Semana"
        elif normalized_period == "month":
            window_start = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            period_label = "Mês"
        elif normalized_period == "all":
            window_start = None
            bucket_kind = "month"
            period_label = "Tudo"
        else:
            raise ValueError("period deve ser day, week, month ou all")

        status_counts = self._status_counts()
        current = self._operational_snapshot(status_counts)

        window_query = self.db.query(PublicationRecord).filter(PublicationRecord.is_duplicate == False)
        searches_query = self.db.query(PublicationSearch)
        if window_start is not None:
            window_query = window_query.filter(PublicationRecord.created_at >= window_start)
            searches_query = searches_query.filter(PublicationSearch.created_at >= window_start)

        window_rows = (
            window_query.with_entities(
                PublicationRecord.created_at,
                PublicationRecord.status,
            )
            .order_by(PublicationRecord.created_at.asc())
            .all()
        )

        summary_status_counts: dict[str, int] = defaultdict(int)
        for created_at, status in window_rows:
            summary_status_counts[str(status)] += 1

        summary = self._operational_snapshot(summary_status_counts)
        summary["recebidas"] = len(window_rows)
        summary["buscas"] = searches_query.count()

        if bucket_kind == "month":
            first_bucket = (
                self._localize_datetime(window_rows[0][0]).replace(
                    day=1,
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                if window_rows
                else now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            )
        else:
            first_bucket = window_start or now_local.replace(hour=0, minute=0, second=0, microsecond=0)

        bucket_map: dict[datetime, dict[str, int]] = {}

        def _next_bucket(current_bucket: datetime) -> datetime:
            if bucket_kind == "hour":
                return current_bucket + timedelta(hours=1)
            if bucket_kind == "day":
                return current_bucket + timedelta(days=1)
            month_anchor = current_bucket.replace(day=28) + timedelta(days=4)
            return month_anchor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        def _bucket_start(value: datetime) -> datetime:
            local_value = self._localize_datetime(value) or now_local
            if bucket_kind == "hour":
                return local_value.replace(minute=0, second=0, microsecond=0)
            if bucket_kind == "day":
                return local_value.replace(hour=0, minute=0, second=0, microsecond=0)
            return local_value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        bucket_cursor = first_bucket
        bucket_end = now_local.replace(minute=0, second=0, microsecond=0)
        while bucket_cursor <= bucket_end:
            bucket_map[bucket_cursor] = {
                "received": 0,
                "pending": 0,
                "awaiting_confirmation": 0,
                "scheduled": 0,
                "without_providence": 0,
                "errors": 0,
            }
            bucket_cursor = _next_bucket(bucket_cursor)

        for created_at, status in window_rows:
            if created_at is None:
                continue
            bucket = _bucket_start(created_at)
            counters = bucket_map.setdefault(
                bucket,
                {
                    "received": 0,
                    "pending": 0,
                    "awaiting_confirmation": 0,
                    "scheduled": 0,
                    "without_providence": 0,
                    "errors": 0,
                },
            )
            counters["received"] += 1
            if status == RECORD_STATUS_NEW:
                counters["pending"] += 1
            elif status == RECORD_STATUS_CLASSIFIED:
                counters["awaiting_confirmation"] += 1
            elif status == RECORD_STATUS_SCHEDULED:
                counters["scheduled"] += 1
            elif status in _WITHOUT_PROVIDENCE_STATUSES:
                counters["without_providence"] += 1
            elif status == RECORD_STATUS_ERROR:
                counters["errors"] += 1

        series = [
            {
                "bucket_start": bucket.isoformat(),
                "received": metrics["received"],
                "pending": metrics["pending"],
                "awaiting_confirmation": metrics["awaiting_confirmation"],
                "scheduled": metrics["scheduled"],
                "without_providence": metrics["without_providence"],
                "errors": metrics["errors"],
            }
            for bucket, metrics in sorted(bucket_map.items(), key=lambda item: item[0])
        ]

        return {
            "period": normalized_period,
            "period_label": period_label,
            "bucket_kind": bucket_kind,
            "generated_at": now_local.isoformat(),
            "window_start": window_start.isoformat() if window_start else None,
            "window_end": now_local.isoformat(),
            "current": {
                **current,
                "total_monitorado": sum(status_counts.values()),
            },
            "summary": summary,
            "series": series,
        }

    # ──────────────────────────────────────────────
    # Cancelamento
    # ──────────────────────────────────────────────

    def cancel_search(self, search_id: int) -> bool:
        search = self.db.query(PublicationSearch).filter_by(id=search_id).first()
        if not search or search.status not in (SEARCH_STATUS_RUNNING,):
            return False
        search.status = SEARCH_STATUS_CANCELLED
        search.finished_at = datetime.now(timezone.utc)
        self.db.commit()
        return True

    # ──────────────────────────────────────────────
    # Duplicatas com divergências de texto
    # ──────────────────────────────────────────────

    def list_duplicate_divergences(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """
        Encontra registros duplicados cujo texto difere do original.
        Retorna pares (original, duplicata) com preview das diferenças.
        """
        from sqlalchemy import func as sqlfunc

        # Encontra update_ids que aparecem mais de uma vez
        subq = (
            self.db.query(PublicationRecord.legal_one_update_id)
            .group_by(PublicationRecord.legal_one_update_id)
            .having(sqlfunc.count(PublicationRecord.id) > 1)
            .subquery()
        )

        # Busca todos os registros com esses update_ids
        records = (
            self.db.query(PublicationRecord)
            .filter(PublicationRecord.legal_one_update_id.in_(
                self.db.query(subq.c.legal_one_update_id)
            ))
            .order_by(
                PublicationRecord.legal_one_update_id,
                PublicationRecord.created_at,
            )
            .all()
        )

        # Agrupa por update_id
        by_update: dict = defaultdict(list)
        for r in records:
            by_update[r.legal_one_update_id].append(r)

        # Filtra apenas os que têm divergência de texto
        divergences = []
        for update_id, group in by_update.items():
            if len(group) < 2:
                continue
            original = group[0]
            original_text = (original.description or "").strip()
            for dup in group[1:]:
                dup_text = (dup.description or "").strip()
                if dup_text != original_text:
                    divergences.append({
                        "legal_one_update_id": update_id,
                        "original": {
                            "id": original.id,
                            "search_id": original.search_id,
                            "status": original.status,
                            "text_preview": original_text[:300],
                            "text_length": len(original_text),
                            "created_at": original.created_at.isoformat() if original.created_at else None,
                        },
                        "duplicate": {
                            "id": dup.id,
                            "search_id": dup.search_id,
                            "status": dup.status,
                            "text_preview": dup_text[:300],
                            "text_length": len(dup_text),
                            "is_duplicate": dup.is_duplicate,
                            "created_at": dup.created_at.isoformat() if dup.created_at else None,
                        },
                        "linked_lawsuit_cnj": original.linked_lawsuit_cnj or dup.linked_lawsuit_cnj,
                    })

        total = len(divergences)
        page = divergences[offset:offset + limit]
        return {"total": total, "offset": offset, "limit": limit, "divergences": page}

    # ──────────────────────────────────────────────
    # Serialização
    # ──────────────────────────────────────────────

    @staticmethod
    def _search_to_dict(search: PublicationSearch) -> dict[str, Any]:
        return {
            "id": search.id,
            "status": search.status,
            "date_from": search.date_from,
            "date_to": search.date_to,
            "origin_type": search.origin_type,
            "office_filter": search.office_filter,
            "total_found": search.total_found,
            "total_new": search.total_new,
            "total_duplicate": search.total_duplicate,
            "progress_step": search.progress_step,
            "progress_detail": search.progress_detail,
            "progress_pct": search.progress_pct,
            "requested_by_email": search.requested_by_email,
            "error_message": search.error_message,
            "created_at": search.created_at.isoformat() if search.created_at else None,
            "finished_at": search.finished_at.isoformat() if search.finished_at else None,
        }

    @staticmethod
    def _record_to_dict(record: PublicationRecord, include_full_text: bool = False) -> dict[str, Any]:
        proposal = None
        proposals = None
        if isinstance(record.raw_relationships, dict):
            proposal = record.raw_relationships.get("_proposed_task")
            proposals = record.raw_relationships.get("_proposed_tasks")

        result = {
            "id": record.id,
            "search_id": record.search_id,
            "legal_one_update_id": record.legal_one_update_id,
            "origin_type": record.origin_type,
            "update_type_id": record.update_type_id,
            "description_preview": (record.description or "")[:200],
            "publication_date": record.publication_date,
            "creation_date": record.creation_date,
            "linked_lawsuit_id": record.linked_lawsuit_id,
            "linked_lawsuit_cnj": record.linked_lawsuit_cnj,
            "linked_office_id": record.linked_office_id,
            "status": record.status,
            "category": record.category,
            "subcategory": record.subcategory,
            "polo": record.polo,
            "audiencia_data": record.audiencia_data,
            "audiencia_hora": record.audiencia_hora,
            "audiencia_link": record.audiencia_link,
            "classifications": record.classifications,
            "uf": record.uf,
            "natureza_processo": record.natureza_processo,
            "has_proposal": bool(proposal),
            "proposal": proposal if include_full_text else None,
            "proposals_count": len(proposals) if proposals else (1 if proposal else 0),
            # Autoria do agendamento (pub002). Só tem valor quando status=AGENDADO.
            "scheduled_by_user_id": record.scheduled_by_user_id,
            "scheduled_by_email": record.scheduled_by_email,
            "scheduled_by_name": record.scheduled_by_name,
            "scheduled_at": record.scheduled_at.isoformat() if record.scheduled_at else None,
            # Autoria da ciência (pub003). Só tem valor quando status=IGNORADO.
            "ignored_by_user_id": record.ignored_by_user_id,
            "ignored_by_email": record.ignored_by_email,
            "ignored_by_name": record.ignored_by_name,
            "ignored_at": record.ignored_at.isoformat() if record.ignored_at else None,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }
        if include_full_text:
            result["description"] = record.description
            result["notes"] = record.notes
            result["raw_relationships"] = record.raw_relationships
        return result
