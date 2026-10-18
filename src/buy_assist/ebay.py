"""eBay: links into its own site, and its Browse API for active listings.

The Browse API needs an application token from eBay's client-credentials grant; the token
lasts two hours and gets reused until shortly before then, as eBay asks. See
docs/research/apis.md for what each field means.
"""

import base64
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlencode, urlsplit

import httpx2
from pydantic import BaseModel, ValidationError

from buy_assist.models import (
    SALE_CONDITION_LIMIT,
    SALE_TITLE_LIMIT,
    Condition,
    ItemFields,
    Listing,
    Marketplace,
    optional_text,
)

_CONDITION_IDS = {Condition.NEW: "1000", Condition.USED: "3000"}
"""eBay's condition ids, for the sold-search link and for telling new listings apart."""
_CONDITIONS = {Condition.NEW: "NEW", Condition.USED: "USED"}
"""Browse search's condition filter values."""
_MAX_QUERY_CHARS = 100
_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
_SCOPE = "https://api.ebay.com/oauth/api_scope"
_TOKEN_MARGIN_SECONDS = 300


def sold_search_url(item: ItemFields) -> str:
    """eBay's search for completed, sold listings of the item, for a person to open."""
    params = {
        "_nkw": item.search_query or item.name,
        "LH_Sold": "1",
        "LH_Complete": "1",
    }
    if condition := _CONDITION_IDS.get(item.condition):
        params["LH_ItemCondition"] = condition
    return "https://www.ebay.com/sch/i.html?" + urlencode(params)


class EbayError(Exception):
    """eBay didn't answer usefully. The message is safe to show to the person."""


class EbayApi:
    def __init__(
        self,
        client: httpx2.Client,
        client_id: str,
        client_secret: str,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        self._clock = clock
        self._lock = threading.Lock()
        self._token: tuple[str, float] | None = None

    @classmethod
    def from_env(cls, client: httpx2.Client) -> EbayApi | None:
        """The API with keys from EBAY_CLIENT_ID and EBAY_CLIENT_SECRET, or None without them."""
        client_id = os.environ.get("EBAY_CLIENT_ID", "")
        client_secret = os.environ.get("EBAY_CLIENT_SECRET", "")
        if not (client_id and client_secret):
            return None
        return cls(client, client_id, client_secret)

    def us_listings(self, item: ItemFields, ship_to: str | None) -> list[Listing]:
        """The item's search results, as listings in dollars with working links, cheapest
        first. With a ship-to ZIP, eBay quotes shipping to it."""
        params = _search_params(item, ship_to)
        headers = {"x-ebay-c-marketplace-id": "EBAY_US"}
        if ship_to:
            location = quote(f"country=US,zip={ship_to}", safe="")
            headers["x-ebay-c-enduserctx"] = f"contextualLocation={location}"
        response = self._search(params, headers)
        if response.status_code == 401:
            self._token = None
            response = self._search(params, headers)
        if response.status_code != 200:
            raise EbayError(f"eBay answered {response.status_code}.")
        try:
            answer = _SearchAnswer.model_validate_json(response.content)
        except ValidationError as e:
            raise EbayError("eBay's answer wasn't what this app expects.") from e
        listings = [listing for s in answer.itemSummaries if (listing := s.listing())]
        return sorted(listings, key=lambda listing: listing.price_cents)

    def _search(self, params: dict[str, str], headers: dict[str, str]) -> httpx2.Response:
        headers = {**headers, "authorization": f"Bearer {self._access_token()}"}
        return self._call(lambda: self._client.get(_SEARCH_URL, params=params, headers=headers))

    def _access_token(self) -> str:
        with self._lock:
            if self._token and self._clock() < self._token[1]:
                return self._token[0]
            response = self._call(
                lambda: self._client.post(
                    _TOKEN_URL,
                    headers={
                        "authorization": f"Basic {self._basic}",
                        "content-type": "application/x-www-form-urlencoded",
                    },
                    content=urlencode({"grant_type": "client_credentials", "scope": _SCOPE}),
                )
            )
            if response.status_code != 200:
                raise EbayError(f"eBay refused the app's keys ({response.status_code}).")
            try:
                token = _TokenAnswer.model_validate_json(response.content)
            except ValidationError as e:
                raise EbayError("eBay's token answer wasn't what this app expects.") from e
            expires = self._clock() + token.expires_in - _TOKEN_MARGIN_SECONDS
            self._token = (token.access_token, expires)
            return token.access_token

    def _call(self, request: Callable[[], httpx2.Response]) -> httpx2.Response:
        try:
            return request()
        except httpx2.HTTPError as e:
            raise EbayError(f"eBay didn't answer ({type(e).__name__}).") from e


def _search_params(item: ItemFields, ship_to: str | None) -> dict[str, str]:
    """Buy It Now and auctions, located in the US and delivering to us, in the item's
    condition. A UPC or EPID finds the product itself; otherwise the search words do."""
    filters = [
        "buyingOptions:{FIXED_PRICE|BEST_OFFER|AUCTION}",
        "itemLocationCountry:US",
        "deliveryCountry:US",
    ]
    if ship_to:
        filters.append(f"deliveryPostalCode:{ship_to}")
    if condition := _CONDITIONS.get(item.condition):
        filters.append(f"conditions:{{{condition}}}")
    # Cheapest first: when more than one page matches, the ones left out (and so taken to
    # have ended) are the dearest.
    params = {"filter": ",".join(filters), "limit": "200", "sort": "price"}
    if item.upc:
        params["gtin"] = item.upc
    elif item.epid:
        params["epid"] = item.epid
    else:
        params["q"] = (item.search_query or item.name)[:_MAX_QUERY_CHARS]
    return params


class _TokenAnswer(BaseModel):
    access_token: str
    expires_in: int


class _Amount(BaseModel):
    value: str
    currency: str

    def us_cents(self, *, allow_zero: bool = False) -> int | None:
        if self.currency != "USD":
            return None
        try:
            dollars = Decimal(self.value)
        except InvalidOperation:
            return None
        if not dollars.is_finite() or dollars < 0 or (dollars == 0 and not allow_zero):
            return None
        return round(dollars * 100)


class _ShippingOption(BaseModel):
    shippingCostType: str | None = None
    shippingCost: _Amount | None = None


class _Location(BaseModel):
    postalCode: str | None = None
    country: str | None = None


class _Summary(BaseModel):
    itemId: str
    title: str = ""
    itemWebUrl: str = ""
    price: _Amount | None = None
    currentBidPrice: _Amount | None = None
    bidCount: int | None = None
    buyingOptions: list[str] = []
    condition: str = ""
    conditionId: str | None = None
    shippingOptions: list[_ShippingOption] = []
    itemLocation: _Location | None = None
    itemEndDate: datetime | None = None

    def listing(self) -> Listing | None:
        """This result as a listing, or None unless it has a dollar price and an https link.
        An auction with a Buy It Now price counts as Buy It Now; one without needs an end
        time, since that's what marks a listing as an auction."""
        auction = "AUCTION" in self.buyingOptions and "FIXED_PRICE" not in self.buyingOptions
        amount = self.currentBidPrice if auction else self.price
        price = amount.us_cents() if amount else None
        title = optional_text(self.title)
        if price is None or not title or urlsplit(self.itemWebUrl).scheme != "https":
            return None
        if auction and self.itemEndDate is None:
            return None
        return Listing(
            marketplace=Marketplace.EBAY,
            external_id=self.itemId,
            title=title[:SALE_TITLE_LIMIT],
            url=self.itemWebUrl,
            price_cents=price,
            is_new=self.conditionId == _CONDITION_IDS[Condition.NEW],
            condition=(optional_text(self.condition) or "Unknown")[:SALE_CONDITION_LIMIT],
            shipping_cents=self._shipping_cents(),
            origin_zip3=self._origin_zip3(),
            auction_ends_at=self.itemEndDate if auction else None,
            bid_count=self.bidCount if auction else None,
        )

    def _shipping_cents(self) -> int | None:
        """The cheapest shipping quoted, or None when eBay gave no cost."""
        costs = [
            cents
            for option in self.shippingOptions
            if option.shippingCost
            and (cents := option.shippingCost.us_cents(allow_zero=True)) is not None
        ]
        return min(costs, default=None)

    def _origin_zip3(self) -> str | None:
        """eBay shows a seller's ZIP as "940**"; the first three digits pick a USPS zone."""
        postal = self.itemLocation.postalCode if self.itemLocation else None
        prefix = (postal or "")[:3]
        return prefix if len(prefix) == 3 and prefix.isdecimal() else None


class _SearchAnswer(BaseModel):
    itemSummaries: list[_Summary] = []
