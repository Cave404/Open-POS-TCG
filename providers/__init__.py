"""
OpenPOS-TCG Addon
Providers Package
"""

from typing import Dict, Optional, Type
from .base import BaseTCGProvider, NormalizedCard
from .mtg_scryfall import ScryfallProvider
from .pokemon_tcgdex import PokemonTCGdexProvider

GAME_PROVIDERS: Dict[str, Type[BaseTCGProvider]] = {
    "mtg": ScryfallProvider,
    "pokemon": PokemonTCGdexProvider,
}

_PROVIDER_INSTANCES: Dict[str, BaseTCGProvider] = {}


def get_provider(game: str) -> Optional[BaseTCGProvider]:
    """
    Centralized provider factory and registry.
    Returns the singleton provider instance for a target collectible card game slug.
    """
    slug = (game or "").strip().lower()
    if slug not in GAME_PROVIDERS:
        return None
    if slug not in _PROVIDER_INSTANCES:
        _PROVIDER_INSTANCES[slug] = GAME_PROVIDERS[slug]()
    return _PROVIDER_INSTANCES[slug]


__all__ = [
    "BaseTCGProvider",
    "NormalizedCard",
    "ScryfallProvider",
    "PokemonTCGdexProvider",
    "GAME_PROVIDERS",
    "get_provider"
]

