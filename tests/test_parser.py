from typing import Any

import pytest

from buy_assist.models import Condition, ItemFields
from buy_assist.parser import ClaudeItemParser, ParseError
from tests.fixtures import BGG_URL, EXIT_NAME

REQUEST = f"EXIT advent calendar golden book, new, under $35 {BGG_URL}"


def answer(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": EXIT_NAME,
        "condition": "new",
        "upc": None,
        "epid": None,
        "model_number": None,
        "reference_url": BGG_URL,
        "variant": None,
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


def test_drops_reference_url_not_in_request() -> None:
    draft = parser_answering(answer(reference_url="https://evil.example/x")).parse(REQUEST)
    assert draft.reference_url is None


def test_drops_upc_that_is_not_digits() -> None:
    draft = parser_answering(answer(upc="ABC-123")).parse(REQUEST)
    assert draft.upc is None


def test_keeps_valid_upc() -> None:
    draft = parser_answering(answer(upc=" 4002051682026 ")).parse(REQUEST)
    assert draft.upc == "4002051682026"


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


@pytest.mark.live
def test_live_claude_parses_bgg_request() -> None:
    draft = ClaudeItemParser().parse(REQUEST)

    assert "golden book" in draft.name.lower()
    assert draft.reference_url == BGG_URL
    assert draft.condition is Condition.NEW
    assert draft.target_price_cents == 3500
