import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from html import escape
from typing import Iterable

from app.core.config import settings


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip()
        setting_value = getattr(settings, name.lower(), None)
        if setting_value is not None:
            return str(setting_value).strip()
    return None


def _split_recipients(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [email.strip() for email in raw.split(",") if email.strip()]


def _normalize_recipients(recipients: Iterable[str] | str | None) -> list[str]:
    if recipients is None:
        return []
    if isinstance(recipients, str):
        return _split_recipients(recipients)

    normalized: list[str] = []
    for email in recipients:
        email = (email or "").strip()
        if email and email not in normalized:
            normalized.append(email)
    return normalized


def _log_reference(item: dict) -> str:
    parts = []
    execution_id = item.get("execution_id")
    item_id = item.get("item_id") or item.get("log_item_id")
    if execution_id is not None:
        parts.append(f"Lote #{execution_id}")
    if item_id is not None:
        parts.append(f"Item #{item_id}")
    return " / ".join(parts) or "-"


def send_failure_report(
    failed_items: list,
    batch_source: str = "OneRequest",
    recipients: Iterable[str] | str | None = None,
    system_name: str | None = None,
) -> bool:
    """
    Envia um e-mail relatando as falhas no processamento em lote.

    Quando `recipients` e informado, usa essa lista explicitamente. Sem ela,
    mantem compatibilidade com EMAIL_TO/MAIL_TO do ambiente.
    """
    mailer = (_first_env("MAIL_MAILER") or "smtp").lower()
    if mailer != "smtp":
        logging.warning("MAIL_MAILER=%s nao suportado para notificacoes. E-mail nao enviado.", mailer)
        return False

    smtp_server = _first_env("MAIL_HOST", "SMTP_SERVER")
    smtp_port = int(_first_env("MAIL_PORT", "SMTP_PORT") or 587)
    smtp_user = _first_env("MAIL_USERNAME", "SMTP_USER")
    smtp_password = _first_env("MAIL_PASSWORD", "SMTP_PASSWORD")
    email_from = _first_env("MAIL_FROM_ADDRESS", "EMAIL_FROM") or smtp_user
    email_from_name = _first_env("MAIL_FROM_NAME")
    encryption = (_first_env("MAIL_ENCRYPTION") or "tls").lower()

    explicit_recipients = recipients is not None
    recipient_list = _normalize_recipients(recipients)
    if not explicit_recipients:
        recipient_list = _split_recipients(_first_env("EMAIL_TO", "MAIL_TO"))

    if not all([smtp_server, smtp_user, smtp_password, email_from, recipient_list]):
        logging.warning("Configuracoes de SMTP incompletas ou nenhum destinatario definido. E-mail nao enviado.")
        return False

    system_label = system_name or _first_env("SYSTEM_NAME", "APP_NAME") or "Flow"
    sent_at = datetime.now().strftime("%d/%m/%Y %H:%M")
    subject = f"[{system_label}] Relatorio de falhas - {batch_source} - {sent_at}"
    email_to_header = ", ".join(recipient_list)
    from_header = (
        formataddr((email_from_name, email_from))
        if email_from_name
        else email_from
    )
    has_log_reference = any(
        item.get("execution_id") is not None
        or item.get("item_id") is not None
        or item.get("log_item_id") is not None
        for item in failed_items
    )

    safe_batch_source = escape(batch_source)
    safe_system_label = escape(system_label)

    html_rows = []
    plain_rows = []
    for item in failed_items:
        cnj = str(item.get("cnj", "N/A"))
        motivo = str(item.get("motivo", "Erro desconhecido"))
        log_reference = _log_reference(item)

        log_cell = ""
        if has_log_reference:
            log_cell = f"<td>{escape(log_reference)}</td>"

        html_rows.append(
            f"""
                <tr>
                    <td>{escape(cnj)}</td>
                    {log_cell}
                    <td style="color: #d9534f; white-space: pre-wrap;">{escape(motivo)}</td>
                </tr>
            """
        )
        plain_rows.append(f"- {cnj} | {_log_reference(item)} | {motivo}")

    log_header = '<th style="text-align: left;">Log</th>' if has_log_reference else ""
    html_content = f"""
    <html>
    <body>
        <h2 style="color: #d9534f;">Relatório de Falhas no Processamento</h2>
        <p>O sistema encontrou erros ao processar o lote via <strong>{safe_batch_source}</strong>.</p>
        <p><strong>Total de falhas:</strong> {len(failed_items)}</p>

        <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%; border-color: #ddd;">
            <thead>
                <tr style="background-color: #f2f2f2;">
                    <th style="text-align: left;">CNJ / Identificador</th>
                    {log_header}
                    <th style="text-align: left;">Motivo do Erro</th>
                </tr>
            </thead>
            <tbody>
                {''.join(html_rows)}
            </tbody>
        </table>
        <br>
        <p style="font-size: 12px; color: #666;">Este é um e-mail automático do sistema {safe_system_label}.</p>
    </body>
    </html>
    """
    plain_content = "\n".join(
        [
            f"Relatorio de Falhas no Processamento - {batch_source}",
            f"Total de falhas: {len(failed_items)}",
            "",
            *plain_rows,
            "",
            f"Este e um e-mail automatico do sistema {system_label}.",
        ]
    )

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = from_header
        msg["To"] = email_to_header
        msg["Subject"] = subject
        msg.attach(MIMEText(plain_content, "plain", "utf-8"))
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        refused_recipients = {}
        if encryption in {"ssl", "smtps"}:
            with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
                server.login(smtp_user, smtp_password)
                refused_recipients = server.sendmail(
                    email_from,
                    recipient_list,
                    msg.as_string(),
                )
        else:
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                if encryption not in {"none", "false", "0", "no"}:
                    server.starttls()
                server.login(smtp_user, smtp_password)
                refused_recipients = server.sendmail(
                    email_from,
                    recipient_list,
                    msg.as_string(),
                )

        if refused_recipients:
            logging.error(
                "SMTP recusou destinatário(s) do alerta: %s",
                ", ".join(sorted(str(email) for email in refused_recipients)),
            )
            return False

        logging.info("E-mail de alerta de falhas enviado com sucesso para: %s", email_to_header)
        return True

    except Exception as exc:
        logging.error("Erro critico ao tentar enviar e-mail de alerta: %s", exc)
        return False
