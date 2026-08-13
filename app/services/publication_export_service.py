"""
Exportação XLSX das publicações listadas em "Processos com Publicações".

Mantém os mesmos filtros usados em ``list_records_grouped`` e gera um
arquivo organizado, com uma linha por publicação (layout plano) para
facilitar filtros dentro do Excel.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from app.models.legal_one import LegalOneOffice
from app.models.publication_search import RECORD_STATUS_OBSOLETE, PublicationRecord


# Ordem e cabeçalho das colunas exportadas. Espelha os campos visíveis
# na tela "Processos com Publicações", mais contexto útil da classificação.
EXPORT_COLUMNS: list[tuple[str, str, int]] = [
    # (chave interna, título, largura aproximada em caracteres)
    ("processo", "Processo (CNJ)", 24),
    ("uf", "UF", 6),
    ("escritorio", "Escritório responsável", 36),
    ("publication_date", "Data da Publicação", 20),
    ("creation_date", "Data no Ajus", 20),
    ("status", "Status", 14),
    ("scheduled_by", "Cadastrado por", 24),
    ("category", "Classificação", 24),
    ("subcategory", "Subclassificação", 24),
    ("polo", "Polo", 10),
    ("confianca", "Confiança", 12),
    ("audiencia_data", "Audiência (Data)", 16),
    ("audiencia_hora", "Audiência (Hora)", 12),
    ("justificativa", "Justificativa da IA", 60),
    ("descricao", "Texto da Publicação", 80),
    ("proposta", "Proposta de Tarefa (Template)", 32),
    ("legal_one_update_id", "ID L1", 12),
    ("link", "Link no Legal One", 48),
]


# DEPRECADO / NAO USADO desde a refatoracao de paridade: o export passou a
# reutilizar PublicationSearchService._base_publication_query (ver
# export_records_grouped_xlsx). Mantido so por referencia historica.
def _apply_filters(
    query, *, search_id, status, linked_office_id, date_from, date_to,
    category, uf=None, polo=None, scheduled_by_user_id=None,
):
    """Aplica os MESMOS filtros de ``PublicationSearchService._base_publication_query``.

    Todos os filtros multi-seleção (status, escritório, categoria, UF, polo)
    chegam como CSV ("AGENDADO,IGNORADO") e PRECISAM virar ``IN``. Comparar
    contra a string CSV inteira (``status == "AGENDADO,IGNORADO"``) não casa
    com nenhuma linha — era por isso que o export saía vazio enquanto a tela
    listava os registros normalmente.
    """
    # Helpers de parsing compartilhados com o service (fonte única de verdade).
    from app.services.publication_search_service import (
        _parse_csv_ints,
        _parse_csv_strs,
    )
    from sqlalchemy import or_

    query = query.filter(PublicationRecord.is_duplicate == False)  # noqa: E712
    if search_id is not None:
        query = query.filter(PublicationRecord.search_id == search_id)

    status_list = _parse_csv_strs(status)
    if status_list:
        if len(status_list) == 1:
            query = query.filter(PublicationRecord.status == status_list[0])
        else:
            query = query.filter(PublicationRecord.status.in_(status_list))
    else:
        query = query.filter(PublicationRecord.status != RECORD_STATUS_OBSOLETE)

    office_ids = _parse_csv_ints(linked_office_id)
    if office_ids:
        if len(office_ids) == 1:
            query = query.filter(PublicationRecord.linked_office_id == office_ids[0])
        else:
            query = query.filter(PublicationRecord.linked_office_id.in_(office_ids))

    if date_from:
        query = query.filter(PublicationRecord.creation_date >= date_from)
    if date_to:
        # Mantém o mesmo comportamento de list_records_grouped (inclui o dia inteiro).
        query = query.filter(PublicationRecord.creation_date < date_to + "T99")

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

    polo_list = [p.strip().lower() for p in _parse_csv_strs(polo)]
    if polo_list:
        if len(polo_list) == 1:
            query = query.filter(PublicationRecord.polo == polo_list[0])
        else:
            query = query.filter(PublicationRecord.polo.in_(polo_list))

    # "Cadastrado por": casa quem AGENDOU (scheduled_by_user_id) OU quem deu
    # ciência/IGNOROU (ignored_by_user_id). Antes só olhava scheduled_by_user_id,
    # então registros ignorados (que têm scheduled_by NULL) sumiam do filtro.
    by_ids = _parse_csv_ints(scheduled_by_user_id)
    if by_ids:
        query = query.filter(
            or_(
                PublicationRecord.scheduled_by_user_id.in_(by_ids),
                PublicationRecord.ignored_by_user_id.in_(by_ids),
            )
        )
    return query


def _primary_classification(record: PublicationRecord) -> dict[str, Any]:
    """Extrai a classificação primária (primeiro item do JSON ``classifications``)."""
    if isinstance(record.classifications, list) and record.classifications:
        first = record.classifications[0]
        if isinstance(first, dict):
            return first
    return {}


def _proposal_label(record: PublicationRecord) -> Optional[str]:
    """Retorna o nome do template da proposta de tarefa, se houver."""
    raw = getattr(record, "raw_relationships", None)
    if not isinstance(raw, dict):
        return None
    proposal = raw.get("_proposed_task")
    if isinstance(proposal, dict):
        template = proposal.get("template_name") or (
            proposal.get("payload", {}).get("template_name")
            if isinstance(proposal.get("payload"), dict) else None
        )
        if template:
            return str(template)
    proposals = raw.get("_proposed_tasks")
    if isinstance(proposals, list):
        names = []
        for rp in proposals:
            if isinstance(rp, dict):
                name = rp.get("template_name") or (
                    rp.get("payload", {}).get("template_name")
                    if isinstance(rp.get("payload"), dict) else None
                )
                if name:
                    names.append(str(name))
        if names:
            return " | ".join(dict.fromkeys(names))  # dedup mantendo ordem
    return None


def _truncate(value: Any, limit: int = 32_000) -> Any:
    """Excel tem limite de ~32767 caracteres por célula; corta com sufixo."""
    if isinstance(value, str) and len(value) > limit:
        return value[: limit - 20] + " …[truncado]"
    return value


def _xlsx_safe(value: Any) -> Any:
    """Evita célula ``<c t="inlineStr"/>`` sem ``<is>`` (XML inválido).

    O openpyxl 3.1.x serializa string vazia ("") como uma célula inline-string
    SEM o elemento ``<is>`` filho — OOXML inválido, que faz o Excel acusar
    "encontramos um problema com algum conteúdo… deseja recuperar?" (o arquivo
    "corrompido"). Trocar "" por ``None`` faz a célula ser omitida, que é
    válido. Qualquer outro valor passa intacto.
    """
    if value == "":
        return None
    return value


def _row_for_record(
    record: PublicationRecord,
    office_names: dict[int, str],
) -> dict[str, Any]:
    cls = _primary_classification(record)
    return {
        "processo": record.linked_lawsuit_cnj or "",
        "uf": record.uf or "",
        "escritorio": (
            office_names.get(record.linked_office_id, "")
            if record.linked_office_id is not None
            else ""
        ),
        "publication_date": record.publication_date or "",
        "creation_date": record.creation_date or "",
        "status": record.status or "",
        "scheduled_by": (
            record.scheduled_by_name
            or record.scheduled_by_email
            or record.ignored_by_name
            or record.ignored_by_email
            or ""
        ),
        "category": record.category or "",
        "subcategory": record.subcategory or "",
        "polo": record.polo or "",
        "confianca": cls.get("confianca") if cls else None,
        "audiencia_data": record.audiencia_data or "",
        "audiencia_hora": record.audiencia_hora or "",
        "justificativa": _truncate(cls.get("justificativa") or ""),
        "descricao": _truncate(record.description or ""),
        "proposta": _proposal_label(record) or "",
        "legal_one_update_id": record.legal_one_update_id,
        "link": (
            f"https://firm.legalone.com.br/publications?publicationId={record.legal_one_update_id}&treatStatus=3"
            if record.legal_one_update_id
            else ""
        ),
    }


def export_records_grouped_xlsx(
    db: Session,
    *,
    search_id: Optional[int] = None,
    status: Optional[str] = None,
    linked_office_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    category: Optional[str] = None,
    uf: Optional[str] = None,
    vinculo: Optional[str] = None,
    natureza: Optional[str] = None,
    polo: Optional[str] = None,
    cnj_search: Optional[str] = None,
    scheduled_by_user_id: Optional[str] = None,
    etiqueta: Optional[str] = None,
) -> tuple[bytes, str]:
    """Gera o XLSX e devolve ``(bytes, filename)``."""

    # Reutiliza a MESMA query-base do list (PublicationSearchService) para
    # garantir paridade TOTAL de filtros entre a tela e o export, incluindo
    # vinculo/natureza/cnj_search (que o _apply_filters local nao cobria). O
    # construtor do service so guarda db/client; passamos client=None porque
    # _base_publication_query e' SQL puro e nao toca no client.
    from app.services.publication_search_service import PublicationSearchService
    query = PublicationSearchService(db, None)._base_publication_query(
        search_id=search_id,
        status=status,
        linked_office_id=linked_office_id,
        date_from=date_from,
        date_to=date_to,
        category=category,
        uf=uf,
        vinculo=vinculo,
        natureza=natureza,
        polo=polo,
        cnj_search=cnj_search,
        scheduled_by_user_id=scheduled_by_user_id,
        etiqueta=etiqueta,
    )

    records = (
        query
        .order_by(
            PublicationRecord.linked_lawsuit_cnj.asc().nullslast(),
            PublicationRecord.publication_date.desc(),
        )
        .all()
    )

    # Pré-carrega nomes de escritórios para mapear id → nome sem N+1.
    # Preferimos `path` (hierarquia completa, ex.: "MDR / Filial BA / Cível")
    # em vez de `name` (folha) — é o padrão usado em outras telas e dá
    # contexto ao operador sobre onde o escritório se encaixa na estrutura.
    office_ids = {r.linked_office_id for r in records if r.linked_office_id is not None}
    office_names: dict[int, str] = {}
    if office_ids:
        offices = (
            db.query(LegalOneOffice)
            .filter(LegalOneOffice.external_id.in_(office_ids))
            .all()
        )
        office_names = {o.external_id: (o.path or o.name) for o in offices}

    wb = Workbook()
    ws = wb.active
    ws.title = "Publicações"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_align = Alignment(horizontal="left", vertical="center", wrap_text=True)
    body_align = Alignment(vertical="top", wrap_text=True)

    # Cabeçalho
    for idx, (_key, title, width) in enumerate(EXPORT_COLUMNS, start=1):
        cell = ws.cell(row=1, column=idx, value=title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        ws.column_dimensions[get_column_letter(idx)].width = width

    ws.row_dimensions[1].height = 28
    ws.freeze_panes = "A2"

    # Linhas
    for row_idx, record in enumerate(records, start=2):
        row = _row_for_record(record, office_names)
        for col_idx, (key, _title, _width) in enumerate(EXPORT_COLUMNS, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=_xlsx_safe(row.get(key)))
            cell.alignment = body_align
        ws.row_dimensions[row_idx].height = 25

    # Autofiltro sobre todo o range usado
    if records:
        last_col = get_column_letter(len(EXPORT_COLUMNS))
        ws.auto_filter.ref = f"A1:{last_col}{len(records) + 1}"

    # Oculta linhas além do range preenchido (evita "linhas infinitas em branco")
    ws.sheet_format.zeroHeight = True

    # Aba de metadados — deixa rastreável qual filtro gerou o arquivo.
    meta = wb.create_sheet("Filtros")
    meta_rows = [
        ("Gerado em (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
        ("Total de publicações exportadas", len(records)),
        ("search_id", search_id if search_id is not None else ""),
        ("status", status or ""),
        ("linked_office_id", linked_office_id if linked_office_id is not None else ""),
        ("date_from", date_from or ""),
        ("date_to", date_to or ""),
        ("category", category or ""),
        ("uf", uf or ""),
        ("vinculo", vinculo or ""),
        ("natureza", natureza or ""),
        ("polo", polo or ""),
        ("cnj_search", cnj_search or ""),
        ("scheduled_by_user_id", scheduled_by_user_id or ""),
        ("etiqueta", etiqueta or ""),
    ]
    for row_idx, (label, value) in enumerate(meta_rows, start=1):
        meta.cell(row=row_idx, column=1, value=label).font = Font(bold=True)
        meta.cell(row=row_idx, column=2, value=_xlsx_safe(value))
    meta.column_dimensions["A"].width = 36
    meta.column_dimensions["B"].width = 48

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"publicacoes-{stamp}.xlsx"
    return buffer.read(), filename
