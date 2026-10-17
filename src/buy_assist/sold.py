"""Where sold prices come from automatically. Today that's BGG's GeekMarket history; pasted
eBay results come in by hand through the item page (see paste.py)."""

from typing import Protocol

from buy_assist.bgg import BggApi, BggError, game_id
from buy_assist.models import ItemFields, Sale


class SoldPriceError(Exception):
    """Sold prices couldn't be fetched. The message is safe to show to the person."""


class SoldPriceSource(Protocol):
    label: str
    """The source's name, as shown to people."""

    def covers(self, item: ItemFields) -> bool:
        """Whether this source can look up sales for the item."""
        ...

    def sales_for(self, item: ItemFields) -> list[Sale]:
        """Recent sales of the item. Raises SoldPriceError."""
        ...


class BggSoldPrices:
    """GeekMarket sales for items whose reference link is a BGG game."""

    label = "BoardGameGeek"

    def __init__(self, bgg: BggApi) -> None:
        self._bgg = bgg

    def covers(self, item: ItemFields) -> bool:
        return item.reference_url is not None and game_id(item.reference_url) is not None

    def sales_for(self, item: ItemFields) -> list[Sale]:
        bgg_id = game_id(item.reference_url) if item.reference_url else None
        if bgg_id is None:
            return []
        try:
            return self._bgg.sales(bgg_id)
        except BggError as e:
            raise SoldPriceError(str(e)) from e
