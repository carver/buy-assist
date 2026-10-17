import json

import pytest

from buy_assist.usps import (
    Box,
    Origin,
    ShippingEstimate,
    UspsRates,
    ZoneChart,
    billable_ounces,
    estimate_shipping,
)
from tests.fixtures import DATA

CHART = ZoneChart.from_usps(json.loads((DATA / "usps_zone_chart_941.json").read_text()))
RATES = UspsRates.load()
EXIT_BOX = Box(weight_oz=30, length_in=17.76, width_in=12.91, depth_in=2.13)


@pytest.mark.parametrize(
    ("zip3", "zone"), [("701", 8), ("941", 1), ("900", 4), ("981", 5), ("100", 8), ("987", None)]
)
def test_zone_chart_gives_zone_from_each_origin(zip3: str, zone: int | None) -> None:
    assert CHART.zone_from(zip3) == zone


def test_known_origin_gives_one_price() -> None:
    estimate = estimate_shipping(EXIT_BOX, CHART, RATES, Origin(state="Louisiana"))

    # 30 oz + 12 oz packing bills as 3 lb; Notice 123 commercial, zone 8: $16.30.
    assert estimate == ShippingEstimate(low_cents=1630, typical_cents=1630, high_cents=1630)


def test_origin_zip_beats_state() -> None:
    estimate = estimate_shipping(EXIT_BOX, CHART, RATES, Origin(zip3="900", state="Louisiana"))
    assert estimate is not None
    assert estimate.typical_cents == 1007  # 3 lb, zone 4


def test_unknown_origin_gives_range_across_zones_and_typical_price() -> None:
    estimate = estimate_shipping(EXIT_BOX, CHART, RATES, Origin())

    assert estimate is not None
    assert estimate.low_cents == 904  # zone 1
    assert estimate.high_cents == 1630  # zone 8
    assert estimate.typical_cents == 1630  # most 3-digit areas are zone 8 from 941


def test_unknown_state_name_counts_as_unknown_origin() -> None:
    assert estimate_shipping(EXIT_BOX, CHART, RATES, Origin(state="Atlantis")) == (
        estimate_shipping(EXIT_BOX, CHART, RATES, Origin())
    )


def test_light_item_bills_by_ounce_tier() -> None:
    assert billable_ounces(Box(weight_oz=2)) == 14  # 2 oz + 12 oz packing, under a pound


def test_large_light_box_bills_dimensional_weight() -> None:
    # Packed 21 x 21 x 21 in = 9261 in3 > 1728, so 9261 / 139 = 66.6, billed as 67 lb.
    assert billable_ounces(Box(weight_oz=32, length_in=20, width_in=20, depth_in=20)) == 67 * 16


def test_box_without_weight_has_no_estimate() -> None:
    assert estimate_shipping(Box(weight_oz=None), CHART, RATES, Origin()) is None


def test_heavier_than_table_has_no_estimate() -> None:
    assert estimate_shipping(Box(weight_oz=71 * 16), CHART, RATES, Origin()) is None


def test_empty_zone_chart_gives_no_estimate() -> None:
    assert estimate_shipping(EXIT_BOX, ZoneChart({}), RATES, Origin()) is None
