"""
OpenPOS-TCG Addon
File: routes/pricing.py
Addon ID: tcg_pos

Pricing & Market Intelligence Endpoints.
Exposes REST APIs to trigger, monitor, and cancel non-blocking price refresh jobs,
and view/manage cards with significant price volatility drifts.
"""

from typing import Any, Callable, Dict, Optional
from flask import current_app, jsonify, render_template, request
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from models import SinglesInventory, get_db_session
from services.market_refresher import refresher_service

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


def _get_session_factory() -> Optional[Callable[[], Session]]:
    """Returns a factory producing fresh database sessions for background threads."""
    if hasattr(current_app, "db_session") and current_app.db_session is not None:
        return lambda: current_app.db_session
    if current_app.config.get("DB_SESSION"):
        return lambda: current_app.config["DB_SESSION"]
    if current_app.config.get("DB_SESSION_FACTORY"):
        return current_app.config["DB_SESSION_FACTORY"]
    if current_app.config.get("DB_ENGINE"):
        from sqlalchemy.orm import sessionmaker
        return sessionmaker(bind=current_app.config["DB_ENGINE"])
    return None


@addon_bp.route("/pricing", methods=["GET"])
def render_pricing_view():
    """Renders the Market Pricing & Volatility Intelligence dashboard."""
    return render_template("tcg_pos/pricing.html")


@addon_bp.route("/api/pricing/status", methods=["GET"])
def api_pricing_status():
    """Returns the current execution state and progress of the price refresh worker."""
    status_data = refresher_service.get_status()
    return jsonify({"success": True, **status_data})


@addon_bp.route("/api/pricing/refresh", methods=["POST"])
def api_pricing_refresh():
    """
    Triggers a background market price update job.

    Payload format:
    {
      "game": "mtg",                     // Optional: 'mtg', 'pokemon', or null for all active
      "in_stock_only": true,              // Skip zero-inventory cards
      "max_age_days": 1,                  // Only cards not updated within N days (0 for all)
      "auto_adjust_sell_price": false,    // Whether to mutate live POS retail shelf price
      "margin_multiplier": 1.0            // Retail price multiplier if auto-adjust is on
    }
    """
    payload = request.get_json(silent=True) or {}

    game = payload.get("game")
    if game:
        game = str(game).strip().lower()
        if game not in ("mtg", "pokemon", "all"):
            return jsonify({"success": False, "error": f"Unsupported game slug: '{game}'"}), 400
        if game == "all":
            game = None

    in_stock_only = bool(payload.get("in_stock_only", True))

    try:
        max_age_days = int(payload.get("max_age_days", 1))
    except (ValueError, TypeError):
        max_age_days = 1

    auto_adjust_sell_price = bool(payload.get("auto_adjust_sell_price", False))

    try:
        margin_multiplier = float(payload.get("margin_multiplier", 1.0))
    except (ValueError, TypeError):
        margin_multiplier = 1.0

    try:
        job_id = refresher_service.start_refresh(
            game=game,
            in_stock_only=in_stock_only,
            max_age_days=max_age_days,
            auto_adjust_sell_price=auto_adjust_sell_price,
            margin_multiplier=margin_multiplier,
            session_factory=_get_session_factory()
        )
        return jsonify({
            "success": True,
            "job_id": job_id,
            "message": "Market price refresh job successfully launched."
        })
    except RuntimeError as ex:
        return jsonify({
            "success": False,
            "error": str(ex),
            "current_job": refresher_service.get_status()
        }), 409


@addon_bp.route("/api/pricing/cancel", methods=["POST"])
def api_pricing_cancel():
    """Requests early graceful cancellation of an active refresh job."""
    cancelled = refresher_service.cancel_refresh()
    if cancelled:
        return jsonify({"success": True, "message": "Cancellation signal sent."})
    return jsonify({
        "success": False,
        "message": "No active running job to cancel."
    }), 400


@addon_bp.route("/api/pricing/alerts", methods=["GET"])
def api_pricing_alerts():
    """
    Returns inventory items flagged with significant price volatility shifts
    (>= 20% swing) for manual review.
    """
    session = _resolve_session()
    try:
        items = session.query(SinglesInventory).order_by(SinglesInventory.updated_at.desc()).all()
        alerts = []

        for item in items:
            meta = item.api_metadata or {}
            drift = meta.get("last_price_drift")
            if drift and isinstance(drift, dict):
                alerts.append({
                    "id": item.id,
                    "game": item.game,
                    "name": item.name,
                    "set_code": item.set_code,
                    "set_name": item.set_name,
                    "collector_number": item.collector_number,
                    "finish": item.finish,
                    "condition": item.condition,
                    "quantity": item.quantity,
                    "sell_price": item.sell_price,
                    "market_price": item.market_price,
                    "drift": drift,
                    "updated_at": item.updated_at.isoformat() if item.updated_at else None
                })

        return jsonify({
            "success": True,
            "count": len(alerts),
            "alerts": alerts
        })
    finally:
        pass


@addon_bp.route("/api/pricing/alerts/<int:item_id>/apply", methods=["POST"])
def api_pricing_alert_apply(item_id: int):
    """
    Applies the updated market price to the card's shelf price (sell_price)
    and clears the volatility alert.
    """
    session = _resolve_session()
    item = session.get(SinglesInventory, item_id)
    if not item:
        return jsonify({"success": False, "error": f"Item #{item_id} not found."}), 404

    payload = request.get_json(silent=True) or {}
    multiplier = float(payload.get("multiplier", 1.0))

    if item.market_price is not None:
        item.sell_price = round(item.market_price * multiplier, 2)

    meta = dict(item.api_metadata or {})
    meta.pop("last_price_drift", None)
    meta["price_alert"] = False
    item.api_metadata = meta
    flag_modified(item, "api_metadata")

    session.commit()

    return jsonify({
        "success": True,
        "item_id": item.id,
        "new_sell_price": item.sell_price,
        "message": f"Updated shelf price for {item.name} to ${item.sell_price:.2f}"
    })


@addon_bp.route("/api/pricing/alerts/<int:item_id>/dismiss", methods=["POST"])
def api_pricing_alert_dismiss(item_id: int):
    """
    Dismisses the price volatility alert without altering the card's shelf price.
    """
    session = _resolve_session()
    item = session.get(SinglesInventory, item_id)
    if not item:
        return jsonify({"success": False, "error": f"Item #{item_id} not found."}), 404

    meta = dict(item.api_metadata or {})
    meta.pop("last_price_drift", None)
    meta["price_alert"] = False
    item.api_metadata = meta
    flag_modified(item, "api_metadata")

    session.commit()

    return jsonify({
        "success": True,
        "item_id": item.id,
        "message": f"Alert dismissed for {item.name}."
    })
