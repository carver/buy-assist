"""eBay Browse search, against a response shaped from eBay's OpenAPI field names. It isn't a
captured response: there are no keys yet (see docs/research/apis.md)."""

import base64
import json
from dataclasses import replace
from datetime import UTC, datetime

import httpx2
import pytest

from buy_assist.ebay import EbayApi
from buy_assist.listings import EbayListings, ListingError
from buy_assist.models import Condition, ItemFields, Listing, Marketplace
from tests.fixtures import DATA

SEARCH = (DATA / "ebay_search_exit.json").read_bytes()
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
ITEM = ItemFields(name="EXIT calendar", search_query="EXIT advent calendar golden book")


class FakeEbay:
    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.search_status: list[int] = []
        self.now = 1000.0
        self.body = SEARCH

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if str(request.url) == TOKEN_URL:
            body = {"access_token": f"token-{len(self.tokens())}", "expires_in": 7200}
            return httpx2.Response(200, json=body)
        status = self.search_status.pop(0) if self.search_status else 200
        return httpx2.Response(status, content=self.body if status == 200 else b"{}")

    def tokens(self) -> list[httpx2.Request]:
        return [r for r in self.requests if str(r.url) == TOKEN_URL]

    def searches(self) -> list[httpx2.Request]:
        return [r for r in self.requests if str(r.url).startswith(SEARCH_URL)]

    def listings(self) -> EbayListings:
        client = httpx2.Client(transport=httpx2.MockTransport(self.handle))
        return EbayListings(EbayApi(client, "app-id", "app-secret", clock=lambda: self.now))


@pytest.fixture
def ebay() -> FakeEbay:
    return FakeEbay()


def test_search_results_become_us_dollar_listings(ebay: FakeEbay) -> None:
    listings = ebay.listings().listings_for(ITEM, "94114")

    assert listings == [
        Listing(
            marketplace=Marketplace.EBAY,
            external_id="v1|335511112222|0",
            title="KOSMOS EXIT Advent Calendar Hunt for the Golden Book",
            url="https://www.ebay.com/itm/335511112222",
            price_cents=1250,
            is_new=True,
            condition="New",
            shipping_cents=0,
            origin_zip3="981",
            auction_ends_at=datetime(2026, 10, 20, 3, 15, tzinfo=UTC),
            bid_count=3,
        ),
        Listing(
            marketplace=Marketplace.EBAY,
            external_id="v1|125598765432|0",
            title="Exit Advent Calendar Golden Book - played once, all pieces",
            url="https://www.ebay.com/itm/125598765432",
            price_cents=1800,
            is_new=False,
            condition="Used",
            shipping_cents=None,
            origin_zip3="606",
        ),
        Listing(
            marketplace=Marketplace.EBAY,
            external_id="v1|335533334444|0",
            title="EXIT Advent Calendar Golden Book auction or buy now",
            url="https://www.ebay.com/itm/335533334444",
            price_cents=3000,
            is_new=True,
            condition="New",
            shipping_cents=525,
        ),
        Listing(
            marketplace=Marketplace.EBAY,
            external_id="v1|205512345678|0",
            title="EXIT The Game Advent Calendar The Hunt for the Golden Book KOSMOS New Sealed",
            url="https://www.ebay.com/itm/205512345678",
            price_cents=3299,
            is_new=True,
            condition="New",
            shipping_cents=645,
            origin_zip3="940",
        ),
    ]


def test_auction_without_an_end_time_is_left_out(ebay: FakeEbay) -> None:
    answer = json.loads(SEARCH)
    auction = next(s for s in answer["itemSummaries"] if s["buyingOptions"] == ["AUCTION"])
    del auction["itemEndDate"]
    ebay.body = json.dumps(answer).encode()

    listings = ebay.listings().listings_for(ITEM, "94114")

    assert auction["itemId"] not in {listing.external_id for listing in listings}
    assert len(listings) == 3


def test_search_asks_for_us_listings_quoted_to_the_ship_to_zip(ebay: FakeEbay) -> None:
    ebay.listings().listings_for(replace(ITEM, condition=Condition.NEW), "94110")

    [search] = ebay.searches()
    params = search.url.params
    assert params["q"] == "EXIT advent calendar golden book"
    assert params["limit"] == "200"
    assert params["sort"] == "price"
    assert params["filter"].split(",") == [
        "buyingOptions:{FIXED_PRICE|BEST_OFFER|AUCTION}",
        "itemLocationCountry:US",
        "deliveryCountry:US",
        "deliveryPostalCode:94110",
        "conditions:{NEW}",
    ]
    assert search.headers["x-ebay-c-enduserctx"] == "contextualLocation=country%3DUS%2Czip%3D94110"
    assert search.headers["x-ebay-c-marketplace-id"] == "EBAY_US"
    assert search.headers["authorization"] == "Bearer token-1"


def test_upc_or_epid_searches_by_product_not_words(ebay: FakeEbay) -> None:
    source = ebay.listings()
    source.listings_for(ItemFields(name="Pan", upc="012345678905", epid="123"), None)
    source.listings_for(ItemFields(name="Pan", epid="2254276049"), None)

    by_upc, by_epid = (s.url.params for s in ebay.searches())
    assert (by_upc.get("gtin"), by_upc.get("epid"), by_upc.get("q")) == ("012345678905", None, None)
    assert (by_epid.get("epid"), by_epid.get("q")) == ("2254276049", None)
    assert "deliveryPostalCode" not in by_upc["filter"]


def test_token_is_fetched_once_and_reused_until_it_nearly_expires(ebay: FakeEbay) -> None:
    source = ebay.listings()
    source.listings_for(ITEM, None)
    ebay.now += 3600
    source.listings_for(ITEM, None)
    ebay.now += 3550
    source.listings_for(ITEM, None)

    [first, second] = ebay.tokens()
    expected = base64.b64encode(b"app-id:app-secret").decode()
    assert first.headers["authorization"] == f"Basic {expected}"
    assert first.content.decode() == (
        "grant_type=client_credentials&scope=https%3A%2F%2Fapi.ebay.com%2Foauth%2Fapi_scope"
    )
    assert second is not first


def test_rejected_token_is_replaced_once(ebay: FakeEbay) -> None:
    ebay.search_status = [401]

    assert ebay.listings().listings_for(ITEM, None)
    assert len(ebay.tokens()) == 2


@pytest.mark.parametrize(("status", "message"), [(500, "eBay answered 500."), (401, "eBay")])
def test_failed_search_is_listing_error(ebay: FakeEbay, status: int, message: str) -> None:
    ebay.search_status = [status, status, status]

    with pytest.raises(ListingError, match=message):
        ebay.listings().listings_for(ITEM, None)


def test_unexpected_answer_is_listing_error() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == TOKEN_URL:
            return httpx2.Response(200, json={"access_token": "t", "expires_in": 7200})
        return httpx2.Response(200, content=json.dumps({"itemSummaries": "nope"}).encode())

    client = httpx2.Client(transport=httpx2.MockTransport(handle))
    with pytest.raises(ListingError, match="wasn't what this app expects"):
        EbayListings(EbayApi(client, "a", "b")).listings_for(ITEM, None)


@pytest.mark.live
def test_live_search_finds_the_fixture() -> None:
    api = EbayApi.from_env(httpx2.Client(timeout=20))
    if api is None:
        pytest.skip("EBAY_CLIENT_ID and EBAY_CLIENT_SECRET aren't set")

    listings = EbayListings(api).listings_for(ITEM, "94114")

    assert all(listing.url.startswith("https://www.ebay.com/") for listing in listings)
