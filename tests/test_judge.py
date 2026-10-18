from typing import Any

import pytest

from buy_assist.claude import ParseError
from buy_assist.judge import ClaudeListingJudge
from buy_assist.models import ItemFields, Listing, Marketplace

ITEM = ItemFields(name="EXIT calendar", variant="English edition")


def listing(n: int, title: str) -> Listing:
    return Listing(
        marketplace=Marketplace.EBAY,
        external_id=f"v1|{n}|0",
        title=title,
        url=f"https://www.ebay.com/itm/{n}",
        price_cents=2000,
        is_new=True,
        condition="New",
    )


class Recorder:
    def __init__(self, verdicts: list[dict[str, Any]]) -> None:
        self.verdicts = verdicts
        self.prompts: list[str] = []

    def __call__(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        return {"verdicts": self.verdicts}


def test_rejections_name_listings_that_arent_the_item() -> None:
    run = Recorder(
        [
            {"n": 1, "matches": True, "reason": ""},
            {"n": 2, "matches": False, "reason": "Box only"},
            {"n": 3, "matches": False, "reason": ""},
        ]
    )
    listings = [listing(1, "EXIT calendar"), listing(2, "EXIT calendar EMPTY BOX"), listing(3, "x")]

    rejected = ClaudeListingJudge(run).rejections(ITEM, listings)

    assert rejected == {"v1|2|0": "Box only", "v1|3|0": "Claude judged it isn't this item."}
    [prompt] = run.prompts
    assert "<item>" in prompt and "English edition" in prompt
    assert '"n": 2, "title": "EXIT calendar EMPTY BOX"' in prompt


def test_verdicts_for_listings_that_dont_exist_are_ignored() -> None:
    run = Recorder([{"n": 0, "matches": False, "reason": "x"}, {"n": 9, "matches": False}])
    assert ClaudeListingJudge(run).rejections(ITEM, [listing(1, "EXIT calendar")]) == {}


def test_many_listings_are_judged_in_batches() -> None:
    run = Recorder([{"n": 50, "matches": False, "reason": "Lot of 3"}])
    listings = [listing(n, f"EXIT calendar {n}") for n in range(1, 121)]

    rejected = ClaudeListingJudge(run).rejections(ITEM, listings)

    assert len(run.prompts) == 3
    assert rejected == {"v1|50|0": "Lot of 3", "v1|100|0": "Lot of 3"}


def test_malformed_answer_is_parse_error() -> None:
    with pytest.raises(ParseError):
        ClaudeListingJudge(lambda p, s: {"verdicts": "none"}).rejections(ITEM, [listing(1, "x")])


@pytest.mark.live
def test_live_claude_drops_near_misses() -> None:
    listings = [
        listing(1, "EXIT The Game Advent Calendar Hunt for the Golden Book KOSMOS new"),
        listing(2, "EXIT Advent Calendar Golden Book - EMPTY BOX ONLY, no puzzles"),
        listing(3, "EXIT Adventskalender Die Jagd nach dem goldenen Buch (German)"),
    ]
    item = ItemFields(
        name="EXIT: The Game Advent Calendar: The Hunt for the Golden Book",
        variant="English edition",
    )

    rejected = ClaudeListingJudge().rejections(item, listings)

    assert set(rejected) == {"v1|2|0", "v1|3|0"}
