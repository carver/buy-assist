"""Domain types shared by the store, the parser and the web app."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Condition(StrEnum):
    NEW = "new"
    USED = "used"
    ANY = "any"


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
    variant: str | None = None
    zip: str | None = None
    target_price_cents: int | None = None
    notes: str = ""
    watcher_ids: frozenset[int] = field(default_factory=frozenset[int])


@dataclass(frozen=True)
class ItemDraft:
    """What Claude read out of a free-text request. A person reviews it before saving."""

    name: str
    condition: Condition = Condition.ANY
    upc: str | None = None
    epid: str | None = None
    model_number: str | None = None
    reference_url: str | None = None
    variant: str | None = None
    target_price_cents: int | None = None
    notes: str = ""


@dataclass(frozen=True)
class Item:
    id: int
    fields: ItemFields
    created_at: datetime
    closed_at: datetime | None

    @property
    def is_open(self) -> bool:
        return self.closed_at is None
