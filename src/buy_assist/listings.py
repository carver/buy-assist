"""Where listings come from: BGG GeekMarket and eBay."""

import re
from typing import Protocol

from buy_assist.bgg import BggApi, BggError, game_id
from buy_assist.ebay import EbayApi, EbayError
from buy_assist.models import Condition, ItemFields, Listing, Marketplace

_YEAR_OR_COPY_SUFFIX = re.compile(r"\s*(\(\d{4}\)|#\d+)\s*$")


class ListingError(Exception):
    """Listings couldn't be fetched. The message is safe to show to the person."""


class ListingSource(Protocol):
    marketplace: Marketplace
    label: str
    needs_judging: bool
    """Whether its results need Claude's check that they're the item (see judge.py)."""

    def covers(self, item: ItemFields) -> bool:
        """Whether this source can look for the item."""
        ...

    def listings_for(self, item: ItemFields, ship_to: str | None) -> list[Listing]:
        """Everything on sale now that matches the item, with shipping quoted to `ship_to`
        where the source can. Raises ListingError."""
        ...


class GeekMarketListings:
    """GeekMarket listings for items whose reference link is a BGG game, in the item's
    edition when the listing says which."""

    marketplace = Marketplace.BGG
    label = Marketplace.BGG.label
    needs_judging = False

    def __init__(self, bgg: BggApi) -> None:
        self._bgg = bgg

    def covers(self, item: ItemFields) -> bool:
        return _bgg_id(item) is not None

    def listings_for(self, item: ItemFields, ship_to: str | None) -> list[Listing]:
        bgg_id = _bgg_id(item)
        if bgg_id is None:
            return []
        try:
            listings = self._bgg.us_listings(bgg_id)
        except BggError as e:
            raise ListingError(str(e)) from e
        return [listing for listing in listings if _same_edition(listing, item)]


class EbayListings:
    """eBay listings, Buy It Now and auctions, located in the US and delivering to us. A UPC
    or EPID finds the product itself; otherwise the search words do."""

    marketplace = Marketplace.EBAY
    label = Marketplace.EBAY.label
    needs_judging = True

    def __init__(self, ebay: EbayApi) -> None:
        self._ebay = ebay

    def covers(self, item: ItemFields) -> bool:
        return True

    def listings_for(self, item: ItemFields, ship_to: str | None) -> list[Listing]:
        try:
            return self._ebay.us_listings(_ebay_search(item, ship_to), ship_to)
        except EbayError as e:
            raise ListingError(str(e)) from e


_EBAY_CONDITIONS = {Condition.NEW: "NEW", Condition.USED: "USED"}
_EBAY_MAX_QUERY_CHARS = 100


def _ebay_search(item: ItemFields, ship_to: str | None) -> dict[str, str]:
    filters = [
        "buyingOptions:{FIXED_PRICE|BEST_OFFER|AUCTION}",
        "itemLocationCountry:US",
        "deliveryCountry:US",
    ]
    if ship_to:
        filters.append(f"deliveryPostalCode:{ship_to}")
    if condition := _EBAY_CONDITIONS.get(item.condition):
        filters.append(f"conditions:{{{condition}}}")
    # Cheapest first: when more than one page matches, the ones left out (and so taken to
    # have ended) are the dearest.
    params = {"filter": ",".join(filters), "limit": "200", "sort": "price"}
    if item.upc:
        params["gtin"] = item.upc
    elif item.epid:
        params["epid"] = item.epid
    else:
        params["q"] = (item.search_query or item.name)[:_EBAY_MAX_QUERY_CHARS]
    return params


def _bgg_id(item: ItemFields) -> str | None:
    return game_id(item.reference_url) if item.reference_url else None


def _same_edition(listing: Listing, item: ItemFields) -> bool:
    if not (listing.edition and item.variant):
        return True
    return _edition_key(listing.edition) == _edition_key(item.variant)


def _edition_key(name: str) -> str:
    """'Dutch edition (2025)' and 'Dutch edition' are the same edition to a listing."""
    return _YEAR_OR_COPY_SUFFIX.sub("", name).casefold()
