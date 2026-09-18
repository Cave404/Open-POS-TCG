"""
OpenPOS-TCG Addon
File: services/core_customer_client.py

Client interface communicating with OpenPOS Core Customer and Store Credit endpoints.
"""

from typing import Optional, Dict, Any
import requests


class CoreCustomerClient:
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or "http://127.0.0.1:5000").rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "OpenPOS-TCG/1.0.1 (Internal Client)",
            "Accept": "application/json"
        })

    def resolve_customer(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Resolves customer by 14-character NFC UID, phone, email, or name."""
        try:
            res = self.session.get(
                f"{self.base_url}/api/core/customers/resolve",
                params={"q": identifier},
                timeout=3
            )
            if res.status_code == 200:
                data = res.json()
                return data.get("customer") if data.get("found") else None
        except requests.exceptions.RequestException:
            return None
        return None

    def deposit_trade_in_credit(
        self,
        customer_id: int,
        amount: float,
        batch_number: str,
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """Deposits buylist trade-in proceeds into Core store credit ledger."""
        payload = {
            "amount": round(float(amount), 2),
            "source_addon": "tcg_pos",
            "reference_id": batch_number,
            "notes": notes or f"TCG Buylist Trade-In: {batch_number}"
        }
        res = self.session.post(
            f"{self.base_url}/api/core/customers/{customer_id}/credit/deposit",
            json=payload,
            timeout=5
        )
        res.raise_for_status()
        return res.json()

    def redeem_store_credit(
        self,
        customer_id: int,
        amount: float,
        transaction_number: str,
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """Redeems store credit for register checkout."""
        payload = {
            "amount": round(float(amount), 2),
            "source_addon": "tcg_pos",
            "reference_id": transaction_number,
            "notes": notes or f"POS Register Checkout: {transaction_number}"
        }
        res = self.session.post(
            f"{self.base_url}/api/core/customers/{customer_id}/credit/redeem",
            json=payload,
            timeout=5
        )
        res.raise_for_status()
        return res.json()
