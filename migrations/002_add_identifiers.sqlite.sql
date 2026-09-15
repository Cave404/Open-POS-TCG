-- OpenPOS-TCG: Add hardware & POS identifiers migration (SQLite)
-- Adds SKU and custom_tag_id (NFC UID / Barcode / QR payload) columns

ALTER TABLE singles_inventory ADD COLUMN sku TEXT;
ALTER TABLE singles_inventory ADD COLUMN custom_tag_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS ix_singles_sku ON singles_inventory (sku);
CREATE INDEX IF NOT EXISTS ix_singles_custom_tag_id ON singles_inventory (custom_tag_id);
