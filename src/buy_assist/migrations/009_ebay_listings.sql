-- Auctions: the current bid is the price, so these say how far along the auction is.
ALTER TABLE listings ADD COLUMN auction_ends_at TEXT;
ALTER TABLE listings ADD COLUMN bid_count INTEGER;
-- Why Claude judged a listing not to be the item; NULL for a match or an unjudged source.
ALTER TABLE listings ADD COLUMN rejection TEXT;
