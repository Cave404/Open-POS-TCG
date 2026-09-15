# OpenPOS-TCG (`tcg_pos`)

Official Trading Card Game (TCG) inventory, buylist valuation, and market pricing addon for the **OpenPOS** platform (v1.0.8).

Designed for card shops and hobby retailers, `Open-POS-TCG` provides universal card intake, condition grading, buylist trade-in calculation, multi-tender POS checkout, and real-time hardware scanning via NFC, 1D barcodes, and 2D QR codes.

---

## Key Architecture & Features

- **Polymorphic Storage Engine**: A unified `singles_inventory` schema supporting both SQLite and PostgreSQL. Game-specific mechanics (Mana, HP, Type, Legalities) are encapsulated in dynamic `api_metadata` JSON columns without schema bloat.
- **Decoupled Hardware Integration**: Interfaces with `Open-POS-Hardware-Hub` to support PC/SC USB NFC readers (ACR122U/ACR1252U), 1D/2D barcode scanners, and thermal label printers (ZPL/TSPL/HTML).
- **Core Platform Interoperability**: Fully integrates with OpenPOS Core Customer Management and Store Credit Ledgers for buylist trade-in deposits and multi-tender register redemption.
- **Ghost Tag Wiper**: Strict physical tag isolation ensures customer loyalty badges never conflict with or ring up inventory items.

---

## Ethical API Usage & Third-Party Attribution

OpenPOS-TCG is built to operate as a polite, responsible ecosystem partner to community card databases. We adhere strictly to third-party developer policies to minimize external server load and ensure high availability for the community.

### Magic: The Gathering — Powered by Scryfall API
- **Data Source**: Card text, printings, imagery, and market benchmark pricing are supplied by [Scryfall](https://scryfall.com).
- **Rate Limiting & Concurrency**: All outbound requests strictly enforce a polite inter-request delay (100ms spacing) conforming to Scryfall guidelines.
- **Descriptive Headers**: All API queries transmit a compliant, transparent `User-Agent` and explicit `Accept` headers.
- **Local Art Caching**: Card art images are fetched once and saved locally to `data/cache/tcg_art/mtg/` using atomic writes. Subsequent requests serve assets entirely from local disk, preventing repetitive CDN hits.
- **Attribution**: Magic: The Gathering is copyright © Wizards of the Coast LLC. This project is not affiliated with, endorsed, or sponsored by Wizards of the Coast or Scryfall LLC.

### Pokémon TCG — Powered by TCGdex API
- **Data Source**: Card definitions, variants, and expansion data are provided by [TCGdex](https://tcgdex.net).
- **Caching Pipeline**: Card imagery and game metadata are cached on first retrieval to `data/cache/tcg_art/pokemon/`.
- **Attribution**: Pokémon and Pokémon character names are trademarks of Nintendo, Creatures Inc., and Game Freak Inc. This project is not affiliated with, sponsored, or endorsed by Nintendo or TCGdex.

---

## Directory Structure

```text
Open-POS-TCG/
├── manifest.json                  # Core Target API v1.0.0 Addon Manifest
├── plugin.py                     # Entrypoint & Blueprint registration
├── models.py                     # Polymorphic singles_inventory & transaction models
├── migrations/                   # SQLite & PostgreSQL DDL migration scripts
├── providers/                    # API Driver Abstraction
│   ├── base.py                   # BaseTCGProvider & NormalizedCard contract
│   ├── mtg_scryfall.py           # Scryfall MTG Driver
│   └── pokemon_tcgdex.py         # TCGdex Pokémon Driver
├── services/                     # Business Logic Services
│   ├── buylist.py                # Trade-in margin & condition valuation
│   ├── buylist_settlement.py     # Intake batch committing
│   ├── checkout_service.py       # Atomic sales decrements & tender handling
│   ├── core_customer_client.py   # OpenPOS Core REST API Bridge
│   └── market_refresher.py       # Modular, non-blocking pricing updater
├── routes/                       # Flask Blueprint Routes
│   ├── intake.py                 # Intake workstation & card search
│   ├── inventory.py              # Inventory grid & label payloads
│   ├── register.py               # POS checkout & universal tag resolver
│   ├── pricing.py                # Market sync triggers & alerts
│   └── internal.py               # Inter-addon webhooks (ghost tag wiper)
├── static/tcg_pos/               # Scoped assets (hardware_bridge.js, CSS)
└── templates/tcg_pos/            # Scoped views adhering to OpenPOS UI standards
```

---

## Hardware & Peripheral Capabilities

- **NFC Tag Support**: Reads 14-character uppercase hex UIDs from NXP NTAG213 chips. Supports password protection (KUGE) and factory wiping via `Open-POS-Hardware-Hub`.
- **Barcode & QR Tokens**: Any standard 1D/2D scanner functions as an input device against `/tcg/api/resolve`.
- **Optional Peripherals**: Configurable toggles for thermal receipt printers (58mm/80mm) and cash drawer kick pulses (ESC p), defaulting to non-blocking operation.