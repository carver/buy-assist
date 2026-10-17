CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    email TEXT
);

INSERT INTO users (name) VALUES ('Jason'), ('Shira');

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE items (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL CHECK (name <> ''),
    upc TEXT,
    epid TEXT,
    model_number TEXT,
    reference_url TEXT,
    variant TEXT,
    condition TEXT NOT NULL CHECK (condition IN ('new', 'used', 'any')),
    zip TEXT,
    target_price_cents INTEGER CHECK (target_price_cents >= 0),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE TABLE item_watchers (
    item_id INTEGER NOT NULL REFERENCES items (id),
    user_id INTEGER NOT NULL REFERENCES users (id),
    PRIMARY KEY (item_id, user_id)
);
