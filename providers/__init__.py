"""
OpenPOS-TCG Addon
Providers Package
"""

from .base import BaseTCGProvider, NormalizedCard
from .mtg_scryfall import ScryfallProvider

__all__ = ["BaseTCGProvider", "NormalizedCard", "ScryfallProvider"]
