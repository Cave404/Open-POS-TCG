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
import logging
import re
import threading
from typing import Any, Dict, List, Optional
from flask import current_app, jsonify, render_template, request
from sqlalchemy.orm import Session

from models import SinglesInventory, TCGTransaction, get_db_session
from services.checkout_service import CheckoutService, InsufficientStockError, InvalidTenderError

try:
    from plugin import addon_bp
except ImportError:
    from ..plugin import addon_bp

log = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# GET /tcg/api/register/customer — Core Customer Lookup Bridge
# ---------------------------------------------------------------------------

@addon_bp.route("/api/register/customer", methods=["GET"])
def api_register_customer():
    """
    Customer lookup endpoint bridging to OpenPOS Core.
    Resolves customer by 14-character NFC UID, phone, email, or name.
    """
    identifier = request.args.get("q", "").strip()
    if not identifier:
        return jsonify({"found": False, "error": "Query parameter 'q' is required."}), 400

    from services.core_customer_client import CoreCustomerClient
    client = CoreCustomerClient(base_url=current_app.config.get("CORE_BASE_URL", "http://127.0.0.1:5000"))
    customer = client.resolve_customer(identifier)
    if customer:
        return jsonify({"found": True, "customer": customer})
    return jsonify({"found": False, "error": f"Customer '{identifier}' not found."}), 404


# ---------------------------------------------------------------------------
# POST /tcg/api/checkout/submit
# ---------------------------------------------------------------------------

@addon_bp.route("/api/checkout/submit", methods=["POST"])
def api_checkout_submit():
    """
    Submits a register sale transaction.
    Atomically verifies stock, decrements inventory, snapshots COGS,
    and commits the transaction record.

    Payload format:
    {
      "items": [{"id": 1, "quantity": 2}, {"id": 4, "quantity": 1}],
      "tenders": {"cash": 50.00, "card": 0.00, "store_credit": 12.50},
      "customer_id": 42,
      "discount": 0.0,
      "notes": "",
      "options": {
        "print_receipt": true,
        "kick_drawer": false
      }
    }
    """
    raw_data = request.get_json(silent=True)
    if not raw_data or not isinstance(raw_data, dict):
        return jsonify({"success": False, "error": "Missing or invalid JSON request body."}), 400

    items = raw_data.get("items") or raw_data.get("cart") or []
    tenders = raw_data.get("tenders") or {}
    discount = float(raw_data.get("discount", 0.0))
    tax_rate = float(raw_data.get("tax_rate", current_app.config.get("DEFAULT_TAX_RATE", 0.0)))
    notes = raw_data.get("notes")
    customer_id = raw_data.get("customer_id")
    options = raw_data.get("options") or {}

    if not items:
        return jsonify({"success": False, "error": "Cart is empty."}), 400
    if not tenders:
        return jsonify({"success": False, "error": "No payment tender provided."}), 400

    # Calculate store credit tender amount
    credit_amount = 0.0
    if isinstance(tenders, dict):
        credit_amount = float(tenders.get("store_credit", 0.0))
    elif isinstance(tenders, list):
        for t in tenders:
            if isinstance(t, dict) and (t.get("tender_type") == "store_credit" or t.get("type") == "store_credit"):
                credit_amount += float(t.get("amount", 0.0))

    if credit_amount > 0 and not customer_id:
        return jsonify({"success": False, "error": "customer_id is required when tendering Store Credit."}), 400

    session = _resolve_session()
    is_external_session = hasattr(current_app, "db_session") and current_app.db_session is session

    try:
        txn = CheckoutService.process_sale(
            cart_items=items,
            tenders=tenders,
            tax_rate=tax_rate,
            discount=discount,
            notes=notes,
            customer_id=customer_id,
            session=session,
        )

        # If store credit was tendered, redeem via OpenPOS Core ledger
        if credit_amount > 0 and customer_id:
            from services.core_customer_client import CoreCustomerClient
            import requests
            client = CoreCustomerClient(base_url=current_app.config.get("CORE_BASE_URL", "http://127.0.0.1:5000"))
            try:
                client.redeem_store_credit(
                    customer_id=int(customer_id),
                    amount=credit_amount,
                    transaction_number=txn.transaction_number or txn.receipt_number
                )
            except requests.exceptions.HTTPError as he:
                session.rollback()
                error_msg = "Store Credit redemption failed: Insufficient funds or invalid customer."
                if he.response is not None and he.response.text:
                    error_msg = f"Store Credit redemption failed: {he.response.text}"
                return jsonify({"success": False, "error": error_msg}), 400
            except Exception as ce:
                session.rollback()
                log.warning("[CheckoutSubmit] Core customer credit error: %s", ce)
                return jsonify({"success": False, "error": f"Store credit service error: {str(ce)}"}), 400

        # Hardware drawer kick option
        enable_drawer = current_app.config.get("ENABLE_CASH_DRAWER", False)
        if options.get("kick_drawer") and enable_drawer:
            hub_url = current_app.config.get("HARDWARE_HUB_URL", "http://127.0.0.1:5000")

            def _async_kick():
                try:
                    import requests
                    requests.post(f"{hub_url.rstrip('/')}/hardware/drawer/kick", timeout=1.5)
                except Exception as exc:
                    log.warning("[CheckoutSubmit] Cash drawer kick failed: %s", exc)

            threading.Thread(target=_async_kick, daemon=True).start()

        return jsonify({
            "success": True,
            "transaction": txn.to_dict(),
            "change_due": txn.change_due,
            "receipt_url": f"/tcg/receipt/{txn.id}",
        }), 201

    except InsufficientStockError as ise:
        session.rollback()
        return jsonify({"success": False, "error": str(ise)}), 400
    except (InvalidTenderError, ValueError) as ve:
        session.rollback()
        return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e:
        session.rollback()
        log.exception("[CheckoutSubmit] Unexpected error during checkout: %s", e)
        return jsonify({"success": False, "error": f"Checkout failed: {str(e)}"}), 500
    finally:
        if not is_external_session:
            session.close()


