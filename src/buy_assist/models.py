"""Domain types shared by the store, the parser and the web app."""

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import StrEnum
from urllib.parse import urlsplit

_ZIP = re.compile(r"\d{5}")
_UPC = re.compile(r"\d{8,14}")


def is_zip(text: str) -> bool:
    return bool(_ZIP.fullmatch(text))


def is_upc(text: str) -> bool:
    return bool(_UPC.fullmatch(text))


def url_domain(url: str) -> str | None:
    """The host a link points at, without a leading "www.", or None if it has no host."""
    host = urlsplit(url).hostname
    return host.removeprefix("www.") if host else None


def optional_text(value: object) -> str | None:
    """Stripped text, or None for blanks and anything that isn't a string."""
    if not isinstance(value, str):
        return None
    return value.strip() or None


class Condition(StrEnum):
    NEW = "new"
    USED = "used"
    ANY = "any"


@dataclass(frozen=True)
class Edition:
    """One published edition of a product, such as a game's English or German printing."""

    name: str
    title: str
    product_code: str | None
    year: str | None
    languages: tuple[str, ...]
    publishers: tuple[str, ...]
    weight_lb: float | None = None
    length_in: float | None = None
    width_in: float | None = None
    depth_in: float | None = None


class Marketplace(StrEnum):
    BGG = "bgg"
    EBAY = "ebay"

    @property
    def label(self) -> str:
        """The source's name, as shown to people."""
        return {Marketplace.BGG: "BoardGameGeek", Marketplace.EBAY: "eBay"}[self]

    @property
    def collects_tax(self) -> bool:
        """Whether buying here adds sales tax. eBay collects it as a marketplace facilitator;
        GeekMarket buyers pay sellers directly, untaxed."""
        return self is Marketplace.EBAY


class NoticeKind(StrEnum):
    """Why a person heard about a listing."""

    FIRST_FOUND = "first_found"
    AT_TARGET = "at_target"
    NEW_LOW = "new_low"
    DIGEST = "digest"


@dataclass(frozen=True)
class Listing:
    """Something for sale right now, as a marketplace shows it."""

    marketplace: Marketplace
    external_id: str
    title: str
    url: str
    price_cents: int
    is_new: bool
    condition: str
    shipping_cents: int | None = None
    edition: str | None = None
    origin_state: str | None = None
    origin_zip3: str | None = None
    auction_ends_at: datetime | None = None
    """Set for an auction with no Buy It Now price; its price is then the current bid."""
    bid_count: int | None = None

    @property
    def is_auction(self) -> bool:
        return self.auction_ends_at is not None


@dataclass(frozen=True)
class RecordedListing:
    id: int
    item_id: int
    listing: Listing
    first_seen: datetime
    last_seen: datetime
    ended_at: datetime | None
    rejection: str | None = None
    """Why Claude judged this listing not to be the item, if it did."""


@dataclass(frozen=True)
class ListingChanges:
    """What one look at a marketplace found, compared with the look before."""

    new: list[RecordedListing]
    repriced: list[RecordedListing]
    ended: list[RecordedListing]


@dataclass(frozen=True)
class Sale:
    """A completed sale seen somewhere, used as evidence of what an item really sells for."""

    source: Marketplace
    sold_on: date
    price_cents: int
    is_new: bool
    condition: str
    title: str = ""
    shipping_cents: int | None = None
    source_id: str | None = None


EARLIEST_SALE = date(2000, 1, 1)
SALE_TITLE_LIMIT = 300
SALE_CONDITION_LIMIT = 60


def checked_sale(sale: Sale, today: date) -> Sale | None:
    """The sale with its text tidied and capped, or None if it can't be a real sale.
    Sales come from untrusted pages, so every source runs them through this."""
    condition = " ".join(sale.condition.split())[:SALE_CONDITION_LIMIT]
    if (
        not condition
        or sale.price_cents <= 0
        or (sale.shipping_cents is not None and sale.shipping_cents < 0)
        or not EARLIEST_SALE <= sale.sold_on <= today
    ):
        return None
    return replace(sale, condition=condition, title=" ".join(sale.title.split())[:SALE_TITLE_LIMIT])


@dataclass(frozen=True)
class RecordedSale:
    id: int
    item_id: int
    sale: Sale


@dataclass(frozen=True)
class User:
    id: int
    name: str
    email: str | None
    is_admin: bool


@dataclass(frozen=True)
class ItemFields:
    """Everything about an item that a person can set."""

    name: str
    condition: Condition = Condition.ANY
    upc: str | None = None
    epid: str | None = None
    model_number: str | None = None
    reference_url: str | None = None
    image_url: str | None = None
    search_query: str | None = None
    variant: str | None = None
    zip: str | None = None
    weight_oz: int | None = None
    length_in: float | None = None
    width_in: float | None = None
    depth_in: float | None = None
    target_price_cents: int | None = None
    notes: str = ""
    watcher_ids: frozenset[int] = field(default_factory=frozenset[int])


@dataclass(frozen=True)
class Item:
    id: int
    fields: ItemFields
    created_at: datetime
    closed_at: datetime | None

    @property
    def is_open(self) -> bool:
        return self.closed_at is None
