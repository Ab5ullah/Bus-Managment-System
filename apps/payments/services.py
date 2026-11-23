"""
Authoritative Domain Service Layer for Payments, Webhooks & Financial Reconciliation.
Guarantees:
- Strict monetary precision (Decimal 12, 2)
- Row-level locking with select_for_update() inside atomic transaction boundaries
- Cryptographic and idempotent webhook ingestion (no duplicate charges or double credits)
- Instant inventory release upon payment cancellation/failure
- Audit-compliant financial receipt generation
"""
import uuid
from decimal import Decimal
from typing import Dict, Any, Optional, Tuple
from django.db import transaction
from django.utils import timezone
from common.exceptions import BusinessLogicError
from common.constants import (
    BookingStatus,
    BookingSeatStatus,
    PaymentStatus,
    PaymentMethod,
)
from common.money import round_currency
from bookings.models import Booking, BookingSeat, SeatLock
from .models import Payment, PaymentReceipt
from .gateways import PaymentGatewayFactory


class PaymentService:
    """
    Core Domain Service managing payment intents, captures, webhooks, and refunds.
    """

    @classmethod
    @transaction.atomic
    def initiate_payment(
        cls,
        booking_id: Optional[uuid.UUID] = None,
        pnr: Optional[str] = None,
        method: str = PaymentMethod.CARD_ONLINE,
        gateway_name: str = 'mock',
        idempotency_key: Optional[str] = None,
        return_url: str = '',
        metadata: Optional[Dict[str, Any]] = None,
        user=None
    ) -> Tuple[Payment, Dict[str, Any]]:
        """
        Creates or retrieves an idempotent payment intent for a PENDING booking.
        """
        now = timezone.now()

        # 1. Fetch booking with row lock
        booking_qs = Booking.objects.select_for_update()
        if booking_id:
            booking = booking_qs.filter(id=booking_id).first()
        elif pnr:
            booking = booking_qs.filter(pnr=pnr.strip().upper()).first()
        else:
            raise BusinessLogicError("Either booking_id or pnr must be provided.")

        if not booking:
            raise BusinessLogicError("Booking not found.")

        # 2. Enforce booking state invariants
        if booking.status == BookingStatus.CONFIRMED:
            # Check if already paid
            existing_payment = Payment.objects.filter(
                booking=booking,
                status=PaymentStatus.SUCCESS
            ).first()
            if existing_payment:
                gateway = PaymentGatewayFactory.get_gateway(existing_payment.gateway_name)
                return existing_payment, {
                    "success": True,
                    "already_confirmed": True,
                    "gateway_reference": existing_payment.gateway_reference,
                    "checkout_url": ""
                }
            raise BusinessLogicError(f"Booking '{booking.pnr}' is already confirmed.")

        if booking.status in (BookingStatus.CANCELLED, BookingStatus.REFUNDED, BookingStatus.EXPIRED):
            raise BusinessLogicError(f"Cannot initiate payment for a {booking.get_status_display().lower()} booking.")

        # 3. Check booking expiration
        if booking.expires_at and booking.expires_at <= now:
            raise BusinessLogicError("Booking has expired. Please create a new booking.")

        # 4. Enforce / Verify Idempotency Key
        if idempotency_key:
            idempotency_key = str(idempotency_key).strip()
            existing_payment = Payment.objects.filter(idempotency_key=idempotency_key).first()
            if existing_payment:
                if existing_payment.booking_id != booking.id:
                    raise BusinessLogicError(
                        f"Idempotency key collision: key '{idempotency_key}' is already bound to another transaction."
                    )
                # Re-use existing initiated payment
                gateway = PaymentGatewayFactory.get_gateway(existing_payment.gateway_name)
                gateway_result = gateway.initiate_payment(
                    payment=existing_payment,
                    return_url=return_url,
                    metadata=metadata
                )
                return existing_payment, gateway_result
        else:
            idempotency_key = f"IDEMP-{uuid.uuid4().hex}"

        # 5. Resolve Payment Gateway
        gateway = PaymentGatewayFactory.get_gateway(gateway_name)

        # 6. Create Payment Record (Pending/Initiated)
        payment = Payment.objects.create(
            booking=booking,
            amount=round_currency(booking.net_amount),
            currency='PKR',
            method=method,
            status=PaymentStatus.INITIATED,
            gateway_name=gateway.gateway_name,
            idempotency_key=idempotency_key,
            gateway_response_raw={}
        )

        # 7. Call gateway initiation
        gateway_result = gateway.initiate_payment(
            payment=payment,
            return_url=return_url,
            metadata=metadata
        )

        # 8. Store gateway reference and payload
        payment.gateway_reference = gateway_result.get('gateway_reference', '')
        payment.gateway_response_raw = gateway_result.get('raw_data', {})
        payment.save(update_fields=['gateway_reference', 'gateway_response_raw', 'updated_at'])

        return payment, gateway_result

    @classmethod
    @transaction.atomic
    def process_payment_success(
        cls,
        payment_id: uuid.UUID,
        gateway_reference: str = '',
        raw_response: Optional[Dict[str, Any]] = None
    ) -> PaymentReceipt:
        """
        Confirms a payment, updates Booking to CONFIRMED, updates BookingSeats
        to BOOKED, and issues an audit PaymentReceipt.
        Fully idempotent: duplicate invocations safely return the existing receipt.
        """
        # Row lock payment and booking
        payment = Payment.objects.select_for_update().get(id=payment_id)
        booking = Booking.objects.select_for_update().get(id=payment.booking_id)

        # 1. Idempotency Check: Already captured?
        if payment.status == PaymentStatus.SUCCESS:
            receipt = PaymentReceipt.objects.filter(payment=payment).first()
            if receipt:
                return receipt
            # In the rare event receipt wasn't created, generate it below

        if payment.status == PaymentStatus.FAILED:
            raise BusinessLogicError("Cannot mark a previously failed payment as successful without re-initiating.")

        # 2. Update Payment
        now = timezone.now()
        payment.status = PaymentStatus.SUCCESS
        payment.paid_at = now
        if gateway_reference:
            payment.gateway_reference = gateway_reference
        if raw_response:
            payment.gateway_response_raw.update(raw_response)
        payment.save()

        # 3. Transition Booking to CONFIRMED
        booking.status = BookingStatus.CONFIRMED
        booking.save(update_fields=['status', 'updated_at'])

        # 4. Transition Seats to BOOKED
        BookingSeat.objects.filter(
            booking=booking,
            status=BookingSeatStatus.LOCKED
        ).update(status=BookingSeatStatus.BOOKED)

        # 5. Clean up any remaining temporary seat locks for this trip/booking
        seat_ids = list(booking.seats.values_list('seat_id', flat=True))
        SeatLock.objects.filter(
            trip=booking.trip,
            seat_id__in=seat_ids,
            from_stop_order=booking.from_stop_order,
            to_stop_order=booking.to_stop_order
        ).delete()

        # 6. Issue Audit-Compliant Financial Receipt
        receipt, _ = PaymentReceipt.objects.get_or_create(
            payment=payment,
            defaults={
                "gross_amount": booking.total_gross_fare,
                "discount_amount": booking.discount_amount,
                "tax_amount": booking.tax_amount,
                "service_fee": booking.service_fee,
                "net_amount": payment.amount,
            }
        )

        # 7. Automatically Issue Cryptographically Signed Boarding Tickets
        try:
            from tickets.services import TicketService
            TicketService.issue_tickets_for_booking(booking)
        except Exception:
            pass

        return receipt

    @classmethod
    @transaction.atomic
    def process_payment_failure(
        cls,
        payment_id: uuid.UUID,
        failure_reason: str,
        raw_response: Optional[Dict[str, Any]] = None
    ) -> Payment:
        """
        Marks payment as FAILED, releases held seats back into inventory,
        and marks the Booking as EXPIRED or CANCELLED.
        """
        payment = Payment.objects.select_for_update().get(id=payment_id)
        booking = Booking.objects.select_for_update().get(id=payment.booking_id)

        # Idempotency check
        if payment.status == PaymentStatus.FAILED:
            return payment

        if payment.status == PaymentStatus.SUCCESS:
            raise BusinessLogicError("Cannot fail an already captured and successful payment.")

        # 1. Update Payment Record
        payment.status = PaymentStatus.FAILED
        payment.failure_reason = failure_reason or "Payment capture failed"
        if raw_response:
            payment.gateway_response_raw.update(raw_response)
        payment.save()

        # 2. Release Seats & Transition Booking
        if booking.status == BookingStatus.PENDING:
            booking.status = BookingStatus.EXPIRED
            booking.save(update_fields=['status', 'updated_at'])

            # Transition locked seats to CANCELLED to release inventory immediately
            BookingSeat.objects.filter(
                booking=booking,
                status=BookingSeatStatus.LOCKED
            ).update(status=BookingSeatStatus.CANCELLED)

            # Delete any remaining temporary SeatLocks
            seat_ids = list(booking.seats.values_list('seat_id', flat=True))
            SeatLock.objects.filter(
                trip=booking.trip,
                seat_id__in=seat_ids,
                from_stop_order=booking.from_stop_order,
                to_stop_order=booking.to_stop_order
            ).delete()

        return payment

    @classmethod
    def handle_webhook(
        cls,
        gateway_name: str,
        payload: Dict[str, Any],
        signature: str = '',
        headers: Optional[Dict[str, str]] = None
    ) -> Tuple[Optional[Payment], bool, str]:
        """
        Central Webhook Ingestion Engine.
        Verifies gateway signature, resolves payment, and idempotently captures
        or releases seats.
        """
        gateway = PaymentGatewayFactory.get_gateway(gateway_name)
        verified = gateway.verify_webhook(payload, signature=signature, headers=headers)

        if not verified.get('is_valid', False):
            raise BusinessLogicError("Cryptographic webhook validation failed. Invalid signature.")

        # Resolve Payment by payment_id, idempotency_key, or gateway_reference
        payment_id = verified.get('payment_id')
        idempotency_key = verified.get('idempotency_key')
        gateway_reference = verified.get('gateway_reference')

        payment = None
        if payment_id:
            try:
                payment = Payment.objects.filter(id=payment_id).first()
            except Exception:
                pass

        if not payment and idempotency_key:
            payment = Payment.objects.filter(idempotency_key=idempotency_key).first()

        if not payment and gateway_reference:
            payment = Payment.objects.filter(gateway_reference=gateway_reference).first()

        if not payment:
            raise BusinessLogicError(
                "Webhook event received but no matching Payment transaction was found."
            )

        event_type = verified.get('event_type', '').upper()

        if event_type == 'SUCCESS':
            cls.process_payment_success(
                payment_id=payment.id,
                gateway_reference=verified.get('gateway_reference') or payment.gateway_reference,
                raw_response=verified.get('raw_data')
            )
            return payment, True, "Payment successfully confirmed and receipt issued."

        elif event_type in ('FAILED', 'CANCELLED'):
            cls.process_payment_failure(
                payment_id=payment.id,
                failure_reason=verified.get('failure_reason', 'Payment failed via webhook'),
                raw_response=verified.get('raw_data')
            )
            return payment, True, "Payment marked as failed; seats released back to inventory."

        return payment, False, f"Event '{event_type}' acknowledged without state mutation."

    @classmethod
    @transaction.atomic
    def process_refund(
        cls,
        payment_id: uuid.UUID,
        refund_amount: Optional[Decimal] = None,
        reason: str = ''
    ) -> Payment:
        """
        Executes a gateway refund against a captured payment.
        """
        payment = Payment.objects.select_for_update().get(id=payment_id)
        booking = Booking.objects.select_for_update().get(id=payment.booking_id)

        if payment.status != PaymentStatus.SUCCESS:
            raise BusinessLogicError("Only captured and successful payments can be refunded.")

        target_refund = refund_amount if refund_amount is not None else payment.amount
        target_refund = round_currency(target_refund)

        if target_refund <= Decimal('0.00'):
            raise BusinessLogicError("Refund amount must be strictly greater than 0.00.")

        if target_refund > payment.amount:
            raise BusinessLogicError(
                f"Refund amount ({target_refund}) cannot exceed original payment amount ({payment.amount})."
            )

        # Call gateway refund
        gateway = PaymentGatewayFactory.get_gateway(payment.gateway_name)
        refund_result = gateway.process_refund(payment, target_refund, reason=reason)

        payment.status = PaymentStatus.REFUNDED
        payment.gateway_response_raw.update(refund_result.get('raw_data', {}))
        payment.save()

        if target_refund == payment.amount:
            booking.status = BookingStatus.REFUNDED
            booking.save(update_fields=['status', 'updated_at'])

        return payment
