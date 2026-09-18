"""
OpenPOS-TCG Addon
File: routes/main.py
Addon ID: tcg_pos

Operational TCG Workstation Hub & Primary Dashboard View.
Provides immediate access to the Register Checkout, Card Intake,
Database Explorer, Market Pricing Engine, and Configuration panels.
Displays real-time KPI aggregates: total inventory count, retail valuation,
cost basis, and today's trade-in volume.
"""

from datetime import datetime, time, timezone
import logging
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


def get_workstation_stats(session: Session) -> dict:
    """Calculates live KPI metrics across singles inventory and trade-in activity."""
    try:
        # Total distinct card titles and total quantity of cards in stock
        total_items = session.query(SinglesInventory).count()
        total_units = session.query(func.coalesce(func.sum(SinglesInventory.quantity), 0)).scalar() or 0

        # Total cost basis and retail valuation
        cost_expr = func.coalesce(func.sum(SinglesInventory.cost_basis * SinglesInventory.quantity), 0.0)
        sell_expr = func.coalesce(func.sum(SinglesInventory.sell_price * SinglesInventory.quantity), 0.0)
        market_expr = func.coalesce(
            func.sum(func.coalesce(SinglesInventory.market_price, SinglesInventory.sell_price) * SinglesInventory.quantity),
            0.0
        )

        total_cost_basis = float(session.query(cost_expr).scalar() or 0.0)
        total_sell_valuation = float(session.query(sell_expr).scalar() or 0.0)
        total_market_valuation = float(session.query(market_expr).scalar() or 0.0)
        potential_margin = round(total_sell_valuation - total_cost_basis, 2)

        # Today's Trade-in Volume (Cards created or acquired today with cost basis > 0)
        now_utc = datetime.now(timezone.utc)
        today_start = datetime.combine(now_utc.date(), time.min).replace(tzinfo=timezone.utc)

        trade_units_query = session.query(
            func.coalesce(func.sum(SinglesInventory.quantity), 0)
        ).filter(
            SinglesInventory.created_at >= today_start,
            SinglesInventory.cost_basis > 0
        )
        today_trade_units = int(trade_units_query.scalar() or 0)

        trade_volume_query = session.query(
            func.coalesce(func.sum(SinglesInventory.cost_basis * SinglesInventory.quantity), 0.0)
        ).filter(
            SinglesInventory.created_at >= today_start,
            SinglesInventory.cost_basis > 0
        )
        today_trade_volume = float(trade_volume_query.scalar() or 0.0)

        # Low stock and out of stock counts
        low_stock_count = session.query(SinglesInventory).filter(
            SinglesInventory.quantity > 0,
            SinglesInventory.quantity <= 2
        ).count()
        out_of_stock_count = session.query(SinglesInventory).filter(
            SinglesInventory.quantity == 0
        ).count()

        return {
            "total_items": total_items,
            "total_units": total_units,
            "total_cost_basis": round(total_cost_basis, 2),
            "total_sell_valuation": round(total_sell_valuation, 2),
            "total_market_valuation": round(total_market_valuation, 2),
            "potential_margin": potential_margin,
            "today_trade_units": today_trade_units,
            "today_trade_volume": round(today_trade_volume, 2),
            "low_stock_count": low_stock_count,
            "out_of_stock_count": out_of_stock_count,
        }
    except Exception as exc:
        logger.exception("[MAIN_ROUTE] Failed to compute workstation stats: %s", exc)
        return {
            "total_items": 0,
            "total_units": 0,
            "total_cost_basis": 0.0,
            "total_sell_valuation": 0.0,
            "total_market_valuation": 0.0,
            "potential_margin": 0.0,
            "today_trade_units": 0,
            "today_trade_volume": 0.0,
            "low_stock_count": 0,
            "out_of_stock_count": 0,
        }


@addon_bp.route("", methods=["GET"])
@addon_bp.route("/", methods=["GET"])
def workstation_landing_view():
    """
    Renders the unified operational TCG Workstation Landing View.
    Replaces static placeholders with the live operational workstation hub.
    """
    session = _resolve_session()
    is_external_session = hasattr(current_app, "db_session") and current_app.db_session is session

    try:
        stats = get_workstation_stats(session)
        return render_template("tcg_pos/index.html", stats=stats)
    finally:
        if not is_external_session:
            session.close()


@addon_bp.route("/api/stats", methods=["GET"])
def api_workstation_stats():
    """Returns real-time workstation KPI stats as JSON."""
    session = _resolve_session()
    is_external_session = hasattr(current_app, "db_session") and current_app.db_session is session

    try:
        stats = get_workstation_stats(session)
        return jsonify({"success": True, "stats": stats})
    finally:
        if not is_external_session:
            session.close()
