"""
OpenPOS-TCG Addon
File: tests/test_market_refresher.py
Addon ID: tcg_pos

Unit and Integration Test Suite for MarketRefresherService & Pricing Engine:
1. Verifies centralized provider factory & registry (GAME_PROVIDERS, get_provider).
2. Verifies selective game execution (refresh MTG while skipping Pokémon, or vice-versa).
3. Verifies in-stock only (quantity > 0) and max_age_days threshold filtering.
4. Verifies price drift & volatility protection:
   - Specifically asserts api_metadata['last_price_drift'] is populated on price spikes.
5. Verifies configurable sell_price auto-adjustment policy and margin multiplier.
6. Verifies cooperative non-blocking worker cancellation.
7. Verifies Flask REST endpoints (/tcg/pricing, /tcg/api/pricing/*, alerts apply/dismiss).
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import time
from typing import Dict, Optional
from unittest.mock import MagicMock, patch
import pytest

# Ensure project root is available on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, SinglesInventory
from plugin import addon_bp
from providers import GAME_PROVIDERS, get_provider
from providers.base import BaseTCGProvider
from providers.mtg_scryfall import ScryfallProvider
from providers.pokemon_tcgdex import PokemonTCGdexProvider
from services.market_refresher import MarketRefresherService, PriceRefreshJob


# --- Test Fixtures ---

from sqlalchemy.pool import StaticPool

@pytest.fixture
def test_db():
    """Sets up an isolated in-memory SQLite database sessionmaker with thread sharing."""
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    return engine, session_factory


@pytest.fixture
def seed_inventory(test_db):
    """Seeds test singles inventory covering games, conditions, and stock levels."""
    _, session_factory = test_db
    session = session_factory()

    stale_time = datetime.now(timezone.utc) - timedelta(days=2)
    recent_time = datetime.now(timezone.utc) - timedelta(minutes=30)

    items = [
        # MTG 1: In-stock, stale (will test +150% price spike)
        SinglesInventory(
            game="mtg",
            provider_card_id="mtg-sol-ring",
            name="Sol Ring",
            clean_name="sol ring",
            set_code="cmm",
            set_name="Commander Masters",
            collector_number="401",
            rarity="uncommon",
            finish="nonfoil",
            condition="NM",
            quantity=4,
            cost_basis=5.00,
            sell_price=10.00,
            market_price=10.00,
            updated_at=stale_time,
            api_metadata={}
        ),
        # MTG 2: Out of stock (quantity = 0), stale
        SinglesInventory(
            game="mtg",
            provider_card_id="mtg-black-lotus",
            name="Black Lotus",
            clean_name="black lotus",
            set_code="lea",
            set_name="Limited Edition Alpha",
            collector_number="232",
            rarity="rare",
            finish="nonfoil",
            condition="NM",
            quantity=0,
            cost_basis=3000.00,
            sell_price=5000.00,
            market_price=5000.00,
            updated_at=stale_time,
            api_metadata={}
        ),
        # MTG 3: In-stock, recently updated (within 30 mins)
        SinglesInventory(
            game="mtg",
            provider_card_id="mtg-counterspell",
            name="Counterspell",
            clean_name="counterspell",
            set_code="dmr",
            set_name="Dominaria Remastered",
            collector_number="45",
            rarity="uncommon",
            finish="nonfoil",
            condition="NM",
            quantity=2,
            cost_basis=0.80,
            sell_price=1.50,
            market_price=1.50,
            updated_at=recent_time,
            api_metadata={}
        ),
        # Pokémon 1: In-stock, stale (normal drift +5%)
        SinglesInventory(
            game="pokemon",
            provider_card_id="swsh3-136",
            name="Pikachu",
            clean_name="pikachu",
            set_code="swsh3",
            set_name="Darkness Ablaze",
            collector_number="136",
            rarity="common",
            finish="nonfoil",
            condition="NM",
            quantity=5,
            cost_basis=10.00,
            sell_price=20.00,
            market_price=20.00,
            updated_at=stale_time,
            api_metadata={}
        ),
        # Pokémon 2: In-stock, stale (will test -30% price crash)
        SinglesInventory(
            game="pokemon",
            provider_card_id="base1-4",
            name="Charizard",
            clean_name="charizard",
            set_code="base1",
            set_name="Base Set",
            collector_number="4",
            rarity="rare holo",
            finish="holo",
            condition="NM",
            quantity=1,
            cost_basis=80.00,
            sell_price=100.00,
            market_price=100.00,
            updated_at=stale_time,
            api_metadata={}
        )
    ]

    session.add_all(items)
    session.commit()
    session.close()
    return items


@pytest.fixture
def test_app(test_db):
    """Configures an isolated test Flask application with DB engine."""
    engine, session_factory = test_db
    app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))
    app.config["TESTING"] = True
    app.secret_key = "test-pricing-secret"
    app.config["DB_ENGINE"] = engine
    app.config["DB_SESSION_FACTORY"] = session_factory

    app.register_blueprint(addon_bp)
    client = app.test_client()
    return app, client, session_factory


# --- Provider Registry Tests ---

def test_provider_registry():
    """Verifies centralized provider factory and registered game mapping."""
    assert "mtg" in GAME_PROVIDERS
    assert "pokemon" in GAME_PROVIDERS

    mtg_provider = get_provider("mtg")
    assert isinstance(mtg_provider, ScryfallProvider)

    poke_provider = get_provider("pokemon")
    assert isinstance(poke_provider, PokemonTCGdexProvider)

    # Case insensitive
    assert get_provider("POKEMON") is poke_provider
    assert get_provider("MTG") is mtg_provider

    # Unsupported game slug
    assert get_provider("yugioh") is None


# --- Service Layer Tests ---

def test_selective_game_execution(seed_inventory, test_db):
    """Verifies running price updates for MTG only leaves Pokémon cards untouched."""
    _, session_factory = test_db
    service = MarketRefresherService()

    # Mock provider price lookups
    def mock_fetch(provider_id):
        if provider_id == "mtg-sol-ring":
            return {"market": 12.00, "low": 10.00}
        if provider_id == "mtg-black-lotus":
            return {"market": 5500.00, "low": 4800.00}
        if provider_id == "swsh3-136":
            return {"market": 99.00}
        return {"market": 1.00}

    mock_mtg = MagicMock()
    mock_mtg.fetch_market_prices.side_effect = mock_fetch

    mock_poke = MagicMock()
    mock_poke.fetch_market_prices.side_effect = mock_fetch

    with patch("services.market_refresher.get_provider") as mock_gp:
        mock_gp.side_effect = lambda g: mock_mtg if g == "mtg" else mock_poke

        # Synchronously execute worker for MTG only, all stock levels, all ages
        job = PriceRefreshJob(job_id="test-job-1", game="mtg", in_stock_only=False, max_age_days=0)
        service._run_refresh(
            job=job,
            in_stock_only=False,
            max_age_days=0,
            auto_adjust_sell_price=False,
            margin_multiplier=1.0,
            session_factory=session_factory
        )

        assert job.status == "completed"
        assert job.total_items == 3  # Only 3 MTG cards in DB
        assert job.updated_items == 3

    # Assert Pokémon provider was never called
    mock_poke.fetch_market_prices.assert_not_called()

    # Verify DB: MTG cards updated, Pokémon cards unchanged
    session = session_factory()
    sol_ring = session.query(SinglesInventory).filter_by(provider_card_id="mtg-sol-ring").first()
    black_lotus = session.query(SinglesInventory).filter_by(provider_card_id="mtg-black-lotus").first()
    assert sol_ring.market_price == 12.00
    assert black_lotus.market_price == 5500.00

    # Assert Pokémon cards remain untouched
    pokemon_cards = session.query(SinglesInventory).filter_by(game="pokemon").all()
    assert len(pokemon_cards) == 2, "Expected 2 Pokémon cards in test database"
    for p_card in pokemon_cards:
        if p_card.provider_card_id == "swsh3-136":
            assert p_card.name == "Pikachu"
            assert p_card.market_price == 20.00, f"Pikachu market_price was modified: {p_card.market_price}"
            assert p_card.sell_price == 20.00, f"Pikachu sell_price was modified: {p_card.sell_price}"
            assert p_card.quantity == 5, f"Pikachu quantity was modified: {p_card.quantity}"
            assert "last_price_drift" not in (p_card.api_metadata or {}), "Pikachu api_metadata was modified!"
        elif p_card.provider_card_id == "base1-4":
            assert p_card.name == "Charizard"
            assert p_card.market_price == 100.00, f"Charizard market_price was modified: {p_card.market_price}"
            assert p_card.sell_price == 100.00, f"Charizard sell_price was modified: {p_card.sell_price}"
            assert p_card.quantity == 1, f"Charizard quantity was modified: {p_card.quantity}"
            assert "last_price_drift" not in (p_card.api_metadata or {}), "Charizard api_metadata was modified!"
    session.close()


def test_pokemon_cards_remain_untouched_when_refreshing_mtg(seed_inventory, test_db):
    """
    EXPLICIT REQUIREMENT:
    Assert Pokémon cards remain untouched when selective refresh is executed for MTG.
    Verifies market_price, sell_price, quantity, api_metadata, and timestamps for all Pokémon cards.
    """
    _, session_factory = test_db
    service = MarketRefresherService()

    mock_provider = MagicMock()
    mock_provider.fetch_market_prices.return_value = {"market": 999.99}

    # Record snapshot of Pokémon cards prior to execution
    session = session_factory()
    before_pokemon = {
        p.provider_card_id: {
            "market_price": p.market_price,
            "sell_price": p.sell_price,
            "quantity": p.quantity,
            "updated_at": p.updated_at,
            "api_metadata": dict(p.api_metadata or {})
        }
        for p in session.query(SinglesInventory).filter_by(game="pokemon").all()
    }
    session.close()

    with patch("services.market_refresher.get_provider") as mock_gp:
        # If MTG, return mock provider; if Pokémon, fail immediately if called
        mock_gp.side_effect = lambda g: mock_provider if g == "mtg" else pytest.fail(f"Pokémon provider was accessed for game '{g}'!")

        job = PriceRefreshJob(job_id="test-poke-untouched", game="mtg", in_stock_only=False, max_age_days=0)
        service._run_refresh(
            job=job,
            in_stock_only=False,
            max_age_days=0,
            auto_adjust_sell_price=True,
            margin_multiplier=1.25,
            session_factory=session_factory
        )

        assert job.status == "completed"

    # Verify every Pokémon card in the database remains completely untouched
    session = session_factory()
    after_pokemon = session.query(SinglesInventory).filter_by(game="pokemon").all()
    assert len(after_pokemon) == 2, "Expected exactly 2 Pokémon cards in database"

    for p_card in after_pokemon:
        orig = before_pokemon[p_card.provider_card_id]
        # Assert Pokémon cards remain untouched across all pricing, inventory, and metadata attributes
        assert p_card.market_price == orig["market_price"], f"Pokémon {p_card.name} market_price changed!"
        assert p_card.sell_price == orig["sell_price"], f"Pokémon {p_card.name} sell_price changed!"
        assert p_card.quantity == orig["quantity"], f"Pokémon {p_card.name} quantity changed!"
        assert p_card.updated_at == orig["updated_at"], f"Pokémon {p_card.name} updated_at changed!"
        assert p_card.api_metadata == orig["api_metadata"], f"Pokémon {p_card.name} api_metadata changed!"
        assert "last_price_drift" not in (p_card.api_metadata or {})

    session.close()


def test_in_stock_only_filtering(seed_inventory, test_db):
    """Verifies in_stock_only=True skips out-of-stock items (quantity=0)."""
    _, session_factory = test_db
    service = MarketRefresherService()

    mock_provider = MagicMock()
    mock_provider.fetch_market_prices.return_value = {"market": 15.00}

    with patch("services.market_refresher.get_provider", return_value=mock_provider):
        job = PriceRefreshJob(job_id="test-job-2", game="mtg", in_stock_only=True, max_age_days=0)
        service._run_refresh(
            job=job,
            in_stock_only=True,
            max_age_days=0,
            auto_adjust_sell_price=False,
            margin_multiplier=1.0,
            session_factory=session_factory
        )

        # MTG has 2 in-stock cards (Sol Ring qty 4, Counterspell qty 2). Black Lotus is qty 0.
        assert job.total_items == 2
        assert job.processed_items == 2

        # Assert only in-stock MTG cards are queried
        queried_ids = [call.args[0] for call in mock_provider.fetch_market_prices.call_args_list]
        assert set(queried_ids) == {"mtg-sol-ring", "mtg-counterspell"}
        assert "mtg-black-lotus" not in queried_ids

    session = session_factory()
    black_lotus = session.query(SinglesInventory).filter_by(provider_card_id="mtg-black-lotus").first()
    assert black_lotus.market_price == 5000.00  # Untouched
    session.close()


def test_only_in_stock_mtg_cards_are_queried(seed_inventory, test_db):
    """
    EXPLICIT REQUIREMENT:
    Assert only in-stock MTG cards are queried when running with game='mtg' and in_stock_only=True.
    Verifies that:
    1. Provider is strictly called only for cards with game='mtg' and quantity > 0.
    2. Out-of-stock MTG cards (quantity == 0) are NOT queried.
    3. Pokémon cards (even if in-stock) are NOT queried.
    """
    _, session_factory = test_db
    service = MarketRefresherService()

    queried_card_ids = []

    def mock_fetch(card_id):
        queried_card_ids.append(card_id)
        return {"market": 15.00}

    mock_provider = MagicMock()
    mock_provider.fetch_market_prices.side_effect = mock_fetch

    with patch("services.market_refresher.get_provider") as mock_gp:
        # If MTG, return mock provider; if Pokémon, fail immediately
        mock_gp.side_effect = lambda g: mock_provider if g == "mtg" else pytest.fail(f"Non-MTG provider requested for game: {g}")

        job = PriceRefreshJob(job_id="test-in-stock-only", game="mtg", in_stock_only=True, max_age_days=0)
        service._run_refresh(
            job=job,
            in_stock_only=True,
            max_age_days=0,
            auto_adjust_sell_price=False,
            margin_multiplier=1.0,
            session_factory=session_factory
        )

        assert job.status == "completed"

    # ASSERT ONLY IN-STOCK MTG CARDS ARE QUERIED:
    assert set(queried_card_ids) == {"mtg-sol-ring", "mtg-counterspell"}, (
        f"Expected only in-stock MTG cards to be queried, got: {queried_card_ids}"
    )
    assert len(queried_card_ids) == 2

    # Specifically assert out-of-stock MTG card was NOT queried
    assert "mtg-black-lotus" not in queried_card_ids

    # Specifically assert in-stock Pokémon cards were NOT queried
    assert "swsh3-136" not in queried_card_ids
    assert "base1-4" not in queried_card_ids

    # Verify in database:
    session = session_factory()
    # In-stock MTG cards were updated
    sol_ring = session.query(SinglesInventory).filter_by(provider_card_id="mtg-sol-ring").first()
    counterspell = session.query(SinglesInventory).filter_by(provider_card_id="mtg-counterspell").first()
    assert sol_ring.market_price == 15.00
    assert counterspell.market_price == 15.00

    # Out-of-stock MTG card untouched
    black_lotus = session.query(SinglesInventory).filter_by(provider_card_id="mtg-black-lotus").first()
    assert black_lotus.market_price == 5000.00

    # Pokémon cards untouched
    pikachu = session.query(SinglesInventory).filter_by(provider_card_id="swsh3-136").first()
    charizard = session.query(SinglesInventory).filter_by(provider_card_id="base1-4").first()
    assert pikachu.market_price == 20.00
    assert charizard.market_price == 100.00
    session.close()


def test_max_age_days_filtering(seed_inventory, test_db):
    """Verifies max_age_days=1 skips recently updated cards."""
    _, session_factory = test_db
    service = MarketRefresherService()

    mock_provider = MagicMock()
    mock_provider.fetch_market_prices.return_value = {"market": 20.00}

    with patch("services.market_refresher.get_provider", return_value=mock_provider):
        # Filter for MTG with max_age_days = 1 (skips Counterspell updated 30 mins ago)
        job = PriceRefreshJob(job_id="test-job-3", game="mtg", in_stock_only=False, max_age_days=1)
        service._run_refresh(
            job=job,
            in_stock_only=False,
            max_age_days=1,
            auto_adjust_sell_price=False,
            margin_multiplier=1.0,
            session_factory=session_factory
        )

        # Only Sol Ring and Black Lotus are stale > 1 day
        assert job.total_items == 2
        assert job.processed_items == 2

    session = session_factory()
    counterspell = session.query(SinglesInventory).filter_by(provider_card_id="mtg-counterspell").first()
    assert counterspell.market_price == 1.50  # Untouched
    session.close()


def test_price_volatility_drift_protection_alert_populated(seed_inventory, test_db):
    """
    CRITICAL: Verifies price volatility drift detection (>= 20% swing).
    Explicitly asserts that the price spike card has api_metadata['last_price_drift'] populated.
    """
    _, session_factory = test_db
    service = MarketRefresherService()

    def mock_market_prices(card_id):
        if card_id == "mtg-sol-ring":
            # Spike from $10.00 to $25.00 (+150.0%)
            return {"market": 25.00, "low": 22.00, "foil": 40.00}
        if card_id == "base1-4":
            # Crash from $100.00 to $70.00 (-30.0%)
            return {"market": 70.00, "low": 50.00}
        if card_id == "swsh3-136":
            # Small shift from $20.00 to $21.00 (+5.0% - normal drift)
            return {"market": 21.00}
        if card_id == "mtg-counterspell":
            # No change
            return {"market": 1.50}
        return {"market": 1.00}

    mock_provider = MagicMock()
    mock_provider.fetch_market_prices.side_effect = mock_market_prices

    with patch("services.market_refresher.get_provider", return_value=mock_provider):
        job = PriceRefreshJob(job_id="test-drift-job", in_stock_only=True, max_age_days=0)
        service._run_refresh(
            job=job,
            in_stock_only=True,
            max_age_days=0,
            auto_adjust_sell_price=False,
            margin_multiplier=1.0,
            session_factory=session_factory
        )

        assert job.status == "completed"
        # 2 cards triggered volatility alerts (Sol Ring spike +150%, Charizard crash -30%)
        assert job.volatility_alerts_count == 2

    session = session_factory()
    sol_ring = session.query(SinglesInventory).filter_by(provider_card_id="mtg-sol-ring").first()
    charizard = session.query(SinglesInventory).filter_by(provider_card_id="base1-4").first()
    pikachu = session.query(SinglesInventory).filter_by(provider_card_id="swsh3-136").first()

    # 1. ASSERT THE PRICE SPIKE CARD HAS api_metadata['last_price_drift'] POPULATED
    assert "last_price_drift" in sol_ring.api_metadata, "Price spike card missing last_price_drift!"
    assert sol_ring.api_metadata["last_price_drift"] == {
        "old": 10.0,
        "new": 25.0,
        "change_pct": 150.0
    }
    assert sol_ring.api_metadata.get("price_alert") is True
    assert sol_ring.market_price == 25.00
    assert sol_ring.foil_price == 40.00
    # sell_price remained unchanged ($10.00) because auto_adjust_sell_price was False
    assert sol_ring.sell_price == 10.00

    # 2. Check price crash card also populated
    assert "last_price_drift" in charizard.api_metadata
    assert charizard.api_metadata["last_price_drift"] == {
        "old": 100.0,
        "new": 70.0,
        "change_pct": -30.0
    }

    # 3. Check normal drift (5%) did NOT flag alert
    assert "last_price_drift" not in pikachu.api_metadata
    assert pikachu.market_price == 21.00

    session.close()


def test_auto_adjust_sell_price_with_margin(seed_inventory, test_db):
    """Verifies auto_adjust_sell_price updates sell_price = round(new_market * multiplier, 2)."""
    _, session_factory = test_db
    service = MarketRefresherService()

    mock_provider = MagicMock()
    # Return new market = $30.00 for Sol Ring (was 10.00)
    mock_provider.fetch_market_prices.return_value = {"market": 30.00}

    with patch("services.market_refresher.get_provider", return_value=mock_provider):
        job = PriceRefreshJob(
            job_id="test-auto-adjust",
            game="mtg",
            in_stock_only=True,
            max_age_days=0,
            auto_adjust_sell_price=True,
            margin_multiplier=1.10  # 10% premium
        )
        service._run_refresh(
            job=job,
            in_stock_only=True,
            max_age_days=0,
            auto_adjust_sell_price=True,
            margin_multiplier=1.10,
            session_factory=session_factory
        )

    session = session_factory()
    sol_ring = session.query(SinglesInventory).filter_by(provider_card_id="mtg-sol-ring").first()

    assert sol_ring.market_price == 30.00
    # sell_price updated to round(30.00 * 1.10, 2) = 33.00
    assert sol_ring.sell_price == 33.00
    session.close()


def test_cancellation_flow(seed_inventory, test_db):
    """Verifies cooperative cancellation halts worker thread cleanly."""
    _, session_factory = test_db
    service = MarketRefresherService()

    # Signal cancellation immediately on first lookup
    def mock_fetch_with_cancel(_):
        service.cancel_refresh()
        return {"market": 20.00}

    mock_provider = MagicMock()
    mock_provider.fetch_market_prices.side_effect = mock_fetch_with_cancel

    with patch("services.market_refresher.get_provider", return_value=mock_provider):
        job = PriceRefreshJob(job_id="test-cancel-job", in_stock_only=False, max_age_days=0)
        service.job = job
        service.job.status = "running"
        service._run_refresh(
            job=job,
            in_stock_only=False,
            max_age_days=0,
            auto_adjust_sell_price=False,
            margin_multiplier=1.0,
            session_factory=session_factory
        )

        assert job.status == "cancelled"


# --- Flask REST Endpoint Tests ---

def test_get_pricing_view(test_app):
    """Verifies GET /tcg/pricing returns 200 and adheres to zero dead-end navigation."""
    _, client, _ = test_app
    resp = client.get("/tcg/pricing")
    assert resp.status_code == 200

    html = resp.get_data(as_text=True)
    assert 'href="/"' in html, "Dashboard navigation link missing!"
    assert "Return to Dashboard" in html
    assert "TCG POS - Market Pricing Engine" in html
    assert "btn-start-sync" in html
    assert "alerts-table" in html


def test_api_pricing_status_and_trigger(test_app, seed_inventory):
    """Verifies GET /tcg/api/pricing/status and POST /tcg/api/pricing/refresh."""
    _, client, session_factory = test_app

    # 1. Check initial status
    resp = client.get("/tcg/api/pricing/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "status" in data

    # 2. Trigger refresh with mocked worker
    with patch("services.market_refresher.MarketRefresherService.start_refresh", return_value="job-abc-123"):
        post_resp = client.post("/tcg/api/pricing/refresh", json={
            "game": "mtg",
            "in_stock_only": True,
            "max_age_days": 1,
            "auto_adjust_sell_price": False,
            "margin_multiplier": 1.0
        })
        assert post_resp.status_code == 200
        post_data = post_resp.get_json()
        assert post_data["success"] is True
        assert post_data["job_id"] == "job-abc-123"

    # 3. Test cancel endpoint
    with patch("services.market_refresher.MarketRefresherService.cancel_refresh", return_value=True):
        cancel_resp = client.post("/tcg/api/pricing/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.get_json()["success"] is True


def test_api_pricing_alerts_and_actions(test_app, seed_inventory):
    """Verifies GET /tcg/api/pricing/alerts and POST apply / dismiss actions."""
    _, client, session_factory = test_app
    session = session_factory()

    # Seed an alert on Sol Ring
    sol_ring = session.query(SinglesInventory).filter_by(provider_card_id="mtg-sol-ring").first()
    sol_ring.market_price = 35.00
    sol_ring.api_metadata = {
        "last_price_drift": {
            "old": 10.0,
            "new": 35.0,
            "change_pct": 250.0
        },
        "price_alert": True
    }
    session.commit()
    item_id = sol_ring.id
    session.close()

    # 1. Fetch alerts list
    resp = client.get("/tcg/api/pricing/alerts")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["count"] >= 1

    alert = next(a for a in data["alerts"] if a["id"] == item_id)
    assert alert["name"] == "Sol Ring"
    assert alert["drift"]["change_pct"] == 250.0

    # 2. Test Apply Action
    apply_resp = client.post(f"/tcg/api/pricing/alerts/{item_id}/apply", json={"multiplier": 1.0})
    assert apply_resp.status_code == 200
    apply_data = apply_resp.get_json()
    assert apply_data["success"] is True
    assert apply_data["new_sell_price"] == 35.00

    # Verify in DB: sell_price updated and last_price_drift cleared
    session = session_factory()
    updated_sol_ring = session.get(SinglesInventory, item_id)
    assert updated_sol_ring.sell_price == 35.00
    assert "last_price_drift" not in updated_sol_ring.api_metadata
    session.close()

    # 3. Re-flag Charizard and test Dismiss Action
    session = session_factory()
    charizard = session.query(SinglesInventory).filter_by(provider_card_id="base1-4").first()
    charizard.api_metadata = {
        "last_price_drift": {"old": 100.0, "new": 70.0, "change_pct": -30.0},
        "price_alert": True
    }
    session.commit()
    char_id = charizard.id
    session.close()

    dismiss_resp = client.post(f"/tcg/api/pricing/alerts/{char_id}/dismiss")
    assert dismiss_resp.status_code == 200
    assert dismiss_resp.get_json()["success"] is True

    session = session_factory()
    updated_char = session.get(SinglesInventory, char_id)
    # sell_price remained untouched at 100.00
    assert updated_char.sell_price == 100.00
    assert "last_price_drift" not in updated_char.api_metadata
    session.close()


def test_database_concurrency_background_worker_and_register_checkout(test_app, seed_inventory):
    """
    CRITICAL CONCURRENCY TEST:
    Verifies that background thread writing to database uses short-lived scoped sessions,
    allowing the main Flask register thread to execute checkout transactions concurrently
    without encountering database locks or operational errors.
    """
    import threading
    app, client, session_factory = test_app
    service = MarketRefresherService()

    worker_started_event = threading.Event()

    def slow_fetch(card_id):
        worker_started_event.set()
        time.sleep(0.05)  # 50ms simulated network wait
        return {"market": 15.00}

    mock_provider = MagicMock()
    mock_provider.fetch_market_prices.side_effect = slow_fetch

    with patch("services.market_refresher.get_provider", return_value=mock_provider):
        # 1. Start real background thread
        job_id = service.start_refresh(
            game="mtg",
            in_stock_only=True,
            max_age_days=0,
            session_factory=session_factory
        )

        # Wait until background worker has begun executing network calls
        assert worker_started_event.wait(timeout=2.0), "Background worker failed to start!"

        # 2. Concurrently execute register checkout decrement on main Flask thread
        # Decrement Sol Ring (id=1, starts with quantity=4)
        checkout_payload = [
            {"id": 1, "quantity": 1}
        ]
        checkout_resp = client.post("/tcg/api/register/decrement", json=checkout_payload)

        # Main register thread must succeed with HTTP 200 without database locks
        assert checkout_resp.status_code == 200, (
            f"Register checkout failed during background sync: {checkout_resp.get_data(as_text=True)}"
        )
        checkout_data = checkout_resp.get_json()
        assert checkout_data["success"] is True

        # 3. Wait for background worker to complete
        timeout = time.time() + 5.0
        while time.time() < timeout and service.is_running():
            time.sleep(0.05)

        assert not service.is_running(), "Background worker timed out!"
        assert service.job.status == "completed"

    # Verify inventory was decremented to 3 by register, and market_price was updated by worker
    session = session_factory()
    sol_ring = session.get(SinglesInventory, 1)
    assert sol_ring.quantity == 3, f"Expected quantity 3 after checkout, got {sol_ring.quantity}"
    assert sol_ring.market_price == 15.00, f"Expected updated market price 15.00, got {sol_ring.market_price}"

    # Assert Pokémon cards remain untouched
    pokemon_cards = session.query(SinglesInventory).filter_by(game="pokemon").all()
    assert len(pokemon_cards) == 2
    for p_card in pokemon_cards:
        if p_card.provider_card_id == "swsh3-136":
            assert p_card.market_price == 20.00, "Pikachu market_price was modified during MTG sync!"
            assert p_card.sell_price == 20.00
            assert "last_price_drift" not in (p_card.api_metadata or {})
        elif p_card.provider_card_id == "base1-4":
            assert p_card.market_price == 100.00, "Charizard market_price was modified during MTG sync!"
            assert p_card.sell_price == 100.00
            assert "last_price_drift" not in (p_card.api_metadata or {})

    session.close()

