"""
OpenPOS-TCG Addon
File: tests/test_phase4_inventory.py

Phase 4 Verification Test Suite:
1. Verifies migration 002 execution against SQLite.
2. Verifies Universal Item Resolver API (NFC Tag UID, SKU, and ID fallback).
3. Verifies POS Register Search and Atomic Checkout Decrement with stock guarding.
4. Verifies Hardware-Agnostic Tag & Sleeve Label Payload generation.
5. Verifies Inventory Management UI view and PATCH updates.
"""

import os
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import sqlite3
import pytest
from flask import Flask
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from models import Base, SinglesInventory
from plugin import addon_bp


def test_migration_002_ddl():
    """1. Verify migrations/002_add_identifiers.sqlite.sql applies cleanly on top of 001."""
    m1_path = PROJECT_ROOT / "migrations" / "001_initial_schema.sqlite.sql"
    m2_path = PROJECT_ROOT / "migrations" / "002_add_identifiers.sqlite.sql"

    assert m1_path.exists(), "Migration 001 missing"
    assert m2_path.exists(), "Migration 002 missing"

    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()

    # Apply Migration 001
    cursor.executescript(m1_path.read_text(encoding="utf-8"))

    # Apply Migration 002
    cursor.executescript(m2_path.read_text(encoding="utf-8"))

    # Verify columns in singles_inventory
    cursor.execute("PRAGMA table_info(singles_inventory)")
    columns = {row[1] for row in cursor.fetchall()}
    assert "sku" in columns, "Column 'sku' not added by migration 002!"
    assert "custom_tag_id" in columns, "Column 'custom_tag_id' not added by migration 002!"

    # Verify indexes
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='singles_inventory'")
    indexes = {row[0] for row in cursor.fetchall()}
    assert "ix_singles_sku" in indexes, "Index 'ix_singles_sku' missing!"
    assert "ix_singles_custom_tag_id" in indexes, "Index 'ix_singles_custom_tag_id' missing!"

    conn.close()
    print("\n  [PASS] Migration 002 DDL applied and verified on SQLite.")


@pytest.fixture
def test_app_and_db():
    """Configures test Flask client and database with Phase 4 schema."""
    app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))
    app.config["TESTING"] = True
    app.secret_key = "phase4-test-secret"

    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    app.config["DB_ENGINE"] = engine

    # Register addon blueprint
    app.register_blueprint(addon_bp)

    client = app.test_client()
    return client, engine


def test_resolver_and_register_flow(test_app_and_db):
    """
    Tests Universal Item Resolver (custom_tag_id, SKU, and ID fallback),
    Register In-Stock Search, and Atomic Stock Decrement.
    """
    client, engine = test_app_and_db
    Session = sessionmaker(bind=engine)
    session = Session()

    # Insert test card: Sheoldred, the Apocalypse
    card = SinglesInventory(
        game="mtg",
        provider_card_id="dmu-107-sheoldred",
        name="Sheoldred, the Apocalypse",
        clean_name="sheoldred the apocalypse",
        set_code="dmu",
        set_name="Dominaria United",
        collector_number="107",
        rarity="mythic",
        finish="foil",
        condition="NM",
        quantity=5,
        cost_basis=40.00,
        sell_price=74.99,
        market_price=80.00,
        sku="TCG-1042",
        custom_tag_id="04:A2:3B:49:10"
    )
    session.add(card)
    session.commit()
    card_id = card.id
    session.close()

    # 1. Resolve by Custom Tag ID (NFC UID / Scanned Barcode)
    resp_tag = client.get("/tcg/api/resolve?identifier=04:A2:3B:49:10")
    assert resp_tag.status_code == 200
    data_tag = resp_tag.get_json()
    assert data_tag["found"] is True
    assert data_tag["item"]["name"] == "Sheoldred, the Apocalypse"
    assert data_tag["item"]["custom_tag_id"] == "04:A2:3B:49:10"
    assert data_tag["item"]["sku"] == "TCG-1042"

    # 2. Resolve by SKU fallback
    resp_sku = client.get("/tcg/api/resolve?identifier=TCG-1042")
    assert resp_sku.status_code == 200
    data_sku = resp_sku.get_json()
    assert data_sku["found"] is True
    assert data_sku["item"]["id"] == card_id

    # 3. Resolve by Primary ID fallback
    resp_id = client.get(f"/tcg/api/resolve?identifier={card_id}")
    assert resp_id.status_code == 200
    data_id = resp_id.get_json()
    assert data_id["found"] is True
    assert data_id["item"]["id"] == card_id

    # 4. Resolve non-existent identifier -> 404
    resp_404 = client.get("/tcg/api/resolve?identifier=NON_EXISTENT_TOKEN")
    assert resp_404.status_code == 404
    assert resp_404.get_json()["found"] is False

    # 5. Register Fast Search
    resp_search = client.get("/tcg/api/register/search?q=Sheoldred")
    assert resp_search.status_code == 200
    data_search = resp_search.get_json()
    assert data_search["success"] is True
    assert len(data_search["results"]) == 1
    assert data_search["results"][0]["name"] == "Sheoldred, the Apocalypse"

    # 6. Atomic Checkout Decrement: Success case (decrement 2 units from 5)
    resp_dec1 = client.post("/tcg/api/register/decrement", json=[{"id": card_id, "quantity": 2}])
    assert resp_dec1.status_code == 200
    data_dec1 = resp_dec1.get_json()
    assert data_dec1["success"] is True
    assert data_dec1["decremented"][0]["remaining_quantity"] == 3

    # Verify in DB
    session = Session()
    db_card = session.query(SinglesInventory).filter_by(id=card_id).first()
    assert db_card.quantity == 3
    session.close()

    # 7. Atomic Checkout Decrement: Insufficient stock rejection (try to decrement 4 units when only 3 available)
    resp_dec2 = client.post("/tcg/api/register/decrement", json=[{"id": card_id, "quantity": 4}])
    assert resp_dec2.status_code == 400
    data_dec2 = resp_dec2.get_json()
    assert data_dec2["success"] is False
    assert "Insufficient stock" in data_dec2["error"]

    # Verify rollback: quantity remains 3
    session = Session()
    db_card = session.query(SinglesInventory).filter_by(id=card_id).first()
    assert db_card.quantity == 3, f"Expected 3 remaining stock after rollback, got {db_card.quantity}"
    session.close()


def test_label_payload_and_inventory_endpoints(test_app_and_db):
    """
    Tests GET /tcg/api/items/<id>/label-payload, Inventory List API,
    and Inventory View rendering.
    """
    client, engine = test_app_and_db
    Session = sessionmaker(bind=engine)
    session = Session()

    item = SinglesInventory(
        game="mtg",
        provider_card_id="cmm-401-solring",
        name="Sol Ring",
        clean_name="sol ring",
        set_code="cmm",
        set_name="Commander Masters",
        collector_number="401",
        rarity="uncommon",
        finish="nonfoil",
        condition="NM",
        quantity=10,
        cost_basis=1.00,
        sell_price=2.50,
        sku="TCG-2001",
        custom_tag_id="SLV-SOL-01"
    )
    session.add(item)
    session.commit()
    item_id = item.id
    session.close()

    # 1. Label Payload Engine
    resp_label = client.get(f"/tcg/api/items/{item_id}/label-payload")
    assert resp_label.status_code == 200
    label = resp_label.get_json()

    # Assert required schema fields
    assert label["id"] == item_id
    assert label["sku"] == "TCG-2001"
    assert label["custom_tag_id"] == "SLV-SOL-01"
    assert label["display_name"] == "Sol Ring"
    assert label["set_name"] == "Commander Masters"
    assert label["set_code"] == "CMM"
    assert label["collector_number"] == "401"
    assert label["rarity"] == "uncommon"
    assert label["condition"] == "NM"
    assert label["finish"] == "nonfoil"
    assert label["sell_price"] == 2.50
    assert label["qr_payload"] == f"openpos://tcg/{item_id}"
    assert label["barcode_payload"] == "SLV-SOL-01"
    assert label["nfc_ndef_payload"] == f"openpos://tcg/{item_id}"

    # 2. Inventory Management View (Zero Dead-End Navigation)
    resp_view = client.get("/tcg/inventory")
    assert resp_view.status_code == 200
    html = resp_view.get_data(as_text=True)
    assert 'href="/"' in html, "Return to Dashboard link missing from inventory view!"
    assert "TCG POS - Inventory Management" in html
    assert "inv-search-input" in html
    assert "inv-table-body" in html

    # 3. Inventory List API
    resp_list = client.get("/tcg/api/inventory?search=Sol")
    assert resp_list.status_code == 200
    list_data = resp_list.get_json()
    assert list_data["success"] is True
    assert list_data["total"] == 1
    assert list_data["items"][0]["name"] == "Sol Ring"

    # 4. PATCH Inventory Item (Update price & assign new token)
    resp_patch = client.patch(
        f"/tcg/api/inventory/{item_id}",
        json={"sell_price": 3.25, "custom_tag_id": "NEW-NFC-UID-999"}
    )
    assert resp_patch.status_code == 200
    patch_data = resp_patch.get_json()
    assert patch_data["success"] is True
    assert patch_data["item"]["sell_price"] == 3.25
    assert patch_data["item"]["custom_tag_id"] == "NEW-NFC-UID-999"

    # 5. Verify Tag Collision Prevention
    # Add a second item and try to assign it the same custom_tag_id
    session = Session()
    item2 = SinglesInventory(
        game="mtg",
        provider_card_id="lea-lotus",
        name="Black Lotus",
        clean_name="black lotus",
        set_code="lea",
        set_name="Alpha",
        collector_number="232",
        rarity="rare",
        finish="nonfoil",
        condition="NM",
        quantity=1
    )
    session.add(item2)
    session.commit()
    item2_id = item2.id
    session.close()

    resp_conflict = client.patch(
        f"/tcg/api/inventory/{item2_id}",
        json={"custom_tag_id": "NEW-NFC-UID-999"}
    )
    assert resp_conflict.status_code == 400
    assert "already assigned" in resp_conflict.get_json()["error"]


if __name__ == "__main__":
    pytest.main(["-v", str(Path(__file__))])
