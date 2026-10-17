"""The scheduler's work: fetch listings, alert watchers, send the daily digest.

`Watch.tick` runs every hour. Each job checks when it last succeeded, so a sandbox that was
off catches up on its first tick back. Instant alerts are worked out from what's stored, not
from what one fetch changed, and the notice log stops repeats; an email that fails to send is
tried again next tick.
"""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from buy_assist.domains import ReferenceDomain, reference_domains
from buy_assist.forms import format_dollars
from buy_assist.listings import ListingError, ListingSource
from buy_assist.mailer import Mailer, MailerError
from buy_assist.models import Item, NoticeKind, RecordedListing, User
from buy_assist.prices import FullPrice
from buy_assist.pricing import Pricer, ShippingQuoter
from buy_assist.sold import SoldPriceError, SoldPriceSource
from buy_assist.store import Store

_log = logging.getLogger(__name__)

PACIFIC = ZoneInfo("America/Los_Angeles")
DIGEST_TIME = time(11, 30)
FETCH_EVERY = timedelta(hours=24)
SOLD_REFRESH_EVERY = timedelta(days=7)
NEW_LOW_MIN_DATA_POINTS = 5
REMINDER_RECIPIENT = "Jason"
"""Who hears that a reference-link site deserves its own reader."""

_HEADINGS = {
    NoticeKind.AT_TARGET: "At your target",
    NoticeKind.NEW_LOW: "New low price",
    NoticeKind.FIRST_FOUND: "First listing found",
}
_EMAIL_TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
    keep_trailing_newline=True,
)
_EMAIL_TEMPLATES.filters["dollars"] = format_dollars  # pyright: ignore[reportArgumentType]


@dataclass(frozen=True)
class _Priced:
    listing: RecordedListing
    price: FullPrice


@dataclass(frozen=True)
class _Reason:
    kind: NoticeKind
    listing: RecordedListing
    price: FullPrice

    @property
    def heading(self) -> str:
        return _HEADINGS[self.kind]


@dataclass(frozen=True)
class _AlertLine:
    """One listing in an alert, with every reason it's there."""

    listing: RecordedListing
    price: FullPrice
    kinds: list[NoticeKind]

    @property
    def heading(self) -> str:
        return " · ".join(_HEADINGS[kind] for kind in self.kinds)


def _alert_lines(reasons: list[_Reason]) -> list[_AlertLine]:
    """Reasons grouped by listing, most important first."""
    ordered = sorted(reasons, key=lambda r: list(_HEADINGS).index(r.kind))
    lines: dict[int, _AlertLine] = {}
    for reason in ordered:
        line = lines.setdefault(reason.listing.id, _AlertLine(reason.listing, reason.price, []))
        line.kinds.append(reason.kind)
    return list(lines.values())


@dataclass(frozen=True)
class _DigestLine:
    listing: RecordedListing
    price: FullPrice
    rank: str


@dataclass(frozen=True)
class _DigestEntry:
    item: Item
    item_url: str | None
    lines: list[_DigestLine]


class Watch:
    def __init__(
        self,
        store: Store,
        *,
        listing_sources: Sequence[ListingSource],
        sold_prices: SoldPriceSource,
        shipping: ShippingQuoter,
        mailer: Mailer | None,
    ) -> None:
        self._store = store
        self._sources = listing_sources
        self._sold = sold_prices
        self._pricer = Pricer(store, shipping)
        self._mailer = mailer

    def tick(self, now: datetime) -> None:
        items = self._store.list_items(open_items=True)
        if self._due("fetch", now, FETCH_EVERY) and self._fetch_listings(items, now):
            self._store.set_last_run("fetch", now)
        if self._due("sold", now, SOLD_REFRESH_EVERY):
            self._refresh_sold(items)
            self._store.set_last_run("sold", now)
        if self._mailer is None:
            return
        for item in items:
            self._send_alerts(self._mailer, item, now)
        if self._digest_due(now) and self._send_digests(self._mailer, items, now):
            self._store.set_last_run("digest", now)

    def _due(self, job: str, now: datetime, every: timedelta) -> bool:
        last = self._store.last_run(job)
        return last is None or now - last >= every

    def _fetch_listings(self, items: Iterable[Item], now: datetime) -> bool:
        """Look for every open item. True unless every lookup failed."""
        tried = failed = 0
        for item in items:
            for source in self._sources:
                if not source.covers(item.fields):
                    continue
                tried += 1
                try:
                    found = source.listings_for(item.fields)
                except ListingError as e:
                    failed += 1
                    _log.warning("%s listings for item %d: %s", source.label, item.id, e)
                    continue
                self._store.record_listings(item.id, source.marketplace, found, now)
        return tried == 0 or failed < tried

    def _refresh_sold(self, items: Iterable[Item]) -> None:
        for item in items:
            if not self._sold.covers(item.fields):
                continue
            try:
                self._store.add_sales(item.id, self._sold.sales_for(item.fields))
            except SoldPriceError as e:
                _log.warning("%s sold prices for item %d: %s", self._sold.label, item.id, e)

    def _priced(self, item: Item) -> list[_Priced]:
        """Every listing ever seen for the item, priced in full, cheapest first."""
        priced = [
            _Priced(r, self._pricer.listing(item.fields, r.listing))
            for r in self._store.listings(item.id, include_ended=True)
        ]
        return sorted(priced, key=lambda p: p.price.total_cents)

    def _send_alerts(self, mailer: Mailer, item: Item, now: datetime) -> None:
        priced = self._priced(item)
        if not priced:
            return
        sale_totals = [
            self._pricer.sale(item.fields, r.sale).total_cents for r in self._store.sales(item.id)
        ]
        for user in self._recipients(item):
            reasons = self._reasons(item, user, priced, sale_totals)
            if not reasons:
                continue
            lines = _alert_lines(reasons)
            subject = f"{_HEADINGS[lines[0].kinds[0]]}: {item.fields.name}"
            context: dict[str, object] = {
                "subject": subject,
                "item": item.fields,
                "lines": lines,
                "first_found": any(r.kind is NoticeKind.FIRST_FOUND for r in reasons),
                "item_url": self._item_url(item),
            }
            if self._send(mailer, user, subject, "alert", context):
                for reason in reasons:
                    self._store.log_notice(reason.listing.id, user.id, reason.kind, now)

    def _reasons(
        self, item: Item, user: User, priced: list[_Priced], sale_totals: list[int]
    ) -> list[_Reason]:
        noticed = {p.listing.id: self._store.noticed(p.listing.id, user.id) for p in priced}
        active = [p for p in priced if p.listing.ended_at is None]
        reasons: list[_Reason] = []
        if active and not any(noticed.values()):
            cheapest = active[0]
            reasons.append(_Reason(NoticeKind.FIRST_FOUND, cheapest.listing, cheapest.price))
        target = item.fields.target_price_cents
        for p in active:
            seen = noticed[p.listing.id]
            if (
                target is not None
                and p.price.total_cents <= target
                and NoticeKind.AT_TARGET not in seen
            ):
                reasons.append(_Reason(NoticeKind.AT_TARGET, p.listing, p.price))
            earlier = [
                q.price.total_cents for q in priced if q.listing.first_seen < p.listing.first_seen
            ] + sale_totals
            if (
                len(earlier) >= NEW_LOW_MIN_DATA_POINTS
                and p.price.total_cents < min(earlier)
                and NoticeKind.NEW_LOW not in seen
            ):
                reasons.append(_Reason(NoticeKind.NEW_LOW, p.listing, p.price))
        return reasons

    def _digest_due(self, now: datetime) -> bool:
        local = now.astimezone(PACIFIC)
        if local.time() < DIGEST_TIME:
            return False
        last = self._store.last_run("digest")
        return last is None or last.astimezone(PACIFIC).date() < local.date()

    def _send_digests(self, mailer: Mailer, items: list[Item], now: datetime) -> bool:
        """Each person's digest. True if every one that had something to say went out."""
        wanted = [d for d in reference_domains(self._store) if d.wants_reader]
        reminded = self._store.reminded_domains()
        all_sent = True
        for user in self._store.users():
            if not user.email:
                continue
            entries = self._digest_entries(user, items)
            reminders = (
                wanted
                if user.name == REMINDER_RECIPIENT and {d.domain for d in wanted} - reminded
                else []
            )
            if not entries and not reminders:
                continue
            subject = _digest_subject(entries, reminders)
            context: dict[str, object] = {
                "subject": subject,
                "entries": entries,
                "reminders": reminders,
            }
            if not self._send(mailer, user, subject, "digest", context):
                all_sent = False
                continue
            for entry in entries:
                for line in entry.lines:
                    self._store.log_notice(line.listing.id, user.id, NoticeKind.DIGEST, now)
            if reminders:
                self._store.set_reminded_domains(reminded | {d.domain for d in reminders})
        return all_sent

    def _digest_entries(self, user: User, items: list[Item]) -> list[_DigestEntry]:
        entries: list[_DigestEntry] = []
        for item in items:
            if user.id not in item.fields.watcher_ids:
                continue
            priced = self._priced(item)
            totals = [p.price.total_cents for p in priced]
            lines = [
                _DigestLine(p.listing, p.price, _rank(p.price.total_cents, totals))
                for p in priced
                if p.listing.ended_at is None
                and NoticeKind.DIGEST not in self._store.noticed(p.listing.id, user.id)
            ]
            if lines:
                entries.append(_DigestEntry(item, self._item_url(item), lines))
        return entries

    def _recipients(self, item: Item) -> list[User]:
        return [u for u in self._store.users() if u.id in item.fields.watcher_ids and u.email]

    def _item_url(self, item: Item) -> str | None:
        address = self._store.app_address()
        return f"{address.rstrip('/')}/items/{item.id}" if address else None

    def _send(
        self, mailer: Mailer, user: User, subject: str, template: str, context: dict[str, object]
    ) -> bool:
        assert user.email is not None
        text = _EMAIL_TEMPLATES.get_template(f"email/{template}.txt").render(context)
        html = _EMAIL_TEMPLATES.get_template(f"email/{template}.html").render(context)
        try:
            mailer.send(user.email, subject, text, html)
        except MailerError as e:
            _log.warning("Mail to %s: %s", user.name, e)
            return False
        return True


def _rank(total: int, totals: list[int]) -> str:
    """'cheapest of 14 seen, median $62.00', or '3rd of 14 seen, ...'."""
    place = sum(1 for t in totals if t < total) + 1
    where = "cheapest" if place == 1 else f"#{place}"
    return f"{where} of {len(totals)} seen, median ${format_dollars(round(median(totals)))}"


def _digest_subject(entries: list[_DigestEntry], reminders: list[ReferenceDomain]) -> str:
    count = sum(len(entry.lines) for entry in entries)
    if count:
        return f"Buy Assistant digest: {count} new listing{'' if count == 1 else 's'}"
    if len(reminders) == 1:
        return "Buy Assistant digest: a reference-link reader to write"
    return f"Buy Assistant digest: {len(reminders)} reference-link readers to write"
