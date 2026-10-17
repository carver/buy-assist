from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from buy_assist.store import Store
from buy_assist.web import Services, create_app
from tests.web_fakes import (
    FakeLinkReader,
    FakeListingSource,
    FakeParser,
    FakeSalesParser,
    FakeSoldPrices,
    FixedQuoter,
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
def links() -> FakeLinkReader:
    return FakeLinkReader()


@pytest.fixture
def sold() -> FakeSoldPrices:
    return FakeSoldPrices()


@pytest.fixture
def sales_parser() -> FakeSalesParser:
    return FakeSalesParser()


@pytest.fixture
def listing_source() -> FakeListingSource:
    return FakeListingSource()


@pytest.fixture
def client(
    store: Store,
    parser: FakeParser,
    links: FakeLinkReader,
    sold: FakeSoldPrices,
    sales_parser: FakeSalesParser,
    listing_source: FakeListingSource,
    tmp_path: Path,
) -> Iterator[TestClient]:
    services = Services(
        parser=parser,
        links=links,
        sold_prices=sold,
        sales_parser=sales_parser,
        listing_sources=[listing_source],
        shipping=FixedQuoter(),
        mailer=None,
    )
    with TestClient(create_app(tmp_path / "buy.db", services)) as client:
        yield client


@pytest.fixture
def shira(client: TestClient, store: Store) -> TestClient:
    shira = store.users()[1]
    client.post("/whoami", data={"user_id": str(shira.id)})
    return client
