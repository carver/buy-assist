#!/usr/bin/env python3
"""Turn USPS Notice 123 into the Ground Advantage commercial price table the app uses.

USPS changes prices two or three times a year. To update:

    curl -sSA "Mozilla/5.0" https://pe.usps.com/text/dmm300/Notice123.htm -o notice123.htm
    uv run python scripts/usps_rates.py notice123.htm \\
        > src/buy_assist/rates/usps_ground_advantage.json

then check the diff and commit.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

_EFFECTIVE = re.compile(r"Effective\s+(\w+ \d{1,2}, \d{4})")
_PRICE = re.compile(r"\$?(\d+\.\d\d)")
_OUNCES = re.compile(r"([\d.]+) oz")
_ZONES = 9


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("notice", type=Path, help="Notice123.htm saved from pe.usps.com")
    args = parser.parse_args()
    html = args.notice.read_text(encoding="utf-8", errors="replace")
    lines = [s.strip() for s in BeautifulSoup(html, "html.parser").get_text("\n").split("\n")]
    lines = [s for s in lines if s]
    effective = next((m.group(1) for s in lines if (m := _EFFECTIVE.search(s))), None)
    start = _section_start(lines)
    rows = _price_rows(lines, start)
    if not rows or effective is None:
        print("Couldn't find the Ground Advantage commercial table.", file=sys.stderr)
        return 1
    json.dump(
        {
            "service": "USPS Ground Advantage, commercial parcels",
            "source": "https://pe.usps.com/text/dmm300/Notice123.htm",
            "effective": effective,
            "zones": list(range(1, _ZONES + 1)),
            "rows": rows,
        },
        sys.stdout,
        indent=1,
    )
    print()
    return 0


def _section_start(lines: list[str]) -> int:
    for i, line in enumerate(lines):
        if line == "USPS Ground Advantage" and "Commercial-Parcels" in lines[i + 1 : i + 4]:
            return i
    raise SystemExit("No 'Commercial-Parcels' section in that file.")


def _price_rows(lines: list[str], start: int) -> list[dict[str, object]]:
    """Rows of {max_ounces, cents: [zone 1..9]} from 'weight, price x 9' runs."""
    rows: list[dict[str, object]] = []
    i = lines.index("9", start) + 1
    while i + _ZONES < len(lines):
        weight = _ounces(lines[i])
        prices = [_PRICE.fullmatch(s) for s in lines[i + 1 : i + 1 + _ZONES]]
        if weight is None or not all(prices):
            break
        cents = [round(float(m.group(1)) * 100) for m in prices if m]
        rows.append({"max_ounces": weight, "cents": cents})
        i += 1 + _ZONES
    return rows


def _ounces(label: str) -> float | None:
    if match := _OUNCES.fullmatch(label):
        return float(match.group(1))
    if label.isdecimal():
        return int(label) * 16.0
    return None


if __name__ == "__main__":
    raise SystemExit(main())
