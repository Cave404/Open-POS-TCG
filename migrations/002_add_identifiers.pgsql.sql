-- OpenPOS-TCG: Add hardware & POS identifiers migration (PostgreSQL)
-- Adds SKU and custom_tag_id (NFC UID / Barcode / QR payload) columns

ALTER TABLE singles_inventory ADD COLUMN IF NOT EXISTS sku VARCHAR(64);
ALTER TABLE singles_inventory ADD COLUMN IF NOT EXISTS custom_tag_id VARCHAR(128);

CREATE UNIQUE INDEX IF NOT EXISTS ix_singles_sku ON singles_inventory (sku);
CREATE INDEX IF NOT EXISTS ix_singles_custom_tag_id ON singles_inventory (custom_tag_id);
