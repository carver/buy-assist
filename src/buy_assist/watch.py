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
from datetime import date, datetime, time, timedelta
from statistics import median

from buy_assist.claude import ParseError
from buy_assist.forms import describe_auction, describe_time, format_dollars
from buy_assist.judge import ListingJudge
from buy_assist.listings import ListingError, ListingSource
from buy_assist.mailer import Mailer
from buy_assist.models import PACIFIC, Item, Listing, NoticeKind, RecordedListing, User
from buy_assist.outbox import Outbox
from buy_assist.prices import FullPrice, max_bid_cents
from buy_assist.pricing import Pricer, ShippingQuoter
from buy_assist.sold import SoldPriceError, SoldPriceSource
from buy_assist.store import Store

_log = logging.getLogger(__name__)

DIGEST_TIME = time(11, 30)
DIGEST_LATENESS = timedelta(hours=1)
"""The digest goes out at the first hourly tick from 11:30, so up to this much later."""
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
    NoticeKind.ENDING_SOON: "Auction ending soon",
}


@dataclass(frozen=True)
class _Priced:
    listing: RecordedListing
    price: FullPrice

    @property
    def is_auction(self) -> bool:
        return self.listing.listing.is_auction


@dataclass(frozen=True)
class _BidLimit:
    """What an auction's full price should stay within, and why."""

    cents: int
    why: str


@dataclass(frozen=True)
class _Reminder:
    """An auction ending soon, with how it's going and what to bid."""

    priced: _Priced
    note: str


@dataclass(frozen=True)
class _Reason:
    kind: NoticeKind
    listing: RecordedListing
    price: FullPrice
    note: str = ""

    @property
    def heading(self) -> str:
        return _HEADINGS[self.kind]


@dataclass(frozen=True)
class _AlertLine:
    """One listing in an alert, with every reason it's there."""

    listing: RecordedListing
    price: FullPrice
    kinds: tuple[NoticeKind, ...]
    note: str

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
    return [_AlertLine(r.listing, r.price, tuple(kinds[i]), r.note) for i, r in first.items()]


@dataclass(frozen=True)
class _DigestLine:
    listing: RecordedListing
    price: FullPrice
    standing: str
    """Where its price ranks among everything seen, or for an auction, how it's going and,
    when it ends soon, what to bid."""


@dataclass(frozen=True)
class _DigestEntry:
    item: Item
    item_url: str | None
    lines: list[_DigestLine]
    ending: list[_DigestLine]
    """Auctions that end before the next digest."""


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
        self._outbox = Outbox(store, mailer) if mailer else None

    def tick(self, now: datetime) -> None:
        items = self._store.list_items(open_items=True)
        for item in items:
            due = [s for s in self._sources if self._due(_fetch_job(item, s), now, FETCH_EVERY)]
            if due:
                fetch_listings(self._store, due, self._judge, item, now)
        if self._due("sold", now, SOLD_REFRESH_EVERY) and self._refresh_sold(items):
            self._store.set_last_run("sold", now)
        if self._outbox is None:
            return
        for item in items:
            self._send_alerts(self._outbox, item, now)
        if now.astimezone(PACIFIC).time() >= DIGEST_TIME:
            self._send_digests(self._outbox, items, now)

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
        """Every listing ever seen for the item that Claude didn't reject, priced in full,
        cheapest first."""
        priced = [
            _Priced(r, self._pricer.listing(item.fields, r.listing))
            for r in self._store.listings(item.id, include_ended=True)
            if r.rejection is None
        ]
        return sorted(priced, key=lambda p: p.price.total_cents)

    def _send_alerts(self, outbox: Outbox, item: Item, now: datetime) -> None:
        priced = self._priced(item)
        if not priced:
            return
        sale_totals = [
            price.total_cents
            for r in self._store.sales(item.id)
            if (price := self._pricer.sale(item.fields, r.sale)).complete
        ]
        for recipient in outbox.watchers(item):
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
                "item_url": outbox.item_url(item),
            }
            if outbox.send(recipient, subject, "alert", context):
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
        shipping or tax can't be at target or a new low: it's only a lower bound. Nor can an
        auction's bid, which will rise; an auction alerts only when it ends before the next
        digest could remind them of it."""
        noticed = {p.listing.id: self._store.noticed(p.listing.id, user.id) for p in priced}
        buy_now = [p for p in priced if not p.is_auction]
        active = [p for p in buy_now if p.listing.ended_at is None]
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
                for q in buy_now
                if q.listing.first_seen < p.listing.first_seen and q.price.complete
            ] + sale_totals
            if (
                len(earlier) >= NEW_LOW_MIN_DATA_POINTS
                and p.price.total_cents < min(earlier)
                and NoticeKind.NEW_LOW not in seen
            ):
                reasons.append(_Reason(NoticeKind.NEW_LOW, p.listing, p.price))
        for reminder in self._ending_soon(item, user, priced, now, self._next_digest(user, now)):
            p = reminder.priced
            reasons.append(_Reason(NoticeKind.ENDING_SOON, p.listing, p.price, reminder.note))
        return reasons

    def _ending_soon(
        self, item: Item, user: User, priced: list[_Priced], now: datetime, before: datetime
    ) -> list[_Reminder]:
        """Auctions ending before `before` that are still worth a bid, and that this person
        hasn't been reminded of. With nothing to compare a bid with, every one is."""
        limit = self._bid_limit(item, priced)
        return [
            _Reminder(p, self._reminder_note(item, p, limit))
            for p in priced
            if p.listing.ended_at is None
            and (ends := p.listing.listing.auction_ends_at) is not None
            and now < ends < before
            and (limit is None or p.price.total_cents <= limit.cents)
            and NoticeKind.ENDING_SOON not in self._store.noticed(p.listing.id, user.id)
        ]

    def _bid_limit(self, item: Item, priced: list[_Priced]) -> _BidLimit | None:
        """The target, or else just under the cheapest Buy It Now listing's full price."""
        target = item.fields.target_price_cents
        if target is not None:
            return _BidLimit(target, f"to stay within your ${format_dollars(target)} target")
        buy_now = [
            p.price.total_cents
            for p in priced
            if not p.is_auction and p.listing.ended_at is None and p.price.complete
        ]
        if not buy_now:
            return None
        cheapest = min(buy_now)
        return _BidLimit(
            cheapest - 1, f"to beat the cheapest Buy It Now, ${format_dollars(cheapest)} all-in"
        )

    def _reminder_note(self, item: Item, p: _Priced, limit: _BidLimit | None) -> str:
        """'auction, 3 bids, ends ..., bid checked .... Bid up to $46.03 to ...'. The bid is
        from the last search, up to a day old."""
        listing = p.listing.listing
        note = f"{describe_auction(listing)}, bid checked {describe_time(p.listing.last_seen)}."
        rate = self._pricer.listing_tax_rate(item.fields, listing)
        if limit is None or p.price.shipping_cents is None or rate is None:
            return note
        bid = max_bid_cents(limit.cents, p.price.shipping_cents, rate)
        about = "about " if p.price.shipping_estimated else ""
        return f"{note} Bid up to {about}${format_dollars(bid)} {limit.why}."

    def _next_digest(self, user: User, now: datetime) -> datetime:
        """The latest this person's next digest can go out: today's, until they've had it.
        Today's may be past, when it goes out this tick."""
        today = now.astimezone(PACIFIC).date()
        return _digest_due_by(today + timedelta(days=1) if self._had_digest(user, today) else today)

    def _had_digest(self, user: User, day: date) -> bool:
        last = self._store.last_run(_digest_job(user))
        return last is not None and last.astimezone(PACIFIC).date() >= day

    def _send_digests(self, outbox: Outbox, items: list[Item], now: datetime) -> None:
        """Today's digest for everyone who hasn't had one yet today. Each person's day is
        tracked on its own, so one failed send can't double anyone else's digest."""
        today = now.astimezone(PACIFIC).date()
        for recipient in outbox.everyone():
            if self._had_digest(recipient.user, today):
                continue
            before = _digest_due_by(today + timedelta(days=1))
            entries = self._digest_entries(outbox, recipient.user, items, now, before)
            if not entries:
                continue
            subject = _digest_subject(entries)
            context: dict[str, object] = {"subject": subject, "entries": entries}
            if not outbox.send(recipient, subject, "digest", context):
                continue
            self._store.set_last_run(_digest_job(recipient.user), now)
            for entry in entries:
                for line in entry.lines + entry.ending:
                    self._store.log_notice(
                        line.listing.id, recipient.user.id, NoticeKind.DIGEST, now
                    )
                for line in entry.ending:
                    self._store.log_notice(
                        line.listing.id, recipient.user.id, NoticeKind.ENDING_SOON, now
                    )

    def _digest_entries(
        self, outbox: Outbox, user: User, items: list[Item], now: datetime, before: datetime
    ) -> list[_DigestEntry]:
        """Per item, auctions ending before `before`, then new listings: ones this person
        hasn't heard about in any way yet."""
        entries: list[_DigestEntry] = []
        for item in items:
            if user.id not in item.fields.watcher_ids:
                continue
            priced = self._priced(item)
            reminders = self._ending_soon(item, user, priced, now, before)
            ending = [_DigestLine(r.priced.listing, r.priced.price, r.note) for r in reminders]
            reminded = {r.priced.listing.id for r in reminders}
            totals = [p.price.total_cents for p in priced if not p.is_auction]
            lines = [
                _DigestLine(
                    p.listing,
                    p.price,
                    describe_auction(p.listing.listing) or _rank(p.price.total_cents, totals),
                )
                for p in priced
                if p.listing.ended_at is None
                and not _auction_over(p.listing.listing, now)
                and p.listing.id not in reminded
                and not self._store.noticed(p.listing.id, user.id)
            ]
            if lines or ending:
                entries.append(_DigestEntry(item, outbox.item_url(item), lines, ending))
        return entries


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


def _auction_over(listing: Listing, now: datetime) -> bool:
    """Past its end time, though still in the last search."""
    return listing.auction_ends_at is not None and listing.auction_ends_at <= now


def _digest_job(user: User) -> str:
    return f"digest:{user.id}"


def _digest_due_by(day: date) -> datetime:
    return datetime.combine(day, DIGEST_TIME, tzinfo=PACIFIC) + DIGEST_LATENESS


def _rank(total: int, totals: list[int]) -> str:
    """'cheapest of 14 seen, median $62.00', or '#3 of 14 seen, ...'."""
    place = sum(1 for t in totals if t < total) + 1
    where = "cheapest" if place == 1 else f"#{place}"
    return f"{where} of {len(totals)} seen, median ${format_dollars(round(median(totals)))}"


def _digest_subject(entries: list[_DigestEntry]) -> str:
    """'Buy Assistant digest: 2 new listings, 1 auction ending soon'."""
    new = sum(len(entry.lines) for entry in entries)
    ending = sum(len(entry.ending) for entry in entries)
    parts = [f"{new} new listing{'' if new == 1 else 's'}"] if new else []
    if ending:
        parts.append(f"{ending} auction{'' if ending == 1 else 's'} ending soon")
    return f"Buy Assistant digest: {', '.join(parts)}"
