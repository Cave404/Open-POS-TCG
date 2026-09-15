-- Initial placeholder schema to verify migration loader
CREATE TABLE IF NOT EXISTS singles_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game TEXT NOT NULL DEFAULT 'mtg',
    name TEXT NOT NULL,
    clean_name TEXT NOT NULL,
    set_code TEXT NOT NULL,
    collector_number TEXT NOT NULL,
    condition TEXT NOT NULL DEFAULT 'NM',
    finish TEXT NOT NULL DEFAULT 'nonfoil',
    price_market REAL NOT NULL DEFAULT 0.0,
    price_acquired REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'IN_STOCK',
    date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
