-- OpenPOS-TCG: Initial Schema Migration (SQLite)
-- Target: SQLite 3.38+ with native JSON features

CREATE TABLE IF NOT EXISTS singles_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game TEXT NOT NULL DEFAULT 'mtg',
    provider_card_id TEXT NOT NULL,
    name TEXT NOT NULL,
    clean_name TEXT NOT NULL,
    set_code TEXT NOT NULL,
    set_name TEXT NOT NULL,
    collector_number TEXT NOT NULL,
    rarity TEXT NOT NULL,
    finish TEXT NOT NULL DEFAULT 'nonfoil',
    condition TEXT NOT NULL DEFAULT 'NM',
    quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    cost_basis REAL NOT NULL DEFAULT 0.0,
    sell_price REAL NOT NULL DEFAULT 0.0,
    market_price REAL,
    low_price REAL,
    foil_price REAL,
    etched_price REAL,
    image_path TEXT,
    image_uri TEXT,
    api_metadata TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_single_game_card_finish_cond UNIQUE (game, provider_card_id, finish, condition)
);

CREATE INDEX IF NOT EXISTS ix_singles_game_card ON singles_inventory (game, provider_card_id);
CREATE INDEX IF NOT EXISTS ix_singles_lookup ON singles_inventory (game, clean_name, set_code);
CREATE INDEX IF NOT EXISTS ix_singles_set_code ON singles_inventory (set_code);
