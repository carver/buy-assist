from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given
from hypothesis import strategies as st

from buy_assist.models import Condition, ItemFields
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
