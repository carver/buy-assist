from typing import Any

import pytest

from buy_assist.links import Edition, LinkFacts
from buy_assist.models import Condition, ItemFields
from buy_assist.parser import ClaudeItemParser, ParseError
from tests.fixtures import BGG_URL, EXIT_NAME, recorded_bgg_facts

REQUEST = f"EXIT advent calendar golden book, new, under $35 {BGG_URL}"


def answer(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": EXIT_NAME,
        "condition": "new",
        "upc": None,
        "epid": None,
        "model_number": None,
        "variant": None,
        "edition": None,
        "target_price_dollars": 35,
        "notes": "",
    }
    return base | overrides


def parser_answering(reply: dict[str, Any]) -> ClaudeItemParser:
    return ClaudeItemParser(run=lambda prompt, schema: reply)


def test_parses_claude_answer_into_draft() -> None:
    draft = parser_answering(answer()).parse(REQUEST)

    assert draft == ItemFields(
        name=EXIT_NAME,
        condition=Condition.NEW,
        reference_url=BGG_URL,
        target_price_cents=3500,
    )


def test_request_text_reaches_claude() -> None:
    prompts: list[str] = []

    def run(prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        prompts.append(prompt)
        return answer()

    ClaudeItemParser(run=run).parse(REQUEST)

    assert REQUEST in prompts[0]


def test_reference_url_is_first_link_in_request() -> None:
    draft = parser_answering(answer()).parse(f"golden book {BGG_URL} or https://other.example/")
    assert draft.reference_url == BGG_URL


def test_request_without_link_has_no_reference_url() -> None:
    assert parser_answering(answer()).parse("golden book calendar").reference_url is None


def test_drops_upc_that_is_not_digits() -> None:
    draft = parser_answering(answer(upc="ABC-123")).parse(f"{REQUEST} upc ABC-123")
    assert draft.upc is None


def test_keeps_valid_upc_written_in_request() -> None:
    draft = parser_answering(answer(upc=" 4002051682026 ")).parse(f"{REQUEST} 4002051682026")
    assert draft.upc == "4002051682026"


def test_drops_upc_found_nowhere_in_request_or_link() -> None:
    draft = parser_answering(answer(upc="4002051682026")).parse(REQUEST)
    assert draft.upc is None


@pytest.mark.parametrize("price", [-5, float("nan"), float("inf")])
def test_drops_impossible_target_price(price: float) -> None:
    draft = parser_answering(answer(target_price_dollars=price)).parse(REQUEST)
    assert draft.target_price_cents is None


def test_rounds_target_price_to_cents() -> None:
    draft = parser_answering(answer(target_price_dollars=59.999)).parse(REQUEST)
    assert draft.target_price_cents == 6000


def test_blank_strings_become_none() -> None:
    draft = parser_answering(answer(variant="  ", model_number="")).parse(REQUEST)
    assert draft.variant is None
    assert draft.model_number is None


def test_unknown_condition_means_any() -> None:
    draft = parser_answering(answer(condition="mint")).parse(REQUEST)
    assert draft.condition is Condition.ANY


def test_runner_failure_is_parse_error() -> None:
    def fail(prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise ParseError("claude-json exited 1")

    with pytest.raises(ParseError):
        ClaudeItemParser(run=fail).parse(REQUEST)


def test_malformed_answer_is_parse_error() -> None:
    with pytest.raises(ParseError):
        parser_answering({"name": 7}).parse(REQUEST)


ENGLISH = Edition(
    name="English edition",
    title="EXIT: The Game - Advent Calendar: The Hunt for the Golden Book",
    product_code="681951",
    year="2022",
    languages=("English",),
    publishers=("KOSMOS",),
)
GERMAN = Edition(
    name="German edition",
    title="EXIT: Das Spiel - Adventskalender: Die Jagd nach dem goldenen Buch",
    product_code=None,
    year="2021",
    languages=("German",),
    publishers=("KOSMOS",),
)
BGG_FACTS = LinkFacts(
    source="BoardGameGeek",
    name="EXIT: The Game - Advent Calendar: The Hunt for the Golden Book",
    image_url="https://cf.geekdo-images.com/pic6632318.jpg",
    editions=(GERMAN, ENGLISH),
)
SKILLET_URL = "https://shop.example/skillet"
SKILLET_FACTS = LinkFacts(
    source="shop.example",
    name="Lodge 10.25 Inch Cast Iron Skillet",
    image_url="https://shop.example/img/skillet-1.jpg",
    upc="075536250112",
    model_number="L8SK3",
    page_text="Lodge Cast Iron Skillet, 10.25 in. Pre-seasoned. Price: $24.90",
)


def test_chosen_edition_sets_name_variant_and_product_code() -> None:
    draft = parser_answering(answer(edition="English edition")).parse(REQUEST, BGG_FACTS)

    assert draft == ItemFields(
        name=ENGLISH.title,
        condition=Condition.NEW,
        model_number="681951",
        reference_url=BGG_URL,
        image_url="https://cf.geekdo-images.com/pic6632318.jpg",
        variant="English edition",
        target_price_cents=3500,
    )


def test_edition_matching_text_is_kept() -> None:
    draft = parser_answering(answer(edition="German edition")).parse(REQUEST, BGG_FACTS)
    assert draft.variant == "German edition"
    assert draft.name == GERMAN.title
    assert draft.model_number is None


@pytest.mark.parametrize("edition", [None, "Klingon edition"])
def test_missing_or_unknown_edition_defaults_to_english(edition: str | None) -> None:
    draft = parser_answering(answer(edition=edition)).parse(REQUEST, BGG_FACTS)
    assert draft.variant == "English edition"


def test_link_without_editions_keeps_claudes_variant_and_uses_link_name() -> None:
    draft = parser_answering(answer(name="skillet", variant="10.25 inch")).parse(
        f"cast iron skillet {SKILLET_URL}", SKILLET_FACTS
    )

    assert draft.name == "Lodge 10.25 Inch Cast Iron Skillet"
    assert draft.variant == "10.25 inch"
    assert draft.image_url == "https://shop.example/img/skillet-1.jpg"


def test_identifiers_come_from_link_when_claude_has_none() -> None:
    draft = parser_answering(answer()).parse(f"skillet {SKILLET_URL}", SKILLET_FACTS)
    assert draft.upc == "075536250112"
    assert draft.model_number == "L8SK3"


def test_claude_upc_found_in_page_text_is_kept() -> None:
    facts = LinkFacts(source="shop.example", page_text="UPC 075536250112, cast iron")
    draft = parser_answering(answer(upc="075536250112")).parse(f"pan {SKILLET_URL}", facts)
    assert draft.upc == "075536250112"


def test_link_facts_reach_claude_as_data_with_edition_choices() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def run(prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        calls.append((prompt, schema))
        return answer()

    ClaudeItemParser(run=run).parse(f"skillet {SKILLET_URL}", SKILLET_FACTS)
    ClaudeItemParser(run=run).parse(REQUEST, BGG_FACTS)

    skillet_prompt, _ = calls[0]
    assert "<link>" in skillet_prompt
    assert "Pre-seasoned. Price: $24.90" in skillet_prompt
    bgg_prompt, bgg_schema = calls[1]
    assert "German edition" in bgg_prompt
    assert bgg_schema["properties"]["edition"]["enum"] == [
        "German edition",
        "English edition",
        None,
    ]


@pytest.mark.live
def test_live_claude_picks_edition_from_bgg_facts() -> None:
    draft = ClaudeItemParser().parse(
        f"EXIT golden book advent calendar in Dutch please {BGG_URL}", recorded_bgg_facts()
    )
    assert draft.variant is not None
    assert draft.variant.startswith("Dutch edition")
    assert draft.model_number == "999-EXI22"


@pytest.mark.live
def test_live_claude_parses_bgg_request() -> None:
    draft = ClaudeItemParser().parse(REQUEST)

    assert "golden book" in draft.name.lower()
    assert draft.reference_url == BGG_URL
    assert draft.condition is Condition.NEW
    assert draft.target_price_cents == 3500


def test_upc_inside_a_longer_number_is_not_found() -> None:
    draft = parser_answering(answer(upc="12345678")).parse(f"{REQUEST} order 9912345678")
    assert draft.upc is None


def test_drops_model_number_and_epid_found_nowhere() -> None:
    draft = parser_answering(answer(model_number="XK-9", epid="2254799")).parse(REQUEST)
    assert draft.model_number is None
    assert draft.epid is None


def test_keeps_model_number_and_epid_written_in_request() -> None:
    draft = parser_answering(answer(model_number="XK-9", epid="2254799")).parse(
        f"{REQUEST} model XK-9 epid 2254799"
    )
    assert draft.model_number == "XK-9"
    assert draft.epid == "2254799"


def test_no_edition_and_no_english_edition_leaves_edition_unset() -> None:
    facts = LinkFacts(source="BoardGameGeek", name="Calendar", editions=(GERMAN,))
    draft = parser_answering(answer(edition=None)).parse(REQUEST, facts)
    assert draft.variant is None
