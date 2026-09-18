"""
OpenPOS-TCG Addon
File: tests/test_v101_upgrades.py
Addon ID: tcg_pos

Comprehensive test suite verifying OpenPOS-TCG v1.0.1 upgrades:
  1. Addon manifest declarations: version 1.0.1, config_route, default_route, donor settings defaults.
  2. Operational TCG Workstation landing dashboard replacing static placeholders.
  3. Settings & Configuration panel (/tcg/settings) matching donor parameters.
  4. Persistent settings storage (data/configs/tcg_pos.json) and dynamic BuylistCalculator integration.
  5. Dedicated Card Database Explorer (/tcg/database and /tcg/api/database/query).
  6. Database inline editing and batch operations.
  7. Media & image maintenance thumbnail healing endpoint.
  8. Plugin version metadata reporting.
"""

import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, SinglesInventory
from plugin import addon_bp
from services.settings_service import (
    SettingsService,
    get_all_settings,
    set_custom_config_path,
    reset_to_defaults,
)
from services.buylist import BuylistCalculator


class TestV101Upgrades(unittest.TestCase):
    """Test suite validating all v1.0.1 architectural and operational requirements."""

    def setUp(self):
        """Initializes test Flask client, in-memory DB, and isolated settings config path."""
        self.app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))
        self.app.config["TESTING"] = True
        self.app.secret_key = "v101-test-secret-key"

        self.engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(self.engine)
        self.app.config["DB_ENGINE"] = self.engine

        self.SessionFactory = sessionmaker(bind=self.engine)
        self.session = self.SessionFactory()
        self.app.db_session = self.session

        # Mount blueprint
        self.app.register_blueprint(addon_bp)
        self.client = self.app.test_client()

        # Isolate settings file into test scratch directory
        self.test_config_path = PROJECT_ROOT / "data" / "configs" / "test_tcg_pos.json"
        self.test_config_path.parent.mkdir(parents=True, exist_ok=True)
        if self.test_config_path.exists():
            self.test_config_path.unlink()
        set_custom_config_path(self.test_config_path)
        reset_to_defaults()

    def tearDown(self):
        """Cleans up database session, schema, and isolated config file."""
        self.session.close()
        Base.metadata.drop_all(self.engine)
        set_custom_config_path(None)
        if self.test_config_path.exists():
            try:
                self.test_config_path.unlink()
            except OSError:
                pass

    def test_01_manifest_version_and_config_route(self):
        """1. Verify manifest.json reports version 1.0.1 and declares config_route."""
        manifest_path = PROJECT_ROOT / "manifest.json"
        self.assertTrue(manifest_path.exists(), "manifest.json does not exist!")

        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        self.assertEqual(manifest.get("version"), "1.0.1", "Manifest version must be 1.0.1")
        self.assertEqual(manifest.get("config_route"), "/tcg/settings", "Missing config_route: /tcg/settings")
        self.assertEqual(manifest.get("default_route"), "/tcg", "Missing default_route: /tcg")

        settings = manifest.get("settings", {})
        self.assertEqual(settings.get("cash_payout_rate"), 60.0, "Default cash payout must be 60.0%")
        self.assertEqual(settings.get("store_credit_payout_rate"), 80.0, "Default store credit must be 80.0%")
        self.assertEqual(settings.get("daily_trade_limit"), 10, "Default daily trade limit must be 10")

        cm = settings.get("condition_multipliers", {})
        self.assertEqual(cm.get("NM"), 1.0)
        self.assertEqual(cm.get("LP"), 0.85)
        self.assertEqual(cm.get("MP"), 0.70)
        self.assertEqual(cm.get("HP"), 0.50)
        self.assertEqual(cm.get("DMG"), 0.30)

    def test_02_workstation_landing_dashboard_replaces_placeholder(self):
        """2. Verify GET /tcg returns HTTP 200 with operational workstation dashboard and zero dead ends."""
        resp = self.client.get("/tcg")
        self.assertEqual(resp.status_code, 200)

        html = resp.get_data(as_text=True)
        self.assertNotIn("UNDER ACTIVE CONSTRUCTION", html, "Landing view must not contain placeholder text!")
        self.assertIn("TCG Workstation Command Center", html)
        self.assertIn("/tcg/register", html)
        self.assertIn("/tcg/intake", html)
        self.assertIn("/tcg/database", html)
        self.assertIn("/tcg/pricing", html)
        self.assertIn("/tcg/settings", html)
        self.assertIn('href="/"', html, "Missing zero dead-end return link to core dashboard!")

        # Trailing slash route also works
        resp_slash = self.client.get("/tcg/")
        self.assertEqual(resp_slash.status_code, 200)

    def test_03_settings_view_matches_donor_inputs(self):
        """3. Verify GET /tcg/settings returns HTTP 200 containing trade-in and condition multiplier inputs."""
        resp = self.client.get("/tcg/settings")
        self.assertEqual(resp.status_code, 200)

        html = resp.get_data(as_text=True)
        # Verify navigation
        self.assertIn('href="/"', html, "Missing Return to Dashboard navigation!")
        self.assertIn('href="/tcg"', html, "Missing Back to TCG Workstation navigation!")

        # Verify trade-in inputs
        self.assertIn('id="cash_payout_rate"', html)
        self.assertIn('id="store_credit_payout_rate"', html)
        self.assertIn('id="daily_trade_limit"', html)

        # Verify condition multiplier inputs
        self.assertIn('id="cond_NM"', html)
        self.assertIn('id="cond_LP"', html)
        self.assertIn('id="cond_MP"', html)
        self.assertIn('id="cond_HP"', html)
        self.assertIn('id="cond_DMG"', html)

        # Verify automation & maintenance tools
        self.assertIn('id="market_refresh_days"', html)
        self.assertIn('id="btnFixThumbnails"', html)
        self.assertIn('id="btnSaveSettings"', html)

    def test_04_settings_persistence_and_buylist_calculator_sync(self):
        """4. Verify POST /tcg/api/settings persists to disk and BuylistCalculator dynamically reflects changes."""
        # Initial buylist calculations reflect donor default 60% cash and 80% store credit
        calc_initial = BuylistCalculator()
        self.assertEqual(calc_initial.default_cash_percentage, 0.60)
        self.assertEqual(calc_initial.credit_percentage, 0.80)
        self.assertEqual(calc_initial.condition_multipliers["DMG"], 0.30)

        initial_offer = calc_initial.calculate_offer_from_price(market_price=100.0, condition="NM")
        self.assertEqual(initial_offer.unit_cash_offer, 60.00)
        self.assertEqual(initial_offer.unit_credit_offer, 80.00)

        # Update settings via API
        new_settings = {
            "cash_payout_rate": 65.0,
            "store_credit_payout_rate": 85.0,
            "daily_trade_limit": 15,
            "condition_multipliers": {
                "NM": 1.0,
                "LP": 0.88,
                "MP": 0.72,
                "HP": 0.55,
                "DMG": 0.35,
            }
        }
        resp = self.client.post("/tcg/api/settings", json=new_settings)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))

        # Verify persistent disk file was updated
        self.assertTrue(self.test_config_path.exists())
        with open(self.test_config_path, "r", encoding="utf-8") as f:
            disk_settings = json.load(f)
        self.assertEqual(disk_settings.get("cash_payout_rate"), 65.0)
        self.assertEqual(disk_settings.get("store_credit_payout_rate"), 85.0)
        self.assertEqual(disk_settings.get("condition_multipliers", {}).get("DMG"), 0.35)

        # Verify BuylistCalculator dynamically reflects modified percentages
        calc_updated = BuylistCalculator()
        self.assertEqual(calc_updated.default_cash_percentage, 0.65)
        self.assertEqual(calc_updated.credit_percentage, 0.85)
        self.assertEqual(calc_updated.condition_multipliers["DMG"], 0.35)

        # Test calculation with modified settings
        updated_offer = calc_updated.calculate_offer_from_price(market_price=100.0, condition="NM")
        self.assertEqual(updated_offer.unit_cash_offer, 65.00)
        self.assertEqual(updated_offer.unit_credit_offer, 85.00)

        # Test DMG calculation: $100 * 0.35 multiplier = $35 adjusted * 0.65 = $22.75
        dmg_offer = calc_updated.calculate_offer_from_price(market_price=100.0, condition="DMG")
        self.assertEqual(dmg_offer.unit_cash_offer, 22.75)
        self.assertEqual(dmg_offer.unit_credit_offer, 29.75)

    def test_05_database_explorer_search_and_filtration(self):
        """5. Verify GET /tcg/database and GET /tcg/api/database/query filter singles_inventory."""
        # Check UI view
        resp_view = self.client.get("/tcg/database")
        self.assertEqual(resp_view.status_code, 200)
        self.assertIn("Card Database Explorer", resp_view.get_data(as_text=True))

        # Seed test items
        card_mtg = SinglesInventory(
            game="mtg",
            provider_card_id="lotus-001",
            name="Black Lotus",
            clean_name="black lotus",
            set_code="lea",
            set_name="Limited Edition Alpha",
            collector_number="1",
            rarity="rare",
            finish="nonfoil",
            condition="NM",
            quantity=1,
            cost_basis=5000.0,
            sell_price=12000.0,
            market_price=12500.0,
            sku="TCG-LOTUS-LEA",
            custom_tag_id="04394D4FBD2A81",
        )
        card_low_stock = SinglesInventory(
            game="mtg",
            provider_card_id="bolt-002",
            name="Lightning Bolt",
            clean_name="lightning bolt",
            set_code="m10",
            set_name="Magic 2010",
            collector_number="146",
            rarity="common",
            finish="nonfoil",
            condition="LP",
            quantity=2,  # Low stock
            cost_basis=1.0,
            sell_price=3.50,
            market_price=4.00,
            sku="TCG-BOLT-M10",
        )
        card_pokemon = SinglesInventory(
            game="pokemon",
            provider_card_id="swsh3-136",
            name="Charizard VMAX",
            clean_name="charizard vmax",
            set_code="swsh3",
            set_name="Darkness Ablaze",
            collector_number="136",
            rarity="ultra rare",
            finish="foil",
            condition="NM",
            quantity=0,  # Out of stock
            cost_basis=30.0,
            sell_price=75.0,
            market_price=80.0,
            sku="TCG-CHAR-SWSH3",
        )

        self.session.add_all([card_mtg, card_low_stock, card_pokemon])
        self.session.commit()

        # Query all
        r_all = self.client.get("/tcg/api/database/query")
        self.assertEqual(r_all.status_code, 200)
        d_all = r_all.get_json()
        self.assertEqual(d_all["total"], 3)
        self.assertEqual(d_all["filtered_in_stock"], 2)

        # Filter by Game: pokemon
        r_poke = self.client.get("/tcg/api/database/query?game=pokemon")
        d_poke = r_poke.get_json()
        self.assertEqual(d_poke["total"], 1)
        self.assertEqual(d_poke["items"][0]["name"], "Charizard VMAX")

        # Filter by stock_status: low_stock (qty <= 2 and > 0)
        r_low = self.client.get("/tcg/api/database/query?stock_status=low_stock")
        d_low = r_low.get_json()
        # In stock qty: lotus has 1, bolt has 2 -> both <= 2
        self.assertEqual(d_low["total"], 2)

        # Filter by stock_status: out_of_stock (qty == 0)
        r_out = self.client.get("/tcg/api/database/query?stock_status=out_of_stock")
        d_out = r_out.get_json()
        self.assertEqual(d_out["total"], 1)
        self.assertEqual(d_out["items"][0]["name"], "Charizard VMAX")

        # Search query matching tag ID
        r_tag = self.client.get("/tcg/api/database/query?search=04394D4FBD2A81")
        d_tag = r_tag.get_json()
        self.assertEqual(d_tag["total"], 1)
        self.assertEqual(d_tag["items"][0]["name"], "Black Lotus")

    def test_06_database_inline_edit_and_bulk_adjust(self):
        """6. Verify inline PATCH and bulk operations on database explorer."""
        item = SinglesInventory(
            game="mtg",
            provider_card_id="mox-001",
            name="Mox Sapphire",
            clean_name="mox sapphire",
            set_code="lea",
            set_name="Limited Edition Alpha",
            collector_number="2",
            rarity="rare",
            finish="nonfoil",
            condition="NM",
            quantity=1,
            cost_basis=2000.0,
            sell_price=4000.0,
            market_price=5000.0,
            custom_tag_id="04AA11223344",
        )
        self.session.add(item)
        self.session.commit()
        item_id = item.id

        # Inline edit PATCH
        patch_resp = self.client.patch(
            f"/tcg/api/database/{item_id}",
            json={"sell_price": 4500.0, "quantity": 3}
        )
        self.assertEqual(patch_resp.status_code, 200)
        patched_data = patch_resp.get_json()
        self.assertTrue(patched_data["success"])
        self.assertEqual(patched_data["item"]["sell_price"], 4500.0)
        self.assertEqual(patched_data["item"]["quantity"], 3)

        # Bulk operation: set_market_price
        bulk_resp = self.client.post(
            "/tcg/api/database/bulk-adjust",
            json={"item_ids": [item_id], "action": "set_market_price"}
        )
        self.assertEqual(bulk_resp.status_code, 200)
        self.session.refresh(item)
        self.assertEqual(item.sell_price, 5000.0)

        # Bulk operation: offset_percent (+10%)
        bulk_offset = self.client.post(
            "/tcg/api/database/bulk-adjust",
            json={"item_ids": [item_id], "action": "offset_percent", "percent": 10.0}
        )
        self.assertEqual(bulk_offset.status_code, 200)
        self.session.refresh(item)
        self.assertEqual(item.sell_price, 5500.0)

        # Bulk operation: clear_tags
        bulk_clear = self.client.post(
            "/tcg/api/database/bulk-adjust",
            json={"item_ids": [item_id], "action": "clear_tags"}
        )
        self.assertEqual(bulk_clear.status_code, 200)
        self.session.refresh(item)
        self.assertIsNone(item.custom_tag_id)

    def test_07_fix_thumbnails_endpoint(self):
        """7. Verify POST /tcg/api/maintenance/fix-thumbnails scans and queues missing images."""
        # Add item with missing image_path
        item = SinglesInventory(
            game="mtg",
            provider_card_id="scry-test-thumb",
            name="Sol Ring",
            clean_name="sol ring",
            set_code="cmm",
            set_name="Commander Masters",
            collector_number="401",
            rarity="uncommon",
            finish="nonfoil",
            condition="NM",
            quantity=4,
            image_path=None,
            image_uri="https://cards.scryfall.io/large/front/test.jpg"
        )
        self.session.add(item)
        self.session.commit()

        resp = self.client.post("/tcg/api/maintenance/fix-thumbnails")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertGreaterEqual(data.get("queued_count", 0), 1)

        # Status check
        status_resp = self.client.get("/tcg/api/maintenance/thumbnails-status")
        self.assertEqual(status_resp.status_code, 200)
        status_data = status_resp.get_json()
        self.assertTrue(status_data.get("success"))
        self.assertIn("status", status_data)

    def test_08_plugin_status_version(self):
        """8. Verify GET /tcg/status reports version 1.0.1."""
        resp = self.client.get("/tcg/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("version"), "1.0.1")
        self.assertEqual(data.get("status"), "active")
        self.assertEqual(data.get("addon"), "tcg_pos")


if __name__ == "__main__":
    unittest.main()
