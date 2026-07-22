"""Balanceador de Agenda — leituras do pool de pendentes pro supervisor
diagnosticar carga e redistribuir entre colaboradores.

MOCK (2026-06-29): lê do snapshot `perf_l1_tarefa` (o mesmo do Minha Equipe).
Na versão real a fila vem AO VIVO do L1 e a escrita reatribui de fato
(API PATCH p/ normal, POST ModalEnvolvimentoEmLote p/ Workflow — já provado).
Aqui é tudo read-only.

Escopo redistribuível: TODAS as pendentes — inclusive os subtipos `Acompanhar*`
(restrição removida em 2026-07-09 após revisão; antes ficavam de fora por serem
segmento de tarefa já iniciada).
"""

import datetime as _dt

from sqlalchemy import text
from sqlalchemy.orm import Session

_BRT = "America/Sao_Paulo"
_HOJE = f"(now() AT TIME ZONE '{_BRT}')::date"
_PRAZO = f"(t.prazo_previsto AT TIME ZONE '{_BRT}')::date"

try:
    from zoneinfo import ZoneInfo

    _TZ = ZoneInfo(_BRT)
except Exception:  # pragma: no cover
    _TZ = None


def _hoje_brt() -> _dt.date:
    return (_dt.datetime.now(tz=_TZ) if _TZ else _dt.datetime.now()).date()


def _parse_l1_dt(valor: str | None) -> _dt.datetime | None:
    """Datetime do L1 → aware em BRT. O L1 mistura formatos: '-03:00' e 'Z'
    (UTC) — e o fromisoformat do Py3.10 NÃO aceita 'Z', o que rotulava
    centenas de tarefas como sem_prazo (caso Myllene: 396 de 423, todas com
    'Z') e as jogava pro FIM da fila de divisão. Converter pra BRT também
    corrige a situação: 02:59Z = 23:59 BRT do dia ANTERIOR."""
    if not valor:
        return None
    try:
        d = _dt.datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is not None and _TZ is not None:
        d = d.astimezone(_TZ)
    return d


# Cache (vida do processo) do catálogo de usuários do L1. Usuários mudam
# raramente; evita bater get_all_users (~310, ~6s) a cada chamada.
_USERS: list = []  # [{id, name, norm}]


def _users() -> list:
    if not _USERS:
        from app.services.legal_one_client import LegalOneApiClient
        from app.services.performance.seed import norm

        for u in LegalOneApiClient().get_all_users():
            nome, uid = u.get("name"), u.get("id")
            if nome and uid:
                _USERS.append({"id": uid, "name": nome, "norm": norm(nome)})
    return _USERS


def _user_map() -> dict:
    """nome_norm -> contact_id (deriva do cache de usuários)."""
    return {u["norm"]: u["id"] for u in _users()}


def _periodo_clause(dias: int) -> str:
    """Janela: atrasados + os que vencem nos próximos `dias` (+ sem prazo).
    dias<=0 = tudo (sem teto)."""
    if dias and dias > 0:
        return f" AND (t.prazo_previsto IS NULL OR {_PRAZO} <= {_HOJE} + :dias)"
    return ""


class BalanceadorService:
    def __init__(self, db: Session):
        self.db = db

    def diagnostico(self, team: str) -> list[dict]:
        """Por colaborador do time: pendentes atrasadas / fatais hoje / futuras."""
        rows = self.db.execute(
            text(
                f"""
                SELECT p.id, p.nome, p.cargo, p.is_supervisor,
                  count(t.id) FILTER (WHERE t.prazo_previsto IS NOT NULL AND {_PRAZO} < {_HOJE}) AS atrasado,
                  count(t.id) FILTER (WHERE {_PRAZO} = {_HOJE}) AS fatal_hoje,
                  count(t.id) FILTER (WHERE t.prazo_previsto IS NOT NULL AND {_PRAZO} > {_HOJE}) AS futuro,
                  count(t.id) FILTER (WHERE t.prazo_previsto IS NULL) AS sem_prazo,
                  count(t.id) AS total
                FROM perf_pessoa p
                LEFT JOIN perf_l1_tarefa t
                  ON t.pessoa_id = p.id AND t.status = 'Pendente'
                WHERE p.equipe = :team AND p.ativo
                GROUP BY p.id, p.nome, p.cargo, p.is_supervisor
                ORDER BY p.is_supervisor DESC, atrasado DESC, futuro DESC, p.nome
                """
            ),
            {"team": team},
        ).fetchall()
        return [
            {
                "id": r.id, "nome": r.nome, "cargo": r.cargo, "is_supervisor": r.is_supervisor,
                "atrasado": r.atrasado, "fatal_hoje": r.fatal_hoje, "futuro": r.futuro,
                "sem_prazo": r.sem_prazo, "total": r.total,
            }
            for r in rows
        ]

    def redistribuir_matriz(self, team: str, pessoa_ids: list, dias: int) -> list[dict]:
        """Subtipos × colaborador (contagens) pra os escolhidos, dentro do período."""
        if not pessoa_ids:
            return []
        rows = self.db.execute(
            text(
                f"""
                SELECT t.pessoa_id, coalesce(t.subtipo, '(sem subtipo)') AS subtipo,
                  count(*) AS total,
                  count(*) FILTER (WHERE t.prazo_previsto IS NOT NULL AND {_PRAZO} < {_HOJE}) AS atrasado,
                  count(*) FILTER (WHERE {_PRAZO} = {_HOJE}) AS fatal_hoje
                FROM perf_l1_tarefa t
                WHERE t.pessoa_id = ANY(:ids) AND t.status = 'Pendente'
                  {_periodo_clause(dias)}
                GROUP BY t.pessoa_id, subtipo
                ORDER BY total DESC
                """
            ),
            {"ids": list(pessoa_ids), "dias": dias},
        ).fetchall()
        return [
            {
                "pessoa_id": r.pessoa_id, "subtipo": r.subtipo, "total": r.total,
                "atrasado": r.atrasado, "fatal_hoje": r.fatal_hoje,
            }
            for r in rows
        ]

    def redistribuir_tarefas(self, team: str, pessoa_id: int, subtipo: str, dias: int, limit: int = 500) -> list[dict]:
        """Tarefas individuais de um (colaborador, subtipo) pro modal de detalhe."""
        sub_clause = "t.subtipo IS NULL" if subtipo == "(sem subtipo)" else "t.subtipo = :sub"
        rows = self.db.execute(
            text(
                f"""
                SELECT t.l1_task_id, t.subtipo, t.cnj, t.pasta, t.uf,
                  t.prazo_previsto, t.cadastrado_em,
                  CASE WHEN t.prazo_previsto IS NULL THEN 'sem_prazo'
                       WHEN {_PRAZO} < {_HOJE} THEN 'atrasado'
                       WHEN {_PRAZO} = {_HOJE} THEN 'fatal_hoje'
                       ELSE 'futuro' END AS situacao
                FROM perf_l1_tarefa t
                WHERE t.pessoa_id = :pid AND t.status = 'Pendente' AND {sub_clause}
                  {_periodo_clause(dias)}
                ORDER BY t.prazo_previsto ASC NULLS LAST
                LIMIT :lim
                """
            ),
            {"pid": pessoa_id, "sub": subtipo, "dias": dias, "lim": limit},
        ).fetchall()
        return [
            {
                "l1_task_id": r.l1_task_id, "subtipo": r.subtipo, "cnj": r.cnj,
                "pasta": r.pasta, "uf": r.uf, "situacao": r.situacao,
                "prazo": r.prazo_previsto.isoformat() if r.prazo_previsto else None,
            }
            for r in rows
        ]

    def descricoes(self, ids: list) -> dict:
        """Descrição (assunto/anotações) AO VIVO do L1 pra os task ids dados —
        não vem no snapshot. Batch de 30 (limite do $top em /Tasks)."""
        from app.services.legal_one_client import LegalOneApiClient

        clean = [int(i) for i in ids if i][:150]
        if not clean:
            return {}
        client = LegalOneApiClient()
        out: dict = {}
        for i in range(0, len(clean), 30):
            chunk = clean[i : i + 30]
            flt = "id in (" + ",".join(str(x) for x in chunk) + ")"
            try:
                for t in client.search_tasks(filter_expression=flt, top=30, select="id,description"):
                    out[t.get("id")] = t.get("description")
            except Exception:  # noqa: BLE001 — best-effort; descrição é enriquecimento
                continue
        return out

    # ── Log de redistribuição (aba Relatórios) ──
    def registrar_log(self, team: str, user, movimentos: list) -> dict:
        from app.models.performance import BalanceadorLog

        total_tar = sum(int(m.get("qtd") or 0) for m in (movimentos or []))
        log = BalanceadorLog(
            team=team,
            criado_por_id=getattr(user, "id", None),
            criado_por_nome=getattr(user, "name", None) or getattr(user, "email", None),
            total_movimentos=len(movimentos or []),
            total_tarefas=total_tar,
            origem="mock",
            detalhe=movimentos or [],
        )
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)
        return {"id": log.id, "total_movimentos": log.total_movimentos, "total_tarefas": log.total_tarefas}

    def buscar_usuarios(self, team: str, busca: str, limit_externos: int = 20) -> list:
        """Destinos da distribuição em fila. PRIORIZA o roster do setor (aparece
        primeiro, e sem busca já mostra o time todo); só vai no catálogo do L1
        (externos) quando há texto de busca. `setor=True/False` distingue."""
        from app.models.performance import PerfPessoa
        from app.services.performance.seed import norm

        b = norm(busca)
        out, vistos = [], set()
        rows = (
            self.db.query(PerfPessoa)
            .filter(PerfPessoa.equipe == team, PerfPessoa.ativo.is_(True))
            .order_by(PerfPessoa.is_supervisor.desc(), PerfPessoa.nome)
            .all()
        )
        for p in rows:
            if b and b not in p.nome_norm:
                continue
            out.append({"id": p.id, "nome": p.nome, "setor": True})
            vistos.add(p.nome_norm)
        if b:  # externos só sob busca, sem repetir quem já é do setor
            ext = 0
            for u in _users():
                if u["norm"] in vistos or b not in u["norm"]:
                    continue
                out.append({"id": u["id"], "nome": u["name"], "setor": False})
                ext += 1
                if ext >= limit_externos:
                    break
        return out

    def listar_logs(self, team: str, limit: int = 10, offset: int = 0) -> dict:
        from app.models.performance import BalanceadorLog

        base = self.db.query(BalanceadorLog).filter(BalanceadorLog.team == team)
        total = base.count()
        rows = (
            base.order_by(BalanceadorLog.criado_em.desc())
            .offset(max(0, offset))
            .limit(max(1, min(limit, 100)))
            .all()
        )
        logs = [
            {
                "id": r.id,
                "criado_em": r.criado_em.isoformat() if r.criado_em else None,
                "criado_por_nome": r.criado_por_nome,
                "total_movimentos": r.total_movimentos,
                "total_tarefas": r.total_tarefas,
                "origem": r.origem,
                "detalhe": r.detalhe or [],
            }
            for r in rows
        ]
        return {"total": total, "logs": logs}

    # ── Destinos recorrentes da distribuição em fila (preferência aprendida) ──
    def sugestoes_fila(self, team: str, origem_pessoa_id: int, subtipo: str, limit: int = 8) -> list:
        from app.models.performance import BalanceadorFilaPref

        rows = (
            self.db.query(BalanceadorFilaPref)
            .filter(
                BalanceadorFilaPref.origem_pessoa_id == origem_pessoa_id,
                BalanceadorFilaPref.subtipo == subtipo,
            )
            .order_by(BalanceadorFilaPref.vezes.desc(), BalanceadorFilaPref.ultimo_uso.desc())
            .limit(limit)
            .all()
        )
        return [{"id": r.alvo_id, "nome": r.alvo_nome, "vezes": r.vezes} for r in rows]

    def registrar_fila_pref(self, team: str, origem_pessoa_id: int, subtipo: str, alvos: list) -> dict:
        """+1 vez por alvo distribuído nessa (origem, subtipo) — alimenta as sugestões."""
        from sqlalchemy.sql import func as _func

        from app.models.performance import BalanceadorFilaPref

        for a in alvos or []:
            nome = (a.get("nome") or "").strip()
            if not nome:
                continue
            row = (
                self.db.query(BalanceadorFilaPref)
                .filter_by(origem_pessoa_id=origem_pessoa_id, subtipo=subtipo, alvo_nome=nome)
                .first()
            )
            if row is None:
                row = BalanceadorFilaPref(
                    team=team, origem_pessoa_id=origem_pessoa_id, subtipo=subtipo,
                    alvo_id=a.get("id"), alvo_nome=nome, vezes=0,
                )
                self.db.add(row)
            row.vezes = (row.vezes or 0) + 1
            row.alvo_id = a.get("id") or row.alvo_id
            row.team = team
            row.ultimo_uso = _func.now()
        self.db.commit()
        return {"ok": True, "registrados": len(alvos or [])}

    @staticmethod
    def _carregar_l1_pool(client, endpoint: str, flt: str, max_itens: int) -> tuple:
        """Pagina UM endpoint do L1 (/Tasks ou /Appointments) com o filtro dado.
        Os dois compartilham o modelo (participants, statusId, endDateTime,
        subTypeId) — só o endpoint muda. Retorna (raw, total_real, capado)."""
        raw, skip, total_real, capado = [], 0, None, False
        while skip < max_itens:
            params = {
                "$filter": flt, "$top": "30", "$skip": str(skip),
                "$orderby": "endDateTime", "$select": "id,subTypeId,endDateTime,description",
            }
            if skip == 0:
                params["$count"] = "true"
            r = client._request_with_retry("GET", f"{client.base_url}{endpoint}", params=params)
            j = r.json()
            if total_real is None:
                total_real = j.get("@odata.count")
            batch = j.get("value", [])
            raw.extend(batch)
            if len(batch) < 30:
                break
            skip += 30
        else:
            capado = True
        return raw, (total_real or 0), capado

    # ── LIVE: pendentes não-iniciadas de uma pessoa, direto do L1 ──
    def live_pessoa(
        self,
        team: str,
        pessoa_id: int,
        dias: int = 0,
        incluir_atrasadas: bool = True,
        inicio: str | None = None,
        fim: str | None = None,
    ) -> dict:
        """Pendentes NÃO iniciadas (statusId=0) da pessoa, AO VIVO do L1 (filtro
        por participante). Agrupa por subtipo (nome via catálogo local
        LegalOneTaskSubType) + devolve os detalhes. Base da redistribuição em
        tempo real — o número que o supervisor vê é o de AGORA, não o snapshot.

        Recorte de data pela **data de conclusão prevista** (endDateTime):
        - `inicio`/`fim` (YYYY-MM-DD): faixa EXATA; as VENCIDAS (prazo < hoje)
          entram SEMPRE, independentemente do início (decisão do operador —
          o balanceamento existe pra resolver atraso).
        - fallback legado `dias>0`: janela "próximos N dias" (+ vencidas se
          incluir_atrasadas). Mantido pra compat de chamadas antigas.
        A ordenação é por prazo CRESCENTE (mais antigo/vencido primeiro) — a
        divisão prioriza as de conclusão prevista mais antiga."""
        from collections import defaultdict

        from app.models.legal_one import LegalOneTaskSubType
        from app.models.performance import PerfPessoa
        from app.services.legal_one_client import LegalOneApiClient

        p = self.db.query(PerfPessoa).filter(PerfPessoa.id == pessoa_id).first()
        if not p:
            return {"pessoa_id": pessoa_id, "nome": None, "resolvido": False, "subtipos": [], "tarefas": []}
        cid = _user_map().get(p.nome_norm)
        if not cid:
            return {"pessoa_id": pessoa_id, "nome": p.nome, "resolvido": False, "subtipos": [], "tarefas": []}

        client = LegalOneApiClient()
        # A agenda da pessoa = tarefas onde ela é EXECUTANTE (isExecuter), com o
        # prazo em endDateTime — é assim que o relatório Agenda Analytics (fonte
        # do snapshot/diagnóstico) atribui. Validado empiricamente 2026-07-07:
        # participants/any sem papel puxava também onde ela é só responsável/
        # solicitante (pool de OUTRO universo), e deadLine é null nessas tarefas
        # (o prazo real vive em endDateTime). Ordena da mais urgente. Recorte de
        # data feito NO L1.
        hoje = _hoje_brt()
        flt = (
            f"participants/any(pp: pp/contact/id eq {cid} and pp/isExecuter eq true)"
            " and statusId eq 0 and endDateTime ne null"
        )
        if inicio or fim:
            # Faixa EXATA por data de conclusão prevista. Vencidas sempre entram.
            if fim:
                flt += f" and endDateTime le {fim}T23:59:59-03:00"
            if inicio:
                flt += (
                    f" and (endDateTime ge {inicio}T00:00:00-03:00"
                    f" or endDateTime lt {hoje.isoformat()}T00:00:00-03:00)"
                )
        else:
            # Legado: janela "próximos N dias" (+ vencidas se pedido).
            if not incluir_atrasadas:
                flt += f" and endDateTime ge {hoje.isoformat()}T00:00:00-03:00"
            if dias and dias > 0:
                teto = hoje + _dt.timedelta(days=dias)
                flt += f" and endDateTime le {teto.isoformat()}T23:59:59-03:00"
        # No L1 "Compromissos e Tarefas" são entidades SEPARADAS na API (/Tasks e
        # /Appointments), mesmo modelo. A agenda de execução é a UNIÃO das duas —
        # ler só /Tasks perdia os compromissos (audiências, prazos internos,
        # peticionamentos) e subcontava a carga (ver docs/balanceador-compromissos-plano.md).
        # Teto alto (decisão do operador 2026-07-22): carrega o backlog inteiro
        # pra nunca deixar tarefa de fora da divisão. Como a ordem é prazo-antigo-
        # primeiro, se algum dia estourar, o que fica de fora é a de prazo mais
        # distante — nunca a mais antiga. Backstop contra runaway.
        MAX_POR_ENDPOINT = 1500  # 50 páginas/endpoint; L1 quebra cedo se vier menos
        raw_t, total_t, cap_t = self._carregar_l1_pool(client, "/Tasks", flt, MAX_POR_ENDPOINT)
        raw_a, total_a, cap_a = self._carregar_l1_pool(client, "/Appointments", flt, MAX_POR_ENDPOINT)
        for it in raw_t:
            it["_origem"] = "tarefa"
        for it in raw_a:
            it["_origem"] = "compromisso"
        raw = raw_t + raw_a
        total_real = total_t + total_a
        capado = cap_t or cap_a

        sub_ids = {t.get("subTypeId") for t in raw if t.get("subTypeId")}
        nomes = {
            s.external_id: s.name
            for s in self.db.query(LegalOneTaskSubType).filter(LegalOneTaskSubType.external_id.in_(sub_ids)).all()
        }
        # Fallback de nome pro subtipo FORA do catálogo local (não há endpoint
        # /TaskSubTypes no L1): o snapshot do relatório tem o nome dessas mesmas
        # tarefas — resolve subTypeId→nome pelo l1_task_id.
        faltantes = sub_ids - set(nomes)
        if faltantes:
            ids_falt = [int(t["id"]) for t in raw if t.get("subTypeId") in faltantes and t.get("id")]
            if ids_falt:
                for row in self.db.execute(
                    text("SELECT l1_task_id, subtipo FROM perf_l1_tarefa WHERE l1_task_id = ANY(:ids) AND subtipo IS NOT NULL"),
                    {"ids": ids_falt},
                ).fetchall():
                    tid = int(row.l1_task_id)
                    stid = next((t.get("subTypeId") for t in raw if t.get("id") == tid), None)
                    if stid in faltantes:
                        nomes[stid] = row.subtipo
        tarefas = []
        for t in raw:
            sub = nomes.get(t.get("subTypeId")) or f"subtipo {t.get('subTypeId')}"
            # Normaliza o datetime pra BRT (o L1 mistura 'Z' e '-03:00'; ver
            # _parse_l1_dt) — a situação, a ordenação e o front dependem disso.
            dt_prazo = _parse_l1_dt(t.get("endDateTime"))
            d = dt_prazo.date() if dt_prazo else None
            if d is None:
                sit = "sem_prazo"
            elif d < hoje:
                sit = "atrasado"
            elif d == hoje:
                sit = "fatal_hoje"
            else:
                sit = "futuro"
            tarefas.append(
                {
                    "l1_task_id": t.get("id"), "subtipo": sub, "descricao": t.get("description"),
                    # prazo NORMALIZADO em BRT: strings comparáveis entre si (a
                    # ordenação do front usa comparação de string).
                    "prazo": dt_prazo.isoformat() if dt_prazo else t.get("endDateTime"),
                    "situacao": sit,
                    # "tarefa" | "compromisso" — o front distingue e a reatribuição
                    # sabe qual endpoint (/Tasks ou /Appointments) bater no PATCH.
                    "origem": t.get("_origem", "tarefa"),
                }
            )

        # Ordena por PRAZO crescente (mais antigo/vencido primeiro), sem prazo por
        # último. Cada endpoint (/Tasks, /Appointments) já vem ordenado, mas a
        # concatenação raw_t+raw_a furava a ordem GLOBAL — e a divisão consome
        # essa ordem (o front pega as primeiras N). Garante aqui a prioridade das
        # de conclusão prevista mais antiga, independentemente da origem.
        tarefas.sort(key=lambda t: (t.get("prazo") is None, t.get("prazo") or ""))

        agg = defaultdict(lambda: {"total": 0, "atrasado": 0, "fatal_hoje": 0})
        for t in tarefas:
            a = agg[t["subtipo"]]
            a["total"] += 1
            if t["situacao"] == "atrasado":
                a["atrasado"] += 1
            elif t["situacao"] == "fatal_hoje":
                a["fatal_hoje"] += 1
        subtipos = [{"subtipo": k, **v} for k, v in sorted(agg.items(), key=lambda x: -x[1]["total"])]
        return {
            "pessoa_id": pessoa_id, "nome": p.nome, "resolvido": True,
            "total_real": total_real, "carregadas": len(tarefas), "capado": capado,
            "subtipos": subtipos, "tarefas": tarefas,
        }
