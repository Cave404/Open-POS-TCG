"""
OpenPOS-TCG Addon
File: tests/test_pokemon_provider.py
Addon ID: tcg_pos

Unit and Integration Test Suite for PokemonTCGdexProvider:
1. Mocks TCGdex REST API responses for sample Pokémon cards (e.g., Pikachu swsh3-136).
2. Verifies _normalize converts raw payload into a valid NormalizedCard (game='pokemon').
3. Verifies finishes mapping (nonfoil, reverse_holo, holo, first_edition) and api_metadata.
4. Verifies atomic image caching logic under data/cache/tcg_art/pokemon/.
5. Verifies Flask search endpoint GET /tcg/api/search?q=Pikachu&game=pokemon.
6. Verifies Flask lookup endpoint GET /tcg/api/lookup/<set_code>/<collector_number>?game=pokemon.
"""

from io import BytesIO
from pathlib import Path
import sys
from typing import Any, Dict
from unittest.mock import MagicMock, patch
import pytest

# Ensure project root is available on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask
from sqlalchemy import create_engine
from models import Base
from plugin import addon_bp
from providers.base import NormalizedCard
from providers.pokemon_tcgdex import PokemonTCGdexProvider
import routes.intake as intake_routes


# --- Mock Payloads ---

SAMPLE_PIKACHU_DATA: Dict[str, Any] = {
    "id": "swsh3-136",
    "localId": "136",
    "name": "Pikachu",
    "image": "https://assets.tcgdex.net/en/swsh/swsh3/136",
    "category": "Pokemon",
    "illustrator": "Ryuta Fuse",
    "rarity": "Common",
    "set": {
        "id": "swsh3",
        "name": "Darkness Ablaze",
        "cardCount": {
            "official": 189,
            "total": 201
        }
    },
    "variants": {
        "firstEdition": False,
        "holo": False,
        "normal": True,
        "reverse": True,
        "wPromo": False
    },
    "hp": 60,
    "types": ["Lightning"],
    "stage": "Basic",
    "attacks": [
        {
            "cost": ["Lightning"],
            "name": "Charge",
            "effect": "Search your deck for a Lightning Energy card and attach it to this Pokémon."
        },
        {
            "cost": ["Lightning", "Colorless"],
            "name": "Bite",
            "damage": 20
        }
    ],
    "weaknesses": [
        {
            "type": "Fighting",
            "value": "×2"
        }
    ],
    "retreat": 1,
    "legal": {
        "standard": False,
        "expanded": True
    },
    "cardmarket": {
        "prices": {
            "averageSellPrice": 0.45,
            "lowPrice": 0.05,
            "trendPrice": 0.38,
            "reverseHoloAvg": 1.10
        }
    },
    "tcgplayer": {
        "prices": {
            "normal": {
                "low": 0.08,
                "mid": 0.35,
                "market": 0.28
            },
            "reverseHolofoil": {
                "low": 0.45,
                "mid": 1.25,
                "market": 1.12
            }
        }
    }
}

SAMPLE_CHARIZARD_DATA: Dict[str, Any] = {
    "id": "base1-4",
    "localId": "4",
    "name": "Charizard",
    "image": "https://assets.tcgdex.net/en/base/base1/4",
    "category": "Pokemon",
    "illustrator": "Mitsuhiro Arita",
    "rarity": "Rare Holo",
    "set": {
        "id": "base1",
        "name": "Base Set"
    },
    "variants": {
        "firstEdition": True,
        "holo": True,
        "normal": False,
        "reverse": False
    },
    "hp": 120,
    "types": ["Fire"],
    "stage": "Stage 2",
    "evolveFrom": "Charmeleon",
    "retreat": 3,
    "attacks": [
        {"name": "Fire Spin", "damage": 100, "cost": ["Fire", "Fire", "Fire", "Fire"]}
    ],
    "cardmarket": {
        "prices": {
            "averageSellPrice": 350.00,
            "lowPrice": 120.00,
            "trendPrice": 320.00
        }
    }
}


@pytest.fixture
def temp_provider(tmp_path):
    """Creates an isolated PokemonTCGdexProvider instance using a temporary data directory."""
    provider = PokemonTCGdexProvider(custom_data_dir=tmp_path)
    return provider


@pytest.fixture
def test_client():
    """Configures an isolated test Flask app with the tcg_pos blueprint."""
    app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))
    app.config["TESTING"] = True
    app.secret_key = "test-secret"

    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    app.config["DB_ENGINE"] = engine

    app.register_blueprint(addon_bp)
    client = app.test_client()
    return client


# --- Provider Unit Tests ---

def test_pokemon_provider_initialization(temp_provider, tmp_path):
    """Verifies default parameters and paths for the Pokémon provider."""
    assert temp_provider.game_slug == "pokemon"
    assert temp_provider.base_url == "https://api.tcgdex.net/v2/en"
    assert temp_provider.rate_limit_delay == 0.1
    expected_cache_dir = tmp_path / "cache" / "tcg_art" / "pokemon"
    assert temp_provider.cache_dir == expected_cache_dir
    assert expected_cache_dir.exists()


def test_normalize_pikachu(temp_provider):
    """Verifies _normalize transforms raw TCGdex payload into NormalizedCard."""
    card = temp_provider._normalize(SAMPLE_PIKACHU_DATA)

    assert isinstance(card, NormalizedCard)
    assert card.game == "pokemon"
    assert card.provider_card_id == "swsh3-136"
    assert card.name == "Pikachu"
    assert card.clean_name == "pikachu"
    assert card.set_code == "swsh3"
    assert card.set_name == "Darkness Ablaze"
    assert card.collector_number == "136"
    assert card.rarity == "common"
    assert card.image_uri == "https://assets.tcgdex.net/en/swsh/swsh3/136/high.webp"

    # Price verification (Cardmarket priority)
    assert card.market_price == 0.45
    assert card.low_price == 0.05
    assert card.foil_price == 1.10  # reverseHoloAvg mapped to foil_price

    # Gameplay metadata verification
    meta = card.api_metadata
    assert meta["hp"] == 60
    assert meta["stage"] == "Basic"
    assert meta["types"] == ["Lightning"]
    assert meta["retreat"] == 1
    assert len(meta["attacks"]) == 2
    assert meta["attacks"][0]["name"] == "Charge"
    assert meta["weaknesses"][0]["type"] == "Fighting"
    assert "reverse_holo" in meta["finishes"]
    assert "nonfoil" in meta["finishes"]


def test_normalize_charizard_with_first_edition(temp_provider):
    """Verifies _normalize handles Stage 2, Holo, and 1st Edition finishes."""
    card = temp_provider._normalize(SAMPLE_CHARIZARD_DATA)

    assert card.game == "pokemon"
    assert card.provider_card_id == "base1-4"
    assert card.name == "Charizard"
    assert card.set_code == "base1"
    assert card.collector_number == "4"
    assert card.market_price == 350.00
    assert card.low_price == 120.00

    meta = card.api_metadata
    assert meta["hp"] == 120
    assert meta["stage"] == "Stage 2"
    assert meta["evolveFrom"] == "Charmeleon"
    assert "holo" in meta["finishes"]
    assert "first_edition" in meta["finishes"]


def test_extract_finishes(temp_provider):
    """Verifies variant extraction across various combination flags."""
    # Regular + Reverse
    f1 = temp_provider._extract_finishes({"normal": True, "reverse": True, "holo": False})
    assert "nonfoil" in f1
    assert "reverse_holo" in f1
    assert "holo" not in f1

    # Holo + 1st Edition
    f2 = temp_provider._extract_finishes({"normal": False, "holo": True, "firstEdition": True})
    assert "holo" in f2
    assert "first_edition" in f2
    assert "nonfoil" not in f2

    # None provided fallback
    f3 = temp_provider._extract_finishes(None)
    assert f3 == ["nonfoil"]


def test_search_cards_mocked(temp_provider):
    """Verifies search_cards issues correct API call and returns normalized list."""
    with patch.object(temp_provider, "_get", return_value=[SAMPLE_PIKACHU_DATA, SAMPLE_CHARIZARD_DATA]) as mock_get:
        results = temp_provider.search_cards("Pikachu")
        mock_get.assert_called_once_with("cards", params={"name": "Pikachu"})
        assert len(results) == 2
        assert results[0].name == "Pikachu"
        assert results[0].game == "pokemon"
        assert results[1].name == "Charizard"


def test_get_card_by_id_mocked(temp_provider):
    """Verifies direct lookup by TCGdex ID."""
    with patch.object(temp_provider, "_get", return_value=SAMPLE_PIKACHU_DATA) as mock_get:
        card = temp_provider.get_card_by_id("swsh3-136")
        mock_get.assert_called_once_with("cards/swsh3-136")
        assert card is not None
        assert card.provider_card_id == "swsh3-136"
        assert card.name == "Pikachu"


def test_get_card_by_collector_number_mocked(temp_provider):
    """Verifies collector number lookup formatting and fallback."""
    with patch.object(temp_provider, "get_card_by_id", return_value=temp_provider._normalize(SAMPLE_PIKACHU_DATA)) as mock_id:
        card = temp_provider.get_card_by_collector_number("swsh3", "136")
        mock_id.assert_called_with("swsh3-136", download_image=False)
        assert card is not None
        assert card.collector_number == "136"


def test_fetch_market_prices(temp_provider):
    """Verifies market price dictionary extraction."""
    with patch.object(temp_provider, "get_card_by_id", return_value=temp_provider._normalize(SAMPLE_PIKACHU_DATA)):
        prices = temp_provider.fetch_market_prices("swsh3-136")
        assert prices["market"] == 0.45
        assert prices["low"] == 0.05
        assert prices["foil"] == 1.10


def test_atomic_image_caching(temp_provider, tmp_path):
    """Verifies that card image downloads execute atomically into data/cache/tcg_art/pokemon/."""
    fake_image_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIFfakeimagecontent"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_content = MagicMock(return_value=[fake_image_bytes])

    with patch.object(temp_provider.session, "get", return_value=mock_resp):
        cached_path = temp_provider.cache_card_image(
            image_url="https://assets.tcgdex.net/en/swsh/swsh3/136/high.webp",
            provider_card_id="swsh3-136"
        )

        assert cached_path == "data/cache/tcg_art/pokemon/swsh3-136.jpg"

        expected_dest = tmp_path / "cache" / "tcg_art" / "pokemon" / "swsh3-136.jpg"
        assert expected_dest.exists(), "Target image file was not created!"
        assert expected_dest.read_bytes() == fake_image_bytes

        # No residual .tmp files
        tmp_files = list((tmp_path / "cache" / "tcg_art" / "pokemon").glob("*.tmp"))
        assert len(tmp_files) == 0, f"Found orphan temp files: {tmp_files}"


# --- Flask Endpoint Integration Tests ---

def test_api_search_endpoint_pokemon(test_client):
    """Verifies GET /tcg/api/search?q=Pikachu&game=pokemon queries PokemonTCGdexProvider."""
    normalized_card = PokemonTCGdexProvider()._normalize(SAMPLE_PIKACHU_DATA)

    mock_pokemon_provider = MagicMock(spec=PokemonTCGdexProvider)
    mock_pokemon_provider.search_cards.return_value = [normalized_card]

    with patch("routes.intake.get_pokemon_provider", return_value=mock_pokemon_provider):
        resp = test_client.get("/tcg/api/search?q=Pikachu&game=pokemon")
        assert resp.status_code == 200

        data = resp.get_json()
        assert data["success"] is True
        assert len(data["results"]) == 1

        result_card = data["results"][0]
        assert result_card["name"] == "Pikachu"
        assert result_card["game"] == "pokemon"
        assert result_card["set_code"] == "swsh3"
        assert result_card["collector_number"] == "136"
        assert result_card["api_metadata"]["hp"] == 60
        assert result_card["api_metadata"]["stage"] == "Basic"
        assert result_card["api_metadata"]["types"] == ["Lightning"]


def test_api_lookup_endpoint_pokemon(test_client):
    """Verifies GET /tcg/api/lookup/swsh3/136?game=pokemon resolves Pokémon cards."""
    normalized_card = PokemonTCGdexProvider()._normalize(SAMPLE_PIKACHU_DATA)

    mock_pokemon_provider = MagicMock(spec=PokemonTCGdexProvider)
    mock_pokemon_provider.get_card_by_collector_number.return_value = normalized_card

    with patch("routes.intake.get_pokemon_provider", return_value=mock_pokemon_provider):
        resp = test_client.get("/tcg/api/lookup/swsh3/136?game=pokemon")
        assert resp.status_code == 200

        data = resp.get_json()
        assert data["success"] is True
        assert data["card"]["name"] == "Pikachu"
        assert data["card"]["game"] == "pokemon"
        assert data["card"]["collector_number"] == "136"
