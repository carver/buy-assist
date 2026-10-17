import httpx2
import pytest

from buy_assist.bgg import BggApi
from buy_assist.models import ItemFields
from buy_assist.sold import BggSoldPrices, SoldPriceError
from tests.fixtures import BGG_URL, recorded_bgg_api

GAME = ItemFields(name="Calendar", reference_url=BGG_URL)
PAN = ItemFields(name="Pan", reference_url="https://shop.example/pan")


def source(handler: httpx2.MockTransport) -> BggSoldPrices:
    return BggSoldPrices(BggApi(httpx2.Client(transport=handler), sleep=lambda seconds: None))


def test_covers_only_items_linking_a_bgg_game() -> None:
    sold = source(httpx2.MockTransport(recorded_bgg_api))
    assert sold.covers(GAME)
    assert not sold.covers(PAN)
    assert not sold.covers(ItemFields(name="Cardigan"))


def test_sales_for_bgg_game_are_its_geekmarket_sales() -> None:
    sales = source(httpx2.MockTransport(recorded_bgg_api)).sales_for(GAME)
    assert len(sales) == 5


def test_item_it_does_not_cover_has_no_sales() -> None:
    assert source(httpx2.MockTransport(recorded_bgg_api)).sales_for(PAN) == []


def test_bgg_failure_is_sold_price_error() -> None:
    failing = httpx2.MockTransport(lambda request: httpx2.Response(503))
    with pytest.raises(SoldPriceError, match="503"):
        source(failing).sales_for(GAME)
