"""
OpenPOS-TCG Addon
File: tests/test_checkout_settlement.py
Addon ID: tcg_pos

Automated test suite for the TCG POS Transaction Settlement Workflow.
Covers migration integrity, atomic inventory decrements, split-tender
validation, COGS snapshotting, receipt endpoint, and hardware fail-safety.
"""

import os
import sys
from pathlib import Path

# Ensure project root is importable (same pattern as other test modules)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, SinglesInventory, TCGTransaction, TCGTransactionItem, TCGTransactionTender
from plugin import addon_bp

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def engine():
    """In-memory SQLite engine for the full test session."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False}
    )
    return eng


@pytest.fixture(scope="module")
def db_setup(engine):
    """Create all tables (verifies models migrate) and seed inventory."""
    Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine)
    session = Session()

    def _card(game, name, qty, cost, sell, provider_id, finish="nonfoil", condition="NM"):
        return SinglesInventory(
            game=game,
            provider_card_id=provider_id,
            name=name,
            clean_name=name.lower(),
            set_code="tst",
            set_name="Test Set",
            collector_number="001",
            rarity="rare",
            finish=finish,
            condition=condition,
            quantity=qty,
            cost_basis=cost,
            sell_price=sell,
            api_metadata={}
        )

    items = [
        # MTG in-stock cards
        _card("mtg", "Black Lotus",          qty=3, cost=10.00, sell=50.00, provider_id="mtg-001"),
        _card("mtg", "Mox Pearl",            qty=2, cost=5.00,  sell=25.00, provider_id="mtg-002"),
        _card("mtg", "Underground Sea",      qty=1, cost=8.00,  sell=40.00, provider_id="mtg-003"),
        _card("mtg", "Volcanic Island",      qty=0, cost=7.00,  sell=35.00, provider_id="mtg-004"),  # out of stock
        _card("mtg", "Timetwister",          qty=2, cost=15.00, sell=60.00, provider_id="mtg-005"),
        # Pokemon in-stock cards
        _card("pokemon", "Charizard Base",   qty=4, cost=20.00, sell=100.00, provider_id="poke-001"),
        _card("pokemon", "Blastoise Base",   qty=1, cost=15.00, sell=75.00,  provider_id="poke-002"),
        _card("pokemon", "Venusaur Base",    qty=2, cost=12.00, sell=60.00,  provider_id="poke-003"),
        _card("pokemon", "Pikachu Promo",    qty=0, cost=2.00,  sell=10.00,  provider_id="poke-004"),  # out of stock
        _card("pokemon", "Mewtwo EX",        qty=3, cost=8.00,  sell=35.00,  provider_id="poke-005"),
    ]

    for item in items:
        session.add(item)
    session.commit()
    session.close()

    return engine


@pytest.fixture
def session_factory(db_setup):
    """Returns a SQLAlchemy session factory for test isolation."""
    return sessionmaker(bind=db_setup)


@pytest.fixture
def fresh_session(session_factory):
    """Provides a fresh session for direct DB inspection in tests."""
    s = session_factory()
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Helper to look up inventory by name
# ---------------------------------------------------------------------------

def _get_by_name(session, name):
    from models import SinglesInventory
    return session.query(SinglesInventory).filter_by(name=name).first()


# ---------------------------------------------------------------------------
# Test 1: Model Migration — All Three Tables Created
# ---------------------------------------------------------------------------

class TestModelMigration:
    def test_all_tables_created(self, db_setup):
        """Verify Base.metadata.create_all creates all three transaction tables."""
        from sqlalchemy import inspect
        inspector = inspect(db_setup)
        tables = inspector.get_table_names()
        assert "tcg_transactions"        in tables, "tcg_transactions table missing"
        assert "tcg_transaction_items"   in tables, "tcg_transaction_items table missing"
        assert "tcg_transaction_tenders" in tables, "tcg_transaction_tenders table missing"
        assert "singles_inventory"       in tables, "singles_inventory table missing"

    def test_transaction_columns(self, db_setup):
        """Verify TCGTransaction has the required financial columns."""
        from sqlalchemy import inspect
        inspector = inspect(db_setup)
        cols = {c["name"] for c in inspector.get_columns("tcg_transactions")}
        for required in ("id", "receipt_number", "subtotal", "tax_rate", "tax_amount",
                         "grand_total", "total_tendered", "change_due", "status", "hardware_meta"):
            assert required in cols, f"Column '{required}' missing from tcg_transactions"

    def test_item_cogs_column(self, db_setup):
        """Verify TCGTransactionItem has the COGS snapshot column."""
        from sqlalchemy import inspect
        inspector = inspect(db_setup)
        cols = {c["name"] for c in inspector.get_columns("tcg_transaction_items")}
        assert "cost_basis_snapshot" in cols
        assert "unit_price"          in cols
        assert "line_total"          in cols

    def test_tender_columns(self, db_setup):
        """Verify TCGTransactionTender has tender_type and amount columns."""
        from sqlalchemy import inspect
        inspector = inspect(db_setup)
        cols = {c["name"] for c in inspector.get_columns("tcg_transaction_tenders")}
        assert "tender_type" in cols
        assert "amount"      in cols
        assert "change_due"  in cols


# ---------------------------------------------------------------------------
# Test 2: Full Cash Settlement
# ---------------------------------------------------------------------------

class TestCashSettlement:
    def test_cash_settlement_success(self, session_factory, fresh_session):
        """Single cash tender settles transaction and decrements inventory."""
        from services.checkout import CartItem, CheckoutService, TenderEntry

        lotus = _get_by_name(fresh_session, "Black Lotus")
        initial_qty = lotus.quantity

        result = CheckoutService.settle(
            cart_items=[CartItem(inventory_id=lotus.id, quantity=1)],
            tenders=[TenderEntry(tender_type="cash", amount=60.00)],
            tax_rate=0.0,
            config={},
            session_factory=session_factory,
        )

        assert result.success, f"Expected success, got error: {result.error}"
        assert result.receipt_number.startswith("RCT-")
        assert result.transaction_id is not None
        assert result.grand_total == pytest.approx(50.00)
        assert result.change_due == pytest.approx(10.00)

        # Verify inventory was decremented
        fresh_session.expire_all()
        lotus_after = _get_by_name(fresh_session, "Black Lotus")
        assert lotus_after.quantity == initial_qty - 1

    def test_transaction_record_persisted(self, session_factory, fresh_session):
        """Verifies that a TCGTransaction with items and tenders was saved."""
        from services.checkout import CartItem, CheckoutService, TenderEntry

        mox = _get_by_name(fresh_session, "Mox Pearl")

        result = CheckoutService.settle(
            cart_items=[CartItem(inventory_id=mox.id, quantity=1)],
            tenders=[TenderEntry(tender_type="cash", amount=25.00)],
            tax_rate=0.0,
            config={},
            session_factory=session_factory,
        )

        assert result.success

        fresh_session.expire_all()
        txn = fresh_session.query(TCGTransaction).filter_by(
            id=result.transaction_id
        ).first()
        assert txn is not None
        assert txn.status == "completed"
        assert len(txn.items) == 1
        assert len(txn.tenders) == 1
        assert txn.tenders[0].tender_type == "cash"
        assert txn.tenders[0].amount == pytest.approx(25.00)


# ---------------------------------------------------------------------------
# Test 3: Split Tender (Cash + Card)
# ---------------------------------------------------------------------------

class TestSplitTender:
    def test_split_cash_and_card(self, session_factory, fresh_session):
        """Cash + Card tenders summing to grand total are accepted."""
        from services.checkout import CartItem, CheckoutService, TenderEntry

        charizard = _get_by_name(fresh_session, "Charizard Base")
        initial_qty = charizard.quantity

        result = CheckoutService.settle(
            cart_items=[CartItem(inventory_id=charizard.id, quantity=2)],
            tenders=[
                TenderEntry(tender_type="cash", amount=100.00),
                TenderEntry(tender_type="card", amount=100.00, reference="xxxx-4321"),
            ],
            tax_rate=0.0,
            config={},
            session_factory=session_factory,
        )

        assert result.success, f"Split tender failed: {result.error}"
        assert result.grand_total == pytest.approx(200.00)

        fresh_session.expire_all()
        txn = fresh_session.query(TCGTransaction).filter_by(
            id=result.transaction_id
        ).first()
        assert len(txn.tenders) == 2
        tender_types = {t.tender_type for t in txn.tenders}
        assert "cash" in tender_types
        assert "card" in tender_types

        charizard_after = _get_by_name(fresh_session, "Charizard Base")
        assert charizard_after.quantity == initial_qty - 2

    def test_split_three_tender_types(self, session_factory, fresh_session):
        """Cash + Card + Store Credit all three tender types in one transaction."""
        from services.checkout import CartItem, CheckoutService, TenderEntry

        blastoise = _get_by_name(fresh_session, "Blastoise Base")

        result = CheckoutService.settle(
            cart_items=[CartItem(inventory_id=blastoise.id, quantity=1)],
            tenders=[
                TenderEntry(tender_type="cash",         amount=25.00),
                TenderEntry(tender_type="card",         amount=25.00),
                TenderEntry(tender_type="store_credit", amount=25.00, reference="SC-ACCT-001"),
            ],
            tax_rate=0.0,
            config={},
            session_factory=session_factory,
        )

        assert result.success, f"Three-way split failed: {result.error}"
        assert result.grand_total == pytest.approx(75.00)

        fresh_session.expire_all()
        txn = fresh_session.query(TCGTransaction).filter_by(
            id=result.transaction_id
        ).first()
        assert len(txn.tenders) == 3


# ---------------------------------------------------------------------------
# Test 4: Store Credit Tender
# ---------------------------------------------------------------------------

class TestStoreCreditTender:
    def test_store_credit_full_amount(self, session_factory, fresh_session):
        """Store credit covering full sale amount is accepted."""
        from services.checkout import CartItem, CheckoutService, TenderEntry

        mewtwo = _get_by_name(fresh_session, "Mewtwo EX")
        initial_qty = mewtwo.quantity

        result = CheckoutService.settle(
            cart_items=[CartItem(inventory_id=mewtwo.id, quantity=1)],
            tenders=[TenderEntry(tender_type="store_credit", amount=35.00, reference="TRADE-IN-007")],
            tax_rate=0.0,
            config={},
            session_factory=session_factory,
        )

        assert result.success, f"Store credit settlement failed: {result.error}"
        assert result.grand_total == pytest.approx(35.00)
        assert result.change_due == pytest.approx(0.00)

        fresh_session.expire_all()
        mewtwo_after = _get_by_name(fresh_session, "Mewtwo EX")
        assert mewtwo_after.quantity == initial_qty - 1


# ---------------------------------------------------------------------------
# Test 5: Insufficient Tender Rejected
# ---------------------------------------------------------------------------

class TestInsufficientTender:
    def test_tender_below_total_rejected(self, session_factory, fresh_session):
        """CheckoutService rejects when tendered amount < grand total."""
        from services.checkout import CartItem, CheckoutService, TenderEntry

        timetwister = _get_by_name(fresh_session, "Timetwister")
        initial_qty = timetwister.quantity

        result = CheckoutService.settle(
            cart_items=[CartItem(inventory_id=timetwister.id, quantity=1)],
            tenders=[TenderEntry(tender_type="cash", amount=10.00)],  # only $10 for $60 item
            tax_rate=0.0,
            config={},
            session_factory=session_factory,
        )

        assert not result.success
        assert "Insufficient tender" in result.error

        # Inventory must NOT have been decremented
        fresh_session.expire_all()
        timetwister_after = _get_by_name(fresh_session, "Timetwister")
        assert timetwister_after.quantity == initial_qty

    def test_invalid_tender_type_rejected(self, session_factory, fresh_session):
        """Unknown tender type is rejected before any DB writes."""
        from services.checkout import CartItem, CheckoutService, TenderEntry

        lotus = _get_by_name(fresh_session, "Black Lotus")
        initial_qty = lotus.quantity

        result = CheckoutService.settle(
            cart_items=[CartItem(inventory_id=lotus.id, quantity=1)],
            tenders=[TenderEntry(tender_type="bitcoin", amount=9999.00)],
            tax_rate=0.0,
            config={},
            session_factory=session_factory,
        )

        assert not result.success
        assert "Invalid tender type" in result.error

        fresh_session.expire_all()
        assert _get_by_name(fresh_session, "Black Lotus").quantity == initial_qty

    def test_empty_cart_rejected(self, session_factory):
        """Empty cart is rejected immediately."""
        from services.checkout import CheckoutService, TenderEntry

        result = CheckoutService.settle(
            cart_items=[],
            tenders=[TenderEntry(tender_type="cash", amount=50.00)],
            config={},
            session_factory=session_factory,
        )

        assert not result.success
        assert "empty" in result.error.lower()


# ---------------------------------------------------------------------------
# Test 6: Zero-Stock Rejection (Atomic)
# ---------------------------------------------------------------------------

class TestZeroStockRejection:
    def test_out_of_stock_item_rejected(self, session_factory, fresh_session):
        """Buying an item with quantity=0 is atomically rejected."""
        from services.checkout import CartItem, CheckoutService, TenderEntry

        volcanic = _get_by_name(fresh_session, "Volcanic Island")
        assert volcanic.quantity == 0

        result = CheckoutService.settle(
            cart_items=[CartItem(inventory_id=volcanic.id, quantity=1)],
            tenders=[TenderEntry(tender_type="cash", amount=100.00)],
            tax_rate=0.0,
            config={},
            session_factory=session_factory,
        )

        assert not result.success
        assert "Insufficient stock" in result.error or "insufficient" in result.error.lower()

    def test_mixed_cart_with_oos_item_rolled_back(self, session_factory, fresh_session):
        """If one item in a multi-item cart is OOS, the whole transaction rolls back."""
        from services.checkout import CartItem, CheckoutService, TenderEntry

        pikachu = _get_by_name(fresh_session, "Pikachu Promo")    # out of stock
        venusaur = _get_by_name(fresh_session, "Venusaur Base")    # in stock
        initial_venusaur_qty = venusaur.quantity

        result = CheckoutService.settle(
            cart_items=[
                CartItem(inventory_id=venusaur.id, quantity=1),
                CartItem(inventory_id=pikachu.id,  quantity=1),   # will fail
            ],
            tenders=[TenderEntry(tender_type="cash", amount=100.00)],
            tax_rate=0.0,
            config={},
            session_factory=session_factory,
        )

        assert not result.success

        # Venusaur quantity must be unchanged due to rollback
        fresh_session.expire_all()
        assert _get_by_name(fresh_session, "Venusaur Base").quantity == initial_venusaur_qty


# ---------------------------------------------------------------------------
# Test 7: COGS Snapshot Accuracy
# ---------------------------------------------------------------------------

class TestCOGSSnapshot:
    def test_cost_basis_snapshot_matches_inventory(self, session_factory, fresh_session):
        """TCGTransactionItem.cost_basis_snapshot equals inventory.cost_basis at sale time."""
        from services.checkout import CartItem, CheckoutService, TenderEntry

        underground = _get_by_name(fresh_session, "Underground Sea")
        expected_cost_basis = underground.cost_basis   # 8.00

        result = CheckoutService.settle(
            cart_items=[CartItem(inventory_id=underground.id, quantity=1)],
            tenders=[TenderEntry(tender_type="cash", amount=50.00)],
            tax_rate=0.0,
            config={},
            session_factory=session_factory,
        )

        assert result.success

        fresh_session.expire_all()
        item_row = (
            fresh_session.query(TCGTransactionItem)
            .filter_by(transaction_id=result.transaction_id)
            .first()
        )
        assert item_row is not None
        assert item_row.cost_basis_snapshot == pytest.approx(expected_cost_basis)
        assert item_row.unit_price          == pytest.approx(40.00)
        assert item_row.line_total          == pytest.approx(40.00)

    def test_line_total_calculated_correctly(self, session_factory, fresh_session):
        """line_total = unit_price * quantity_sold."""
        from services.checkout import CartItem, CheckoutService, TenderEntry

        timetwister = _get_by_name(fresh_session, "Timetwister")
        if timetwister.quantity < 1:
            pytest.skip("Timetwister out of stock (already sold in earlier test)")

        result = CheckoutService.settle(
            cart_items=[CartItem(inventory_id=timetwister.id, quantity=1, unit_price=60.00)],
            tenders=[TenderEntry(tender_type="cash", amount=60.00)],
            tax_rate=0.0,
            config={},
            session_factory=session_factory,
        )

        assert result.success

        fresh_session.expire_all()
        item_row = (
            fresh_session.query(TCGTransactionItem)
            .filter_by(transaction_id=result.transaction_id)
            .first()
        )
        assert item_row.line_total == pytest.approx(60.00)
        assert item_row.unit_price == pytest.approx(60.00)
        assert item_row.quantity_sold == 1

    def test_tax_is_applied_to_grand_total(self, session_factory, fresh_session):
        """grand_total = subtotal + (subtotal * tax_rate)."""
        from services.checkout import CartItem, CheckoutService, TenderEntry

        mewtwo = _get_by_name(fresh_session, "Mewtwo EX")
        if mewtwo.quantity < 1:
            pytest.skip("Mewtwo out of stock")

        result = CheckoutService.settle(
            cart_items=[CartItem(inventory_id=mewtwo.id, quantity=1, unit_price=35.00)],
            tenders=[TenderEntry(tender_type="cash", amount=40.00)],
            tax_rate=0.10,  # 10% for easy math
            config={},
            session_factory=session_factory,
        )

        assert result.success
        assert result.grand_total == pytest.approx(38.50)   # 35 + 3.50
        assert result.change_due  == pytest.approx(1.50)


# ---------------------------------------------------------------------------
# Test 8: Receipt Endpoint
# ---------------------------------------------------------------------------

class TestReceiptEndpoint:
    @pytest.fixture
    def app(self, session_factory):
        """Minimal Flask test app wired to the test session factory."""
        app = Flask(__name__, template_folder="../templates")
        app.config["TESTING"] = True
        app.config["DB_SESSION_FACTORY"] = session_factory
        app.config["ENABLE_CASH_DRAWER"] = False
        app.config["ENABLE_RECEIPT_PRINTER"] = False

        app.register_blueprint(addon_bp)

        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_settle_endpoint_returns_201(self, client, session_factory, fresh_session):
        """POST /tcg/api/checkout/settle returns 201 with transaction details."""
        from services.checkout import CartItem, CheckoutService, TenderEntry
        lotus = _get_by_name(fresh_session, "Black Lotus")
        if lotus.quantity < 1:
            pytest.skip("Black Lotus out of stock")

        resp = client.post("/tcg/api/checkout/settle", json={
            "cart": [{"inventory_id": lotus.id, "quantity": 1, "unit_price": 50.00}],
            "tenders": [{"tender_type": "cash", "amount": 60.00}],
            "tax_rate": 0.0,
        })

        assert resp.status_code == 201
        data = resp.get_json()
        assert data["success"] is True
        assert data["receipt_url"].startswith("/tcg/receipt/")

    def test_receipt_view_returns_200(self, client, session_factory, fresh_session):
        """GET /tcg/receipt/<id> returns 200 with receipt content."""
        from services.checkout import CartItem, CheckoutService, TenderEntry

        charizard = _get_by_name(fresh_session, "Charizard Base")
        if charizard.quantity < 1:
            pytest.skip("Charizard out of stock")

        result = CheckoutService.settle(
            cart_items=[CartItem(inventory_id=charizard.id, quantity=1)],
            tenders=[TenderEntry(tender_type="cash", amount=100.00)],
            tax_rate=0.0,
            config={},
            session_factory=session_factory,
        )
        assert result.success

        resp = client.get(f"/tcg/receipt/{result.transaction_id}")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert result.receipt_number in body
        assert "Back to Register" in body
        assert "Print Receipt" in body

    def test_receipt_view_404_for_missing(self, client):
        """GET /tcg/receipt/99999 returns 404 for non-existent transaction."""
        resp = client.get("/tcg/receipt/99999")
        assert resp.status_code == 404

    def test_settle_endpoint_missing_cart(self, client):
        """POST /tcg/api/checkout/settle with empty cart returns 400."""
        resp = client.post("/tcg/api/checkout/settle", json={
            "cart": [],
            "tenders": [{"tender_type": "cash", "amount": 50.00}],
            "tax_rate": 0.0,
        })
        assert resp.status_code == 400

    def test_settle_endpoint_insufficient_tender(self, client, fresh_session):
        """POST /tcg/api/checkout/settle with insufficient tender returns 400."""
        lotus = _get_by_name(fresh_session, "Black Lotus")
        if lotus.quantity < 1:
            pytest.skip("Black Lotus out of stock")

        resp = client.post("/tcg/api/checkout/settle", json={
            "cart": [{"inventory_id": lotus.id, "quantity": 1, "unit_price": 50.00}],
            "tenders": [{"tender_type": "cash", "amount": 1.00}],
            "tax_rate": 0.0,
        })
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False


# ---------------------------------------------------------------------------
# Test 9: Hardware Failure is Non-Blocking
# ---------------------------------------------------------------------------

class TestHardwareNonBlocking:
    def test_hardware_failure_does_not_abort_transaction(self, session_factory, fresh_session):
        """
        With hardware hub unreachable, settle() still returns success=True.
        Hardware errors are captured in hardware_errors but do not raise.
        """
        from services.checkout import CartItem, CheckoutService, TenderEntry
        import importlib
        checkout_mod = importlib.import_module("services.checkout")

        original_drawer = checkout_mod._fire_cash_drawer
        original_receipt = checkout_mod._fire_receipt_spool

        def fail_drawer(*args, **kwargs):
            return "Connection refused: hardware hub unreachable"

        def fail_receipt(*args, **kwargs):
            return "Connection refused: receipt printer unreachable"

        checkout_mod._fire_cash_drawer   = fail_drawer
        checkout_mod._fire_receipt_spool = fail_receipt

        try:
            mewtwo = _get_by_name(fresh_session, "Mewtwo EX")
            if mewtwo.quantity < 1:
                pytest.skip("Mewtwo out of stock")

            result = CheckoutService.settle(
                cart_items=[CartItem(inventory_id=mewtwo.id, quantity=1)],
                tenders=[TenderEntry(tender_type="cash", amount=35.00)],
                tax_rate=0.0,
                config={
                    "enable_cash_drawer":     True,
                    "enable_receipt_printer": True,
                    "hardware_hub_url":       "http://localhost:59999",  # nothing listening
                },
                session_factory=session_factory,
            )

            # Transaction MUST succeed even when hardware fails
            assert result.success, f"Expected success despite hardware failure, got: {result.error}"
            assert result.transaction_id is not None
            assert result.receipt_number is not None

        finally:
            checkout_mod._fire_cash_drawer   = original_drawer
            checkout_mod._fire_receipt_spool = original_receipt
