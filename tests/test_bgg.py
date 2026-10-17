import json
from collections.abc import Callable
from datetime import date

import httpx2
import pytest

from buy_assist.bgg import BggApi, BggError, game_id
from buy_assist.models import Listing, Marketplace, Sale
from tests.fixtures import BGG_URL, DATA, FakeClock, recorded_bgg_api

EXIT_TITLE = "EXIT: The Game \u2013 Advent Calendar: The Hunt for the Golden Book"


def api(
    handler: Callable[[httpx2.Request], httpx2.Response] = recorded_bgg_api,
    clock: FakeClock | None = None,
) -> BggApi:
    clock = clock or FakeClock()
    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    return BggApi(client, clock=clock.time, sleep=clock.sleep)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (BGG_URL, "343322"),
        ("https://boardgamegeek.com/boardgame/13", "13"),
        ("https://www.boardgamegeek.com/boardgameexpansion/2807/catan-seafarers", "2807"),
        ("https://boardgamegeek.com/forum/123/whatever", None),
        ("https://shop.example/boardgame/13", None),
    ],
)
def test_game_id_comes_from_bgg_game_links_only(url: str, expected: str | None) -> None:
    assert game_id(url) == expected


def test_sales_are_usd_geekmarket_sales_newest_first() -> None:
    sales = api().sales("343322")

    assert len(sales) == 5
    assert sales[0] == Sale(
        source=Marketplace.BGG,
        source_id="2070310",
        sold_on=date(2025, 11, 18),
        price_cents=4000,
        is_new=True,
        condition="New",
        title="New in excellent condition",
    )
    assert [s.is_new for s in sales] == [True, True, True, False, False]
    assert all(s.shipping_cents is None for s in sales)


def test_sales_in_other_currencies_are_skipped() -> None:
    history = json.loads((DATA / "bgg_pricehistory_343322.json").read_text())
    history["items"][0]["currency"] = "EUR"

    def answer(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=history)

    assert len(api(answer).sales("343322")) == 4


def test_unparseable_sale_is_skipped() -> None:
    history = json.loads((DATA / "bgg_pricehistory_343322.json").read_text())
    history["items"][1]["price"] = "a lot"
    history["items"][2]["usaledate"] = "someday"

    def answer(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=history)

    assert len(api(answer).sales("343322")) == 3


def test_all_bgg_calls_share_one_five_second_gap() -> None:
    clock = FakeClock()
    bgg = api(clock=clock)

    bgg.game("343322")
    bgg.editions("343322")
    bgg.sales("343322")

    assert clock.sleeps == [5.0, 5.0]


@pytest.mark.parametrize("status", [404, 429, 503])
def test_bgg_error_status_is_bgg_error(status: int) -> None:
    with pytest.raises(BggError):
        api(lambda request: httpx2.Response(status)).sales("343322")


@pytest.mark.live
def test_live_bgg_sales_for_fixture_game() -> None:
    sales = BggApi(httpx2.Client(timeout=20)).sales("343322")
    assert len(sales) >= 5
    assert all(s.price_cents > 0 for s in sales)


def test_editions_carry_box_size_and_weight_when_bgg_has_them() -> None:
    editions = {e.name: e for e in api().editions("343322")}

    dutch = editions["Dutch edition (2025)"]
    assert (dutch.length_in, dutch.width_in, dutch.depth_in) == (17.7559, 12.9134, 2.12598)
    assert dutch.weight_lb == 1.87393
    assert editions["French edition"].length_in is None


def test_edition_without_weight_borrows_it_from_an_edition_with_the_same_box() -> None:
    editions = {e.name: e for e in api().editions("343322")}

    assert editions["English edition"].weight_lb == 1.87393
    assert editions["French edition"].weight_lb is None


def test_us_listings_are_active_usd_listings_located_in_the_us() -> None:
    listings = api().us_listings("343322")

    assert len(listings) == 4
    assert [listing.price_cents for listing in listings] == [3500, 4000, 4000, 4000]
    by_id = {listing.external_id: listing for listing in listings}
    assert by_id["3851948"] == Listing(
        marketplace=Marketplace.BGG,
        external_id="3851948",
        title=f"{EXIT_TITLE} (English edition)",
        url="https://boardgamegeek.com/market/product/3851948",
        price_cents=4000,
        is_new=True,
        condition="New",
        edition="English edition",
        origin_state="Louisiana",
    )
    assert {listing.condition for listing in listings} == {"New", "Very Good", "Like New"}
