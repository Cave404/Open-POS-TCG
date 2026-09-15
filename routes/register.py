"""
OpenPOS-TCG Addon
File: routes/register.py
Addon ID: tcg_pos

POS Register Integration & Universal Item Resolver API.
Provides endpoints for rapid item scanning, hardware wedge token resolution
(NFC UID, 1D Barcode, 2D QR Code), in-stock register search, and atomic inventory
decrements during checkout.
"""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional
from flask import current_app, jsonify, render_template, request
from sqlalchemy.orm import Session

from models import SinglesInventory, get_db_session

try:
    from plugin import addon_bp
except ImportError:
    from ..plugin import addon_bp


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


@addon_bp.route("/register", methods=["GET"])
def register_view():
    """Renders the TCG POS Register Checkout screen."""
    return render_template("tcg_pos/register.html")


@addon_bp.route("/api/resolve", methods=["GET"])
def api_resolve_item():
    """
    Universal Item Resolver API.
    Resolves an arbitrary scanned or tapped hardware identifier into a uniform
    POS cart item payload.

    Waterfall Resolution Order:
      1. custom_tag_id (NFC hardware UID, 1D barcode text, or 2D QR code payload)
      2. sku (system SKU or custom SKU)
      3. id (primary integer key if numeric or formatted 'TCG-<id>')
    """
    raw_identifier = request.args.get("identifier", "").strip()
    if not raw_identifier:
        return jsonify({
            "found": False,
            "error": "Query parameter 'identifier' is required."
        }), 400

    session = _resolve_session()
    is_external_session = hasattr(current_app, "db_session") and current_app.db_session is session

    try:
        # Step 1: Match custom_tag_id (NFC, Barcode, QR)
        item = session.query(SinglesInventory).filter_by(custom_tag_id=raw_identifier).first()

        # Step 2: Match sku
        if not item:
            item = session.query(SinglesInventory).filter_by(sku=raw_identifier).first()

        # Step 3: Match primary ID
        if not item:
            # Direct numeric match
            if raw_identifier.isdigit():
                item = session.query(SinglesInventory).filter_by(id=int(raw_identifier)).first()
            # Prefix format match (e.g. 'TCG-1042' -> 1042)
            elif raw_identifier.upper().startswith("TCG-"):
                num_part = raw_identifier[4:].strip()
                if num_part.isdigit():
                    item = session.query(SinglesInventory).filter_by(id=int(num_part)).first()

        if item:
            return jsonify({
                "found": True,
                "item": item.to_dict()
            })

        return jsonify({
            "found": False,
            "error": f"Item not found for identifier '{raw_identifier}'."
        }), 404

    finally:
        if not is_external_session:
            session.close()


@addon_bp.route("/api/register/search", methods=["GET"])
def api_register_search():
    """
    Register Fast Search API.
    Returns in-stock items (`quantity > 0`) matching search query by name, clean_name,
    set_code, or SKU.
    """
    query = request.args.get("q", "").strip()
    limit = max(1, min(100, int(request.args.get("limit", 25))))

    session = _resolve_session()
    is_external_session = hasattr(current_app, "db_session") and current_app.db_session is session

    try:
        q_filter = session.query(SinglesInventory).filter(SinglesInventory.quantity > 0)

        if query:
            clean_q = re.sub(r"[^\w\s]", "", query).strip().lower()
            pattern = f"%{query}%"
            clean_pattern = f"%{clean_q}%"
            q_filter = q_filter.filter(
                (SinglesInventory.name.ilike(pattern)) |
                (SinglesInventory.clean_name.ilike(clean_pattern)) |
                (SinglesInventory.set_code == query.lower()) |
                (SinglesInventory.sku == query) |
                (SinglesInventory.custom_tag_id == query)
            )

        items = q_filter.order_by(SinglesInventory.name.asc()).limit(limit).all()
        return jsonify({
            "success": True,
            "results": [item.to_dict() for item in items],
            "count": len(items)
        })

    finally:
        if not is_external_session:
            session.close()


@addon_bp.route("/api/register/decrement", methods=["POST"])
def api_register_decrement():
    """
    Atomic Register Checkout Decrement API.
    Validates stock availability and decrements quantities atomically during sale checkout.

    Payload format:
    [
      { "id": 1042, "quantity": 1 },
      { "id": 1045, "quantity": 2 }
    ]
    """
    raw_data = request.get_json(silent=True)
    if raw_data is None:
        return jsonify({"success": False, "error": "Missing JSON request body."}), 400

    if isinstance(raw_data, list):
        items_to_decrement = raw_data
    elif isinstance(raw_data, dict):
        items_to_decrement = raw_data.get("items") or [raw_data]
    else:
        return jsonify({"success": False, "error": "Invalid payload format, expected array."}), 400

    if not items_to_decrement:
        return jsonify({"success": False, "error": "No items provided to decrement."}), 400

    session = _resolve_session()
    is_external_session = hasattr(current_app, "db_session") and current_app.db_session is session

    decremented_results = []

    try:
        # Pre-validation and row locking
        for req_item in items_to_decrement:
            item_id = req_item.get("id")
            req_qty = int(req_item.get("quantity", 1))

            if not item_id:
                session.rollback()
                return jsonify({"success": False, "error": "Each item must have an 'id'."}), 400

            if req_qty <= 0:
                session.rollback()
                return jsonify({"success": False, "error": f"Invalid decrement quantity: {req_qty}."}), 400

            # Acquire row with lock where supported
            query = session.query(SinglesInventory).filter_by(id=item_id)
            try:
                item = query.with_for_update().first()
            except Exception:
                # Fallback for backends without with_for_update (e.g. SQLite memory)
                item = query.first()

            if not item:
                session.rollback()
                return jsonify({
                    "success": False,
                    "error": f"Item with ID {item_id} not found."
                }), 404

            if item.quantity < req_qty:
                session.rollback()
                return jsonify({
                    "success": False,
                    "error": (
                        f"Insufficient stock for '{item.name}' ({item.set_code.upper()} "
                        f"{item.condition}/{item.finish}). Requested {req_qty}, available {item.quantity}."
                    ),
                    "insufficient_item_id": item_id,
                    "available_quantity": item.quantity,
                    "requested_quantity": req_qty
                }), 400

            # Decrement quantity
            item.quantity -= req_qty
            item.updated_at = datetime.now(timezone.utc)

            decremented_results.append({
                "id": item.id,
                "name": item.name,
                "sku": item.sku or item.generate_default_sku(),
                "decremented_by": req_qty,
                "remaining_quantity": item.quantity
            })

        session.commit()
        return jsonify({
            "success": True,
            "decremented": decremented_results,
            "message": f"Successfully decremented stock for {len(decremented_results)} items."
        })

    except Exception as e:
        session.rollback()
        return jsonify({
            "success": False,
            "error": f"Inventory decrement transaction failed: {str(e)}"
        }), 500

    finally:
        if not is_external_session:
            session.close()
