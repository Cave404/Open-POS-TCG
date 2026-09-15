-- OpenPOS-TCG: Add transaction settlement tables (PostgreSQL)
-- Migration 003: TCGTransaction, TCGTransactionItem, TCGTransactionTender

CREATE TABLE IF NOT EXISTS tcg_transactions (
    id                   SERIAL PRIMARY KEY,
    transaction_number   VARCHAR(64) NOT NULL UNIQUE,
    receipt_number       VARCHAR(64),
    subtotal             DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    tax_rate             DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    tax_amount           DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    tax_total            DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    discount_total       DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    grand_total          DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    total_tendered       DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    change_due           DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    payment_method       VARCHAR(32) NOT NULL DEFAULT 'cash',
    tender_details       JSONB NOT NULL DEFAULT '{}',
    customer_id          VARCHAR(64),
    status               VARCHAR(32) NOT NULL DEFAULT 'completed',
    cashier_id           VARCHAR(64),
    notes                TEXT,
    hardware_meta        JSONB NOT NULL DEFAULT '{}',
    created_at           TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_tcg_txn_number   ON tcg_transactions (transaction_number);
CREATE INDEX IF NOT EXISTS ix_tcg_txn_status   ON tcg_transactions (status);
CREATE INDEX IF NOT EXISTS ix_tcg_txn_created  ON tcg_transactions (created_at);

CREATE TABLE IF NOT EXISTS tcg_transaction_items (
    id                   SERIAL PRIMARY KEY,
    transaction_id       INTEGER NOT NULL REFERENCES tcg_transactions(id) ON DELETE CASCADE,
    singles_inventory_id INTEGER REFERENCES singles_inventory(id) ON DELETE SET NULL,
    inventory_id         INTEGER REFERENCES singles_inventory(id) ON DELETE SET NULL,
    item_name            VARCHAR(255) NOT NULL,
    name                 VARCHAR(255),
    sku                  VARCHAR(64),
    game                 VARCHAR(32) NOT NULL DEFAULT 'mtg',
    set_code             VARCHAR(32) NOT NULL,
    collector_number     VARCHAR(32),
    condition            VARCHAR(8) NOT NULL,
    finish               VARCHAR(32) NOT NULL,
    quantity             INTEGER NOT NULL DEFAULT 1,
    quantity_sold        INTEGER,
    unit_cost_basis      DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    cost_basis_snapshot  DOUBLE PRECISION,
    unit_sell_price      DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    unit_price           DOUBLE PRECISION,
    total_sell_price     DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    line_total           DOUBLE PRECISION,
    api_metadata         JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS ix_txn_items_transaction ON tcg_transaction_items (transaction_id);
CREATE INDEX IF NOT EXISTS ix_txn_items_inventory   ON tcg_transaction_items (singles_inventory_id);

CREATE TABLE IF NOT EXISTS tcg_transaction_tenders (
    id              SERIAL PRIMARY KEY,
    transaction_id  INTEGER NOT NULL REFERENCES tcg_transactions(id) ON DELETE CASCADE,
    tender_type     VARCHAR(32) NOT NULL,
    amount          DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    change_due      DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    reference       VARCHAR(128)
);

CREATE INDEX IF NOT EXISTS ix_txn_tenders_transaction ON tcg_transaction_tenders (transaction_id);
