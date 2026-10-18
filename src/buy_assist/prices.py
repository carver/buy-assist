"""Summaries of what items sell for."""

from collections.abc import Callable, Iterable
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
    median_all_in_cents: int
    """Median with shipping (known or estimated) and any tax."""


@dataclass(frozen=True)
class SalesSummary:
    """Sale prices, split so new and used can be compared."""

    new: PriceStats | None
    used: PriceStats | None


def summarize_sales(
    sales: Iterable[Sale], all_in: Callable[[Sale], int] = lambda sale: sale.price_cents
) -> SalesSummary:
    """`all_in` gives a sale's full price; by default, just its item price."""
    sales = list(sales)
    return SalesSummary(
        new=_stats([s for s in sales if s.is_new], all_in),
        used=_stats([s for s in sales if not s.is_new], all_in),
    )


def _stats(sales: list[Sale], all_in: Callable[[Sale], int]) -> PriceStats | None:
    if not sales:
        return None
    prices = [s.price_cents for s in sales]
    return PriceStats(
        count=len(prices),
        low_cents=min(prices),
        median_cents=round(median(prices)),
        high_cents=max(prices),
        median_all_in_cents=round(median(all_in(s) for s in sales)),
    )


@dataclass(frozen=True)
class FullPrice:
    """What buying something costs: the price, shipping and tax. None marks what's unknown."""

    item_cents: int
    shipping_cents: int | None
    shipping_estimated: bool
    tax_cents: int | None
    shipping_range: tuple[int, int] | None = None
    """Low and high shipping when the estimate couldn't pin down the seller's zone."""

    @property
    def total_cents(self) -> int:
        return self.item_cents + (self.shipping_cents or 0) + (self.tax_cents or 0)

    @property
    def complete(self) -> bool:
        return self.shipping_cents is not None and self.tax_cents is not None


def max_bid_cents(limit_cents: int, shipping_cents: int, tax_rate: Decimal) -> int:
    """The highest bid whose full price, with tax on the bid, stays within the limit."""
    bid = int((limit_cents - shipping_cents) / (1 + tax_rate)) + 1
    while bid + shipping_cents + round(bid * tax_rate) > limit_cents:
        bid -= 1
    return bid


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
    spread = estimate is not None and estimate.low_cents != estimate.high_cents
    return FullPrice(
        item_cents=item_cents,
        shipping_cents=estimate.typical_cents if estimated and estimate else shipping_cents,
        shipping_estimated=estimated,
        tax_cents=tax_cents,
        shipping_range=(estimate.low_cents, estimate.high_cents)
        if estimated and estimate and spread
        else None,
    )
