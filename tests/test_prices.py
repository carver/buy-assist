from datetime import date
from decimal import Decimal

from buy_assist.models import Marketplace, Sale
from buy_assist.prices import FullPrice, PriceStats, full_price, summarize_sales
from buy_assist.usps import ShippingEstimate


def sale(dollars: int, *, is_new: bool) -> Sale:
    return Sale(
        source=Marketplace.BGG,
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


ESTIMATE = ShippingEstimate(low_cents=904, typical_cents=1630, high_cents=1630)


def test_private_sale_full_price_adds_estimated_shipping_and_no_tax() -> None:
    price = full_price(4000, None, ESTIMATE, Decimal("0.08625"), collects_tax=False)

    assert price == FullPrice(
        item_cents=4000, shipping_cents=1630, shipping_estimated=True, tax_cents=0
    )
    assert price.total_cents == 5630


def test_marketplace_that_collects_tax_adds_tax_on_the_item_price() -> None:
    price = full_price(4000, 585, ESTIMATE, Decimal("0.08625"), collects_tax=True)

    assert price.shipping_cents == 585
    assert not price.shipping_estimated
    assert price.tax_cents == 345
    assert price.total_cents == 4930


def test_unknown_shipping_and_tax_rate_leave_gaps() -> None:
    price = full_price(4000, None, None, None, collects_tax=True)

    assert price.shipping_cents is None
    assert price.tax_cents is None
    assert price.total_cents == 4000
    assert not price.complete
