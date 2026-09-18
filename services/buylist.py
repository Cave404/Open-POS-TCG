"""
OpenPOS-TCG Addon
File: services/buylist.py
Addon ID: tcg_pos

Buylist & Trade-in Pricing Engine.
Modernized, modularized, and sanitized service for evaluating customer trade-in
offers. Supports customizable cash margins, store credit bonuses, and physical
card condition degradation curves.
"""

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
import math
from typing import Any, Dict, List, Optional, Union

from providers.base import NormalizedCard


# Standard collectible card condition multipliers (OpenPOS v1.0.1 donor standards)
DEFAULT_CONDITION_MULTIPLIERS: Dict[str, float] = {
    "NM": 1.00,  # Near Mint: pristine, pack-fresh
    "LP": 0.85,  # Lightly Played: minor edge wear or faint scratches
    "MP": 0.70,  # Moderately Played: visible whitening, minor creases
    "HP": 0.50,  # Heavily Played: heavy wear, major whitening, micro-creases
    "DMG": 0.30   # Damaged: structural damage, severe bends, tears, ink marks
}

# Synonyms and grading tier aliases mapped to standard TCG abbreviations
CONDITION_ALIASES: Dict[str, str] = {
    "NEAR MINT": "NM",
    "MINT": "NM",
    "M": "NM",
    "LIGHT PLAY": "LP",
    "LIGHTLY PLAYED": "LP",
    "SLIGHTLY PLAYED": "LP",
    "SP": "LP",
    "EX": "LP",
    "EXCELLENT": "LP",
    "MODERATE PLAY": "MP",
    "MODERATELY PLAYED": "MP",
    "PLAYED": "MP",
    "PL": "MP",
    "GD": "MP",
    "GOOD": "MP",
    "HEAVY PLAY": "HP",
    "HEAVILY PLAYED": "HP",
    "POOR": "DMG",
    "DAMAGED": "DMG"
}


@dataclass
class BuylistOffer:
    """
    Evaluated buylist valuation for a single card variant.
    Includes rate breakdowns, condition adjustments, and unit/total financial totals.
    """
    card_name: str
    game: str
    condition: str
    finish: str
    quantity: int
    raw_market_price: float
    applicable_market_price: float
    condition_multiplier: float
    adjusted_market_value: float
    cash_percentage: float
    credit_percentage: float
    unit_cash_offer: float
    unit_credit_offer: float
    total_cash_offer: float
    total_credit_offer: float

    def to_dict(self) -> Dict[str, Any]:
        """Converts offer instance into JSON-friendly dictionary."""
        return {
            "card_name": self.card_name,
            "game": self.game,
            "condition": self.condition,
            "finish": self.finish,
            "quantity": self.quantity,
            "raw_market_price": self.raw_market_price,
            "applicable_market_price": self.applicable_market_price,
            "condition_multiplier": self.condition_multiplier,
            "adjusted_market_value": self.adjusted_market_value,
            "cash_percentage": self.cash_percentage,
            "credit_percentage": self.credit_percentage,
            "unit_cash_offer": self.unit_cash_offer,
            "unit_credit_offer": self.unit_credit_offer,
            "total_cash_offer": self.total_cash_offer,
            "total_credit_offer": self.total_credit_offer
        }


@dataclass
class BuylistBatchResult:
    """
    Consolidated summary and itemized collection of evaluated buylist items.
    """
    offers: List[BuylistOffer] = field(default_factory=list)
    total_quantity: int = 0
    total_cash: float = 0.0
    total_credit: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Serializes batch results for API transmission."""
        return {
            "offers": [offer.to_dict() for offer in self.offers],
            "total_quantity": self.total_quantity,
            "total_cash": self.total_cash,
            "total_credit": self.total_credit
        }


class BuylistCalculator:
    """
    Core buylist and trade-in pricing engine.
    Calculates instant cash and store credit buy offers for singles inventory.
    """

    def __init__(
        self,
        default_cash_percentage: Optional[float] = None,
        credit_bonus_percentage: Optional[float] = None,
        credit_percentage: Optional[float] = None,
        condition_multipliers: Optional[Dict[str, float]] = None,
        minimum_cash_offer: float = 0.0,
        minimum_credit_offer: float = 0.0
    ):
        """
        Initialize the pricing calculator with store trade margins.
        Pulls default rates dynamically from SettingsService if not explicitly specified.

        :param default_cash_percentage: Base cash payout ratio relative to market value (e.g., 0.60 = 60%).
        :param credit_bonus_percentage: Trade-in credit bonus relative to cash offer (e.g., 0.30 = +30% over cash).
        :param credit_percentage: Optional direct credit payout ratio relative to market value (overrides bonus).
        :param condition_multipliers: Dict mapping condition codes (NM, LP, MP, HP, DMG) to value multipliers.
        :param minimum_cash_offer: Absolute minimum floor for any cash offer.
        :param minimum_credit_offer: Absolute minimum floor for any store credit offer.
        """
        # Resolve dynamic cash payout rate from settings if not explicitly provided
        if default_cash_percentage is None:
            try:
                from services.settings_service import get_setting
                cfg_cash = get_setting("cash_payout_rate", 60.0)
                default_cash_percentage = (float(cfg_cash) / 100.0) if float(cfg_cash) > 1.0 else float(cfg_cash)
            except Exception:
                default_cash_percentage = 0.60

        if not (0.0 <= float(default_cash_percentage) <= 1.0):
            raise ValueError(f"default_cash_percentage must be between 0.0 and 1.0, got {default_cash_percentage}")

        # Resolve dynamic store credit payout rate from settings if no explicit credit parameters passed
        if credit_percentage is None and credit_bonus_percentage is None:
            try:
                from services.settings_service import get_setting
                cfg_credit = get_setting("store_credit_payout_rate", 80.0)
                credit_percentage = (float(cfg_credit) / 100.0) if float(cfg_credit) > 1.0 else float(cfg_credit)
            except Exception:
                credit_percentage = 0.80

        self.default_cash_percentage = float(default_cash_percentage)
        self.credit_bonus_percentage = float(credit_bonus_percentage) if credit_bonus_percentage is not None else 0.30
        self.credit_percentage = float(credit_percentage) if credit_percentage is not None else None

        # Build condition multipliers map from settings or defaults
        if condition_multipliers is None:
            try:
                from services.settings_service import get_setting
                cfg_conds = get_setting("condition_multipliers", DEFAULT_CONDITION_MULTIPLIERS)
                if isinstance(cfg_conds, dict) and cfg_conds:
                    condition_multipliers = cfg_conds
            except Exception:
                pass

        self.condition_multipliers = dict(DEFAULT_CONDITION_MULTIPLIERS)
        if condition_multipliers:
            for cond, mult in condition_multipliers.items():
                self.condition_multipliers[cond.strip().upper()] = float(mult)

        self.minimum_cash_offer = max(0.0, float(minimum_cash_offer))
        self.minimum_credit_offer = max(0.0, float(minimum_credit_offer))

    @staticmethod
    def _round_currency(value: float) -> float:
        """Rounds float safely using banker's rounding or standard half-up to 2 decimals."""
        d = Decimal(str(round(value, 4)))
        return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def normalize_condition(self, condition: str) -> str:
        """
        Sanitizes and resolves condition strings into standard canonical representations
        (NM, LP, MP, HP, DMG).
        """
        cleaned = condition.strip().upper()
        if cleaned in self.condition_multipliers:
            return cleaned
        if cleaned in CONDITION_ALIASES:
            return CONDITION_ALIASES[cleaned]
        # Fallback to NM if completely unrecognized
        return "NM"

    def get_condition_multiplier(self, condition: str) -> float:
        """Returns the valuation multiplier for a given card condition code."""
        canonical = self.normalize_condition(condition)
        return self.condition_multipliers.get(canonical, 1.00)

    def resolve_finish_price(
        self,
        market_price: Optional[float],
        foil_price: Optional[float] = None,
        etched_price: Optional[float] = None,
        finish: str = "nonfoil"
    ) -> float:
        """
        Extracts the relevant baseline price depending on card finish variant.
        Falls back to regular market price if specialty finish prices are unrecorded.
        """
        norm_finish = finish.strip().lower()

        if norm_finish in ("foil", "reverse_holo", "holo"):
            if foil_price is not None and foil_price > 0.0:
                return float(foil_price)
            if market_price is not None and market_price > 0.0:
                return float(market_price)
            return 0.0

        if norm_finish in ("etched", "textured"):
            if etched_price is not None and etched_price > 0.0:
                return float(etched_price)
            if foil_price is not None and foil_price > 0.0:
                return float(foil_price)
            if market_price is not None and market_price > 0.0:
                return float(market_price)
            return 0.0

        # Regular non-foil default
        if market_price is not None and market_price > 0.0:
            return float(market_price)

        return 0.0

    def get_applicable_price(self, card: NormalizedCard, finish: str = "nonfoil") -> float:
        """Resolves the active market baseline price for a NormalizedCard instance."""
        return self.resolve_finish_price(
            market_price=card.market_price,
            foil_price=card.foil_price,
            etched_price=card.etched_price,
            finish=finish
        )

    def calculate_offer_from_price(
        self,
        market_price: float,
        condition: str = "NM",
        finish: str = "nonfoil",
        quantity: int = 1,
        card_name: str = "Unknown Card",
        game: str = "mtg",
        cash_percentage: Optional[float] = None,
        credit_bonus_percentage: Optional[float] = None
    ) -> BuylistOffer:
        """
        Calculates a buylist offer directly from a known market price float.

        :param market_price: Raw baseline market price.
        :param condition: Condition string (NM, LP, MP, HP, DMG).
        :param finish: Finish designation (nonfoil, foil, etched).
        :param quantity: Number of identical units.
        :param card_name: Descriptive card name.
        :param game: Game slug ('mtg', 'pokemon').
        :param cash_percentage: Optional override for cash payout percentage.
        :param credit_bonus_percentage: Optional override for credit bonus percentage.
        :return: Populated BuylistOffer instance.
        """
        if quantity < 1:
            raise ValueError(f"Quantity must be at least 1, got {quantity}")

        safe_market_price = max(0.0, float(market_price))
        canonical_cond = self.normalize_condition(condition)
        cond_mult = self.get_condition_multiplier(canonical_cond)

        # Apply condition degradation
        adjusted_market_value = self._round_currency(safe_market_price * cond_mult)

        # Cash payout calculation
        cash_rate = (
            float(cash_percentage)
            if cash_percentage is not None
            else self.default_cash_percentage
        )
        raw_cash_offer = adjusted_market_value * cash_rate
        unit_cash = self._round_currency(max(self.minimum_cash_offer, raw_cash_offer))

        # Credit payout calculation
        if self.credit_percentage is not None and cash_percentage is None:
            # Explicit credit percentage of market
            effective_credit_rate = self.credit_percentage
            raw_credit_offer = adjusted_market_value * effective_credit_rate
            unit_credit = self._round_currency(max(self.minimum_credit_offer, raw_credit_offer))
        else:
            # Credit bonus over cash
            effective_bonus = (
                float(credit_bonus_percentage)
                if credit_bonus_percentage is not None
                else self.credit_bonus_percentage
            )
            effective_credit_rate = cash_rate * (1.0 + effective_bonus)
            raw_credit_offer = unit_cash * (1.0 + effective_bonus)
            unit_credit = self._round_currency(max(self.minimum_credit_offer, raw_credit_offer))

        total_cash = self._round_currency(unit_cash * quantity)
        total_credit = self._round_currency(unit_credit * quantity)

        return BuylistOffer(
            card_name=card_name,
            game=game,
            condition=canonical_cond,
            finish=finish.strip().lower(),
            quantity=quantity,
            raw_market_price=safe_market_price,
            applicable_market_price=safe_market_price,
            condition_multiplier=cond_mult,
            adjusted_market_value=adjusted_market_value,
            cash_percentage=round(cash_rate, 4),
            credit_percentage=round(effective_credit_rate, 4),
            unit_cash_offer=unit_cash,
            unit_credit_offer=unit_credit,
            total_cash_offer=total_cash,
            total_credit_offer=total_credit
        )

    def calculate_offer_from_card(
        self,
        card: NormalizedCard,
        condition: str = "NM",
        finish: str = "nonfoil",
        quantity: int = 1,
        cash_percentage: Optional[float] = None,
        credit_bonus_percentage: Optional[float] = None
    ) -> BuylistOffer:
        """
        Calculates a buylist offer from an upstream NormalizedCard entity,
        resolving the correct variant market price automatically.
        """
        applicable_price = self.get_applicable_price(card, finish=finish)

        offer = self.calculate_offer_from_price(
            market_price=applicable_price,
            condition=condition,
            finish=finish,
            quantity=quantity,
            card_name=card.name,
            game=card.game,
            cash_percentage=cash_percentage,
            credit_bonus_percentage=credit_bonus_percentage
        )
        # Retain raw unadjusted market price if different
        offer.raw_market_price = float(card.market_price or 0.0)
        offer.applicable_market_price = applicable_price
        return offer

    def calculate_offer(
        self,
        card_or_price: Union[NormalizedCard, float, int],
        condition: str = "NM",
        finish: str = "nonfoil",
        quantity: int = 1,
        **kwargs: Any
    ) -> BuylistOffer:
        """
        Unified dispatch method supporting both NormalizedCard entities
        and raw market price numbers.
        """
        if isinstance(card_or_price, NormalizedCard):
            return self.calculate_offer_from_card(
                card=card_or_price,
                condition=condition,
                finish=finish,
                quantity=quantity,
                **kwargs
            )
        else:
            return self.calculate_offer_from_price(
                market_price=float(card_or_price),
                condition=condition,
                finish=finish,
                quantity=quantity,
                **kwargs
            )

    def calculate_batch(self, items: List[Union[Dict[str, Any], NormalizedCard]]) -> BuylistBatchResult:
        """
        Processes a list of card items, aggregating individual lines into a single trade-in batch.

        Each item in `items` may be a dictionary with keys:
        `{'card': NormalizedCard}` or `{'market_price': float}`, along with optional
        `'condition'`, `'finish'`, `'quantity'`, `'card_name'`, `'game'`.
        """
        offers: List[BuylistOffer] = []
        total_qty = 0
        total_cash = 0.0
        total_credit = 0.0

        for item in items:
            if isinstance(item, NormalizedCard):
                offer = self.calculate_offer_from_card(item)
            elif isinstance(item, dict):
                card = item.get("card")
                price = item.get("market_price")
                condition = item.get("condition", "NM")
                finish = item.get("finish", "nonfoil")
                quantity = int(item.get("quantity", 1))

                if card is not None:
                    offer = self.calculate_offer_from_card(
                        card=card,
                        condition=condition,
                        finish=finish,
                        quantity=quantity
                    )
                elif price is not None:
                    card_name = item.get("card_name", "Unknown Card")
                    game = item.get("game", "mtg")
                    offer = self.calculate_offer_from_price(
                        market_price=float(price),
                        condition=condition,
                        finish=finish,
                        quantity=quantity,
                        card_name=card_name,
                        game=game
                    )
                else:
                    continue
            else:
                continue

            offers.append(offer)
            total_qty += offer.quantity
            total_cash += offer.total_cash_offer
            total_credit += offer.total_credit_offer

        return BuylistBatchResult(
            offers=offers,
            total_quantity=total_qty,
            total_cash=self._round_currency(total_cash),
            total_credit=self._round_currency(total_credit)
        )
