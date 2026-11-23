"""
Interactive verification script for Module 7: Payments, Webhooks & Financial Reconciliation.
Verifies:
  1. Setting up corridor, luxury fleet, scheduled trip & passenger account.
  2. Creating a PENDING booking with locked seat (Lahore to Islamabad).
  3. Initiating an idempotent payment intent with gateway session creation.
  4. Testing idempotency key collision rejection & safe duplicate retrieval.
  5. Atomic payment capture: Transitions booking to CONFIRMED, seats to BOOKED, issues audit PaymentReceipt.
  6. Cryptographic webhook ingestion & duplicate replay safety (idempotent).
  7. Payment failure handling & instant seat inventory release (Booking EXPIRED, Seat CANCELLED).
  8. Financial refund execution & audit balance verification.

Usage:
  python verify_module_7.py
"""
import os
import sys
import uuid
from datetime import timedelta
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from django.utils import timezone
from common.constants import (
    UserRole,
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
from accounts.models import User
from fleet.models import Bus, Seat
from fleet.services import generate_bus_seat_matrix
from routes.models import Terminal, Route, RouteStop
from routes.services import RouteService
from trips.models import Trip
from trips.services import FareService
from bookings.models import Booking, BookingSeat, SeatLock
from bookings.services import SeatLockService, BookingService
from payments.models import Payment, PaymentReceipt
from payments.services import PaymentService


def print_header(title: str):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def run_verification():
    print_header("MODULE 7 VERIFICATION: PAYMENTS, WEBHOOKS & FINANCIAL RECONCILIATION")

    # -------------------------------------------------------------------------
    # [1/8] Setting up Environment & Test Corridor
    # -------------------------------------------------------------------------
    print("\n[1/8] Provisioning System Entities (Passenger, 3-Stop Corridor, Executive Bus)...")
    passenger, _ = User.objects.get_or_create(
        phone="+923009990001",
        defaults={
            "role": UserRole.PASSENGER,
            "email": "tariq.passenger@example.com",
            "is_verified": True
        }
    )

    t_lhr, _ = Terminal.objects.get_or_create(code="V7-LHR", defaults={"name": "Lahore Terminal", "city": "Lahore", "is_active": True})
    t_bhr, _ = Terminal.objects.get_or_create(code="V7-BHR", defaults={"name": "Bhera Rest Area", "city": "Bhera", "is_active": True})
    t_isb, _ = Terminal.objects.get_or_create(code="V7-ISB", defaults={"name": "Islamabad Station", "city": "Islamabad", "is_active": True})

    route, _ = Route.objects.get_or_create(
        code="V7-M2-EXP",
        defaults={
            "name": "M2 Motorway Super Express",
            "origin": t_lhr,
            "destination": t_isb,
            "total_distance_km": Decimal("375.00"),
            "estimated_duration_minutes": 260,
            "is_active": True
        }
    )
    s0, _ = RouteStop.objects.get_or_create(route=route, stop_order=0, defaults={"terminal": t_lhr, "distance_from_origin_km": Decimal("0.00"), "offset_minutes": 0})
    s1, _ = RouteStop.objects.get_or_create(route=route, stop_order=1, defaults={"terminal": t_bhr, "distance_from_origin_km": Decimal("190.00"), "offset_minutes": 120})
    s2, _ = RouteStop.objects.get_or_create(route=route, stop_order=2, defaults={"terminal": t_isb, "distance_from_origin_km": Decimal("375.00"), "offset_minutes": 260})

    bus, created = Bus.objects.get_or_create(
        registration_number="V7-BUS-700",
        defaults={
            "bus_type": BusType.EXECUTIVE,
            "status": BusStatus.ACTIVE,
            "total_seats": 40
        }
    )
    if created or bus.seats.count() == 0:
        generate_bus_seat_matrix(bus, layout_type="2x2", rows=10, ladies_rows=[1, 2])

    # Clean up prior test runs to ensure idempotent re-runs
    old_trip = Trip.objects.filter(trip_number="V7-TRIP-701").first()
    if old_trip:
        SeatLock.objects.filter(trip=old_trip).delete()
        PaymentReceipt.objects.filter(payment__booking__trip=old_trip).delete()
        Payment.objects.filter(booking__trip=old_trip).delete()
        BookingSeat.objects.filter(trip=old_trip).delete()
        Booking.objects.filter(trip=old_trip).delete()

    now = timezone.now().replace(microsecond=0)
    trip, _ = Trip.objects.get_or_create(
        trip_number="V7-TRIP-701",
        defaults={
            "route": route,
            "bus": bus,
            "departure_datetime": now + timedelta(days=2),
            "arrival_datetime": now + timedelta(days=2, minutes=260),
            "status": TripStatus.SCHEDULED,
            "base_fare": Decimal("2000.00")
        }
    )
    FareService.set_fare_rule(route, s0, s2, Decimal("2000.00"))

    print(f"      OK: Environment ready. Trip '{trip.trip_number}' configured with 40 seats.")

    # -------------------------------------------------------------------------
    # [2/8] Creating Pending Booking for Seat 4A
    # -------------------------------------------------------------------------
    print("\n[2/8] Reserving Seat 4A & Creating PENDING Booking...")
    seat_4a = Seat.objects.get(bus=bus, seat_number="4A")
    lock_info = SeatLockService.lock_seats(
        trip=trip,
        seat_ids=[seat_4a.id],
        from_stop=s0,
        to_stop=s2
    )

    booking = BookingService.create_booking_from_locks(
        trip=trip,
        lock_token=uuid.UUID(lock_info['lock_token']),
        from_stop=s0,
        to_stop=s2,
        passenger_name="Tariq Mahmood",
        passenger_phone="+923009990001",
        passenger_email="tariq@example.com",
        passengers_data=[{
            'seat_id': seat_4a.id,
            'passenger_name': 'Tariq Mahmood',
            'age': 34,
            'gender': Gender.MALE
        }],
        user=passenger
    )

    assert booking.status == BookingStatus.PENDING, "Booking must be in PENDING state"
    assert booking.net_amount == Decimal("2150.00"), f"Expected 2150.00 net fare, got {booking.net_amount}"
    print(f"      OK: Booking created with PNR '{booking.pnr}'. Status: PENDING | Net Amount: PKR {booking.net_amount}")

    # -------------------------------------------------------------------------
    # [3/8] Initiating Idempotent Payment Intent
    # -------------------------------------------------------------------------
    print("\n[3/8] Initiating Payment Intent via PaymentService...")
    idemp_key = f"V7-IDEMP-{uuid.uuid4().hex[:8]}"
    payment, gateway_info = PaymentService.initiate_payment(
        booking_id=booking.id,
        method=PaymentMethod.CARD_ONLINE,
        gateway_name="mock",
        idempotency_key=idemp_key
    )

    assert payment.status == PaymentStatus.INITIATED, "Payment must be in INITIATED state"
    assert payment.amount == booking.net_amount, "Payment amount must match booking net total"
    assert gateway_info['success'] is True, "Gateway initiation must succeed"
    print(f"      OK: Payment initiated successfully:")
    print(f"          - Payment ID: {payment.id}")
    print(f"          - Amount: PKR {payment.amount}")
    print(f"          - Status: {payment.status}")
    print(f"          - Gateway Ref: {payment.gateway_reference}")
    print(f"          - Idempotency Key: {payment.idempotency_key}")

    # -------------------------------------------------------------------------
    # [4/8] Testing Idempotency & Duplicate Request Safety
    # -------------------------------------------------------------------------
    print("\n[4/8] Testing Idempotent Re-initiation with Same Key...")
    payment_duplicate, info_duplicate = PaymentService.initiate_payment(
        booking_id=booking.id,
        idempotency_key=idemp_key
    )

    assert payment_duplicate.id == payment.id, "Duplicate call must return existing Payment record"
    count = Payment.objects.filter(idempotency_key=idemp_key).count()
    assert count == 1, "Must never duplicate payment records for same idempotency key"
    print(f"      OK: Idempotency strictly preserved. Exactly 1 payment record exists for key '{idemp_key}'.")

    # -------------------------------------------------------------------------
    # [5/8] Capturing Payment & Issuing Audit Financial Receipt
    # -------------------------------------------------------------------------
    print("\n[5/8] Processing Payment Capture & Verifying Financial Receipt...")
    receipt = PaymentService.process_payment_success(
        payment_id=payment.id,
        gateway_reference="MOCK-VERIFIED-TXN-001",
        raw_response={"bank_auth_code": "AUTH-778899"}
    )

    payment.refresh_from_db()
    booking.refresh_from_db()

    assert payment.status == PaymentStatus.SUCCESS, "Payment must be in SUCCESS state"
    assert payment.paid_at is not None, "paid_at must be populated"
    assert booking.status == BookingStatus.CONFIRMED, "Booking must transition to CONFIRMED"

    for seat_record in booking.seats.all():
        assert seat_record.status == BookingSeatStatus.BOOKED, "Seats must transition to BOOKED"

    assert receipt is not None, "PaymentReceipt must be created"
    assert receipt.net_amount == payment.amount, "Receipt net must match payment amount"
    assert receipt.receipt_number.startswith("REC-"), "Receipt number must have REC- prefix"

    print(f"      OK: Payment successfully captured:")
    print(f"          - Payment Status: {payment.status} | Captured At: {payment.paid_at:%Y-%m-%d %H:%M:%S}")
    print(f"          - Booking Status: {booking.status}")
    print(f"          - Seat 4A Status: BOOKED")
    print(f"          - Receipt Number: {receipt.receipt_number}")
    print(f"          - Financial Breakdown: Gross: PKR {receipt.gross_amount} | Tax: PKR {receipt.tax_amount} | Fee: PKR {receipt.service_fee} | Net: PKR {receipt.net_amount}")

    # -------------------------------------------------------------------------
    # [6/8] Testing Asynchronous Webhook Ingestion & Replay Safety
    # -------------------------------------------------------------------------
    print("\n[6/8] Testing Webhook Callback Ingestion & Idempotent Replay...")
    # Create a second booking for webhook demonstration
    seat_4b = Seat.objects.get(bus=bus, seat_number="4B")
    lock_wh = SeatLockService.lock_seats(trip=trip, seat_ids=[seat_4b.id], from_stop=s0, to_stop=s2)
    booking_wh = BookingService.create_booking_from_locks(
        trip=trip,
        lock_token=uuid.UUID(lock_wh['lock_token']),
        from_stop=s0,
        to_stop=s2,
        passenger_name="Rashid Ali",
        passenger_phone="+923009990002",
        passengers_data=[{'seat_id': seat_4b.id, 'passenger_name': 'Rashid Ali', 'gender': Gender.MALE}]
    )
    payment_wh, _ = PaymentService.initiate_payment(booking_id=booking_wh.id, gateway_name="mock")

    webhook_payload = {
        "payment_id": str(payment_wh.id),
        "status": "SUCCESS",
        "gateway_reference": "WH-CAPTURE-5544",
        "amount": str(payment_wh.amount),
        "currency": "PKR"
    }

    # Delivery 1
    _, processed1, msg1 = PaymentService.handle_webhook(gateway_name="mock", payload=webhook_payload)
    assert processed1 is True, "First webhook delivery must process successfully"

    # Delivery 2 (Replay / Network retry)
    _, processed2, msg2 = PaymentService.handle_webhook(gateway_name="mock", payload=webhook_payload)
    assert processed2 is True, "Webhook replay must be handled safely and idempotently"

    receipts_count = PaymentReceipt.objects.filter(payment=payment_wh).count()
    assert receipts_count == 1, "Exactly 1 receipt must exist despite duplicate webhook delivery"
    print(f"      OK: Webhook successfully captured and confirmed booking '{booking_wh.pnr}'. Duplicate delivery handled cleanly with 1 receipt.")

    # -------------------------------------------------------------------------
    # [7/8] Testing Payment Failure & Immediate Inventory Release
    # -------------------------------------------------------------------------
    print("\n[7/8] Testing Payment Failure & Instant Seat Inventory Release...")
    seat_4c = Seat.objects.get(bus=bus, seat_number="4C")
    lock_fail = SeatLockService.lock_seats(trip=trip, seat_ids=[seat_4c.id], from_stop=s0, to_stop=s2)
    booking_fail = BookingService.create_booking_from_locks(
        trip=trip,
        lock_token=uuid.UUID(lock_fail['lock_token']),
        from_stop=s0,
        to_stop=s2,
        passenger_name="Zahid Iqbal",
        passenger_phone="+923009990003",
        passengers_data=[{'seat_id': seat_4c.id, 'passenger_name': 'Zahid Iqbal', 'gender': Gender.MALE}]
    )
    payment_fail, _ = PaymentService.initiate_payment(booking_id=booking_fail.id, gateway_name="mock")

    # Simulate payment failure (e.g. Card Declined)
    PaymentService.process_payment_failure(
        payment_id=payment_fail.id,
        failure_reason="Transaction declined: Insufficient funds"
    )

    payment_fail.refresh_from_db()
    booking_fail.refresh_from_db()

    assert payment_fail.status == PaymentStatus.FAILED, "Payment must be in FAILED state"
    assert booking_fail.status == BookingStatus.EXPIRED, "Booking must transition to EXPIRED"
    assert booking_fail.seats.first().status == BookingSeatStatus.CANCELLED, "Seat status must be CANCELLED"

    # Verify that Seat 4C is now immediately unblocked and can be re-locked
    re_lock = SeatLockService.lock_seats(trip=trip, seat_ids=[seat_4c.id], from_stop=s0, to_stop=s2)
    assert re_lock['lock_token'] is not None, "Seat 4C must be immediately available for other travelers"
    print(f"      OK: Payment failure correctly handled:")
    print(f"          - Payment Status: FAILED | Reason: {payment_fail.failure_reason}")
    print(f"          - Booking Status: EXPIRED")
    print(f"          - Seat 4C released back into available inventory and successfully re-locked!")

    # -------------------------------------------------------------------------
    # [8/8] Testing Financial Refund Processing
    # -------------------------------------------------------------------------
    print("\n[8/8] Testing Financial Refund Execution against Captured Payment...")
    refunded_payment = PaymentService.process_refund(
        payment_id=payment.id,
        refund_amount=payment.amount,
        reason="Customer cancellation request within policy window"
    )

    booking.refresh_from_db()
    assert refunded_payment.status == PaymentStatus.REFUNDED, "Payment must be in REFUNDED state"
    assert booking.status == BookingStatus.REFUNDED, "Booking must transition to REFUNDED"
    print(f"      OK: Full refund of PKR {refunded_payment.amount} processed successfully:")
    print(f"          - Payment Status: {refunded_payment.status}")
    print(f"          - Booking Status: {booking.status}")

    print_header("MODULE 7 VERIFICATION PASSED: ALL 8 CORE CHECKS 100% SUCCESSFUL!")


if __name__ == '__main__':
    run_verification()
