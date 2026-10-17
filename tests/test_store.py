from dataclasses import replace
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given
from hypothesis import strategies as st

from buy_assist.models import Condition, ItemFields, Sale, SaleSource
from buy_assist.prices import PriceStats, summarize_sales
from buy_assist.store import ItemNotFoundError, Store
from tests.fixtures import BGG_URL, EXIT_NAME


def test_new_store_has_household_users(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "buy.db")
    assert [u.name for u in store.users()] == ["Jason", "Shira"]


def exit_calendar(watcher_ids: frozenset[int]) -> ItemFields:
    return ItemFields(
        name=EXIT_NAME,
        condition=Condition.NEW,
        model_number="681951",
        reference_url=BGG_URL,
        variant="English edition",
        zip="94110",
        target_price_cents=3500,
        notes="Unopened only",
        image_url="https://cf.geekdo-images.com/pic6632318.jpg",
        search_query="EXIT advent calendar golden book",
        watcher_ids=watcher_ids,
    )


def test_created_item_reads_back_open_with_its_fields(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "buy.db")
    shira = store.users()[1]
    fields = exit_calendar(frozenset({shira.id}))

    item = store.create_item(fields)

    assert store.get_item(item.id) == item
    assert item.fields == fields
    assert item.is_open


def test_missing_item_is_none(tmp_path: Path) -> None:
    assert Store.open(tmp_path / "buy.db").get_item(999) is None


def test_update_replaces_fields_and_watchers(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "buy.db")
    jason, shira = store.users()
    item = store.create_item(exit_calendar(frozenset({shira.id})))
    changed = replace(
        item.fields, target_price_cents=None, notes="", watcher_ids=frozenset({jason.id})
    )

    updated = store.update_item(item.id, changed)

    assert updated.fields == changed
    assert store.get_item(item.id) == updated


def test_closed_item_leaves_open_list_and_appears_in_closed_list(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "buy.db")
    first = store.create_item(ItemFields(name="Wool cardigan"))
    second = store.create_item(ItemFields(name="Cast iron pan"))

    closed = store.close_item(first.id)

    assert not closed.is_open
    assert [i.id for i in store.list_items(open_items=True)] == [second.id]
    assert [i.id for i in store.list_items(open_items=False)] == [first.id]


def test_home_zip_is_unset_until_saved(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "buy.db")
    assert store.home_zip() is None
    store.set_home_zip("94110")
    assert Store.open(tmp_path / "buy.db").home_zip() == "94110"


optional_text = st.none() | st.text(min_size=1, max_size=40)


@given(
    fields=st.builds(
        ItemFields,
        name=st.text(min_size=1, max_size=80),
        condition=st.sampled_from(Condition),
        upc=optional_text,
        epid=optional_text,
        model_number=optional_text,
        reference_url=optional_text,
        image_url=optional_text,
        search_query=optional_text,
        variant=optional_text,
        zip=optional_text,
        target_price_cents=st.none() | st.integers(min_value=0, max_value=10**9),
        notes=st.text(max_size=200),
        watcher_ids=st.frozensets(st.sampled_from([1, 2])),
    )
)
def test_any_item_fields_round_trip(fields: ItemFields) -> None:
    with TemporaryDirectory() as tmp:
        store = Store.open(Path(tmp) / "buy.db")
        item = store.create_item(fields)
        assert store.get_item(item.id) == item
        assert item.fields == fields


def test_changing_missing_item_raises_not_found(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "buy.db")
    with pytest.raises(ItemNotFoundError):
        store.update_item(999, ItemFields(name="Ghost"))
    with pytest.raises(ItemNotFoundError):
        store.close_item(999)


def test_reference_domains_count_open_and_closed_items(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "buy.db")
    store.create_item(ItemFields(name="Calendar", reference_url=BGG_URL))
    other_game = "https://www.boardgamegeek.com/boardgame/13/catan"
    store.close_item(store.create_item(ItemFields(name="Catan", reference_url=other_game)).id)
    store.create_item(ItemFields(name="Pan", reference_url="https://example.com/pan"))
    store.create_item(ItemFields(name="Cardigan"))

    assert store.reference_domain_counts() == {"boardgamegeek.com": 2, "example.com": 1}


def bgg_sale(source_id: str, sold_on: date, dollars: int, condition: str = "New") -> Sale:
    return Sale(
        source=SaleSource.BGG,
        source_id=source_id,
        sold_on=sold_on,
        price_cents=dollars * 100,
        is_new=condition == "New",
        condition=condition,
    )


def ebay_sale(sold_on: date, dollars: int, title: str, shipping: int | None = None) -> Sale:
    return Sale(
        source=SaleSource.EBAY,
        sold_on=sold_on,
        price_cents=dollars * 100,
        shipping_cents=None if shipping is None else shipping * 100,
        is_new=False,
        condition="Pre-owned",
        title=title,
    )


def test_sales_read_back_newest_first_per_item(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "buy.db")
    item = store.create_item(ItemFields(name="Calendar"))
    other = store.create_item(ItemFields(name="Pan"))
    older = bgg_sale("1", date(2023, 5, 4), 35, "Very Good")
    newer = ebay_sale(date(2025, 1, 2), 30, "EXIT calendar, used", shipping=6)

    assert store.add_sales(item.id, [older, newer]) == 2
    store.add_sales(other.id, [bgg_sale("9", date(2024, 1, 1), 99)])

    assert [r.sale for r in store.sales(item.id)] == [newer, older]
    assert all(r.item_id == item.id for r in store.sales(item.id))


def test_same_sales_added_twice_are_kept_once(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "buy.db")
    item = store.create_item(ItemFields(name="Calendar"))
    sales = [
        bgg_sale("1", date(2023, 5, 4), 35),
        ebay_sale(date(2025, 1, 2), 30, "EXIT calendar"),
    ]
    store.add_sales(item.id, sales)

    assert store.add_sales(item.id, sales) == 0
    assert len(store.sales(item.id)) == 2


def test_bgg_sale_with_new_id_but_same_details_is_a_different_sale(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "buy.db")
    item = store.create_item(ItemFields(name="Calendar"))
    store.add_sales(item.id, [bgg_sale("1", date(2023, 5, 4), 40)])
    assert store.add_sales(item.id, [bgg_sale("2", date(2023, 5, 4), 40)]) == 1


def test_removed_sale_is_gone_and_only_from_its_item(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "buy.db")
    item = store.create_item(ItemFields(name="Calendar"))
    other = store.create_item(ItemFields(name="Pan"))
    store.add_sales(item.id, [bgg_sale("1", date(2023, 5, 4), 35)])
    [recorded] = store.sales(item.id)

    assert not store.remove_sale(other.id, recorded.id)
    assert store.remove_sale(item.id, recorded.id)
    assert store.sales(item.id) == []


def test_summary_splits_new_and_used_prices(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "buy.db")
    item = store.create_item(ItemFields(name="Calendar"))
    store.add_sales(
        item.id,
        [
            bgg_sale("1", date(2025, 1, 1), 40),
            bgg_sale("2", date(2025, 1, 2), 36),
            bgg_sale("3", date(2025, 1, 3), 50),
            bgg_sale("4", date(2025, 1, 4), 35, "Very Good"),
            bgg_sale("5", date(2025, 1, 5), 20, "Good"),
        ],
    )

    summary = summarize_sales([r.sale for r in store.sales(item.id)])

    assert summary.new == PriceStats(count=3, low_cents=3600, median_cents=4000, high_cents=5000)
    assert summary.used == PriceStats(count=2, low_cents=2000, median_cents=2750, high_cents=3500)


def test_summary_of_no_sales_is_empty() -> None:
    summary = summarize_sales([])
    assert summary.new is None
    assert summary.used is None
