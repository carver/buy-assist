import re
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from buy_assist.models import Listing, Marketplace
from buy_assist.store import Store
from buy_assist.web import Services, create_app
from tests.web_fakes import (
    FakeLinkReader,
    FakeParser,
    FakeSalesParser,
    FakeSoldPrices,
    FixedQuoter,
    item_form,
)

SEEN = datetime(2026, 10, 17, 18, tzinfo=UTC)


def add_item(client: TestClient, **overrides: str | list[str]) -> int:
    location = client.post("/items", data=item_form(**overrides), follow_redirects=False)
    return int(location.headers["location"].rsplit("/", 1)[1])


def bgg_listing(external_id: str, dollars: int) -> Listing:
    return Listing(
        marketplace=Marketplace.BGG,
        external_id=external_id,
        title=f"EXIT calendar #{external_id}",
        url=f"https://boardgamegeek.com/market/product/{external_id}",
        price_cents=dollars * 100,
        is_new=True,
        condition="New",
        origin_state="Louisiana",
    )


def test_people_set_their_email_addresses(shira: TestClient, store: Store) -> None:
    jason, shira_user = store.users()
    shira.post(
        "/settings/people",
        data={f"email_{jason.id}": "", f"email_{shira_user.id}": "shira@example.com"},
    )

    assert [u.email for u in store.users()] == [None, "shira@example.com"]
    assert 'value="shira@example.com"' in shira.get("/settings").text


def test_bad_email_is_refused(shira: TestClient, store: Store) -> None:
    shira_user = store.users()[1]
    response = shira.post("/settings/people", data={f"email_{shira_user.id}": "not-an-email"})
    assert response.status_code == 422
    assert "doesn&#39;t look like an email address" in response.text


def test_app_address_is_saved(shira: TestClient, store: Store) -> None:
    shira.post("/settings/app", data={"app_address": "http://box.example.ts.net:6767"})
    assert store.app_address() == "http://box.example.ts.net:6767"
    assert shira.post("/settings/app", data={"app_address": "ftp://x"}).status_code == 422


def test_tax_rates_are_listed_and_edited(shira: TestClient, store: Store) -> None:
    assert "8.625%" in shira.get("/settings").text

    shira.post("/settings/tax", data={"zip": "10001", "rate_percent": "8.875"})
    assert store.tax_rate("10001") == Decimal("0.08875")

    shira.post("/settings/tax", data={"zip": "10001", "rate_percent": ""})
    assert store.tax_rate("10001") is None


def test_bad_tax_rate_is_refused(shira: TestClient) -> None:
    assert (
        shira.post("/settings/tax", data={"zip": "10001", "rate_percent": "lots"}).status_code
        == 422
    )
    assert shira.post("/settings/tax", data={"zip": "1", "rate_percent": "8"}).status_code == 422
    assert (
        shira.post("/settings/tax", data={"zip": "10001", "rate_percent": "40"}).status_code == 422
    )


def test_item_page_lists_listings_with_full_prices(shira: TestClient, store: Store) -> None:
    item_id = add_item(shira)
    store.record_listings(
        item_id, Marketplace.BGG, [bgg_listing("1", 45), bgg_listing("2", 40)], SEEN
    )

    page = shira.get(f"/items/{item_id}").text

    section = re.search(r"<h2>Listings</h2>(.*?)</section>", page, re.S)
    assert section is not None
    assert section.group(1).index("EXIT calendar #2") < section.group(1).index("EXIT calendar #1")
    assert "$50.00 all-in" in section.group(1)
    assert "~$10.00 ship" in section.group(1)
    assert "Louisiana" in section.group(1)


def test_gone_listings_move_to_price_history(shira: TestClient, store: Store) -> None:
    item_id = add_item(shira)
    store.record_listings(item_id, Marketplace.BGG, [bgg_listing("1", 45)], SEEN)
    store.record_listings(item_id, Marketplace.BGG, [], datetime(2026, 10, 20, tzinfo=UTC))

    page = shira.get(f"/items/{item_id}").text

    history = re.search(r"<h2>Price history</h2>(.*?)</section>", page, re.S)
    assert history is not None
    assert "EXIT calendar #1" in history.group(1)
    assert "gone Oct 20" in history.group(1)


def test_sold_summary_shows_typical_all_in_price(shira: TestClient) -> None:
    page = shira.get(f"/items/{add_item(shira)}").text

    new_row = re.search(r'<tr class="summary-new">(.*?)</tr>', page, re.S)
    assert new_row is not None
    assert "$50.00" in new_row.group(1)  # $40 median + ~$10 shipping, no tax on BGG sales


def test_app_runs_the_watch_while_it_is_up(tmp_path: Path) -> None:
    db = tmp_path / "watched.db"
    services = Services(
        parser=FakeParser(),
        links=FakeLinkReader(),
        sold_prices=FakeSoldPrices(),
        sales_parser=FakeSalesParser(),
        listing_sources=[],
        shipping=FixedQuoter(),
        mailer=None,
    )
    store = Store.open(db)

    with TestClient(create_app(db, services, watch_every=timedelta(hours=1))):
        deadline = time.monotonic() + 5
        while store.last_run("fetch") is None and time.monotonic() < deadline:
            time.sleep(0.05)

    assert store.last_run("fetch") is not None
