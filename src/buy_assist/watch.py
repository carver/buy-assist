"""The scheduler's work: fetch listings, alert watchers, send the daily digest.

`Watch.tick` runs every hour. Each job checks when it last succeeded, so a sandbox that was
off catches up on its first tick back. Instant alerts are worked out from what's stored, not
from what one fetch changed, and the notice log stops repeats; an email that fails to send is
tried again next tick. Delivery is at least once: a send that finishes just as the app shuts
down, before its notice is logged, goes out again on the next start.
"""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from buy_assist.claude import ParseError
from buy_assist.domains import ReferenceDomain, reference_domains
from buy_assist.forms import format_dollars
from buy_assist.judge import ListingJudge
from buy_assist.listings import ListingError, ListingSource
from buy_assist.mailer import Mailer, MailerError
from buy_assist.models import Item, Listing, NoticeKind, RecordedListing, User
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
FIRST_FOUND_WINDOW = timedelta(days=2)
"""How soon after an item's first listing turns up a "first listing found" alert still makes
sense. Later, say for a watcher added weeks on, the digest covers it instead."""

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
    kinds: tuple[NoticeKind, ...]

    @property
    def heading(self) -> str:
        return " · ".join(_HEADINGS[kind] for kind in self.kinds)


def _alert_lines(reasons: list[_Reason]) -> list[_AlertLine]:
    """Reasons grouped by listing, most important first."""
    ordered = sorted(reasons, key=lambda r: list(_HEADINGS).index(r.kind))
    kinds: dict[int, list[NoticeKind]] = {}
    first: dict[int, _Reason] = {}
    for reason in ordered:
        kinds.setdefault(reason.listing.id, []).append(reason.kind)
        first.setdefault(reason.listing.id, reason)
    return [_AlertLine(r.listing, r.price, tuple(kinds[i])) for i, r in first.items()]


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


@dataclass(frozen=True)
class _Recipient:
    user: User
    email: str


class Watch:
    def __init__(
        self,
        store: Store,
        *,
        listing_sources: Sequence[ListingSource],
        judge: ListingJudge,
        sold_prices: SoldPriceSource,
        shipping: ShippingQuoter,
        mailer: Mailer | None,
    ) -> None:
        self._store = store
        self._sources = listing_sources
        self._judge = judge
        self._sold = sold_prices
        self._pricer = Pricer(store, shipping)
        self._mailer = mailer

    def tick(self, now: datetime) -> None:
        items = self._store.list_items(open_items=True)
        for item in items:
            due = [s for s in self._sources if self._due(_fetch_job(item, s), now, FETCH_EVERY)]
            if due:
                fetch_listings(self._store, due, self._judge, item, now)
        if self._due("sold", now, SOLD_REFRESH_EVERY) and self._refresh_sold(items):
            self._store.set_last_run("sold", now)
        if self._mailer is None:
            return
        for item in items:
            self._send_alerts(self._mailer, item, now)
        if now.astimezone(PACIFIC).time() >= DIGEST_TIME:
            self._send_digests(self._mailer, items, now)

    def _due(self, job: str, now: datetime, every: timedelta) -> bool:
        last = self._store.last_run(job)
        return last is None or now - last >= every

    def _refresh_sold(self, items: Iterable[Item]) -> bool:
        """Refresh sold prices for every item a source covers. True unless every one failed."""
        tried = failed = 0
        for item in items:
            if not self._sold.covers(item.fields):
                continue
            tried += 1
            try:
                self._store.add_sales(item.id, self._sold.sales_for(item.fields))
            except SoldPriceError as e:
                failed += 1
                _log.warning("%s sold prices for item %d: %s", self._sold.label, item.id, e)
        return tried == 0 or failed < tried

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
            price.total_cents
            for r in self._store.sales(item.id)
            if (price := self._pricer.sale(item.fields, r.sale)).complete
        ]
        for recipient in self._recipients(item):
            reasons = self._reasons(item, recipient.user, priced, sale_totals, now)
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
            if self._send(mailer, recipient, subject, "alert", context):
                for reason in reasons:
                    self._store.log_notice(reason.listing.id, recipient.user.id, reason.kind, now)

    def _reasons(
        self,
        item: Item,
        user: User,
        priced: list[_Priced],
        sale_totals: list[int],
        now: datetime,
    ) -> list[_Reason]:
        """Why this person should hear about the item's listings now. A price with unknown
        shipping or tax can't be at target or a new low: it's only a lower bound."""
        noticed = {p.listing.id: self._store.noticed(p.listing.id, user.id) for p in priced}
        active = [p for p in priced if p.listing.ended_at is None]
        reasons: list[_Reason] = []
        first_seen = min(p.listing.first_seen for p in priced)
        if active and now - first_seen <= FIRST_FOUND_WINDOW and not any(noticed.values()):
            cheapest = active[0]
            reasons.append(_Reason(NoticeKind.FIRST_FOUND, cheapest.listing, cheapest.price))
        target = item.fields.target_price_cents
        for p in (p for p in active if p.price.complete):
            seen = noticed[p.listing.id]
            if (
                target is not None
                and p.price.total_cents <= target
                and NoticeKind.AT_TARGET not in seen
            ):
                reasons.append(_Reason(NoticeKind.AT_TARGET, p.listing, p.price))
            earlier = [
                q.price.total_cents
                for q in priced
                if q.listing.first_seen < p.listing.first_seen and q.price.complete
            ] + sale_totals
            if (
                len(earlier) >= NEW_LOW_MIN_DATA_POINTS
                and p.price.total_cents < min(earlier)
                and NoticeKind.NEW_LOW not in seen
            ):
                reasons.append(_Reason(NoticeKind.NEW_LOW, p.listing, p.price))
        return reasons

    def _send_digests(self, mailer: Mailer, items: list[Item], now: datetime) -> None:
        """Today's digest for everyone who hasn't had one yet today. Each person's day is
        tracked on its own, so one failed send can't double anyone else's digest."""
        today = now.astimezone(PACIFIC).date()
        for recipient in self._everyone_with_email():
            job = f"digest:{recipient.user.id}"
            last = self._store.last_run(job)
            if last is not None and last.astimezone(PACIFIC).date() >= today:
                continue
            entries = self._digest_entries(recipient.user, items)
            if not entries:
                continue
            subject = _digest_subject(entries)
            context: dict[str, object] = {"subject": subject, "entries": entries}
            if not self._send(mailer, recipient, subject, "digest", context):
                continue
            self._store.set_last_run(job, now)
            for entry in entries:
                for line in entry.lines:
                    self._store.log_notice(
                        line.listing.id, recipient.user.id, NoticeKind.DIGEST, now
                    )

    def _digest_entries(self, user: User, items: list[Item]) -> list[_DigestEntry]:
        """New listings per item: ones this person hasn't heard about in any way yet."""
        entries: list[_DigestEntry] = []
        for item in items:
            if user.id not in item.fields.watcher_ids:
                continue
            priced = self._priced(item)
            totals = [p.price.total_cents for p in priced]
            lines = [
                _DigestLine(p.listing, p.price, _rank(p.price.total_cents, totals))
                for p in priced
                if p.listing.ended_at is None and not self._store.noticed(p.listing.id, user.id)
            ]
            if lines:
                entries.append(_DigestEntry(item, self._item_url(item), lines))
        return entries

    def _everyone_with_email(self) -> list[_Recipient]:
        return [_Recipient(u, u.email) for u in self._store.users() if u.email]

    def _recipients(self, item: Item) -> list[_Recipient]:
        return [r for r in self._everyone_with_email() if r.user.id in item.fields.watcher_ids]

    def _reminders(self, user: User) -> list[ReferenceDomain]:
        """For the admin's footer: reference-link sites that deserve their own reader."""
        if not user.is_admin:
            return []
        return [d for d in reference_domains(self._store) if d.wants_reader]

    def _item_url(self, item: Item) -> str | None:
        address = self._store.app_address()
        return f"{address.rstrip('/')}/items/{item.id}" if address else None

    def _send(
        self,
        mailer: Mailer,
        recipient: _Recipient,
        subject: str,
        template: str,
        context: dict[str, object],
    ) -> bool:
        """Send one email. Every email gets `reminders` for its footer (see
        email/_footer.*), so each template must include that footer."""
        context = {**context, "reminders": self._reminders(recipient.user)}
        text = _EMAIL_TEMPLATES.get_template(f"email/{template}.txt").render(context)
        html = _EMAIL_TEMPLATES.get_template(f"email/{template}.html").render(context)
        try:
            mailer.send(recipient.email, subject, text, html)
        except MailerError as e:
            _log.warning("Mail to %s: %s", recipient.user.name, e)
            return False
        return True


def fetch_listings(
    store: Store, sources: Sequence[ListingSource], judge: ListingJudge, item: Item, now: datetime
) -> None:
    """Look for an item in every source that covers it, and record what's there, with
    Claude's verdict on listings not seen before where the source needs one. Each source
    remembers its own last good look: one whose look or judging failed records nothing and
    is tried again next tick, without repeating the others."""
    for source in sources:
        if not source.covers(item.fields):
            continue
        try:
            found = source.listings_for(item.fields, store.ship_to(item.fields))
        except ListingError as e:
            _log.warning("%s listings for item %d: %s", source.label, item.id, e)
            continue
        try:
            rejections = _judged(store, source, judge, item, found)
        except ParseError as e:
            _log.warning("Judging %s listings for item %d: %s", source.label, item.id, e)
            continue
        store.record_listings(item.id, source.marketplace, found, now, rejections=rejections)
        store.set_last_run(_fetch_job(item, source), now)


def _judged(
    store: Store,
    source: ListingSource,
    judge: ListingJudge,
    item: Item,
    found: Sequence[Listing],
) -> dict[str, str]:
    """Claude's rejections among the listings this look found for the first time."""
    if not source.needs_judging:
        return {}
    known = store.known_listing_ids(item.id, source.marketplace)
    unseen = [listing for listing in found if listing.external_id not in known]
    return judge.rejections(item.fields, unseen) if unseen else {}


def _fetch_job(item: Item, source: ListingSource) -> str:
    return f"fetch:{item.id}:{source.marketplace}"


def _rank(total: int, totals: list[int]) -> str:
    """'cheapest of 14 seen, median $62.00', or '#3 of 14 seen, ...'."""
    place = sum(1 for t in totals if t < total) + 1
    where = "cheapest" if place == 1 else f"#{place}"
    return f"{where} of {len(totals)} seen, median ${format_dollars(round(median(totals)))}"


def _digest_subject(entries: list[_DigestEntry]) -> str:
    count = sum(len(entry.lines) for entry in entries)
    return f"Buy Assistant digest: {count} new listing{'' if count == 1 else 's'}"
