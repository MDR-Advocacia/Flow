"""Diagnóstico do OneLog disparado quando a coleta do BB não traz nada.

## Por que existe

Coleta que volta ZERADA é ambígua, e essa ambiguidade já custou caro: em
07→10/08/2026 o cadastro do BB ficou três dias parado porque a busca voltava
vazia e ninguém sabia distinguir "não havia processo novo" de "quebrou". Em
12/08 aconteceu de novo por outro motivo (o OneLog travado na renovação de
segurança do BB), e o operador só descobriu investigando na mão.

Então toda coleta que termina com **0 processos** ou **em erro** dispara este
diagnóstico, que responde a pergunta certa: *o zero é legítimo ou tem coisa
quebrada?*

## Por que o teto é curto

O `obter_sessao` da coleta espera até 15 minutos, porque lá o objetivo é
CONSEGUIR a sessão. Aqui o objetivo é outro: descobrir depressa se o login está
travado. Esperar 15 minutos de novo penduraria a coleta — inclusive nas noites
em que zero é o resultado correto. Com teto curto, o veredito sai em menos de
dois minutos e diz o mesmo: se o login não passa em 90s, ele está travado.

## Por que nem todo diagnóstico vira e-mail

Zero legítimo é rotina (a passagem das 03:00 quase nunca traz processo). Se
todo zero virasse e-mail, o alerta viraria ruído e as pessoas parariam de ler —
que é exatamente como uma falha real passa despercebida. Só há e-mail quando o
diagnóstico ENCONTRA problema.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger("distribuidos_bb.diagnostico")

# Teto curto do login no diagnóstico — ver o docstring do módulo.
TIMEOUT_DIAGNOSTICO_SEGUNDOS = 90

# Veredito
OK = "OK"
PROBLEMA = "PROBLEMA"
INDEFINIDO = "INDEFINIDO"


def diagnosticar(*, timeout_segundos: int = TIMEOUT_DIAGNOSTICO_SEGUNDOS) -> dict[str, Any]:
    """Roda o teste do OneLog e devolve um veredito legível.

    Nunca levanta: um diagnóstico que quebra não pode derrubar a coleta que já
    terminou. Devolve dict com `veredito`, `resumo`, `detalhes`.
    """
    detalhes: dict[str, Any] = {}
    try:
        from app.services.distribuidos_bb.onelog_client import OneLogClient, OneLogError

        cliente = OneLogClient()
        detalhes["api_url"] = cliente.api_url
        detalhes["usuario"] = cliente.username

        if not cliente.configurado:
            return {
                "veredito": PROBLEMA,
                "resumo": (
                    "Credenciais do OneLog não estão configuradas "
                    "(DISTRIBUIDOS_BB_ONELOG_USERNAME/PASSWORD)."
                ),
                "detalhes": detalhes,
            }

        t0 = time.time()
        try:
            sessao = cliente.obter_sessao(timeout_segundos=timeout_segundos)
        except OneLogError as exc:
            # O texto do erro já carrega a mensagem do OneLog (ex.: "Aguardando
            # renovação de segurança...") — é ela que diz de quem é o problema.
            detalhes["segundos"] = round(time.time() - t0, 1)
            detalhes["mensagem_onelog"] = cliente.ultima_mensagem
            return {
                "veredito": PROBLEMA,
                "resumo": f"O login no OneLog não passou: {exc}",
                "detalhes": detalhes,
            }

        cookies = sessao.get("cookies") or []
        detalhes["segundos"] = round(time.time() - t0, 1)
        detalhes["cookies"] = len(cookies)
        detalhes["sessao_em_cache"] = cliente.sessao_em_cache
        detalhes["mensagem_onelog"] = cliente.ultima_mensagem

        if not cookies:
            return {
                "veredito": PROBLEMA,
                "resumo": "O OneLog respondeu, mas não devolveu cookies de sessão.",
                "detalhes": detalhes,
            }

        origem = "sessão que já estava ativa" if cliente.sessao_em_cache else "login feito do zero"
        return {
            "veredito": OK,
            "resumo": (
                f"OneLog respondeu normalmente ({origem}, {len(cookies)} cookie[s] "
                f"em {detalhes['segundos']}s). Nada aponta problema de sessão."
            ),
            "detalhes": detalhes,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Diagnóstico do OneLog falhou (ignorado).")
        return {
            "veredito": INDEFINIDO,
            "resumo": f"O diagnóstico não pôde ser concluído: {exc}",
            "detalhes": detalhes,
        }


def diagnosticar_e_registrar(
    db,
    *,
    run_id: Optional[int],
    motivo: str,
    alertar_sempre: bool = False,
) -> dict[str, Any]:
    """Diagnostica, grava o evento no Log de tudo e alerta quando há problema.

    `motivo`: por que o diagnóstico rodou ("coleta voltou zerada", "coleta
    falhou") — entra no evento e no e-mail.
    `alertar_sempre`: usado quando a coleta já falhou (aí o e-mail sai de
    qualquer forma e o diagnóstico entra como contexto).
    """
    from app.models.distribuidos_bb import (
        NIVEL_AVISO,
        NIVEL_ERRO,
        NIVEL_INFO,
        SECAO_SESSAO,
    )
    from app.services.distribuidos_bb.log_service import registrar_evento

    res = diagnosticar()
    veredito = res["veredito"]
    nivel = (
        NIVEL_INFO if veredito == OK
        else NIVEL_ERRO if veredito == PROBLEMA
        else NIVEL_AVISO
    )
    try:
        registrar_evento(
            db, secao=SECAO_SESSAO, nivel=nivel,
            acao=f"Diagnóstico do OneLog ({veredito})",
            mensagem=f"Rodado porque {motivo}. {res['resumo']}",
            dados=res.get("detalhes"), run_id=run_id, commit=True,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Não consegui registrar o evento do diagnóstico.")

    if veredito == PROBLEMA or alertar_sempre:
        try:
            from app.services.distribuidos_bb.alertas import alertar_falha_cadastro

            alertar_falha_cadastro(
                contexto=f"diagnóstico do OneLog — {motivo}",
                erro=res["resumo"],
                run_id=run_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Não consegui alertar sobre o diagnóstico.")

    return res
