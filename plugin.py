"""
OpenPOS-TCG Addon
File: plugin.py
Addon ID: tcg_pos

Plugin Entrypoint.
Exposes the addon blueprint to the core OpenPOS loader.
"""

from flask import Blueprint, jsonify

addon_bp = Blueprint(
    "tcg_pos",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/addons/tcg_pos/static",
    url_prefix="/tcg"
)


@addon_bp.route("/status")
def addon_status():
    """Health check route indicating addon availability."""
    return jsonify({"status": "active", "addon": "tcg_pos", "version": "1.0.1"})


@addon_bp.route("/art/<path:filepath>")
def serve_card_art(filepath: str):
    """Serves locally cached card art from data/cache/tcg_art/."""
    from pathlib import Path
    from flask import send_from_directory, abort
    cache_dir = (Path.cwd() / "data" / "cache" / "tcg_art").resolve()
    target = (cache_dir / filepath).resolve()
    try:
        if not target.is_relative_to(cache_dir) or not target.is_file():
            abort(404)
    except (ValueError, OSError):
        abort(404)
    return send_from_directory(str(target.parent), target.name)


# Import route handlers to attach endpoints to addon_bp
from routes import intake  # noqa: E402, F401
from routes import register  # noqa: E402, F401
from routes import inventory  # noqa: E402, F401
from routes import pricing  # noqa: E402, F401
from routes import checkout  # noqa: E402, F401
from routes import internal  # noqa: E402, F401
from routes import main  # noqa: E402, F401
from routes import settings  # noqa: E402, F401
from routes import database  # noqa: E402, F401


