"""
OpenPOS-TCG Addon
File: services/checkout.py
Addon ID: tcg_pos

Compatibility layer re-exporting from services.checkout_service.
"""

from services.checkout_service import (
    CheckoutService,
    CartItem,
    TenderEntry,
    SettlementResult,
    InsufficientStockError,
    InvalidTenderError,
    _fire_cash_drawer,
    _fire_receipt_spool,
)

__all__ = [
    "CheckoutService",
    "CartItem",
    "TenderEntry",
    "SettlementResult",
    "InsufficientStockError",
    "InvalidTenderError",
    "_fire_cash_drawer",
    "_fire_receipt_spool",
]
