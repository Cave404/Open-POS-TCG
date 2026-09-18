"""
OpenPOS-TCG Addon
File: providers/pokemon_tcgdex.py
Addon ID: tcg_pos

Pokémon Trading Card Game driver utilizing the public TCGdex REST API (v2).
Provides catalog search, collector number lookup, variant finish mapping,
and gameplay metadata normalization.
"""

from pathlib import Path
import re
from typing import Any, Dict, List, Optional
from .base import BaseTCGProvider, NormalizedCard


class PokemonTCGdexProvider(BaseTCGProvider):
    """
    Production Pokémon TCG driver interfacing with TCGdex REST API:
    - 100ms polite inter-request delay.
    - Resolves high-resolution card art (.webp / .png).
    - Maps card finishes (nonfoil, reverse_holo, holo, first_edition).
    - Extracts Cardmarket & TCGPlayer pricing benchmarks.
    - Normalizes Pokémon gameplay attributes (HP, Stage, Types, Attacks, Retreat)
      into polymorphic api_metadata column.
    """

    def __init__(
        self,
        user_agent: str = "OpenPOS-TCG/1.0.1 (TCGdex Engine; +https://github.com/OpenPOS-Platform/Open-POS-TCG)",
        custom_data_dir: Optional[Path] = None
    ):
        super().__init__(
            game_slug="pokemon",
            base_url="https://api.tcgdex.net/v2/en",
            rate_limit_delay=0.1,  # 100ms polite spacing
            user_agent=user_agent,
            custom_data_dir=custom_data_dir
        )
        self.session.headers.update({
            "Accept": "application/json"
        })

    def _sanitize_name(self, raw_name: str) -> str:
        """Strips non-alphanumerics for fast localized indexing."""
        return re.sub(r"[^\w\s]", "", raw_name or "").strip().lower()

    def _extract_image_uri(self, card_data: Dict[str, Any]) -> Optional[str]:
        """
        Resolves high-resolution card artwork from TCGdex asset CDN.
        TCGdex v2 base URLs omit extensions; appending /high.webp provides
        optimal fidelity for desktop displays.
        """
        raw_img = card_data.get("image")
        if not raw_img:
            return None

        # If extension is already present
        if any(raw_img.lower().endswith(ext) for ext in (".webp", ".png", ".jpg", ".jpeg")):
            return raw_img

        return f"{raw_img.rstrip('/')}/high.webp"

    def _extract_finishes(self, variants: Optional[Dict[str, Any]]) -> List[str]:
        """
        Maps TCGdex variants boolean map into standardized finish identifiers:
        ['nonfoil', 'reverse_holo', 'holo', 'first_edition'].
        """
        finishes: List[str] = []
        if not variants:
            return ["nonfoil"]

        if variants.get("normal", False):
            finishes.append("nonfoil")
        if variants.get("reverse", False):
            finishes.append("reverse_holo")
        if variants.get("holo", False):
            finishes.append("holo")
        if variants.get("firstEdition", False):
            finishes.append("first_edition")

        if not finishes:
            finishes.append("nonfoil")

        return finishes

    def _parse_prices(self, card_data: Dict[str, Any]) -> Dict[str, Optional[float]]:
        """
        Extracts regular, low, reverse-holo, and holofoil market prices from
        Cardmarket and TCGplayer payloads.
        """
        result: Dict[str, Optional[float]] = {
            "market": None,
            "low": None,
            "foil": None,
            "etched": None
        }

        # 1. Inspect Cardmarket pricing structure
        cardmarket = card_data.get("cardmarket") or {}
        cm_prices = cardmarket.get("prices") or {}
        if cm_prices:
            avg_price = cm_prices.get("averageSellPrice") or cm_prices.get("trendPrice")
            low_price = cm_prices.get("lowPrice")
            rev_price = cm_prices.get("reverseHoloAvg") or cm_prices.get("reverseHoloTrend")

            if avg_price is not None:
                try: result["market"] = float(avg_price)
                except (ValueError, TypeError): pass
            if low_price is not None:
                try: result["low"] = float(low_price)
                except (ValueError, TypeError): pass
            if rev_price is not None:
                try: result["foil"] = float(rev_price)
                except (ValueError, TypeError): pass

            if result["market"] is not None or result["foil"] is not None:
                return result

        # 2. Inspect TCGPlayer pricing structure
        tcgplayer = card_data.get("tcgplayer") or {}
        tcg_prices = tcgplayer.get("prices") or {}
        if tcg_prices:
            normal = tcg_prices.get("normal") or {}
            reverse = tcg_prices.get("reverseHolofoil") or {}
            holo = tcg_prices.get("holofoil") or {}

            norm_market = normal.get("market") or normal.get("mid")
            norm_low = normal.get("low")
            foil_market = reverse.get("market") or holo.get("market") or reverse.get("mid") or holo.get("mid")

            if norm_market is not None:
                try: result["market"] = float(norm_market)
                except (ValueError, TypeError): pass
            if norm_low is not None:
                try: result["low"] = float(norm_low)
                except (ValueError, TypeError): pass
            if foil_market is not None:
                try: result["foil"] = float(foil_market)
                except (ValueError, TypeError): pass

            if result["market"] is not None or result["foil"] is not None:
                return result

        # 3. Top-level pricing fallbacks if passed directly
        direct_prices = card_data.get("prices") or {}
        if direct_prices:
            for k in ("market", "low", "foil", "etched"):
                if direct_prices.get(k) is not None:
                    try: result[k] = float(direct_prices[k])
                    except (ValueError, TypeError): pass

        return result

    def _normalize(self, raw: Dict[str, Any], download_image: bool = False) -> NormalizedCard:
        """
        Transforms raw TCGdex card JSON into unified NormalizedCard schema.
        Populates api_metadata with Pokémon gameplay attributes (HP, Stage, Attacks).
        """
        card_id = str(raw.get("id", "")).strip()

        # Set code extraction: check set object or parse from id (e.g. 'swsh3-136' -> 'swsh3')
        set_obj = raw.get("set")
        if isinstance(set_obj, dict):
            set_code = str(set_obj.get("id", "")).strip().lower()
            set_name = str(set_obj.get("name", "")).strip()
        elif "-" in card_id:
            set_code = card_id.split("-")[0].strip().lower()
            set_name = set_code.upper()
        else:
            set_code = str(raw.get("set_code", "")).strip().lower()
            set_name = set_code.upper()

        # Collector number: check localId, or parse from id
        collector_num = str(raw.get("localId") or "").strip()
        if not collector_num and "-" in card_id:
            collector_num = card_id.split("-")[1].strip()

        rarity_str = str(raw.get("rarity", "Common")).strip().lower()
        remote_image = self._extract_image_uri(raw)
        cached_image = None

        if download_image and remote_image:
            cached_image = self.cache_card_image(remote_image, card_id)

        parsed_prices = self._parse_prices(raw)
        finishes_list = self._extract_finishes(raw.get("variants"))

        # Pokémon Gameplay Attributes mapped into polymorphic api_metadata column
        extended_meta = {
            "hp": raw.get("hp"),
            "stage": raw.get("stage"),
            "types": raw.get("types", []),
            "evolveFrom": raw.get("evolveFrom"),
            "attacks": raw.get("attacks", []),
            "weaknesses": raw.get("weaknesses", []),
            "resistances": raw.get("resistances", []),
            "retreat": raw.get("retreat"),
            "legal": raw.get("legal", {}),
            "variants": raw.get("variants", {}),
            "finishes": finishes_list,
            "illustrator": raw.get("illustrator"),
            "category": raw.get("category", "Pokemon"),
            "description": raw.get("description")
        }

        return NormalizedCard(
            provider_card_id=card_id,
            game="pokemon",
            name=raw.get("name", "Unknown Pokémon Card"),
            clean_name=self._sanitize_name(raw.get("name", "")),
            set_code=set_code,
            set_name=set_name or set_code.upper(),
            collector_number=collector_num,
            rarity=rarity_str,
            image_uri=remote_image,
            cached_image_path=cached_image,
            market_price=parsed_prices["market"],
            low_price=parsed_prices["low"],
            foil_price=parsed_prices["foil"],
            etched_price=parsed_prices["etched"],
            api_metadata=extended_meta
        )

    def search_cards(self, query: str, page: int = 1, download_images: bool = False) -> List[NormalizedCard]:
        """
        Searches TCGdex card catalog matching query string against card names.
        """
        if not query:
            return []

        res = self._get("cards", params={"name": query.strip()})
        if not res or not isinstance(res, list):
            return []

        # TCGdex /cards?name= returns array of matching cards
        # Limit or paginate slice
        page_size = 25
        start_idx = max(0, (page - 1) * page_size)
        sliced = res[start_idx : start_idx + page_size]

        results: List[NormalizedCard] = []
        for card_data in sliced:
            if isinstance(card_data, dict):
                results.append(self._normalize(card_data, download_image=download_images))

        return results

    def get_card_by_id(self, provider_card_id: str, download_image: bool = False) -> Optional[NormalizedCard]:
        """
        Resolves detailed card entity via TCGdex unique identifier (e.g. 'swsh3-136').
        """
        clean_id = provider_card_id.strip()
        res = self._get(f"cards/{clean_id}")
        if not res or not isinstance(res, dict) or "id" not in res:
            return None

        return self._normalize(res, download_image=download_image)

    def get_card_by_collector_number(
        self,
        set_code: str,
        collector_number: str,
        lang: str = "en",
        download_image: bool = False
    ) -> Optional[NormalizedCard]:
        """
        Resolves card details via set abbreviation and collector number.
        In TCGdex, IDs are standardly formatted as: {set_code}-{collector_number}.
        """
        clean_set = set_code.lower().strip()
        clean_num = collector_number.strip()

        # Try standard primary key format: e.g. swsh3-136
        target_id = f"{clean_set}-{clean_num}"
        card = self.get_card_by_id(target_id, download_image=download_image)
        if card:
            return card

        # Try stripped leading zeros (e.g. '025' -> '25')
        num_no_zeros = clean_num.lstrip("0")
        if num_no_zeros and num_no_zeros != clean_num:
            card = self.get_card_by_id(f"{clean_set}-{num_no_zeros}", download_image=download_image)
            if card:
                return card

        # Try query filter by set
        set_cards = self._get("cards", params={"set": clean_set})
        if set_cards and isinstance(set_cards, list):
            for item in set_cards:
                if isinstance(item, dict) and str(item.get("localId", "")).strip() in (clean_num, num_no_zeros):
                    full_id = item.get("id")
                    if full_id:
                        return self.get_card_by_id(full_id, download_image=download_image)
                    return self._normalize(item, download_image=download_image)

        return None

    def fetch_market_prices(self, provider_card_id: str) -> Dict[str, Optional[float]]:
        """
        Pulls current market pricing benchmarks directly for a Pokémon card.
        """
        card = self.get_card_by_id(provider_card_id, download_image=False)
        if not card:
            return {"market": None, "foil": None, "etched": None, "low": None}

        return {
            "market": card.market_price,
            "foil": card.foil_price,
            "etched": card.etched_price,
            "low": card.low_price
        }
