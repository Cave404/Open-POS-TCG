"""
OpenPOS-TCG Addon
File: providers/base.py
Addon ID: tcg_pos

Abstract Provider Layer establishing the standard data contract and baseline
HTTP/caching infrastructure for all external collectible card game APIs.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
import requests


@dataclass
class NormalizedCard:
    """
    Unified card entity schema across all supported card games.
    Decouples OpenPOS inventory, buylist, and POS logic from upstream schema shifts.
    """
    # Upstream Identification
    provider_card_id: str             # Unique provider ID (e.g., Scryfall UUID, TCGdex ID)
    game: str                         # System slug: 'mtg', 'pokemon', etc.
    name: str                         # Official card name
    clean_name: str                   # Sanitized alphanumeric name for fast localized indexing
    set_code: str                     # Lowercase set identifier ('neo', 'swsh1')
    set_name: str                     # Human-readable set/expansion title
    collector_number: str             # Collector number (string to support '024a', 'PL1', etc.)
    rarity: str                       # Normalized: 'common', 'uncommon', 'rare', 'mythic', etc.

    # Visual Asset Resolution
    image_uri: Optional[str] = None         # Remote upstream CDN URL
    cached_image_path: Optional[str] = None # Relative path: data/cache/tcg_art/<game>/<id>.jpg

    # Pricing Engine Baseline (USD)
    market_price: Optional[float] = None    # Regular non-foil market average
    low_price: Optional[float] = None       # Low market floor
    foil_price: Optional[float] = None      # Foil / reverse-holo market rate
    etched_price: Optional[float] = None    # Etched / specialty finish market rate

    # Game-Specific Payload
    # Stored directly into the polymorphic singles_inventory.api_metadata JSON column
    api_metadata: Dict[str, Any] = field(default_factory=dict)
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Serializes NormalizedCard dataclass instance into a clean JSON-serializable dictionary."""
        return {
            "provider_card_id": self.provider_card_id,
            "game": self.game,
            "name": self.name,
            "clean_name": self.clean_name,
            "set_code": self.set_code,
            "set_name": self.set_name,
            "collector_number": self.collector_number,
            "rarity": self.rarity,
            "image_uri": self.image_uri,
            "cached_image_path": self.cached_image_path,
            "market_price": self.market_price,
            "low_price": self.low_price,
            "foil_price": self.foil_price,
            "etched_price": self.etched_price,
            "api_metadata": self.api_metadata if isinstance(self.api_metadata, dict) else {},
            "fetched_at": self.fetched_at
        }


class BaseTCGProvider(ABC):
    """
    Base driver providing automatic rate-limiting, compliant User-Agent headers,
    atomic image caching, and strict timeout controls for Windows desktop environments.
    """

    def __init__(
        self,
        game_slug: str,
        base_url: str,
        rate_limit_delay: float = 0.1,
        user_agent: Optional[str] = None,
        custom_data_dir: Optional[Path] = None
    ):
        """
        :param game_slug: Identifier slug matching the DB column and cache subdirectory.
        :param base_url: Upstream API base endpoint without trailing slash.
        :param rate_limit_delay: Inter-request sleep ceiling in seconds.
        :param user_agent: Explicit User-Agent required by provider guidelines.
        :param custom_data_dir: Optional root override for OpenPOS data directory.
        """
        self.game_slug = game_slug.strip().lower()
        self.base_url = base_url.rstrip("/")
        self.rate_limit_delay = rate_limit_delay
        self._last_request_time: float = 0.0

        # Enforce compliant descriptive User-Agent
        self.user_agent = user_agent or f"OpenPOS-TCG/1.0.0 (Windows Native; +https://github.com/OpenPOS-Platform/Open-POS-TCG)"

        # Resolve isolated data path defensively for Windows packaging
        root_data = custom_data_dir or (Path.cwd() / "data")
        self.cache_dir = root_data / "cache" / "tcg_art" / self.game_slug
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Persistent HTTP connection pooling
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "application/json"
        })

    def _apply_rate_limit(self) -> None:
        """Enforces thread-safe interval spacing between external HTTP queries."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self._last_request_time = time.time()

    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
        """
        Executes a rate-limited GET request with automatic retry on upstream 429 throttling.
        """
        self._apply_rate_limit()
        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        try:
            response = self.session.get(url, params=params, timeout=12)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                return None
            elif response.status_code == 429:
                # Obey rate limiting headers or back off 2 seconds
                retry_after = int(response.headers.get("Retry-After", 2))
                time.sleep(retry_after)
                return self._get(endpoint, params)
            else:
                response.raise_for_status()
        except requests.exceptions.RequestException:
            return None

        return None

    def cache_card_image(self, image_url: Optional[str], provider_card_id: str) -> Optional[str]:
        """
        Downloads remote card art to data/cache/tcg_art/<game>/<sanitized_id>.jpg.
        Employs atomic file writes (.tmp -> rename) to avoid corrupt image assets
        if the desktop app window or background thread terminates abruptly.
        """
        if not image_url:
            return None

        safe_id = "".join(c for c in provider_card_id if c.isalnum() or c in ("-", "_"))
        target_file = self.cache_dir / f"{safe_id}.jpg"
        temp_file = self.cache_dir / f"{safe_id}.tmp"
        relative_path = f"data/cache/tcg_art/{self.game_slug}/{safe_id}.jpg"

        # Return existing asset without issuing HTTP request
        if target_file.exists() and target_file.stat().st_size > 0:
            return relative_path

        self._apply_rate_limit()
        try:
            res = self.session.get(image_url, timeout=15, stream=True)
            if res.status_code == 200:
                with open(temp_file, "wb") as f:
                    for chunk in res.iter_content(chunk_size=8192):
                        f.write(chunk)
                # Atomic swap replaces destination safely
                temp_file.replace(target_file)
                return relative_path
        except requests.exceptions.RequestException:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass
            return None

        return None

    @abstractmethod
    def search_cards(self, query: str, page: int = 1, download_images: bool = False) -> List[NormalizedCard]:
        """Search upstream API by name, set, or advanced game query string."""
        pass

    @abstractmethod
    def get_card_by_id(self, provider_card_id: str, download_image: bool = False) -> Optional[NormalizedCard]:
        """Direct lookup via upstream primary key / UUID."""
        pass

    @abstractmethod
    def get_card_by_collector_number(
        self,
        set_code: str,
        collector_number: str,
        lang: str = "en",
        download_image: bool = False
    ) -> Optional[NormalizedCard]:
        """Lookup by expansion abbreviation and card number."""
        pass

    @abstractmethod
    def fetch_market_prices(self, provider_card_id: str) -> Dict[str, Optional[float]]:
        """Fetch current market pricing values: regular, foil, etched, and low floor."""
        pass
