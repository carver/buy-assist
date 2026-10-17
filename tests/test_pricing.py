from dataclasses import replace
from datetime import date
from pathlib import Path

import httpx2
import pytest

from buy_assist.models import ItemFields, Listing, Marketplace
from buy_assist.pricing import Pricer, UspsQuoter
from buy_assist.store import Store
from buy_assist.usps import Origin
from tests.fixtures import DATA

EXIT_BOX = ItemFields(name="Calendar", weight_oz=30, length_in=17.76, width_in=12.91, depth_in=2.13)
CHART = (DATA / "usps_zone_chart_941.json").read_bytes()


class Usps:
    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.status = 200

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(self.status, content=CHART)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    store = Store.open(tmp_path / "buy.db")
    store.set_home_zip("94114")
    return store


def quoter(store: Store, usps: Usps, today: date = date(2026, 10, 17)) -> UspsQuoter:
    return UspsQuoter(store, httpx2.Client(transport=httpx2.MockTransport(usps)), lambda: today)


def test_quote_uses_zone_chart_for_home_zip_prefix(store: Store) -> None:
    usps = Usps()
    estimate = quoter(store, usps).quote(EXIT_BOX, Origin(state="Louisiana"))

    assert estimate is not None
    assert estimate.typical_cents == 1630
    assert usps.requests[0].url.params["zipCode3Digit"] == "941"


def test_item_zip_beats_home_zip(store: Store) -> None:
    usps = Usps()
    quoter(store, usps).quote(replace(EXIT_BOX, zip="10001"), Origin())
    assert usps.requests[0].url.params["zipCode3Digit"] == "100"


def test_zone_chart_is_fetched_once_a_month(store: Store) -> None:
    usps = Usps()
    quoter(store, usps).quote(EXIT_BOX, Origin())
    quoter(store, usps, date(2026, 11, 1)).quote(EXIT_BOX, Origin())
    assert len(usps.requests) == 1

    quoter(store, usps, date(2026, 11, 20)).quote(EXIT_BOX, Origin())
    assert len(usps.requests) == 2


def test_stale_chart_still_serves_when_usps_is_down(store: Store) -> None:
    usps = Usps()
    quoter(store, usps).quote(EXIT_BOX, Origin())
    usps.status = 503

    estimate = quoter(store, usps, date(2026, 12, 1)).quote(EXIT_BOX, Origin())

    assert estimate is not None


def test_item_without_weight_has_no_quote(store: Store) -> None:
    assert quoter(store, Usps()).quote(ItemFields(name="Calendar"), Origin()) is None


def test_listing_price_uses_home_zip_tax_only_where_marketplace_collects_it(store: Store) -> None:
    pricer = Pricer(store, quoter(store, Usps()))
    listing = Listing(
        marketplace=Marketplace.EBAY,
        external_id="1",
        title="Calendar",
        url="https://www.ebay.com/itm/1",
        price_cents=4000,
        shipping_cents=500,
        is_new=True,
        condition="New",
    )

    ebay = pricer.listing(EXIT_BOX, listing)
    bgg = pricer.listing(EXIT_BOX, replace(listing, marketplace=Marketplace.BGG))

    assert ebay.tax_cents == 345
    assert bgg.tax_cents == 0
