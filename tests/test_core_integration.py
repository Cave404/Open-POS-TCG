"""
OpenPOS-TCG Addon
File: tests/test_core_integration.py
Addon ID: tcg_pos

Integration tests verifying OpenPOS Core customer bridge, buylist credit deposits,
register store credit redemption, ghost tag wiping, and README attribution.
"""

from datetime import datetime, timezone
from pathlib import Path
import sys
from unittest.mock import patch, MagicMock

import pytest
import requests
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models import Base, SinglesInventory
from plugin import addon_bp
from services.core_customer_client import CoreCustomerClient


@pytest.fixture(scope="module")
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine)


@pytest.fixture
def app(session_factory):
    app = Flask(__name__, template_folder="../templates")
    app.config["TESTING"] = True
    app.config["DB_SESSION_FACTORY"] = session_factory
    app.config["ENABLE_CASH_DRAWER"] = False
    app.config["ENABLE_RECEIPT_PRINTER"] = False
    app.config["CORE_BASE_URL"] = "http://127.0.0.1:5000"
    app.register_blueprint(addon_bp)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


class TestCoreCustomerClient:
    """Verifies CoreCustomerClient endpoint calls and timeout/error resilience."""

    def test_resolve_customer_success(self):
        client = CoreCustomerClient("http://mock-core:5000")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "found": True,
            "customer": {"id": 101, "name": "Ash Ketchum", "balance": 45.50}
        }

        with patch.object(client.session, "get", return_value=mock_resp) as mock_get:
            res = client.resolve_customer("04394D4FBD2A81")
            assert res is not None
            assert res["id"] == 101
            assert res["name"] == "Ash Ketchum"
            mock_get.assert_called_once()

    def test_resolve_customer_timeout_graceful(self):
        client = CoreCustomerClient("http://mock-core:5000")
        with patch.object(client.session, "get", side_effect=requests.exceptions.Timeout("Timeout")):
            res = client.resolve_customer("unknown")
            assert res is None

    def test_deposit_trade_in_credit(self):
        client = CoreCustomerClient("http://mock-core:5000")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "new_balance": 150.00}

        with patch.object(client.session, "post", return_value=mock_resp) as mock_post:
            data = client.deposit_trade_in_credit(
                customer_id=101,
                amount=50.00,
                batch_number="BUY-20260915-ABCD"
            )
            assert data["success"] is True
            assert data["new_balance"] == 150.00
            mock_post.assert_called_once()

    def test_redeem_store_credit(self):
        client = CoreCustomerClient("http://mock-core:5000")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "remaining_balance": 25.00}

        with patch.object(client.session, "post", return_value=mock_resp) as mock_post:
            data = client.redeem_store_credit(
                customer_id=101,
                amount=25.00,
                transaction_number="TXN-20260915-1234"
            )
            assert data["success"] is True
            mock_post.assert_called_once()


class TestGhostTagWiper:
    """Verifies that customer loyalty tags are unlinked from singles inventory."""

    def test_ghost_tag_wipe_endpoint(self, client, session_factory):
        session = session_factory()
        # Seed an item with the custom_tag_id
        target_uid = "04394D4FBD2A81"
        card = SinglesInventory(
            game="mtg",
            provider_card_id="wipe-001",
            name="Ghost Tag Card",
            clean_name="ghost tag card",
            set_code="tst",
            set_name="Test Set",
            collector_number="999",
            rarity="rare",
            finish="nonfoil",
            condition="NM",
            quantity=1,
            cost_basis=5.00,
            sell_price=10.00,
            custom_tag_id=target_uid,
            api_metadata={}
        )
        session.add(card)
        session.commit()
        card_id = card.id
        session.close()

        # Call wipe endpoint
        resp = client.post("/tcg/api/internal/ghost-tag-wipe", json={
            "nfc_uid": "04:39:4D:4F:BD:2A:81"
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["unlinked_items"] >= 1

        # Assert custom_tag_id is now None
        verify_session = session_factory()
        updated_card = verify_session.query(SinglesInventory).filter_by(id=card_id).first()
        assert updated_card.custom_tag_id is None
        verify_session.close()


class TestAttributionAndDocumentation:
    """Verifies that README.md exists and contains explicit compliance and attribution sections."""

    def test_readme_exists_and_contains_attributions(self):
        readme_path = PROJECT_ROOT / "README.md"
        assert readme_path.exists(), "README.md must exist in project root"
        content = readme_path.read_text(encoding="utf-8")

        assert "Scryfall" in content, "README must contain Scryfall attribution"
        assert "TCGdex" in content, "README must contain TCGdex attribution"
        assert "Wizards of the Coast" in content, "README must contain Wizards of the Coast copyright statement"
        assert "Pokémon" in content or "Pokemon" in content, "README must contain Pokemon trademark attribution"
