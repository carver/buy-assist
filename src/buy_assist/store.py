"""SQLite persistence. Each call opens its own connection, so the store is safe to share
across the web server's worker threads."""

import json
import sqlite3
from collections import Counter
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib import resources
from pathlib import Path
from typing import Any

from buy_assist.models import (
    Condition,
    Item,
    ItemFields,
    Listing,
    ListingChanges,
    Marketplace,
    NoticeKind,
    RecordedListing,
    RecordedSale,
    Sale,
    User,
    url_domain,
)


def _migrations() -> list[tuple[int, str]]:
    files = resources.files("buy_assist.migrations").iterdir()
    scripts = [(int(f.name.split("_")[0]), f.read_text()) for f in files if f.name.endswith(".sql")]
    return sorted(scripts)


class ItemNotFoundError(LookupError):
    pass


class Store:
    def __init__(self, path: Path) -> None:
        self._path = path

    @classmethod
    def open(cls, path: Path) -> Store:
        """Open the database at `path`, creating it or migrating it to the current schema."""
        store = cls(path)
        store._migrate()
        return store

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _migrate(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            version: int = conn.execute("PRAGMA user_version").fetchone()[0]
            for number, script in _migrations():
                if number > version:
                    conn.executescript(
                        f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;"
                    )

    def users(self) -> list[User]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id, name, email FROM users ORDER BY id").fetchall()
        return [User(id=r["id"], name=r["name"], email=r["email"]) for r in rows]

    def create_item(self, fields: ItemFields) -> Item:
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                f"INSERT INTO items ({_ITEM_COLUMNS}, created_at)"
                f" VALUES ({', '.join('?' * len(_ITEM_COLUMN_NAMES))}, ?)",
                (*_item_values(fields), now),
            )
            item_id = cursor.lastrowid
            assert item_id is not None
            _set_watchers(conn, item_id, fields.watcher_ids)
            return _read_item(conn, item_id)

    def get_item(self, item_id: int) -> Item | None:
        try:
            with self._connect() as conn:
                return _read_item(conn, item_id)
        except ItemNotFoundError:
            return None

    def update_item(self, item_id: int, fields: ItemFields) -> Item:
        assignments = ", ".join(f"{name} = ?" for name in _ITEM_COLUMN_NAMES)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE items SET {assignments} WHERE id = ?", (*_item_values(fields), item_id)
            )
            _set_watchers(conn, item_id, fields.watcher_ids)
            return _read_item(conn, item_id)

    def close_item(self, item_id: int) -> Item:
        """Archive an item. Closed items keep their history but leave the open list."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE items SET closed_at = ? WHERE id = ? AND closed_at IS NULL",
                (datetime.now(UTC).isoformat(), item_id),
            )
            return _read_item(conn, item_id)

    def list_items(self, *, open_items: bool) -> list[Item]:
        status = "closed_at IS NULL" if open_items else "closed_at IS NOT NULL"
        with self._connect() as conn:
            ids = conn.execute(f"SELECT id FROM items WHERE {status} ORDER BY id").fetchall()
            return [_read_item(conn, row["id"]) for row in ids]

    def add_sales(self, item_id: int, sales: Iterable[Sale]) -> int:
        """Record sales for an item, skipping ones already recorded. Returns how many were new."""
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            before = conn.total_changes
            conn.executemany(
                "INSERT OR IGNORE INTO sales (item_id, source, dedupe_key, source_id, sold_on,"
                " price_cents, shipping_cents, is_new, condition, title, added_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        item_id,
                        s.source.value,
                        _dedupe_key(s),
                        s.source_id,
                        s.sold_on.isoformat(),
                        s.price_cents,
                        s.shipping_cents,
                        s.is_new,
                        s.condition,
                        s.title,
                        now,
                    )
                    for s in sales
                ],
            )
            return conn.total_changes - before

    def sales(self, item_id: int) -> list[RecordedSale]:
        """An item's recorded sales, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sales WHERE item_id = ? AND removed_at IS NULL"
                " ORDER BY sold_on DESC, id DESC",
                (item_id,),
            ).fetchall()
        return [_recorded_sale(row) for row in rows]

    def remove_sale(self, item_id: int, sale_id: int) -> bool:
        """Hide a sale. It stays recorded, so seeing it again doesn't bring it back."""
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE sales SET removed_at = ?"
                " WHERE id = ? AND item_id = ? AND removed_at IS NULL",
                (datetime.now(UTC).isoformat(), sale_id, item_id),
            )
            return cursor.rowcount > 0

    def record_listings(
        self, item_id: int, marketplace: Marketplace, listings: Iterable[Listing], now: datetime
    ) -> ListingChanges:
        """Record what a marketplace shows for an item now. Its listings missing from this look
        have ended; a listing seen again after ending is active again but not new."""
        seen_at = now.isoformat()
        new: list[int] = []
        repriced: list[int] = []
        with self._connect() as conn:
            known = {
                row["external_id"]: row
                for row in conn.execute(
                    "SELECT id, external_id, price_cents, shipping_cents FROM listings"
                    " WHERE item_id = ? AND marketplace = ?",
                    (item_id, marketplace.value),
                )
            }
            seen: set[str] = set()
            for listing in listings:
                seen.add(listing.external_id)
                row = known.get(listing.external_id)
                values = _listing_values(listing)
                if row is None:
                    cursor = conn.execute(
                        f"INSERT INTO listings (item_id, {_LISTING_COLUMNS}, first_seen, last_seen)"
                        f" VALUES (?, {', '.join('?' * len(values))}, ?, ?)",
                        (item_id, *values, seen_at, seen_at),
                    )
                    listing_id = cursor.lastrowid
                    assert listing_id is not None
                    new.append(listing_id)
                else:
                    listing_id = row["id"]
                    assignments = ", ".join(f"{c} = ?" for c in _LISTING_COLUMN_NAMES)
                    conn.execute(
                        f"UPDATE listings SET {assignments}, last_seen = ?, ended_at = NULL"
                        " WHERE id = ?",
                        (*values, seen_at, listing_id),
                    )
                    price = (row["price_cents"], row["shipping_cents"])
                    if price == (listing.price_cents, listing.shipping_cents):
                        continue
                    repriced.append(listing_id)
                conn.execute(
                    "INSERT INTO listing_prices (listing_id, seen_at, price_cents, shipping_cents)"
                    " VALUES (?, ?, ?, ?)",
                    (listing_id, seen_at, listing.price_cents, listing.shipping_cents),
                )
            ended = [
                row["id"]
                for external_id, row in known.items()
                if external_id not in seen
                and conn.execute(
                    "UPDATE listings SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                    (seen_at, row["id"]),
                ).rowcount
            ]
            return ListingChanges(
                new=[_read_listing(conn, i) for i in new],
                repriced=[_read_listing(conn, i) for i in repriced],
                ended=[_read_listing(conn, i) for i in ended],
            )

    def listings(self, item_id: int, *, include_ended: bool = False) -> list[RecordedListing]:
        """An item's listings, cheapest first."""
        ended = "" if include_ended else " AND ended_at IS NULL"
        with self._connect() as conn:
            ids = conn.execute(
                f"SELECT id FROM listings WHERE item_id = ?{ended} ORDER BY price_cents, id",
                (item_id,),
            ).fetchall()
            return [_read_listing(conn, row["id"]) for row in ids]

    def price_history(self, listing_id: int) -> list[tuple[datetime, int]]:
        """When a listing's price was seen and what it was, each change once."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT seen_at, price_cents FROM listing_prices WHERE listing_id = ?"
                " ORDER BY seen_at",
                (listing_id,),
            ).fetchall()
        return [(datetime.fromisoformat(r["seen_at"]), r["price_cents"]) for r in rows]

    def reference_domain_counts(self) -> dict[str, int]:
        """How many items, open or closed, link to each reference domain."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT reference_url FROM items WHERE reference_url IS NOT NULL"
            ).fetchall()
        domains = (url_domain(row["reference_url"]) for row in rows)
        return dict(Counter(d for d in domains if d))

    def set_email(self, user_id: int, email: str | None) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE users SET email = ? WHERE id = ?", (email, user_id))

    def tax_rate(self, zip_code: str) -> Decimal | None:
        """Sales tax for delivery to a ZIP, as a fraction (0.08625 for 8.625%)."""
        return self.tax_rates().get(zip_code)

    def tax_rates(self) -> dict[str, Decimal]:
        with self._connect() as conn:
            rows = conn.execute("SELECT zip, rate FROM tax_rates ORDER BY zip").fetchall()
        return {row["zip"]: Decimal(row["rate"]) for row in rows}

    def set_tax_rate(self, zip_code: str, rate: Decimal | None) -> None:
        with self._connect() as conn:
            if rate is None:
                conn.execute("DELETE FROM tax_rates WHERE zip = ?", (zip_code,))
            else:
                conn.execute(
                    "INSERT INTO tax_rates (zip, rate) VALUES (?, ?)"
                    " ON CONFLICT (zip) DO UPDATE SET rate = excluded.rate",
                    (zip_code, str(rate)),
                )

    def app_address(self) -> str | None:
        """Where people reach the app, for links in email."""
        return self._setting("app_address")

    def set_app_address(self, address: str | None) -> None:
        self._set_setting("app_address", address)

    def last_run(self, job: str) -> datetime | None:
        value = self._setting(f"last_run:{job}")
        return datetime.fromisoformat(value) if value else None

    def set_last_run(self, job: str, when: datetime) -> None:
        self._set_setting(f"last_run:{job}", when.isoformat())

    def zone_chart(self, zip3: str) -> tuple[date, Any] | None:
        """The cached USPS zone chart for a 3-digit ZIP, and when it was fetched."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT fetched_on, data FROM zone_charts WHERE zip3 = ?", (zip3,)
            ).fetchone()
        if row is None:
            return None
        return date.fromisoformat(row["fetched_on"]), json.loads(row["data"])

    def save_zone_chart(self, zip3: str, fetched_on: date, data: object) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO zone_charts (zip3, fetched_on, data) VALUES (?, ?, ?)"
                " ON CONFLICT (zip3) DO UPDATE SET"
                " fetched_on = excluded.fetched_on, data = excluded.data",
                (zip3, fetched_on.isoformat(), json.dumps(data)),
            )

    def log_notice(self, listing_id: int, user_id: int, kind: NoticeKind, when: datetime) -> bool:
        """Record that a person heard about a listing. False if they already had, this way."""
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO notices (listing_id, user_id, kind, sent_at)"
                " VALUES (?, ?, ?, ?)",
                (listing_id, user_id, kind.value, when.isoformat()),
            )
            return cursor.rowcount > 0

    def noticed(self, listing_id: int, user_id: int) -> set[NoticeKind]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT kind FROM notices WHERE listing_id = ? AND user_id = ?",
                (listing_id, user_id),
            ).fetchall()
        return {NoticeKind(row["kind"]) for row in rows}

    def reminded_domains(self) -> set[str]:
        """Reference-link domains someone has already been told deserve a reader."""
        return set(json.loads(self._setting("reminded_domains") or "[]"))

    def set_reminded_domains(self, domains: set[str]) -> None:
        self._set_setting("reminded_domains", json.dumps(sorted(domains)))

    def _setting(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def _set_setting(self, key: str, value: str | None) -> None:
        with self._connect() as conn:
            if value is None:
                conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            else:
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)"
                    " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )

    def home_zip(self) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'home_zip'").fetchone()
        return row["value"] if row else None

    def set_home_zip(self, zip_code: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('home_zip', ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (zip_code,),
            )


_ITEM_COLUMN_NAMES = (
    "name",
    "condition",
    "upc",
    "epid",
    "model_number",
    "reference_url",
    "image_url",
    "search_query",
    "variant",
    "zip",
    "weight_oz",
    "length_in",
    "width_in",
    "depth_in",
    "target_price_cents",
    "notes",
)
_ITEM_COLUMNS = ", ".join(_ITEM_COLUMN_NAMES)


def _item_values(fields: ItemFields) -> tuple[object, ...]:
    return tuple(getattr(fields, name) for name in _ITEM_COLUMN_NAMES)


_LISTING_COLUMN_NAMES = (
    "marketplace",
    "external_id",
    "title",
    "url",
    "price_cents",
    "shipping_cents",
    "is_new",
    "condition",
    "edition",
    "origin_state",
    "origin_zip3",
)
_LISTING_COLUMNS = ", ".join(_LISTING_COLUMN_NAMES)


def _listing_values(listing: Listing) -> tuple[object, ...]:
    return tuple(
        listing.marketplace.value if name == "marketplace" else getattr(listing, name)
        for name in _LISTING_COLUMN_NAMES
    )


def _read_listing(conn: sqlite3.Connection, listing_id: int) -> RecordedListing:
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    ended_at = row["ended_at"]
    return RecordedListing(
        id=row["id"],
        item_id=row["item_id"],
        listing=Listing(
            marketplace=Marketplace(row["marketplace"]),
            external_id=row["external_id"],
            title=row["title"],
            url=row["url"],
            price_cents=row["price_cents"],
            shipping_cents=row["shipping_cents"],
            is_new=bool(row["is_new"]),
            condition=row["condition"],
            edition=row["edition"],
            origin_state=row["origin_state"],
            origin_zip3=row["origin_zip3"],
        ),
        first_seen=datetime.fromisoformat(row["first_seen"]),
        last_seen=datetime.fromisoformat(row["last_seen"]),
        ended_at=datetime.fromisoformat(ended_at) if ended_at else None,
    )


def _dedupe_key(sale: Sale) -> str:
    """What makes a sale the same one seen again: the source's own id when it has one."""
    if sale.source_id:
        return sale.source_id
    title = " ".join(sale.title.split()).casefold()
    return f"{sale.sold_on.isoformat()}|{sale.price_cents}|{title}"


def _recorded_sale(row: sqlite3.Row) -> RecordedSale:
    return RecordedSale(
        id=row["id"],
        item_id=row["item_id"],
        sale=Sale(
            source=Marketplace(row["source"]),
            sold_on=date.fromisoformat(row["sold_on"]),
            price_cents=row["price_cents"],
            is_new=bool(row["is_new"]),
            condition=row["condition"],
            title=row["title"],
            shipping_cents=row["shipping_cents"],
            source_id=row["source_id"],
        ),
    )


def _set_watchers(conn: sqlite3.Connection, item_id: int, watcher_ids: frozenset[int]) -> None:
    conn.execute("DELETE FROM item_watchers WHERE item_id = ?", (item_id,))
    conn.executemany(
        "INSERT INTO item_watchers (item_id, user_id) VALUES (?, ?)",
        [(item_id, user_id) for user_id in watcher_ids],
    )


def _read_item(conn: sqlite3.Connection, item_id: int) -> Item:
    row = conn.execute(
        f"SELECT id, {_ITEM_COLUMNS}, created_at, closed_at FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    if row is None:
        raise ItemNotFoundError(item_id)
    watchers = conn.execute("SELECT user_id FROM item_watchers WHERE item_id = ?", (item_id,))
    fields = ItemFields(
        **{name: row[name] for name in _ITEM_COLUMN_NAMES if name != "condition"},
        condition=Condition(row["condition"]),
        watcher_ids=frozenset(w["user_id"] for w in watchers),
    )
    closed_at = row["closed_at"]
    return Item(
        id=row["id"],
        fields=fields,
        created_at=datetime.fromisoformat(row["created_at"]),
        closed_at=datetime.fromisoformat(closed_at) if closed_at else None,
    )
