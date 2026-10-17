"""Summaries of what items sell for."""

from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median

from buy_assist.models import Sale


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
