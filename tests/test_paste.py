from datetime import date
from typing import Any

import pytest

from buy_assist.claude import ParseError
from buy_assist.models import Condition, ItemFields, Marketplace, Sale
from buy_assist.paste import ClaudeSalesParser, PastedSale
from tests.fixtures import DATA, EXIT_NAME

PASTE = (DATA / "ebay_sold_paste.txt").read_text()
TODAY = date(2025, 10, 17)
ITEM = ItemFields(name=EXIT_NAME, condition=Condition.NEW, variant="English edition")


def row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "sold_on": "2025-10-12",
        "price_dollars": 32.99,
        "shipping_dollars": 5.85,
        "condition": "Brand New",
        "is_new": True,
        "title": "EXIT The Game Advent Calendar The Hunt for the Golden Book KOSMOS New Sealed",
        "matches": True,
        "reason": "",
    }
    return base | overrides


def parser_answering(*rows: dict[str, Any]) -> ClaudeSalesParser:
    return ClaudeSalesParser(run=lambda prompt, schema: {"sales": list(rows)}, today=lambda: TODAY)


def test_rows_become_ebay_sales() -> None:
    [pasted] = parser_answering(row()).parse(PASTE, ITEM)

    assert pasted == PastedSale(
        sale=Sale(
            source=Marketplace.EBAY,
            sold_on=date(2025, 10, 12),
            price_cents=3299,
            shipping_cents=585,
            is_new=True,
            condition="Brand New",
            title="EXIT The Game Advent Calendar The Hunt for the Golden Book KOSMOS New Sealed",
        ),
        matches=True,
        reason="",
    )


def test_free_delivery_is_zero_shipping_and_unknown_is_none() -> None:
    rows = parser_answering(
        row(price_dollars=18.50, shipping_dollars=0),
        row(price_dollars=29.95, shipping_dollars=None),
    ).parse(PASTE, ITEM)
    assert [r.sale.shipping_cents for r in rows] == [0, None]


def test_doubtful_rows_keep_claudes_reason() -> None:
    [pasted] = parser_answering(
        row(price_dollars=4.00, matches=False, reason="Replacement parts, not the game")
    ).parse(PASTE, ITEM)
    assert not pasted.matches
    assert pasted.reason == "Replacement parts, not the game"


@pytest.mark.parametrize(
    "bad",
    [
        {"sold_on": "2025-13-01"},
        {"sold_on": "2026-01-01"},
        {"sold_on": "1999-12-31"},
        {"price_dollars": 0},
        {"price_dollars": -3},
        {"price_dollars": float("nan")},
        {"price_dollars": 77.77},
        {"shipping_dollars": -1},
        {"title": "  "},
    ],
)
def test_impossible_or_invented_rows_are_dropped(bad: dict[str, Any]) -> None:
    assert parser_answering(row(**bad)).parse(PASTE, ITEM) == []


def test_item_details_and_paste_reach_claude() -> None:
    prompts: list[str] = []

    def run(prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        prompts.append(prompt)
        return {"sales": []}

    ClaudeSalesParser(run=run, today=lambda: TODAY).parse(PASTE, ITEM)

    assert EXIT_NAME in prompts[0]
    assert "English edition" in prompts[0]
    assert "REPLACEMENT PARTS ONLY" in prompts[0]


def test_huge_paste_is_refused() -> None:
    with pytest.raises(ParseError):
        parser_answering().parse("x" * 200_001, ITEM)


def test_malformed_answer_is_parse_error() -> None:
    parser = ClaudeSalesParser(run=lambda prompt, schema: {"sales": "none"}, today=lambda: TODAY)
    with pytest.raises(ParseError):
        parser.parse(PASTE, ITEM)


@pytest.mark.live
def test_live_claude_reads_sample_paste() -> None:
    rows = ClaudeSalesParser(today=lambda: TODAY).parse(PASTE, ITEM)

    assert len(rows) == 5
    by_price = {r.sale.price_cents: r for r in rows}
    assert by_price[3299].matches
    assert by_price[3299].sale.shipping_cents == 585
    assert by_price[1850].sale.shipping_cents == 0
    assert not by_price[1850].sale.is_new
    assert not by_price[400].matches
    assert not by_price[2400].matches
