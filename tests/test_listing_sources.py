import httpx2
import pytest

from buy_assist.bgg import BggApi
from buy_assist.listings import GeekMarketListings, ListingError
from buy_assist.models import ItemFields
from tests.fixtures import BGG_URL, recorded_bgg_api


def geekmarket(handler: httpx2.MockTransport) -> GeekMarketListings:
    return GeekMarketListings(BggApi(httpx2.Client(transport=handler), sleep=lambda s: None))


GAME = ItemFields(name="Calendar", reference_url=BGG_URL)


def test_item_without_edition_gets_every_us_listing() -> None:
    listings = geekmarket(httpx2.MockTransport(recorded_bgg_api)).listings_for(GAME, "94114")
    assert len(listings) == 4


@pytest.mark.parametrize("variant", ["English edition", "english edition"])
def test_listings_naming_another_edition_are_left_out(variant: str) -> None:
    item = ItemFields(name="Calendar", reference_url=BGG_URL, variant=variant)
    listings = geekmarket(httpx2.MockTransport(recorded_bgg_api)).listings_for(item, "94114")
    assert sorted(listing.edition or "" for listing in listings) == [
        "",
        "",
        "English edition",
        "English edition",
    ]


def test_year_suffix_on_edition_still_matches() -> None:
    item = ItemFields(name="Calendar", reference_url=BGG_URL, variant="Dutch edition (2025)")
    listings = geekmarket(httpx2.MockTransport(recorded_bgg_api)).listings_for(item, "94114")
    assert all(listing.edition != "English edition" for listing in listings)


def test_only_bgg_game_items_are_covered() -> None:
    source = geekmarket(httpx2.MockTransport(recorded_bgg_api))
    assert source.covers(GAME)
    assert not source.covers(ItemFields(name="Pan", reference_url="https://shop.example/pan"))


def test_bgg_failure_is_listing_error() -> None:
    failing = httpx2.MockTransport(lambda request: httpx2.Response(503))
    with pytest.raises(ListingError, match="503"):
        geekmarket(failing).listings_for(GAME, "94114")
