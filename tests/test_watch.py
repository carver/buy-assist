from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from buy_assist.claude import ParseError
from buy_assist.listings import ListingError
from buy_assist.mailer import MailerError
from buy_assist.models import Condition, ItemFields, Listing, Marketplace, Sale
from buy_assist.sold import SoldPriceError
from buy_assist.store import Store
from buy_assist.usps import Origin, ShippingEstimate
from buy_assist.watch import Watch
from tests.fixtures import BGG_URL

# 9:00 Pacific on Saturday 2026-10-17, before the 11:30 digest.
MORNING = datetime(2026, 10, 17, 16, 0, tzinfo=UTC)
SHIPPING = ShippingEstimate(low_cents=900, typical_cents=1000, high_cents=1600)


def listing(external_id: str, dollars: int) -> Listing:
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


class FakeListings:
    marketplace = Marketplace.BGG
    label = "BoardGameGeek"
    needs_judging = False

    def __init__(self) -> None:
        self.shown: list[Listing] = []
        self.calls = 0
        self.error: str | None = None

    def covers(self, item: ItemFields) -> bool:
        return item.reference_url == BGG_URL

    def listings_for(self, item: ItemFields, ship_to: str | None) -> list[Listing]:
        self.calls += 1
        if self.error:
            raise ListingError(self.error)
        return self.shown


class FakeEbayListings(FakeListings):
    marketplace = Marketplace.EBAY
    label = "eBay"
    needs_judging = True


class FakeJudge:
    def __init__(self) -> None:
        self.judged: list[list[str]] = []
        self.rejecting: dict[str, str] = {}
        self.fail = False

    def rejections(self, item: ItemFields, listings: Sequence[Listing]) -> dict[str, str]:
        if self.fail:
            raise ParseError("claude-json exited 1")
        ids = [listing.external_id for listing in listings]
        self.judged.append(ids)
        return {i: reason for i, reason in self.rejecting.items() if i in ids}


class FakeSold:
    label = "BoardGameGeek"

    def __init__(self) -> None:
        self.calls = 0

    def covers(self, item: ItemFields) -> bool:
        return item.reference_url == BGG_URL

    def sales_for(self, item: ItemFields) -> list[Sale]:
        self.calls += 1
        return []


class FakeQuoter:
    def quote(self, item: ItemFields, origin: Origin) -> ShippingEstimate | None:
        return SHIPPING


class FakeMailer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str, str | None]] = []
        self.fail = False

    def send(self, to: str, subject: str, text: str, html: str | None) -> None:
        if self.fail:
            raise MailerError("Couldn't send mail through smtp.example: ConnectionRefusedError")
        self.sent.append((to, subject, text, html))

    def to(self, address: str) -> list[tuple[str, str]]:
        return [(subject, text) for to, subject, text, _ in self.sent if to == address]


@pytest.fixture
def store(tmp_path: Path) -> Store:
    store = Store.open(tmp_path / "buy.db")
    store.set_home_zip("94114")
    admin, member = store.users()
    store.save_people(
        [replace(admin, email="admin@example.com"), replace(member, email="member@example.com")]
    )
    return store


@pytest.fixture
def source() -> FakeListings:
    return FakeListings()


@pytest.fixture
def sold() -> FakeSold:
    return FakeSold()


@pytest.fixture
def mailer() -> FakeMailer:
    return FakeMailer()


@pytest.fixture
def judge() -> FakeJudge:
    return FakeJudge()


@pytest.fixture
def watch(
    store: Store, source: FakeListings, sold: FakeSold, mailer: FakeMailer, judge: FakeJudge
) -> Watch:
    return Watch(
        store,
        listing_sources=[source],
        judge=judge,
        sold_prices=sold,
        shipping=FakeQuoter(),
        mailer=mailer,
    )


def calendar(store: Store, *, target_dollars: int | None = None, watchers: str = "Member") -> int:
    ids = {u.name: u.id for u in store.users()}
    fields = ItemFields(
        name="EXIT calendar",
        condition=Condition.NEW,
        reference_url=BGG_URL,
        target_price_cents=None if target_dollars is None else target_dollars * 100,
        watcher_ids=frozenset(ids[name] for name in watchers.split(",")),
    )
    return store.create_item(fields).id


def test_first_listing_found_is_sent_at_once_to_watchers_only(
    store: Store, source: FakeListings, watch: Watch, mailer: FakeMailer
) -> None:
    item_id = calendar(store)
    source.shown = [listing("1", 45), listing("2", 40)]

    watch.tick(MORNING)

    assert len(store.listings(item_id)) == 2
    [(subject, text)] = mailer.to("member@example.com")
    assert subject == "First listing found: EXIT calendar"
    assert "$40.00" in text
    assert "$50.00 all-in" in text  # $40 + ~$10 estimated shipping, no tax on GeekMarket
    assert "The rest will come in your daily digest." in text
    assert mailer.to("admin@example.com") == []


def test_listings_are_fetched_once_a_day(store: Store, source: FakeListings, watch: Watch) -> None:
    calendar(store)

    watch.tick(MORNING)
    watch.tick(MORNING + timedelta(hours=1))
    assert source.calls == 1

    watch.tick(MORNING + timedelta(hours=24))
    assert source.calls == 2


def test_failed_fetch_is_retried_next_tick(
    store: Store, source: FakeListings, watch: Watch
) -> None:
    calendar(store)
    source.error = "BoardGameGeek answered 503."

    watch.tick(MORNING)
    source.error = None
    watch.tick(MORNING + timedelta(hours=1))

    assert source.calls == 2


def test_listing_at_target_full_price_alerts_once(
    store: Store, source: FakeListings, watch: Watch, mailer: FakeMailer
) -> None:
    calendar(store, target_dollars=45)
    source.shown = [listing("1", 60)]
    watch.tick(MORNING)
    mailer.sent.clear()

    source.shown = [listing("1", 60), listing("2", 35)]
    watch.tick(MORNING + timedelta(days=1))
    watch.tick(MORNING + timedelta(days=1, hours=1))

    [(subject, text)] = mailer.to("member@example.com")
    assert subject == "At your target: EXIT calendar"
    assert "$45.00 all-in" in text


def test_new_low_alerts_only_once_there_are_five_data_points(
    store: Store, source: FakeListings, watch: Watch, mailer: FakeMailer
) -> None:
    calendar(store)
    source.shown = [listing(str(n), 50 + n) for n in range(4)]
    watch.tick(MORNING)
    source.shown = [*source.shown, listing("9", 49)]
    watch.tick(MORNING + timedelta(days=1))
    assert [s for s, _ in mailer.to("member@example.com")] == ["First listing found: EXIT calendar"]

    source.shown = [*source.shown, listing("10", 45)]
    watch.tick(MORNING + timedelta(days=2))

    subjects = [s for s, _ in mailer.to("member@example.com")]
    assert subjects[-1] == "New low price: EXIT calendar"


def test_digest_goes_out_after_1130_pacific_once_a_day(
    store: Store, source: FakeListings, watch: Watch, mailer: FakeMailer
) -> None:
    calendar(store)
    source.shown = [listing("1", 45), listing("2", 40)]
    watch.tick(MORNING)
    mailer.sent.clear()

    watch.tick(MORNING + timedelta(hours=2))  # 11:00 Pacific
    assert mailer.sent == []
    watch.tick(MORNING + timedelta(hours=2, minutes=45))  # 11:45 Pacific
    watch.tick(MORNING + timedelta(hours=3, minutes=45))

    [(subject, text)] = mailer.to("member@example.com")
    assert subject == "Buy Assistant digest: 1 new listing"
    assert "EXIT calendar #1" in text  # #2 already came as the first-found alert
    assert "#2 of 2 seen, median $52.50" in text


def test_digest_with_nothing_new_is_skipped(
    store: Store, source: FakeListings, watch: Watch, mailer: FakeMailer
) -> None:
    calendar(store)
    source.shown = [listing("1", 45)]
    watch.tick(MORNING + timedelta(hours=3))
    mailer.sent.clear()

    watch.tick(MORNING + timedelta(days=1, hours=3))

    assert mailer.to("member@example.com") == []


def test_missed_digest_is_sent_late_the_same_day_only(
    store: Store, source: FakeListings, watch: Watch, mailer: FakeMailer
) -> None:
    calendar(store)
    source.shown = [listing("1", 45), listing("2", 40)]
    watch.tick(MORNING)
    mailer.sent.clear()

    watch.tick(datetime(2026, 10, 18, 4, 30, tzinfo=UTC))  # 21:30 Pacific, same day

    assert [s for s, _ in mailer.to("member@example.com")] == [
        "Buy Assistant digest: 1 new listing"
    ]


def test_failed_send_is_retried_next_tick(
    store: Store, source: FakeListings, watch: Watch, mailer: FakeMailer
) -> None:
    calendar(store)
    source.shown = [listing("1", 45)]
    mailer.fail = True
    watch.tick(MORNING)

    mailer.fail = False
    watch.tick(MORNING + timedelta(hours=1))

    assert [s for s, _ in mailer.to("member@example.com")] == ["First listing found: EXIT calendar"]


def test_person_without_email_gets_nothing(
    store: Store, source: FakeListings, watch: Watch, mailer: FakeMailer
) -> None:
    store.save_people([replace(store.users()[1], email=None)])
    calendar(store)
    source.shown = [listing("1", 45)]

    watch.tick(MORNING + timedelta(hours=3))

    assert mailer.sent == []


def test_links_point_at_the_item_page_once_app_address_is_set(
    store: Store, source: FakeListings, watch: Watch, mailer: FakeMailer
) -> None:
    store.set_app_address("http://box.example.ts.net:6767/")
    item_id = calendar(store)
    source.shown = [listing("1", 45)]

    watch.tick(MORNING)

    [(_, text)] = mailer.to("member@example.com")
    assert f"http://box.example.ts.net:6767/items/{item_id}" in text


def test_sold_prices_refresh_weekly(store: Store, sold: FakeSold, watch: Watch) -> None:
    calendar(store)

    watch.tick(MORNING)
    watch.tick(MORNING + timedelta(days=3))
    assert sold.calls == 1

    watch.tick(MORNING + timedelta(days=7))
    assert sold.calls == 2


def test_closed_items_are_not_watched(
    store: Store, source: FakeListings, watch: Watch, mailer: FakeMailer
) -> None:
    store.close_item(calendar(store))
    source.shown = [listing("1", 45)]

    watch.tick(MORNING)

    assert source.calls == 0
    assert mailer.sent == []


def pans(store: Store, site: str) -> None:
    """Three items with reference links on one site, enough for it to deserve a reader."""
    admin = store.users()[0]
    for n in range(3):
        store.create_item(
            ItemFields(
                name=f"Pan {n}",
                reference_url=f"https://{site}/{n}",
                watcher_ids=frozenset({admin.id}),
            )
        )


def test_every_admin_email_ends_with_the_reader_reminder(
    store: Store, source: FakeListings, watch: Watch, mailer: FakeMailer
) -> None:
    pans(store, "shop.example")
    calendar(store, watchers="Admin,Member")
    source.shown = [listing("1", 45)]
    watch.tick(MORNING)
    source.shown = [listing("1", 45), listing("2", 50)]
    watch.tick(MORNING + timedelta(days=1, hours=3))

    to_admin = [(s, text, html) for to, s, text, html in mailer.sent if to == "admin@example.com"]
    assert [s for s, _, _ in to_admin] == [
        "First listing found: EXIT calendar",
        "Buy Assistant digest: 1 new listing",
    ]
    for _, text, html in to_admin:
        assert "shop.example (3 items)" in text
        assert html is not None and "shop.example (3 items)" in html
    assert len(mailer.to("member@example.com")) == 2
    assert not any("shop.example" in text for _, text in mailer.to("member@example.com"))


def test_reader_reminder_alone_sends_no_email(
    store: Store, watch: Watch, mailer: FakeMailer
) -> None:
    pans(store, "shop.example")

    watch.tick(MORNING + timedelta(hours=3))

    assert mailer.sent == []


def test_item_added_later_is_fetched_on_the_next_tick(
    store: Store, source: FakeListings, watch: Watch
) -> None:
    calendar(store)
    watch.tick(MORNING)

    calendar(store)
    watch.tick(MORNING + timedelta(hours=1))

    assert source.calls == 2


class NoQuote:
    def quote(self, item: ItemFields, origin: Origin) -> ShippingEstimate | None:
        return None


def test_listing_with_unknown_shipping_never_counts_as_at_target(
    store: Store, source: FakeListings, sold: FakeSold, mailer: FakeMailer
) -> None:
    watch = Watch(
        store,
        listing_sources=[source],
        judge=FakeJudge(),
        sold_prices=sold,
        shipping=NoQuote(),
        mailer=mailer,
    )
    calendar(store, target_dollars=45)
    source.shown = [listing("1", 40)]

    watch.tick(MORNING)

    assert [s for s, _ in mailer.to("member@example.com")] == ["First listing found: EXIT calendar"]


def test_first_found_is_only_for_a_new_item(
    store: Store, source: FakeListings, watch: Watch, mailer: FakeMailer
) -> None:
    item_id = calendar(store, watchers="Member")
    source.shown = [listing("1", 45)]
    watch.tick(MORNING)

    admin, member = store.users()
    item = store.get_item(item_id)
    assert item is not None
    store.update_item(item_id, replace(item.fields, watcher_ids=frozenset({admin.id, member.id})))
    watch.tick(MORNING + timedelta(days=3))

    assert [s for s, _ in mailer.to("admin@example.com") if "First" in s] == []


def test_digest_leaves_out_listings_already_alerted(
    store: Store, source: FakeListings, watch: Watch, mailer: FakeMailer
) -> None:
    calendar(store)
    source.shown = [listing("1", 45), listing("2", 40)]
    watch.tick(MORNING)
    mailer.sent.clear()

    watch.tick(MORNING + timedelta(hours=3))

    [(subject, text)] = mailer.to("member@example.com")
    assert subject == "Buy Assistant digest: 1 new listing"
    assert "EXIT calendar #1" in text
    assert "EXIT calendar #2" not in text


def test_failed_digest_for_one_person_never_doubles_anothers(
    store: Store, source: FakeListings, watch: Watch, mailer: FakeMailer
) -> None:
    calendar(store, watchers="Admin,Member")
    source.shown = [listing("1", 45), listing("2", 50)]
    watch.tick(MORNING)

    sent_before = len(mailer.sent)
    real_send = mailer.send

    def fail_for_admin(to: str, subject: str, text: str, html: str | None) -> None:
        if to == "admin@example.com":
            raise MailerError("Couldn't send mail through smtp.example: TimeoutError")
        real_send(to, subject, text, html)

    mailer.send = fail_for_admin  # type: ignore[method-assign]
    watch.tick(MORNING + timedelta(hours=3))
    mailer.send = real_send  # type: ignore[method-assign]
    item_id = store.list_items(open_items=True)[0].id
    later = MORNING + timedelta(hours=4)
    store.record_listings(item_id, Marketplace.BGG, [*source.shown, listing("3", 60)], later)
    watch.tick(later + timedelta(minutes=1))

    digests = [s for _, s, _, _ in mailer.sent[sent_before:] if "digest" in s]
    assert [to for to, s, _, _ in mailer.sent[sent_before:] if "digest" in s] == [
        "member@example.com",
        "admin@example.com",
    ]
    assert len(digests) == 2


def test_failed_sold_refresh_is_retried_next_tick(store: Store, watch: Watch) -> None:
    class FailingSold(FakeSold):
        def sales_for(self, item: ItemFields) -> list[Sale]:
            self.calls += 1
            raise SoldPriceError("BoardGameGeek answered 503.")

    sold = FailingSold()
    failing = Watch(
        store,
        listing_sources=[],
        judge=FakeJudge(),
        sold_prices=sold,
        shipping=FakeQuoter(),
        mailer=None,
    )
    calendar(store)

    failing.tick(MORNING)
    failing.tick(MORNING + timedelta(hours=1))

    assert sold.calls == 2


def ebay_listing(external_id: str, dollars: int) -> Listing:
    return replace(
        listing(external_id, dollars),
        marketplace=Marketplace.EBAY,
        url=f"https://www.ebay.com/itm/{external_id}",
    )


def test_ebay_listings_are_judged_once_when_first_seen(
    store: Store, judge: FakeJudge, mailer: FakeMailer
) -> None:
    ebay = FakeEbayListings()
    watch = Watch(
        store,
        listing_sources=[ebay],
        judge=judge,
        sold_prices=FakeSold(),
        shipping=FakeQuoter(),
        mailer=mailer,
    )
    item_id = calendar(store)
    ebay.shown = [ebay_listing("1", 40), ebay_listing("2", 5)]
    judge.rejecting = {"2": "Box only"}
    watch.tick(MORNING)
    ebay.shown = [ebay_listing("1", 40), ebay_listing("2", 5), ebay_listing("3", 42)]
    watch.tick(MORNING + timedelta(days=1))

    assert judge.judged == [["1", "2"], ["3"]]
    rejections = {r.listing.external_id: r.rejection for r in store.listings(item_id)}
    assert rejections == {"1": None, "2": "Box only", "3": None}


def test_failed_judging_leaves_the_fetch_for_next_tick(
    store: Store, judge: FakeJudge, mailer: FakeMailer
) -> None:
    ebay = FakeEbayListings()
    watch = Watch(
        store,
        listing_sources=[ebay],
        judge=judge,
        sold_prices=FakeSold(),
        shipping=FakeQuoter(),
        mailer=mailer,
    )
    item_id = calendar(store)
    ebay.shown = [ebay_listing("1", 40)]
    judge.fail = True
    watch.tick(MORNING)
    assert store.listings(item_id) == []

    judge.fail = False
    watch.tick(MORNING + timedelta(hours=1))
    assert len(store.listings(item_id)) == 1


def test_failed_judging_retries_ebay_without_refetching_geekmarket(
    store: Store, source: FakeListings, judge: FakeJudge, mailer: FakeMailer
) -> None:
    ebay = FakeEbayListings()
    watch = Watch(
        store,
        listing_sources=[source, ebay],
        judge=judge,
        sold_prices=FakeSold(),
        shipping=FakeQuoter(),
        mailer=mailer,
    )
    calendar(store)
    ebay.shown = [ebay_listing("1", 40)]
    judge.fail = True
    watch.tick(MORNING)

    judge.fail = False
    watch.tick(MORNING + timedelta(hours=1))

    assert (source.calls, ebay.calls) == (1, 2)
