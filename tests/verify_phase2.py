"""
Verification Script for OpenPOS-TCG Phase 2
Tests:
1. SQLAlchemy model & schema generation against sqlite:///:memory:
2. Raw SQLite migration DDL execution against sqlite3
3. BuylistCalculator condition degradation, finish resolution, and mathematical assertions
"""

import os
import sys
from pathlib import Path

# Add project root to sys.path defensively
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import sqlite3
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from models import Base, SinglesInventory
from providers.base import NormalizedCard
from services.buylist import BuylistCalculator, BuylistOffer, BuylistBatchResult


def test_sqlite_migration_ddl():
    """Verify raw migrations/001_initial_schema.sqlite.sql syntax on SQLite."""
    print("--- 1. Testing Raw SQLite Migration DDL ---")
    migration_file = Path("migrations/001_initial_schema.sqlite.sql")
    assert migration_file.exists(), f"Migration file missing: {migration_file}"

    sql_content = migration_file.read_text(encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    cursor.executescript(sql_content)

    # Check table existence
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='singles_inventory'")
    row = cursor.fetchone()
    assert row is not None, "Table singles_inventory not found in sqlite_master"
    print("  [PASS] Raw SQLite DDL executed successfully and table created.")

    # Check indexes
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='singles_inventory'")
    indexes = {r[0] for r in cursor.fetchall()}
    assert "ix_singles_game_card" in indexes, "Index ix_singles_game_card missing"
    assert "ix_singles_lookup" in indexes, "Index ix_singles_lookup missing"
    assert "ix_singles_set_code" in indexes, "Index ix_singles_set_code missing"
    print(f"  [PASS] All expected SQLite indexes found: {indexes}")
    conn.close()


def test_sqlalchemy_model_in_memory():
    """Verify SQLAlchemy model mapping, schema creation, insert, and to_dict()."""
    print("\n--- 2. Testing SQLAlchemy Model with in-memory SQLite ---")
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    tables = inspector.get_table_names()
    assert "singles_inventory" in tables, f"singles_inventory missing from tables: {tables}"
    print("  [PASS] Base.metadata.create_all generated singles_inventory table.")

    # Validate columns
    columns = {col["name"]: col for col in inspector.get_columns("singles_inventory")}
    expected_cols = [
        "id", "game", "provider_card_id", "name", "clean_name", "set_code",
        "set_name", "collector_number", "rarity", "finish", "condition",
        "quantity", "cost_basis", "sell_price", "market_price", "low_price",
        "foil_price", "etched_price", "image_path", "image_uri", "api_metadata",
        "created_at", "updated_at"
    ]
    for col in expected_cols:
        assert col in columns, f"Expected column '{col}' missing from table!"
    print(f"  [PASS] All {len(expected_cols)} columns correctly mapped.")

    # Insert test record
    Session = sessionmaker(bind=engine)
    session = Session()

    item = SinglesInventory(
        game="mtg",
        provider_card_id="scry-12345",
        name="Black Lotus",
        clean_name="black lotus",
        set_code="lea",
        set_name="Limited Edition Alpha",
        collector_number="232",
        rarity="rare",
        finish="nonfoil",
        condition="NM",
        quantity=1,
        cost_basis=5000.00,
        sell_price=10000.00,
        market_price=12000.00,
        low_price=9000.00,
        foil_price=None,
        etched_price=None,
        image_path="data/cache/tcg_art/mtg/scry-12345.jpg",
        image_uri="https://cards.scryfall.io/large/front/black-lotus.jpg",
        api_metadata={"mana_cost": "{0}", "type_line": "Artifact", "colors": []}
    )
    session.add(item)
    session.commit()

    saved = session.query(SinglesInventory).filter_by(provider_card_id="scry-12345").first()
    assert saved is not None
    assert saved.name == "Black Lotus"
    assert saved.api_metadata["type_line"] == "Artifact"
    print("  [PASS] Insert and query verified.")

    # Verify to_dict()
    data = saved.to_dict()
    assert isinstance(data, dict)
    assert data["name"] == "Black Lotus"
    assert data["cost_basis"] == 5000.00
    assert data["api_metadata"]["mana_cost"] == "{0}"
    print("  [PASS] to_dict() serialization verified.")

    session.close()


def test_buylist_calculator():
    """Verify BuylistCalculator margins, condition curves, and finish resolution."""
    print("\n--- 3. Testing BuylistCalculator Engine ---")
    calc = BuylistCalculator(default_cash_percentage=0.50, credit_bonus_percentage=0.30)

    # Test baseline $10.00 card across conditions
    test_cases = [
        ("NM", 1.00, 10.00, 5.00, 6.50),
        ("LP", 0.85, 8.50, 4.25, 5.53),
        ("MP", 0.70, 7.00, 3.50, 4.55),
        ("HP", 0.50, 5.00, 2.50, 3.25),
        ("DMG", 0.25, 2.50, 1.25, 1.63)
    ]

    for cond, expected_mult, expected_adj, expected_cash, expected_credit in test_cases:
        offer = calc.calculate_offer_from_price(market_price=10.00, condition=cond)
        assert offer.condition_multiplier == expected_mult, f"{cond} multiplier mismatch"
        assert offer.adjusted_market_value == expected_adj, f"{cond} adjusted value mismatch"
        assert offer.unit_cash_offer == expected_cash, f"{cond} cash offer mismatch: got {offer.unit_cash_offer}, expected {expected_cash}"
        assert offer.unit_credit_offer == expected_credit, f"{cond} credit offer mismatch: got {offer.unit_credit_offer}, expected {expected_credit}"
        print(f"  [PASS] Condition {cond}: Market $10 -> Adj ${expected_adj:.2f} -> Cash ${offer.unit_cash_offer:.2f} | Credit ${offer.unit_credit_offer:.2f}")

    # Test Quantity Totals
    multi_offer = calc.calculate_offer_from_price(market_price=10.00, condition="NM", quantity=4)
    assert multi_offer.quantity == 4
    assert multi_offer.total_cash_offer == 20.00
    assert multi_offer.total_credit_offer == 26.00
    print("  [PASS] Quantity lot totals: 4x NM $10 -> Total Cash $20.00 | Total Credit $26.00")

    # Test NormalizedCard finish pricing resolution
    card = NormalizedCard(
        provider_card_id="test-card-1",
        game="mtg",
        name="Sol Ring",
        clean_name="sol ring",
        set_code="cmm",
        set_name="Commander Masters",
        collector_number="401",
        rarity="uncommon",
        market_price=2.00,
        foil_price=15.00,
        etched_price=30.00
    )

    nonfoil_offer = calc.calculate_offer_from_card(card, finish="nonfoil")
    assert nonfoil_offer.applicable_market_price == 2.00
    assert nonfoil_offer.unit_cash_offer == 1.00
    assert nonfoil_offer.unit_credit_offer == 1.30

    foil_offer = calc.calculate_offer_from_card(card, finish="foil")
    assert foil_offer.applicable_market_price == 15.00
    assert foil_offer.unit_cash_offer == 7.50
    assert foil_offer.unit_credit_offer == 9.75

    etched_offer = calc.calculate_offer_from_card(card, finish="etched")
    assert etched_offer.applicable_market_price == 30.00
    assert etched_offer.unit_cash_offer == 15.00
    assert etched_offer.unit_credit_offer == 19.50

    print("  [PASS] Finish resolution: Nonfoil ($2->Cash $1.00) | Foil ($15->Cash $7.50) | Etched ($30->Cash $15.00)")

    # Test Batch calculation
    batch = calc.calculate_batch([
        {"card": card, "condition": "NM", "finish": "nonfoil", "quantity": 2},
        {"card": card, "condition": "LP", "finish": "foil", "quantity": 1},
        {"market_price": 50.00, "condition": "NM", "quantity": 1, "card_name": "Mox Diamond"}
    ])
    assert batch.total_quantity == 4
    # Line 1: 2x nonfoil NM Sol Ring ($2.00 * 1.0 * 0.50 = $1.00/ea => $2.00 cash, credit $1.30/ea => $2.60)
    # Line 2: 1x foil LP Sol Ring ($15.00 * 0.85 = $12.75 * 0.50 = $6.38 cash, credit $6.38 * 1.30 = $8.29)
    # Line 3: 1x Mox Diamond ($50.00 * 1.0 * 0.50 = $25.00 cash, credit $25.00 * 1.30 = $32.50)
    # Total cash: 2.00 + 6.38 + 25.00 = 33.38
    # Total credit: 2.60 + 8.29 + 32.50 = 43.39
    assert batch.total_cash == 33.38, f"Batch cash mismatch: {batch.total_cash}"
    assert batch.total_credit == 43.39, f"Batch credit mismatch: {batch.total_credit}"
    print(f"  [PASS] Batch calculation: 4 items -> Total Cash ${batch.total_cash:.2f} | Total Credit ${batch.total_credit:.2f}")

    # Test direct credit percentage configuration
    direct_credit_calc = BuylistCalculator(default_cash_percentage=0.50, credit_percentage=0.65)
    direct_offer = direct_credit_calc.calculate_offer_from_price(100.00, condition="NM")
    assert direct_offer.unit_cash_offer == 50.00
    assert direct_offer.unit_credit_offer == 65.00
    print("  [PASS] Direct credit percentage mode: $100 -> Cash $50.00 (50%) | Credit $65.00 (65%)")


if __name__ == "__main__":
    test_sqlite_migration_ddl()
    test_sqlalchemy_model_in_memory()
    test_buylist_calculator()
    print("\n*** ALL PHASE 2 VERIFICATION TESTS PASSED SUCCESSFULLY! ***")
