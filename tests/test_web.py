from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from buy_assist.links import LinkFacts
from buy_assist.models import Condition, ItemFields
from buy_assist.parser import ParseError
from buy_assist.store import Store
from buy_assist.web import create_app
from tests.fixtures import BGG_URL, EXIT_NAME


class FakeParser:
    def __init__(self) -> None:
        self.requests: list[str] = []
        self.fail = False

    def parse(self, text: str, link: LinkFacts | None = None) -> ItemFields:
        self.requests.append(text)
        if self.fail:
            raise ParseError("claude-json exited 1")
        return ItemFields(
            name=EXIT_NAME,
            condition=Condition.NEW,
            reference_url=BGG_URL,
            target_price_cents=3500,
        )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    store = Store.open(tmp_path / "buy.db")
    store.set_home_zip("94110")
    return store


@pytest.fixture
def parser() -> FakeParser:
    return FakeParser()


@pytest.fixture
def client(store: Store, parser: FakeParser, tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(tmp_path / "buy.db", parser)) as client:
        yield client


@pytest.fixture
def shira(client: TestClient, store: Store) -> TestClient:
    shira = store.users()[1]
    client.post("/whoami", data={"user_id": str(shira.id)})
    return client


def item_form(**overrides: str | list[str]) -> dict[str, str | list[str]]:
    form: dict[str, str | list[str]] = {
        "name": EXIT_NAME,
        "condition": "new",
        "upc": "",
        "epid": "",
        "model_number": "",
        "reference_url": BGG_URL,
        "variant": "",
        "zip": "94110",
        "target_price": "35",
        "notes": "",
        "watcher_ids": ["2"],
    }
    return form | overrides


def test_visitor_without_identity_is_asked_who_they_are(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/whoami"


def test_picker_lists_household(client: TestClient) -> None:
    page = client.get("/whoami").text
    assert "Jason" in page
    assert "Shira" in page


def test_unknown_user_cookie_is_asked_again(client: TestClient) -> None:
    client.cookies.set("user_id", "99")
    assert client.get("/", follow_redirects=False).headers["location"] == "/whoami"


def test_picking_unknown_user_is_rejected(client: TestClient) -> None:
    assert client.post("/whoami", data={"user_id": "99"}).status_code == 400


def test_draft_prefills_form_from_claude_with_home_zip_and_current_user(
    shira: TestClient, parser: FakeParser
) -> None:
    page = shira.post("/items/draft", data={"text": f"golden book calendar {BGG_URL}"}).text

    assert parser.requests == [f"golden book calendar {BGG_URL}"]
    assert EXIT_NAME in page
    assert 'name="zip" inputmode="numeric" value=""' in page
    assert 'placeholder="94110"' in page
    assert 'value="35.00"' in page
    assert 'name="watcher_ids" value="2" checked' in page
    assert 'name="watcher_ids" value="1" checked' not in page


def test_draft_falls_back_to_blank_form_with_request_in_notes(
    shira: TestClient, parser: FakeParser
) -> None:
    parser.fail = True
    page = shira.post("/items/draft", data={"text": "wool cardigan size M"}).text

    assert "fill it in by hand" in page
    assert "wool cardigan size M</textarea>" in page


def test_saving_form_creates_item_and_shows_its_page(shira: TestClient, store: Store) -> None:
    response = shira.post("/items", data=item_form(), follow_redirects=False)

    assert response.status_code == 303
    item_id = int(response.headers["location"].removeprefix("/items/"))
    assert store.get_item(item_id) is not None
    page = shira.get(response.headers["location"]).text
    assert EXIT_NAME in page
    assert "$35.00" in page
    assert "Shira" in page
    for section in ("Listings", "Price history", "Sold prices"):
        assert f"<h2>{section}</h2>" in page


def test_saved_item_has_form_values(shira: TestClient, store: Store) -> None:
    shira.post("/items", data=item_form(upc="4002051682026", watcher_ids=["1", "2"]))

    [item] = store.list_items(open_items=True)
    assert item.fields == ItemFields(
        name=EXIT_NAME,
        condition=Condition.NEW,
        upc="4002051682026",
        reference_url=BGG_URL,
        zip="94110",
        target_price_cents=3500,
        watcher_ids=frozenset({1, 2}),
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("name", "  ", "Name is required"),
        ("target_price", "cheap", "Target price must be a dollar amount"),
        ("target_price", "-3", "Target price must be a dollar amount"),
        ("zip", "9411", "ZIP must be 5 digits"),
        ("condition", "mint", "Condition must be new, used or any"),
        ("upc", "12-34", "UPC must be 8 to 14 digits"),
        ("reference_url", "javascript:alert(1)", "Reference link must start with http"),
        ("watcher_ids", ["99"], "Unknown watcher"),
        ("watcher_ids", [], "Pick at least one person to alert"),
    ],
)
def test_invalid_form_is_shown_again_with_error(
    shira: TestClient, store: Store, field: str, value: str | list[str], message: str
) -> None:
    response = shira.post("/items", data=item_form(**{field: value}))

    assert response.status_code == 422
    assert message in response.text
    assert store.list_items(open_items=True) == []


def test_editing_item_changes_its_fields(shira: TestClient, store: Store) -> None:
    location = shira.post("/items", data=item_form(), follow_redirects=False).headers["location"]

    assert 'value="35.00"' in shira.get(f"{location}/edit").text
    shira.post(location, data=item_form(target_price="30", notes="Sealed only"))

    page = shira.get(location).text
    assert "$30.00" in page
    assert "Sealed only" in page


def test_closed_item_moves_from_open_list_to_closed_list(shira: TestClient) -> None:
    location = shira.post("/items", data=item_form(), follow_redirects=False).headers["location"]
    assert EXIT_NAME in shira.get("/").text

    shira.post(f"{location}/close")

    assert EXIT_NAME not in shira.get("/").text
    assert EXIT_NAME in shira.get("/?closed=1").text
    assert "Closed" in shira.get(location).text


def test_unknown_item_is_404(shira: TestClient) -> None:
    assert shira.get("/items/999").status_code == 404
    assert shira.post("/items/999/close").status_code == 404


def test_items_shipping_home_follow_home_zip_changes(shira: TestClient) -> None:
    location = shira.post("/items", data=item_form(zip=""), follow_redirects=False).headers[
        "location"
    ]

    shira.post("/settings", data={"home_zip": "10001"})

    assert 'value="10001"' in shira.get("/settings").text
    assert "home (10001)" in shira.get(location).text


def test_settings_reject_bad_zip(shira: TestClient) -> None:
    assert shira.post("/settings", data={"home_zip": "abc"}).status_code == 422


def test_item_text_is_escaped(shira: TestClient) -> None:
    location = shira.post(
        "/items", data=item_form(name="<script>x</script>"), follow_redirects=False
    ).headers["location"]
    assert "<script>x</script>" not in shira.get(location).text
