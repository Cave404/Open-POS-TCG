"""
OpenPOS-TCG Addon
File: models.py
Addon ID: tcg_pos

Polymorphic SQLAlchemy models for singles inventory, buylist tracking,
and market pricing persistence across SQLite and PostgreSQL engines.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Generator, Optional
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
    ForeignKey,
    create_engine
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship

# Declarative Base for OpenPOS-TCG models
Base = declarative_base()

# Cross-dialect JSON mapping: JSONB on PostgreSQL, standard JSON on SQLite
PolymorphicJSON = JSON().with_variant(JSONB, "postgresql")

_SESSION_FACTORY = None


def init_db(engine=None, database_uri: Optional[str] = None):
    """
    Initializes database tables and creates the session factory.
    Safely creates tables if they do not yet exist.
    Configures polite busy timeouts on SQLite to prevent lock contention.
    """
    global _SESSION_FACTORY
    if engine is None:
        if database_uri:
            connect_args = {"timeout": 30.0} if database_uri.startswith("sqlite") else {}
            engine = create_engine(database_uri, echo=False, connect_args=connect_args)
        else:
            from pathlib import Path
            data_dir = Path.cwd() / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "openpos.db"
            engine = create_engine(
                f"sqlite:///{db_path.as_posix()}",
                echo=False,
                connect_args={"timeout": 30.0}
            )
    Base.metadata.create_all(engine)
    _SESSION_FACTORY = sessionmaker(bind=engine)
    return engine


def get_db_session(engine=None) -> Session:
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


@contextmanager
def db_session_scope(engine=None, session_factory=None) -> Generator[Session, None, None]:
    """
    Context manager providing a short-lived transactional database session.
    Automatically commits on normal block exit, rolls back on exception,
    and unconditionally closes the session to release database connection locks.
    Essential for background worker threads to avoid locking SQLite or PostgreSQL tables
    against concurrent main Flask register/intake threads.
    """
    if session_factory is not None:
        session = session_factory()
    else:
        session = get_db_session(engine=engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


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

    # Hardware & POS Tracking Identifiers
    sku = Column(String(64), unique=True, index=True, nullable=True)
    custom_tag_id = Column(String(128), index=True, nullable=True)

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

    def generate_default_sku(self) -> str:
        """Generates a deterministic SKU if none is explicitly assigned."""
        if self.sku:
            return self.sku
        if self.id:
            return f"TCG-{self.id}"
        finish_code = (self.finish or "n")[:1].lower()
        return f"{self.game}-{self.set_code}-{self.collector_number}-{finish_code}-{self.condition}".upper()

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
            "sku": self.sku or (f"TCG-{self.id}" if self.id else None),
            "custom_tag_id": self.custom_tag_id,
            "image_path": self.image_path,
            "image_uri": self.image_uri,
            "api_metadata": self.api_metadata if isinstance(self.api_metadata, dict) else {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }


class TCGTransaction(Base):
    """
    Header record for a completed POS transaction.
    Captures totals, tax, tender summary, receipt reference, and optional
    hardware outcome metadata (cash drawer, receipt spool).
    """
    __tablename__ = "tcg_transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    receipt_number = Column(String(64), unique=True, nullable=False, index=True)

    # Financial Totals
    subtotal = Column(Float, nullable=False, default=0.0)
    tax_rate = Column(Float, nullable=False, default=0.0)      # e.g. 0.0825 for 8.25%
    tax_amount = Column(Float, nullable=False, default=0.0)
    grand_total = Column(Float, nullable=False, default=0.0)
    total_tendered = Column(Float, nullable=False, default=0.0)
    change_due = Column(Float, nullable=False, default=0.0)

    # Status: 'completed' | 'voided' | 'refunded'
    status = Column(String(32), nullable=False, default="completed")

    # Optional notes / cashier identifier
    cashier_id = Column(String(64), nullable=True)
    notes = Column(Text, nullable=True)

    # Hardware outcome JSON: {cash_drawer: bool, receipt_spooled: bool, errors: [...]}
    hardware_meta = Column(PolymorphicJSON, nullable=False, default=dict)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    items = relationship("TCGTransactionItem", back_populates="transaction",
                         cascade="all, delete-orphan", lazy="selectin")
    tenders = relationship("TCGTransactionTender", back_populates="transaction",
                           cascade="all, delete-orphan", lazy="selectin")

    __table_args__ = (
        Index("ix_tcg_txn_status", "status"),
        Index("ix_tcg_txn_created", "created_at"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "receipt_number": self.receipt_number,
            "subtotal": self.subtotal,
            "tax_rate": self.tax_rate,
            "tax_amount": self.tax_amount,
            "grand_total": self.grand_total,
            "total_tendered": self.total_tendered,
            "change_due": self.change_due,
            "status": self.status,
            "cashier_id": self.cashier_id,
            "notes": self.notes,
            "hardware_meta": self.hardware_meta if isinstance(self.hardware_meta, dict) else {},
            "items": [item.to_dict() for item in (self.items or [])],
            "tenders": [t.to_dict() for t in (self.tenders or [])],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TCGTransactionItem(Base):
    """
    Line item within a TCGTransaction.
    Snapshots inventory state at time of sale (name, SKU, price, COGS) so historical
    records are not affected by future inventory edits.
    """
    __tablename__ = "tcg_transaction_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(Integer, ForeignKey("tcg_transactions.id", ondelete="CASCADE"),
                            nullable=False, index=True)

    # Inventory reference (nullable — item may be deleted later)
    inventory_id = Column(Integer, ForeignKey("singles_inventory.id", ondelete="SET NULL"),
                          nullable=True, index=True)

    # Point-in-time snapshot
    name = Column(String(255), nullable=False)
    sku = Column(String(64), nullable=True)
    game = Column(String(32), nullable=False, default="mtg")
    set_code = Column(String(32), nullable=False)
    condition = Column(String(8), nullable=False)
    finish = Column(String(32), nullable=False)
    quantity_sold = Column(Integer, nullable=False, default=1)
    unit_price = Column(Float, nullable=False, default=0.0)          # Sell price at time of sale
    cost_basis_snapshot = Column(Float, nullable=False, default=0.0) # COGS per unit at time of sale
    line_total = Column(Float, nullable=False, default=0.0)          # unit_price * quantity_sold

    # Relationship back-reference
    transaction = relationship("TCGTransaction", back_populates="items")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "transaction_id": self.transaction_id,
            "inventory_id": self.inventory_id,
            "name": self.name,
            "sku": self.sku,
            "game": self.game,
            "set_code": self.set_code,
            "condition": self.condition,
            "finish": self.finish,
            "quantity_sold": self.quantity_sold,
            "unit_price": self.unit_price,
            "cost_basis_snapshot": self.cost_basis_snapshot,
            "line_total": self.line_total,
        }


class TCGTransactionTender(Base):
    """
    Individual tender row within a TCGTransaction.
    A single transaction may have multiple tender rows (split payment).
    tender_type: 'cash' | 'card' | 'store_credit'
    """
    __tablename__ = "tcg_transaction_tenders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(Integer, ForeignKey("tcg_transactions.id", ondelete="CASCADE"),
                            nullable=False, index=True)

    tender_type = Column(String(32), nullable=False)   # cash | card | store_credit
    amount = Column(Float, nullable=False, default=0.0)
    # change_due only meaningful for cash tenders; 0.0 for card/store_credit
    change_due = Column(Float, nullable=False, default=0.0)

    # Optional: card last-4, auth code, store credit account ref
    reference = Column(String(128), nullable=True)

    # Relationship back-reference
    transaction = relationship("TCGTransaction", back_populates="tenders")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "transaction_id": self.transaction_id,
            "tender_type": self.tender_type,
            "amount": self.amount,
            "change_due": self.change_due,
            "reference": self.reference,
        }
