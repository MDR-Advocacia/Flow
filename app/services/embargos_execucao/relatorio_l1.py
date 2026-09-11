"""Relatório "ROBÔ - EMBARGOS À EXECUÇÃO" (modelo 799 do L1): gerar, baixar, importar.

Fluxo validado no HAR de 11/09/2026: GET /agenda/GenericReport/?id=799 → clique
"Gerar" (POST do form, 302) → /agenda/ReportAgenda/Search com a linha do novo
relatório em "Buscando dados" → o front consulta
POST /shared/ReportShared/DocumentIsLoaded (reportIds[]=<id>) até sair o
GetFile. Status 0/6/7/8 = gerando; 4 com "Não possui dados" = relatório vazio
(foi o que aconteceu no primeiro teste, com o filtro de executantes antigo).

O POST puro não conclui a geração (depende do SignalR do navegador) — o clique
é do runner `generate-report.js`, o mesmo do Minha Equipe. Aqui fica o poll, o
download e a importação (com a data de corte: só caso novo entra).
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.models.embargos_execucao import EVT_ERRO, ORIGEM_RELATORIO, SECAO_ENTRADA
from app.services.app_settings import get_setting, set_setting
from app.services.embargos_execucao import importacao, service
from app.services.embargos_execucao.normaliza import nome_normalizado

logger = logging.getLogger(__name__)

SETTING_STATUS = "embargos_execucao_relatorio_status"
_STATUS_GERANDO = {0, 6, 7, 8}
_STATUS_SEM_DADOS = 4
_ROW = re.compile(r"<tr\b.*?</tr>", re.S)
_TITULO_LINHA = re.compile(r'id="report_title_(\d+)"\s*>\s*([^<]+?)\s*<', re.S)
_DATA = re.compile(r"\d{2}/\d{2}/\d{4}")
_STALE_MIN = 40


def _url_lista(titulo: str) -> str:
    return "/agenda/reportagenda/Search?" + urlencode({
        "Titulo": titulo, "ShowAdvancedFilters": "True", "IsSearchExecutedByUser": "true",
    })


def relatorios_na_lista(pagina: str, titulo: str) -> list[dict[str, Any]]:
    """Linhas da lista de relatórios gerados cujo título bate (sem acento/caixa)."""
    alvo = nome_normalizado(titulo)
    saida = []
    for tr in _ROW.findall(pagina or ""):
        m = _TITULO_LINHA.search(tr)
        if not m or nome_normalizado(html_lib.unescape(m.group(2))) != alvo:
            continue
        rid = int(m.group(1))
        data = _DATA.search(tr)
        saida.append({
            "id": rid,
            "pronto": f"GetFile/{rid}" in tr,
            "gerando": f'id="gerando_{rid}"' in tr,
            "data": data.group(0) if data else None,
        })
    return saida


def _status(fase: Optional[str], *, running: bool, ultimo: Optional[dict] = None) -> None:
    atual = status_relatorio()
    novo = {
        "running": running,
        "fase": fase,
        "iniciado_em": atual.get("iniciado_em") if running and atual.get("running") else service.agora().isoformat(),
        "ultimo": ultimo if ultimo is not None else atual.get("ultimo"),
    }
    if not running:
        novo["iniciado_em"] = None
    set_setting(SETTING_STATUS, json.dumps(novo, ensure_ascii=False))


def status_relatorio() -> dict[str, Any]:
    bruto = get_setting(SETTING_STATUS, "") or ""
    try:
        st = json.loads(bruto) if bruto else {}
    except ValueError:
        st = {}
    if st.get("running") and st.get("iniciado_em"):
        try:
            ini = datetime.fromisoformat(st["iniciado_em"])
            if (service.agora() - ini).total_seconds() > _STALE_MIN * 60:
                st["running"] = False
                st["fase"] = "interrompido (sem sinal de vida)"
        except ValueError:
            pass
    return st


def em_andamento() -> bool:
    return bool(status_relatorio().get("running"))


def _documento(session, base: str, report_id: int) -> Optional[dict[str, Any]]:
    try:
        r = session.post(f"{base}/shared/ReportShared/DocumentIsLoaded",
                         data={"reportIds[]": report_id}, timeout=60)
        corpo = r.json()
        return corpo[0] if isinstance(corpo, list) and corpo else None
    except Exception:  # noqa: BLE001
        return None


def gerar_e_importar(
    db: Session,
    *,
    espera_max_s: int = 900,
    intervalo_s: int = 20,
    user_id: Optional[int] = None,
    gerar: bool = True,
) -> dict[str, Any]:
    from app.services.performance import report_ingest as ri
    from app.services.prazos_iniciais.legacy_task_helpers import web_base_url
    from app.services.prazos_iniciais.legacy_task_http_cancellation_service import (
        LegacyTaskHttpCancellationService,
    )

    titulo, modelo = service.relatorio_titulo(), service.relatorio_modelo_id()
    base = web_base_url()
    url = base + _url_lista(titulo)
    _status("lendo a lista de relatórios do L1", running=True)
    resultado: dict[str, Any] = {"ok": False, "modelo": modelo, "titulo": titulo}
    try:
        session = ri._session()
        existentes = relatorios_na_lista(session.get(url, timeout=60).text, titulo)
        baseline = max([r["id"] for r in existentes] or [0])

        if gerar:
            _status("gerando o relatório no L1", running=True)
            if not ri.disparar_geracao(modelo):
                resultado["motivo"] = "disparo_falhou"
                raise RuntimeError("O runner não conseguiu clicar em 'Gerar' no L1.")
            # O login do runner troca o cookie da sessão web: reloga pro poll.
            LegacyTaskHttpCancellationService()._invalidate_session()
            session = ri._session()

        alvo = None
        esperou = 0
        while True:
            linhas = relatorios_na_lista(session.get(url, timeout=60).text, titulo)
            candidatos = [l for l in linhas if (l["id"] > baseline or not gerar)]
            if candidatos:
                alvo = max(candidatos, key=lambda l: l["id"])
                if alvo["pronto"]:
                    break
                doc = _documento(session, base, alvo["id"])
                if doc and doc.get("Status") == _STATUS_SEM_DADOS:
                    resultado.update({"ok": True, "report_id": alvo["id"], "sem_dados": True,
                                      "mensagem": doc.get("ErrorMessage") or "Não possui dados"})
                    _status(None, running=False, ultimo={**resultado, "em": service.agora().isoformat()})
                    return resultado
                if doc and doc.get("Status") not in _STATUS_GERANDO and doc.get("ErrorMessage"):
                    raise RuntimeError(f"L1 recusou o relatório {alvo['id']}: {doc.get('ErrorMessage')}")
            if esperou >= espera_max_s:
                resultado["motivo"] = "timeout_geracao"
                raise RuntimeError(f"O relatório não ficou pronto em {espera_max_s // 60} min.")
            _status(f"aguardando o L1 terminar o relatório ({esperou}s)", running=True)
            time.sleep(intervalo_s)
            esperou += intervalo_s

        _status("baixando e importando", running=True)
        resp = session.get(f"{base}/shared/ReportShared/GetFile/{alvo['id']}", timeout=300)
        resp.raise_for_status()
        importado = importacao.importar_bytes(
            db, resp.content, f"relatorio-{alvo['id']}.xlsx", origem=ORIGEM_RELATORIO, user_id=user_id,
        )
        resultado.update({"ok": True, "report_id": alvo["id"], "bytes": len(resp.content), **importado})
        _status(None, running=False, ultimo={**resultado, "em": service.agora().isoformat()})
        return resultado
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        resultado["erro"] = str(exc)[:500]
        logger.exception("Embargos: falha no relatório do L1.")
        try:
            service.registrar_evento(db, SECAO_ENTRADA, f"Falha ao gerar/importar o relatório do L1: {exc}",
                                     nivel=EVT_ERRO, user_id=user_id, dados=resultado)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        _status(None, running=False, ultimo={**resultado, "em": service.agora().isoformat()})
        return resultado
