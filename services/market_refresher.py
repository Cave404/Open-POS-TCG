"""
OpenPOS-TCG Addon
File: services/market_refresher.py
Addon ID: tcg_pos

Dynamic, Modular Market Price Refresher Engine.
Executes non-blocking background price synchronizations across collectible card games,
providing per-game filtering, stale-age thresholding, chunked database commits,
and automatic price drift & volatility protection.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import threading
from typing import Any, Callable, Dict, List, Optional
import uuid

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from models import SinglesInventory, get_db_session
from providers import GAME_PROVIDERS, get_provider


@dataclass
class PriceRefreshJob:
    """
    Execution state and progress tracker for a market price refresh batch.
    """
    job_id: str
    status: str = "idle"  # 'idle', 'running', 'completed', 'failed', 'cancelled'
    game: Optional[str] = None
    in_stock_only: bool = True
    max_age_days: int = 1
    auto_adjust_sell_price: bool = False
    margin_multiplier: float = 1.0

    total_items: int = 0
    processed_items: int = 0
    updated_items: int = 0
    volatility_alerts_count: int = 0
    current_card_name: Optional[str] = None
    errors: List[str] = field(default_factory=list)

    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serializes current job status and metrics for REST clients."""
        progress_pct = 0.0
        if self.total_items > 0:
            progress_pct = round((self.processed_items / self.total_items) * 100.0, 1)

        duration_sec = None
        if self.started_at:
            end_time = self.finished_at or datetime.now(timezone.utc)
            duration_sec = round((end_time - self.started_at).total_seconds(), 1)

        return {
            "job_id": self.job_id,
            "status": self.status,
            "game": self.game,
            "in_stock_only": self.in_stock_only,
            "max_age_days": self.max_age_days,
            "auto_adjust_sell_price": self.auto_adjust_sell_price,
            "margin_multiplier": self.margin_multiplier,
            "total_items": self.total_items,
            "processed_items": self.processed_items,
            "updated_items": self.updated_items,
            "volatility_alerts_count": self.volatility_alerts_count,
            "progress_percent": progress_pct,
            "current_card_name": self.current_card_name,
            "errors": self.errors[-10:],  # Tail of recent errors
            "total_errors": len(self.errors),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": duration_sec
        }


class MarketRefresherService:
    """
    Singleton service managing asynchronous, non-blocking price updates across
    all registered card game providers.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self.job: Optional[PriceRefreshJob] = None

    def is_running(self) -> bool:
        """Returns True if a background synchronization thread is actively executing."""
        with self._lock:
            return self.job is not None and self.job.status == "running"

    def start_refresh(
        self,
        game: Optional[str] = None,
        in_stock_only: bool = True,
        max_age_days: int = 1,
        auto_adjust_sell_price: bool = False,
        margin_multiplier: float = 1.0,
        session_factory: Optional[Callable[[], Session]] = None
    ) -> str:
        """
        Validates parameters and launches a background thread to refresh prices.
        """
        with self._lock:
            if self.job is not None and self.job.status == "running":
                raise RuntimeError("A price refresh job is already in progress.")

            job_id = uuid.uuid4().hex[:12]
            self._cancel_event.clear()

            self.job = PriceRefreshJob(
                job_id=job_id,
                status="running",
                game=game.strip().lower() if game else None,
                in_stock_only=in_stock_only,
                max_age_days=max_age_days,
                auto_adjust_sell_price=auto_adjust_sell_price,
                margin_multiplier=margin_multiplier,
                started_at=datetime.now(timezone.utc)
            )

            self._worker_thread = threading.Thread(
                target=self._run_refresh,
                args=(
                    self.job,
                    in_stock_only,
                    max_age_days,
                    auto_adjust_sell_price,
                    margin_multiplier,
                    session_factory
                ),
                daemon=True,
                name=f"MarketRefresher-{job_id}"
            )
            self._worker_thread.start()
            return job_id

    def cancel_refresh(self) -> bool:
        """Requests cooperative early termination of the active refresh job."""
        with self._lock:
            if self.job and self.job.status == "running":
                self._cancel_event.set()
                return True
            return False

    def get_status(self) -> Dict[str, Any]:
        """Returns snapshot dictionary of the current or most recent job."""
        with self._lock:
            if self.job:
                return self.job.to_dict()
            return {
                "job_id": None,
                "status": "idle",
                "progress_percent": 0.0,
                "processed_items": 0,
                "total_items": 0
            }

    def _run_refresh(
        self,
        job: PriceRefreshJob,
        in_stock_only: bool,
        max_age_days: int,
        auto_adjust_sell_price: bool,
        margin_multiplier: float,
        session_factory: Optional[Callable[[], Session]] = None
    ) -> None:
        """
        Core worker routine executed in background thread:
        - Uses short-lived scoped database sessions (db_session_scope) to prevent database locks
          on SQLite or PostgreSQL while the main Flask register thread processes checkouts/intake.
        - Resolves work items in a short-lived read session and closes it immediately.
        - Issues external network requests completely detached from any open database session.
        - Writes price updates using sub-millisecond short-lived sessions, committing and releasing
          locks immediately after each record.
        """
        from models import db_session_scope

        job.status = "running"
        if not job.started_at:
            job.started_at = datetime.now(timezone.utc)

        try:
            # 1. Short-lived read session: query target items and close session immediately
            with db_session_scope(session_factory=session_factory) as read_session:
                query = read_session.query(SinglesInventory)

                if job.game:
                    query = query.filter_by(game=job.game)
                else:
                    # Only include games with active providers registered
                    query = query.filter(SinglesInventory.game.in_(list(GAME_PROVIDERS.keys())))

                if in_stock_only:
                    query = query.filter(SinglesInventory.quantity > 0)

                if max_age_days > 0:
                    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
                    query = query.filter(SinglesInventory.updated_at <= cutoff)

                # Detach lightweight item specs so no ORM objects or cursors are held
                items_to_process = [
                    {
                        "id": row.id,
                        "game": row.game,
                        "provider_card_id": row.provider_card_id,
                        "name": row.name,
                        "set_code": row.set_code,
                        "collector_number": row.collector_number,
                    }
                    for row in query.all()
                ]

            # Read session is now closed. Zero database locks held during upstream network calls!
            job.total_items = len(items_to_process)

            if job.total_items == 0:
                job.status = "completed"
                job.finished_at = datetime.now(timezone.utc)
                return

            for idx, item_spec in enumerate(items_to_process, start=1):
                if self._cancel_event.is_set():
                    job.status = "cancelled"
                    break

                job.current_card_name = f"{item_spec['name']} ({item_spec['set_code'].upper()} #{item_spec['collector_number']})"

                provider = get_provider(item_spec["game"])
                if not provider:
                    job.errors.append(f"No provider registered for game '{item_spec['game']}' (Item ID {item_spec['id']})")
                    job.processed_items = idx
                    continue

                # Upstream HTTP request: executed outside any database session
                try:
                    prices = provider.fetch_market_prices(item_spec["provider_card_id"])
                except Exception as ex:
                    job.errors.append(f"Provider error for {item_spec['name']} ({item_spec['provider_card_id']}): {str(ex)}")
                    job.processed_items = idx
                    continue

                # Short-lived write session: updates single record and releases lock immediately (< 1ms)
                if prices and prices.get("market") is not None:
                    new_market = float(prices["market"])

                    with db_session_scope(session_factory=session_factory) as write_session:
                        item = write_session.get(SinglesInventory, item_spec["id"])
                        if item:
                            old_market = item.market_price

                            # Price Drift & Volatility Protection: Flag swings >= 20%
                            if old_market is not None and old_market > 0:
                                pct_change = round(((new_market - old_market) / old_market) * 100.0, 2)
                                if abs(pct_change) >= 20.0:
                                    meta = dict(item.api_metadata or {})
                                    meta["last_price_drift"] = {
                                        "old": old_market,
                                        "new": new_market,
                                        "change_pct": pct_change
                                    }
                                    meta["price_alert"] = True
                                    item.api_metadata = meta
                                    flag_modified(item, "api_metadata")
                                    job.volatility_alerts_count += 1

                            item.market_price = new_market

                            if prices.get("low") is not None:
                                try:
                                    item.low_price = float(prices["low"])
                                except (ValueError, TypeError):
                                    pass

                            if prices.get("foil") is not None:
                                try:
                                    item.foil_price = float(prices["foil"])
                                except (ValueError, TypeError):
                                    pass

                            if prices.get("etched") is not None:
                                try:
                                    item.etched_price = float(prices["etched"])
                                except (ValueError, TypeError):
                                    pass

                            if auto_adjust_sell_price:
                                item.sell_price = round(new_market * float(margin_multiplier), 2)

                            item.updated_at = datetime.now(timezone.utc)
                            job.updated_items += 1

                job.processed_items = idx

            if job.status == "running":
                job.status = "completed"

        except Exception as ex:
            job.status = "failed"
            job.errors.append(f"Fatal worker exception: {str(ex)}")
        finally:
            job.finished_at = datetime.now(timezone.utc)


# Shared singleton service instance
refresher_service = MarketRefresherService()
