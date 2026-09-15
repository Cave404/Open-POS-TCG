"""
OpenPOS-TCG Addon
File: routes/internal.py
Addon ID: tcg_pos

Internal Webhook Endpoints & Inter-Addon Handlers.
Includes Ghost Tag Wiper to ensure customer badges do not collide with singles inventory tags.
"""

import logging
import re
from flask import current_app, jsonify, request
from sqlalchemy.orm import Session

from models import SinglesInventory, get_db_session

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


@addon_bp.route("/api/internal/ghost-tag-wipe", methods=["POST"])
def api_ghost_tag_wipe():
    """
    Ghost Tag Wiper Endpoint.
    Receives customer:badge_assigned event payload from Core.
    If the NFC UID is assigned to any card in singles_inventory, sets custom_tag_id = NULL
    to prevent loyalty fobs from ringing up cards.

    Payload format:
    { "nfc_uid": "04394D4FBD2A81" }
    """
    raw_data = request.get_json(silent=True)
    if not raw_data or not isinstance(raw_data, dict):
        return jsonify({"success": False, "error": "Missing or invalid JSON request body."}), 400

    raw_uid = str(raw_data.get("nfc_uid", "")).strip()
    if not raw_uid:
        return jsonify({"success": False, "error": "Missing required field 'nfc_uid'."}), 400

    cleaned_uid = re.sub(r"[^A-F0-9]", "", raw_uid.upper())
    if not cleaned_uid:
        return jsonify({"success": False, "error": "Invalid NFC UID format."}), 400

    session = _resolve_session()
    is_external_session = hasattr(current_app, "db_session") and current_app.db_session is session

    try:
        items = session.query(SinglesInventory).filter_by(custom_tag_id=cleaned_uid).all()
        count = len(items)
        if count > 0:
            for item in items:
                item.custom_tag_id = None
            session.commit()
            log.info("[GhostTagWipe] Unlinked %d items from customer badge UID %s", count, cleaned_uid)

        return jsonify({
            "success": True,
            "unlinked_items": count
        }), 200

    except Exception as exc:
        session.rollback()
        log.exception("[GhostTagWipe] Database failure during ghost tag wipe: %s", exc)
        return jsonify({
            "success": False,
            "error": f"Ghost tag wipe failed: {str(exc)}"
        }), 500
    finally:
        if not is_external_session:
            session.close()
