"""
OpenPOS-TCG Addon
File: routes/database.py
Addon ID: tcg_pos

Dedicated Card Database Explorer Endpoints.
Tailored for day-to-day card shop operations: multi-field catalog browsing,
stock filtering, inline price and quantity updates, bulk pricing adjustments,
batch tag wiping, and hardware sleeve label integration.
"""

from datetime import datetime, timezone
import logging
import math
import re
from typing import Any, Dict, List, Optional
from flask import current_app, jsonify, render_template, request
from sqlalchemy import func
from sqlalchemy.orm import Session

from models import SinglesInventory, get_db_session

try:
    from plugin import addon_bp
except ImportError:
    from ..plugin import addon_bp

logger = logging.getLogger(__name__)


def _resolve_session() -> Session:
    """Safely resolves database session from active Flask app or global session factory."""
    if hasattr(current_app, "db_session") and current_app.db_session is not None:
        return current_app.db_session
    if current_app.config.get("DB_SESSION"):
        return current_app.config["DB_SESSION"]
    if current_app.config.get("DB_SESSION_FACTORY"):
        return current_app.config["DB_SESSION_FACTORY"]()
    if current_app.config.get("DB_ENGINE"):
        return get_db_session(engine=current_app.config["DB_ENGINE"])
    return get_db_session()


@addon_bp.route("/database", methods=["GET"])
def render_database_view():
    """
    Renders the dedicated Card Database Explorer workstation view.
    """
    return render_template("tcg_pos/database.html")


@addon_bp.route("/api/database/query", methods=["GET"])
def api_database_query():
    """
    Card catalog search and filtering endpoint.

    Supported Query Parameters:
      page: Page index (default: 1)
      limit: Records per page (default: 50, max: 200)
      q / search: Search keyword (name, SKU, custom tag UID, collector number)
      game: 'all', 'mtg', 'pokemon'
      set_code: Expansion code (e.g. 'mh2', 'swsh3')
      rarity: 'common', 'uncommon', 'rare', 'mythic', etc.
      finish: 'nonfoil', 'foil', 'etched', 'reverse_holo'
      condition: 'NM', 'LP', 'MP', 'HP', 'DMG'
      stock_status: 'all', 'in_stock', 'low_stock' (qty <= 2), 'out_of_stock' (qty == 0)
      sort: 'newest', 'oldest', 'name_asc', 'price_desc', 'price_asc', 'qty_desc', 'qty_asc'
    """
    page = max(1, int(request.args.get("page", 1)))
    limit = max(1, min(200, int(request.args.get("limit", 50))))

    search = (request.args.get("search") or request.args.get("q") or "").strip()
    game = request.args.get("game", "all").strip().lower()
    set_code = request.args.get("set_code", "").strip().lower()
    rarity = request.args.get("rarity", "").strip().lower()
    finish = request.args.get("finish", "").strip().lower()
    condition = request.args.get("condition", "").strip().upper()
    stock_status = request.args.get("stock_status", "all").strip().lower()
    sort_by = request.args.get("sort", "newest").strip().lower()

    session = _resolve_session()
    is_external_session = hasattr(current_app, "db_session") and current_app.db_session is session

    try:
        query = session.query(SinglesInventory)

        # Game filtration
        if game and game != "all":
            query = query.filter(SinglesInventory.game == game)

        # Set code filtration
        if set_code:
            query = query.filter(SinglesInventory.set_code == set_code)

        # Rarity filtration
        if rarity:
            query = query.filter(SinglesInventory.rarity.ilike(f"%{rarity}%"))

        # Finish filtration
        if finish:
            query = query.filter(SinglesInventory.finish == finish)

        # Condition filtration
        if condition:
            query = query.filter(SinglesInventory.condition == condition)

        # Stock status filtration
        if stock_status == "in_stock":
            query = query.filter(SinglesInventory.quantity > 0)
        elif stock_status == "low_stock":
            query = query.filter(SinglesInventory.quantity > 0, SinglesInventory.quantity <= 2)
        elif stock_status == "out_of_stock":
            query = query.filter(SinglesInventory.quantity == 0)

        # Keyword search
        if search:
            clean_kw = re.sub(r"[^\w\s]", "", search).strip().lower()
            pattern = f"%{search}%"
            clean_pattern = f"%{clean_kw}%"
            query = query.filter(
                (SinglesInventory.name.ilike(pattern)) |
                (SinglesInventory.clean_name.ilike(clean_pattern)) |
                (SinglesInventory.sku.ilike(pattern)) |
                (SinglesInventory.custom_tag_id.ilike(pattern)) |
                (SinglesInventory.collector_number == search)
            )

        # Metrics aggregation on filtered result set
        total_items = query.count()
        in_stock_count = query.filter(SinglesInventory.quantity > 0).count()
        val_expr = func.coalesce(func.sum(SinglesInventory.sell_price * SinglesInventory.quantity), 0.0)
        filtered_valuation = round(float(query.with_entities(val_expr).scalar() or 0.0), 2)

        # Sorting logic
        if sort_by == "oldest":
            query = query.order_by(SinglesInventory.updated_at.asc())
        elif sort_by == "name_asc":
            query = query.order_by(SinglesInventory.name.asc())
        elif sort_by == "price_desc":
            query = query.order_by(SinglesInventory.sell_price.desc())
        elif sort_by == "price_asc":
            query = query.order_by(SinglesInventory.sell_price.asc())
        elif sort_by == "qty_desc":
            query = query.order_by(SinglesInventory.quantity.desc())
        elif sort_by == "qty_asc":
            query = query.order_by(SinglesInventory.quantity.asc())
        else:  # newest
            query = query.order_by(SinglesInventory.updated_at.desc(), SinglesInventory.name.asc())

        items = query.offset((page - 1) * limit).limit(limit).all()
        total_pages = math.ceil(total_items / limit) if total_items > 0 else 1

        return jsonify({
            "success": True,
            "items": [item.to_dict() for item in items],
            "total": total_items,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "filtered_in_stock": in_stock_count,
            "filtered_valuation": filtered_valuation
        })

    finally:
        if not is_external_session:
            session.close()


@addon_bp.route("/api/database/<int:item_id>", methods=["PATCH"])
def api_database_inline_edit(item_id: int):
    """
    Inline editing endpoint for card stock parameters.
    Supports sell_price, quantity, cost_basis, custom_tag_id, and condition.
    """
    data = request.get_json(silent=True) or {}
    session = _resolve_session()
    is_external_session = hasattr(current_app, "db_session") and current_app.db_session is session

    try:
        item = session.query(SinglesInventory).filter_by(id=item_id).first()
        if not item:
            return jsonify({"success": False, "error": f"Card record #{item_id} not found."}), 404

        if "sell_price" in data:
            item.sell_price = max(0.0, round(float(data["sell_price"]), 2))

        if "quantity" in data:
            item.quantity = max(0, int(data["quantity"]))

        if "cost_basis" in data:
            item.cost_basis = max(0.0, round(float(data["cost_basis"]), 2))

        if "condition" in data:
            item.condition = str(data["condition"]).strip().upper()

        if "finish" in data:
            item.finish = str(data["finish"]).strip().lower()

        if "custom_tag_id" in data:
            new_tag = str(data["custom_tag_id"]).strip() if data["custom_tag_id"] else None
            if new_tag:
                conflict = session.query(SinglesInventory).filter(
                    SinglesInventory.custom_tag_id == new_tag,
                    SinglesInventory.id != item_id
                ).first()
                if conflict:
                    return jsonify({
                        "success": False,
                        "error": f"Tag UID '{new_tag}' is already bound to #{conflict.id} ({conflict.name})."
                    }), 400
            item.custom_tag_id = new_tag

        item.updated_at = datetime.now(timezone.utc)
        session.commit()

        return jsonify({
            "success": True,
            "item": item.to_dict(),
            "message": f"Updated card #{item.id} ({item.name})."
        })

    except Exception as exc:
        session.rollback()
        logger.exception("[DATABASE_ROUTE] Inline update failed for #%d: %s", item_id, exc)
        return jsonify({"success": False, "error": f"Update failed: {str(exc)}"}), 500
    finally:
        if not is_external_session:
            session.close()


@addon_bp.route("/api/database/bulk-adjust", methods=["POST"])
def api_database_bulk_adjust():
    """
    Executes batch operations across selected card IDs.

    Supported Actions:
      - set_market_price: Set sell_price equal to market_price where market_price > 0
      - offset_percent: Apply a percentage adjustment (e.g. +10% or -5%) to sell_price
      - clear_tags: Unlink custom_tag_id / NFC UID from all selected cards
    """
    payload = request.get_json(silent=True) or {}
    item_ids = payload.get("item_ids") or []
    action = payload.get("action", "").strip().lower()

    if not item_ids or not isinstance(item_ids, list):
        return jsonify({"success": False, "error": "Missing or invalid 'item_ids' list."}), 400

    session = _resolve_session()
    is_external_session = hasattr(current_app, "db_session") and current_app.db_session is session

    try:
        items = session.query(SinglesInventory).filter(SinglesInventory.id.in_(item_ids)).all()
        updated_count = 0

        for item in items:
            if action == "set_market_price":
                if item.market_price is not None and item.market_price > 0.0:
                    item.sell_price = round(float(item.market_price), 2)
                    item.updated_at = datetime.now(timezone.utc)
                    updated_count += 1

            elif action == "offset_percent":
                pct = float(payload.get("percent", 0.0))
                factor = 1.0 + (pct / 100.0)
                item.sell_price = max(0.0, round(item.sell_price * factor, 2))
                item.updated_at = datetime.now(timezone.utc)
                updated_count += 1

            elif action == "clear_tags":
                if item.custom_tag_id is not None:
                    item.custom_tag_id = None
                    item.updated_at = datetime.now(timezone.utc)
                    updated_count += 1

            elif action == "zero_stock":
                item.quantity = 0
                item.updated_at = datetime.now(timezone.utc)
                updated_count += 1

        session.commit()
        return jsonify({
            "success": True,
            "action": action,
            "updated_count": updated_count,
            "message": f"Successfully applied '{action}' to {updated_count} card(s)."
        })

    except Exception as exc:
        session.rollback()
        logger.exception("[DATABASE_ROUTE] Bulk operation failed: %s", exc)
        return jsonify({"success": False, "error": f"Bulk operation failed: {str(exc)}"}), 500
    finally:
        if not is_external_session:
            session.close()
