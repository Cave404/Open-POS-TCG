"""
OpenPOS-TCG Addon
File: routes/intake.py
Addon ID: tcg_pos

Intake & Buylist Trade-In Endpoints.
Exposes REST APIs for card searching, collector number lookup, buylist offer calculations,
and atomic inventory intake commits with weighted-average cost basis accounting.
"""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional
from flask import Blueprint, current_app, jsonify, render_template, request
from sqlalchemy.orm import Session

from models import SinglesInventory, get_db_session
from providers.mtg_scryfall import ScryfallProvider
from providers.pokemon_tcgdex import PokemonTCGdexProvider
from services.buylist import BuylistCalculator

try:
    from plugin import addon_bp
except ImportError:
    from ..plugin import addon_bp

# Cached provider and calculator instances
_scryfall_provider: Optional[ScryfallProvider] = None
_pokemon_provider: Optional[PokemonTCGdexProvider] = None
_buylist_calculator: Optional[BuylistCalculator] = None


def get_scryfall_provider() -> ScryfallProvider:
    """Lazy-initializes and returns the shared ScryfallProvider instance."""
    global _scryfall_provider
    if _scryfall_provider is None:
        _scryfall_provider = ScryfallProvider()
    return _scryfall_provider


def get_pokemon_provider() -> PokemonTCGdexProvider:
    """Lazy-initializes and returns the shared PokemonTCGdexProvider instance."""
    global _pokemon_provider
    if _pokemon_provider is None:
        _pokemon_provider = PokemonTCGdexProvider()
    return _pokemon_provider


def get_provider_for_game(game: str):
    """Factory returning the registered card catalog provider for a target collectible game."""
    norm_game = (game or "mtg").strip().lower()
    if norm_game == "pokemon":
        return get_pokemon_provider()
    return get_scryfall_provider()


def get_buylist_calculator() -> BuylistCalculator:
    """Initializes and returns a dynamic BuylistCalculator reflecting active store settings."""
    return BuylistCalculator()


def _resolve_session() -> Session:
    """
    Safely resolves a database session from current Flask app context,
    or falls back to the default database session factory.
    """
    if hasattr(current_app, "db_session") and current_app.db_session is not None:
        return current_app.db_session
    if current_app.config.get("DB_SESSION"):
        return current_app.config["DB_SESSION"]
    if current_app.config.get("DB_SESSION_FACTORY"):
        return current_app.config["DB_SESSION_FACTORY"]()
    if current_app.config.get("DB_ENGINE"):
        return get_db_session(engine=current_app.config["DB_ENGINE"])
    return get_db_session()


@addon_bp.route("/intake", methods=["GET"])
def render_intake_view():
    """
    Renders the primary TCG POS intake and buylist workstation UI.
    """
    return render_template("tcg_pos/intake.html")


@addon_bp.route("/api/search", methods=["GET"])
def api_search_cards():
    """
    Card Search API.
    Query parameters:
      q: Search query text or collector syntax (e.g. 'Black Lotus' or 'neo 242').
      game: Target game identifier (default: 'mtg').
      page: Upstream catalog page number (default: 1).
      download_images: Boolean whether to cache image files locally (default: false).
    """
    query = request.args.get("q", "").strip()
    game = request.args.get("game", "mtg").strip().lower()
    page = int(request.args.get("page", 1))
    download_images = request.args.get("download_images", "false").lower() in ("true", "1", "yes")

    if not query:
        return jsonify({"success": True, "results": [], "query": ""})

    if game not in ("mtg", "pokemon"):
        return jsonify({"success": False, "error": f"Unsupported game: '{game}'"}), 400

    provider = get_provider_for_game(game)

    # Check for direct set code + collector number scanner syntax: e.g. "neo 242" or "swsh3 136"
    scanner_match = re.match(r"^([a-zA-Z0-9]{2,8})[\s/#\-]+([a-zA-Z0-9\-★]+)$", query)
    if scanner_match:
        set_code, collector_num = scanner_match.group(1).lower(), scanner_match.group(2)
        card = provider.get_card_by_collector_number(
            set_code=set_code,
            collector_number=collector_num,
            download_image=download_images
        )
        if card:
            return jsonify({
                "success": True,
                "results": [card.to_dict()],
                "matched_scanner_syntax": True,
                "query": query
            })

    # Catalog search
    cards = provider.search_cards(query=query, page=page, download_images=download_images)
    return jsonify({
        "success": True,
        "results": [card.to_dict() for card in cards],
        "matched_scanner_syntax": False,
        "query": query
    })


@addon_bp.route("/api/lookup/<set_code>/<collector_number>", methods=["GET"])
def api_lookup_card(set_code: str, collector_number: str):
    """
    Direct collector number card lookup.
    """
    game = request.args.get("game", "mtg").strip().lower()
    lang = request.args.get("lang", "en").strip().lower()
    download_image = request.args.get("download_image", "false").lower() in ("true", "1", "yes")

    if game not in ("mtg", "pokemon"):
        return jsonify({"success": False, "error": f"Unsupported game: '{game}'"}), 400

    provider = get_provider_for_game(game)
    card = provider.get_card_by_collector_number(
        set_code=set_code.lower().strip(),
        collector_number=collector_number.strip(),
        lang=lang,
        download_image=download_image
    )
    if card:
        return jsonify({"success": True, "card": card.to_dict()})
    return jsonify({
        "success": False,
        "error": f"Card not found for set '{set_code}' #{collector_number}"
    }), 404


@addon_bp.route("/api/buylist/calculate", methods=["POST"])
def api_calculate_buylist():
    """
    Buylist Price Evaluation API.
    Computes real-time cash and store credit offers for given card condition and finish.

    Payload format:
    {
      "market_price": 10.00,
      "condition": "NM",      // NM, LP, MP, HP, DMG
      "finish": "nonfoil",    // nonfoil, foil, etched
      "custom_margin": 0.50,  // optional custom cash payout percentage
      "quantity": 1           // optional quantity lot size
    }
    """
    payload = request.get_json(silent=True) or {}
    market_price = payload.get("market_price")

    if market_price is None:
        return jsonify({"success": False, "error": "market_price is required"}), 400

    try:
        price_val = float(market_price)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "market_price must be a numeric value"}), 400

    condition = str(payload.get("condition", "NM")).strip().upper()
    finish = str(payload.get("finish", "nonfoil")).strip().lower()
    quantity = max(1, int(payload.get("quantity", 1)))
    custom_margin = payload.get("custom_margin")

    cash_override = float(custom_margin) if custom_margin is not None else None
    calculator = get_buylist_calculator()

    offer = calculator.calculate_offer_from_price(
        market_price=price_val,
        condition=condition,
        finish=finish,
        quantity=quantity,
        card_name=payload.get("card_name", "Card Offer"),
        game=payload.get("game", "mtg"),
        cash_percentage=cash_override
    )

    return jsonify({
        "success": True,
        "cash_offer": offer.unit_cash_offer,
        "credit_offer": offer.unit_credit_offer,
        "total_cash_offer": offer.total_cash_offer,
        "total_credit_offer": offer.total_credit_offer,
        "offer": offer.to_dict()
    })


@addon_bp.route("/api/intake/commit", methods=["POST"])
def api_intake_commit():
    """
    Batch Inventory Intake Commit API.
    Atomically inserts or updates card records in singles_inventory with weighted-average
    cost basis calculation:

      New Cost Basis = ((Current Qty * Current Cost) + (Incoming Qty * Incoming Cost))
                       -------------------------------------------------------------
                                        (Current Qty + Incoming Qty)

    Expected Payload: List of items or JSON object with "items" key:
    [
      {
        "game": "mtg",
        "provider_card_id": "scry-uuid-123",
        "name": "Card Name",
        "clean_name": "card name",
        "set_code": "lea",
        "set_name": "Limited Edition Alpha",
        "collector_number": "1",
        "rarity": "rare",
        "finish": "nonfoil",
        "condition": "NM",
        "quantity": 1,
        "cost_basis": 5.00,
        "sell_price": 10.00,
        "market_price": 10.00,
        "low_price": 8.00,
        "foil_price": null,
        "etched_price": null,
        "image_path": "data/cache/tcg_art/mtg/...",
        "image_uri": "https://...",
        "api_metadata": {}
      }
    ]
    """
    raw_data = request.get_json(silent=True)
    if raw_data is None:
        return jsonify({"success": False, "error": "Missing JSON request body"}), 400

    payout_type = None
    customer_id = None
    batch_number = None
    total_payout = None

    if isinstance(raw_data, list):
        items = raw_data
    elif isinstance(raw_data, dict):
        items = raw_data.get("items") or raw_data.get("cards") or [raw_data]
        payout_type = raw_data.get("payout_type")
        customer_id = raw_data.get("customer_id")
        batch_number = raw_data.get("batch_number")
        total_payout = raw_data.get("total_payout")
    else:
        return jsonify({"success": False, "error": "Invalid payload format, expected array"}), 400

    if not items:
        return jsonify({"success": False, "error": "Empty intake items list"}), 400

    if payout_type == "store_credit" and not customer_id:
        return jsonify({"success": False, "error": "customer_id is required when payout_type is 'store_credit'."}), 400

    session = _resolve_session()
    # Check if this session was created locally and needs closing
    is_external_session = hasattr(current_app, "db_session") and current_app.db_session is session

    committed_count = 0
    updated_records = []


    try:
        for raw_item in items:
            provider_card_id = str(raw_item.get("provider_card_id", "")).strip()
            name = str(raw_item.get("name", "")).strip()
            set_code = str(raw_item.get("set_code", "")).strip().lower()
            collector_number = str(raw_item.get("collector_number", "")).strip()

            if not provider_card_id or not name:
                continue

            game = str(raw_item.get("game", "mtg")).strip().lower()
            clean_name = str(raw_item.get("clean_name") or re.sub(r"[^\w\s]", "", name)).strip().lower()
            set_name = str(raw_item.get("set_name", "")).strip()
            rarity = str(raw_item.get("rarity", "common")).strip().lower()
            finish = str(raw_item.get("finish", "nonfoil")).strip().lower()
            condition = str(raw_item.get("condition", "NM")).strip().upper()

            incoming_qty = max(0, int(raw_item.get("quantity", 1)))
            incoming_cost = max(0.0, float(raw_item.get("cost_basis", 0.0)))
            sell_price = max(0.0, float(raw_item.get("sell_price", 0.0)))

            market_price = float(raw_item["market_price"]) if raw_item.get("market_price") is not None else None
            low_price = float(raw_item["low_price"]) if raw_item.get("low_price") is not None else None
            foil_price = float(raw_item["foil_price"]) if raw_item.get("foil_price") is not None else None
            etched_price = float(raw_item["etched_price"]) if raw_item.get("etched_price") is not None else None

            image_path = raw_item.get("image_path")
            image_uri = raw_item.get("image_uri")
            api_metadata = raw_item.get("api_metadata") if isinstance(raw_item.get("api_metadata"), dict) else {}
            custom_tag_id = str(raw_item.get("custom_tag_id", "")).strip() or None

            # Query existing row by composite unique key: (game, provider_card_id, finish, condition)
            existing = session.query(SinglesInventory).filter_by(
                game=game,
                provider_card_id=provider_card_id,
                finish=finish,
                condition=condition
            ).first()

            if existing is not None:
                # Existing item: increment quantity and apply rolling weighted-average cost basis
                current_qty = existing.quantity
                current_cost = existing.cost_basis
                total_qty = current_qty + incoming_qty

                if total_qty > 0:
                    if current_qty <= 0:
                        new_cost_basis = incoming_cost
                    else:
                        new_cost_basis = (
                            (current_qty * current_cost) + (incoming_qty * incoming_cost)
                        ) / total_qty
                else:
                    new_cost_basis = incoming_cost

                existing.quantity = total_qty
                existing.cost_basis = round(new_cost_basis, 4)

                # Update shelf sell price if provided or not previously set
                if sell_price > 0.0 or existing.sell_price == 0.0:
                    existing.sell_price = sell_price

                # Refresh market benchmark tracking
                if market_price is not None:
                    existing.market_price = market_price
                if low_price is not None:
                    existing.low_price = low_price
                if foil_price is not None:
                    existing.foil_price = foil_price
                if etched_price is not None:
                    existing.etched_price = etched_price

                # Asset fallback updates
                if image_path:
                    existing.image_path = image_path
                if image_uri and not existing.image_uri:
                    existing.image_uri = image_uri
                if api_metadata:
                    existing.api_metadata = api_metadata
                if custom_tag_id:
                    existing.custom_tag_id = custom_tag_id

                existing.updated_at = datetime.now(timezone.utc)
                updated_records.append(existing.to_dict())
            else:
                # New inventory SKU row
                new_item = SinglesInventory(
                    game=game,
                    provider_card_id=provider_card_id,
                    name=name,
                    clean_name=clean_name,
                    set_code=set_code,
                    set_name=set_name,
                    collector_number=collector_number,
                    rarity=rarity,
                    finish=finish,
                    condition=condition,
                    quantity=incoming_qty,
                    cost_basis=round(incoming_cost, 4),
                    sell_price=sell_price,
                    market_price=market_price,
                    low_price=low_price,
                    foil_price=foil_price,
                    etched_price=etched_price,
                    image_path=image_path,
                    image_uri=image_uri,
                    api_metadata=api_metadata,
                    custom_tag_id=custom_tag_id,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )
                session.add(new_item)
                updated_records.append(new_item.to_dict())

            committed_count += 1

        session.commit()

        customer_credit_balance = None
        if payout_type == "store_credit":
            from services.buylist_settlement import BuylistSettlementService
            from services.core_customer_client import CoreCustomerClient
            client = CoreCustomerClient(base_url=current_app.config.get("CORE_BASE_URL", "http://127.0.0.1:5000"))
            calc_payout = float(total_payout) if total_payout is not None else sum(
                float(i.get("cost_basis", 0.0)) * int(i.get("quantity", 1)) for i in items
            )
            settlement = BuylistSettlementService.settle_intake_payout(
                payout_type="store_credit",
                total_payout=calc_payout,
                customer_id=int(customer_id),
                batch_number=batch_number,
                core_client=client
            )
            customer_credit_balance = settlement.get("customer_credit_balance")

        resp_payload = {
            "success": True,
            "updated_count": committed_count,
            "message": f"Successfully processed and committed {committed_count} singles inventory items."
        }
        if customer_credit_balance is not None:
            resp_payload["customer_credit_balance"] = customer_credit_balance

        return jsonify(resp_payload)


    except Exception as e:
        session.rollback()
        return jsonify({
            "success": False,
            "error": f"Database commit failed: {str(e)}"
        }), 500

    finally:
        if not is_external_session:
            session.close()
