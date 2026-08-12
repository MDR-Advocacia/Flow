"""Alerta por e-mail da captura de publicações.

Mesmo mecanismo dos outros alertas da casa (Distribuídos BB, classificador em
lote): reusa o sender SMTP de `mail_service`, destinatários em
`settings.publication_alert_email` (env `PUBLICATION_ALERT_EMAIL`), com queda
para `EMAIL_TO`. Best-effort — NUNCA levanta exceção pro caller, porque um
problema no e-mail não pode derrubar a captura.

## Por que isso existe

Em 30/07/2026 a rodada da madrugada falhou nos 13 escritórios (HTTP 502 do L1
entre 01:04 e 02:00) e **ninguém foi avisado**. Não foi bug: a captura
simplesmente nunca chamou alerta nenhum — `send_failure_report` já era usado
por 4 módulos, e esse ficou de fora.

Pior: a automação terminou com `last_status = success`, porque o passo rodou
até o fim mesmo com todos os escritórios falhando. Ou seja, não havia UM lugar
onde a falha aparecesse.

## Um alerta por RODADA, não por escritório

A rodada é diária (cron `0 1 * * *`) e varre 13 escritórios um a um. Alertar por
escritório mandaria 13 e-mails da mesma queda — e alerta que vira ruído é alerta
que ninguém lê. Como é 1 rodada/dia, 1 e-mail por rodada é o teto natural e
dispensa trava anti-spam.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

logger = logging.getLogger("publicacoes.alertas")


def _destinatarios() -> Optional[str]:
    from app.core.config import settings

    return (
        getattr(settings, "publication_alert_email", None)
        or getattr(settings, "email_to", None)
    )


def alertar_falha_captura(
    *,
    escritorios_falha: Sequence[int],
    escritorios_ok: Sequence[int],
    erro: str,
    janela: Optional[str] = None,
    contingencia: Optional[str] = None,
    run_id: Optional[int] = None,
) -> None:
    """Avisa que a captura de publicações não trouxe tudo.

    `contingencia`: o que aconteceu com o fallback do relatório — serve pro
    operador saber se precisa subir a planilha à mão ou se já foi resolvido.
    """
    try:
        from app.services.mail_service import send_failure_report

        destinatarios = _destinatarios()
        if not destinatarios:
            logger.error(
                "CAPTURA DE PUBLICAÇÕES FALHOU em %s escritório(s) e não há "
                "destinatário configurado (PUBLICATION_ALERT_EMAIL/EMAIL_TO) — "
                "ninguém vai ser avisado.",
                len(escritorios_falha),
            )
            return

        total = len(escritorios_falha) + len(escritorios_ok)
        rotulo = f"{len(escritorios_falha)} de {total} escritório(s) sem captura"
        if janela:
            rotulo += f" · janela {janela}"

        motivo = (erro or "erro desconhecido")[:1200]
        if contingencia:
            motivo += f"\n\nContingência por relatório: {contingencia}"
        if not escritorios_ok:
            motivo += (
                "\n\nNENHUM escritório capturou nesta rodada. Se a contingência "
                "também não resolveu, suba a planilha do Legal One em "
                "Publicações → Importar planilha."
            )

        send_failure_report(
            failed_items=[{
                "cnj": rotulo,
                "motivo": motivo,
                "execution_id": run_id,
            }],
            batch_source="Captura de Publicações",
            recipients=destinatarios,
            system_name="Flow",
        )
        logger.info(
            "Alerta de falha da captura enviado (%s escritórios em falha).",
            len(escritorios_falha),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao enviar o alerta da captura (ignorado).")


def alertar_contingencia_ativada(
    *,
    total_publicacoes: int,
    processos: int,
    report_id: Optional[int],
    janela: Optional[str] = None,
    erro_api: Optional[str] = None,
) -> None:
    """Avisa que a API do L1 caiu mas a contingência salvou a rodada.

    Não é falha — a captura aconteceu. Mas degradação silenciosa é como se
    perde a próxima: se a API ficar quebrada por dias e ninguém souber, a gente
    só descobre quando a contingência também falhar.
    """
    try:
        from app.services.mail_service import send_failure_report

        destinatarios = _destinatarios()
        if not destinatarios:
            logger.warning(
                "Contingência ativada, mas sem destinatário de alerta configurado."
            )
            return

        motivo = (
            f"A busca pela API do Legal One falhou e a captura foi feita pelo "
            f"relatório #{report_id}: {total_publicacoes} publicação(ões) de "
            f"{processos} processo(s). Nada foi perdido."
        )
        if erro_api:
            motivo += f"\n\nErro da API: {str(erro_api)[:800]}"
        motivo += (
            "\n\nA captura está funcionando pela contingência. Vale verificar a "
            "API do Legal One — se ela seguir fora, a próxima queda pode não ter "
            "rede de proteção."
        )

        send_failure_report(
            failed_items=[{
                "cnj": f"Captura pela contingência · janela {janela or '-'}",
                "motivo": motivo,
            }],
            batch_source="Captura de Publicações (contingência ativada)",
            recipients=destinatarios,
            system_name="Flow",
        )
        logger.info("Alerta de contingência ativada enviado (relatório #%s).", report_id)
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao enviar o alerta de contingência (ignorado).")


def alertar_cobertura_furada(
    *,
    pastas_na_raiz: int,
    escritorios_fora: Optional[Sequence[dict]] = None,
    office_ids_varridos: Optional[Sequence[int]] = None,
) -> None:
    """Avisa que existe pasta ATIVA fora do perímetro varrido pela captura.

    O terceiro tipo de falha, descoberto em 05/08/2026: não é a rodada que
    quebra nem a API que cai — é a pasta que nunca entrou no mapa. Publicação
    de processo no escritório raiz não é capturada e não vira descarte; ela
    simplesmente não existe pro Flow. Os outros dois alertas vigiam a execução
    e não pegam isso, porque a execução termina perfeita.
    """
    try:
        from app.services.mail_service import send_failure_report

        destinatarios = _destinatarios()
        if not destinatarios:
            logger.warning(
                "Cobertura furada (%s pasta(s) na raiz) mas não há destinatário "
                "configurado — alerta não enviado.", pastas_na_raiz,
            )
            return

        motivo = (
            f"{pastas_na_raiz} pasta(s) ATIVA(s) estão no escritório raiz "
            f"(\"MDR Advocacia\"), ou seja, sem escritório responsável de "
            f"verdade.\n\n"
            "A busca de publicações filtra por escritório. Pasta na raiz não "
            "entra em busca nenhuma: a publicação desses processos NÃO é "
            "capturada e também NÃO aparece como descarte na auditoria — ela "
            "não deixa rastro. O prazo corre sem ninguém ver.\n\n"
            "O que fazer: preencher o escritório responsável dessas pastas no "
            "Legal One. Para corrigir em lote, use "
            "scripts/corrigir_escritorio_responsavel.py."
        )
        if office_ids_varridos:
            motivo += (
                f"\n\nEscritórios varridos hoje: "
                f"{', '.join(str(o) for o in office_ids_varridos)}."
            )
        if escritorios_fora:
            linhas = "\n".join(
                f"  · {e['pastas']} pasta(s) — escritório {e['office_id']} ({e['path']})"
                for e in escritorios_fora
            )
            motivo += (
                "\n\nOutros escritórios fora da varredura (pode ser intencional, "
                "mas confira se alguma dessas áreas deveria receber publicação):\n"
                + linhas
            )

        send_failure_report(
            failed_items=[{
                "cnj": f"{pastas_na_raiz} pasta(s) sem escritório responsável",
                "motivo": motivo,
            }],
            batch_source="Captura de Publicações (cobertura)",
            recipients=destinatarios,
            system_name="Flow",
        )
        logger.info(
            "Alerta de cobertura enviado (%s pasta(s) na raiz).", pastas_na_raiz
        )
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao enviar o alerta de cobertura (ignorado).")
