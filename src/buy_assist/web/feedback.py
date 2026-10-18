"""Feedback on an item's listings: "not this", putting a filtered one back, and "bought"."""

import logging
from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from buy_assist.claude import ParseError
from buy_assist.forms import format_dollars, parse_dollars
from buy_assist.models import SALE_TITLE_LIMIT, Item, Purchase, RecordedListing, User
from buy_assist.outbox import Outbox
from buy_assist.store import Store
from buy_assist.web.pages import ExistingItem, Me, Services, ServicesDep, StoreDep, item_page

router = APIRouter()
_log = logging.getLogger(__name__)


def _listing_of(store: Store, item: Item, listing_id: int) -> RecordedListing:
    listing = store.get_listing(item.id, listing_id)
    if listing is None:
        raise HTTPException(404, "No such listing for this item")
    return listing


def _back_to(item: Item) -> Response:
    return RedirectResponse(f"/items/{item.id}", status_code=303)


def _refused_when_closed(request: Request, me: User, item: Item) -> HTMLResponse | None:
    """Feedback is for open items; a closed one's page says so instead."""
    if item.is_open:
        return None
    return item_page(request, me, item, ["This item is closed."], status_code=422)


@router.post("/items/{item_id}/listings/{listing_id}/not-this")
def not_this(
    request: Request,
    store: StoreDep,
    services: ServicesDep,
    me: Me,
    item: ExistingItem,
    listing_id: int,
    reason: Annotated[str, Form()] = "",
) -> Response:
    """Filter a listing out. A reason goes in the item's notes, and Claude checks the
    item's other listings again with them."""
    listing = _listing_of(store, item, listing_id)
    if refused := _refused_when_closed(request, me, item):
        return refused
    reason = " ".join(reason.split())[:SALE_TITLE_LIMIT]
    store.reject_listing(listing.id, reason or "Not this", by=me.id)
    if not reason:
        return _back_to(item)
    notes = "\n".join(n for n in (item.fields.notes.rstrip(), f"Not this: {reason}") if n)
    item = store.update_item(item.id, replace(item.fields, notes=notes))
    return item_page(request, me, item, _check_again(store, services, item))


def _check_again(store: Store, services: Services, item: Item) -> list[str]:
    """Claude's check of the item's current listings from each source that needs one,
    against the item as it now stands. Listings a person put back are left alone."""
    messages: list[str] = []
    for source in services.listing_sources:
        if not source.needs_judging:
            continue
        candidates = [
            r
            for r in store.listings(item.id)
            if r.listing.marketplace is source.marketplace and r.rejection is None and not r.kept
        ]
        if not candidates:
            continue
        try:
            rejections = services.judge.rejections(item.fields, [r.listing for r in candidates])
        except ParseError as e:
            _log.warning("Checking %s listings for item %d again: %s", source.label, item.id, e)
            messages.append("Claude couldn't check the other listings again, so they're unchanged.")
            continue
        for r in candidates:
            if (why := rejections.get(r.listing.external_id)) is not None:
                store.reject_listing(r.id, why, by=None)
        count = len(candidates)
        messages.append(
            f"Claude checked the {count} other {source.label}"
            f" listing{'' if count == 1 else 's'} again and filtered out {len(rejections)}."
        )
    return messages


@router.post("/items/{item_id}/listings/{listing_id}/put-back")
def put_back(
    request: Request, store: StoreDep, me: Me, item: ExistingItem, listing_id: int
) -> Response:
    listing = _listing_of(store, item, listing_id)
    if refused := _refused_when_closed(request, me, item):
        return refused
    store.put_back_listing(listing.id)
    return _back_to(item)


@router.post("/items/{item_id}/bought")
def bought(
    request: Request,
    store: StoreDep,
    services: ServicesDep,
    me: Me,
    item: ExistingItem,
    price: Annotated[str, Form()] = "",
    listing_id: Annotated[int | None, Form()] = None,
) -> Response:
    """Record what the item cost all-in, from one of its listings or elsewhere, close it,
    and tell its other watchers so nobody buys a second."""
    listing = None if listing_id is None else _listing_of(store, item, listing_id)
    if refused := _refused_when_closed(request, me, item):
        return refused
    price_cents = parse_dollars(price)
    if not price_cents:
        message = "Enter what you paid, all-in, like 42.50."
        return item_page(request, me, item, [message], status_code=422)
    purchase = Purchase(price_cents, me.id, listing.id if listing else None)
    item = store.buy_item(item.id, purchase)
    failed = _tell_watchers(store, services, item, me, listing)
    if not failed:
        return _back_to(item)
    return item_page(
        request, me, item, [f"Couldn't email {name} that it's bought." for name in failed]
    )


def _tell_watchers(
    store: Store, services: Services, item: Item, buyer: User, listing: RecordedListing | None
) -> list[str]:
    """Email the item's other watchers that it's bought. Returns who couldn't be told."""
    if services.mailer is None or item.purchase is None:
        return []
    outbox = Outbox(store, services.mailer)
    subject = f"Bought: {item.fields.name}"
    context: dict[str, object] = {
        "subject": subject,
        "item": item.fields,
        "buyer": buyer.name,
        "price": format_dollars(item.purchase.price_cents),
        "listing": listing.listing if listing else None,
        "item_url": outbox.item_url(item),
    }
    return [
        r.user.name
        for r in outbox.watchers(item)
        if r.user.id != buyer.id and not outbox.send(r, subject, "bought", context)
    ]
