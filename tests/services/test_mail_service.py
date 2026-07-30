from email import message_from_string

from app.services.mail_service import send_failure_report


class FakeSMTP:
    instances = []
    refused_recipients = {}

    def __init__(self, server, port):
        self.server = server
        self.port = port
        self.started_tls = False
        self.login_args = None
        self.sent = None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.login_args = (username, password)

    def sendmail(self, from_addr, recipients, message):
        self.sent = {
            "from_addr": from_addr,
            "recipients": recipients,
            "message": message,
        }
        return self.refused_recipients


def test_send_failure_report_uses_mail_env_and_explicit_recipients(monkeypatch):
    FakeSMTP.instances = []
    FakeSMTP.refused_recipients = {}
    monkeypatch.setattr("app.services.mail_service.smtplib.SMTP", FakeSMTP)
    monkeypatch.setenv("MAIL_HOST", "smtp.example.test")
    monkeypatch.setenv("MAIL_PORT", "587")
    monkeypatch.setenv("MAIL_USERNAME", "sender@example.test")
    monkeypatch.setenv("MAIL_PASSWORD", "secret")
    monkeypatch.setenv("MAIL_ENCRYPTION", "tls")
    monkeypatch.setenv("MAIL_FROM_ADDRESS", "sender@example.test")
    monkeypatch.setenv("MAIL_FROM_NAME", "NIC")

    sent = send_failure_report(
        [
            {
                "cnj": "0001425-14.2017.4.01.4103",
                "motivo": "Processo <nao encontrado>",
                "execution_id": 7,
                "item_id": 11,
            }
        ],
        "OneRequest",
        recipients=["destino@example.test"],
        system_name="Flow",
    )

    assert sent is True
    smtp = FakeSMTP.instances[0]
    assert smtp.server == "smtp.example.test"
    assert smtp.port == 587
    assert smtp.started_tls is True
    assert smtp.login_args == ("sender@example.test", "secret")
    assert smtp.sent["from_addr"] == "sender@example.test"
    assert smtp.sent["recipients"] == ["destino@example.test"]

    message = message_from_string(smtp.sent["message"])
    html_part = message.get_payload()[1]
    html = html_part.get_payload(decode=True).decode(html_part.get_content_charset())
    assert "sistema Flow" in html
    assert "Lote #7 / Item #11" in html
    assert "Processo &lt;nao encontrado&gt;" in html


def test_send_failure_report_retries_when_smtp_refuses_a_recipient(monkeypatch):
    FakeSMTP.instances = []
    FakeSMTP.refused_recipients = {
        "destino@example.test": (550, b"mailbox unavailable")
    }
    monkeypatch.setattr("app.services.mail_service.smtplib.SMTP", FakeSMTP)
    monkeypatch.setenv("MAIL_HOST", "smtp.example.test")
    monkeypatch.setenv("MAIL_PORT", "587")
    monkeypatch.setenv("MAIL_USERNAME", "sender@example.test")
    monkeypatch.setenv("MAIL_PASSWORD", "secret")
    monkeypatch.setenv("MAIL_ENCRYPTION", "tls")
    monkeypatch.setenv("MAIL_FROM_ADDRESS", "sender@example.test")

    sent = send_failure_report(
        [{"cnj": "Busca #1", "motivo": "HTTP 502"}],
        "Publicações",
        recipients=["destino@example.test"],
        system_name="Flow",
    )

    assert sent is False
