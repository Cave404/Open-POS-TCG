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
    return jsonify({"status": "active", "addon": "tcg_pos", "version": "1.0.0"})


# Import route handlers to attach endpoints to addon_bp
from routes import intake  # noqa: E402, F401
from routes import register  # noqa: E402, F401
from routes import inventory  # noqa: E402, F401
