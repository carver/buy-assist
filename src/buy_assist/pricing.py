"""Full prices for listings and sales: shipping to the item's ZIP, and tax where charged."""

import json
import logging
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol

import httpx2
from pydantic import ValidationError

from buy_assist.models import ItemFields, Listing, Sale
from buy_assist.prices import FullPrice, full_price
from buy_assist.store import Store
from buy_assist.usps import Box, Origin, ShippingEstimate, UspsRates, ZoneChart, estimate_shipping

_log = logging.getLogger(__name__)
_ZONE_CHART_URL = "https://postcalc.usps.com/DomesticZoneChart/GetZoneChart"
_ZONE_CHART_MAX_AGE = timedelta(days=30)
_USER_AGENT = "buy-assist/0.1 (household shopping list)"


class ShippingQuoter(Protocol):
    def quote(self, item: ItemFields, origin: Origin) -> ShippingEstimate | None:
        """Estimated USPS shipping for the item from `origin` to its ZIP, if it can be told."""
        ...


class UspsQuoter:
    """Estimates from the checked-in USPS prices and the zone chart for the item's ZIP,
    fetched from USPS and kept for a month."""

    def __init__(
        self, store: Store, client: httpx2.Client, today: Callable[[], date] = date.today
    ) -> None:
        self._store = store
        self._client = client
        self._today = today
        self._rates = UspsRates.load()

    def quote(self, item: ItemFields, origin: Origin) -> ShippingEstimate | None:
        ship_to = item.zip or self._store.home_zip()
        chart = self._chart(ship_to[:3]) if ship_to else None
        if chart is None:
            return None
        box = Box(item.weight_oz, item.length_in, item.width_in, item.depth_in)
        return estimate_shipping(box, chart, self._rates, origin)

    def _chart(self, zip3: str) -> ZoneChart | None:
        today = self._today()
        cached = self._store.zone_chart(zip3)
        if cached is None or today - cached[0] > _ZONE_CHART_MAX_AGE:
            fresh = self._fetch_chart(zip3, today)
            if fresh is not None:
                self._store.save_zone_chart(zip3, today, fresh)
                cached = (today, fresh)
        if cached is None:
            return None
        try:
            return ZoneChart.from_usps(cached[1])
        except ValidationError:
            return None

    def _fetch_chart(self, zip3: str, today: date) -> object | None:
        params = {"zipCode3Digit": zip3, "shippingDate": today.strftime("%m/%d/%Y")}
        try:
            response = self._client.get(
                _ZONE_CHART_URL, params=params, headers={"user-agent": _USER_AGENT}
            )
            response.raise_for_status()
            return json.loads(response.content)
        except (httpx2.HTTPError, ValueError) as e:
            _log.warning("USPS zone chart for %s: %s", zip3, type(e).__name__)
            return None


class Pricer:
    def __init__(self, store: Store, quoter: ShippingQuoter) -> None:
        self._store = store
        self._quoter = quoter

    def listing(self, item: ItemFields, listing: Listing) -> FullPrice:
        origin = Origin(zip3=listing.origin_zip3, state=listing.origin_state)
        estimate = None if listing.shipping_cents is not None else self._quoter.quote(item, origin)
        return full_price(
            listing.price_cents,
            listing.shipping_cents,
            estimate,
            self._tax_rate(item),
            collects_tax=listing.marketplace.collects_tax,
        )

    def sale(self, item: ItemFields, sale: Sale) -> FullPrice:
        estimate = None if sale.shipping_cents is not None else self._quoter.quote(item, Origin())
        return full_price(
            sale.price_cents,
            sale.shipping_cents,
            estimate,
            self._tax_rate(item),
            collects_tax=sale.source.collects_tax,
        )

    def _tax_rate(self, item: ItemFields) -> Decimal | None:
        ship_to = item.zip or self._store.home_zip()
        return self._store.tax_rate(ship_to) if ship_to else None
