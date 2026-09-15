"""
OpenPOS-TCG Addon
File: routes/inventory.py
Addon ID: tcg_pos

Inventory Management & Label Payload Endpoints.
Provides administrative inventory inspection, filtration, inline adjustments,
hardware token assignment, and standardized label metadata generation.
"""

from datetime import datetime, timezone
import math
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


@addon_bp.route("/inventory", methods=["GET"])
def render_inventory_view():
    """
    Renders the Singles Inventory Management workstation.
    """
    return render_template("tcg_pos/inventory.html")


@addon_bp.route("/api/inventory", methods=["GET"])
def api_list_inventory():
    """
    Paginated Inventory List API with multi-field filtering.

    Query Parameters:
      page: Page index (default: 1)
      limit: Page size (default: 50, max: 200)
      game: Optional game slug ('mtg', 'pokemon')
      set_code: Optional expansion code
      condition: Optional condition ('NM', 'LP', etc.)
      finish: Optional finish ('nonfoil', 'foil', 'etched')
      search: Name, clean_name, SKU, or Tag ID query string
      in_stock_only: Filter quantity > 0 (boolean)
    """
    page = max(1, int(request.args.get("page", 1)))
    limit = max(1, min(200, int(request.args.get("limit", 50))))
    game = request.args.get("game", "").strip().lower()
    set_code = request.args.get("set_code", "").strip().lower()
    condition = request.args.get("condition", "").strip().upper()
    finish = request.args.get("finish", "").strip().lower()
    search = request.args.get("search", "").strip()
    in_stock_only = request.args.get("in_stock_only", "").lower() in ("true", "1", "yes")

    session = _resolve_session()
    is_external_session = hasattr(current_app, "db_session") and current_app.db_session is session

    try:
        query = session.query(SinglesInventory)

        if game:
            query = query.filter_by(game=game)
        if set_code:
            query = query.filter_by(set_code=set_code)
        if condition:
            query = query.filter_by(condition=condition)
        if finish:
            query = query.filter_by(finish=finish)
        if in_stock_only:
            query = query.filter(SinglesInventory.quantity > 0)

        if search:
            clean_s = re.sub(r"[^\w\s]", "", search).strip().lower()
            pattern = f"%{search}%"
            clean_pattern = f"%{clean_s}%"
            query = query.filter(
                (SinglesInventory.name.ilike(pattern)) |
                (SinglesInventory.clean_name.ilike(clean_pattern)) |
                (SinglesInventory.sku == search) |
                (SinglesInventory.custom_tag_id == search)
            )

        total_count = query.count()
        items = query.order_by(
            SinglesInventory.updated_at.desc(),
            SinglesInventory.name.asc()
        ).offset((page - 1) * limit).limit(limit).all()

        total_pages = math.ceil(total_count / limit) if total_count > 0 else 1

        return jsonify({
            "success": True,
            "items": [item.to_dict() for item in items],
            "total": total_count,
            "page": page,
            "limit": limit,
            "total_pages": total_pages
        })

    finally:
        if not is_external_session:
            session.close()


@addon_bp.route("/api/inventory/<int:item_id>", methods=["PATCH"])
def api_update_inventory_item(item_id: int):
    """
    Partial Update API for an inventory SKU.
    Supports updating sell_price, quantity, condition, finish, and custom_tag_id.
    Validates custom_tag_id uniqueness if modified.
    """
    data = request.get_json(silent=True) or {}
    session = _resolve_session()
    is_external_session = hasattr(current_app, "db_session") and current_app.db_session is session

    try:
        item = session.query(SinglesInventory).filter_by(id=item_id).first()
        if not item:
            return jsonify({"success": False, "error": f"Item {item_id} not found."}), 404

        # Update sell_price
        if "sell_price" in data:
            item.sell_price = max(0.0, float(data["sell_price"]))

        # Update quantity
        if "quantity" in data:
            item.quantity = max(0, int(data["quantity"]))

        # Update cost_basis
        if "cost_basis" in data:
            item.cost_basis = max(0.0, float(data["cost_basis"]))

        # Update condition
        if "condition" in data:
            item.condition = str(data["condition"]).strip().upper()

        # Update finish
        if "finish" in data:
            item.finish = str(data["finish"]).strip().lower()

        # Update custom_tag_id with conflict check
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
                        "error": f"Tag ID '{new_tag}' is already assigned to #{conflict.id} ({conflict.name})."
                    }), 400
            item.custom_tag_id = new_tag

        # Update sku with conflict check
        if "sku" in data:
            new_sku = str(data["sku"]).strip() if data["sku"] else None
            if new_sku:
                conflict = session.query(SinglesInventory).filter(
                    SinglesInventory.sku == new_sku,
                    SinglesInventory.id != item_id
                ).first()
                if conflict:
                    return jsonify({
                        "success": False,
                        "error": f"SKU '{new_sku}' is already assigned to #{conflict.id} ({conflict.name})."
                    }), 400
            item.sku = new_sku

        item.updated_at = datetime.now(timezone.utc)
        session.commit()

        return jsonify({
            "success": True,
            "item": item.to_dict(),
            "message": f"Successfully updated item #{item.id}."
        })

    except Exception as e:
        session.rollback()
        return jsonify({"success": False, "error": f"Update failed: {str(e)}"}), 500

    finally:
        if not is_external_session:
            session.close()


@addon_bp.route("/api/inventory/<int:item_id>", methods=["DELETE"])
def api_delete_inventory_item(item_id: int):
    """
    Deletes an inventory record or zeros out stock (soft delete).
    """
    soft = request.args.get("soft", "false").lower() in ("true", "1", "yes")
    session = _resolve_session()
    is_external_session = hasattr(current_app, "db_session") and current_app.db_session is session

    try:
        item = session.query(SinglesInventory).filter_by(id=item_id).first()
        if not item:
            return jsonify({"success": False, "error": f"Item {item_id} not found."}), 404

        if soft:
            item.quantity = 0
            item.updated_at = datetime.now(timezone.utc)
            session.commit()
            return jsonify({"success": True, "message": f"Item #{item_id} stock zeroed out."})
        else:
            session.delete(item)
            session.commit()
            return jsonify({"success": True, "message": f"Item #{item_id} permanently deleted."})

    except Exception as e:
        session.rollback()
        return jsonify({"success": False, "error": f"Delete failed: {str(e)}"}), 500

    finally:
        if not is_external_session:
            session.close()


@addon_bp.route("/api/items/<int:item_id>/label-payload", methods=["GET"])
def api_item_label_payload(item_id: int):
    """
    Hardware-Agnostic Tag Payload Engine.
    Generates standardized metadata payloads for thermal sleeve labels, 1D/2D barcodes,
    and NFC NDEF tag programming.
    """
    session = _resolve_session()
    is_external_session = hasattr(current_app, "db_session") and current_app.db_session is session

    try:
        item = session.query(SinglesInventory).filter_by(id=item_id).first()
        if not item:
            return jsonify({"success": False, "error": f"Item {item_id} not found."}), 404

        sku_val = item.sku or item.generate_default_sku()
        qr_uri = f"openpos://tcg/{item.id}"
        barcode_code = item.custom_tag_id or sku_val

        return jsonify({
            "id": item.id,
            "sku": sku_val,
            "custom_tag_id": item.custom_tag_id,
            "display_name": item.name,
            "set_name": item.set_name,
            "set_code": item.set_code.upper(),
            "collector_number": item.collector_number,
            "rarity": item.rarity,
            "condition": item.condition,
            "finish": item.finish,
            "sell_price": item.sell_price,
            "qr_payload": qr_uri,
            "barcode_payload": barcode_code,
            "nfc_ndef_payload": qr_uri
        })

    finally:
        if not is_external_session:
            session.close()
