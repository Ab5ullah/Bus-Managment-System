"""
Mock Sandbox Payment Gateway for automated testing, local development, and demonstration.
"""
import hmac
import hashlib
from decimal import Decimal
from typing import Dict, Any, Optional
from .base import BasePaymentGateway
from common.constants import PaymentStatus


class MockPaymentGateway(BasePaymentGateway):
    gateway_name = 'mock'
    SECRET_KEY = 'busms-mock-gateway-secret-token'

    def initiate_payment(
        self,
        payment,
        return_url: str = '',
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        ref = f"MOCK-TXN-{payment.id.hex[:10].upper()}"
        return {
            "success": True,
            "gateway_reference": ref,
            "checkout_url": f"/api/payments/checkout/mock/{payment.id}/",
            "raw_data": {
                "gateway": "mock",
                "payment_id": str(payment.id),
                "booking_pnr": payment.booking.pnr,
                "amount": str(payment.amount),
                "currency": payment.currency,
                "status": "INITIATED",
                "mode": "sandbox",
                "simulated_action_url": f"/api/payments/confirm-mock/",
                "metadata": metadata or {}
            }
        }

    def verify_webhook(
        self,
        payload: Dict[str, Any],
        signature: str = '',
        headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Validates mock webhook payload.
        Verifies HMAC signature if provided, or accepts mock test payloads.
        """
        event_status = payload.get('status', 'SUCCESS').upper()
        idempotency_key = payload.get('idempotency_key', '')
        payment_id = payload.get('payment_id', '')
        gateway_reference = payload.get('gateway_reference', '')
        failure_reason = payload.get('failure_reason', '')

        # Check signature if provided
        is_valid_signature = True
        if signature:
            # Simple HMAC check or bypass if matching test signature
            if signature != "test-mock-signature":
                expected_sig = hmac.new(
                    self.SECRET_KEY.encode('utf-8'),
                    idempotency_key.encode('utf-8'),
                    hashlib.sha256
                ).hexdigest()
                if signature != expected_sig:
                    is_valid_signature = False

        amount_str = payload.get('amount')
        amount = Decimal(str(amount_str)) if amount_str is not None else None

        return {
            "is_valid": is_valid_signature,
            "event_type": event_status,
            "payment_id": payment_id,
            "idempotency_key": idempotency_key,
            "gateway_reference": gateway_reference,
            "amount": amount,
            "currency": payload.get('currency', 'PKR'),
            "failure_reason": failure_reason,
            "raw_data": payload
        }

    def process_refund(
        self,
        payment,
        amount: Decimal,
        reason: str = ''
    ) -> Dict[str, Any]:
        ref = f"MOCK-REF-{payment.id.hex[:8].upper()}"
        return {
            "success": True,
            "refund_reference": ref,
            "refunded_amount": amount,
            "raw_data": {
                "gateway": "mock",
                "original_payment_id": str(payment.id),
                "refund_reference": ref,
                "amount": str(amount),
                "reason": reason,
                "status": "REFUNDED"
            }
        }
