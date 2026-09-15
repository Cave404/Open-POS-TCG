"""
Open-POS-TCG Plugin Entrypoint
Exposes the addon blueprint to the core OpenPOS loader.
"""
from flask import Blueprint, jsonify

addon_bp = Blueprint(
    'tcg_pos',
    __name__,
    template_folder='templates',
    static_folder='static',
    static_url_path='/addons/tcg_pos/static'
)

@addon_bp.route('/status')
def addon_status():
    return jsonify({"status": "active", "addon": "tcg_pos", "version": "1.0.0"})
