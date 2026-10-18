"""Feedback on the item page: "not this", putting a filtered listing back, and "bought"."""

import re
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from buy_assist.claude import ParseError
from buy_assist.mailer import MailerError
from buy_assist.models import ItemFields, Listing, Marketplace, Purchase
from buy_assist.store import Store
from buy_assist.web import Services, create_app
from tests.web_fakes import (
    FakeLinkReader,
    FakeListingSource,
    FakeParser,
    FakeSalesParser,
    FakeSoldPrices,
    FixedQuoter,
    item_form,
)

SEEN = datetime(2026, 10, 17, 18, tzinfo=UTC)


class JudgedSource:
    """eBay as far as feedback cares: its listings get Claude's check. It finds nothing
    itself; tests record its listings directly."""

    marketplace = Marketplace.EBAY
    label = "eBay"
    needs_judging = True

    def covers(self, item: ItemFields) -> bool:
        return False

    def listings_for(self, item: ItemFields, ship_to: str | None) -> list[Listing]:
        return []


class FakeJudge:
    def __init__(self) -> None:
        self.judged: list[tuple[str, list[str]]] = []
        self.rejecting: dict[str, str] = {}
        self.fail = False

    def rejections(self, item: ItemFields, listings: Sequence[Listing]) -> dict[str, str]:
        if self.fail:
            raise ParseError("claude-json exited 1")
        ids = [listing.external_id for listing in listings]
        self.judged.append((item.notes, ids))
        return {i: reason for i, reason in self.rejecting.items() if i in ids}


class FakeMailer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.fail = False

    def send(self, to: str, subject: str, text: str, html: str | None) -> None:
        if self.fail:
            raise MailerError("Couldn't send mail through smtp.example: ConnectionRefusedError")
        self.sent.append((to, subject, text))


@pytest.fixture
def judge() -> FakeJudge:
    return FakeJudge()


@pytest.fixture
def mailer() -> FakeMailer:
    return FakeMailer()


@pytest.fixture
def member(
    store: Store, judge: FakeJudge, mailer: FakeMailer, tmp_path: Path
) -> Iterator[TestClient]:
    admin, member = store.users()
    store.save_people(
        [replace(admin, email="admin@example.com"), replace(member, email="member@example.com")]
    )
    services = Services(
        parser=FakeParser(),
        links=FakeLinkReader(),
        sold_prices=FakeSoldPrices(),
        sales_parser=FakeSalesParser(),
        listing_sources=[FakeListingSource(), JudgedSource()],
        shipping=FixedQuoter(),
        judge=judge,
        mailer=mailer,
    )
    with TestClient(create_app(tmp_path / "buy.db", services)) as client:
        client.post("/whoami", data={"user_id": str(member.id)})
        yield client


def ebay_listing(external_id: str, dollars: int) -> Listing:
    return Listing(
        marketplace=Marketplace.EBAY,
        external_id=external_id,
        title=f"EXIT calendar #{external_id}",
        url=f"https://www.ebay.com/itm/{external_id}",
        price_cents=dollars * 100,
        is_new=True,
        condition="New",
        origin_zip3="940",
    )


def add_item(client: TestClient, store: Store, *ebay: Listing, **form: str | list[str]) -> int:
    location = client.post("/items", data=item_form(**form), follow_redirects=False)
    item_id = int(location.headers["location"].rsplit("/", 1)[1])
    store.record_listings(item_id, Marketplace.EBAY, ebay, SEEN)
    return item_id


def listing_id(store: Store, item_id: int, external_id: str) -> int:
    return next(
        r.id
        for r in store.listings(item_id, include_ended=True)
        if r.listing.marketplace is Marketplace.EBAY and r.listing.external_id == external_id
    )


def section(page: str, pattern: str) -> str:
    match = re.search(pattern, page, re.S)
    assert match is not None, pattern
    return match.group(1)


def shown(page: str) -> str:
    """The item page's current listings, without the filtered-out list under them."""
    return section(page, r"<h2>Listings</h2>(.*?)</section>").split('<details class="filtered"')[0]


def filtered(page: str) -> str:
    return section(page, r"Filtered out \(\d+\)</summary>(.*?)</details>")


def test_not_this_with_a_reason_files_it_and_has_claude_check_the_rest(
    member: TestClient, store: Store, judge: FakeJudge
) -> None:
    item_id = add_item(
        member, store, ebay_listing("1", 45), ebay_listing("2", 50), ebay_listing("3", 55)
    )
    judge.rejecting = {"2": "German edition"}

    page = member.post(
        f"/items/{item_id}/listings/{listing_id(store, item_id, '1')}/not-this",
        data={"reason": "German edition"},
    ).text

    item = store.get_item(item_id)
    assert item is not None
    assert item.fields.notes.endswith("Not this: German edition")
    assert judge.judged == [(item.fields.notes, ["2", "3"])]
    assert "Claude checked the 2 other eBay listings again and filtered out 1." in page
    assert "#1" not in shown(page) and "#2" not in shown(page)
    assert "#3" in shown(page)
    rows = filtered(page).split("<li>")
    assert any("#1" in row and "German edition" in row and "Member" in row for row in rows)
    assert any("#2" in row and "German edition" in row and "Claude" in row for row in rows)


def test_not_this_without_a_reason_leaves_notes_and_claude_alone(
    member: TestClient, store: Store, judge: FakeJudge
) -> None:
    item_id = add_item(member, store, ebay_listing("1", 45), ebay_listing("2", 50), notes="Sealed")

    page = member.post(
        f"/items/{item_id}/listings/{listing_id(store, item_id, '1')}/not-this",
        data={"reason": " "},
    ).text

    item = store.get_item(item_id)
    assert item is not None and item.fields.notes == "Sealed"
    assert judge.judged == []
    assert "#1" in filtered(page) and "Not this" in filtered(page)


def test_failed_recheck_keeps_the_not_this_and_says_so(
    member: TestClient, store: Store, judge: FakeJudge
) -> None:
    item_id = add_item(member, store, ebay_listing("1", 45), ebay_listing("2", 50))
    judge.fail = True

    page = member.post(
        f"/items/{item_id}/listings/{listing_id(store, item_id, '1')}/not-this",
        data={"reason": "Missing cards"},
    ).text

    assert "Claude couldn&#39;t check the other listings again" in page
    assert "#1" in filtered(page)
    assert "#2" in shown(page)


def test_put_back_returns_a_listing_and_claude_leaves_it_alone(
    member: TestClient, store: Store, judge: FakeJudge
) -> None:
    item_id = add_item(member, store)
    store.record_listings(
        item_id,
        Marketplace.EBAY,
        [ebay_listing("1", 45), ebay_listing("2", 50), ebay_listing("3", 55)],
        SEEN,
        rejections={"2": "Box only"},
    )
    member.post(f"/items/{item_id}/listings/{listing_id(store, item_id, '2')}/put-back")
    judge.rejecting = {"2": "Box only"}

    page = member.post(
        f"/items/{item_id}/listings/{listing_id(store, item_id, '1')}/not-this",
        data={"reason": "Damaged"},
    ).text

    assert [ids for _, ids in judge.judged] == [["3"]]
    assert "#2" in shown(page)


def test_bought_from_a_listing_closes_the_item_and_tells_the_other_watchers(
    member: TestClient, store: Store, mailer: FakeMailer
) -> None:
    store.set_tax_rate("94110", Decimal("0.08625"))
    item_id = add_item(member, store, ebay_listing("1", 45), watcher_ids=["1", "2"])
    bought_id = listing_id(store, item_id, "1")
    assert 'value="58.88"' in shown(member.get(f"/items/{item_id}").text)  # $45 + ~$10 + tax

    page = member.post(
        f"/items/{item_id}/bought", data={"listing_id": str(bought_id), "price": "52.10"}
    ).text

    item = store.get_item(item_id)
    member_id = store.users()[1].id
    assert item is not None and not item.is_open
    assert item.purchase == Purchase(price_cents=5210, buyer_id=member_id, listing_id=bought_id)
    assert "Bought by Member for $52.10 all-in" in page
    [(to, subject, text)] = mailer.sent
    assert (to, subject) == ("admin@example.com", f"Bought: {item.fields.name}")
    assert "Member bought it for $52.10 all-in" in text
    assert "https://www.ebay.com/itm/1" in text


def test_won_auction_in_price_history_can_be_marked_bought(
    member: TestClient, store: Store
) -> None:
    ends = SEEN + timedelta(hours=2)
    auction = replace(ebay_listing("1", 12), auction_ends_at=ends, bid_count=4)
    item_id = add_item(member, store, auction)
    store.record_listings(item_id, Marketplace.EBAY, [], SEEN + timedelta(days=1))
    won = listing_id(store, item_id, "1")

    page = member.get(f"/items/{item_id}").text
    history = section(page, r"<h2>Price history</h2>(.*?)</section>")
    assert f'name="listing_id" value="{won}"' in history

    member.post(f"/items/{item_id}/bought", data={"listing_id": str(won), "price": "31"})
    item = store.get_item(item_id)
    assert item is not None and item.purchase is not None
    assert (item.purchase.listing_id, item.purchase.price_cents) == (won, 3100)


def test_bought_elsewhere_records_the_typed_price(member: TestClient, store: Store) -> None:
    item_id = add_item(member, store)

    page = member.post(f"/items/{item_id}/bought", data={"price": "$40"}).text

    item = store.get_item(item_id)
    assert item is not None and item.purchase is not None
    assert (item.purchase.listing_id, item.purchase.price_cents) == (None, 4000)
    assert "Bought by Member for $40.00 all-in" in page
    assert "$40.00 paid" in member.get("/?closed=1").text


@pytest.mark.parametrize("price", ["", "lots", "0"])
def test_bad_price_is_refused(member: TestClient, store: Store, price: str) -> None:
    item_id = add_item(member, store)

    response = member.post(f"/items/{item_id}/bought", data={"price": price})

    assert response.status_code == 422
    assert "Enter what you paid, all-in, like 42.50." in response.text
    item = store.get_item(item_id)
    assert item is not None and item.is_open


def test_listing_of_another_item_is_404(member: TestClient, store: Store) -> None:
    first = add_item(member, store, ebay_listing("1", 45))
    second = add_item(member, store)
    other = listing_id(store, first, "1")

    assert (
        member.post(
            f"/items/{second}/bought", data={"listing_id": str(other), "price": "9"}
        ).status_code
        == 404
    )
    assert (
        member.post(f"/items/{second}/listings/{other}/not-this", data={"reason": ""}).status_code
        == 404
    )
    assert member.post(f"/items/{second}/listings/{other}/put-back").status_code == 404


def test_failed_bought_email_is_reported(
    member: TestClient, store: Store, mailer: FakeMailer
) -> None:
    item_id = add_item(member, store, watcher_ids=["1", "2"])
    mailer.fail = True

    page = member.post(f"/items/{item_id}/bought", data={"price": "40"}).text

    assert "Couldn&#39;t email Admin that it&#39;s bought" in page
    item = store.get_item(item_id)
    assert item is not None and not item.is_open


def test_bought_form_starts_empty_when_the_all_in_price_isnt_known(
    member: TestClient, store: Store
) -> None:
    item_id = add_item(member, store, ebay_listing("1", 45))  # no tax rate for 94110

    page = shown(member.get(f"/items/{item_id}").text)

    assert re.search(r'<input name="price"[^>]* value=""', page)


@pytest.mark.parametrize("action", ["not-this", "put-back", "bought"])
def test_closed_item_takes_no_more_feedback(member: TestClient, store: Store, action: str) -> None:
    item_id = add_item(member, store, ebay_listing("1", 45))
    store.close_item(item_id)
    one = listing_id(store, item_id, "1")
    url = (
        f"/items/{item_id}/bought"
        if action == "bought"
        else f"/items/{item_id}/listings/{one}/{action}"
    )

    response = member.post(url, data={"listing_id": str(one), "price": "40", "reason": "Damaged"})

    assert response.status_code == 422
    assert "This item is closed." in response.text
    item = store.get_item(item_id)
    assert item is not None and item.purchase is None and item.fields.notes == ""
    [recorded] = [r for r in store.listings(item_id) if r.id == one]
    assert recorded.rejection is None
