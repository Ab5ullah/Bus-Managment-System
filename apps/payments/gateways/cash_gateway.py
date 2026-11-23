"""
Cash Counter Gateway for terminal and in-person walk-in bookings.
"""
from decimal import Decimal
from typing import Dict, Any, Optional
from .base import BasePaymentGateway


class CashCounterGateway(BasePaymentGateway):
    gateway_name = 'cash'

    def initiate_payment(
        self,
        payment,
        return_url: str = '',
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        ref = f"CASH-TXN-{payment.id.hex[:10].upper()}"
        return {
            "success": True,
            "gateway_reference": ref,
            "checkout_url": "",
            "raw_data": {
                "gateway": "cash",
                "payment_id": str(payment.id),
                "booking_pnr": payment.booking.pnr,
                "amount": str(payment.amount),
                "currency": payment.currency,
                "status": "SUCCESS",
                "collector_agent": str(payment.booking.booked_by_agent_id or 'COUNTER')
            }
        }

    def verify_webhook(
        self,
        payload: Dict[str, Any],
        signature: str = '',
        headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        return {
            "is_valid": True,
            "event_type": "SUCCESS",
            "payment_id": payload.get('payment_id', ''),
            "idempotency_key": payload.get('idempotency_key', ''),
            "gateway_reference": payload.get('gateway_reference', ''),
            "amount": Decimal(str(payload.get('amount', '0.00'))),
            "currency": payload.get('currency', 'PKR'),
            "failure_reason": "",
            "raw_data": payload
        }

    def process_refund(
        self,
        payment,
        amount: Decimal,
        reason: str = ''
    ) -> Dict[str, Any]:
        ref = f"CASH-REF-{payment.id.hex[:8].upper()}"
        return {
            "success": True,
            "refund_reference": ref,
            "refunded_amount": amount,
            "raw_data": {
                "gateway": "cash",
                "refund_reference": ref,
                "amount": str(amount),
                "reason": reason,
                "status": "REFUNDED"
            }
        }
