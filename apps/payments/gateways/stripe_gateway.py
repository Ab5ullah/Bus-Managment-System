"""
Stripe Payment Gateway integration using the official Stripe Python SDK.
Supports Stripe Hosted Checkout Sessions, cryptographic webhook verification
via Stripe-Signature, session retrieval, and PaymentIntent refunds.
"""
import json
from decimal import Decimal
from typing import Dict, Any, Optional

import stripe
from django.conf import settings

from common.exceptions import BusinessLogicError
from common.money import round_currency
from .base import BasePaymentGateway


class StripePaymentGateway(BasePaymentGateway):
    gateway_name = 'stripe'

    @staticmethod
    def _get_api_key() -> str:
        api_key = getattr(settings, 'STRIPE_SECRET_KEY', '')
        if not api_key:
            raise BusinessLogicError(
                "Stripe is not configured: STRIPE_SECRET_KEY is missing from environment settings."
            )
        return api_key

    @staticmethod
    def _to_plain_dict(obj: Any) -> Any:
        """Recursively convert StripeObject instances into JSON-serializable Python dicts/lists."""
        if hasattr(obj, 'to_dict_recursive'):
            return obj.to_dict_recursive()
        if hasattr(obj, 'to_dict'):
            return StripePaymentGateway._to_plain_dict(obj.to_dict())
        if isinstance(obj, dict):
            return {k: StripePaymentGateway._to_plain_dict(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [StripePaymentGateway._to_plain_dict(v) for v in obj]
        return obj

    def initiate_payment(
        self,
        payment,
        return_url: str = '',
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Creates either:
        - checkout_mode='checkout_session' (default): Stripe Hosted Checkout Session (returns checkout_url)
        - checkout_mode='payment_intent': Native Stripe PaymentIntent (returns client_secret + publishable_key for Flutter PaymentSheet)
        """
        api_key = self._get_api_key()
        unit_amount = int(round_currency(payment.amount) * 100)
        currency = (payment.currency or 'PKR').lower()
        meta_dict = dict(metadata or {})
        checkout_mode = meta_dict.pop('checkout_mode', 'checkout_session')

        tx_metadata = {
            "payment_id": str(payment.id),
            "booking_pnr": payment.booking.pnr,
            "idempotency_key": payment.idempotency_key,
            **{str(k): str(v) for k, v in meta_dict.items()},
        }

        if checkout_mode == 'payment_intent':
            intent_kwargs: Dict[str, Any] = {
                "api_key": api_key,
                "amount": unit_amount,
                "currency": currency,
                "automatic_payment_methods": {"enabled": True},
                "description": f"Bus Ticket Reservation — PNR {payment.booking.pnr}",
                "metadata": tx_metadata,
                "idempotency_key": payment.idempotency_key,
            }
            if payment.booking.passenger_email:
                intent_kwargs["receipt_email"] = payment.booking.passenger_email

            try:
                intent = stripe.PaymentIntent.create(**intent_kwargs)
            except stripe.StripeError as exc:
                err_msg = getattr(exc, 'user_message', None) or str(exc)
                raise BusinessLogicError(f"Stripe PaymentIntent creation failed: {err_msg}")

            intent_dict = self._to_plain_dict(intent)
            return {
                "success": True,
                "checkout_mode": "payment_intent",
                "gateway_reference": intent.id,
                "checkout_url": "",
                "client_secret": intent.client_secret,
                "publishable_key": getattr(settings, 'STRIPE_PUBLISHABLE_KEY', ''),
                "raw_data": intent_dict,
            }

        base_return = (return_url or 'http://127.0.0.1:8000/').strip()
        sep = '&' if '?' in base_return else '?'
        success_url = (
            f"{base_return}{sep}payment_status=success"
            f"&pnr={payment.booking.pnr}&session_id={{CHECKOUT_SESSION_ID}}"
        )
        cancel_url = f"{base_return}{sep}payment_status=cancelled&pnr={payment.booking.pnr}"

        origin_city = payment.booking.from_stop.terminal.city
        dest_city = payment.booking.to_stop.terminal.city

        session_kwargs: Dict[str, Any] = {
            "api_key": api_key,
            "mode": "payment",
            "line_items": [{
                "price_data": {
                    "currency": currency,
                    "product_data": {
                        "name": f"Bus Ticket Reservation — PNR {payment.booking.pnr}",
                        "description": f"{origin_city} to {dest_city} ({payment.booking.seats.count()} seat(s))",
                    },
                    "unit_amount": unit_amount,
                },
                "quantity": 1,
            }],
            "client_reference_id": str(payment.id),
            "metadata": tx_metadata,
            "payment_intent_data": {"metadata": tx_metadata},
            "success_url": success_url,
            "cancel_url": cancel_url,
            "idempotency_key": payment.idempotency_key,
        }
        if payment.booking.passenger_email:
            session_kwargs["customer_email"] = payment.booking.passenger_email

        try:
            session = stripe.checkout.Session.create(**session_kwargs)
        except stripe.StripeError as exc:
            err_msg = getattr(exc, 'user_message', None) or str(exc)
            raise BusinessLogicError(f"Stripe Checkout creation failed: {err_msg}")

        session_dict = self._to_plain_dict(session)
        return {
            "success": True,
            "gateway_reference": session.id,
            "checkout_url": session.url,
            "client_secret": getattr(session, 'client_secret', None),
            "raw_data": session_dict,
        }

    def retrieve_checkout_session(self, session_id: str) -> Dict[str, Any]:
        """
        Retrieves a Stripe Checkout Session directly from Stripe's API
        to verify payment status synchronously on redirect return.
        """
        api_key = self._get_api_key()
        try:
            session = stripe.checkout.Session.retrieve(session_id, api_key=api_key)
        except stripe.StripeError as exc:
            err_msg = getattr(exc, 'user_message', None) or str(exc)
            raise BusinessLogicError(f"Failed to retrieve Stripe session: {err_msg}")
        return self._to_plain_dict(session)

    def verify_webhook(
        self,
        payload: Dict[str, Any],
        signature: str = '',
        headers: Optional[Dict[str, str]] = None,
        raw_body: Optional[bytes] = None
    ) -> Dict[str, Any]:
        """
        Verifies Stripe webhook authenticity via Stripe-Signature and
        STRIPE_WEBHOOK_SECRET using raw unparsed request bytes.
        """
        webhook_secret = getattr(settings, 'STRIPE_WEBHOOK_SECRET', '')
        event_dict = payload

        if webhook_secret:
            if not signature or raw_body is None:
                return {
                    "is_valid": False,
                    "event_type": "INVALID",
                    "failure_reason": "Missing Stripe-Signature header or raw payload body.",
                    "raw_data": payload,
                }
            try:
                raw_str = raw_body.decode('utf-8') if isinstance(raw_body, (bytes, bytearray)) else str(raw_body)
                event = stripe.Webhook.construct_event(
                    payload=raw_str,
                    sig_header=signature,
                    secret=webhook_secret,
                )
                event_dict = self._to_plain_dict(event)
            except (ValueError, stripe.SignatureVerificationError) as exc:
                return {
                    "is_valid": False,
                    "event_type": "INVALID",
                    "failure_reason": f"Stripe signature verification failed: {exc}",
                    "raw_data": payload,
                }
        elif raw_body and not event_dict:
            try:
                event_dict = json.loads(raw_body.decode('utf-8'))
            except Exception:
                pass

        event_type = event_dict.get('type', '')
        data_obj = event_dict.get('data', {}).get('object', {})

        is_success = event_type in ('checkout.session.completed', 'payment_intent.succeeded')
        is_failure = event_type in (
            'payment_intent.payment_failed',
            'charge.failed',
            'checkout.session.expired',
        )

        # For async checkout sessions, only mark SUCCESS if payment_status is actually paid
        if event_type == 'checkout.session.completed' and data_obj.get('payment_status') == 'unpaid':
            status_val = 'PROCESSING'
        else:
            status_val = 'SUCCESS' if is_success else ('FAILED' if is_failure else 'PROCESSING')

        metadata = data_obj.get('metadata') or {}
        payment_id = metadata.get('payment_id') or data_obj.get('client_reference_id') or ''
        idempotency_key = metadata.get('idempotency_key', '')

        # Prefer PaymentIntent ID when available so refunds have it directly, fallback to object id (cs_...)
        gateway_reference = data_obj.get('payment_intent') or data_obj.get('id') or ''

        amount_raw = data_obj.get('amount_total') if data_obj.get('amount_total') is not None else data_obj.get('amount')
        amount = (Decimal(str(amount_raw)) / Decimal('100.00')) if amount_raw is not None else None

        last_err = data_obj.get('last_payment_error') or {}
        failure_reason = last_err.get('message', '') or data_obj.get('failure_message', '')

        return {
            "is_valid": True,
            "event_type": status_val,
            "payment_id": str(payment_id),
            "idempotency_key": idempotency_key,
            "gateway_reference": gateway_reference,
            "amount": amount,
            "currency": (data_obj.get('currency') or 'pkr').upper(),
            "failure_reason": failure_reason,
            "raw_data": event_dict,
        }

    def process_refund(
        self,
        payment,
        amount: Decimal,
        reason: str = ''
    ) -> Dict[str, Any]:
        """
        Executes a live full or partial refund through Stripe's Refunds API.
        Resolves PaymentIntent ID from payment record or Checkout Session if needed.
        """
        api_key = self._get_api_key()
        unit_amount = int(round_currency(amount) * 100)

        # Resolve PaymentIntent ID (pi_...)
        payment_intent = None
        if payment.gateway_reference and payment.gateway_reference.startswith('pi_'):
            payment_intent = payment.gateway_reference
        elif isinstance(payment.gateway_response_raw, dict):
            raw = payment.gateway_response_raw
            payment_intent = raw.get('payment_intent') or (
                raw.get('data', {}).get('object', {}).get('payment_intent')
            )

        if not payment_intent and payment.gateway_reference and payment.gateway_reference.startswith('cs_'):
            session = stripe.checkout.Session.retrieve(payment.gateway_reference, api_key=api_key)
            payment_intent = getattr(session, 'payment_intent', None)

        if not payment_intent:
            raise BusinessLogicError(
                "Cannot execute Stripe refund: no Stripe PaymentIntent ID is linked to this payment yet."
            )

        try:
            refund = stripe.Refund.create(
                api_key=api_key,
                payment_intent=payment_intent,
                amount=unit_amount,
                reason="requested_by_customer",
                metadata={
                    "booking_pnr": payment.booking.pnr,
                    "payment_id": str(payment.id),
                    "refund_reason": (reason or "Booking cancellation")[:200],
                },
            )
        except stripe.StripeError as exc:
            err_msg = getattr(exc, 'user_message', None) or str(exc)
            raise BusinessLogicError(f"Stripe refund failed: {err_msg}")

        refund_dict = self._to_plain_dict(refund)
        return {
            "success": True,
            "refund_reference": refund.id,
            "refunded_amount": amount,
            "raw_data": refund_dict,
        }
