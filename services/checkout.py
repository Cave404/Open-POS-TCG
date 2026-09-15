"""
OpenPOS-TCG Addon
File: services/checkout.py
Addon ID: tcg_pos

CheckoutService: Atomic transaction settlement engine.
Handles inventory decrement, COGS snapshotting, split-tender validation,
transaction persistence, and non-blocking hardware integration
(cash drawer kick + receipt hub spool).

Hardware Design Contract
------------------------
- All hardware calls are guarded by config flags (enable_cash_drawer,
  enable_receipt_printer) that default to False.
- Hardware is invoked in a daemon thread with a 1.5 s timeout.
- Any hardware failure is captured into hardware_meta['errors'] and
  NEVER propagates to the caller or aborts the transaction.
"""

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from models import (
    SinglesInventory,
    TCGTransaction,
    TCGTransactionItem,
    TCGTransactionTender,
    db_session_scope,
)

log = logging.getLogger(__name__)

VALID_TENDER_TYPES = {"cash", "card", "store_credit"}


# ---------------------------------------------------------------------------
# Public DTOs
# ---------------------------------------------------------------------------

@dataclass
class CartItem:
    """Represents one line in the customer's cart."""
    inventory_id: int
    quantity: int
    unit_price: Optional[float] = None  # Override; defaults to item.sell_price


@dataclass
class TenderEntry:
    """Represents one tender row (part of a potentially split payment)."""
    tender_type: str          # cash | card | store_credit
    amount: float
    reference: Optional[str] = None


@dataclass
class SettlementResult:
    """Returned by CheckoutService.settle()."""
    success: bool
    transaction_id: Optional[int] = None
    receipt_number: Optional[str] = None
    grand_total: float = 0.0
    change_due: float = 0.0
    hardware_errors: List[str] = field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Hardware helpers (non-blocking, fire-and-forget)
# ---------------------------------------------------------------------------

def _fire_cash_drawer(hub_url: str, timeout: float = 1.5) -> Optional[str]:
    """
    Sends a cash drawer kick request to Open-POS-Hardware-Hub.
    Returns None on success, error string on failure.
    Runs in a daemon thread — never blocks the caller.
    """
    try:
        import requests  # type: ignore
        resp = requests.post(f"{hub_url}/api/drawer/kick", timeout=timeout)
        if not resp.ok:
            return f"Cash drawer kick failed: HTTP {resp.status_code}"
    except Exception as exc:
        return f"Cash drawer unreachable: {exc}"
    return None


def _fire_receipt_spool(hub_url: str, receipt_payload: Dict[str, Any],
                        timeout: float = 1.5) -> Optional[str]:
    """
    Spools receipt data to the hardware hub ESC/POS printer endpoint.
    Returns None on success, error string on failure.
    """
    try:
        import requests  # type: ignore
        resp = requests.post(
            f"{hub_url}/api/receipt/print",
            json=receipt_payload,
            timeout=timeout
        )
        if not resp.ok:
            return f"Receipt spool failed: HTTP {resp.status_code}"
    except Exception as exc:
        return f"Receipt printer unreachable: {exc}"
    return None


def _launch_hardware(
    config: Dict[str, Any],
    transaction: "TCGTransaction",
    hardware_errors: List[str]
) -> None:
    """
    Fires hardware actions (cash drawer + receipt spool) in a daemon thread.
    Writes outcomes into hardware_errors list (shared with caller via reference).
    """
    hub_url = config.get("hardware_hub_url", "http://localhost:5111")

    if config.get("enable_cash_drawer", False):
        err = _fire_cash_drawer(hub_url)
        if err:
            hardware_errors.append(err)
            log.warning("[CheckoutService] %s", err)
        else:
            log.info("[CheckoutService] Cash drawer kicked for txn %s", transaction.receipt_number)

    if config.get("enable_receipt_printer", False):
        payload = {
            "receipt_number": transaction.receipt_number,
            "grand_total": transaction.grand_total,
            "items": [item.to_dict() for item in (transaction.items or [])],
            "tenders": [t.to_dict() for t in (transaction.tenders or [])],
        }
        err = _fire_receipt_spool(hub_url, payload)
        if err:
            hardware_errors.append(err)
            log.warning("[CheckoutService] %s", err)
        else:
            log.info("[CheckoutService] Receipt spooled for txn %s", transaction.receipt_number)


# ---------------------------------------------------------------------------
# CheckoutService
# ---------------------------------------------------------------------------

class CheckoutService:
    """
    Stateless service class providing TCG POS transaction settlement.
    All public methods are thread-safe (no shared mutable state).
    """

    @staticmethod
    def _generate_receipt_number() -> str:
        """Generates a unique, human-readable receipt number."""
        now = datetime.now(timezone.utc)
        uid = uuid.uuid4().hex[:6].upper()
        return f"RCT-{now.strftime('%Y%m%d')}-{uid}"

    @staticmethod
    def settle(
        cart_items: List[CartItem],
        tenders: List[TenderEntry],
        tax_rate: float = 0.0,
        config: Optional[Dict[str, Any]] = None,
        session_factory=None,
    ) -> SettlementResult:
        """
        Atomically settle a transaction:
          1. Validate tenders.
          2. Lock inventory rows, validate stock, decrement quantities.
          3. Snapshot COGS per line.
          4. Persist TCGTransaction + items + tenders.
          5. (Non-blocking) fire hardware actions in a daemon thread.

        Parameters
        ----------
        cart_items      : List of CartItem DTOs describing what was purchased.
        tenders         : List of TenderEntry DTOs describing payment.
        tax_rate        : Decimal tax rate (e.g. 0.0825 for 8.25%). Default 0.
        config          : App config dict with hardware flags and hub_url.
        session_factory : Optional callable returning a SQLAlchemy Session.
                          If None, falls back to models.get_db_session().
        """
        if config is None:
            config = {}

        if not cart_items:
            return SettlementResult(success=False, error="Cart is empty.")

        if not tenders:
            return SettlementResult(success=False, error="No tender provided.")

        # --- Validate tender types ---
        for t in tenders:
            if t.tender_type not in VALID_TENDER_TYPES:
                return SettlementResult(
                    success=False,
                    error=f"Invalid tender type '{t.tender_type}'. "
                          f"Must be one of: {', '.join(sorted(VALID_TENDER_TYPES))}."
                )
            if t.amount <= 0:
                return SettlementResult(
                    success=False,
                    error=f"Tender amount must be > 0, got {t.amount} for {t.tender_type}."
                )

        hardware_errors: List[str] = []

        try:
            with db_session_scope(session_factory=session_factory) as session:
                # ----------------------------------------------------------
                # Step 1: Resolve inventory rows (with row lock where supported)
                # ----------------------------------------------------------
                resolved_items: List[Dict[str, Any]] = []

                for cart_item in cart_items:
                    q = session.query(SinglesInventory).filter_by(id=cart_item.inventory_id)
                    try:
                        inv = q.with_for_update().first()
                    except Exception:
                        inv = q.first()

                    if inv is None:
                        raise ValueError(
                            f"Inventory item ID {cart_item.inventory_id} not found."
                        )

                    req_qty = cart_item.quantity
                    if req_qty <= 0:
                        raise ValueError(
                            f"Invalid quantity {req_qty} for item '{inv.name}'."
                        )

                    if inv.quantity < req_qty:
                        raise ValueError(
                            f"Insufficient stock for '{inv.name}' "
                            f"({inv.set_code.upper()} {inv.condition}/{inv.finish}). "
                            f"Requested {req_qty}, available {inv.quantity}."
                        )

                    resolved_items.append({
                        "inv": inv,
                        "quantity": req_qty,
                        "unit_price": cart_item.unit_price
                            if cart_item.unit_price is not None
                            else inv.sell_price,
                    })

                # ----------------------------------------------------------
                # Step 2: Calculate financials
                # ----------------------------------------------------------
                subtotal = sum(
                    ri["unit_price"] * ri["quantity"] for ri in resolved_items
                )
                subtotal = round(subtotal, 2)
                tax_amount = round(subtotal * tax_rate, 2)
                grand_total = round(subtotal + tax_amount, 2)
                total_tendered = round(sum(t.amount for t in tenders), 2)

                if total_tendered < grand_total:
                    raise ValueError(
                        f"Insufficient tender: ${total_tendered:.2f} tendered "
                        f"for ${grand_total:.2f} due."
                    )

                # Change due is only meaningful for cash
                cash_tendered = sum(
                    t.amount for t in tenders if t.tender_type == "cash"
                )
                change_due = round(max(0.0, total_tendered - grand_total), 2)

                # ----------------------------------------------------------
                # Step 3: Decrement inventory
                # ----------------------------------------------------------
                for ri in resolved_items:
                    inv = ri["inv"]
                    inv.quantity -= ri["quantity"]
                    inv.updated_at = datetime.now(timezone.utc)

                # ----------------------------------------------------------
                # Step 4: Create transaction header
                # ----------------------------------------------------------
                receipt_number = CheckoutService._generate_receipt_number()
                txn = TCGTransaction(
                    receipt_number=receipt_number,
                    subtotal=subtotal,
                    tax_rate=tax_rate,
                    tax_amount=tax_amount,
                    grand_total=grand_total,
                    total_tendered=total_tendered,
                    change_due=change_due,
                    status="completed",
                    hardware_meta={},
                )
                session.add(txn)
                session.flush()  # Assign txn.id

                # ----------------------------------------------------------
                # Step 5: Create line items (COGS snapshot)
                # ----------------------------------------------------------
                for ri in resolved_items:
                    inv = ri["inv"]
                    line_total = round(ri["unit_price"] * ri["quantity"], 2)
                    item_row = TCGTransactionItem(
                        transaction_id=txn.id,
                        inventory_id=inv.id,
                        name=inv.name,
                        sku=inv.sku or inv.generate_default_sku(),
                        game=inv.game,
                        set_code=inv.set_code,
                        condition=inv.condition,
                        finish=inv.finish,
                        quantity_sold=ri["quantity"],
                        unit_price=ri["unit_price"],
                        cost_basis_snapshot=inv.cost_basis,
                        line_total=line_total,
                    )
                    session.add(item_row)

                # ----------------------------------------------------------
                # Step 6: Create tender rows
                # ----------------------------------------------------------
                for t in tenders:
                    tender_row = TCGTransactionTender(
                        transaction_id=txn.id,
                        tender_type=t.tender_type,
                        amount=t.amount,
                        change_due=change_due if t.tender_type == "cash" else 0.0,
                        reference=t.reference,
                    )
                    session.add(tender_row)

                # session auto-commits on scope exit
                transaction_id = txn.id
                log.info(
                    "[CheckoutService] Settled transaction %s (receipt: %s, total: $%.2f)",
                    transaction_id, receipt_number, grand_total
                )

        except ValueError as ve:
            return SettlementResult(success=False, error=str(ve))
        except Exception as exc:
            log.exception("[CheckoutService] Unexpected settlement failure: %s", exc)
            return SettlementResult(
                success=False,
                error=f"Settlement failed: {exc}"
            )

        # ------------------------------------------------------------------
        # Step 7: Fire hardware actions (non-blocking daemon thread)
        # ------------------------------------------------------------------
        if config.get("enable_cash_drawer", False) or config.get("enable_receipt_printer", False):
            # Re-fetch transaction for hardware payload (outside the settled scope)
            try:
                with db_session_scope(session_factory=session_factory) as hw_session:
                    hw_txn = hw_session.query(TCGTransaction).filter_by(
                        id=transaction_id
                    ).first()
                    if hw_txn:
                        hw_thread = threading.Thread(
                            target=_launch_hardware,
                            args=(config, hw_txn, hardware_errors),
                            daemon=True,
                            name=f"hw-{receipt_number}"
                        )
                        hw_thread.start()
            except Exception as exc:
                hardware_errors.append(f"Hardware thread setup failed: {exc}")
                log.warning("[CheckoutService] Hardware thread setup error: %s", exc)

        return SettlementResult(
            success=True,
            transaction_id=transaction_id,
            receipt_number=receipt_number,
            grand_total=grand_total,
            change_due=change_due,
            hardware_errors=hardware_errors,
        )
