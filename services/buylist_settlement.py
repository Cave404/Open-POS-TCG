"""
OpenPOS-TCG Addon
File: services/buylist_settlement.py
Addon ID: tcg_pos

Buylist Intake Settlement Service.
Connects buylist intake batch finalization to OpenPOS Core customer credit deposits.
"""

import logging
from typing import Any, Dict, Optional
from datetime import datetime, timezone
import uuid

from services.core_customer_client import CoreCustomerClient

log = logging.getLogger(__name__)


class BuylistSettlementService:
    @staticmethod
    def generate_batch_number() -> str:
        now = datetime.now(timezone.utc)
        uid = uuid.uuid4().hex[:6].upper()
        return f"BUY-{now.strftime('%Y%m%d')}-{uid}"

    @classmethod
    def settle_intake_payout(
        cls,
        payout_type: str,
        total_payout: float,
        customer_id: Optional[int] = None,
        batch_number: Optional[str] = None,
        core_client: Optional[CoreCustomerClient] = None
    ) -> Dict[str, Any]:
        """
        Settles buylist intake payout. If payout_type is store_credit,
        deposits credit via CoreCustomerClient.
        """
        batch_num = batch_number or cls.generate_batch_number()
        result: Dict[str, Any] = {
            "payout_type": payout_type,
            "total_payout": round(float(total_payout), 2),
            "batch_number": batch_num,
        }

        if payout_type == "store_credit":
            if not customer_id:
                raise ValueError("customer_id is required when payout_type is 'store_credit'.")

            client = core_client or CoreCustomerClient()
            deposit_data = client.deposit_trade_in_credit(
                customer_id=int(customer_id),
                amount=total_payout,
                batch_number=batch_num
            )
            result["customer_credit_balance"] = deposit_data.get("new_balance") or deposit_data.get("balance")
            result["deposit_details"] = deposit_data

        return result
