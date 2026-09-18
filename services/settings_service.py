"""
OpenPOS-TCG Addon
File: services/settings_service.py
Addon ID: tcg_pos

Thread-safe configuration management service for OpenPOS-TCG.
Persists settings to data/configs/tcg_pos.json with atomic disk writes,
merges default trade-in parameters, condition degradation scales, and
falls back safely to OpenPOS core configuration when available.
"""

import json
import logging
import os
from pathlib import Path
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Donor standard default configuration values
DEFAULT_SETTINGS: Dict[str, Any] = {
    "cash_payout_rate": 60.0,
    "store_credit_payout_rate": 80.0,
    "daily_trade_limit": 10,
    "condition_multipliers": {
        "NM": 1.0,
        "LP": 0.85,
        "MP": 0.70,
        "HP": 0.50,
        "DMG": 0.30,
    },
    "market_refresh_days": 1,
    "enable_cash_drawer": False,
    "enable_receipt_printer": True,
}

_SETTINGS_LOCK = threading.RLock()
_CUSTOM_CONFIG_PATH: Optional[Path] = None


def set_custom_config_path(path: Optional[Path]) -> None:
    """Allows test fixtures or alternate runtime environments to point to an isolated config file."""
    global _CUSTOM_CONFIG_PATH
    with _SETTINGS_LOCK:
        _CUSTOM_CONFIG_PATH = Path(path) if path is not None else None


def get_config_file_path() -> Path:
    """
    Returns the resolved absolute Path to data/configs/tcg_pos.json.
    Ensures parent directory structure exists.
    """
    if _CUSTOM_CONFIG_PATH is not None:
        _CUSTOM_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        return _CUSTOM_CONFIG_PATH

    # Attempt to resolve from OpenPOS Core config if imported
    config_dir = None
    try:
        from core.config import Config
        if hasattr(Config, "DATA_DIR"):
            config_dir = Path(Config.DATA_DIR) / "configs"
    except ImportError:
        pass

    if config_dir is None:
        config_dir = Path.cwd() / "data" / "configs"

    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "tcg_pos.json"


def _deep_merge_defaults(source: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively populates missing keys in source dictionary from defaults."""
    merged = dict(defaults)
    for key, val in source.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge_defaults(val, merged[key])
        else:
            merged[key] = val
    return merged


def get_all_settings() -> Dict[str, Any]:
    """
    Loads and returns all configuration settings from data/configs/tcg_pos.json.
    Automatically populates and persists defaults if file is absent or incomplete.
    """
    with _SETTINGS_LOCK:
        config_path = get_config_file_path()
        settings = dict(DEFAULT_SETTINGS)

        if config_path.is_file():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    file_data = json.load(f)
                if isinstance(file_data, dict):
                    settings = _deep_merge_defaults(file_data, DEFAULT_SETTINGS)
            except Exception as err:
                logger.error("[SETTINGS_SERVICE] Failed to read %s: %s", config_path, err)
                settings = dict(DEFAULT_SETTINGS)
        else:
            # First initialization: save initial defaults to disk
            _save_settings_unlocked(settings, config_path)

        return settings


def get_setting(key: str, default: Any = None) -> Any:
    """
    Retrieves a specific configuration parameter.
    Falls back to OpenPOS core configuration store if absent in addon settings.
    """
    settings = get_all_settings()
    if key in settings:
        return settings[key]

    # Optional fallback to OpenPOS Core settings
    try:
        from core.settings import get_setting as core_get_setting
        core_val = core_get_setting(key, None)
        if core_val is not None:
            return core_val
    except ImportError:
        pass

    return default if default is not None else DEFAULT_SETTINGS.get(key)


def _save_settings_unlocked(settings: Dict[str, Any], config_path: Path) -> bool:
    """Internal helper to atomically write settings JSON to disk."""
    temp_path = config_path.parent / f"{config_path.name}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        # Atomic replace avoids corrupted reads during concurrent transactions
        temp_path.replace(config_path)
        return True
    except Exception as err:
        logger.error("[SETTINGS_SERVICE] Failed to write settings to %s: %s", config_path, err)
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        return False


def update_settings(updates: Dict[str, Any]) -> bool:
    """
    Atomically updates and persists one or more configuration settings.
    Accepts partial dictionaries and merges nested condition multipliers.
    """
    if not isinstance(updates, dict):
        raise ValueError("Updates must be a dictionary.")

    with _SETTINGS_LOCK:
        config_path = get_config_file_path()
        current = get_all_settings()

        for key, val in updates.items():
            if key == "condition_multipliers" and isinstance(val, dict):
                current["condition_multipliers"].update({
                    k.strip().upper(): float(v) for k, v in val.items()
                })
            elif key in ("cash_payout_rate", "store_credit_payout_rate"):
                current[key] = float(val)
            elif key in ("daily_trade_limit", "market_refresh_days"):
                current[key] = int(val)
            elif key in ("enable_cash_drawer", "enable_receipt_printer"):
                current[key] = bool(val)
            else:
                current[key] = val

        return _save_settings_unlocked(current, config_path)


def set_setting(key: str, value: Any) -> bool:
    """Convenience setter for a single configuration key."""
    return update_settings({key: value})


def reset_to_defaults() -> Dict[str, Any]:
    """Resets all settings to donor standard defaults and writes to disk."""
    with _SETTINGS_LOCK:
        config_path = get_config_file_path()
        defaults = dict(DEFAULT_SETTINGS)
        _save_settings_unlocked(defaults, config_path)
        return defaults


class SettingsService:
    """Class wrapper exposing static and instance access to settings service functions."""
    get_setting = staticmethod(get_setting)
    set_setting = staticmethod(set_setting)
    get_all_settings = staticmethod(get_all_settings)
    update_settings = staticmethod(update_settings)
    reset_to_defaults = staticmethod(reset_to_defaults)
    get_config_file_path = staticmethod(get_config_file_path)
    set_custom_config_path = staticmethod(set_custom_config_path)
