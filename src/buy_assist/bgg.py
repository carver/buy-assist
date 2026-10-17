"""BoardGameGeek's JSON API: games, their editions, and GeekMarket sales.

This is the undocumented API at api.geekdo.com that the BGG site itself calls; BGG's pages
sit behind a Cloudflare challenge. Every call goes through one throttle, at most one
request every 5 seconds, the gap BGG's API wiki gives as safe.
"""

import math
import re
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import httpx2
from pydantic import BaseModel, ValidationError

from buy_assist.models import (
    SALE_CONDITION_LIMIT,
    Edition,
    Listing,
    Marketplace,
    Sale,
    checked_sale,
    optional_text,
    url_domain,
)

_API = "https://api.geekdo.com/api"
_GAME_PATH = re.compile(r"/(?:boardgame|boardgameexpansion)/(\d+)(?:/|$)")
_MIN_INTERVAL_SECONDS = 5.0
_EDITION_IN_TITLE = re.compile(r"\(([^()]*)\)\s*$")
_USER_AGENT = "buy-assist/0.1 (household shopping list)"


class BggError(Exception):
    """BGG didn't answer usefully. The message is safe to show to the person."""


@dataclass(frozen=True)
class BggGame:
    name: str | None
    image_url: str | None


def game_id(url: str) -> str | None:
    """The BGG game id in a boardgamegeek.com game link, else None."""
    if url_domain(url) != "boardgamegeek.com":
        return None
    match = _GAME_PATH.search(urlsplit(url).path)
    return match.group(1) if match else None


class _Throttle:
    """Keeps requests at least `interval` seconds apart, across threads."""

    def __init__(
        self, interval: float, clock: Callable[[], float], sleep: Callable[[float], None]
    ) -> None:
        self._interval = interval
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last: float | None = None

    def wait(self) -> None:
        with self._lock:
            if self._last is not None:
                remaining = self._last + self._interval - self._clock()
                if remaining > 0:
                    self._sleep(remaining)
            self._last = self._clock()


class BggApi:
    def __init__(
        self,
        client: httpx2.Client,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._throttle = _Throttle(_MIN_INTERVAL_SECONDS, clock, sleep)

    def game(self, game_id: str) -> BggGame:
        params = {"objectid": game_id, "objecttype": "thing", "nosession": "1"}
        item = self._get(_GameAnswer, "/geekitems", params).item
        return BggGame(name=optional_text(item.name), image_url=optional_text(item.imageurl))

    def editions(self, game_id: str) -> tuple[Edition, ...]:
        params = {
            "ajax": "1",
            "linkdata_index": "boardgameversion",
            "nosession": "1",
            "objectid": game_id,
            "objecttype": "thing",
            "pageid": "1",
            "showcount": "25",
            "sort": "yearpublished",
            "subtype": "boardgameversion",
        }
        versions = self._get(_VersionsAnswer, "/geekitem/linkeditems", params).items
        return _with_shared_weights(_distinct_names([e for v in versions if (e := v.edition())]))

    def sales(self, game_id: str) -> list[Sale]:
        """GeekMarket sales in US dollars, newest first. GeekMarket records no shipping."""
        params = {
            "ajax": "1",
            "condition": "any",
            "currency": "USD",
            "objectid": game_id,
            "objecttype": "thing",
            "pageid": "1",
        }
        history = self._get(_PriceHistoryAnswer, "/market/products/pricehistory", params)
        sales = [s for entry in history.items if (s := entry.sale())]
        return sorted(sales, key=lambda s: s.sold_on, reverse=True)

    def us_listings(self, game_id: str) -> list[Listing]:
        """Active GeekMarket listings a US buyer can pay for in dollars, cheapest first."""
        params = {"objectid": game_id, "objecttype": "thing", "pageid": "1"}
        products = self._get(_ProductsAnswer, "/market/products", params).products
        listings = [listing for p in products if (listing := p.us_listing())]
        return sorted(listings, key=lambda listing: listing.price_cents)

    def _get[T: BaseModel](self, answer: type[T], path: str, params: dict[str, str]) -> T:
        self._throttle.wait()
        try:
            response = self._client.get(
                _API + path, params=params, headers={"user-agent": _USER_AGENT}
            )
        except httpx2.HTTPError as e:
            raise BggError(f"BoardGameGeek didn't answer ({type(e).__name__}).") from e
        if response.status_code != 200:
            raise BggError(f"BoardGameGeek answered {response.status_code}.")
        try:
            return answer.model_validate_json(response.content)
        except ValidationError as e:
            raise BggError("BoardGameGeek's answer wasn't what this app expects.") from e


def _distinct_names(editions: list[Edition]) -> tuple[Edition, ...]:
    """Add the year to editions whose names repeat, so a name picks out one edition."""
    counts = Counter(e.name for e in editions)
    renamed = [
        replace(e, name=f"{e.name} ({e.year})") if counts[e.name] > 1 and e.year else e
        for e in editions
    ]
    seen: Counter[str] = Counter()
    distinct: list[Edition] = []
    for edition in renamed:
        seen[edition.name] += 1
        if seen[edition.name] > 1:
            edition = replace(edition, name=f"{edition.name} #{seen[edition.name]}")
        distinct.append(edition)
    return tuple(distinct)


def _with_shared_weights(editions: tuple[Edition, ...]) -> tuple[Edition, ...]:
    """Give editions with a box size but no weight the weight of an edition with the same box.
    BGG contributors often fill in one edition's weight and only the sizes of the others."""
    weights = {_box(e): e.weight_lb for e in editions if e.weight_lb and _box(e)}
    return tuple(
        replace(e, weight_lb=weights.get(_box(e))) if e.weight_lb is None and _box(e) else e
        for e in editions
    )


def _box(edition: Edition) -> tuple[float, float, float] | None:
    if edition.length_in and edition.width_in and edition.depth_in:
        return (edition.length_in, edition.width_in, edition.depth_in)
    return None


def _measure(value: str | None) -> float | None:
    """A BGG size in inches or weight in pounds. BGG stores unknown ones as 0."""
    try:
        number = float(value or "")
    except ValueError:
        return None
    return number if number > 0 and math.isfinite(number) else None


class _Link(BaseModel):
    name: str = ""


class _Game(BaseModel):
    name: str | None = None
    imageurl: str | None = None


class _GameAnswer(BaseModel):
    item: _Game


class _Version(BaseModel):
    versionname: str | None = None
    linkedname: str | None = None
    productcode: str | None = None
    yearpublished: str | None = None
    length: str | None = None
    width: str | None = None
    depth: str | None = None
    weight: str | None = None
    links: dict[str, list[_Link]] = {}

    def edition(self) -> Edition | None:
        name = optional_text(self.versionname)
        title = optional_text(self.linkedname)
        if not (name and title):
            return None
        return Edition(
            name=name,
            title=title,
            product_code=optional_text(self.productcode),
            year=optional_text(self.yearpublished),
            languages=self._names("languages"),
            publishers=self._names("boardgamepublisher"),
            weight_lb=_measure(self.weight),
            length_in=_measure(self.length),
            width_in=_measure(self.width),
            depth_in=_measure(self.depth),
        )

    def _names(self, kind: str) -> tuple[str, ...]:
        return tuple(link.name for link in self.links.get(kind, []) if link.name)


class _VersionsAnswer(BaseModel):
    items: list[_Version]


class _MarketSale(BaseModel):
    itemid: str | None = None
    currency: str | None = None
    price: str | None = None
    condition: str | None = None
    notes: str | None = None
    usaledate: str | None = None

    def sale(self) -> Sale | None:
        """This sale, or None if it isn't in dollars or doesn't parse."""
        if self.currency != "USD" or not self.price or not self.usaledate:
            return None
        try:
            price = Decimal(self.price)
            sold_at = datetime.fromisoformat(self.usaledate)
        except InvalidOperation, ValueError:
            return None
        if not price.is_finite():
            return None
        condition = optional_text(self.condition) or "Unknown"
        sale = Sale(
            source=Marketplace.BGG,
            source_id=self.itemid,
            sold_on=sold_at.date(),
            price_cents=round(price * 100),
            is_new=condition == "New",
            condition=condition,
            title=self.notes or "",
        )
        return checked_sale(sale, date.today())


class _ProductVersion(BaseModel):
    name: str | None = None


class _Seller(BaseModel):
    state: str | None = None


class _Product(BaseModel):
    productid: str
    price: str | None = None
    currency: str | None = None
    prettycondition: str | None = None
    conditioncode: str | None = None
    productstate: str | None = None
    itemlocation_code: str | None = None
    version: _ProductVersion | None = None
    linkeduser: _Seller | None = None

    def us_listing(self) -> Listing | None:
        """This product as a listing, or None unless it's active, in dollars and in the US."""
        if (self.productstate, self.currency, self.itemlocation_code) != ("active", "USD", "US"):
            return None
        try:
            price = Decimal(self.price or "")
        except InvalidOperation:
            return None
        if not price.is_finite() or price <= 0:
            return None
        title = optional_text(self.version.name) if self.version else None
        edition = _EDITION_IN_TITLE.search(title) if title else None
        condition = optional_text(self.prettycondition) or "Unknown"
        return Listing(
            marketplace=Marketplace.BGG,
            external_id=self.productid,
            title=title or "GeekMarket listing",
            url=f"https://boardgamegeek.com/market/product/{self.productid}",
            price_cents=round(price * 100),
            is_new=self.conditioncode == "n",
            condition=condition[:SALE_CONDITION_LIMIT],
            edition=edition.group(1) if edition else None,
            origin_state=optional_text(self.linkeduser.state) if self.linkeduser else None,
        )


class _ProductsAnswer(BaseModel):
    products: list[_Product]


class _PriceHistoryAnswer(BaseModel):
    items: list[_MarketSale]
