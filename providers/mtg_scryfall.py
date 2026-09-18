"""
OpenPOS-TCG Addon
File: providers/mtg_scryfall.py
Addon ID: tcg_pos

Magic: The Gathering driver utilizing the Scryfall REST API.
Handles rate constraints, multi-face card image resolution, and price normalization.
"""

import re
from typing import Any, Dict, List, Optional
from pathlib import Path
from .base import BaseTCGProvider, NormalizedCard


class ScryfallProvider(BaseTCGProvider):
    """
    Production Scryfall API driver compliant with Scryfall developer policies:
    - 100ms request spacing.
    - Resolves multi-face cards (DFCs, Transform, Flip, Reversible).
    - Extracts regular, foil, and etched price indices.
    """

    def __init__(
        self,
        user_agent: str = "OpenPOS-TCG/1.0.1 (Scryfall Engine; +https://github.com/OpenPOS-Platform/Open-POS-TCG)",
        custom_data_dir: Optional[Path] = None
    ):
        super().__init__(
            game_slug="mtg",
            base_url="https://api.scryfall.com",
            rate_limit_delay=0.1,  # 100ms polite delay
            user_agent=user_agent,
            custom_data_dir=custom_data_dir
        )
        self.session.headers.update({
            "Accept": "application/json;q=0.9,*/*;q=0.8"
        })

    def _sanitize_name(self, raw_name: str) -> str:
        """Strip non-alphanumerics for fast localized querying."""
        return re.sub(r"[^\w\s]", "", raw_name).strip().lower()

    def _extract_image_uri(self, card_data: Dict[str, Any]) -> Optional[str]:
        """
        Locates the best card image. If top-level image_uris is missing
        (common on Double-Faced Cards, split cards, or flip cards), inspects card_faces[0].
        """
        if "image_uris" in card_data and card_data["image_uris"]:
            return card_data["image_uris"].get("normal") or card_data["image_uris"].get("large")

        if "card_faces" in card_data and isinstance(card_data["card_faces"], list):
            front = card_data["card_faces"][0]
            if "image_uris" in front and front["image_uris"]:
                return front["image_uris"].get("normal") or front["image_uris"].get("large")

        return None

    def _parse_prices(self, prices: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
        """Parses price fields into floating point representations."""
        result: Dict[str, Optional[float]] = {
            "market": None,
            "foil": None,
            "etched": None,
            "low": None
        }
        if not prices:
            return result

        mapping = [("usd", "market"), ("usd_foil", "foil"), ("usd_etched", "etched")]
        for src_key, dest_key in mapping:
            val = prices.get(src_key)
            if val is not None:
                try:
                    result[dest_key] = float(val)
                except (ValueError, TypeError):
                    result[dest_key] = None

        return result

    def _normalize(self, raw: Dict[str, Any], download_image: bool = False) -> NormalizedCard:
        """Transforms raw Scryfall payload into standardized NormalizedCard."""
        card_id = raw.get("id", "")
        remote_image = self._extract_image_uri(raw)
        cached_image = None

        if download_image and remote_image:
            cached_image = self.cache_card_image(remote_image, card_id)

        parsed_prices = self._parse_prices(raw.get("prices"))

        # Preserve game-specific details inside the polymorphic api_metadata column
        extended_meta = {
            "scryfall_uri": raw.get("scryfall_uri"),
            "mana_cost": raw.get("mana_cost"),
            "cmc": raw.get("cmc"),
            "type_line": raw.get("type_line"),
            "oracle_text": raw.get("oracle_text"),
            "colors": raw.get("colors", []),
            "color_identity": raw.get("color_identity", []),
            "keywords": raw.get("keywords", []),
            "legalities": raw.get("legalities", {}),
            "layout": raw.get("layout"),
            "finishes": raw.get("finishes", []),
            "lang": raw.get("lang", "en"),
            "artist": raw.get("artist")
        }

        return NormalizedCard(
            provider_card_id=card_id,
            game="mtg",
            name=raw.get("name", "Unknown Card"),
            clean_name=self._sanitize_name(raw.get("name", "")),
            set_code=raw.get("set", "").lower(),
            set_name=raw.get("set_name", ""),
            collector_number=raw.get("collector_number", "").strip(),
            rarity=raw.get("rarity", "common").lower(),
            image_uri=remote_image,
            cached_image_path=cached_image,
            market_price=parsed_prices["market"],
            low_price=parsed_prices["low"],
            foil_price=parsed_prices["foil"],
            etched_price=parsed_prices["etched"],
            api_metadata=extended_meta
        )

    def search_cards(self, query: str, page: int = 1, download_images: bool = False) -> List[NormalizedCard]:
        """Queries Scryfall card catalog using full Scryfall syntax support."""
        res = self._get("cards/search", params={"q": query, "page": page})
        if not res or "data" not in res:
            return []
        return [self._normalize(card, download_image=download_images) for card in res["data"]]

    def get_card_by_id(self, provider_card_id: str, download_image: bool = False) -> Optional[NormalizedCard]:
        """Resolves card details via Scryfall UUID."""
        res = self._get(f"cards/{provider_card_id}")
        if not res:
            return None
        return self._normalize(res, download_image=download_image)

    def get_card_by_collector_number(
        self,
        set_code: str,
        collector_number: str,
        lang: str = "en",
        download_image: bool = False
    ) -> Optional[NormalizedCard]:
        """Resolves card details via set code and collector number."""
        endpoint = f"cards/{set_code.lower().strip()}/{collector_number.strip()}"
        if lang and lang != "en":
            endpoint += f"/{lang.lower().strip()}"
        res = self._get(endpoint)
        if not res:
            return None
        return self._normalize(res, download_image=download_image)

    def fetch_market_prices(self, provider_card_id: str) -> Dict[str, Optional[float]]:
        """Pulls current pricing indexes directly from Scryfall."""
        card = self.get_card_by_id(provider_card_id, download_image=False)
        if not card:
            return {"market": None, "foil": None, "etched": None, "low": None}
        return {
            "market": card.market_price,
            "foil": card.foil_price,
            "etched": card.etched_price,
            "low": card.low_price
        }
