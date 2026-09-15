-- OpenPOS-TCG: Add transaction settlement tables (SQLite)
-- Migration 003: TCGTransaction, TCGTransactionItem, TCGTransactionTender

CREATE TABLE IF NOT EXISTS tcg_transactions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_number   TEXT NOT NULL UNIQUE,
    receipt_number       TEXT,
    subtotal             REAL NOT NULL DEFAULT 0.0,
    tax_rate             REAL NOT NULL DEFAULT 0.0,
    tax_amount           REAL NOT NULL DEFAULT 0.0,
    tax_total            REAL NOT NULL DEFAULT 0.0,
    discount_total       REAL NOT NULL DEFAULT 0.0,
    grand_total          REAL NOT NULL DEFAULT 0.0,
    total_tendered       REAL NOT NULL DEFAULT 0.0,
    change_due           REAL NOT NULL DEFAULT 0.0,
    payment_method       TEXT NOT NULL DEFAULT 'cash',
    tender_details       TEXT NOT NULL DEFAULT '{}',
    customer_id          TEXT,
    status               TEXT NOT NULL DEFAULT 'completed',
    cashier_id           TEXT,
    notes                TEXT,
    hardware_meta        TEXT NOT NULL DEFAULT '{}',
    created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_tcg_txn_number   ON tcg_transactions (transaction_number);
CREATE INDEX IF NOT EXISTS ix_tcg_txn_status   ON tcg_transactions (status);
CREATE INDEX IF NOT EXISTS ix_tcg_txn_created  ON tcg_transactions (created_at);

CREATE TABLE IF NOT EXISTS tcg_transaction_items (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id       INTEGER NOT NULL REFERENCES tcg_transactions(id) ON DELETE CASCADE,
    singles_inventory_id INTEGER REFERENCES singles_inventory(id) ON DELETE SET NULL,
    inventory_id         INTEGER REFERENCES singles_inventory(id) ON DELETE SET NULL,
    item_name            TEXT NOT NULL,
    name                 TEXT,
    sku                  TEXT,
    game                 TEXT NOT NULL DEFAULT 'mtg',
    set_code             TEXT NOT NULL,
    collector_number     TEXT,
    condition            TEXT NOT NULL,
    finish               TEXT NOT NULL,
    quantity             INTEGER NOT NULL DEFAULT 1,
    quantity_sold        INTEGER,
    unit_cost_basis      REAL NOT NULL DEFAULT 0.0,
    cost_basis_snapshot  REAL,
    unit_sell_price      REAL NOT NULL DEFAULT 0.0,
    unit_price           REAL,
    total_sell_price     REAL NOT NULL DEFAULT 0.0,
    line_total           REAL,
    api_metadata         TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS ix_txn_items_transaction ON tcg_transaction_items (transaction_id);
CREATE INDEX IF NOT EXISTS ix_txn_items_inventory   ON tcg_transaction_items (singles_inventory_id);

CREATE TABLE IF NOT EXISTS tcg_transaction_tenders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id  INTEGER NOT NULL REFERENCES tcg_transactions(id) ON DELETE CASCADE,
    tender_type     TEXT NOT NULL,
    amount          REAL NOT NULL DEFAULT 0.0,
    change_due      REAL NOT NULL DEFAULT 0.0,
    reference       TEXT
);

CREATE INDEX IF NOT EXISTS ix_txn_tenders_transaction ON tcg_transaction_tenders (transaction_id);
