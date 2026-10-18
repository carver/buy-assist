-- Who said a listing isn't the item: a person's id, or NULL for Claude (see rejection).
ALTER TABLE listings ADD COLUMN rejected_by INTEGER REFERENCES users (id);
-- 1 once a person put a filtered listing back; Claude's re-checks then leave it alone.
ALTER TABLE listings ADD COLUMN kept INTEGER NOT NULL DEFAULT 0;
-- What a bought item cost all-in, who bought it, and from which listing (NULL: elsewhere).
-- An item is bought when bought_price_cents is set; closed_at is when.
ALTER TABLE items ADD COLUMN bought_price_cents INTEGER;
ALTER TABLE items ADD COLUMN bought_by INTEGER REFERENCES users (id);
ALTER TABLE items ADD COLUMN bought_listing_id INTEGER REFERENCES listings (id);
