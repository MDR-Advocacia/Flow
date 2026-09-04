"""POST no Legal One web com renovação automática da sessão.

⚠️ A SESSÃO DO L1 MORRE ANTES DO TTL DO CACHE. `_ensure_session()` devolve os
cookies gravados em disco enquanto o TTL não vence, mas o servidor pode ter
invalidado a sessão muito antes — e o cliente não tem como saber sem tentar.

Os dois sintomas, ambos silenciosos:
  - toda LEITURA volta HTTP **200** com a página de login (~6 KB em vez dos
    ~200 KB da página real). Foi o que gerou falsos negativos de verificação;
  - todo POST volta HTTP **403** "You do not have permission to view this
    directory or page".

Aconteceu em 04/09/2026: a correção em lote de 17 processos falhou INTEIRA às
15:38 com uma sessão de apenas 23 minutos — a etiquetagem, que tinha funcionado
às 14:24 pelo mesmo caminho, também passou a dar 403. Apagar o cache e relogar
resolveu na hora e os mesmos 17 passaram (35 pastas, zero falha).

Por isso o retry vive aqui e não em cada chamador: sem ele, o operador clica no
botão do painel e leva um 403 sem entender o motivo — o erro não diz "sua
sessão caiu", diz "você não tem permissão", que parece problema de perfil.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import requests

logger = logging.getLogger("distribuidos_bb.l1_web")

# Códigos em que vale a pena jogar fora a sessão e tentar de novo. 403 é o que o
# L1 devolve pra sessão morta; 401 entra por precaução.
_STATUS_SESSAO_MORTA = (401, 403)


# Uma página real do L1 passa de 200 KB; a de login tem ~6 KB. É por TAMANHO
# que dá pra distinguir, porque o servidor devolve as duas com HTTP 200.
_TAMANHO_MINIMO_PAGINA_REAL = 20_000


def get_l1_web(caminho: str, *, timeout: int = 60) -> Optional[str]:
    """GET no L1 web; devolve o HTML, ou None quando a página não veio.

    A sessão morta NÃO dá erro aqui: devolve HTTP 200 com a página de login. Um
    chamador que procure um nome nesse HTML não acha e conclui "não confirmado"
    — falso negativo silencioso. Por isso a checagem é por tamanho, e não pelo
    status: página curta = reloga e tenta de novo.
    """
    from app.services.prazos_iniciais.legacy_task_http_cancellation_service import (
        _SESSION_CACHE_PATH,
        LegacyTaskHttpCancellationService,
    )

    svc = LegacyTaskHttpCancellationService()
    url = f"{svc._web_base_url()}{caminho}"

    def _buscar() -> Optional[str]:
        r = svc._http.get(url, cookies=svc._ensure_session(), timeout=timeout)
        if r.status_code != 200:
            return None
        return r.text

    texto = _buscar()
    if texto is not None and len(texto) >= _TAMANHO_MINIMO_PAGINA_REAL:
        return texto

    logger.warning(
        "L1 web devolveu página curta (%s bytes) em %s — sessão provavelmente "
        "morta. Relogando pra tentar de novo.",
        len(texto or ""), caminho,
    )
    try:
        _SESSION_CACHE_PATH.unlink(missing_ok=True)
    except OSError as exc:  # noqa: BLE001
        logger.warning("Não consegui apagar o cache de sessão: %s", exc)
        return texto
    texto = _buscar()
    if texto is not None and len(texto) < _TAMANHO_MINIMO_PAGINA_REAL:
        logger.error("Página continuou curta em %s mesmo com sessão nova.", caminho)
        return None
    return texto


def post_l1_web(
    caminho: str,
    *,
    data: Any = None,
    json: Any = None,
    timeout: int = 180,
    headers: Optional[dict[str, str]] = None,
) -> requests.Response:
    """POST no L1 web; se a sessão estiver morta, reloga e repete UMA vez.

    `caminho` é relativo à base web (ex.: "/processos/Processos/ModalAlterarEmLote").
    Os dois modos do L1 são suportados: `data` (form urlencoded, usado pela
    etiquetagem em lote) e `json` (usado pela troca de envolvido).
    Devolve a resposta — quem chama decide o que fazer com o status/corpo.
    """
    from app.services.prazos_iniciais.legacy_task_http_cancellation_service import (
        _SESSION_CACHE_PATH,
        LegacyTaskHttpCancellationService,
    )

    svc = LegacyTaskHttpCancellationService()
    url = f"{svc._web_base_url()}{caminho}"
    cabecalho = {"X-Requested-With": "XMLHttpRequest", "Accept": "*/*"}
    cabecalho.update(headers or {})

    def _enviar() -> requests.Response:
        return svc._http.post(
            url, data=data, json=json, cookies=svc._ensure_session(),
            timeout=timeout, headers=cabecalho,
        )

    resposta = _enviar()
    if resposta.status_code not in _STATUS_SESSAO_MORTA:
        return resposta

    # Só o TTL não prova que a sessão vale: apaga o cache pra forçar login novo.
    # O `_ensure_session` serializa o login com filelock, então dois workers
    # nessa situação não abrem duas sessões ao mesmo tempo.
    logger.warning(
        "L1 web devolveu HTTP %s em %s — sessão provavelmente morta. "
        "Apagando o cache e relogando pra tentar de novo.",
        resposta.status_code, caminho,
    )
    try:
        _SESSION_CACHE_PATH.unlink(missing_ok=True)
    except OSError as exc:  # noqa: BLE001
        logger.warning("Não consegui apagar o cache de sessão: %s", exc)
        return resposta

    resposta = _enviar()
    if resposta.status_code in _STATUS_SESSAO_MORTA:
        logger.error(
            "L1 web continuou em HTTP %s em %s mesmo com sessão nova — "
            "aí não é sessão (perfil sem permissão, ou o endpoint mudou).",
            resposta.status_code, caminho,
        )
    else:
        logger.info("Sessão renovada; %s passou na segunda tentativa.", caminho)
    return resposta
