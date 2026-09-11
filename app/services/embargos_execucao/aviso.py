"""Aviso de embargos encontrados: e-mail para a Controladoria + trilha do card.

Sai uma vez por achado (`aviso_enviado_em`). Destinatários nos parâmetros do
painel (`embargos_execucao_aviso_emails`); sem destinatário, fica só o evento
e o destaque no board. SMTP é o mesmo do resto do Flow (MAIL_* no Coolify).
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from html import escape
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.embargos_execucao import (
    EVT_AVISO,
    EVT_INFO,
    NIVEL_CONFIRMADO_DJEN,
    SECAO_AVISO,
    EmbCandidato,
    EmbExecucao,
)
from app.services import mail_service
from app.services.embargos_execucao import service

logger = logging.getLogger(__name__)

_NIVEL_TEXTO = {
    NIVEL_CONFIRMADO_DJEN: "embargante é parte da execução (DJEN)",
    "PROVAVEL": "distribuído por dependência + petição na execução no mesmo dia",
}


def _enviar(assunto: str, html: str, texto: str, destinatarios: list[str]) -> bool:
    env = mail_service._first_env
    servidor = env("MAIL_HOST", "SMTP_SERVER")
    porta = int(env("MAIL_PORT", "SMTP_PORT") or 587)
    usuario = env("MAIL_USERNAME", "SMTP_USER")
    senha = env("MAIL_PASSWORD", "SMTP_PASSWORD")
    remetente = env("MAIL_FROM_ADDRESS", "EMAIL_FROM") or usuario
    nome_remetente = env("MAIL_FROM_NAME")
    cripto = (env("MAIL_ENCRYPTION") or "tls").lower()
    if not all([servidor, usuario, senha, remetente, destinatarios]):
        logger.warning("Embargos: SMTP incompleto ou sem destinatário — aviso não enviado.")
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr((nome_remetente, remetente)) if nome_remetente else remetente
    msg["To"] = ", ".join(destinatarios)
    msg["Subject"] = assunto
    msg.attach(MIMEText(texto, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        if cripto in {"ssl", "smtps"}:
            with smtplib.SMTP_SSL(servidor, porta) as s:
                s.login(usuario, senha)
                s.sendmail(remetente, destinatarios, msg.as_string())
        else:
            with smtplib.SMTP(servidor, porta) as s:
                if cripto not in {"none", "false", "0", "no"}:
                    s.starttls()
                s.login(usuario, senha)
                s.sendmail(remetente, destinatarios, msg.as_string())
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("Embargos: falha ao enviar aviso por e-mail: %s", exc)
        return False


def montar_mensagem(exe: EmbExecucao, candidatos: list[EmbCandidato]) -> tuple[str, str, str]:
    assunto = f"[Flow] Embargos à execução encontrados — {exe.pasta} ({exe.cnj or 'sem CNJ'})"
    linhas_html = []
    linhas_txt = []
    for c in candidatos:
        motivo = _NIVEL_TEXTO.get(c.nivel, c.nivel)
        quando = c.data_ajuizamento.strftime("%d/%m/%Y") if c.data_ajuizamento else "—"
        embargantes = ", ".join(c.djen_nomes_casados or c.djen_embargantes or []) or "—"
        linhas_html.append(
            f"<tr><td>{escape(c.cnj)}</td><td>{quando}</td><td>{escape(embargantes)}</td>"
            f"<td>{escape(motivo)}</td></tr>"
        )
        linhas_txt.append(f"- {c.cnj} | ajuizado {quando} | {embargantes} | {motivo}")
    html = f"""
    <html><body>
      <h2>Embargos à execução encontrados</h2>
      <p>Execução <strong>{escape(exe.pasta)}</strong> — CNJ {escape(exe.cnj or '—')}
      {(' — NPJ ' + escape(exe.npj)) if exe.npj else ''}<br>
      Ajuizada em {exe.data_ajuizamento:%d/%m/%Y}
      {(' · ' + escape(exe.orgao_nome)) if exe.orgao_nome else ''}
      {(' · responsável ' + escape(exe.responsavel_nome)) if exe.responsavel_nome else ''}</p>
      <table border="1" cellpadding="5" cellspacing="0" style="border-collapse:collapse">
        <thead><tr><th>Embargos (CNJ)</th><th>Ajuizamento</th><th>Embargante</th><th>Evidência</th></tr></thead>
        <tbody>{''.join(linhas_html)}</tbody>
      </table>
      <p>O monitoramento dessa execução foi pausado. Confira o vínculo no board
      <strong>Minha Equipe › Controladoria › Embargos à Execução</strong>.</p>
      <p style="font-size:12px;color:#666">E-mail automático do Flow.</p>
    </body></html>
    """
    texto = "\n".join([
        "Embargos à execução encontrados",
        f"Execução {exe.pasta} — CNJ {exe.cnj or '—'} — ajuizada em {exe.data_ajuizamento:%d/%m/%Y}",
        "",
        *linhas_txt,
        "",
        "Confira no board Minha Equipe › Controladoria › Embargos à Execução.",
    ])
    return assunto, html, texto


def avisar_encontrado(db: Session, exe: EmbExecucao, candidatos: list[EmbCandidato],
                      enviar=None) -> bool:
    if exe.aviso_enviado_em is not None:
        return False
    destinatarios = service.emails_aviso()
    enviado = False
    if destinatarios:
        assunto, html, texto = montar_mensagem(exe, candidatos)
        enviado = (enviar or _enviar)(assunto, html, texto, destinatarios)
    exe.aviso_enviado_em = service.agora()
    service.registrar_evento(
        db, SECAO_AVISO,
        (f"Aviso enviado por e-mail para {', '.join(destinatarios)}."
         if enviado else
         ("Sem destinatário configurado — aviso só no board." if not destinatarios
          else "Falha no envio do e-mail — o card segue destacado no board.")),
        nivel=EVT_INFO if enviado or not destinatarios else EVT_AVISO,
        execucao_id=exe.id,
        dados={"candidatos": [c.cnj for c in candidatos], "destinatarios": destinatarios},
    )
    return enviado
