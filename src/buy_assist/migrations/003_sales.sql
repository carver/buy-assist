ALTER TABLE items ADD COLUMN search_query TEXT;

CREATE TABLE sales (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES items (id),
    source TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    source_id TEXT,
    sold_on TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents > 0),
    shipping_cents INTEGER CHECK (shipping_cents >= 0),
    is_new INTEGER NOT NULL,
    condition TEXT NOT NULL,
    title TEXT NOT NULL,
    added_at TEXT NOT NULL,
    UNIQUE (item_id, source, dedupe_key)
);
