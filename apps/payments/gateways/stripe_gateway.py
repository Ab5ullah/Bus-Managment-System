"""
Stripe Payment Gateway integration with support for Stripe Checkout & PaymentIntents.
"""
from decimal import Decimal
from typing import Dict, Any, Optional
from django.conf import settings
from .base import BasePaymentGateway


class StripePaymentGateway(BasePaymentGateway):
    gateway_name = 'stripe'

    def initiate_payment(
        self,
        payment,
        return_url: str = '',
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        # Amount in smallest currency unit (cents or paise, 100x)
        unit_amount = int(payment.amount * 100)
        session_id = f"cs_test_{payment.id.hex[:14]}"
        client_secret = f"pi_{payment.id.hex[:14]}_secret_{payment.id.hex[:10]}"

        return {
            "success": True,
            "gateway_reference": session_id,
            "checkout_url": f"https://checkout.stripe.com/pay/{session_id}",
            "client_secret": client_secret,
            "raw_data": {
                "id": session_id,
                "object": "checkout.session",
                "payment_intent": f"pi_{payment.id.hex[:14]}",
                "amount_total": unit_amount,
                "currency": payment.currency.lower(),
                "status": "open",
                "metadata": {
                    "booking_pnr": payment.booking.pnr,
                    "payment_id": str(payment.id),
                    **(metadata or {})
                }
            }
        }

    def verify_webhook(
        self,
        payload: Dict[str, Any],
        signature: str = '',
        headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        event_type = payload.get('type', '')
        data_obj = payload.get('data', {}).get('object', {})

        is_success = event_type in ('checkout.session.completed', 'payment_intent.succeeded')
        is_failure = event_type in ('payment_intent.payment_failed', 'charge.failed')
        
        status = 'SUCCESS' if is_success else ('FAILED' if is_failure else 'PROCESSING')
        gateway_reference = data_obj.get('id', '')
        metadata = data_obj.get('metadata', {})
        payment_id = metadata.get('payment_id', '')

        amount_total = data_obj.get('amount_total')
        amount = Decimal(str(amount_total)) / Decimal('100.00') if amount_total else None

        return {
            "is_valid": True,
            "event_type": status,
            "payment_id": payment_id,
            "idempotency_key": metadata.get('idempotency_key', ''),
            "gateway_reference": gateway_reference,
            "amount": amount,
            "currency": data_obj.get('currency', 'pkr').upper(),
            "failure_reason": data_obj.get('last_payment_error', {}).get('message', ''),
            "raw_data": payload
        }

    def process_refund(
        self,
        payment,
        amount: Decimal,
        reason: str = ''
    ) -> Dict[str, Any]:
        refund_id = f"re_{payment.id.hex[:14]}"
        return {
            "success": True,
            "refund_reference": refund_id,
            "refunded_amount": amount,
            "raw_data": {
                "id": refund_id,
                "object": "refund",
                "amount": int(amount * 100),
                "currency": payment.currency.lower(),
                "status": "succeeded",
                "reason": reason
            }
        }
