from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from buy_assist.models import ItemFields, Listing, Marketplace
from buy_assist.store import Store

DAY1 = datetime(2026, 10, 1, 18, tzinfo=UTC)
DAY2 = datetime(2026, 10, 2, 18, tzinfo=UTC)


def listing(external_id: str, dollars: int) -> Listing:
    return Listing(
        marketplace=Marketplace.BGG,
        external_id=external_id,
        title=f"Calendar {external_id}",
        url=f"https://boardgamegeek.com/market/product/{external_id}",
        price_cents=dollars * 100,
        is_new=True,
        condition="New",
    )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store.open(tmp_path / "buy.db")


def test_first_sighting_records_listings_as_new(store: Store) -> None:
    item = store.create_item(ItemFields(name="Calendar"))

    changes = store.record_listings(item.id, Marketplace.BGG, [listing("1", 40)], DAY1)

    assert [r.listing for r in changes.new] == [listing("1", 40)]
    [recorded] = store.listings(item.id)
    assert recorded.first_seen == DAY1
    assert recorded.ended_at is None


def test_later_sighting_finds_new_repriced_and_gone_listings(store: Store) -> None:
    item = store.create_item(ItemFields(name="Calendar"))
    store.record_listings(item.id, Marketplace.BGG, [listing("1", 40), listing("2", 38)], DAY1)

    changes = store.record_listings(
        item.id, Marketplace.BGG, [listing("1", 35), listing("3", 30)], DAY2
    )

    assert [r.listing.external_id for r in changes.new] == ["3"]
    assert [r.listing.price_cents for r in changes.repriced] == [3500]
    assert [r.listing.external_id for r in changes.ended] == ["2"]
    active = {r.listing.external_id: r for r in store.listings(item.id)}
    assert set(active) == {"1", "3"}
    assert active["1"].first_seen == DAY1
    assert active["1"].last_seen == DAY2
    assert store.price_history(active["1"].id) == [(DAY1, 4000), (DAY2, 3500)]


def test_ended_listings_stay_in_history(store: Store) -> None:
    item = store.create_item(ItemFields(name="Calendar"))
    store.record_listings(item.id, Marketplace.BGG, [listing("1", 40)], DAY1)
    store.record_listings(item.id, Marketplace.BGG, [], DAY2)

    assert store.listings(item.id) == []
    [ended] = store.listings(item.id, include_ended=True)
    assert ended.ended_at == DAY2


def test_listing_seen_again_after_ending_is_active_but_not_new(store: Store) -> None:
    item = store.create_item(ItemFields(name="Calendar"))
    store.record_listings(item.id, Marketplace.BGG, [listing("1", 40)], DAY1)
    store.record_listings(item.id, Marketplace.BGG, [], DAY2)

    changes = store.record_listings(item.id, Marketplace.BGG, [listing("1", 40)], DAY2)

    assert changes.new == []
    assert [r.ended_at for r in store.listings(item.id)] == [None]


def test_other_marketplaces_listings_are_left_alone(store: Store) -> None:
    item = store.create_item(ItemFields(name="Calendar"))
    store.record_listings(item.id, Marketplace.BGG, [listing("1", 40)], DAY1)

    store.record_listings(item.id, Marketplace.EBAY, [], DAY2)

    assert len(store.listings(item.id)) == 1


def test_auction_details_read_back(store: Store) -> None:
    item = store.create_item(ItemFields(name="Calendar"))
    ends = datetime(2026, 10, 4, 1, 30, tzinfo=UTC)
    auction = replace(listing("1", 12), auction_ends_at=ends, bid_count=3)

    store.record_listings(item.id, Marketplace.BGG, [auction], DAY1)

    [recorded] = store.listings(item.id)
    assert recorded.listing == auction
    assert recorded.listing.is_auction
    assert not listing("2", 40).is_auction


def test_rejections_are_kept_with_new_listings_only(store: Store) -> None:
    item = store.create_item(ItemFields(name="Calendar"))
    store.record_listings(
        item.id, Marketplace.BGG, [listing("1", 40)], DAY1, rejections={"1": "Box only"}
    )

    store.record_listings(
        item.id,
        Marketplace.BGG,
        [listing("1", 40), listing("2", 38)],
        DAY2,
        rejections={"1": "changed", "2": "Lot of 3"},
    )

    assert {r.listing.external_id: r.rejection for r in store.listings(item.id)} == {
        "1": "Box only",
        "2": "Lot of 3",
    }
    assert store.known_listing_ids(item.id, Marketplace.BGG) == {"1", "2"}
    assert store.known_listing_ids(item.id, Marketplace.EBAY) == set()
