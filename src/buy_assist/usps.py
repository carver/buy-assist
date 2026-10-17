"""Estimate what USPS Ground Advantage costs to ship an item to us.

Prices are Notice 123 commercial (online label) prices, checked in as
rates/usps_ground_advantage.json; scripts/usps_rates.py regenerates the file when USPS
changes prices. Zones come from USPS's zone chart for our 3-digit ZIP. See
docs/research/shipping.md for how well this tracks what buyers actually pay.
"""

import json
import math
import re
from dataclasses import dataclass
from importlib import resources
from statistics import median_low

from pydantic import BaseModel

PACKING_OUNCES = 12
"""What a box, padding and tape add to an item's own weight."""

_PACKING_INCHES = 1.0
_DIM_WEIGHT_MIN_CUBIC_INCHES = 1728
_DIM_WEIGHT_DIVISOR = 139
_ZONE = re.compile(r"(\d+)")

# The largest city's 3-digit ZIP in each state, for sellers who only give a state.
STATE_ZIP3 = {
    "Alabama": "352", "Alaska": "995", "Arizona": "850", "Arkansas": "722",
    "California": "900", "Colorado": "802", "Connecticut": "061", "Delaware": "197",
    "District of Columbia": "200", "Florida": "331", "Georgia": "303", "Hawaii": "968",
    "Idaho": "837", "Illinois": "606", "Indiana": "462", "Iowa": "503", "Kansas": "672",
    "Kentucky": "402", "Louisiana": "701", "Maine": "041", "Maryland": "212",
    "Massachusetts": "021", "Michigan": "482", "Minnesota": "554", "Mississippi": "392",
    "Missouri": "631", "Montana": "591", "Nebraska": "681", "Nevada": "891",
    "New Hampshire": "031", "New Jersey": "071", "New Mexico": "871", "New York": "100",
    "North Carolina": "282", "North Dakota": "581", "Ohio": "432", "Oklahoma": "731",
    "Oregon": "972", "Pennsylvania": "191", "Rhode Island": "029", "South Carolina": "292",
    "South Dakota": "571", "Tennessee": "372", "Texas": "770", "Utah": "841",
    "Vermont": "054", "Virginia": "232", "Washington": "981", "West Virginia": "253",
    "Wisconsin": "532", "Wyoming": "820",
}  # fmt: skip


@dataclass(frozen=True)
class Box:
    """An item as it sits on the shelf, before packing. Inches and ounces."""

    weight_oz: float | None
    length_in: float | None = None
    width_in: float | None = None
    depth_in: float | None = None


@dataclass(frozen=True)
class Origin:
    """Where a seller ships from, as far as we know it."""

    zip3: str | None = None
    state: str | None = None

    def known_zip3(self) -> str | None:
        return self.zip3 or STATE_ZIP3.get(self.state or "")


@dataclass(frozen=True)
class ShippingEstimate:
    low_cents: int
    typical_cents: int
    high_cents: int


class _ZoneRow(BaseModel):
    ZipCodes: str = ""
    Zone: str = ""


class _ZoneChartAnswer(BaseModel):
    Column0: list[_ZoneRow] = []
    Column1: list[_ZoneRow] = []
    Column2: list[_ZoneRow] = []
    Column3: list[_ZoneRow] = []


@dataclass(frozen=True)
class ZoneChart:
    """Zones from every 3-digit ZIP to ours."""

    zones: dict[str, int]

    @classmethod
    def from_usps(cls, data: object) -> ZoneChart:
        """Read postcalc.usps.com's GetZoneChart answer. Raises pydantic's ValidationError."""
        answer = _ZoneChartAnswer.model_validate(data)
        zones: dict[str, int] = {}
        for row in answer.Column0 + answer.Column1 + answer.Column2 + answer.Column3:
            first, _, last = row.ZipCodes.partition("---")
            zone = _ZONE.match(row.Zone)
            if not (first.isdecimal() and (last or first).isdecimal() and zone):
                continue
            for prefix in range(int(first), int(last or first) + 1):
                zones[f"{prefix:03d}"] = int(zone.group(1))
        return cls(zones)

    def zone_from(self, zip3: str) -> int | None:
        return self.zones.get(zip3)


@dataclass(frozen=True)
class UspsRates:
    effective: str
    rows: list[tuple[float, list[int]]]
    """(max ounces, cents for zones 1 to 9), lightest first."""

    @classmethod
    def load(cls) -> UspsRates:
        text = resources.files("buy_assist.rates").joinpath("usps_ground_advantage.json")
        data = json.loads(text.read_text())
        rows = [(float(r["max_ounces"]), [int(c) for c in r["cents"]]) for r in data["rows"]]
        return cls(effective=data["effective"], rows=rows)

    def price_cents(self, ounces: float, zone: int) -> int | None:
        row = next((cents for max_oz, cents in self.rows if ounces <= max_oz), None)
        return row[zone - 1] if row and 1 <= zone <= len(row) else None


def billable_ounces(box: Box) -> float | None:
    """Packed weight, rounded up the way USPS bills, or dimensional weight if larger."""
    if box.weight_oz is None:
        return None
    packed = box.weight_oz + PACKING_OUNCES
    billed = math.ceil(packed) if packed < 16 else math.ceil(packed / 16) * 16
    dims = (box.length_in, box.width_in, box.depth_in)
    if all(dims):
        volume = math.prod((d or 0) + _PACKING_INCHES for d in dims)
        if volume > _DIM_WEIGHT_MIN_CUBIC_INCHES:
            billed = max(billed, math.ceil(volume / _DIM_WEIGHT_DIVISOR) * 16)
    return billed


def estimate_shipping(
    box: Box, chart: ZoneChart, rates: UspsRates, origin: Origin
) -> ShippingEstimate | None:
    """One price when the seller's zone is known, else the range across our zone chart with
    the price for the typical 3-digit area as the middle figure."""
    ounces = billable_ounces(box)
    if ounces is None:
        return None
    zip3 = origin.known_zip3()
    zone = chart.zone_from(zip3) if zip3 else None
    if zone is not None:
        price = rates.price_cents(ounces, zone)
        return ShippingEstimate(price, price, price) if price is not None else None
    zones = [z for z in chart.zones.values() if z <= 8]
    if not zones:
        return None
    prices = [rates.price_cents(ounces, z) for z in (min(zones), median_low(zones), max(zones))]
    if None in prices:
        return None
    low, typical, high = (p or 0 for p in prices)
    return ShippingEstimate(low, typical, high)
