import socket
from collections.abc import Iterator
from email import message_from_bytes, policy
from email.message import EmailMessage

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP, Envelope, Session

from buy_assist.mailer import MailerError, SmtpMailer


class Inbox:
    def __init__(self) -> None:
        self.messages: list[tuple[Envelope, EmailMessage]] = []

    async def handle_DATA(self, server: SMTP, session: Session, envelope: Envelope) -> str:
        content = envelope.content
        assert isinstance(content, bytes)
        message = message_from_bytes(content, policy=policy.default)
        assert isinstance(message, EmailMessage)
        self.messages.append((envelope, message))
        return "250 OK"


@pytest.fixture
def inbox() -> Iterator[tuple[Inbox, int]]:
    handler = Inbox()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    controller = Controller(handler, hostname="127.0.0.1", port=port)
    controller.start()
    try:
        yield handler, port
    finally:
        controller.stop()


def test_message_arrives_with_text_and_html(inbox: tuple[Inbox, int]) -> None:
    handler, port = inbox
    mailer = SmtpMailer("127.0.0.1", port, "buyer@example.com", password=None, use_ssl=False)

    mailer.send("shira@example.com", "New listing: Calendar", "Plain words", "<p>HTML words</p>")

    [(envelope, message)] = handler.messages
    assert envelope.rcpt_tos == ["shira@example.com"]
    assert message["From"] == "Buy Assistant <buyer@example.com>"
    assert message["Subject"] == "New listing: Calendar"
    plain = message.get_body(preferencelist=("plain",))
    html = message.get_body(preferencelist=("html",))
    assert plain is not None
    assert html is not None
    assert "Plain words" in plain.get_content()
    assert "<p>HTML words</p>" in html.get_content()


def test_unreachable_server_is_mailer_error() -> None:
    mailer = SmtpMailer("127.0.0.1", 1, "buyer@example.com", password=None, use_ssl=False)
    with pytest.raises(MailerError):
        mailer.send("shira@example.com", "Hi", "text", None)


def test_missing_credentials_name_their_source_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PURELYMAIL_ADDRESS", raising=False)
    monkeypatch.setenv("PURELYMAIL_PASSWORD", "hunter2-secret")
    with pytest.raises(MailerError) as caught:
        SmtpMailer.purelymail_from_env()
    assert "PURELYMAIL_ADDRESS" in str(caught.value)
    assert "hunter2" not in str(caught.value)


def test_line_breaks_in_subject_are_flattened(inbox: tuple[Inbox, int]) -> None:
    handler, port = inbox
    mailer = SmtpMailer("127.0.0.1", port, "buyer@example.com", password=None, use_ssl=False)

    mailer.send("shira@example.com", "At your target: EXIT\nBcc: x@evil.example", "t", None)

    [(envelope, message)] = handler.messages
    assert envelope.rcpt_tos == ["shira@example.com"]
    assert message["Subject"] == "At your target: EXIT Bcc: x@evil.example"


def test_password_is_never_sent_without_tls() -> None:
    with pytest.raises(ValueError):
        SmtpMailer("127.0.0.1", 25, "buyer@example.com", password="secret", use_ssl=False)
