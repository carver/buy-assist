"""Summaries of what items sell for."""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from statistics import median

from buy_assist.models import Sale
from buy_assist.usps import ShippingEstimate


@dataclass(frozen=True)
class PriceStats:
    count: int
    low_cents: int
    median_cents: int
    high_cents: int


@dataclass(frozen=True)
class SalesSummary:
    """Item prices before shipping, split so new and used can be compared."""

    new: PriceStats | None
    used: PriceStats | None


def summarize_sales(sales: Iterable[Sale]) -> SalesSummary:
    sales = list(sales)
    return SalesSummary(
        new=_stats([s.price_cents for s in sales if s.is_new]),
        used=_stats([s.price_cents for s in sales if not s.is_new]),
    )


def _stats(prices: list[int]) -> PriceStats | None:
    if not prices:
        return None
    return PriceStats(
        count=len(prices),
        low_cents=min(prices),
        median_cents=round(median(prices)),
        high_cents=max(prices),
    )


@dataclass(frozen=True)
class FullPrice:
    """What buying something costs: the price, shipping and tax. None marks what's unknown."""

    item_cents: int
    shipping_cents: int | None
    shipping_estimated: bool
    tax_cents: int | None

    @property
    def total_cents(self) -> int:
        return self.item_cents + (self.shipping_cents or 0) + (self.tax_cents or 0)

    @property
    def complete(self) -> bool:
        return self.shipping_cents is not None and self.tax_cents is not None


def full_price(
    item_cents: int,
    shipping_cents: int | None,
    estimate: ShippingEstimate | None,
    tax_rate: Decimal | None,
    *,
    collects_tax: bool,
) -> FullPrice:
    """Known shipping beats the estimate. Tax applies to the item price only: California
    doesn't tax separately stated delivery by a common carrier."""
    estimated = shipping_cents is None and estimate is not None
    if not collects_tax:
        tax_cents = 0
    elif tax_rate is None:
        tax_cents = None
    else:
        tax_cents = round(item_cents * tax_rate)
    return FullPrice(
        item_cents=item_cents,
        shipping_cents=estimate.typical_cents if estimated and estimate else shipping_cents,
        shipping_estimated=estimated,
        tax_cents=tax_cents,
    )
