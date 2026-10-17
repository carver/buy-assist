from datetime import date

from buy_assist.models import Sale, SaleSource
from buy_assist.prices import PriceStats, summarize_sales


def sale(dollars: int, *, is_new: bool) -> Sale:
    return Sale(
        source=SaleSource.BGG,
        sold_on=date(2025, 1, 1),
        price_cents=dollars * 100,
        is_new=is_new,
        condition="New" if is_new else "Very Good",
    )


def test_summary_splits_new_and_used_prices() -> None:
    summary = summarize_sales(
        [
            sale(40, is_new=True),
            sale(36, is_new=True),
            sale(50, is_new=True),
            sale(35, is_new=False),
            sale(20, is_new=False),
        ]
    )

    assert summary.new == PriceStats(count=3, low_cents=3600, median_cents=4000, high_cents=5000)
    assert summary.used == PriceStats(count=2, low_cents=2000, median_cents=2750, high_cents=3500)


def test_summary_of_no_sales_is_empty() -> None:
    summary = summarize_sales([])
    assert summary.new is None
    assert summary.used is None
