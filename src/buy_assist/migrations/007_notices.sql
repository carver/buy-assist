CREATE TABLE tax_rates (
    zip TEXT PRIMARY KEY,
    rate TEXT NOT NULL
);

-- San Francisco, from CDTFA's tax rate API on 2026-10-17; confirmed by the household.
INSERT INTO tax_rates (zip, rate) VALUES ('94114', '0.08625');

CREATE TABLE zone_charts (
    zip3 TEXT PRIMARY KEY,
    fetched_on TEXT NOT NULL,
    data TEXT NOT NULL
);

CREATE TABLE notices (
    listing_id INTEGER NOT NULL REFERENCES listings (id),
    user_id INTEGER NOT NULL REFERENCES users (id),
    kind TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (listing_id, user_id, kind)
);
