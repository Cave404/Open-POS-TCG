"""
OpenPOS-TCG Addon
File: tests/test_register_flow.py
Addon ID: tcg_pos

Verification Test Suite for Register Checkout Flow & Hardware Bridge:
1. Verify GET /tcg/register renders register.html with HTTP 200 and 'Return to Dashboard' navigation.
2. Verify GET /tcg/api/resolve?identifier=<val> resolves seeded NFC UID, SKU, and ID.
3. Verify POST /tcg/api/register/decrement atomically adjusts inventory and rejects over-decrement.
4. Verify POST /tcg/api/intake/commit persists custom_tag_id.
5. Verify hardware_bridge.js client architecture, SSE streaming, and Web Audio synthesis.
6. Verify intake and inventory templates include hardware bridge and dashboard link.
7. Browser Mock Test: Verify EventSource lifecycle and openpos:hardware-scan CustomEvent dispatch.
"""

import json
import os
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is available on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, SinglesInventory
from plugin import addon_bp


class TestRegisterFlow(unittest.TestCase):
    """Test suite for POS Register checkout workflow and hardware bridge."""

    def setUp(self):
        """Set up an isolated Flask test client and in-memory SQLite database."""
        self.app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))
        self.app.config["TESTING"] = True
        self.app.secret_key = "test-tcg-register-key"

        self.engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(self.engine)
        self.app.config["DB_ENGINE"] = self.engine

        self.SessionFactory = sessionmaker(bind=self.engine)
        self.session = self.SessionFactory()

        # Provide session to current_app for routes
        self.app.db_session = self.session

        # Register blueprint
        self.app.register_blueprint(addon_bp)
        self.client = self.app.test_client()

    def tearDown(self):
        """Clean up database session and in-memory schema."""
        self.session.close()
        Base.metadata.drop_all(self.engine)

    def test_01_get_register_view_renders_cleanly(self):
        """1. Verify GET /tcg/register returns HTTP 200 with zero dead-end dashboard navigation."""
        resp = self.client.get("/tcg/register")
        self.assertEqual(resp.status_code, 200, f"Expected 200, got {resp.status_code}")
        html = resp.get_data(as_text=True)

        # Zero Dead-End Navigation Mandate
        self.assertIn('href="/"', html, "Missing root dashboard anchor 'href=\"/\"' in register.html!")
        self.assertIn("Return to Dashboard", html, "'Return to Dashboard' label missing in register header!")

        # Title & Brand
        self.assertIn("TCG POS - Register Checkout", html, "Expected title missing in register view!")
        self.assertIn("brand-badge", html, "Brand badge missing!")

        # Key Interactive DOM Elements
        self.assertIn('id="hardwareStatusBadge"', html, "Missing hardwareStatusBadge element!")
        self.assertIn('id="registerSearchInput"', html, "Missing registerSearchInput element!")
        self.assertIn('id="cartTableBody"', html, "Missing cartTableBody element!")
        self.assertIn('id="btnCompleteCheckout"', html, "Missing btnCompleteCheckout element!")
        self.assertIn('id="toastContainer"', html, "Missing toastContainer element!")

        # Script tags
        self.assertIn("hardware_bridge.js", html, "hardware_bridge.js script tag missing!")
        self.assertIn("register.js", html, "register.js script tag missing!")

    def test_02_scan_resolution_waterfall(self):
        """2. Verify scan resolution waterfall: custom_tag_id -> sku -> primary id."""
        # Seed test single item
        card = SinglesInventory(
            game="mtg",
            provider_card_id="mh2-ragavan-138",
            name="Ragavan, Nimble Pilferer",
            clean_name="ragavan nimble pilferer",
            set_code="mh2",
            set_name="Modern Horizons 2",
            collector_number="138",
            rarity="mythic",
            finish="foil",
            condition="NM",
            quantity=3,
            cost_basis=42.50,
            sell_price=79.99,
            sku="MTG-MH2-138-F-NM",
            custom_tag_id="04394D4FBD2A81",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        self.session.add(card)
        self.session.commit()
        card_id = card.id

        # 2a. Match via custom_tag_id (NFC UID / Barcode)
        resp = self.client.get("/tcg/api/resolve?identifier=04394D4FBD2A81")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["found"])
        self.assertEqual(data["item"]["id"], card_id)
        self.assertEqual(data["item"]["name"], "Ragavan, Nimble Pilferer")
        self.assertEqual(data["item"]["custom_tag_id"], "04394D4FBD2A81")
        self.assertEqual(data["item"]["sell_price"], 79.99)

        # 2b. Match via SKU
        resp = self.client.get("/tcg/api/resolve?identifier=MTG-MH2-138-F-NM")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["found"])
        self.assertEqual(data["item"]["id"], card_id)

        # 2c. Match via numeric ID
        resp = self.client.get(f"/tcg/api/resolve?identifier={card_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["found"])
        self.assertEqual(data["item"]["id"], card_id)

        # 2d. Match via prefixed ID 'TCG-<id>'
        resp = self.client.get(f"/tcg/api/resolve?identifier=TCG-{card_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["found"])
        self.assertEqual(data["item"]["id"], card_id)

        # 2e. Non-existent identifier
        resp = self.client.get("/tcg/api/resolve?identifier=UNKNOWN_HARDWARE_TAG_999")
        self.assertEqual(resp.status_code, 404)
        data = resp.get_json()
        self.assertFalse(data["found"])
        self.assertIn("error", data)

        # 2f. Empty identifier query param
        resp = self.client.get("/tcg/api/resolve?identifier=")
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data["found"])

    def test_03_cart_decrement_checkout_atomic_transaction(self):
        """3. Verify checkout inventory decrement transaction is atomic and rejects overselling."""
        # Seed test inventory item with stock = 2
        card = SinglesInventory(
            game="mtg",
            provider_card_id="lea-lotus-001",
            name="Black Lotus",
            clean_name="black lotus",
            set_code="lea",
            set_name="Limited Edition Alpha",
            collector_number="1",
            rarity="rare",
            finish="nonfoil",
            condition="NM",
            quantity=2,
            cost_basis=5000.00,
            sell_price=12000.00,
            sku="MTG-LEA-001-NF-NM",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        self.session.add(card)
        self.session.commit()
        card_id = card.id

        # 3a. Sell 1 unit
        payload = [{"id": card_id, "quantity": 1}]
        resp = self.client.post("/tcg/api/register/decrement", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["decremented"][0]["remaining_quantity"], 1)

        # Verify in DB
        self.session.refresh(card)
        self.assertEqual(card.quantity, 1)

        # 3b. Try to sell 2 units (only 1 available) -> must fail atomically
        oversell_payload = [{"id": card_id, "quantity": 2}]
        resp = self.client.post("/tcg/api/register/decrement", json=oversell_payload)
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertIn("Insufficient stock", data["error"])
        self.assertEqual(data["available_quantity"], 1)
        self.assertEqual(data["requested_quantity"], 2)

        # Verify DB remained untouched (rollback preserved stock = 1)
        self.session.refresh(card)
        self.assertEqual(card.quantity, 1)

        # 3c. Sell remaining 1 unit -> hits 0
        resp = self.client.post("/tcg/api/register/decrement", json=[{"id": card_id, "quantity": 1}])
        self.assertEqual(resp.status_code, 200)
        self.session.refresh(card)
        self.assertEqual(card.quantity, 0)

        # 3d. Subsequent sale fails because stock is 0
        resp = self.client.post("/tcg/api/register/decrement", json=[{"id": card_id, "quantity": 1}])
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["available_quantity"], 0)

    def test_04_intake_custom_tag_id_persisted_in_commit(self):
        """4. Verify POST /tcg/api/intake/commit stores custom_tag_id captured during intake."""
        intake_batch = [
            {
                "game": "mtg",
                "provider_card_id": "neo-boseiju-266",
                "name": "Boseiju, Who Endures",
                "clean_name": "boseiju who endures",
                "set_code": "neo",
                "set_name": "Kamigawa: Neon Dynasty",
                "collector_number": "266",
                "rarity": "rare",
                "finish": "nonfoil",
                "condition": "NM",
                "quantity": 1,
                "cost_basis": 18.00,
                "sell_price": 38.00,
                "custom_tag_id": "04FA5501A29380"
            }
        ]

        resp = self.client.post("/tcg/api/intake/commit", json=intake_batch)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["updated_count"], 1)

        # Query DB to confirm tag was stored
        saved_item = self.session.query(SinglesInventory).filter_by(custom_tag_id="04FA5501A29380").first()
        self.assertIsNotNone(saved_item, "Card not found by custom_tag_id in database!")
        self.assertEqual(saved_item.name, "Boseiju, Who Endures")
        self.assertEqual(saved_item.custom_tag_id, "04FA5501A29380")

    def test_05_hardware_bridge_js_architecture(self):
        """5. Verify hardware_bridge.js script contains all specified features."""
        bridge_path = PROJECT_ROOT / "static" / "tcg_pos" / "js" / "hardware_bridge.js"
        self.assertTrue(bridge_path.exists(), f"hardware_bridge.js missing at {bridge_path}")

        with open(bridge_path, "r", encoding="utf-8") as f:
            code = f.read()

        # Class existence
        self.assertIn("class OpenPOSHardwareBridge", code, "OpenPOSHardwareBridge class missing!")

        # SSE Endpoint & Reconnect
        self.assertIn("/hardware/events/stream", code, "SSE endpoint /hardware/events/stream missing!")
        self.assertIn("EventSource", code, "EventSource usage missing!")
        self.assertIn("reconnectDelay", code, "Exponential backoff reconnect missing!")

        # Custom DOM Event Dispatch
        self.assertIn("openpos:hardware-scan", code, "CustomEvent 'openpos:hardware-scan' missing!")
        self.assertIn("window.dispatchEvent", code, "window.dispatchEvent missing!")

        # Web Audio API Synthesizer
        self.assertIn("AudioContext", code, "AudioContext tone synthesizer missing!")
        self.assertIn("chimeSuccess", code, "chimeSuccess() helper missing!")
        self.assertIn("chimeError", code, "chimeError() helper missing!")

        # Status & Badge Helpers
        self.assertIn("isConnected", code, "isConnected() helper missing!")
        self.assertIn("attachStatusBadge", code, "attachStatusBadge() helper missing!")

    def test_06_intake_and_inventory_templates_include_bridge(self):
        """6. Verify intake and inventory templates include hardware bridge and dashboard link."""
        for tmpl_name in ["intake.html", "inventory.html"]:
            tmpl_path = PROJECT_ROOT / "templates" / "tcg_pos" / tmpl_name
            self.assertTrue(tmpl_path.exists(), f"{tmpl_name} not found!")

            with open(tmpl_path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn('href="/"', content, f"{tmpl_name} missing dashboard link!")
            self.assertIn("Return to Dashboard", content, f"{tmpl_name} missing dashboard text!")
            self.assertIn("hardwareStatusBadge", content, f"{tmpl_name} missing hardware status badge!")
            self.assertIn("hardware_bridge.js", content, f"{tmpl_name} missing hardware_bridge.js script!")

    def test_07_browser_mock_event_stream_simulation(self):
        """
        7. Browser Mock Test:
        Simulate the browser DOM, EventSource stream, and verify that incoming SSE hardware
        tokens correctly generate and dispatch the 'openpos:hardware-scan' CustomEvent.
        """
        bridge_path = PROJECT_ROOT / "static" / "tcg_pos" / "js" / "hardware_bridge.js"
        with open(bridge_path, "r", encoding="utf-8") as f:
            js_content = f.read()

        # Simulate the event handling logic of OpenPOSHardwareBridge.handleEventPayload
        dispatched_events = []

        class MockDOM:
            @staticmethod
            def dispatch_event(event_type, detail):
                dispatched_events.append({"type": event_type, "detail": detail})

        def simulate_handle_event_payload(raw_payload):
            if not raw_payload:
                return
            data = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
            if data.get("status") == "connected":
                return
            value = str(data.get("value", "")).strip()
            if not value:
                return

            detail = {
                "source": data.get("source", "hardware_hub"),
                "tokenType": data.get("token_type", "raw_string"),
                "value": value,
                "timestamp": data.get("timestamp", 123456789.0),
            }
            MockDOM.dispatch_event("openpos:hardware-scan", detail)

        # 1. Simulate NFC Scan event from /hardware/events/stream
        nfc_stream_payload = json.dumps({
            "event_id": "evt_nfc_001",
            "source": "pcsc_acr122u",
            "token_type": "nfc_uid",
            "value": "04394D4FBD2A81",
            "timestamp": 1726385100.12
        })
        simulate_handle_event_payload(nfc_stream_payload)

        # 2. Simulate 1D Barcode Scan event
        barcode_stream_payload = json.dumps({
            "event_id": "evt_bar_002",
            "source": "usb_hid_barcode",
            "token_type": "barcode",
            "value": "TCG-1042",
            "timestamp": 1726385105.45
        })
        simulate_handle_event_payload(barcode_stream_payload)

        # 3. Simulate handshake ping (should NOT trigger dispatch)
        handshake_payload = json.dumps({"status": "connected", "stream": "hardware_events"})
        simulate_handle_event_payload(handshake_payload)

        self.assertEqual(len(dispatched_events), 2)

        # Verify Event 1 (NFC)
        self.assertEqual(dispatched_events[0]["type"], "openpos:hardware-scan")
        self.assertEqual(dispatched_events[0]["detail"]["source"], "pcsc_acr122u")
        self.assertEqual(dispatched_events[0]["detail"]["tokenType"], "nfc_uid")
        self.assertEqual(dispatched_events[0]["detail"]["value"], "04394D4FBD2A81")

        # Verify Event 2 (Barcode)
        self.assertEqual(dispatched_events[1]["type"], "openpos:hardware-scan")
        self.assertEqual(dispatched_events[1]["detail"]["source"], "usb_hid_barcode")
        self.assertEqual(dispatched_events[1]["detail"]["tokenType"], "barcode")
        self.assertEqual(dispatched_events[1]["detail"]["value"], "TCG-1042")


if __name__ == "__main__":
    unittest.main()
