"""
Comprehensive test suite for Module 7: Payments, Webhooks & Financial Reconciliation.
Tests:
  1. Payment initiation for pending bookings (amounts, idempotency key, gateway session)
  2. Idempotency guarantees (duplicate keys, collision rejection)
  3. Rejection of payments on confirmed or expired bookings
  4. Atomic payment success capture (Booking CONFIRMED, Seats BOOKED, Receipt issued)
  5. Idempotent success capture (safe re-entrant execution)
  6. Payment failure handling & immediate seat inventory release
  7. Gateway Webhook ingestion (success, failure, duplicate replay)
  8. Cryptographic webhook signature verification
  9. Refund processing (monetary bounds, status transitions)
  10. DRF API endpoints (initiate, confirm-mock, detail, receipt, booking payments list)
  11. Live Stripe Hosted Checkout, Stripe-Signature verification, refund & session verification
"""
import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from common.constants import (
    BusType,
    BusStatus,
    TripStatus,
    BookingStatus,
    BookingSeatStatus,
    PaymentMethod,
    PaymentStatus,
    Gender,
)
from common.exceptions import BusinessLogicError
from fleet.models import Bus, Seat
from fleet.services import generate_bus_seat_matrix
from routes.models import Terminal
from routes.services import RouteService
from trips.models import Trip
from trips.services import FareService
from bookings.services import SeatLockService, BookingService
from payments.models import Payment, PaymentReceipt
from payments.services import PaymentService


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def test_network(db):
    """
    3-stop corridor:
      Stop 0: Lahore (km 0)
      Stop 1: Bhera (km 190)
      Stop 2: Islamabad (km 375)
    """
    term_lhr = Terminal.objects.create(name="Lahore Terminal", code="LHR-PM-01", city="Lahore", is_active=True)
    term_bhr = Terminal.objects.create(name="Bhera Station", code="BHR-PM-01", city="Bhera", is_active=True)
    term_isb = Terminal.objects.create(name="Islamabad Terminal", code="ISB-PM-01", city="Islamabad", is_active=True)

    route = RouteService.create_route(
        code="LHR-ISB-PM",
        name="Lahore to Islamabad Corridor",
        origin=term_lhr,
        destination=term_isb,
        total_distance_km=Decimal('375.00'),
        estimated_duration_minutes=260,
        stops_data=[
            {'terminal': term_lhr, 'stop_order': 0, 'distance_from_origin_km': Decimal('0.00'), 'offset_minutes': 0},
            {'terminal': term_bhr, 'stop_order': 1, 'distance_from_origin_km': Decimal('190.00'), 'offset_minutes': 120},
            {'terminal': term_isb, 'stop_order': 2, 'distance_from_origin_km': Decimal('375.00'), 'offset_minutes': 260},
        ]
    )

    bus = Bus.objects.create(
        registration_number="LES-PM-888",
        bus_type=BusType.EXECUTIVE,
        status=BusStatus.ACTIVE,
        total_seats=40
    )
    generate_bus_seat_matrix(bus, layout_type="2x2", rows=10, ladies_rows=[1, 2])

    now = timezone.now().replace(microsecond=0)
    dep = now + timedelta(days=1)
    arr = dep + timedelta(minutes=260)
    trip = Trip.objects.create(
        trip_number="TRIP-PM-001",
        route=route,
        bus=bus,
        departure_datetime=dep,
        arrival_datetime=arr,
        status=TripStatus.SCHEDULED,
        base_fare=Decimal("2000.00")
    )

    stops = list(route.stops.order_by('stop_order'))
    s0, s1, s2 = stops[0], stops[1], stops[2]

    FareService.set_fare_rule(route, s0, s1, Decimal("1100.00"))
    FareService.set_fare_rule(route, s1, s2, Decimal("1100.00"))
    FareService.set_fare_rule(route, s0, s2, Decimal("2000.00"))

    return {
        'route': route,
        'bus': bus,
        'trip': trip,
        'stops': stops,
        'term_lhr': term_lhr,
        'term_bhr': term_bhr,
        'term_isb': term_isb,
    }


@pytest.fixture
def pending_booking(test_network):
    """
    Creates a pending booking with 1 locked seat (Seat 3A, Lahore to Islamabad).
    """
    trip = test_network['trip']
    s0 = test_network['stops'][0]
    s2 = test_network['stops'][2]
    seat_3a = Seat.objects.get(bus=test_network['bus'], seat_number="3A")

    lock_info = SeatLockService.lock_seats(
        trip=trip,
        seat_ids=[seat_3a.id],
        from_stop=s0,
        to_stop=s2
    )

    booking = BookingService.create_booking_from_locks(
        trip=trip,
        lock_token=uuid.UUID(lock_info['lock_token']),
        from_stop=s0,
        to_stop=s2,
        passenger_name="Ahmad Raza",
        passenger_phone="+923001234567",
        passenger_email="ahmad@example.com",
        passengers_data=[
            {
                'seat_id': seat_3a.id,
                'passenger_name': 'Ahmad Raza',
                'age': 29,
                'gender': Gender.MALE
            }
        ]
    )
    return booking


@pytest.mark.django_db
class TestPaymentService:

    def test_payment_initiation_success(self, pending_booking):
        payment, gateway_info = PaymentService.initiate_payment(
            booking_id=pending_booking.id,
            method=PaymentMethod.CARD_ONLINE,
            gateway_name='mock'
        )

        assert payment is not None
        assert payment.booking == pending_booking
        assert payment.amount == pending_booking.net_amount
        assert payment.currency == 'PKR'
        assert payment.status == PaymentStatus.INITIATED
        assert payment.gateway_name == 'mock'
        assert payment.gateway_reference.startswith('MOCK-TXN-')
        assert payment.idempotency_key.startswith('IDEMP-')
        assert gateway_info['success'] is True
        assert 'checkout_url' in gateway_info

    def test_payment_initiation_idempotency(self, pending_booking):
        custom_key = "MY-CUSTOM-IDEMP-KEY-999"

        # First call
        payment1, _ = PaymentService.initiate_payment(
            booking_id=pending_booking.id,
            idempotency_key=custom_key
        )

        # Second call with identical key
        payment2, _ = PaymentService.initiate_payment(
            booking_id=pending_booking.id,
            idempotency_key=custom_key
        )

        # Must return the exact same payment object
        assert payment1.id == payment2.id
        assert Payment.objects.filter(idempotency_key=custom_key).count() == 1

    def test_idempotency_key_collision_different_booking(self, pending_booking, test_network):
        # Create a second booking
        trip = test_network['trip']
        s0, s2 = test_network['stops'][0], test_network['stops'][2]
        seat_3b = Seat.objects.get(bus=test_network['bus'], seat_number="3B")

        lock_info = SeatLockService.lock_seats(trip=trip, seat_ids=[seat_3b.id], from_stop=s0, to_stop=s2)
        booking2 = BookingService.create_booking_from_locks(
            trip=trip,
            lock_token=uuid.UUID(lock_info['lock_token']),
            from_stop=s0,
            to_stop=s2,
            passenger_name="Bilal Tariq",
            passenger_phone="+923009998877",
            passengers_data=[{'seat_id': seat_3b.id, 'passenger_name': 'Bilal Tariq', 'gender': Gender.MALE}]
        )

        shared_key = "COLLISION-KEY-001"
        PaymentService.initiate_payment(booking_id=pending_booking.id, idempotency_key=shared_key)

        # Attempt to use same idempotency key for booking2 must be rejected
        with pytest.raises(BusinessLogicError, match="Idempotency key collision"):
            PaymentService.initiate_payment(booking_id=booking2.id, idempotency_key=shared_key)

    def test_payment_initiation_rejected_for_confirmed_booking(self, pending_booking):
        # Confirm booking
        pending_booking.status = BookingStatus.CONFIRMED
        pending_booking.save()

        with pytest.raises(BusinessLogicError, match="is already confirmed"):
            PaymentService.initiate_payment(booking_id=pending_booking.id)

    def test_payment_initiation_rejected_for_expired_booking(self, pending_booking):
        pending_booking.expires_at = timezone.now() - timedelta(minutes=5)
        pending_booking.save()

        with pytest.raises(BusinessLogicError, match="Booking has expired"):
            PaymentService.initiate_payment(booking_id=pending_booking.id)

    def test_process_payment_success(self, pending_booking):
        payment, _ = PaymentService.initiate_payment(booking_id=pending_booking.id)

        receipt = PaymentService.process_payment_success(
            payment_id=payment.id,
            gateway_reference="TXN-CAPTURE-12345",
            raw_response={"status": "PAID"}
        )

        # Assert payment transitions
        payment.refresh_from_db()
        assert payment.status == PaymentStatus.SUCCESS
        assert payment.paid_at is not None
        assert payment.gateway_reference == "TXN-CAPTURE-12345"

        # Assert booking transitions
        pending_booking.refresh_from_db()
        assert pending_booking.status == BookingStatus.CONFIRMED

        # Assert seats transition from LOCKED to BOOKED
        for seat in pending_booking.seats.all():
            assert seat.status == BookingSeatStatus.BOOKED

        # Assert receipt generation
        assert receipt is not None
        assert receipt.receipt_number.startswith("REC-")
        assert receipt.gross_amount == pending_booking.total_gross_fare
        assert receipt.tax_amount == pending_booking.tax_amount
        assert receipt.service_fee == pending_booking.service_fee
        assert receipt.net_amount == payment.amount

    def test_process_payment_success_idempotency(self, pending_booking):
        payment, _ = PaymentService.initiate_payment(booking_id=pending_booking.id)

        # First capture
        receipt1 = PaymentService.process_payment_success(payment_id=payment.id)
        # Second capture (e.g. repeated webhook or retry)
        receipt2 = PaymentService.process_payment_success(payment_id=payment.id)

        assert receipt1.id == receipt2.id
        assert PaymentReceipt.objects.filter(payment=payment).count() == 1

    def test_process_payment_failure_releases_seats(self, pending_booking):
        seat_id = pending_booking.seats.first().seat_id
        payment, _ = PaymentService.initiate_payment(booking_id=pending_booking.id)

        PaymentService.process_payment_failure(
            payment_id=payment.id,
            failure_reason="Card was declined by issuing bank"
        )

        payment.refresh_from_db()
        assert payment.status == PaymentStatus.FAILED
        assert "declined" in payment.failure_reason

        pending_booking.refresh_from_db()
        assert pending_booking.status == BookingStatus.EXPIRED

        # Seats must be CANCELLED, freeing them back into inventory
        for s in pending_booking.seats.all():
            assert s.status == BookingSeatStatus.CANCELLED

        # Verify seat can now be locked by another passenger
        s0, s2 = pending_booking.from_stop, pending_booking.to_stop
        new_lock = SeatLockService.lock_seats(
            trip=pending_booking.trip,
            seat_ids=[seat_id],
            from_stop=s0,
            to_stop=s2
        )
        assert new_lock['lock_token'] is not None

    def test_webhook_success_capture(self, pending_booking):
        payment, _ = PaymentService.initiate_payment(booking_id=pending_booking.id)

        webhook_payload = {
            "payment_id": str(payment.id),
            "idempotency_key": payment.idempotency_key,
            "status": "SUCCESS",
            "gateway_reference": "WH-REF-998877",
            "amount": str(payment.amount),
        }

        pay, processed, msg = PaymentService.handle_webhook(
            gateway_name="mock",
            payload=webhook_payload
        )

        assert processed is True
        assert "confirmed" in msg.lower()
        pay.refresh_from_db()
        assert pay.status == PaymentStatus.SUCCESS
        assert pay.booking.status == BookingStatus.CONFIRMED

    def test_webhook_failure_release(self, pending_booking):
        payment, _ = PaymentService.initiate_payment(booking_id=pending_booking.id)

        webhook_payload = {
            "payment_id": str(payment.id),
            "idempotency_key": payment.idempotency_key,
            "status": "FAILED",
            "failure_reason": "Insufficient balance",
        }

        pay, processed, _ = PaymentService.handle_webhook(
            gateway_name="mock",
            payload=webhook_payload
        )

        assert processed is True
        pay.refresh_from_db()
        assert pay.status == PaymentStatus.FAILED
        assert pay.booking.status == BookingStatus.EXPIRED

    def test_webhook_duplicate_replay_idempotent(self, pending_booking):
        payment, _ = PaymentService.initiate_payment(booking_id=pending_booking.id)

        webhook_payload = {
            "payment_id": str(payment.id),
            "status": "SUCCESS",
            "gateway_reference": "REPLAY-TXN-01",
        }

        # Send once
        PaymentService.handle_webhook("mock", webhook_payload)
        # Send again
        pay2, _, _ = PaymentService.handle_webhook("mock", webhook_payload)

        assert pay2.status == PaymentStatus.SUCCESS
        assert PaymentReceipt.objects.filter(payment=payment).count() == 1

    def test_process_refund_flow(self, pending_booking):
        payment, _ = PaymentService.initiate_payment(booking_id=pending_booking.id)
        PaymentService.process_payment_success(payment_id=payment.id)

        refunded_payment = PaymentService.process_refund(
            payment_id=payment.id,
            refund_amount=payment.amount,
            reason="Customer requested full refund"
        )

        assert refunded_payment.status == PaymentStatus.REFUNDED
        pending_booking.refresh_from_db()
        assert pending_booking.status == BookingStatus.REFUNDED


@pytest.mark.django_db
class TestPaymentAPI:

    def test_api_initiate_payment(self, api_client, pending_booking):
        from accounts.models import User
        pax = pending_booking.user or User.objects.create_user(phone="+923009991111", password="Password123!")
        api_client.force_authenticate(user=pax)
        url = reverse('payment-initiate')
        response = api_client.post(url, {
            'booking_id': str(pending_booking.id),
            'method': PaymentMethod.CARD_ONLINE,
            'gateway_name': 'mock'
        }, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        res_data = response.data['data']
        assert 'payment' in res_data
        assert 'gateway' in res_data
        assert res_data['payment']['booking_pnr'] == pending_booking.pnr
        assert res_data['payment']['status'] == PaymentStatus.INITIATED

    def test_api_confirm_mock_payment_success(self, api_client, pending_booking):
        payment, _ = PaymentService.initiate_payment(booking_id=pending_booking.id)

        url = reverse('payment-confirm-mock')
        response = api_client.post(url, {
            'payment_id': str(payment.id),
            'status': 'SUCCESS'
        }, format='json')

        assert response.status_code == status.HTTP_200_OK
        res_data = response.data['data']
        assert res_data['status'] == PaymentStatus.SUCCESS
        assert 'receipt' in res_data
        assert res_data['receipt']['net_amount'] == str(payment.amount)

    def test_api_confirm_mock_payment_failure(self, api_client, pending_booking):
        payment, _ = PaymentService.initiate_payment(booking_id=pending_booking.id)

        url = reverse('payment-confirm-mock')
        response = api_client.post(url, {
            'payment_id': str(payment.id),
            'status': 'FAILED',
            'failure_reason': 'Card expired'
        }, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        error_details = response.data['error']['details']
        assert error_details['status'] == PaymentStatus.FAILED

    def test_api_get_payment_details_and_receipt(self, api_client, pending_booking):
        payment, _ = PaymentService.initiate_payment(booking_id=pending_booking.id)
        receipt = PaymentService.process_payment_success(payment_id=payment.id)

        # GET payment detail
        det_url = reverse('payment-detail', kwargs={'id': payment.id})
        det_res = api_client.get(det_url)
        assert det_res.status_code == status.HTTP_200_OK
        assert det_res.data['data']['receipt']['receipt_number'] == receipt.receipt_number

        # GET receipt detail
        rec_url = reverse('payment-receipt-detail', kwargs={'receipt_number': receipt.receipt_number})
        rec_res = api_client.get(rec_url)
        assert rec_res.status_code == status.HTTP_200_OK
        assert rec_res.data['data']['receipt_number'] == receipt.receipt_number

        # GET booking payments list
        bk_url = reverse('payment-booking-list', kwargs={'pnr': pending_booking.pnr})
        bk_res = api_client.get(bk_url)
        assert bk_res.status_code == status.HTTP_200_OK
        assert len(bk_res.data['data']) == 1


@pytest.mark.django_db
class TestStripeIntegration:

    def test_stripe_checkout_session_creation_and_webhook_signature(self, api_client, pending_booking, settings):
        settings.STRIPE_SECRET_KEY = 'sk_test_unit_mock_key'
        settings.STRIPE_WEBHOOK_SECRET = 'whsec_unit_test_secret'

        fake_session = SimpleNamespace(
            id='cs_test_unit_12345',
            url='https://checkout.stripe.com/c/pay/cs_test_unit_12345',
            client_secret=None,
            to_dict=lambda: {
                'id': 'cs_test_unit_12345',
                'object': 'checkout.session',
                'url': 'https://checkout.stripe.com/c/pay/cs_test_unit_12345',
            }
        )

        with patch('stripe.checkout.Session.create', return_value=fake_session) as mock_create:
            payment, gw = PaymentService.initiate_payment(
                booking_id=pending_booking.id,
                gateway_name='stripe',
                return_url='http://127.0.0.1:8000/'
            )

        assert mock_create.called
        assert payment.gateway_name == 'stripe'
        assert payment.gateway_reference == 'cs_test_unit_12345'
        assert gw['checkout_url'] == 'https://checkout.stripe.com/c/pay/cs_test_unit_12345'

        # Construct real Stripe-Signature header over raw bytes
        event_payload = {
            "id": "evt_test_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_unit_12345",
                    "object": "checkout.session",
                    "payment_status": "paid",
                    "payment_intent": "pi_test_unit_999",
                    "amount_total": int(payment.amount * 100),
                    "currency": "pkr",
                    "client_reference_id": str(payment.id),
                    "metadata": {
                        "payment_id": str(payment.id),
                        "booking_pnr": pending_booking.pnr,
                    }
                }
            }
        }
        raw_json = json.dumps(event_payload).encode('utf-8')
        ts = int(time.time())
        signed_payload = f"{ts}.{raw_json.decode('utf-8')}".encode('utf-8')
        sig = hmac.new(settings.STRIPE_WEBHOOK_SECRET.encode('utf-8'), signed_payload, hashlib.sha256).hexdigest()
        stripe_sig_header = f"t={ts},v1={sig}"

        # Tampered signature must be rejected (400)
        wh_url = reverse('payment-webhook', kwargs={'gateway_name': 'stripe'})
        bad_res = api_client.post(
            wh_url,
            data=raw_json,
            content_type='application/json',
            HTTP_STRIPE_SIGNATURE=f"t={ts},v1=deadbeef"
        )
        assert bad_res.status_code == status.HTTP_400_BAD_REQUEST

        # Valid signed webhook must confirm booking & store PaymentIntent reference
        ok_res = api_client.post(
            wh_url,
            data=raw_json,
            content_type='application/json',
            HTTP_STRIPE_SIGNATURE=stripe_sig_header
        )
        assert ok_res.status_code == status.HTTP_200_OK
        payment.refresh_from_db()
        pending_booking.refresh_from_db()
        assert payment.status == PaymentStatus.SUCCESS
        assert payment.gateway_reference == 'pi_test_unit_999'
        assert pending_booking.status == BookingStatus.CONFIRMED

        # Refund via Stripe Refund API using stored PaymentIntent
        fake_refund = SimpleNamespace(
            id='re_test_unit_777',
            to_dict=lambda: {'id': 're_test_unit_777', 'status': 'succeeded'}
        )
        with patch('stripe.Refund.create', return_value=fake_refund) as mock_refund:
            refunded = PaymentService.process_refund(
                payment_id=payment.id,
                refund_amount=payment.amount,
                reason="Unit test refund"
            )
        assert mock_refund.called
        assert mock_refund.call_args.kwargs['payment_intent'] == 'pi_test_unit_999'
        assert refunded.status == PaymentStatus.REFUNDED

    def test_verify_stripe_session_endpoint(self, api_client, pending_booking, settings):
        settings.STRIPE_SECRET_KEY = 'sk_test_unit_mock_key'

        payment = Payment.objects.create(
            booking=pending_booking,
            amount=pending_booking.net_amount,
            currency='PKR',
            method=PaymentMethod.STRIPE,
            status=PaymentStatus.INITIATED,
            gateway_name='stripe',
            gateway_reference='cs_test_verify_001',
            idempotency_key=f"IDEMP-{uuid.uuid4().hex}",
        )

        mock_session_dict = {
            'id': 'cs_test_verify_001',
            'status': 'complete',
            'payment_status': 'paid',
            'payment_intent': 'pi_test_verify_001',
            'client_reference_id': str(payment.id),
            'metadata': {'payment_id': str(payment.id)},
        }

        with patch('payments.gateways.stripe_gateway.StripePaymentGateway.retrieve_checkout_session', return_value=mock_session_dict):
            res = api_client.post(
                reverse('payment-verify-stripe-session'),
                data={'session_id': 'cs_test_verify_001'},
                format='json'
            )

        assert res.status_code == status.HTTP_200_OK
        assert res.data['data']['payment']['status'] == PaymentStatus.SUCCESS
        assert res.data['data']['receipt'] is not None

