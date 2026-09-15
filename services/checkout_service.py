"""
OpenPOS-TCG Addon
File: services/checkout_service.py
Addon ID: tcg_pos

CheckoutService: Core POS transaction settlement and sales engine.
Handles atomic inventory verification & decrement, COGS snapshotting,
multi-tender / split validation, transaction persistence, and non-blocking
peripheral integration.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import threading
from typing import Any, Dict, List, Optional, Union
import uuid

from models import (
    SinglesInventory,
    TCGTransaction,
    TCGTransactionItem,
    TCGTransactionTender,
    db_session_scope,
    get_db_session,
)

log = logging.getLogger(__name__)

VALID_TENDER_TYPES = {"cash", "card", "store_credit"}


class InsufficientStockError(Exception):
    """Raised when an inventory item has fewer units available than requested."""
    pass


class InvalidTenderError(Exception):
    """Raised when tender amounts are invalid or insufficient to cover grand total."""
    pass


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
    """Returned by CheckoutService.settle() for backward compatibility."""
    success: bool
    transaction_id: Optional[int] = None
    receipt_number: Optional[str] = None
    grand_total: float = 0.0
    change_due: float = 0.0
    hardware_errors: List[str] = field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Hardware helpers (non-blocking, fail-safe)
# ---------------------------------------------------------------------------

def _fire_cash_drawer(hub_url: str = "http://127.0.0.1:5000", timeout: float = 1.5) -> Optional[str]:
    """
    Sends a cash drawer kick request to Open-POS-Hardware-Hub.
    Returns None on success, error string on failure.
    Runs in a daemon thread — never blocks register checkout.
    """
    try:
        import requests
        # Support both Core and standalone Hardware Hub URL conventions
        target_url = f"{hub_url.rstrip('/')}/hardware/drawer/kick"
        resp = requests.post(target_url, timeout=timeout)
        if not resp.ok:
            # Fallback check for /api/drawer/kick
            fallback_url = f"{hub_url.rstrip('/')}/api/drawer/kick"
            fb_resp = requests.post(fallback_url, timeout=timeout)
            if not fb_resp.ok:
                return f"Cash drawer kick failed: HTTP {resp.status_code}"
    except Exception as exc:
        return f"Cash drawer unreachable: {exc}"
    return None


def _fire_receipt_spool(hub_url: str = "http://127.0.0.1:5000", receipt_payload: Optional[Dict[str, Any]] = None,
                        timeout: float = 1.5) -> Optional[str]:
    """
    Spools receipt data to the hardware hub ESC/POS printer endpoint.
    Returns None on success, error string on failure.
    """
    try:
        import requests
        target_url = f"{hub_url.rstrip('/')}/hardware/printers/receipt"
        resp = requests.post(target_url, json=receipt_payload or {}, timeout=timeout)
        if not resp.ok:
            fallback_url = f"{hub_url.rstrip('/')}/api/receipt/print"
            fb_resp = requests.post(fallback_url, json=receipt_payload or {}, timeout=timeout)
            if not fb_resp.ok:
                return f"Receipt spool failed: HTTP {resp.status_code}"
    except Exception as exc:
        return f"Receipt printer unreachable: {exc}"
    return None


class CheckoutService:
    """
    Atomic transaction settlement engine for TCG POS.
    Supports atomic inventory decrements, COGS snapshotting, and multi-tender splits.
    """

    @staticmethod
    def _generate_transaction_number() -> str:
        """Generates unique indexed transaction number (TXN-YYYYMMDD-XXXX)."""
        now = datetime.now(timezone.utc)
        uid = uuid.uuid4().hex[:6].upper()
        return f"TXN-{now.strftime('%Y%m%d')}-{uid}"

    @staticmethod
    def _generate_receipt_number() -> str:
        """Generates receipt number alias (RCT-YYYYMMDD-XXXX)."""
        now = datetime.now(timezone.utc)
        uid = uuid.uuid4().hex[:6].upper()
        return f"RCT-{now.strftime('%Y%m%d')}-{uid}"

    @classmethod
    def process_sale(
        cls,
        cart_items: Union[List[Dict[str, Any]], List[CartItem]],
        tenders: Union[Dict[str, float], List[Dict[str, Any]], List[TenderEntry]],
        tax_rate: float = 0.0,
        discount: float = 0.0,
        notes: Optional[str] = None,
        customer_id: Optional[Union[str, int]] = None,
        session_factory=None,
        session=None,
    ) -> TCGTransaction:
        """
        Atomically processes a sale:
          1. Validates total tenders (Cash + Card + Store Credit) >= grand_total.
          2. Opens atomic database transaction.
          3. Queries SinglesInventory rows with row locking.
          4. Asserts item.quantity >= requested_qty (raises InsufficientStockError if short).
          5. Decrements item.quantity.
          6. Creates TCGTransactionItem snapshotting unit_cost_basis and unit_sell_price.
          7. Creates and commits TCGTransaction and TCGTransactionTender rows.
          8. Returns the committed TCGTransaction instance.
        """
        if not cart_items:
            raise ValueError("Cart is empty.")

        # Normalize cart items into standardized structure: list of (inventory_id, quantity, unit_price_override)
        normalized_cart = []
        for c in cart_items:
            if isinstance(c, CartItem):
                inv_id = c.inventory_id
                qty = c.quantity
                override_price = c.unit_price
            elif isinstance(c, dict):
                inv_id = int(c.get("id") or c.get("inventory_id"))
                qty = int(c.get("quantity", 1))
                override_price = float(c["unit_price"]) if "unit_price" in c and c["unit_price"] is not None else None
            else:
                raise ValueError(f"Invalid cart item structure: {c}")

            if qty <= 0:
                raise ValueError(f"Item quantity must be positive, got {qty} for item ID {inv_id}.")
            normalized_cart.append({"inventory_id": inv_id, "quantity": qty, "unit_price": override_price})

        # Normalize tenders into standardized dict: {"cash": float, "card": float, "store_credit": float}
        tender_dict: Dict[str, float] = {}
        tender_refs: Dict[str, Optional[str]] = {}

        if isinstance(tenders, dict):
            for t_type, amt in tenders.items():
                norm_type = str(t_type).strip().lower()
                amt_val = float(amt)
                if amt_val > 0:
                    tender_dict[norm_type] = round(amt_val, 2)
        elif isinstance(tenders, list):
            for t in tenders:
                if isinstance(t, TenderEntry):
                    norm_type = t.tender_type.strip().lower()
                    amt_val = float(t.amount)
                    ref = t.reference
                elif isinstance(t, dict):
                    norm_type = str(t.get("tender_type") or t.get("type", "")).strip().lower()
                    amt_val = float(t.get("amount", 0.0))
                    ref = t.get("reference")
                else:
                    raise ValueError(f"Invalid tender item: {t}")

                if amt_val > 0:
                    tender_dict[norm_type] = round(tender_dict.get(norm_type, 0.0) + amt_val, 2)
                    if ref:
                        tender_refs[norm_type] = ref
        else:
            raise ValueError(f"Invalid tenders structure: {tenders}")

        for t_type in tender_dict.keys():
            if t_type not in VALID_TENDER_TYPES:
                raise InvalidTenderError(
                    f"Invalid tender type '{t_type}'. Allowed types: {', '.join(sorted(VALID_TENDER_TYPES))}."
                )

        total_tendered = round(sum(tender_dict.values()), 2)
        if total_tendered <= 0:
            raise InvalidTenderError("No valid payment tender provided.")

        def _execute_settlement(db_session) -> TCGTransaction:
            # 1. Lock and resolve inventory items
            resolved_lines = []
            subtotal = 0.0

            for entry in normalized_cart:
                item_id = entry["inventory_id"]
                req_qty = entry["quantity"]

                q = db_session.query(SinglesInventory).filter_by(id=item_id)
                try:
                    inv = q.with_for_update().first()
                except Exception:
                    inv = q.first()

                if inv is None:
                    raise ValueError(f"Singles inventory item ID {item_id} not found.")

                if inv.quantity < req_qty:
                    raise InsufficientStockError(
                        f"Insufficient stock for '{inv.name}' ({inv.set_code.upper()} "
                        f"{inv.condition}/{inv.finish}). Requested {req_qty}, available {inv.quantity}."
                    )

                unit_price = entry["unit_price"] if entry["unit_price"] is not None else (inv.sell_price or 0.0)
                unit_cost = inv.cost_basis if inv.cost_basis is not None else 0.0
                line_total = round(unit_price * req_qty, 2)
                subtotal += line_total

                resolved_lines.append({
                    "inv": inv,
                    "quantity": req_qty,
                    "unit_sell_price": unit_price,
                    "unit_cost_basis": unit_cost,
                    "total_sell_price": line_total,
                })

            subtotal = round(subtotal, 2)
            discount_total = round(max(0.0, float(discount)), 2)
            discounted_subtotal = max(0.0, subtotal - discount_total)
            tax_amount = round(discounted_subtotal * float(tax_rate), 2)
            grand_total = round(discounted_subtotal + tax_amount, 2)

            if total_tendered < grand_total:
                raise InvalidTenderError(
                    f"Insufficient tender: ${total_tendered:.2f} tendered for ${grand_total:.2f} due."
                )

            # Determine payment method description
            active_tenders = [k for k, v in tender_dict.items() if v > 0]
            if len(active_tenders) == 1:
                payment_method = active_tenders[0]
            else:
                payment_method = "split"

            cash_tendered = tender_dict.get("cash", 0.0)
            change_due = round(max(0.0, total_tendered - grand_total), 2) if cash_tendered > 0 else 0.0

            # 2. Decrement inventory quantities
            for line in resolved_lines:
                inv = line["inv"]
                inv.quantity -= line["quantity"]
                inv.updated_at = datetime.now(timezone.utc)

            # 3. Create TCGTransaction header
            txn_num = cls._generate_transaction_number()
            rcpt_num = cls._generate_receipt_number()
            txn = TCGTransaction(
                transaction_number=txn_num,
                receipt_number=rcpt_num,
                subtotal=subtotal,
                tax_rate=float(tax_rate),
                tax_amount=tax_amount,
                tax_total=tax_amount,
                discount_total=discount_total,
                grand_total=grand_total,
                total_tendered=total_tendered,
                change_due=change_due,
                payment_method=payment_method,
                tender_details=tender_dict,
                customer_id=str(customer_id) if customer_id else None,
                status="completed",
                notes=notes,
                hardware_meta={},
            )
            db_session.add(txn)
            db_session.flush()

            # 4. Create TCGTransactionItem line snapshots
            for line in resolved_lines:
                inv = line["inv"]
                item_row = TCGTransactionItem(
                    transaction_id=txn.id,
                    singles_inventory_id=inv.id,
                    inventory_id=inv.id,
                    item_name=inv.name,
                    name=inv.name,
                    sku=inv.sku or inv.generate_default_sku(),
                    game=inv.game,
                    set_code=inv.set_code,
                    collector_number=inv.collector_number,
                    condition=inv.condition,
                    finish=inv.finish,
                    quantity=line["quantity"],
                    quantity_sold=line["quantity"],
                    unit_cost_basis=line["unit_cost_basis"],
                    cost_basis_snapshot=line["unit_cost_basis"],
                    unit_sell_price=line["unit_sell_price"],
                    unit_price=line["unit_sell_price"],
                    total_sell_price=line["total_sell_price"],
                    line_total=line["total_sell_price"],
                    api_metadata=inv.api_metadata if isinstance(inv.api_metadata, dict) else {},
                )
                db_session.add(item_row)

            # 5. Create TCGTransactionTender rows
            for t_type, amt in tender_dict.items():
                t_change = change_due if t_type == "cash" else 0.0
                t_row = TCGTransactionTender(
                    transaction_id=txn.id,
                    tender_type=t_type,
                    amount=amt,
                    change_due=t_change,
                    reference=tender_refs.get(t_type),
                )
                db_session.add(t_row)

            db_session.commit()

            # Eagerly load all attributes and relationships before expunging
            _ = txn.id
            _ = txn.transaction_number
            _ = txn.receipt_number
            _ = txn.subtotal
            _ = txn.tax_rate
            _ = txn.tax_amount
            _ = txn.tax_total
            _ = txn.discount_total
            _ = txn.grand_total
            _ = txn.total_tendered
            _ = txn.change_due
            _ = txn.payment_method
            _ = txn.tender_details
            _ = txn.customer_id
            _ = txn.status
            _ = txn.cashier_id
            _ = txn.notes
            _ = txn.hardware_meta
            _ = txn.created_at
            _ = txn.updated_at

            if txn.items:
                for it in txn.items:
                    _ = it.id
                    _ = it.transaction_id
                    _ = it.singles_inventory_id
                    _ = it.inventory_id
                    _ = it.item_name
                    _ = it.name
                    _ = it.sku
                    _ = it.game
                    _ = it.set_code
                    _ = it.collector_number
                    _ = it.condition
                    _ = it.finish
                    _ = it.quantity
                    _ = it.quantity_sold
                    _ = it.unit_cost_basis
                    _ = it.cost_basis_snapshot
                    _ = it.unit_sell_price
                    _ = it.unit_price
                    _ = it.total_sell_price
                    _ = it.line_total
                    _ = it.api_metadata

            if txn.tenders:
                for td in txn.tenders:
                    _ = td.id
                    _ = td.transaction_id
                    _ = td.tender_type
                    _ = td.amount
                    _ = td.change_due
                    _ = td.reference

            db_session.expunge_all()
            return txn

        if session is not None:
            return _execute_settlement(session)

        with db_session_scope(session_factory=session_factory) as db_session:
            return _execute_settlement(db_session)

    @classmethod
    def settle(
        cls,
        cart_items: List[CartItem],
        tenders: List[TenderEntry],
        tax_rate: float = 0.0,
        config: Optional[Dict[str, Any]] = None,
        session_factory=None,
    ) -> SettlementResult:
        """
        Backward-compatible settle() API returning SettlementResult.
        Fires non-blocking hardware actions after commit if configured.
        """
        if config is None:
            config = {}

        try:
            txn = cls.process_sale(
                cart_items=cart_items,
                tenders=tenders,
                tax_rate=tax_rate,
                session_factory=session_factory,
            )
        except (InsufficientStockError, InvalidTenderError, ValueError) as exc:
            return SettlementResult(success=False, error=str(exc))
        except Exception as exc:
            log.exception("[CheckoutService.settle] Unexpected error: %s", exc)
            return SettlementResult(success=False, error=f"Settlement failed: {exc}")

        hardware_errors: List[str] = []

        txn_id = txn.id
        txn_num = txn.transaction_number
        rcpt_num = txn.receipt_number or txn_num
        g_total = txn.grand_total
        c_due = txn.change_due
        items_payload = [item.to_dict() for item in (txn.items or [])]
        tenders_payload = [t.to_dict() for t in (txn.tenders or [])]

        # Non-blocking peripheral triggering
        if config.get("enable_cash_drawer", False) or config.get("enable_receipt_printer", False):
            def _async_hardware():
                hub_url = config.get("hardware_hub_url", "http://127.0.0.1:5000")
                if config.get("enable_cash_drawer", False):
                    err = _fire_cash_drawer(hub_url)
                    if err:
                        hardware_errors.append(err)
                if config.get("enable_receipt_printer", False):
                    payload = {
                        "receipt_number": rcpt_num,
                        "transaction_number": txn_num,
                        "grand_total": g_total,
                        "items": items_payload,
                        "tenders": tenders_payload,
                    }
                    err = _fire_receipt_spool(hub_url, payload)
                    if err:
                        hardware_errors.append(err)

            hw_thread = threading.Thread(
                target=_async_hardware,
                daemon=True,
                name=f"hw-{txn_num}"
            )
            hw_thread.start()

        return SettlementResult(
            success=True,
            transaction_id=txn_id,
            receipt_number=rcpt_num,
            grand_total=g_total,
            change_due=c_due,
            hardware_errors=hardware_errors,
        )

