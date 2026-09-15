"""
OpenPOS-TCG Addon
File: models.py
Addon ID: tcg_pos

Polymorphic SQLAlchemy models for singles inventory, buylist tracking,
and market pricing persistence across SQLite and PostgreSQL engines.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
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
    CheckConstraint,
    create_engine
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# Declarative Base for OpenPOS-TCG models
Base = declarative_base()

# Cross-dialect JSON mapping: JSONB on PostgreSQL, standard JSON on SQLite
PolymorphicJSON = JSON().with_variant(JSONB, "postgresql")

_SESSION_FACTORY = None


def init_db(engine=None, database_uri: Optional[str] = None):
    """
    Initializes database tables and creates the session factory.
    Safely creates tables if they do not yet exist.
    """
    global _SESSION_FACTORY
    if engine is None:
        if database_uri:
            engine = create_engine(database_uri, echo=False)
        else:
            from pathlib import Path
            data_dir = Path.cwd() / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "openpos.db"
            engine = create_engine(f"sqlite:///{db_path.as_posix()}", echo=False)
    Base.metadata.create_all(engine)
    _SESSION_FACTORY = sessionmaker(bind=engine)
    return engine


def get_db_session(engine=None):
    """
    Returns an active SQLAlchemy session.
    Allows passing an explicit engine (e.g. for in-memory testing).
    """
    global _SESSION_FACTORY
    if engine is not None:
        return sessionmaker(bind=engine)()
    if _SESSION_FACTORY is None:
        init_db()
    return _SESSION_FACTORY()


class SinglesInventory(Base):
    """
    Polymorphic singles inventory entity.
    Tracks physical card condition, finishes, average cost basis, and active POS pricing.
    Differentiates inventory items by composite tuple: (game, provider_card_id, finish, condition).
    """
    __tablename__ = "singles_inventory"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Core Card Classification
    game = Column(String(32), nullable=False, default="mtg")
    provider_card_id = Column(String(64), nullable=False)
    name = Column(String(255), nullable=False)
    clean_name = Column(String(255), nullable=False)
    set_code = Column(String(32), nullable=False)
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

    # Visual Asset References
    image_path = Column(String(512), nullable=True)                  # Local cached path (data/cache/tcg_art/...)
    image_uri = Column(String(512), nullable=True)                   # Upstream CDN fallback

    # Game-Specific Polymorphic Attributes (Mana, Type, Legalities, Finishes, HP, etc.)
    api_metadata = Column(PolymorphicJSON, nullable=False, default=dict)

    # Audit Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        # Prevent inventory duplication: one SKU record per exact finish + condition combination
        UniqueConstraint(
            "game",
            "provider_card_id",
            "finish",
            "condition",
            name="uq_single_game_card_finish_cond"
        ),
        # Prevent negative inventory quantities
        CheckConstraint("quantity >= 0", name="chk_singles_qty_positive"),
        # Query optimization indexes matching migration DDL
        Index("ix_singles_game_card", "game", "provider_card_id"),
        Index("ix_singles_lookup", "game", "clean_name", "set_code"),
        Index("ix_singles_set_code", "set_code"),
    )

    def to_dict(self) -> Dict[str, Any]:
        """
        Serializes model instance into a JSON-serializable dictionary
        for OpenPOS Flask blueprints, web APIs, and front-end state.
        """
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
            "api_metadata": self.api_metadata if isinstance(self.api_metadata, dict) else {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }
