"""Ingestão diária do Minha Equipe via DOWNLOAD do relatório "Agenda Analytics".

O L1 gera esse relatório (denormalizado — mesmo formato do seed) todo dia de
manhã. Em vez de remontar via API, baixamos o arquivo pronto e ingerimos:

  sessão web (reuso do login .ASPXAUTH, filelock) →
  GET /agenda/reportagenda/Search → acha o "Agenda Analytics" mais recente →
  GET /shared/ReportShared/GetFile/{id} → parser do seed (replace) + classify.

O roster (perf_pessoa) NÃO é tocado — só as tarefas. Validado 2026-06-26:
relatório do dia = ~365k linhas, header idêntico ao parser.
"""

import datetime
import json
import logging
import re

import requests

logger = logging.getLogger(__name__)

_REPORT_PATH = "/tmp/perf_agenda_latest.xlsx"
_TITULO = "Agenda Analytics"
# Busca AVANÇADA filtrada por título: o L1 devolve só os "Agenda Analytics"
# (um por dia), em vez da lista geral. Sem o filtro, relatórios ad-hoc gerados
# pelos operadores durante o dia empurram o do dia pra fora da 1ª página e o
# sync falha com "relatorio_nao_encontrado" (já aconteceu em prod 2026-06-26).
_LIST_URL = (
    "/agenda/reportagenda/Search?Titulo=Agenda+Analytics"
    "&ShowAdvancedFilters=True&IsSearchExecutedByUser=true"
)
SETTING_LAST_SYNC = "perf_minha_equipe_last_sync"
# Estado da atualização EM ANDAMENTO (pro banner/barra global + trava de clique).
SETTING_SYNC_RUNNING = "perf_minha_equipe_sync_running"

# Cada linha tem 2 links GetFile (o do nome + o "Download") com o MESMO id; a
# data de geração é a coluna "Data" (1ª dd/mm/aaaa da linha).
_ROW_TR = re.compile(r"<tr\b.*?</tr>", re.DOTALL)
_GETFILE_RE = re.compile(r"GetFile/(\d+)[^>]*>([^<]+)<")
_DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}")

try:
    from zoneinfo import ZoneInfo

    _BRT = ZoneInfo("America/Sao_Paulo")
except Exception:  # pragma: no cover
    _BRT = None


def _now():
    return datetime.datetime.now(tz=_BRT) if _BRT else datetime.datetime.now()


def _hoje_str() -> str:
    return _now().strftime("%d/%m/%Y")


def _session() -> requests.Session:
    """Sessão HTTP autenticada reusando o login .ASPXAUTH existente (filelock/TTL)."""
    from app.services.prazos_iniciais.legacy_task_http_cancellation_service import (
        LegacyTaskHttpCancellationService,
    )

    cookies = LegacyTaskHttpCancellationService()._ensure_session()
    s = requests.Session()
    for k, v in cookies.items():
        s.cookies.set(k, v)
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    return s


def _parse_data(s: str):
    try:
        return datetime.datetime.strptime(s, "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return None


def _find_latest(session: requests.Session, base: str):
    """Acha o 'Agenda Analytics' de MAIOR data de geração na lista já filtrada
    por título. Não confia na ordem/paginação: percorre todas as linhas e fica
    com a de data mais recente (empate → a 1ª, que o L1 mostra como mais nova)."""
    html = session.get(base + _LIST_URL, timeout=60).text
    best = None
    for tr in _ROW_TR.findall(html):
        gid = name = None
        for i, txt in _GETFILE_RE.findall(tr):
            if txt.strip().lower() != "download":
                gid, name = i, txt.strip()
                break
        if not gid or _TITULO not in name:
            continue
        md = _DATE_RE.search(tr)
        d = _parse_data(md.group(0)) if md else None
        if d is None:
            continue
        if best is None or d > best["_d"]:
            best = {"id": gid, "title": name, "data": md.group(0), "_d": d}
    if not best:
        return None
    best.pop("_d", None)
    return best


def _reaplicar_reatribuicoes(db) -> None:
    """Reaplica sobre o snapshot RECÉM-INGERIDO os espelhos das reatribuições
    reais recentes do Balanceador.

    O relatório do L1 pode ter sido GERADO horas antes do rebalanceamento —
    o replace total do seed voltava o painel aos valores antigos (caso real
    22/07: operador redistribuiu, painel refletiu na hora, 'Atualizar agora'
    baixou o relatório da manhã e desfez a visão). Reaplicar é idempotente:
    se o relatório novo já reflete a troca, o UPDATE não muda nada.

    Janela de 48h > idade máxima de um relatório aproveitável; jobs em ordem
    cronológica (o mais recente vence em movimentos conflitantes). Best-effort.
    """
    try:
        import datetime as _dt2

        from app.models.performance import BalanceadorReatribuirJob
        from app.services.performance.reatribuir_job import _espelhar_snapshot

        cutoff = _dt2.datetime.now(_dt2.timezone.utc) - _dt2.timedelta(hours=48)
        jobs = (
            db.query(BalanceadorReatribuirJob)
            .filter(
                BalanceadorReatribuirJob.dry_run.is_(False),
                BalanceadorReatribuirJob.status == "done",
                BalanceadorReatribuirJob.terminado_em >= cutoff,
            )
            .order_by(BalanceadorReatribuirJob.terminado_em.asc())
            .all()
        )
        n = 0
        for j in jobs:
            if j.detalhe:
                _espelhar_snapshot(db, list(j.detalhe))
                n += 1
        if n:
            logger.info(
                "Minha Equipe ingest: %s job(s) de reatribuição (48h) reaplicados sobre o snapshot novo.", n,
            )
    except Exception:  # noqa: BLE001
        logger.exception("Ingest: falha ao reaplicar reatribuições (segue com o snapshot cru).")


def baixar_e_ingerir(db, *, force: bool = False) -> dict:
    """Baixa o relatório do dia e ingere. force=True ingere o mais recente mesmo
    que não seja de hoje (ex.: botão "Atualizar agora")."""
    from app.services.prazos_iniciais.legacy_task_helpers import web_base_url

    base = web_base_url()
    session = _session()
    rel = _find_latest(session, base)
    if not rel:
        return {"ok": False, "motivo": "relatorio_nao_encontrado"}

    hoje = _hoje_str()
    if rel["data"] != hoje and not force:
        return {
            "ok": False,
            "motivo": "relatorio_do_dia_ainda_nao_gerado",
            "ultima_data": rel["data"],
            "hoje": hoje,
        }

    resp = session.get(f"{base}/shared/ReportShared/GetFile/{rel['id']}", timeout=300)
    resp.raise_for_status()
    with open(_REPORT_PATH, "wb") as f:
        f.write(resp.content)

    # Parser do seed (replace total) + classify, mantendo o roster intacto.
    from app.models.performance import PerfPessoa
    from app.services.performance.seed import classify_subtipos, seed_tarefas

    name_to_id = {p.nome_norm: p.id for p in db.query(PerfPessoa).all()}
    n = seed_tarefas(db, name_to_id, agenda_path=_REPORT_PATH)
    classify_subtipos(db)
    _reaplicar_reatribuicoes(db)

    info = {
        "ok": True,
        "tarefas": n,
        "relatorio": rel["title"],
        "data": rel["data"],
        "bytes": len(resp.content),
        "em": _now().isoformat(),
    }
    _set_last_sync(info)
    logger.info("Minha Equipe ingest: %s tarefas do relatório '%s' (%s).", n, rel["title"], rel["data"])
    return info


def _set_last_sync(info: dict) -> None:
    from app.services.app_settings import set_setting

    set_setting(SETTING_LAST_SYNC, json.dumps(info, ensure_ascii=False))


def get_last_sync():
    from app.services.app_settings import get_setting

    raw = get_setting(SETTING_LAST_SYNC, default=None)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def ja_sincronizou_hoje() -> bool:
    ls = get_last_sync()
    return bool(ls and ls.get("ok") and ls.get("data") == _hoje_str())


# ── Estado da atualização em andamento (banner/barra global + trava) ──
# Persistido em app_settings pra qualquer worker do uvicorn (e qualquer aba/
# equipe) enxergar a mesma verdade. Guard de segurança: um run "preso" há mais
# de 20min é considerado morto (o container pode ter reiniciado no meio sem
# limpar), então o próximo clique não fica bloqueado pra sempre.
_SYNC_STALE_SEG = 20 * 60


def marcar_sync_rodando(por_nome: str | None, fase: str = "gerando") -> None:
    from app.services.app_settings import set_setting

    set_setting(SETTING_SYNC_RUNNING, json.dumps(
        {"running": True, "iniciado_em": _now().isoformat(), "por": por_nome, "fase": fase},
        ensure_ascii=False,
    ))


def atualizar_fase_sync(fase: str) -> None:
    st = get_sync_running()
    if not st:
        return
    st["fase"] = fase
    from app.services.app_settings import set_setting

    set_setting(SETTING_SYNC_RUNNING, json.dumps(st, ensure_ascii=False))


def limpar_sync_rodando() -> None:
    from app.services.app_settings import set_setting

    set_setting(SETTING_SYNC_RUNNING, json.dumps({"running": False}, ensure_ascii=False))


def get_sync_running():
    """Retorna o estado de execução, ou None se ocioso. Zera automaticamente um
    run preso (> _SYNC_STALE_SEG) pra não travar o botão pra sempre."""
    from app.services.app_settings import get_setting

    raw = get_setting(SETTING_SYNC_RUNNING, default=None)
    if not raw:
        return None
    try:
        st = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not st.get("running"):
        return None
    try:
        ini = datetime.datetime.fromisoformat(st["iniciado_em"])
        if (_now() - ini).total_seconds() > _SYNC_STALE_SEG:
            limpar_sync_rodando()
            return None
    except (ValueError, TypeError, KeyError):
        pass
    return st


# ── Geração SOB DEMANDA do relatório (via runner Playwright) ──
# O POST headless NÃO conclui (a página "Gerando" depende do SignalR do browser).
# VALIDADO 2026-06-30: clicar "Gerar" num browser real (runner generate-report.js)
# dispara um job SERVER-SIDE que completa sozinho (~45s) mesmo após o browser
# fechar. Modelo "Agenda Analytics" = id 627 no L1.
AGENDA_ANALYTICS_MODEL_ID = 627


def _report_ids(session, base) -> list:
    """GetFile ids dos 'Agenda Analytics' na lista (filtrada por título)."""
    html = session.get(base + _LIST_URL, timeout=60).text
    return sorted(set(int(x) for x in re.findall(r"GetFile/(\d+)", html)))


def disparar_geracao(model_id: int = AGENDA_ANALYTICS_MODEL_ID, timeout_s: int = 240) -> bool:
    """Dispara a geração clicando 'Gerar' num browser real (runner Playwright). O
    job roda server-side e completa sozinho após o browser fechar — o caller faz
    o poll/download. True se o runner confirmou o disparo."""
    import os
    import subprocess

    from app.services.prazos_iniciais.legacy_task_helpers import (
        resolve_node_binary,
        resolve_runner_script,
        resolve_web_credentials,
    )

    script = resolve_runner_script().parent / "generate-report.js"
    if not script.exists():
        logger.error("Minha Equipe: runner generate-report.js não encontrado em %s.", script)
        return False
    cmd = [resolve_node_binary(), str(script), "--id", str(model_id), "--timeout-min", "3"]
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(script.parent),
            env={**os.environ, **resolve_web_credentials()},
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Minha Equipe: runner de geração estourou o timeout (%ss).", timeout_s)
        return False
    ok = completed.returncode == 0
    logger.info(
        "Minha Equipe: disparo de geração (runner) modelo %s -> %s (exit %s) | %s",
        model_id, "OK" if ok else "FALHOU", completed.returncode, (completed.stdout or "").strip()[-300:],
    )
    return ok


def gerar_e_ingerir(
    db,
    model_id: int = AGENDA_ANALYTICS_MODEL_ID,
    espera_max_s: int = 1500,
    intervalo_s: int = 30,
) -> dict:
    """Dispara a geração de um relatório FRESCO, espera ficar pronto e ingere.
    Pras rotinas (madrugada/meio-dia) e pro 'Atualizar pool agora' — não depende
    da geração matinal do L1."""
    import time as _t

    from app.services.prazos_iniciais.legacy_task_helpers import web_base_url

    base = web_base_url()
    baseline = max(_report_ids(_session(), base) or [0])
    if not disparar_geracao(model_id):
        return {"ok": False, "motivo": "disparo_falhou"}

    # O login do runner ROTACIONA o cookie .ASPXAUTH (o L1 troca a sessão a cada
    # login) → a sessão anterior fica inválida. Invalida o cache e reloga pra ter
    # um cookie VÁLIDO pro poll/download (senão o poll nunca acha o relatório).
    from app.services.prazos_iniciais.legacy_task_http_cancellation_service import (
        LegacyTaskHttpCancellationService,
    )

    LegacyTaskHttpCancellationService()._invalidate_session()
    session = _session()

    novo = None
    esperou = 0
    while esperou < espera_max_s:
        _t.sleep(intervalo_s)
        esperou += intervalo_s
        try:
            maiores = [i for i in _report_ids(session, base) if i > baseline]
        except Exception:  # noqa: BLE001
            continue
        if maiores:
            novo = max(maiores)
            break
    if not novo:
        logger.warning("Minha Equipe gerar_e_ingerir: timeout (%ss) esperando o relatório.", esperou)
        return {"ok": False, "motivo": "timeout_geracao", "esperou_s": esperou}

    resp = session.get(f"{base}/shared/ReportShared/GetFile/{novo}", timeout=300)
    resp.raise_for_status()
    with open(_REPORT_PATH, "wb") as f:
        f.write(resp.content)

    from app.models.performance import PerfPessoa
    from app.services.performance.seed import classify_subtipos, seed_tarefas

    name_to_id = {p.nome_norm: p.id for p in db.query(PerfPessoa).all()}
    n = seed_tarefas(db, name_to_id, agenda_path=_REPORT_PATH)
    classify_subtipos(db)
    _reaplicar_reatribuicoes(db)
    info = {
        "ok": True,
        "tarefas": n,
        "relatorio": str(novo),
        "data": _hoje_str(),
        "bytes": len(resp.content),
        "em": _now().isoformat(),
        "gerado_sob_demanda": True,
    }
    _set_last_sync(info)
    logger.info(
        "Minha Equipe gerar_e_ingerir: %s tarefas (report %s, gerado sob demanda em ~%ss).",
        n, novo, esperou,
    )
    return info
