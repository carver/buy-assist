"""Fakes for the web app's collaborators, and a filled-in item form."""

from datetime import date

from buy_assist.claude import ParseError
from buy_assist.links import LinkError, LinkFacts
from buy_assist.models import Condition, Edition, ItemFields, Marketplace, Sale
from buy_assist.paste import PastedSale
from buy_assist.sold import SoldPriceError
from tests.fixtures import BGG_URL, EXIT_NAME

IMAGE_URL = "https://cf.geekdo-images.com/pic6632318.jpg"
ENGLISH = Edition(
    name="English edition",
    title=EXIT_NAME,
    product_code="681951",
    year="2022",
    languages=("English",),
    publishers=("KOSMOS",),
)
GERMAN = Edition(
    name="German edition",
    title="EXIT: Das Spiel - Adventskalender",
    product_code=None,
    year="2021",
    languages=("German",),
    publishers=("KOSMOS",),
)
BGG_FACTS = LinkFacts(
    source="BoardGameGeek", name=EXIT_NAME, image_url=IMAGE_URL, editions=(GERMAN, ENGLISH)
)


class FakeParser:
    def __init__(self) -> None:
        self.requests: list[tuple[str, LinkFacts | None]] = []
        self.fail = False

    def parse(self, text: str, link: LinkFacts | None = None) -> ItemFields:
        self.requests.append((text, link))
        if self.fail:
            raise ParseError("claude-json exited 1")
        return ItemFields(
            name=EXIT_NAME,
            condition=Condition.NEW,
            reference_url=BGG_URL,
            image_url=link.image_url if link else None,
            variant="English edition" if link and ENGLISH in link.editions else None,
            model_number="681951" if link and link.editions else None,
            target_price_cents=3500,
        )


class FakeLinkReader:
    def __init__(self) -> None:
        self.urls: list[str] = []
        self.error: str | None = None
        self.facts = BGG_FACTS

    def read(self, url: str) -> LinkFacts:
        self.urls.append(url)
        if self.error:
            raise LinkError(self.error)
        return self.facts


NEW_SALE = Sale(
    source=Marketplace.BGG,
    source_id="2070310",
    sold_on=date(2025, 11, 18),
    price_cents=4000,
    is_new=True,
    condition="New",
    title="New in excellent condition",
)
USED_SALE = Sale(
    source=Marketplace.BGG,
    source_id="1802529",
    sold_on=date(2023, 5, 4),
    price_cents=3500,
    is_new=False,
    condition="Very Good",
    title="Played and fully reset",
)


class FakeSoldPrices:
    label = "BoardGameGeek"

    def __init__(self) -> None:
        self.items: list[ItemFields] = []
        self.sales = [NEW_SALE, USED_SALE]
        self.error: str | None = None

    def covers(self, item: ItemFields) -> bool:
        return item.reference_url == BGG_URL

    def sales_for(self, item: ItemFields) -> list[Sale]:
        self.items.append(item)
        if self.error:
            raise SoldPriceError(self.error)
        return self.sales


PASTED_MATCH = PastedSale(
    sale=Sale(
        source=Marketplace.EBAY,
        sold_on=date(2025, 10, 12),
        price_cents=3299,
        shipping_cents=585,
        is_new=True,
        condition="Brand New",
        title="EXIT Advent Calendar Golden Book New Sealed",
    ),
    matches=True,
    reason="",
)
PASTED_DOUBT = PastedSale(
    sale=Sale(
        source=Marketplace.EBAY,
        sold_on=date(2025, 9, 3),
        price_cents=400,
        shipping_cents=125,
        is_new=False,
        condition="Pre-Owned",
        title="EXIT Advent Calendar REPLACEMENT PARTS ONLY",
    ),
    matches=False,
    reason="Replacement parts, not the game",
)


class FakeSalesParser:
    def __init__(self) -> None:
        self.pastes: list[tuple[str, ItemFields]] = []
        self.fail = False

    def parse(self, text: str, item: ItemFields) -> list[PastedSale]:
        self.pastes.append((text, item))
        if self.fail:
            raise ParseError("claude-json exited 1")
        return [PASTED_MATCH, PASTED_DOUBT]


def item_form(**overrides: str | list[str]) -> dict[str, str | list[str]]:
    form: dict[str, str | list[str]] = {
        "name": EXIT_NAME,
        "condition": "new",
        "upc": "",
        "epid": "",
        "model_number": "",
        "reference_url": BGG_URL,
        "search_query": "",
        "weight_lb": "",
        "box_in": "",
        "variant": "",
        "zip": "94110",
        "target_price": "35",
        "notes": "",
        "watcher_ids": ["2"],
    }
    return form | overrides
