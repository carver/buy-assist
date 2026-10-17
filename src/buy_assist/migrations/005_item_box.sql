ALTER TABLE items ADD COLUMN weight_oz INTEGER CHECK (weight_oz > 0);
ALTER TABLE items ADD COLUMN length_in REAL CHECK (length_in > 0);
ALTER TABLE items ADD COLUMN width_in REAL CHECK (width_in > 0);
ALTER TABLE items ADD COLUMN depth_in REAL CHECK (depth_in > 0);
