"""Where listings come from. Today that's BGG GeekMarket; eBay joins in step 5."""

import re
from typing import Protocol

from buy_assist.bgg import BggApi, BggError, game_id
from buy_assist.models import ItemFields, Listing, Marketplace

_YEAR_OR_COPY_SUFFIX = re.compile(r"\s*(\(\d{4}\)|#\d+)\s*$")


class ListingError(Exception):
    """Listings couldn't be fetched. The message is safe to show to the person."""


class ListingSource(Protocol):
    marketplace: Marketplace
    label: str

    def covers(self, item: ItemFields) -> bool:
        """Whether this source can look for the item."""
        ...

    def listings_for(self, item: ItemFields) -> list[Listing]:
        """Everything on sale now that matches the item. Raises ListingError."""
        ...


class GeekMarketListings:
    """GeekMarket listings for items whose reference link is a BGG game, in the item's
    edition when the listing says which."""

    marketplace = Marketplace.BGG
    label = Marketplace.BGG.label

    def __init__(self, bgg: BggApi) -> None:
        self._bgg = bgg

    def covers(self, item: ItemFields) -> bool:
        return _bgg_id(item) is not None

    def listings_for(self, item: ItemFields) -> list[Listing]:
        bgg_id = _bgg_id(item)
        if bgg_id is None:
            return []
        try:
            listings = self._bgg.us_listings(bgg_id)
        except BggError as e:
            raise ListingError(str(e)) from e
        return [listing for listing in listings if _same_edition(listing, item)]


def _bgg_id(item: ItemFields) -> str | None:
    return game_id(item.reference_url) if item.reference_url else None


def _same_edition(listing: Listing, item: ItemFields) -> bool:
    if not (listing.edition and item.variant):
        return True
    return _edition_key(listing.edition) == _edition_key(item.variant)


def _edition_key(name: str) -> str:
    """'Dutch edition (2025)' and 'Dutch edition' are the same edition to a listing."""
    return _YEAR_OR_COPY_SUFFIX.sub("", name).casefold()
