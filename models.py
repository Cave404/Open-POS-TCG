"""
OpenPOS-TCG Addon
File: models.py
Addon ID: tcg_pos

Polymorphic SQLAlchemy models for singles inventory, buylist tracking,
and market pricing persistence across SQLite and PostgreSQL engines.
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Text,
    JSON,
    UniqueConstraint,
    Index,
    CheckConstraint
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

# Fallback Base if not imported from OpenPOS core
Base = declarative_base()

# Cross-dialect JSON mapping: JSONB on PostgreSQL, JSON on SQLite
PolymorphicJSON = JSON().with_variant(JSONB, "postgresql")


class SinglesInventory(Base):
    """
    Polymorphic singles inventory item.
    Tracks physical condition, finishes, average cost basis, and active POS pricing.
    """
    __tablename__ = "singles_inventory"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Core Card Classification
    game = Column(String(32), nullable=False, default="mtg", index=True)
    provider_card_id = Column(String(64), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    clean_name = Column(String(255), nullable=False, index=True)
    set_code = Column(String(32), nullable=False, index=True)
    set_name = Column(String(255), nullable=False)
    collector_number = Column(String(32), nullable=False)
    rarity = Column(String(32), nullable=False)

    # Physical Variation & Retail Attributes
    finish = Column(String(32), nullable=False, default="nonfoil")   # nonfoil, foil, etched, reverse_holo
    condition = Column(String(8), nullable=False, default="NM")      # NM, LP, MP, HP, DMG
    quantity = Column(Integer, nullable=False, default=0)

    # Accounting & Price Points
    cost_basis = Column(Float, nullable=False, default=0.0)          # Buylist acquisition cost per unit
    sell_price = Column(Float, nullable=False, default=0.0)          # Live POS register shelf price
    market_price = Column(Float, nullable=True)                      # Scryfall/TCGdex regular market rate
    low_price = Column(Float, nullable=True)                         # Upstream low floor
    foil_price = Column(Float, nullable=True)                        # Upstream foil market rate
    etched_price = Column(Float, nullable=True)                      # Upstream etched rate

    # Asset Reference
    image_path = Column(String(512), nullable=True)                  # Local cached path (data/cache/tcg_art/...)
    image_uri = Column(String(512), nullable=True)                   # Upstream CDN fallback

    # Game-Specific Polymorphic Attributes (Mana, Type, Legalities, HP, etc.)
    api_metadata = Column(PolymorphicJSON, nullable=False, default=dict)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        # Prevent inventory duplication: one SKU record per exact condition + finish combination
        UniqueConstraint(
            "game",
            "provider_card_id",
            "finish",
            "condition",
            name="uq_single_game_card_finish_cond"
        ),
        CheckConstraint("quantity >= 0", name="chk_singles_qty_positive"),
        Index("ix_singles_lookup", "game", "clean_name", "set_code"),
    )

    def to_dict(self) -> dict:
        """Serializes model instance for JSON transmission in OpenPOS blueprints."""
        return {
            "id": self.id,
            "game": self.game,
            "provider_card_id": self.provider_card_id,
            "name": self.name,
            "clean_name": self.clean_name,
            "set_code": self.set_code,
            "set_name": self.set_name,
            "collector_number": self.collector_number,
            "rarity": self.rarity,
            "finish": self.finish,
            "condition": self.condition,
            "quantity": self.quantity,
            "cost_basis": self.cost_basis,
            "sell_price": self.sell_price,
            "market_price": self.market_price,
            "low_price": self.low_price,
            "foil_price": self.foil_price,
            "etched_price": self.etched_price,
            "image_path": self.image_path,
            "image_uri": self.image_uri,
            "api_metadata": self.api_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }
