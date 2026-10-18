import html
import json
import re
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from buy_assist.models import Marketplace
from buy_assist.store import Store
from tests.web_fakes import (
    PASTED_DOUBT,
    PASTED_MATCH,
    FakeListingSource,
    FakeSalesParser,
    FakeSoldPrices,
    item_form,
)


def add_item(client: TestClient, **overrides: str | list[str]) -> str:
    response = client.post("/items", data=item_form(**overrides), follow_redirects=False)
    return response.headers["location"]


def test_new_bgg_item_gets_sold_prices_right_after_saving(
    member: TestClient, sold: FakeSoldPrices
) -> None:
    location = add_item(member)

    assert len(sold.items) == 1
    page = member.get(location).text
    assert "New in excellent condition" in page
    assert "Played and fully reset" in page


def test_item_without_bgg_link_is_not_looked_up(member: TestClient, sold: FakeSoldPrices) -> None:
    location = add_item(member, reference_url="https://shop.example/pan")

    assert sold.items == []
    assert "No sold prices yet" in member.get(location).text


def test_background_lookup_failure_leaves_item_saved(
    member: TestClient, sold: FakeSoldPrices, store: Store
) -> None:
    sold.error = "BoardGameGeek answered 503."
    location = add_item(member)

    assert len(store.list_items(open_items=True)) == 1
    assert "No sold prices yet" in member.get(location).text


def test_summary_separates_new_and_used(member: TestClient) -> None:
    page = member.get(add_item(member)).text

    new_row = re.search(r'<tr class="summary-new">(.*?)</tr>', page, re.S)
    used_row = re.search(r'<tr class="summary-used">(.*?)</tr>', page, re.S)
    assert new_row is not None
    assert used_row is not None
    assert "$40.00" in new_row.group(1)
    assert "$35.00" in used_row.group(1)


def test_refresh_adds_only_new_sales(member: TestClient, sold: FakeSoldPrices) -> None:
    location = add_item(member)

    page = member.post(f"{location}/sales/refresh").text

    assert "No new sales on BoardGameGeek." in page
    assert page.count("New in excellent condition") == 1


def test_refresh_failure_is_shown(member: TestClient, sold: FakeSoldPrices) -> None:
    location = add_item(member)
    sold.error = "BoardGameGeek answered 503."

    assert "BoardGameGeek answered 503." in member.post(f"{location}/sales/refresh").text


def test_refresh_button_only_for_items_a_source_covers(member: TestClient) -> None:
    assert "Refresh sold prices" in member.get(add_item(member)).text
    other = add_item(member, reference_url="https://shop.example/pan")
    assert "Refresh sold prices" not in member.get(other).text


def test_removing_a_sale_drops_it(member: TestClient, store: Store) -> None:
    location = add_item(member)
    item_id = int(location.rsplit("/", 1)[1])
    [_, used] = store.sales(item_id)

    member.post(f"{location}/sales/{used.id}/remove")

    assert "Played and fully reset" not in member.get(location).text


def test_ebay_sold_search_link_uses_search_words_and_condition(member: TestClient) -> None:
    page = member.get(add_item(member, search_query="EXIT advent golden book")).text

    href = re.search(r'href="(https://www\.ebay\.com/sch/i\.html\?[^"]+)"', page)
    assert href is not None
    query = parse_qs(urlsplit(html.unescape(href.group(1))).query)
    assert query["_nkw"] == ["EXIT advent golden book"]
    assert query["LH_Sold"] == ["1"]
    assert query["LH_Complete"] == ["1"]
    assert query["LH_ItemCondition"] == ["1000"]


def test_ebay_link_falls_back_to_name_without_search_words(member: TestClient) -> None:
    page = member.get(add_item(member, name="Cast iron skillet", condition="any")).text
    href = re.search(r'href="(https://www\.ebay\.com/sch/i\.html\?[^"]+)"', page)
    assert href is not None
    query = parse_qs(urlsplit(html.unescape(href.group(1))).query)
    assert query["_nkw"] == ["Cast iron skillet"]
    assert "LH_ItemCondition" not in query


def test_paste_shows_preview_with_doubtful_rows_unchecked(
    member: TestClient, sales_parser: FakeSalesParser
) -> None:
    location = add_item(member)

    page = member.post(f"{location}/sales/paste", data={"text": "Sold Oct 12, 2025 ..."}).text

    assert sales_parser.pastes[0][0] == "Sold Oct 12, 2025 ..."
    assert re.search(r'name="selected" value="0" checked', page)
    assert re.search(r'name="selected" value="1"(?! checked)', page)
    assert "Replacement parts, not the game" in page


def test_saving_preview_keeps_only_checked_rows(member: TestClient, store: Store) -> None:
    location = add_item(member)
    preview = member.post(f"{location}/sales/paste", data={"text": "pasted"}).text
    rows = re.search(r'name="rows" value="([^"]*)"', preview)
    assert rows is not None

    member.post(
        f"{location}/sales/save", data={"rows": html.unescape(rows.group(1)), "selected": ["0"]}
    )

    page = member.get(location).text
    assert PASTED_MATCH.sale.title in page
    assert PASTED_DOUBT.sale.title not in page


def test_saved_rows_are_always_ebay_sales(member: TestClient, store: Store) -> None:
    location = add_item(member)
    item_id = int(location.rsplit("/", 1)[1])
    forged = [
        {
            "source": "bgg",
            "source_id": "x",
            "sold_on": "2025-01-01",
            "price_cents": 100,
            "shipping_cents": None,
            "is_new": True,
            "condition": "New",
            "title": "forged",
        }
    ]

    member.post(f"{location}/sales/save", data={"rows": json.dumps(forged), "selected": ["0"]})

    saved = [r.sale for r in store.sales(item_id) if r.sale.title == "forged"]
    assert [(s.source, s.source_id) for s in saved] == [(Marketplace.EBAY, None)]


def test_tampered_rows_are_rejected(member: TestClient) -> None:
    location = add_item(member)
    response = member.post(f"{location}/sales/save", data={"rows": "{nope", "selected": ["0"]})
    assert response.status_code == 400


def test_paste_that_claude_cannot_read_says_so(
    member: TestClient, sales_parser: FakeSalesParser
) -> None:
    location = add_item(member)
    sales_parser.fail = True

    page = member.post(f"{location}/sales/paste", data={"text": "garbage"}).text

    assert "Claude couldn&#39;t read that paste" in page


def test_sales_routes_for_unknown_item_are_404(member: TestClient) -> None:
    assert member.post("/items/999/sales/refresh").status_code == 404
    assert member.post("/items/999/sales/paste", data={"text": "x"}).status_code == 404
    assert member.post("/items/999/sales/5/remove").status_code == 404


def test_search_words_are_saved_and_shown(member: TestClient) -> None:
    location = add_item(member, search_query="EXIT advent golden book")
    assert 'value="EXIT advent golden book"' in member.get(f"{location}/edit").text
    assert "<dd>EXIT advent golden book</dd>" in member.get(location).text


def test_forged_rows_are_checked_again_on_save(member: TestClient, store: Store) -> None:
    location = add_item(member)
    item_id = int(location.rsplit("/", 1)[1])
    good = {
        "source": "ebay",
        "source_id": None,
        "sold_on": "2025-01-01",
        "price_cents": 1000,
        "shipping_cents": 100,
        "is_new": True,
        "condition": "New",
        "title": "x" * 5000,
    }
    rows = [
        good,
        good | {"shipping_cents": -1, "title": "negative shipping"},
        good | {"sold_on": "2999-01-01", "title": "future"},
        good | {"price_cents": 0, "title": "free"},
        good | {"condition": " ", "title": "no condition"},
    ]

    response = member.post(
        f"{location}/sales/save",
        data={"rows": json.dumps(rows), "selected": ["0", "1", "2", "3", "4"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    titles = [r.sale.title for r in store.sales(item_id) if r.sale.source is Marketplace.EBAY]
    assert titles == ["x" * 300]


def test_new_item_gets_listings_right_after_saving(
    client: TestClient, store: Store, listing_source: FakeListingSource
) -> None:
    member = store.users()[1]
    client.post("/whoami", data={"user_id": str(member.id)})

    location = add_item(client)

    assert listing_source.calls == 1
    assert "EXIT calendar listing" in client.get(location).text
