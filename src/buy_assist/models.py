"""Domain types shared by the store, the parser and the web app."""

import re
from dataclasses import dataclass, field
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


class SaleSource(StrEnum):
    BGG = "bgg"
    EBAY = "ebay"


@dataclass(frozen=True)
class Sale:
    """A completed sale seen somewhere, used as evidence of what an item really sells for."""

    source: SaleSource
    sold_on: date
    price_cents: int
    is_new: bool
    condition: str
    title: str = ""
    shipping_cents: int | None = None
    source_id: str | None = None


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
