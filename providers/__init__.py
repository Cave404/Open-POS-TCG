"""
OpenPOS-TCG Addon
Providers Package
"""

from .base import BaseTCGProvider, NormalizedCard
from .mtg_scryfall import ScryfallProvider
from .pokemon_tcgdex import PokemonTCGdexProvider

__all__ = [
    "BaseTCGProvider",
    "NormalizedCard",
    "ScryfallProvider",
    "PokemonTCGdexProvider"
]
