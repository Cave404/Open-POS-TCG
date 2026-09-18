"""
OpenPOS-TCG Addon
File: routes/settings.py
Addon ID: tcg_pos

Settings & Maintenance Routes.
Handles administrative configuration, trade-in rules, condition multipliers,
and automated image healing (detecting missing or zero-byte card thumbnails).
"""

import logging
import os
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional
from flask import current_app, jsonify, render_template, request
from sqlalchemy.orm import Session

from models import SinglesInventory, get_db_session
from providers.mtg_scryfall import ScryfallProvider
from providers.pokemon_tcgdex import PokemonTCGdexProvider
from services.settings_service import (
    SettingsService,
    get_all_settings,
    get_setting,
    set_setting,
    update_settings,
    reset_to_defaults,
)

try:
    from plugin import addon_bp
except ImportError:
    from ..plugin import addon_bp

logger = logging.getLogger(__name__)

# Global tracking state for the background image healing worker
IMAGE_HEALING_STATUS: Dict[str, Any] = {
    "running": False,
    "total": 0,
    "processed": 0,
    "fixed": 0,
    "errors": 0,
    "logs": ["[SYSTEM] Ready for image maintenance scan."],
    "last_run": None,
}
_HEALING_LOCK = threading.Lock()


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


@addon_bp.route("/settings", methods=["GET"])
def render_settings_view():
    """
    Renders the TCG POS Settings & Configuration workstation.
    Passes current settings dictionary into template context.
    """
    settings = get_all_settings()
    return render_template("tcg_pos/settings.html", settings=settings)


@addon_bp.route("/api/settings", methods=["GET"])
def api_get_settings():
    """Returns current active settings as a JSON object."""
    return jsonify({
        "success": True,
        "settings": get_all_settings()
    })


@addon_bp.route("/api/settings", methods=["POST"])
def api_update_settings():
    """
    Persists updated configuration settings into data/configs/tcg_pos.json.
    Accepts partial or complete settings JSON.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({
            "success": False,
            "error": "Invalid request body; expected JSON dictionary."
        }), 400

    try:
        success = update_settings(payload)
        if success:
            updated = get_all_settings()
            return jsonify({
                "success": True,
                "message": "Settings successfully saved.",
                "settings": updated
            }), 200
        else:
            return jsonify({
                "success": False,
                "error": "Failed to persist settings to disk."
            }), 500
    except Exception as exc:
        logger.exception("[SETTINGS_ROUTE] Failed to update settings: %s", exc)
        return jsonify({
            "success": False,
            "error": f"Failed to save settings: {str(exc)}"
        }), 500


@addon_bp.route("/api/settings/reset", methods=["POST"])
def api_reset_settings():
    """Resets settings to donor platform defaults."""
    try:
        defaults = reset_to_defaults()
        return jsonify({
            "success": True,
            "message": "Settings reset to defaults.",
            "settings": defaults
        }), 200
    except Exception as exc:
        logger.exception("[SETTINGS_ROUTE] Failed to reset settings: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


def _is_image_missing_or_empty(image_path: Optional[str]) -> bool:
    """Checks whether image path is null, points to non-existent file, or is 0 bytes."""
    if not image_path:
        return True
    p = Path(image_path)
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    try:
        if not p.exists() or not p.is_file():
            return True
        if p.stat().st_size == 0:
            return True
    except OSError:
        return True
    return False


def _run_thumbnail_healing_worker(items_to_fix: List[Dict[str, Any]], session_factory) -> None:
    """Background worker thread to re-download missing card thumbnails."""
    global IMAGE_HEALING_STATUS
    mtg_provider = ScryfallProvider()
    pokemon_provider = PokemonTCGdexProvider()

    with _HEALING_LOCK:
        IMAGE_HEALING_STATUS["running"] = True
        IMAGE_HEALING_STATUS["total"] = len(items_to_fix)
        IMAGE_HEALING_STATUS["processed"] = 0
        IMAGE_HEALING_STATUS["fixed"] = 0
        IMAGE_HEALING_STATUS["errors"] = 0
        IMAGE_HEALING_STATUS["logs"].append(f"[START] Beginning healing scan for {len(items_to_fix)} items.")

    for item_meta in items_to_fix:
        item_id = item_meta["id"]
        game = (item_meta.get("game") or "mtg").lower()
        provider_id = item_meta.get("provider_card_id")
        image_uri = item_meta.get("image_uri")
        name = item_meta.get("name", f"Item #{item_id}")

        cached_rel_path = None
        try:
            provider = pokemon_provider if game == "pokemon" else mtg_provider

            # Strategy 1: Re-cache from existing image_uri if available
            if image_uri:
                cached_rel_path = provider.cache_card_image(image_uri, provider_id)

            # Strategy 2: If image_uri absent or failed, query upstream provider by card id
            if not cached_rel_path and provider_id:
                card = provider.get_card_by_id(provider_id, download_image=True)
                if card:
                    cached_rel_path = card.cached_image_path or (
                        provider.cache_card_image(card.image_url, provider_id) if card.image_url else None
                    )
                    image_uri = card.image_url

            # If image successfully downloaded, update database record
            if cached_rel_path:
                session = session_factory()
                try:
                    db_item = session.query(SinglesInventory).filter_by(id=item_id).first()
                    if db_item:
                        db_item.image_path = cached_rel_path
                        if image_uri and not db_item.image_uri:
                            db_item.image_uri = image_uri
                        session.commit()
                        with _HEALING_LOCK:
                            IMAGE_HEALING_STATUS["fixed"] += 1
                            IMAGE_HEALING_STATUS["logs"].append(f"[OK] Re-cached art for #{item_id}: {name}")
                except Exception as db_err:
                    session.rollback()
                    logger.error("[HEALING_WORKER] DB update failed for #%d: %s", item_id, db_err)
                    with _HEALING_LOCK:
                        IMAGE_HEALING_STATUS["errors"] += 1
                finally:
                    session.close()
            else:
                with _HEALING_LOCK:
                    IMAGE_HEALING_STATUS["errors"] += 1
                    IMAGE_HEALING_STATUS["logs"].append(f"[SKIP] Could not fetch art for #{item_id}: {name}")

        except Exception as err:
            logger.error("[HEALING_WORKER] Error re-caching #%d: %s", item_id, err)
            with _HEALING_LOCK:
                IMAGE_HEALING_STATUS["errors"] += 1
                IMAGE_HEALING_STATUS["logs"].append(f"[ERR] Error #{item_id} ({name}): {str(err)}")

        with _HEALING_LOCK:
            IMAGE_HEALING_STATUS["processed"] += 1

    with _HEALING_LOCK:
        IMAGE_HEALING_STATUS["running"] = False
        from datetime import datetime, timezone
        IMAGE_HEALING_STATUS["last_run"] = datetime.now(timezone.utc).isoformat()
        IMAGE_HEALING_STATUS["logs"].append(
            f"[COMPLETE] Finished healing: {IMAGE_HEALING_STATUS['fixed']} fixed, "
            f"{IMAGE_HEALING_STATUS['errors']} errors."
        )


@addon_bp.route("/api/maintenance/fix-thumbnails", methods=["POST"])
def api_fix_thumbnails():
    """
    Scans singles_inventory for records where image_path is null, empty,
    or points to a non-existent/0-byte file in data/cache/tcg_art/.
    Queues background re-download via Scryfall or TCGdex.
    Returns: { "success": true, "queued_count": int }
    """
    session = _resolve_session()
    is_external_session = hasattr(current_app, "db_session") and current_app.db_session is session

    try:
        candidates = session.query(SinglesInventory).all()
        to_fix: List[Dict[str, Any]] = []

        for item in candidates:
            if _is_image_missing_or_empty(item.image_path):
                to_fix.append({
                    "id": item.id,
                    "game": item.game,
                    "provider_card_id": item.provider_card_id,
                    "image_uri": item.image_uri,
                    "name": item.name,
                })

        queued_count = len(to_fix)

        if queued_count > 0:
            session_factory = lambda: _resolve_session()
            t = threading.Thread(
                target=_run_thumbnail_healing_worker,
                args=(to_fix, session_factory),
                daemon=True
            )
            t.start()

        return jsonify({
            "success": True,
            "queued_count": queued_count,
            "message": f"Queued {queued_count} card thumbnail(s) for verification and healing."
        }), 200

    except Exception as exc:
        logger.exception("[SETTINGS_ROUTE] Failed to initiate thumbnail scan: %s", exc)
        return jsonify({
            "success": False,
            "queued_count": 0,
            "error": f"Failed to scan thumbnails: {str(exc)}"
        }), 500
    finally:
        if not is_external_session:
            session.close()


@addon_bp.route("/api/maintenance/thumbnails-status", methods=["GET"])
def api_thumbnails_status():
    """Returns current status and log buffer of the thumbnail healing worker."""
    with _HEALING_LOCK:
        return jsonify({
            "success": True,
            "status": dict(IMAGE_HEALING_STATUS)
        })
