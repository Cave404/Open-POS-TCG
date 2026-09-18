"""
OpenPOS-TCG Addon
File: tests/test_phase3_endpoints.py

Phase 3 Endpoint Verification Test Suite.
Verifies:
1. Blueprint mounting at /tcg.
2. GET /tcg/intake renders workstation with 'Return to Dashboard' link.
3. POST /tcg/api/buylist/calculate evaluates card conditions and finish multipliers.
4. POST /tcg/api/intake/commit upsert transaction logic and rolling weighted-average cost basis.
"""

import os
import sys
from pathlib import Path

# Ensure project root is available on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, SinglesInventory
from plugin import addon_bp


@pytest.fixture
def test_app_and_db():
    """Configures an isolated test Flask application and in-memory SQLite database."""
    app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))
    app.config["TESTING"] = True
    app.secret_key = "test-tcg-secret"

    # In-memory SQLite engine
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    app.config["DB_ENGINE"] = engine

    # Mount addon blueprint
    app.register_blueprint(addon_bp)

    client = app.test_client()
    return client, engine


def test_blueprint_mounting(test_app_and_db):
    """1. Verify that addon_bp is correctly mounted at /tcg."""
    client, _ = test_app_and_db
    app = client.application

    rules = [rule.rule for rule in app.url_map.iter_rules()]
    print("\nRegistered routes:", rules)

    assert "/tcg/intake" in rules, "Route /tcg/intake not registered!"
    assert "/tcg/api/search" in rules, "Route /tcg/api/search not registered!"
    assert "/tcg/api/lookup/<set_code>/<collector_number>" in rules, "Lookup route not registered!"
    assert "/tcg/api/buylist/calculate" in rules, "Route /tcg/api/buylist/calculate not registered!"
    assert "/tcg/api/intake/commit" in rules, "Route /tcg/api/intake/commit not registered!"
    assert "/tcg/status" in rules, "Status route not registered!"


def test_get_intake_view(test_app_and_db):
    """2. Verify GET /tcg/intake returns HTTP 200 and includes 'Return to Dashboard' anchor."""
    client, _ = test_app_and_db
    resp = client.get("/tcg/intake")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.get_data(as_text=True)

    # Assert navigation to core dashboard (No dead-end navigation constraint)
    assert 'href="/"' in html, "Anchor linking to '/' not found in intake HTML!"
    assert "Return to Dashboard" in html, "'Return to Dashboard' text missing from intake view!"
    assert "TCG POS - Universal Intake & Buylist" in html, "Page title missing from view!"
    assert "card-search-input" in html, "Search input element missing!"
    assert "Staged Intake Queue" in html, "Queue table component missing!"


def test_buylist_calculate_endpoint(test_app_and_db):
    """3. Verify POST /tcg/api/buylist/calculate evaluates card pricing for an arbitrary condition."""
    client, _ = test_app_and_db

    # Under v1.0.1 donor defaults (60% cash, 80% store credit):
    # Test: $20.00 market price, LP condition (0.85 multiplier)
    # Adjusted value: $17.00
    # Expected Cash: $17.00 * 0.60 = $10.20
    # Expected Credit: $17.00 * 0.80 = $13.60
    payload = {
        "market_price": 20.00,
        "condition": "LP",
        "finish": "nonfoil",
        "quantity": 1
    }

    resp = client.post("/tcg/api/buylist/calculate", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    data = resp.get_json()
    assert data["success"] is True
    assert data["cash_offer"] == 10.20, f"Expected 10.20 cash, got {data['cash_offer']}"
    assert data["credit_offer"] == 13.60, f"Expected 13.60 credit, got {data['credit_offer']}"
    assert data["offer"]["condition_multiplier"] == 0.85


def test_intake_commit_upsert_and_cost_basis(test_app_and_db):
    """
    4. Verify POST /tcg/api/intake/commit inserts a card row, then a second commit
    of the exact same card/finish/condition updates quantity and cost basis with the
    weighted average formula without constraint violations.
    """
    client, engine = test_app_and_db
    Session = sessionmaker(bind=engine)

    card_payload_batch_1 = [
        {
            "game": "mtg",
            "provider_card_id": "lotus-alpha-001",
            "name": "Black Lotus",
            "clean_name": "black lotus",
            "set_code": "lea",
            "set_name": "Limited Edition Alpha",
            "collector_number": "232",
            "rarity": "rare",
            "finish": "nonfoil",
            "condition": "NM",
            "quantity": 2,
            "cost_basis": 100.00,  # 2 units @ $100 = $200 total outlay
            "sell_price": 250.00,
            "market_price": 300.00,
            "low_price": 200.00,
            "foil_price": None,
            "etched_price": None,
            "image_path": "data/cache/tcg_art/mtg/lotus-alpha-001.jpg",
            "image_uri": "https://cards.scryfall.io/large/front/black-lotus.jpg",
            "api_metadata": {"colors": [], "type_line": "Artifact"}
        }
    ]

    # First commit: Insert new record
    resp1 = client.post("/tcg/api/intake/commit", json=card_payload_batch_1)
    assert resp1.status_code == 200, f"First commit failed: {resp1.get_data(as_text=True)}"
    data1 = resp1.get_json()
    assert data1["success"] is True
    assert data1["updated_count"] == 1

    # Verify first commit in database
    session = Session()
    records = session.query(SinglesInventory).all()
    assert len(records) == 1
    row = records[0]
    assert row.name == "Black Lotus"
    assert row.quantity == 2
    assert row.cost_basis == 100.00
    assert row.sell_price == 250.00
    session.close()

    # Second commit: Same card, finish, and condition with different quantity & cost basis
    # Incoming: 3 units @ $200.00 = $600 outlay
    # Existing: 2 units @ $100.00 = $200 outlay
    # Combined: 5 units with New Cost Basis = (200 + 600) / 5 = $160.00
    card_payload_batch_2 = [
        {
            "game": "mtg",
            "provider_card_id": "lotus-alpha-001",
            "name": "Black Lotus",
            "clean_name": "black lotus",
            "set_code": "lea",
            "set_name": "Limited Edition Alpha",
            "collector_number": "232",
            "rarity": "rare",
            "finish": "nonfoil",
            "condition": "NM",
            "quantity": 3,
            "cost_basis": 200.00,
            "sell_price": 350.00,
            "market_price": 320.00
        }
    ]

    resp2 = client.post("/tcg/api/intake/commit", json=card_payload_batch_2)
    assert resp2.status_code == 200, f"Second commit failed: {resp2.get_data(as_text=True)}"
    data2 = resp2.get_json()
    assert data2["success"] is True
    assert data2["updated_count"] == 1

    # Verify atomic update with weighted average cost basis
    session = Session()
    records = session.query(SinglesInventory).all()
    assert len(records) == 1, f"Expected exactly 1 row after upsert, got {len(records)}"

    updated_row = records[0]
    assert updated_row.quantity == 5, f"Expected 5 total quantity, got {updated_row.quantity}"
    assert updated_row.cost_basis == 160.00, f"Expected 160.00 weighted cost basis, got {updated_row.cost_basis}"
    assert updated_row.sell_price == 350.00, f"Expected updated sell price 350.00, got {updated_row.sell_price}"
    assert updated_row.market_price == 320.00
    session.close()

    # Third commit: Same card ID, but DIFFERENT condition ('LP')
    # Should create a separate record due to composite unique constraint (game, card_id, finish, condition)
    card_payload_batch_3 = [
        {
            "game": "mtg",
            "provider_card_id": "lotus-alpha-001",
            "name": "Black Lotus",
            "clean_name": "black lotus",
            "set_code": "lea",
            "set_name": "Limited Edition Alpha",
            "collector_number": "232",
            "rarity": "rare",
            "finish": "nonfoil",
            "condition": "LP",
            "quantity": 1,
            "cost_basis": 80.00,
            "sell_price": 200.00
        }
    ]

    resp3 = client.post("/tcg/api/intake/commit", json=card_payload_batch_3)
    assert resp3.status_code == 200
    session = Session()
    total_rows = session.query(SinglesInventory).all()
    assert len(total_rows) == 2, f"Expected 2 distinct SKUs for different conditions, got {len(total_rows)}"
    session.close()


if __name__ == "__main__":
    # Allow running directly via python
    pytest.main(["-v", str(Path(__file__))])
