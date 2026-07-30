"""
Contingência de captura: manda o Legal One GERAR o relatório de publicações e
importa o arquivo.

É a automação do caminho que o operador fazia à mão. Entra em cena quando a
busca pela API (`GET /Updates`) falha — foi o que aconteceu em 30/07/2026, com
as 13 buscas do dia morrendo em HTTP 502 enquanto o **site** do L1 seguia
funcionando normalmente. Esse é o modo de falha mais comum, e é exatamente o
que essa rotina cobre.

Fluxo (levantado do HAR de uma geração real, em 30/07/2026):

    GET  /processos/GenericReport/?id=789        abre o modelo salvo
    POST /processos/GenericReport                dispara (302) e cria o relatório
    POST /shared/ReportShared/DocumentIsLoaded   polling: 7=buscando, 8=gerando, 1=PRONTO
    GET  /shared/ReportShared/GetFile/{id}       302 → blob assinado → .xlsx

O arquivo cai no mesmo importador do upload manual
(`publication_spreadsheet_import`), então dedup, detecção de obsoleta e a
"enxugada" antes da classificação valem sem nenhuma lógica nova.

## Por que o corpo do POST é um arquivo versionado, e não montado na hora

O ideal seria remontar o formulário lendo a página do modelo. Não dá: dos 917
campos do POST, **144 não existem no HTML** — são injetados por JavaScript no
submit, e entre eles estão os filtros (origem, status, tipo de andamento) e as
120 entradas de `Columns[...]` que definem as colunas, **inclusive a coluna
`Id`**, que é a que garante que a publicação entre na pasta certa.

Remontar isso na mão quebraria de um jeito que só apareceria em produção, de
madrugada, e provavelmente em silêncio — gerando um relatório sem a coluna
certa. Então o corpo vai gravado em `forms/publicacoes_report_form.txt`, com as
duas datas como placeholder, e a integridade do que volta é conferida pelo
importador (que recusa planilha sem `Id`) antes de qualquer gravação.

Se alguém editar o modelo 789 no L1, isso NÃO passa despercebido: o
`ler_planilha` recusa com motivo e a rotina devolve `ok=False`.

## Janela

Sempre D-1 → D0, por **data de cadastro do andamento** (não de publicação). É a
mesma semântica do `/Updates`: pega tudo que ENTROU no L1 na janela, não
importa quando o tribunal publicou. Por isso uma janela de 2 dias traz
publicação com data bem mais antiga — e é assim que tem que ser, senão
publicação que o tribunal solta com atraso se perde.
"""
from __future__ import annotations

import datetime
import json
import logging
import pathlib
import re
import time
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# Modelo salvo "PUBLICAÇÕES - FLOW" no Legal One.
REPORT_ID = 789
REPORT_TITULO = "PUBLICAÇÕES - FLOW"

# NÃO mover pra uma pasta chamada `data/`: o .gitignore tem `data/` e o
# arquivo some do commit em silêncio — a contingência iria pra produção
# quebrando com FileNotFoundError na primeira madrugada.
_FORM_PATH = pathlib.Path(__file__).with_name("forms") / "publicacoes_report_form.txt"

# Status devolvidos pelo DocumentIsLoaded. Observados numa geração real:
#   7 = "Buscando dados"  |  8 = "Gerando arquivo"  |  1 = pronto
STATUS_PRONTO = 1
STATUS_TRABALHANDO = (7, 8)

# Uma janela de 2 dias gera ~1.200 linhas e ficou pronta em segundos. 15 min é
# folga larga pra um dia atipico, sem deixar o job da madrugada pendurado.
TIMEOUT_GERACAO_S = 900
INTERVALO_POLL_S = 10

_GETFILE_RE = re.compile(r"GetFile/(\d+)")

try:
    from zoneinfo import ZoneInfo

    _BRT = ZoneInfo("America/Sao_Paulo")
except Exception:  # pragma: no cover
    _BRT = None


def _agora() -> datetime.datetime:
    return datetime.datetime.now(tz=_BRT) if _BRT else datetime.datetime.now()


def janela_padrao(dias_atras: int = 1) -> tuple[str, str]:
    """D-1 → D0 em dd/mm/aaaa, no fuso de Brasília."""
    hoje = _agora().date()
    inicio = hoje - datetime.timedelta(days=dias_atras)
    return inicio.strftime("%d/%m/%Y"), hoje.strftime("%d/%m/%Y")


def _session() -> requests.Session:
    """Sessão autenticada reusando o login `.ASPXAUTH` já existente (filelock/TTL).

    Mesma porta de entrada do ingest do Minha Equipe — não abre login novo.
    """
    from app.services.prazos_iniciais.legacy_task_http_cancellation_service import (
        LegacyTaskHttpCancellationService,
    )

    cookies = LegacyTaskHttpCancellationService()._ensure_session()
    s = requests.Session()
    for k, v in cookies.items():
        s.cookies.set(k, v)
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    return s


def corpo_do_formulario(data_inicio: str, data_fim: str) -> str:
    """Corpo do POST com as duas datas substituídas."""
    if not _FORM_PATH.exists():
        raise FileNotFoundError(
            f"Formulário do relatório não encontrado em {_FORM_PATH}. "
            "Sem ele não é possível disparar a geração no Legal One."
        )
    corpo = _FORM_PATH.read_text(encoding="utf-8")
    for marca in ("__DATA_INICIO__", "__DATA_FIM__"):
        if marca not in corpo:
            raise ValueError(
                f"O formulário gravado perdeu o placeholder {marca} — "
                "não dá pra garantir a janela de datas."
            )
    from urllib.parse import quote_plus

    return (
        corpo.replace("__DATA_INICIO__", quote_plus(data_inicio))
        .replace("__DATA_FIM__", quote_plus(data_fim))
    )


def _ids_existentes(session: requests.Session, base: str) -> set[int]:
    """IDs de relatório já presentes na listagem — pra isolar o que acabamos de criar."""
    try:
        html = session.get(
            f"{base}/processos/ReportProcessos/Search", timeout=120
        ).text
        return {int(x) for x in _GETFILE_RE.findall(html)}
    except (requests.RequestException, ValueError):
        logger.warning("Não foi possível listar relatórios antes do disparo.", exc_info=True)
        return set()


def disparar(session: requests.Session, base: str, data_inicio: str, data_fim: str) -> Optional[int]:
    """Dispara a geração e devolve o id do relatório criado."""
    antes = _ids_existentes(session, base)

    # Abre o modelo antes de postar: além de ser o que o browser faz, valida
    # que o modelo 789 ainda existe e que a sessão está de pé.
    r = session.get(f"{base}/processos/GenericReport/?id={REPORT_ID}", timeout=120)
    r.raise_for_status()

    resp = session.post(
        f"{base}/processos/GenericReport",
        data=corpo_do_formulario(data_inicio, data_fim),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=300,
        allow_redirects=True,
    )
    resp.raise_for_status()

    depois = _ids_existentes(session, base)
    novos = depois - antes
    if novos:
        return max(novos)

    # Sem id novo na listagem: pode ser atraso de indexação. Tenta de novo,
    # dando um respiro, antes de desistir.
    time.sleep(INTERVALO_POLL_S)
    novos = _ids_existentes(session, base) - antes
    if novos:
        return max(novos)

    logger.error(
        "Relatório disparado mas nenhum id novo apareceu na listagem "
        "(antes=%s ids, depois=%s ids).", len(antes), len(depois),
    )
    return None


def aguardar_ficar_pronto(
    session: requests.Session,
    base: str,
    report_id: int,
    *,
    timeout: int = TIMEOUT_GERACAO_S,
) -> dict[str, Any]:
    """Faz polling até o relatório ficar pronto (Status 1) ou estourar o tempo."""
    limite = time.monotonic() + timeout
    ultimo: dict[str, Any] = {}
    while time.monotonic() < limite:
        try:
            r = session.post(
                f"{base}/shared/ReportShared/DocumentIsLoaded",
                data={"reportIds[]": str(report_id)},
                timeout=60,
            )
            r.raise_for_status()
            dados = r.json()
        except (requests.RequestException, ValueError, json.JSONDecodeError):
            logger.warning("Polling do relatório %s falhou; segue tentando.", report_id, exc_info=True)
            time.sleep(INTERVALO_POLL_S)
            continue

        item = next((d for d in dados if str(d.get("Id")) == str(report_id)), None)
        if item is None:
            time.sleep(INTERVALO_POLL_S)
            continue
        ultimo = item
        status = item.get("Status")
        if status == STATUS_PRONTO:
            return {"ok": True, "status": status}
        if status not in STATUS_TRABALHANDO:
            # Status desconhecido: registra e continua — a lista de status de
            # trabalho foi levantada de UMA geração, pode não ser exaustiva.
            logger.info(
                "Relatório %s em status inesperado %s (%s) — seguindo o polling.",
                report_id, status, item.get("ErrorMessage"),
            )
        time.sleep(INTERVALO_POLL_S)

    return {
        "ok": False,
        "motivo": "timeout_geracao",
        "ultimo_status": ultimo.get("Status"),
        "detalhe": ultimo.get("ErrorMessage"),
    }


def baixar(session: requests.Session, base: str, report_id: int) -> bytes:
    """Baixa o .xlsx (o GetFile responde 302 pro blob assinado)."""
    r = session.get(
        f"{base}/shared/ReportShared/GetFile/{report_id}",
        timeout=600,
        allow_redirects=True,
    )
    r.raise_for_status()
    return r.content


def gerar_e_baixar(
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    *,
    timeout: int = TIMEOUT_GERACAO_S,
) -> dict[str, Any]:
    """Dispara, espera e baixa. Devolve os bytes do .xlsx."""
    from app.services.prazos_iniciais.legacy_task_helpers import web_base_url

    if not data_inicio or not data_fim:
        data_inicio, data_fim = janela_padrao()

    base = web_base_url()
    session = _session()

    inicio_em = time.monotonic()
    report_id = disparar(session, base, data_inicio, data_fim)
    if not report_id:
        return {"ok": False, "motivo": "relatorio_nao_criado"}

    logger.info(
        "Relatório de publicações #%s disparado (janela %s a %s).",
        report_id, data_inicio, data_fim,
    )

    pronto = aguardar_ficar_pronto(session, base, report_id, timeout=timeout)
    if not pronto.get("ok"):
        return {**pronto, "report_id": report_id}

    conteudo = baixar(session, base, report_id)
    if not conteudo:
        return {"ok": False, "motivo": "download_vazio", "report_id": report_id}

    return {
        "ok": True,
        "report_id": report_id,
        "bytes": conteudo,
        "tamanho": len(conteudo),
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "segundos": round(time.monotonic() - inicio_em, 1),
    }


def capturar_publicacoes(
    db,
    *,
    dias_atras: int = 1,
    timeout: int = TIMEOUT_GERACAO_S,
) -> dict[str, Any]:
    """Ponta a ponta: gera o relatório no L1 e devolve publicações prontas.

    A lista devolvida em `publicacoes` está no contrato do L1 e pode ir direto
    pro `create_and_run_search(prefetched_publications=...)` — igual à lista que
    a API devolveria. Quem chama não precisa saber que veio de planilha.
    """
    from app.services.publication_spreadsheet_import import (
        ler_planilha,
        montar_publicacoes,
    )

    data_inicio, data_fim = janela_padrao(dias_atras)
    resultado = gerar_e_baixar(data_inicio, data_fim, timeout=timeout)
    if not resultado.get("ok"):
        return resultado

    try:
        lido = ler_planilha(resultado["bytes"], db)
    except ValueError as exc:
        # Cai aqui se o modelo 789 foi editado e perdeu a coluna `Id`, ou se o
        # arquivo veio corrompido. Não importa nada — melhor não capturar do
        # que capturar na pasta errada.
        logger.error(
            "Relatório #%s baixado mas inaproveitável: %s",
            resultado.get("report_id"), exc,
        )
        return {
            "ok": False,
            "motivo": "planilha_invalida",
            "detalhe": str(exc),
            "report_id": resultado.get("report_id"),
        }

    publicacoes = montar_publicacoes(lido["validas"])
    logger.info(
        "Contingência por relatório: %s publicações de %s processos "
        "(janela %s a %s, relatório #%s, %ss).",
        len(publicacoes), lido["processos_distintos"],
        data_inicio, data_fim, resultado.get("report_id"), resultado.get("segundos"),
    )
    return {
        "ok": True,
        "publicacoes": publicacoes,
        "report_id": resultado.get("report_id"),
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "total": lido["total_validas"],
        "ignoradas": lido["total_ignoradas"],
        "processos": lido["processos_distintos"],
        "segundos": resultado.get("segundos"),
    }
