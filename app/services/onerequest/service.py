"""Serviço operador do OneRequest (Fase 2).

Listagem com farol/KPIs + filtros (paginada), edição do tratamento e
agendamento da tarefa no Legal One. O agendamento REUSA os helpers da
`OnerequestStrategy` (mesma lógica do batch externo) e materializa a cascata
de resolução do processo (CNJ → [NPJ, pós-probe] → AGUARDANDO_PROCESSO).
Ver docs/onerequest-integracao-plano.md §6 e §7.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.models.legal_one import LegalOneUser
from app.models.onerequest import (
    OnerequestAnotacao,
    OnerequestSolicitacao,
    STATUS_SISTEMA_ABERTO,
    STATUS_SISTEMA_RESPONDIDO,
    STATUS_TRATAMENTO_AGENDADO,
    STATUS_TRATAMENTO_AGUARDANDO_PROCESSO,
    STATUS_TRATAMENTO_ERRO,
    STATUS_TRATAMENTO_IGNORADO,
)
from app.services.batch_strategies.onerequest_strategy import (
    DEFAULT_TASK_STATUS_ID,
    SECTOR_TASK_MAPPING,
    OnerequestStrategy,
)
from app.services.legal_one_client import LegalOneApiClient

logger = logging.getLogger(__name__)

# Indica que o numero_processo capturado pela RPA não dá pra usar no L1.
_DIRTY_PROC_HINTS = ("não", "nao", "erro", "ausente", "encontrado", "informado", "api", "n/a")


def _proc_utilizavel(numero_processo: Optional[str]) -> bool:
    if not numero_processo:
        return False
    s = str(numero_processo).strip()
    if not s:
        return False
    low = s.lower()
    return not any(h in low for h in _DIRTY_PROC_HINTS)


def _parse_prazo(prazo: Optional[str]) -> Optional[date]:
    if not prazo:
        return None
    try:
        return datetime.strptime(prazo.strip(), "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return None


# Horário de Brasília (sem DST desde 2019) — recorte de "disponibilização".
_BRT = timezone(timedelta(hours=-3))


def _brt_date(dt: Optional[datetime]) -> Optional[date]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_BRT).date()


def _fmt_iso_brt(iso: Optional[str]) -> Optional[str]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_BRT).strftime("%d/%m/%Y %H:%M")


def _farol(prazo_date: Optional[date], hoje: date) -> str:
    """Farol do prazo BB. DISTINGUE atrasado (vencido) de sem prazo — antes os
    dois caíam em 'cinza', escondendo as vencidas. Vence hoje é 'vermelho'."""
    if prazo_date is None:
        return "cinza"  # sem prazo informado
    if prazo_date < hoje:
        return "atrasado"  # vencida (o prazo já passou)
    if prazo_date == hoje:
        return "vermelho"  # vence hoje
    if (prazo_date - hoje).days == 1:
        return "amarelo"
    if prazo_date.weekday() >= 5:  # sáb/dom
        return "roxo"
    return "verde"


# ── Legal One web (deep-links) ─────────────────────────────────────────
L1_WEB_BASE_URL = "https://mdradvocacia.novajus.com.br"
L1_BLOCKING_STATUS_IDS = {0, 4}  # Pendente, Iniciado = "em aberto"
L1_STATUS_LABELS_FULL = {
    0: "Pendente",
    1: "Cumprido",
    2: "Não cumprido",
    3: "Cancelado",
    4: "Iniciado",
    5: "Reagendado",
}


def _l1_lawsuit_url(lawsuit_id: int) -> str:
    return f"{L1_WEB_BASE_URL}/processos/processos/DetailsCompromissosTarefas/{int(lawsuit_id)}"


def _l1_task_url(task_id, lawsuit_id) -> str:
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


class OnerequestService:
    def __init__(self, db: Session):
        self.db = db

    # ──────────────────────────────────────────────────────────────────
    # Listagem (paginada) com farol + KPIs
    # ──────────────────────────────────────────────────────────────────
    def list_solicitacoes(
        self,
        *,
        status_sistema: Optional[str] = STATUS_SISTEMA_ABERTO,
        status_tratamento: Optional[str] = None,
        responsavel_user_id: Optional[int] = None,
        setor: Optional[str] = None,
        busca: Optional[str] = None,
        farol: Optional[str] = None,
        sem_responsavel: Optional[bool] = None,
        sem_anotacao: Optional[bool] = None,
        sem_processo_l1: Optional[bool] = None,
        concluidas: Optional[bool] = None,
        disp_de: Optional[date] = None,
        disp_ate: Optional[date] = None,
        prazo_de: Optional[date] = None,
        prazo_ate: Optional[date] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        q = self.db.query(OnerequestSolicitacao)
        if concluidas:
            # "Concluídas" = BB respondeu (RESPONDIDO) OU operador encerrou sem
            # providência (IGNORADO). Ignora o filtro de status_sistema.
            q = q.filter(
                or_(
                    OnerequestSolicitacao.status_sistema == STATUS_SISTEMA_RESPONDIDO,
                    OnerequestSolicitacao.status_tratamento == STATUS_TRATAMENTO_IGNORADO,
                )
            )
        elif status_sistema:
            q = q.filter(OnerequestSolicitacao.status_sistema == status_sistema)
        if status_tratamento:
            q = q.filter(OnerequestSolicitacao.status_tratamento == status_tratamento)
        if responsavel_user_id:
            q = q.filter(OnerequestSolicitacao.responsavel_user_id == responsavel_user_id)
        if setor:
            # "(sem setor)" vem do dashboard pra drill-through das DMIs sem setor.
            if setor == "(sem setor)":
                q = q.filter(OnerequestSolicitacao.setor.is_(None))
            else:
                q = q.filter(OnerequestSolicitacao.setor == setor)
        if sem_processo_l1:
            # "Fora do L1": checadas e NÃO encontradas (exclui as não-checadas).
            q = q.filter(OnerequestSolicitacao.proc_l1_encontrado.is_(False))
        if busca:
            termo = f"%{busca.strip()}%"
            q = q.filter(
                or_(
                    OnerequestSolicitacao.numero_solicitacao.ilike(termo),
                    OnerequestSolicitacao.numero_processo.ilike(termo),
                    OnerequestSolicitacao.titulo.ilike(termo),
                )
            )
        if sem_responsavel:
            # "Novas" = ainda SEM RESPONSÁVEL (não distribuídas) OU travadas
            # (AGUARDANDO_PROCESSO / ERRO) — pra uma DMI que o operador tentou
            # agendar mas falhou NÃO sumir do radar. Exclui só as ignoradas.
            q = q.filter(
                or_(
                    OnerequestSolicitacao.responsavel_user_id.is_(None),
                    OnerequestSolicitacao.status_tratamento.in_(
                        (STATUS_TRATAMENTO_AGUARDANDO_PROCESSO, STATUS_TRATAMENTO_ERRO)
                    ),
                ),
                OnerequestSolicitacao.status_tratamento != STATUS_TRATAMENTO_IGNORADO,
            )

        hoje = date.today()

        # Passo 1: (id, prazo, recebido_em) de TODO o conjunto filtrado pra
        # farol/KPIs/ordenação + recortes de data (disponibilização e prazo fatal).
        leves = q.with_entities(
            OnerequestSolicitacao.id,
            OnerequestSolicitacao.prazo,
            OnerequestSolicitacao.recebido_em,
        ).all()
        kpis = {"atrasadas": 0, "hoje": 0, "amanha": 0, "fds": 0, "futuras": 0, "sem_prazo": 0}
        enriquecidos = []
        for _id, prazo, recebido_em in leves:
            pdate = _parse_prazo(prazo)
            # Recorte por PRAZO FATAL (campo prazo, do BB).
            if prazo_de and (pdate is None or pdate < prazo_de):
                continue
            if prazo_ate and (pdate is None or pdate > prazo_ate):
                continue
            # Recorte por DISPONIBILIZAÇÃO (recebido_em, em horário de Brasília).
            if disp_de or disp_ate:
                rdate = _brt_date(recebido_em)
                if disp_de and (rdate is None or rdate < disp_de):
                    continue
                if disp_ate and (rdate is None or rdate > disp_ate):
                    continue
            farol_row = _farol(pdate, hoje)
            if farol_row == "atrasado":
                kpis["atrasadas"] += 1
            elif farol_row == "vermelho":
                kpis["hoje"] += 1
            elif farol_row == "amarelo":
                kpis["amanha"] += 1
            elif farol_row == "roxo":
                kpis["fds"] += 1
            elif farol_row == "verde":
                kpis["futuras"] += 1
            else:  # cinza = sem prazo informado
                kpis["sem_prazo"] += 1
            # ordena por prazo asc; sem data vai pro fim
            enriquecidos.append((_id, pdate or date.max, farol_row))

        if farol:
            enriquecidos = [e for e in enriquecidos if e[2] == farol]

        # DMIs que já têm anotação (justificativa do atraso, ex.: aguardando
        # providência do cliente). Set único pra badge "anotada"/"sem anotação"
        # e pro filtro `sem_anotacao` (caçar atrasadas que ainda precisam de ação).
        ids_com_anotacao = {
            sid
            for (sid,) in self.db.query(OnerequestAnotacao.solicitacao_id).distinct()
        }
        if sem_anotacao:
            enriquecidos = [e for e in enriquecidos if e[0] not in ids_com_anotacao]

        total = len(enriquecidos)
        enriquecidos.sort(key=lambda x: x[1])
        page_ids = [i for (i, _d, _f) in enriquecidos[offset : offset + limit]]

        # Passo 2: linhas completas só da página, preservando a ordem.
        page_map = {
            row.id: row
            for row in self.db.query(OnerequestSolicitacao)
            .filter(OnerequestSolicitacao.id.in_(page_ids))
            .all()
        }
        page_rows = [page_map[i] for i in page_ids if i in page_map]

        # Resolve nome do responsável em lote.
        user_ids = {r.responsavel_user_id for r in page_rows if r.responsavel_user_id}
        nomes = {}
        if user_ids:
            nomes = {
                u.id: u.name
                for u in self.db.query(LegalOneUser.id, LegalOneUser.name).filter(
                    LegalOneUser.id.in_(user_ids)
                )
            }

        items = []
        for r in page_rows:
            items.append(
                {
                    "id": r.id,
                    "numero_solicitacao": r.numero_solicitacao,
                    "titulo": r.titulo,
                    "npj_direcionador": r.npj_direcionador,
                    "prazo": r.prazo,
                    "texto_dmi": r.texto_dmi,
                    "numero_processo": r.numero_processo,
                    "proc_utilizavel": _proc_utilizavel(r.numero_processo),
                    "polo": r.polo,
                    "recebido_em": r.recebido_em.isoformat() if r.recebido_em else None,
                    "status_sistema": r.status_sistema,
                    "status_tratamento": r.status_tratamento,
                    "desfecho": (
                        "respondida"
                        if r.status_sistema == STATUS_SISTEMA_RESPONDIDO
                        else ("arquivada" if r.status_tratamento == STATUS_TRATAMENTO_IGNORADO else None)
                    ),
                    "responsavel_user_id": r.responsavel_user_id,
                    "responsavel_nome": nomes.get(r.responsavel_user_id),
                    "setor": r.setor,
                    "data_agendamento": r.data_agendamento,
                    "anotacao": r.anotacao,
                    "tem_anotacao": r.id in ids_com_anotacao,
                    "created_task_id": r.created_task_id,
                    "linked_lawsuit_id": r.linked_lawsuit_id,
                    "last_error": r.last_error,
                    "scheduled_by_nome": r.scheduled_by_nome,
                    "scheduled_at": r.scheduled_at.isoformat() if r.scheduled_at else None,
                    "farol": _farol(_parse_prazo(r.prazo), hoje),
                    # Status no L1 (cacheado pelo botão "Atualizar status L1").
                    "l1_checked_at": r.l1_checked_at.isoformat() if r.l1_checked_at else None,
                    "l1_dmi_task_id": r.l1_dmi_task_id,
                    "l1_dmi_status_id": r.l1_dmi_status_id,
                    "l1_dmi_status_label": (
                        L1_STATUS_LABELS_FULL.get(r.l1_dmi_status_id)
                        if r.l1_dmi_status_id is not None
                        else None
                    ),
                    "l1_dmi_respondida": r.l1_dmi_status_id == 1,
                    "l1_dmi_encontrada": r.l1_dmi_task_id is not None,
                    "l1_pendentes_count": r.l1_pendentes_count,
                    "l1_sem_pendencia": (
                        (r.l1_pendentes_count == 0)
                        if r.l1_pendentes_count is not None
                        else None
                    ),
                    "l1_task_url": (
                        _l1_task_url(r.l1_dmi_task_id, r.linked_lawsuit_id)
                        if (r.l1_dmi_task_id and r.linked_lawsuit_id)
                        else None
                    ),
                    # Verificação proativa de existência do processo no L1.
                    "proc_l1_checado_em": r.proc_l1_checado_em.isoformat() if r.proc_l1_checado_em else None,
                    "proc_l1_encontrado": r.proc_l1_encontrado,
                    "proc_l1_via": r.proc_l1_via,
                }
            )

        return {"total": total, "kpis": kpis, "items": items}

    # ──────────────────────────────────────────────────────────────────
    # Exportação Excel (reusa exatamente os filtros do list)
    # ──────────────────────────────────────────────────────────────────
    def export_xlsx(self, **filtros) -> bytes:
        from io import BytesIO

        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter

        filtros.pop("limit", None)
        filtros.pop("offset", None)
        items = self.list_solicitacoes(limit=100000, offset=0, **filtros)["items"]

        desfecho_lbl = {"respondida": "Respondida", "arquivada": "Arquivada"}
        farol_lbl = {
            "atrasado": "Atrasada", "vermelho": "Vence hoje", "amarelo": "Amanhã",
            "roxo": "Fim de semana", "verde": "Futura", "cinza": "Sem prazo",
        }
        # (título, largura, getter)
        colunas = [
            ("DMI", 16, lambda i: i["numero_solicitacao"]),
            ("Título", 42, lambda i: i.get("titulo")),
            ("NPJ direcionador", 16, lambda i: i.get("npj_direcionador")),
            ("Processo", 24, lambda i: i.get("numero_processo")),
            ("Polo", 10, lambda i: i.get("polo")),
            ("Disponibilizada", 18, lambda i: _fmt_iso_brt(i.get("recebido_em"))),
            ("Prazo fatal", 12, lambda i: i.get("prazo")),
            ("Situação do prazo", 16, lambda i: farol_lbl.get(i.get("farol"), "")),
            ("Status BB", 12, lambda i: i.get("status_sistema")),
            ("Tratamento", 18, lambda i: i.get("status_tratamento")),
            ("Desfecho", 12, lambda i: desfecho_lbl.get(i.get("desfecho") or "", "")),
            ("Responsável", 24, lambda i: i.get("responsavel_nome")),
            ("Setor", 18, lambda i: i.get("setor")),
            ("Agendada para", 14, lambda i: i.get("data_agendamento")),
            ("Status no L1", 14, lambda i: i.get("l1_dmi_status_label")),
            ("Tarefa L1", 12, lambda i: i.get("created_task_id")),
            ("Anotação", 50, lambda i: i.get("anotacao")),
        ]

        wb = Workbook()
        ws = wb.active
        ws.title = "DMIs"
        head_font = Font(bold=True, color="FFFFFF")
        head_fill = PatternFill("solid", fgColor="1F4E79")
        for col, (titulo, largura, _g) in enumerate(colunas, start=1):
            cell = ws.cell(row=1, column=col, value=titulo)
            cell.font = head_font
            cell.fill = head_fill
            ws.column_dimensions[get_column_letter(col)].width = largura
        for row, item in enumerate(items, start=2):
            for col, (_t, _w, getter) in enumerate(colunas, start=1):
                ws.cell(row=row, column=col, value=getter(item))
        ws.freeze_panes = "A2"

        buf = BytesIO()
        wb.save(buf)
        return buf.getvalue()

    # ──────────────────────────────────────────────────────────────────
    # Dashboard — visão operacional + risco (KPIs, séries, distribuições)
    # ──────────────────────────────────────────────────────────────────
    def dashboard_data(self, days: int = 30) -> dict:
        hoje = date.today()
        start_date = hoje - timedelta(days=days - 1)

        # Base = DMIs abertas (em tratamento) — alimenta farol/responsável/setor.
        abertas = self.db.query(
            OnerequestSolicitacao.prazo,
            OnerequestSolicitacao.responsavel_user_id,
            OnerequestSolicitacao.setor,
            OnerequestSolicitacao.status_tratamento,
        ).filter(OnerequestSolicitacao.status_sistema == STATUS_SISTEMA_ABERTO).all()

        farol_dist = {"atrasado": 0, "vermelho": 0, "amarelo": 0, "roxo": 0, "verde": 0, "cinza": 0}
        resp_acc: dict = {}
        setor_acc: dict = {}
        sem_responsavel = 0
        agendadas_abertas = 0
        for prazo, ruid, setor, st in abertas:
            f = _farol(_parse_prazo(prazo), hoje)
            farol_dist[f] = farol_dist.get(f, 0) + 1
            if st == STATUS_TRATAMENTO_AGENDADO:
                agendadas_abertas += 1
            if ruid is None and st != STATUS_TRATAMENTO_IGNORADO:
                sem_responsavel += 1
            if ruid is not None:
                acc = resp_acc.setdefault(ruid, {"abertas": 0, "atrasadas": 0, "agendadas": 0})
                acc["abertas"] += 1
                if f == "atrasado":
                    acc["atrasadas"] += 1
                if st == STATUS_TRATAMENTO_AGENDADO:
                    acc["agendadas"] += 1
            setor_acc[setor or "(sem setor)"] = setor_acc.get(setor or "(sem setor)", 0) + 1

        nomes = {}
        if resp_acc:
            nomes = {
                u.id: u.name
                for u in self.db.query(LegalOneUser.id, LegalOneUser.name).filter(
                    LegalOneUser.id.in_(list(resp_acc.keys()))
                )
            }
        por_responsavel = sorted(
            [{"id": uid, "nome": nomes.get(uid) or f"#{uid}", **v} for uid, v in resp_acc.items()],
            key=lambda x: x["abertas"],
            reverse=True,
        )
        por_setor = sorted(
            [{"setor": s, "n": n} for s, n in setor_acc.items()],
            key=lambda x: x["n"],
            reverse=True,
        )

        concluidas_total = (
            self.db.query(OnerequestSolicitacao.id)
            .filter(
                or_(
                    OnerequestSolicitacao.status_sistema == STATUS_SISTEMA_RESPONDIDO,
                    OnerequestSolicitacao.status_tratamento == STATUS_TRATAMENTO_IGNORADO,
                )
            )
            .count()
        )

        # Séries diárias (BRT), densificadas (dias sem dado = 0).
        rec_rows = self.db.execute(
            text(
                "SELECT (recebido_em AT TIME ZONE 'America/Sao_Paulo')::date AS dia, count(*) AS n "
                "FROM onr_solicitacoes WHERE recebido_em IS NOT NULL "
                "AND (recebido_em AT TIME ZONE 'America/Sao_Paulo')::date >= :sd GROUP BY 1"
            ),
            {"sd": start_date},
        ).all()
        age_rows = self.db.execute(
            text(
                "SELECT (scheduled_at AT TIME ZONE 'America/Sao_Paulo')::date AS dia, count(*) AS n "
                "FROM onr_solicitacoes WHERE scheduled_at IS NOT NULL "
                "AND (scheduled_at AT TIME ZONE 'America/Sao_Paulo')::date >= :sd GROUP BY 1"
            ),
            {"sd": start_date},
        ).all()

        def _serie(rows) -> list:
            m = {r[0].isoformat(): int(r[1]) for r in rows}
            out, d = [], start_date
            while d <= hoje:
                out.append({"dia": d.isoformat(), "n": m.get(d.isoformat(), 0)})
                d += timedelta(days=1)
            return out

        recebimentos = _serie(rec_rows)
        agendamentos = _serie(age_rows)

        return {
            "kpis": {
                "abertas": len(abertas),
                "atrasadas": farol_dist["atrasado"],
                "hoje": farol_dist["vermelho"],
                "sem_responsavel": sem_responsavel,
                "agendadas": agendadas_abertas,
                "concluidas": concluidas_total,
            },
            "farol": farol_dist,
            "recebimentos": recebimentos,
            "agendamentos": agendamentos,
            "por_responsavel": por_responsavel,
            "por_setor": por_setor,
            "periodo_dias": days,
        }

    def get(self, solicitacao_id: int) -> Optional[OnerequestSolicitacao]:
        return (
            self.db.query(OnerequestSolicitacao)
            .filter(OnerequestSolicitacao.id == solicitacao_id)
            .first()
        )

    # ──────────────────────────────────────────────────────────────────
    # Edição do tratamento
    # ──────────────────────────────────────────────────────────────────
    def update_tratamento(self, solicitacao: OnerequestSolicitacao, dados: dict) -> OnerequestSolicitacao:
        for campo in ("responsavel_user_id", "setor", "data_agendamento", "anotacao", "status_tratamento"):
            if campo in dados and dados[campo] is not None:
                setattr(solicitacao, campo, dados[campo])
        self.db.commit()
        self.db.refresh(solicitacao)
        return solicitacao

    # ──────────────────────────────────────────────────────────────────
    # Trava-duplo: tarefa da DMI já PENDENTE na pasta? (evita 2 tarefas em
    # aberto pra mesma DMI durante a transição). Cumprida NÃO bloqueia
    # (reiteração é legítima). Fail-open: se a checagem cair, não trava.
    # ──────────────────────────────────────────────────────────────────
    def _tarefa_pendente_da_dmi(
        self, solicitacao: OnerequestSolicitacao, lawsuit_id: int, client: LegalOneApiClient
    ) -> Optional[dict]:
        numero = (solicitacao.numero_solicitacao or "").strip()
        if not numero:
            return None
        rel = (
            "relationships/any("
            f"r: r/linkType eq 'Litigation' and r/linkId eq {int(lawsuit_id)})"
        )
        esc = numero.replace("'", "''")
        try:
            matched = client.search_tasks(
                filter_expression=f"{rel} and contains(description,'{esc}')",
                top=30,
                orderby="id desc",
                select="id,description,statusId,status,endDateTime",
            )
        except Exception as e:
            logger.warning(
                "OneRequest trava-duplo: checagem da DMI %s falhou (%s) — não bloqueia.",
                numero, e,
            )
            return None
        for t in matched:
            sid = self._task_status_id(t)
            if sid in L1_BLOCKING_STATUS_IDS:  # 0 Pendente, 4 Iniciado
                return {
                    "task_id": t.get("id"),
                    "status_id": sid,
                    "status_label": L1_STATUS_LABELS_FULL.get(sid, str(sid)),
                    "description": t.get("description"),
                    "l1_url": _l1_task_url(t.get("id"), lawsuit_id),
                }
        return None

    # ──────────────────────────────────────────────────────────────────
    # Agendamento no Legal One (reusa helpers da OnerequestStrategy)
    # ──────────────────────────────────────────────────────────────────
    def agendar(
        self,
        solicitacao: OnerequestSolicitacao,
        client: LegalOneApiClient,
        current_user: LegalOneUser,
        confirmar: bool = False,
    ) -> dict:
        """
        Cria a tarefa no L1 pra uma solicitação tratada. Retorna
        {ok, status_tratamento, created_task_id?, mensagem}.

        Cascata de resolução do processo (§7) — NÃO exige CNJ:
          1. CNJ utilizável -> search_lawsuit_by_cnj.
          2. NPJ -> contains(notes/title) da pasta no L1 (a casa grava o NPJ
             nas notas/título do cadastro da pasta).
          3. Nada resolve -> AGUARDANDO_PROCESSO (não é erro).
        """
        # Validação dos campos de tratamento (mensagens claras pro operador).
        faltando = []
        if not solicitacao.responsavel_user_id:
            faltando.append("responsável")
        if not solicitacao.setor:
            faltando.append("setor")
        if not solicitacao.data_agendamento:
            faltando.append("data de agendamento")
        if faltando:
            return {
                "ok": False,
                "status_tratamento": solicitacao.status_tratamento,
                "mensagem": f"Preencha antes de agendar: {', '.join(faltando)}.",
            }

        responsavel = (
            self.db.query(LegalOneUser)
            .filter(LegalOneUser.id == solicitacao.responsavel_user_id)
            .first()
        )
        if not responsavel or not responsavel.external_id:
            return {
                "ok": False,
                "status_tratamento": solicitacao.status_tratamento,
                "mensagem": "Responsável sem external_id no Legal One.",
            }

        try:
            strat = OnerequestStrategy(self.db, client)
            type_id, subtype_id = strat._get_task_type_ids(solicitacao.setor)
            end_iso = strat._parse_and_format_deadline(solicitacao.data_agendamento)

            payload_para_textos = {
                "vencimento": solicitacao.prazo,
                "titulo": solicitacao.titulo,
                "npj_direcionador": solicitacao.npj_direcionador,
                "numero_solicitacao": solicitacao.numero_solicitacao,
                "anotacao": solicitacao.anotacao,
                "texto_dmi": solicitacao.texto_dmi,
            }
            description = strat._build_task_description(payload_para_textos)
            notes = strat._build_task_notes(payload_para_textos)

            # Resolução do processo: CNJ -> NPJ(notes/title). Não exige CNJ.
            lawsuit = self.resolver_lawsuit(solicitacao, client)
            avulsa = False
            if lawsuit and lawsuit.get("id"):
                lawsuit_id = lawsuit["id"]
                office_id = lawsuit.get("responsibleOfficeId")
                if not office_id:
                    raise Exception(f"Processo {lawsuit_id} sem responsibleOfficeId no L1.")
            else:
                # Sem pasta no L1 -> TAREFA AVULSA (sem vínculo com processo). O
                # escritório vem do polo/setor da DMI — igual à interface do L1,
                # onde você cria a tarefa, escolhe o "Escritório responsável" e
                # não vincula processo. O create_task já é independente da pasta;
                # o vínculo (link_task_to_lawsuit) é um passo à parte que pulamos.
                avulsa = True
                lawsuit_id = None
                office_id = self._office_avulso_id(solicitacao)
                if not office_id:
                    solicitacao.status_tratamento = STATUS_TRATAMENTO_AGUARDANDO_PROCESSO
                    solicitacao.last_error = (
                        "Sem pasta no L1 e não foi possível derivar o escritório "
                        "(polo/setor) para criar a tarefa avulsa."
                    )
                    self.db.commit()
                    return {
                        "ok": False,
                        "status_tratamento": solicitacao.status_tratamento,
                        "mensagem": "Sem pasta no L1 e sem escritório derivável (polo/setor). Aguardando processo.",
                    }

            # Trava-duplo: se já há tarefa PENDENTE pra esta DMI, pede confirmação
            # (a não ser que o operador já tenha confirmado). Cumprida não trava.
            # Só faz sentido com pasta — a busca de pendentes é dentro da pasta.
            if not confirmar and lawsuit_id:
                pendente = self._tarefa_pendente_da_dmi(solicitacao, lawsuit_id, client)
                if pendente:
                    return {
                        "ok": False,
                        "requires_confirmation": True,
                        "status_tratamento": solicitacao.status_tratamento,
                        "mensagem": (
                            f"Já existe tarefa PENDENTE no Legal One para esta DMI "
                            f"({pendente['status_label']}). Criar mesmo assim?"
                        ),
                        "tarefa_existente": pendente,
                    }

            publish_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            task_payload = {
                "description": description,
                "startDateTime": end_iso,
                "endDateTime": end_iso,
                "publishDate": publish_iso,
                "status": {"id": DEFAULT_TASK_STATUS_ID},
                "typeId": type_id,
                "subTypeId": subtype_id,
                "responsibleOfficeId": office_id,
                "originOfficeId": office_id,
                "participants": [
                    {
                        "contact": {"id": responsavel.external_id},
                        "isResponsible": True,
                        "isExecuter": True,
                        "isRequester": True,
                    }
                ],
            }
            if notes:
                task_payload["notes"] = notes

            created = client.create_task(task_payload)
            if not created or not created.get("id"):
                raise Exception("Falha na criação da tarefa (resposta inválida da API).")
            task_id = created["id"]

            # Vínculo com a pasta SÓ quando há pasta. Avulsa fica sem vínculo.
            if lawsuit_id and not client.link_task_to_lawsuit(
                task_id, {"linkType": "Litigation", "linkId": lawsuit_id}
            ):
                logger.warning("Tarefa %s criada mas falha ao vincular ao processo %s.", task_id, lawsuit_id)

            solicitacao.created_task_id = task_id
            solicitacao.linked_lawsuit_id = lawsuit_id
            solicitacao.status_tratamento = STATUS_TRATAMENTO_AGENDADO
            solicitacao.last_error = None
            solicitacao.scheduled_by_user_id = current_user.id
            solicitacao.scheduled_by_email = current_user.email
            solicitacao.scheduled_by_nome = current_user.name
            solicitacao.scheduled_at = datetime.now(timezone.utc)
            self.db.commit()
            return {
                "ok": True,
                "status_tratamento": solicitacao.status_tratamento,
                "created_task_id": task_id,
                "mensagem": (
                    "Tarefa AVULSA criada no Legal One (sem vínculo com processo, "
                    "no escritório do polo/setor)."
                    if avulsa
                    else "Tarefa criada no Legal One com sucesso."
                ),
            }

        except Exception as e:
            erro = str(e)
            solicitacao.status_tratamento = STATUS_TRATAMENTO_ERRO
            solicitacao.last_error = erro
            self.db.commit()
            logger.error(
                "Falha ao agendar OneRequest (DMI %s): %s", solicitacao.numero_solicitacao, erro
            )
            return {
                "ok": False,
                "status_tratamento": solicitacao.status_tratamento,
                "mensagem": f"Erro ao agendar: {erro}",
            }

    # ──────────────────────────────────────────────────────────────────
    # Anotações (log de auditoria por DMI)
    # ──────────────────────────────────────────────────────────────────
    def list_anotacoes(self, solicitacao_id: int) -> list[dict]:
        rows = (
            self.db.query(OnerequestAnotacao)
            .filter(OnerequestAnotacao.solicitacao_id == solicitacao_id)
            .order_by(OnerequestAnotacao.created_at.desc())
            .all()
        )
        return [
            {
                "id": a.id,
                "texto": a.texto,
                "autor_nome": a.autor_nome,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in rows
        ]

    def add_anotacao(self, solicitacao_id: int, texto: str, autor: LegalOneUser) -> dict:
        anot = OnerequestAnotacao(
            solicitacao_id=solicitacao_id,
            texto=texto.strip(),
            autor_user_id=autor.id,
            autor_nome=autor.name,
        )
        self.db.add(anot)
        self.db.commit()
        self.db.refresh(anot)
        return {
            "id": anot.id,
            "texto": anot.texto,
            "autor_nome": anot.autor_nome,
            "created_at": anot.created_at.isoformat() if anot.created_at else None,
        }

    # ──────────────────────────────────────────────────────────────────
    # Legal One: resolução do processo + tarefas na pasta (sob demanda)
    # ──────────────────────────────────────────────────────────────────
    def _resolver_por_npj(self, client: LegalOneApiClient, npj: Optional[str]) -> Optional[dict]:
        """Resolve a pasta pelo NPJ do BB, gravado nas NOTAS (ou título) do
        cadastro da pasta no L1. Filtra por contains(notes|title, <dígitos>).
        Confirmado empiricamente: NPJ vive em `notes` (maioria) e às vezes
        `title`; `contains` funciona no /Lawsuits (substringof não)."""
        core = re.sub(r"\D", "", (npj or "").split("-")[0])
        if len(core) < 7:
            return None
        for field in ("notes", "title"):
            try:
                res = client._paginated_catalog_loader(
                    "/Lawsuits",
                    {
                        "$filter": f"contains({field},'{core}')",
                        "$select": "id,identifierNumber,responsibleOfficeId,folder",
                        "$top": 5,
                    },
                )
            except Exception as e:
                logger.warning("OneRequest: busca por NPJ em %s falhou: %s", field, e)
                continue
            if res:
                return res[0]
        return None

    def resolver_lawsuit(
        self, solicitacao: OnerequestSolicitacao, client: LegalOneApiClient
    ) -> Optional[dict]:
        """Resolve a pasta no L1 e cacheia o lawsuit_id. Cascata: CNJ -> NPJ
        (notes/title). Retorna o dict do processo (id, responsibleOfficeId, ...)
        ou None. NÃO exige CNJ."""
        if solicitacao.linked_lawsuit_id:
            try:
                return client.get_lawsuit_by_id(solicitacao.linked_lawsuit_id)
            except Exception:
                return {"id": solicitacao.linked_lawsuit_id}

        law = None
        if _proc_utilizavel(solicitacao.numero_processo):
            try:
                law = client.search_lawsuit_by_cnj(solicitacao.numero_processo)
            except Exception as e:
                logger.warning("Falha ao resolver por CNJ %s: %s", solicitacao.numero_processo, e)
                law = None
        if not (law and law.get("id")):
            law = self._resolver_por_npj(client, solicitacao.npj_direcionador)

        if law and law.get("id"):
            solicitacao.linked_lawsuit_id = law["id"]
            self.db.commit()
            return law
        return None

    def verificar_processo_l1(
        self, solicitacao: OnerequestSolicitacao, client: LegalOneApiClient
    ) -> dict:
        """Resolve o processo (CNJ -> NPJ) SEM criar tarefa, só pra sinalizar se a
        pasta existe no L1 antes do agendamento. Grava proc_l1_checado_em/
        encontrado/via. Idempotente — pode rodar de novo (re-checagem)."""
        encontrado = False
        via = None
        lawsuit_id = solicitacao.linked_lawsuit_id
        if lawsuit_id:
            encontrado, via = True, "cache"
        else:
            law = None
            if _proc_utilizavel(solicitacao.numero_processo):
                try:
                    law = client.search_lawsuit_by_cnj(solicitacao.numero_processo)
                except Exception as e:
                    logger.warning(
                        "verificar_processo_l1: busca por CNJ %s falhou: %s",
                        solicitacao.numero_processo, e,
                    )
                    law = None
                if law and law.get("id"):
                    via = "cnj"
            if not (law and law.get("id")):
                law = self._resolver_por_npj(client, solicitacao.npj_direcionador)
                if law and law.get("id"):
                    via = "npj"
            if law and law.get("id"):
                encontrado = True
                lawsuit_id = law["id"]
                solicitacao.linked_lawsuit_id = lawsuit_id

        solicitacao.proc_l1_checado_em = datetime.now(timezone.utc)
        solicitacao.proc_l1_encontrado = encontrado
        solicitacao.proc_l1_via = via
        self.db.commit()
        return {"encontrado": encontrado, "via": via, "lawsuit_id": lawsuit_id}

    def _office_avulso_id(self, solicitacao: OnerequestSolicitacao) -> Optional[int]:
        """Escritório (L1) pra TAREFA AVULSA (sem pasta), derivado do polo/setor:
        BB é Autor no polo ATIVO, Réu no PASSIVO. Busca o external_id (= id do
        escritório no L1) em legal_one_offices, sem hardcode. None = não derivável."""
        from app.models.legal_one import LegalOneOffice

        polo = (solicitacao.polo or "").strip().lower()
        setor = (solicitacao.setor or "").strip().lower()
        if polo == "ativo" or "autor" in setor:
            alvo = "Autor"
        elif polo == "passivo" or "réu" in setor or "reu" in setor:
            alvo = "Réu"
        else:
            return None
        row = (
            self.db.query(LegalOneOffice.external_id)
            .filter(
                LegalOneOffice.path.ilike("%Banco do Brasil%"),
                LegalOneOffice.name == alvo,
            )
            .first()
        )
        return int(row[0]) if row and row[0] else None

    def tarefas_na_pasta(
        self, solicitacao: OnerequestSolicitacao, client: LegalOneApiClient
    ) -> dict:
        """Tarefas pendentes/concluídas na pasta do processo no L1 (sob demanda)."""
        law = self.resolver_lawsuit(solicitacao, client)
        lid = law.get("id") if law else None
        if not lid:
            return {
                "lawsuit_id": None,
                "l1_url": None,
                "pendentes": [],
                "concluidas": [],
                "resolvido": False,
                "check_failed": False,
            }
        url = _l1_lawsuit_url(lid)
        try:
            tasks = client.find_tasks_for_lawsuit(lid, top=30)
        except Exception as e:
            logger.warning("Falha ao buscar tarefas da pasta %s: %s", lid, e)
            return {
                "lawsuit_id": lid,
                "l1_url": url,
                "pendentes": [],
                "concluidas": [],
                "resolvido": True,
                "check_failed": True,
            }
        pendentes, concluidas = [], []
        for t in tasks:
            sid = t.get("statusId")
            if sid is None and isinstance(t.get("status"), dict):
                sid = t["status"].get("id")
            item = {
                "task_id": t.get("id"),
                "description": t.get("description"),
                "status_id": sid,
                "status_label": L1_STATUS_LABELS_FULL.get(sid, str(sid)),
                "end_date_time": t.get("endDateTime"),
                "l1_url": _l1_task_url(t.get("id"), lid),
            }
            (pendentes if sid in L1_BLOCKING_STATUS_IDS else concluidas).append(item)
        return {
            "lawsuit_id": lid,
            "l1_url": url,
            "pendentes": pendentes,
            "concluidas": concluidas[:10],
            "resolvido": True,
            "check_failed": False,
        }

    # ──────────────────────────────────────────────────────────────────
    # Acompanhamento no L1 (sob demanda): a tarefa da DMI foi respondida?
    # ──────────────────────────────────────────────────────────────────
    @staticmethod
    def _task_status_id(task: dict) -> Optional[int]:
        sid = task.get("statusId")
        if sid is None and isinstance(task.get("status"), dict):
            sid = task["status"].get("id")
        return sid

    def _pick_dmi_task(self, tasks: list) -> Optional[dict]:
        """Dentre as tarefas que casam com o número da DMI, escolhe a mais
        representativa: prioriza uma Cumprida (a DMI foi respondida); senão a
        mais recente (a busca já vem em id desc)."""
        if not tasks:
            return None
        for t in tasks:
            if self._task_status_id(t) == 1:  # Cumprido
                return t
        return tasks[0]

    def _status_l1_payload(self, s: OnerequestSolicitacao) -> dict:
        sid = s.l1_dmi_status_id
        lid = s.linked_lawsuit_id
        return {
            "checked_at": s.l1_checked_at.isoformat() if s.l1_checked_at else None,
            "resolvido": lid is not None,
            "lawsuit_id": lid,
            "l1_url": _l1_lawsuit_url(lid) if lid else None,
            "dmi_task_id": s.l1_dmi_task_id,
            "dmi_task_url": _l1_task_url(s.l1_dmi_task_id, lid) if (s.l1_dmi_task_id and lid) else None,
            "dmi_status_id": sid,
            "dmi_status_label": L1_STATUS_LABELS_FULL.get(sid) if sid is not None else None,
            "dmi_respondida": sid == 1,
            "dmi_encontrada": s.l1_dmi_task_id is not None,
            "pendentes_count": s.l1_pendentes_count,
            "sem_pendencia": (s.l1_pendentes_count == 0) if s.l1_pendentes_count is not None else None,
        }

    def verificar_status_l1(
        self, solicitacao: OnerequestSolicitacao, client: LegalOneApiClient
    ) -> dict:
        """Checa no Legal One, sob demanda, e cacheia na linha:
          (A) a TAREFA DA DMI está Cumprida? (match por nº da solicitação na
              descrição — legado `<num>\\t…`, `… | DMI: <num>`, reativações);
          (B) a PASTA tem tarefa Pendente/Iniciado? (0 = sem pendência).
        Não exige CNJ (resolve por NPJ). Best-effort: falha de rede num sinal
        não derruba o outro."""
        solicitacao.l1_checked_at = datetime.now(timezone.utc)
        solicitacao.l1_dmi_task_id = None
        solicitacao.l1_dmi_status_id = None
        solicitacao.l1_pendentes_count = None

        law = self.resolver_lawsuit(solicitacao, client)
        lid = law.get("id") if law else None
        if not lid:
            self.db.commit()
            return self._status_l1_payload(solicitacao)

        # Sinal A: tarefa da DMI. Usa o número COMPLETO no contains (preciso —
        # o curto dá falso-positivo, ex.: '061732' ⊂ '0617321').
        numero = (solicitacao.numero_solicitacao or "").strip()
        if numero:
            rel = (
                "relationships/any("
                f"r: r/linkType eq 'Litigation' and r/linkId eq {int(lid)})"
            )
            esc = numero.replace("'", "''")
            try:
                matched = client.search_tasks(
                    filter_expression=f"{rel} and contains(description,'{esc}')",
                    top=30,
                    orderby="id desc",
                    select="id,description,statusId,status,endDateTime",
                )
            except Exception as e:
                logger.warning(
                    "OneRequest status L1: busca da tarefa da DMI %s falhou: %s", numero, e
                )
                matched = []
            best = self._pick_dmi_task(matched)
            if best:
                solicitacao.l1_dmi_task_id = best.get("id")
                solicitacao.l1_dmi_status_id = self._task_status_id(best)

        # Sinal B: pendências na pasta (Pendente/Iniciado).
        try:
            pendentes = client.find_tasks_for_lawsuit(
                lid, status_ids=list(L1_BLOCKING_STATUS_IDS), top=30
            )
            solicitacao.l1_pendentes_count = len(pendentes)
        except Exception as e:
            logger.warning(
                "OneRequest status L1: contagem de pendentes da pasta %s falhou: %s", lid, e
            )

        self.db.commit()
        return self._status_l1_payload(solicitacao)

    # ──────────────────────────────────────────────────────────────────
    # Auditoria total (consulta por CNJ ou nº da DMI): quem agendou, o que,
    # pra quem — reconstruído + tarefa VIVA no L1 + histórico de anotações.
    # ──────────────────────────────────────────────────────────────────
    _AUDIT_TASK_SELECT = (
        "id,description,statusId,status,startDateTime,endDateTime,typeId,subTypeId"
    )

    def _fetch_audit_task(
        self, s: OnerequestSolicitacao, client: LegalOneApiClient
    ) -> Optional[dict]:
        """A tarefa real da DMI no L1: pelo created_task_id (agendadas via Flow)
        ou por match do número na descrição (legado). None se não achar."""
        law = self.resolver_lawsuit(s, client)
        lid = law.get("id") if law else None
        if not lid:
            return None
        task = None
        try:
            if s.created_task_id:
                found = client.search_tasks(
                    filter_expression=f"id eq {int(s.created_task_id)}",
                    top=1,
                    select=self._AUDIT_TASK_SELECT,
                )
                task = found[0] if found else None
            if task is None:
                numero = (s.numero_solicitacao or "").strip()
                if numero:
                    rel = (
                        "relationships/any("
                        f"r: r/linkType eq 'Litigation' and r/linkId eq {int(lid)})"
                    )
                    esc = numero.replace("'", "''")
                    matched = client.search_tasks(
                        filter_expression=f"{rel} and contains(description,'{esc}')",
                        top=30,
                        orderby="id desc",
                        select=self._AUDIT_TASK_SELECT,
                    )
                    task = self._pick_dmi_task(matched)
        except Exception as e:
            logger.warning(
                "OneRequest auditoria: falha buscando tarefa no L1 da DMI %s: %s",
                s.numero_solicitacao, e,
            )
            return None
        if not task:
            return {"lawsuit_url": _l1_lawsuit_url(lid), "task_id": None}
        sid = self._task_status_id(task)
        return {
            "task_id": task.get("id"),
            "description": task.get("description"),
            "status_id": sid,
            "status_label": L1_STATUS_LABELS_FULL.get(sid, str(sid)) if sid is not None else None,
            "start_date_time": task.get("startDateTime"),
            "end_date_time": task.get("endDateTime"),
            "l1_url": _l1_task_url(task.get("id"), lid),
            "lawsuit_url": _l1_lawsuit_url(lid),
        }

    def auditoria(self, s: OnerequestSolicitacao, client: LegalOneApiClient) -> dict:
        """Auditoria reconstruída de uma DMI + tarefa viva no L1 + anotações."""
        resp_nome = None
        if s.responsavel_user_id:
            u = (
                self.db.query(LegalOneUser)
                .filter(LegalOneUser.id == s.responsavel_user_id)
                .first()
            )
            resp_nome = u.name if u else None
        agendamento = {
            "agendado": bool(s.created_task_id)
            or s.status_tratamento == STATUS_TRATAMENTO_AGENDADO,
            "scheduled_by_nome": s.scheduled_by_nome,
            "scheduled_by_email": s.scheduled_by_email,
            "scheduled_at": s.scheduled_at.isoformat() if s.scheduled_at else None,
            "responsavel_nome": resp_nome,
            "setor": s.setor,
            "data_agendamento": s.data_agendamento,
            "prazo_bb": s.prazo,
            "created_task_id": s.created_task_id,
            "status_sistema": s.status_sistema,
            "status_tratamento": s.status_tratamento,
            "last_error": s.last_error,
        }
        return {
            "id": s.id,
            "numero_solicitacao": s.numero_solicitacao,
            "numero_processo": s.numero_processo,
            "npj_direcionador": s.npj_direcionador,
            "titulo": s.titulo,
            "agendamento": agendamento,
            "tarefa_l1": self._fetch_audit_task(s, client),
            "anotacoes": self.list_anotacoes(s.id),
        }

    # ──────────────────────────────────────────────────────────────────
    # Alertas "vence hoje" agrupados por responsável (texto pronto p/ copiar)
    # ──────────────────────────────────────────────────────────────────
    def alertas_vence_hoje(self) -> list[dict]:
        """Agrupa as DMIs ABERTAS que vencem HOJE por responsável e monta uma
        mensagem de alerta pronta pra copiar (Teams/WhatsApp)."""
        hoje = date.today()
        abertas = (
            self.db.query(OnerequestSolicitacao)
            .filter(OnerequestSolicitacao.status_sistema == STATUS_SISTEMA_ABERTO)
            .all()
        )
        alvos = [s for s in abertas if _parse_prazo(s.prazo) == hoje]

        uids = {s.responsavel_user_id for s in alvos if s.responsavel_user_id}
        nomes: dict = {}
        emails: dict = {}
        if uids:
            for uid, uname, uemail in self.db.query(
                LegalOneUser.id, LegalOneUser.name, LegalOneUser.email
            ).filter(LegalOneUser.id.in_(uids)):
                nomes[uid] = uname
                emails[uid] = uemail

        grupos: dict = {}
        for s in alvos:
            grupos.setdefault(s.responsavel_user_id or 0, []).append(s)

        hoje_br = hoje.strftime("%d/%m/%Y")
        out = []
        for key, lst in grupos.items():
            nome = nomes.get(key) or "Sem responsável"
            primeiro = nome.split()[0] if nome != "Sem responsável" else "pessoal"
            linhas = []
            for s in lst:
                proc = s.numero_processo or s.npj_direcionador or "—"
                tit = (s.titulo or "").strip()
                linhas.append(
                    f"• DMI {s.numero_solicitacao} — Proc {proc}"
                    + (f" — {tit}" if tit else "")
                )
            mensagem = (
                f"Olá, {primeiro}! As DMIs do Banco do Brasil abaixo estão "
                f"PENDENTES DE RESPOSTA e o prazo é HOJE ({hoje_br}):\n\n"
                + "\n".join(linhas)
                + "\n\nPor favor, dê andamento ainda hoje. Se estiver aguardando "
                "alguma providência (ex.: do cliente), registre uma anotação na "
                "DMI pra justificar. Obrigada!"
            )
            email = emails.get(key)
            out.append(
                {
                    "responsavel_user_id": key or None,
                    "responsavel_nome": nome,
                    "responsavel_email": email,
                    "teams_disponivel": self._teams_disponivel(email),
                    "count": len(lst),
                    "mensagem": mensagem,
                }
            )
        out.sort(key=lambda g: (g["responsavel_nome"] == "Sem responsável", g["responsavel_nome"]))
        return out

    @staticmethod
    def _teams_disponivel(email: Optional[str]) -> bool:
        """Endereçável no Teams? Só com e-mail corporativo M365 E o envio
        habilitado (os demais usam e-mail pessoal → só Copiar)."""
        from app.core.config import settings

        if not settings.teams_alert_enabled:
            return False
        dominio = (settings.teams_corporate_email_domain or "").strip().lower()
        return bool(email) and bool(dominio) and email.strip().lower().endswith("@" + dominio)

    def enviar_alerta_teams(self, responsavel_user_id: int, graph_token: str) -> dict:
        """Manda a DM de alerta 'vence hoje' no Teams via Microsoft Graph, com o
        token DELEGADO da operadora (a mensagem sai NO NOME dela). Recalcula a
        mensagem no servidor (não confia no texto do cliente). Passos: /me ->
        /users/{email} -> POST /chats (1:1) -> POST /chats/{id}/messages."""
        import html

        import requests

        from app.core.config import settings

        GRAPH = "https://graph.microsoft.com/v1.0"

        if not settings.teams_alert_enabled:
            return {"ok": False, "mensagem": "Envio pelo Teams não está habilitado."}
        if not (graph_token or "").strip():
            return {"ok": False, "mensagem": "Sessão do Teams não autenticada. Tente de novo."}

        grupos = {g["responsavel_user_id"]: g for g in self.alertas_vence_hoje()}
        g = grupos.get(responsavel_user_id)
        if not g:
            return {"ok": False, "mensagem": "Nenhuma DMI vencendo hoje para esse responsável."}
        if not g.get("teams_disponivel"):
            return {
                "ok": False,
                "mensagem": "Responsável sem e-mail corporativo M365 — use Copiar e envie manualmente.",
            }

        email = g["responsavel_email"]
        headers = {
            "Authorization": f"Bearer {graph_token}",
            "Content-Type": "application/json",
        }
        try:
            me = requests.get(f"{GRAPH}/me?$select=id", headers=headers, timeout=15)
            me.raise_for_status()
            my_id = me.json().get("id")

            usr = requests.get(
                f"{GRAPH}/users/{email}?$select=id", headers=headers, timeout=15
            )
            if usr.status_code == 404:
                return {
                    "ok": False,
                    "mensagem": f"{g['responsavel_nome']} não tem conta no Teams da empresa.",
                }
            usr.raise_for_status()
            target_id = usr.json().get("id")

            chat = requests.post(
                f"{GRAPH}/chats",
                headers=headers,
                json={
                    "chatType": "oneOnOne",
                    "members": [
                        {
                            "@odata.type": "#microsoft.graph.aadUserConversationMember",
                            "roles": ["owner"],
                            "user@odata.bind": f"{GRAPH}/users('{my_id}')",
                        },
                        {
                            "@odata.type": "#microsoft.graph.aadUserConversationMember",
                            "roles": ["owner"],
                            "user@odata.bind": f"{GRAPH}/users('{target_id}')",
                        },
                    ],
                },
                timeout=20,
            )
            chat.raise_for_status()
            chat_id = chat.json().get("id")

            content = html.escape(g["mensagem"]).replace("\n", "<br>")
            msg = requests.post(
                f"{GRAPH}/chats/{chat_id}/messages",
                headers=headers,
                json={"body": {"contentType": "html", "content": content}},
                timeout=20,
            )
            msg.raise_for_status()
        except requests.exceptions.HTTPError as e:
            detalhe = e.response.text[:300] if e.response is not None else str(e)
            logger.error("OneRequest Teams/Graph erro p/ %s: %s", email, detalhe)
            return {"ok": False, "mensagem": f"Falha no Teams (Graph): {detalhe[:160]}"}
        except Exception as e:
            logger.error("OneRequest Teams/Graph erro inesperado p/ %s: %s", email, e)
            return {"ok": False, "mensagem": f"Falha ao enviar pelo Teams: {e}"}

        logger.info(
            "OneRequest: alerta Teams (Graph) enviado p/ %s (%s DMIs).",
            email, g["count"],
        )
        return {
            "ok": True,
            "mensagem": f"Alerta enviado no Teams para {g['responsavel_nome']}.",
        }

    def estado(self) -> dict:
        """Heartbeat da última ingestão + farol GLOBAL das DMIs abertas.

        Os KPI cards do topo são ESTÁTICOS: mostram sempre o total de cada
        farol (pendentes/atrasadas/vence hoje/amanhã/fim de semana/futuras) de
        TODAS as abertas, independente da aba/filtro selecionado na tabela.
        """
        from app.services.app_settings import get_setting

        last = get_setting("onerequest_last_ingest_at")
        hoje = date.today()
        prazos = (
            self.db.query(OnerequestSolicitacao.prazo)
            .filter(OnerequestSolicitacao.status_sistema == STATUS_SISTEMA_ABERTO)
            .all()
        )
        _map = {
            "atrasado": "atrasadas", "vermelho": "hoje", "amarelo": "amanha",
            "roxo": "fds", "verde": "futuras", "cinza": "sem_prazo",
        }
        kpis = {"atrasadas": 0, "hoje": 0, "amanha": 0, "fds": 0, "futuras": 0, "sem_prazo": 0}
        for (prazo,) in prazos:
            kpis[_map.get(_farol(_parse_prazo(prazo), hoje), "sem_prazo")] += 1
        return {"last_ingest_at": last, "abertas": len(prazos), "kpis": kpis}

    @staticmethod
    def setores_disponiveis() -> list[str]:
        return list(SECTOR_TASK_MAPPING.keys())
