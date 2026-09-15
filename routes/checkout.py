"""
OpenPOS-TCG Addon
File: routes/checkout.py
Addon ID: tcg_pos

Checkout Settlement REST API & Receipt View.
Provides transaction settlement, retrieval, and 80mm thermal receipt rendering.
"""

import logging
from flask import current_app, jsonify, render_template, request
from sqlalchemy.orm import Session

from models import TCGTransaction, get_db_session
from services.checkout import CartItem, CheckoutService, TenderEntry

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


def _get_config() -> dict:
    """Extracts hardware and checkout config from the Flask app config."""
    return {
        "enable_cash_drawer": current_app.config.get("ENABLE_CASH_DRAWER", False),
        "enable_receipt_printer": current_app.config.get("ENABLE_RECEIPT_PRINTER", False),
        "hardware_hub_url": current_app.config.get(
            "HARDWARE_HUB_URL", "http://localhost:5111"
        ),
    }


def _get_session_factory():
    """Returns a session factory callable suitable for CheckoutService."""
    if current_app.config.get("DB_SESSION_FACTORY"):
        return current_app.config["DB_SESSION_FACTORY"]
    if current_app.config.get("DB_ENGINE"):
        from sqlalchemy.orm import sessionmaker
        return sessionmaker(bind=current_app.config["DB_ENGINE"])
    return None


# ---------------------------------------------------------------------------
# POST /tcg/api/checkout/settle
# ---------------------------------------------------------------------------

@addon_bp.route("/api/checkout/settle", methods=["POST"])
def api_checkout_settle():
    """
    Settle a POS transaction.

    Request body (JSON):
    {
        "cart": [
            {"inventory_id": 42, "quantity": 1, "unit_price": 9.99},
            ...
        ],
        "tenders": [
            {"tender_type": "cash",  "amount": 20.00},
            {"tender_type": "card",  "amount": 5.00,  "reference": "xxxx-1234"},
            {"tender_type": "store_credit", "amount": 3.00}
        ],
        "tax_rate": 0.0825
    }
    """
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"success": False, "error": "Missing JSON request body."}), 400

    raw_cart = body.get("cart", [])
    raw_tenders = body.get("tenders", [])
    tax_rate = float(body.get("tax_rate", 0.0))

    if not isinstance(raw_cart, list) or not raw_cart:
        return jsonify({"success": False, "error": "'cart' must be a non-empty array."}), 400

    if not isinstance(raw_tenders, list) or not raw_tenders:
        return jsonify({"success": False, "error": "'tenders' must be a non-empty array."}), 400

    # Build DTOs
    try:
        cart_items = [
            CartItem(
                inventory_id=int(c["inventory_id"]),
                quantity=int(c.get("quantity", 1)),
                unit_price=float(c["unit_price"]) if "unit_price" in c else None,
            )
            for c in raw_cart
        ]
    except (KeyError, TypeError, ValueError) as exc:
        return jsonify({"success": False, "error": f"Invalid cart item: {exc}"}), 400

    try:
        tender_entries = [
            TenderEntry(
                tender_type=str(t["tender_type"]),
                amount=float(t["amount"]),
                reference=t.get("reference"),
            )
            for t in raw_tenders
        ]
    except (KeyError, TypeError, ValueError) as exc:
        return jsonify({"success": False, "error": f"Invalid tender entry: {exc}"}), 400

    result = CheckoutService.settle(
        cart_items=cart_items,
        tenders=tender_entries,
        tax_rate=tax_rate,
        config=_get_config(),
        session_factory=_get_session_factory(),
    )

    if not result.success:
        return jsonify({"success": False, "error": result.error}), 400

    response = {
        "success": True,
        "transaction_id": result.transaction_id,
        "receipt_number": result.receipt_number,
        "grand_total": result.grand_total,
        "change_due": result.change_due,
        "receipt_url": f"/tcg/receipt/{result.transaction_id}",
    }
    if result.hardware_errors:
        response["hardware_warnings"] = result.hardware_errors

    return jsonify(response), 201


# ---------------------------------------------------------------------------
# GET /tcg/api/checkout/transaction/<id>
# ---------------------------------------------------------------------------

@addon_bp.route("/api/checkout/transaction/<int:transaction_id>", methods=["GET"])
def api_get_transaction(transaction_id: int):
    """Retrieves a past transaction with all line items and tenders."""
    session = _resolve_session()
    is_external = (
        hasattr(current_app, "db_session") and current_app.db_session is session
    )
    try:
        txn = session.query(TCGTransaction).filter_by(id=transaction_id).first()
        if not txn:
            return jsonify({"success": False, "error": "Transaction not found."}), 404
        return jsonify({"success": True, "transaction": txn.to_dict()})
    finally:
        if not is_external:
            session.close()


# ---------------------------------------------------------------------------
# GET /tcg/receipt/<id>  — 80mm Thermal Receipt View
# ---------------------------------------------------------------------------

@addon_bp.route("/receipt/<int:transaction_id>", methods=["GET"])
def receipt_view(transaction_id: int):
    """
    Renders the printable 80mm thermal receipt for a completed transaction.
    Includes a 'Back to Register' button (hidden on @media print).
    """
    session = _resolve_session()
    is_external = (
        hasattr(current_app, "db_session") and current_app.db_session is session
    )
    try:
        txn = session.query(TCGTransaction).filter_by(id=transaction_id).first()
        if not txn:
            return (
                "<h2>Receipt not found.</h2>"
                "<a href='/tcg/register'>Back to Register</a>",
                404,
            )
        return render_template("tcg_pos/receipt.html", txn=txn)
    finally:
        if not is_external:
            session.close()
