"""Send email: alerts and digests to the household, through the user's Purelymail account."""

import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Protocol

_PURELYMAIL_HOST = "smtp.purelymail.com"
_PURELYMAIL_PORT = 465
_TIMEOUT_SECONDS = 30
_SENDER_NAME = "Buy Assistant"


class MailerError(Exception):
    """Mail couldn't be sent. The message names the cause, never a credential."""


class Mailer(Protocol):
    def send(self, to: str, subject: str, text: str, html: str | None) -> None:
        """Send one message. Raises MailerError."""
        ...


class SmtpMailer:
    def __init__(
        self, host: str, port: int, address: str, *, password: str | None, use_ssl: bool = True
    ) -> None:
        if password is not None and not use_ssl:
            raise ValueError("A password is only ever sent over TLS.")
        self._host = host
        self._port = port
        self._address = address
        self._password = password
        self._use_ssl = use_ssl

    @classmethod
    def purelymail_from_env(cls) -> SmtpMailer:
        """Credentials come from PURELYMAIL_ADDRESS and PURELYMAIL_PASSWORD, which
        /etc/sandbox-secrets.sh sets for the process that starts the app."""
        address = os.environ.get("PURELYMAIL_ADDRESS")
        password = os.environ.get("PURELYMAIL_PASSWORD")
        if not address or not password:
            raise MailerError("PURELYMAIL_ADDRESS and PURELYMAIL_PASSWORD must be set.")
        return cls(_PURELYMAIL_HOST, _PURELYMAIL_PORT, address, password=password)

    def send(self, to: str, subject: str, text: str, html: str | None) -> None:
        message = EmailMessage()
        message["From"] = formataddr((_SENDER_NAME, self._address))
        message["To"] = to
        message["Subject"] = " ".join(subject.split())
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain=self._address.rpartition("@")[2] or None)
        message.set_content(text)
        if html is not None:
            message.add_alternative(html, subtype="html")
        smtp_class = smtplib.SMTP_SSL if self._use_ssl else smtplib.SMTP
        try:
            with smtp_class(self._host, self._port, timeout=_TIMEOUT_SECONDS) as smtp:
                if self._password is not None:
                    smtp.login(self._address, self._password)
                smtp.send_message(message)
        except (OSError, ValueError, smtplib.SMTPException) as e:
            raise MailerError(f"Couldn't send mail through {self._host}: {type(e).__name__}") from e
