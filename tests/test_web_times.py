"""Times on web pages show in the browser's time zone, which app.js keeps in a cookie."""

import re
from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from buy_assist.models import Marketplace, Purchase
from buy_assist.store import Store
from tests.test_web_watch import SEEN, add_item, bgg_listing, ebay_listing

GONE = datetime(2026, 10, 20, tzinfo=UTC)


def item_with_history(client: TestClient, store: Store) -> int:
    """An auction ending Oct 20 03:15 UTC, and a listing seen Oct 17 18:00 UTC and gone
    Oct 20 00:00 UTC."""
    item_id = add_item(client)
    auction = replace(
        ebay_listing("1", 12), auction_ends_at=datetime(2026, 10, 20, 3, 15, tzinfo=UTC)
    )
    store.record_listings(item_id, Marketplace.BGG, [bgg_listing("2", 40)], SEEN)
    store.record_listings(item_id, Marketplace.BGG, [], GONE)
    store.record_listings(item_id, Marketplace.EBAY, [auction], GONE)
    return item_id


def test_times_show_in_the_browsers_zone(member: TestClient, store: Store) -> None:
    item_id = item_with_history(member, store)
    member.cookies.set("tz", "Asia/Tokyo")

    page = member.get(f"/items/{item_id}").text

    assert "ends Tue Oct 20, 12:15pm" in page
    assert "listed Oct 18, gone Oct 20" in page
    assert "seen since Oct 20" in page
    assert 'data-tz="Asia/Tokyo"' in page


def test_old_zone_names_that_browsers_still_report_work(member: TestClient, store: Store) -> None:
    item_id = item_with_history(member, store)
    member.cookies.set("tz", "Asia/Calcutta")  # Chrome's name for Asia/Kolkata

    assert "ends Tue Oct 20, 8:45am" in member.get(f"/items/{item_id}").text


@pytest.mark.parametrize("cookie", [None, "Mars/Olympus_Mons", "../../etc/passwd", ""])
def test_times_default_to_pacific(member: TestClient, store: Store, cookie: str | None) -> None:
    item_id = item_with_history(member, store)
    if cookie is not None:
        member.cookies.set("tz", cookie)

    page = member.get(f"/items/{item_id}").text

    assert "ends Mon Oct 19, 8:15pm" in page
    assert "listed Oct 17, gone Oct 19" in page
    assert "seen since Oct 19" in page
    assert 'data-tz="America/Los_Angeles"' in page


@pytest.mark.parametrize("bought", [True, False])
def test_closed_and_bought_dates_are_the_browsers_date(
    member: TestClient, store: Store, bought: bool
) -> None:
    item_id = add_item(member)
    if bought:
        store.buy_item(item_id, Purchase(price_cents=4000, buyer_id=1, listing_id=None))
    else:
        store.close_item(item_id)
    item = store.get_item(item_id)
    assert item and item.closed_at
    # 25 hours apart, so the two always see different dates.
    zones = ["Pacific/Kiritimati", "Pacific/Pago_Pago"]
    days = [item.closed_at.astimezone(ZoneInfo(z)).strftime("%b %-d, %Y") for z in zones]
    assert days[0] != days[1]

    for zone, day in zip(zones, days, strict=True):
        member.cookies.set("tz", zone)
        notice = re.search(r'<p class="notice">(.*?)</p>', member.get(f"/items/{item_id}").text)
        assert notice and day in notice.group(1)
