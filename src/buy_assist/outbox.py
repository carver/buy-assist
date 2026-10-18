"""Emails to the household: who gets them, links back to the app, and the shared footer."""

import logging
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from buy_assist.domains import ReferenceDomain, reference_domains
from buy_assist.forms import format_dollars
from buy_assist.mailer import Mailer, MailerError
from buy_assist.models import Item, User
from buy_assist.store import Store

_log = logging.getLogger(__name__)

_TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
    keep_trailing_newline=True,
)
_TEMPLATES.filters["dollars"] = format_dollars  # pyright: ignore[reportArgumentType]


@dataclass(frozen=True)
class Recipient:
    user: User
    email: str


class Outbox:
    def __init__(self, store: Store, mailer: Mailer) -> None:
        self._store = store
        self._mailer = mailer

    def everyone(self) -> list[Recipient]:
        return [Recipient(u, u.email) for u in self._store.users() if u.email]

    def watchers(self, item: Item) -> list[Recipient]:
        return [r for r in self.everyone() if r.user.id in item.fields.watcher_ids]

    def item_url(self, item: Item) -> str | None:
        address = self._store.app_address()
        return f"{address.rstrip('/')}/items/{item.id}" if address else None

    def send(
        self, recipient: Recipient, subject: str, template: str, context: dict[str, object]
    ) -> bool:
        """Send one email from `email/<template>.txt` and `.html`. Every email gets
        `reminders` for its footer (see email/_footer.*), so each template must include
        that footer. False when the send failed, which is logged."""
        context = {**context, "reminders": self._reminders(recipient.user)}
        text = _TEMPLATES.get_template(f"email/{template}.txt").render(context)
        html = _TEMPLATES.get_template(f"email/{template}.html").render(context)
        try:
            self._mailer.send(recipient.email, subject, text, html)
        except MailerError as e:
            _log.warning("Mail to %s: %s", recipient.user.name, e)
            return False
        return True

    def _reminders(self, user: User) -> list[ReferenceDomain]:
        """For the admin's footer: reference-link sites that deserve their own reader."""
        if not user.is_admin:
            return []
        return [d for d in reference_domains(self._store) if d.wants_reader]
