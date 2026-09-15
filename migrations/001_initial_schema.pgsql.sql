-- OpenPOS-TCG: Initial Schema Migration (PostgreSQL)
-- Target: PostgreSQL 14+

CREATE TABLE IF NOT EXISTS singles_inventory (
    id SERIAL PRIMARY KEY,
    game VARCHAR(32) NOT NULL DEFAULT 'mtg',
    provider_card_id VARCHAR(64) NOT NULL,
    name VARCHAR(255) NOT NULL,
    clean_name VARCHAR(255) NOT NULL,
    set_code VARCHAR(32) NOT NULL,
    set_name VARCHAR(255) NOT NULL,
    collector_number VARCHAR(32) NOT NULL,
    rarity VARCHAR(32) NOT NULL,
    finish VARCHAR(32) NOT NULL DEFAULT 'nonfoil',
    condition VARCHAR(8) NOT NULL DEFAULT 'NM',
    quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    cost_basis DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    sell_price DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    market_price DOUBLE PRECISION,
    low_price DOUBLE PRECISION,
    foil_price DOUBLE PRECISION,
    etched_price DOUBLE PRECISION,
    image_path VARCHAR(512),
    image_uri VARCHAR(512),
    api_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_single_game_card_finish_cond UNIQUE (game, provider_card_id, finish, condition)
);

CREATE INDEX IF NOT EXISTS ix_singles_game_card ON singles_inventory (game, provider_card_id);
CREATE INDEX IF NOT EXISTS ix_singles_lookup ON singles_inventory (game, clean_name, set_code);
CREATE INDEX IF NOT EXISTS ix_singles_set_code ON singles_inventory (set_code);
CREATE INDEX IF NOT EXISTS ix_singles_metadata_gin ON singles_inventory USING gin (api_metadata);
