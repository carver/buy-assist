"""SQLite persistence. Each call opens its own connection, so the store is safe to share
across the web server's worker threads."""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from buy_assist.models import Condition, Item, ItemFields, User


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
    "variant",
    "zip",
    "target_price_cents",
    "notes",
)
_ITEM_COLUMNS = ", ".join(_ITEM_COLUMN_NAMES)


def _item_values(fields: ItemFields) -> tuple[object, ...]:
    return tuple(getattr(fields, name) for name in _ITEM_COLUMN_NAMES)


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
