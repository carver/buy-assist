CREATE TABLE listings (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES items (id),
    marketplace TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents > 0),
    shipping_cents INTEGER CHECK (shipping_cents >= 0),
    is_new INTEGER NOT NULL,
    condition TEXT NOT NULL,
    edition TEXT,
    origin_state TEXT,
    origin_zip3 TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    ended_at TEXT,
    UNIQUE (item_id, marketplace, external_id)
);

CREATE TABLE listing_prices (
    listing_id INTEGER NOT NULL REFERENCES listings (id),
    seen_at TEXT NOT NULL,
    price_cents INTEGER NOT NULL,
    shipping_cents INTEGER
);
