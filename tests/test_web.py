import html
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from buy_assist.links import Edition, LinkError, LinkFacts
from buy_assist.models import Condition, ItemFields
from buy_assist.parser import ParseError
from buy_assist.store import Store
from buy_assist.web import create_app
from tests.fixtures import BGG_URL, EXIT_NAME

IMAGE_URL = "https://cf.geekdo-images.com/pic6632318.jpg"
ENGLISH = Edition(
    name="English edition",
    title=EXIT_NAME,
    product_code="681951",
    year="2022",
    languages=("English",),
    publishers=("KOSMOS",),
)
GERMAN = Edition(
    name="German edition",
    title="EXIT: Das Spiel - Adventskalender",
    product_code=None,
    year="2021",
    languages=("German",),
    publishers=("KOSMOS",),
)
BGG_FACTS = LinkFacts(
    source="BoardGameGeek", name=EXIT_NAME, image_url=IMAGE_URL, editions=(GERMAN, ENGLISH)
)


class FakeParser:
    def __init__(self) -> None:
        self.requests: list[tuple[str, LinkFacts | None]] = []
        self.fail = False

    def parse(self, text: str, link: LinkFacts | None = None) -> ItemFields:
        self.requests.append((text, link))
        if self.fail:
            raise ParseError("claude-json exited 1")
        return ItemFields(
            name=EXIT_NAME,
            condition=Condition.NEW,
            reference_url=BGG_URL,
            image_url=link.image_url if link else None,
            variant="English edition" if link and ENGLISH in link.editions else None,
            model_number="681951" if link and link.editions else None,
            target_price_cents=3500,
        )


class FakeLinkReader:
    def __init__(self) -> None:
        self.urls: list[str] = []
        self.error: str | None = None
        self.facts = BGG_FACTS

    def read(self, url: str) -> LinkFacts:
        self.urls.append(url)
        if self.error:
            raise LinkError(self.error)
        return self.facts


@pytest.fixture
def store(tmp_path: Path) -> Store:
    store = Store.open(tmp_path / "buy.db")
    store.set_home_zip("94110")
    return store


@pytest.fixture
def parser() -> FakeParser:
    return FakeParser()


@pytest.fixture
def links() -> FakeLinkReader:
    return FakeLinkReader()


@pytest.fixture
def client(
    store: Store, parser: FakeParser, links: FakeLinkReader, tmp_path: Path
) -> Iterator[TestClient]:
    with TestClient(create_app(tmp_path / "buy.db", parser, links)) as client:
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

    assert parser.requests == [(f"golden book calendar {BGG_URL}", BGG_FACTS)]
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

    assert "fill in the rest by hand" in page
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


def test_draft_reads_link_and_offers_its_editions(shira: TestClient, links: FakeLinkReader) -> None:
    page = shira.post("/items/draft", data={"text": f"golden book {BGG_URL} please"}).text

    assert links.urls == [BGG_URL]
    assert '<select id="variant" name="variant"' in page
    assert '<option value="English edition" data-title="' in page
    assert 'data-product-code="681951" selected>English edition' in page
    assert ">German edition" in page
    assert f'name="image_url" value="{IMAGE_URL}"' in page
    assert f'<img src="{IMAGE_URL}"' in page
    assert "Read from BoardGameGeek" in page


def test_draft_without_link_skips_reader(
    shira: TestClient, links: FakeLinkReader, parser: FakeParser
) -> None:
    shira.post("/items/draft", data={"text": "wool cardigan size M"})

    assert links.urls == []
    assert parser.requests == [("wool cardigan size M", None)]


def test_unreadable_link_falls_back_to_text_with_notice(
    shira: TestClient, links: FakeLinkReader, parser: FakeParser
) -> None:
    links.error = "The site answered 403."
    page = shira.post("/items/draft", data={"text": f"calendar {BGG_URL}"}).text

    assert parser.requests == [(f"calendar {BGG_URL}", None)]
    assert "Couldn&#39;t read boardgamegeek.com: The site answered 403." in page
    assert EXIT_NAME in page


def test_saved_image_shows_on_item_page_and_survives_edit(shira: TestClient) -> None:
    location = shira.post(
        "/items", data=item_form(image_url=IMAGE_URL), follow_redirects=False
    ).headers["location"]

    assert f'<img src="{IMAGE_URL}"' in shira.get(location).text
    edit_page = shira.get(f"{location}/edit").text
    assert f'name="image_url" value="{IMAGE_URL}"' in edit_page


def test_image_url_must_be_http(shira: TestClient) -> None:
    response = shira.post("/items", data=item_form(image_url="javascript:alert(1)"))
    assert response.status_code == 422
    assert "Image link must start with http" in response.text


def test_settings_list_reference_domains_and_flag_busy_generic_ones(
    shira: TestClient, store: Store
) -> None:
    for n in range(3):
        store.create_item(ItemFields(name=f"Pan {n}", reference_url=f"https://shop.example/{n}"))
    store.create_item(ItemFields(name="Calendar", reference_url=BGG_URL))
    store.create_item(ItemFields(name="Rug", reference_url="https://rugs.example/r"))

    page = shira.get("/settings").text

    assert "shop.example" in page
    assert "boardgamegeek.com" in page
    assert "BoardGameGeek reader" in page
    assert page.count("worth writing a reader") == 1


def test_claude_failure_still_fills_form_from_link(shira: TestClient, parser: FakeParser) -> None:
    parser.fail = True
    page = shira.post("/items/draft", data={"text": f"calendar {BGG_URL}"}).text

    assert "fill in the rest by hand" in page
    assert f'value="{EXIT_NAME}"' in page
    assert f'name="reference_url" type="url" value="{BGG_URL}"' in page
    assert 'data-product-code="681951" selected>English edition' in page
    assert f'name="image_url" value="{IMAGE_URL}"' in page


def test_edition_dropdown_survives_a_validation_error(shira: TestClient) -> None:
    draft_page = shira.post("/items/draft", data={"text": f"calendar {BGG_URL}"}).text
    editions = re.search(r'name="editions" value="([^"]*)"', draft_page)
    assert editions is not None

    response = shira.post(
        "/items",
        data=item_form(target_price="cheap", editions=html.unescape(editions.group(1))),
    )

    assert response.status_code == 422
    assert '<select id="variant" name="variant"' in response.text
    assert ">German edition" in response.text


def test_tampered_editions_field_falls_back_to_text_variant(shira: TestClient) -> None:
    response = shira.post("/items", data=item_form(target_price="cheap", editions="{nope"))
    assert response.status_code == 422
    assert '<input id="variant" name="variant"' in response.text


def test_dropdown_offers_blank_choice_when_no_edition_was_picked(
    shira: TestClient, parser: FakeParser, links: FakeLinkReader
) -> None:
    links.facts = LinkFacts(source="BoardGameGeek", name=EXIT_NAME, editions=(GERMAN,))
    page = shira.post("/items/draft", data={"text": f"calendar {BGG_URL}"}).text
    assert '<option value="" selected>Choose an edition</option>' in page


def test_link_without_host_names_the_link_in_notice(
    shira: TestClient, links: FakeLinkReader
) -> None:
    links.error = "Only http and https links can be read."
    page = shira.post("/items/draft", data={"text": "see http://:80/x"}).text
    assert "Couldn&#39;t read http://:80/x" in page
