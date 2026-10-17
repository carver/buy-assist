from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from buy_assist.models import ItemFields, Listing, Marketplace, NoticeKind
from buy_assist.store import Store

NOW = datetime(2026, 10, 17, 18, 30, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store.open(tmp_path / "buy.db")


def test_people_set_their_own_email(store: Store) -> None:
    shira = store.users()[1]
    store.set_email(shira.id, "shira@example.com")

    assert [u.email for u in store.users()] == [None, "shira@example.com"]
    store.set_email(shira.id, None)
    assert store.users()[1].email is None


def test_home_zip_comes_with_its_confirmed_tax_rate(store: Store) -> None:
    assert store.tax_rate("94114") == Decimal("0.08625")
    assert store.tax_rate("10001") is None


def test_tax_rates_can_be_set_and_cleared(store: Store) -> None:
    store.set_tax_rate("10001", Decimal("0.08875"))
    assert store.tax_rates() == {"10001": Decimal("0.08875"), "94114": Decimal("0.08625")}
    store.set_tax_rate("10001", None)
    assert store.tax_rate("10001") is None


def test_app_address_is_unset_until_saved(store: Store) -> None:
    assert store.app_address() is None
    store.set_app_address("http://box.tail1234.ts.net:6767")
    assert store.app_address() == "http://box.tail1234.ts.net:6767"


def test_job_runs_are_remembered(store: Store) -> None:
    assert store.last_run("fetch") is None
    store.set_last_run("fetch", NOW)
    assert store.last_run("fetch") == NOW


def test_zone_charts_are_cached_by_prefix(store: Store) -> None:
    assert store.zone_chart("941") is None
    store.save_zone_chart("941", date(2026, 10, 17), {"Column0": []})
    assert store.zone_chart("941") == (date(2026, 10, 17), {"Column0": []})


def test_notices_are_logged_once_per_person_and_kind(store: Store) -> None:
    jason, shira = store.users()
    item = store.create_item(ItemFields(name="Calendar"))
    listing = Listing(
        marketplace=Marketplace.BGG,
        external_id="1",
        title="Calendar",
        url="https://boardgamegeek.com/market/product/1",
        price_cents=4000,
        is_new=True,
        condition="New",
    )
    [recorded] = store.record_listings(item.id, Marketplace.BGG, [listing], NOW).new

    assert store.log_notice(recorded.id, shira.id, NoticeKind.DIGEST, NOW)
    assert not store.log_notice(recorded.id, shira.id, NoticeKind.DIGEST, NOW)
    assert store.log_notice(recorded.id, jason.id, NoticeKind.DIGEST, NOW)
    assert store.noticed(recorded.id, shira.id) == {NoticeKind.DIGEST}
