"""
Interactive verification script for Module 9: Cancellation & Refund Engine.
Verifies:
  1. Default cancellation policy seeding & tier resolution (>24h: 90%, 6-24h: 50%, <6h: 0%).
  2. Advance booking cancellation (>24h): 90% fare/tax refund, service fee retention, ticket voiding.
  3. Standard cancellation (6-24h): 50% refund calculation.
  4. Late cancellation (<6h): 0% refund enforcement (100% cancellation fee).
  5. Partial seat cancellation: booking status transition to PARTIALLY_CANCELLED & instant segment inventory recovery.
  6. Invariant guards: rejection of departed trip cancellation, boarded passenger cancellation, and double cancellation.
  7. Operator bulk trip cancellation: 100% full refund (fare + tax + fee) with zero cancellation fee.
  8. REST API endpoints: policies list, refund estimate preview, booking cancellation, and refund audit history.

Usage:
  python verify_module_9.py
"""
import os
import sys
import uuid
from datetime import timedelta
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from django.test import Client
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
    TicketStatus,
    RefundStatus,
    Gender,
)
from common.exceptions import BusinessLogicError
from common.money import round_currency
from accounts.models import User
from fleet.models import Bus, Seat
from fleet.services import generate_bus_seat_matrix
from routes.models import Terminal, Route, RouteStop
from routes.services import RouteService
from trips.models import Trip
from trips.services import FareService
from bookings.models import Booking, BookingSeat, SeatLock
from bookings.services import SeatLockService, BookingService
from payments.models import Payment
from payments.services import PaymentService
from tickets.models import Ticket
from tickets.services import TicketService
from cancellations.models import CancellationPolicy, Refund
from cancellations.services import CancellationService


def print_header(title: str):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def cleanup_previous_records():
    """Purge prior verification run entities for clean idempotent execution."""
    trips = Trip.objects.filter(trip_number__startswith="V9-TRIP")
    trip_ids = list(trips.values_list('id', flat=True))
    SeatLock.objects.filter(trip_id__in=trip_ids).delete()
    Refund.objects.filter(booking__trip_id__in=trip_ids).delete()
    Ticket.objects.filter(booking_seat__trip_id__in=trip_ids).delete()
    Payment.objects.filter(booking__trip_id__in=trip_ids).delete()
    BookingSeat.objects.filter(trip_id__in=trip_ids).delete()
    Booking.objects.filter(trip_id__in=trip_ids).delete()
    trips.delete()


def create_verified_booking(trip, bus, passenger, seat_numbers, hours_to_departure=48, pnr_prefix="V9-"):
    """Creates a paid, confirmed booking with issued tickets."""
    if hours_to_departure != 48:
        trip.departure_datetime = timezone.now() + timedelta(hours=hours_to_departure)
        trip.save(update_fields=['departure_datetime'])

    stops = list(trip.route.stops.order_by('stop_order'))
    s0, s2 = stops[0], stops[2]

    seats = list(Seat.objects.filter(bus=bus, seat_number__in=seat_numbers))
    seat_ids = [s.id for s in seats]

    lock_info = SeatLockService.lock_seats(
        trip=trip,
        seat_ids=seat_ids,
        from_stop=s0,
        to_stop=s2
    )

    passengers_data = [
        {"seat_id": s.id, "passenger_name": f"Passenger {s.seat_number}", "gender": Gender.MALE, "age": 28}
        for s in seats
    ]

    booking = BookingService.create_booking_from_locks(
        trip=trip,
        lock_token=uuid.UUID(lock_info['lock_token']),
        from_stop=s0,
        to_stop=s2,
        passenger_name="Lead Verifier",
        passenger_phone=passenger.phone,
        passenger_email="verifier@test.com",
        passengers_data=passengers_data,
        payment_method=PaymentMethod.CARD_ONLINE,
        user=passenger
    )

    # Transition to confirmed & create payment
    booking.status = BookingStatus.CONFIRMED
    booking.save(update_fields=['status'])
    booking.seats.update(status=BookingSeatStatus.BOOKED)

    payment = Payment.objects.create(
        booking=booking,
        amount=booking.net_amount,
        currency='PKR',
        method=PaymentMethod.CARD_ONLINE,
        status=PaymentStatus.SUCCESS,
        gateway_name='mock',
        idempotency_key=f"IDEMP-{uuid.uuid4().hex}"
    )

    TicketService.issue_tickets_for_booking(booking)
    booking.refresh_from_db()
    return booking


def run_verification():
    print_header("MODULE 9 VERIFICATION: CANCELLATION & REFUND ENGINE")
    cleanup_previous_records()

    # -------------------------------------------------------------------------
    # [1/8] Environment Setup & Policy Seeding
    # -------------------------------------------------------------------------
    print("\n[1/8] Checking Policy Seeding & Tier Resolution Engine...")
    policies = CancellationService.seed_default_policies()
    assert len(policies) >= 3, "Expected at least 3 default cancellation policies"

    tier_24 = CancellationPolicy.objects.filter(min_hours_before_departure=24).first()
    tier_6 = CancellationPolicy.objects.filter(min_hours_before_departure=6).first()
    tier_0 = CancellationPolicy.objects.filter(min_hours_before_departure=0).first()

    assert tier_24 and tier_24.refund_percentage == Decimal('90.00'), "Tier >24h must offer 90% refund"
    assert tier_6 and tier_6.refund_percentage == Decimal('50.00'), "Tier 6-24h must offer 50% refund"
    assert tier_0 and tier_0.refund_percentage == Decimal('0.00'), "Tier <6h must offer 0% refund"

    print("  [✓] Default policies seeded: >24h (90%), 6-24h (50%), <6h (0%)")

    # Provision Corridor, Bus & Scheduled Trips
    t_lhr, _ = Terminal.objects.get_or_create(code="V9-LHR", defaults={"name": "Lahore Terminal", "city": "Lahore", "is_active": True})
    t_bhr, _ = Terminal.objects.get_or_create(code="V9-BHR", defaults={"name": "Bhera Rest Area", "city": "Bhera", "is_active": True})
    t_isb, _ = Terminal.objects.get_or_create(code="V9-ISB", defaults={"name": "Islamabad Station", "city": "Islamabad", "is_active": True})

    route, _ = Route.objects.get_or_create(
        code="V9-M2-EXP",
        defaults={
            "name": "M2 Cancellation Express",
            "origin": t_lhr,
            "destination": t_isb,
            "total_distance_km": Decimal('375.00'),
            "estimated_duration_minutes": 260
        }
    )
    if route.stops.count() == 0:
        RouteStop.objects.create(route=route, terminal=t_lhr, stop_order=0, distance_from_origin_km=Decimal('0.00'), offset_minutes=0)
        RouteStop.objects.create(route=route, terminal=t_bhr, stop_order=1, distance_from_origin_km=Decimal('190.00'), offset_minutes=120)
        RouteStop.objects.create(route=route, terminal=t_isb, stop_order=2, distance_from_origin_km=Decimal('375.00'), offset_minutes=260)

    stops = list(route.stops.order_by('stop_order'))
    s0, s1, s2 = stops[0], stops[1], stops[2]
    FareService.set_fare_rule(route, s0, s2, Decimal('1000.00'))
    FareService.set_fare_rule(route, s0, s1, Decimal('600.00'))
    FareService.set_fare_rule(route, s1, s2, Decimal('500.00'))

    bus, _ = Bus.objects.get_or_create(
        registration_number="V9-BUS-901",
        defaults={"bus_type": BusType.EXECUTIVE, "status": BusStatus.ACTIVE, "total_seats": 40}
    )
    if bus.seats.count() == 0:
        generate_bus_seat_matrix(bus, layout_type="2x2", rows=10, ladies_rows=[])

    operator_user, _ = User.objects.get_or_create(
        phone="+923009999001",
        defaults={"role": UserRole.OPERATOR, "is_verified": True, "is_staff": True}
    )
    passenger_user, _ = User.objects.get_or_create(
        phone="+923009999002",
        defaults={"role": UserRole.PASSENGER, "is_verified": True}
    )

    dep_future = timezone.now() + timedelta(hours=48)
    trip_1 = Trip.objects.create(
        trip_number="V9-TRIP-901",
        route=route,
        bus=bus,
        departure_datetime=dep_future,
        arrival_datetime=dep_future + timedelta(minutes=260),
        status=TripStatus.SCHEDULED,
        base_fare=Decimal('1000.00')
    )
    print(f"  [✓] Corridor provisioned: {route.name} | Trip: {trip_1.trip_number}")

    # -------------------------------------------------------------------------
    # [2/8] Advance Cancellation (>24h): 90% Refund & Service Fee Retention
    # -------------------------------------------------------------------------
    print("\n[2/8] Testing Advance Cancellation (>24h: 90% Refund)...")
    b1 = create_verified_booking(trip_1, bus, passenger_user, seat_numbers=['3A'], hours_to_departure=48)
    assert b1.total_gross_fare == Decimal('1000.00')
    assert b1.tax_amount == Decimal('50.00')
    assert b1.service_fee == Decimal('50.00')
    assert b1.net_amount == Decimal('1100.00')

    b1_cancelled, refund_1 = CancellationService.cancel_booking(pnr=b1.pnr, reason="Advance schedule change")
    assert b1_cancelled.status == BookingStatus.CANCELLED
    assert refund_1.status == RefundStatus.PROCESSED
    # 90% of fare (900.00) + 90% of tax (45.00) = 945.00 net refund
    assert refund_1.gross_fare_refunded == Decimal('900.00')
    assert refund_1.tax_refunded == Decimal('45.00')
    assert refund_1.net_refund_amount == Decimal('945.00')
    # Service fee (50.00) + 10% unrefunded fare/tax (105.00) retained
    assert refund_1.cancellation_fee_charged == Decimal('105.00')

    # Verify ticket voided & seat released
    tkt_1 = Ticket.objects.get(booking_seat__booking=b1)
    assert tkt_1.status == TicketStatus.CANCELLED
    assert b1.seats.first().status == BookingSeatStatus.CANCELLED
    print(f"  [✓] Booking {b1.pnr} cancelled: Net Refund PKR {refund_1.net_refund_amount} | Retained Fee PKR {refund_1.cancellation_fee_charged}")
    print("  [✓] Ticket voided & seat marked CANCELLED")

    # -------------------------------------------------------------------------
    # [3/8] Standard Cancellation (6-24h): 50% Refund Calculation
    # -------------------------------------------------------------------------
    print("\n[3/8] Testing Standard Cancellation (6-24h: 50% Refund)...")
    b2 = create_verified_booking(trip_1, bus, passenger_user, seat_numbers=['3B'], hours_to_departure=14)
    b2_cancelled, refund_2 = CancellationService.cancel_booking(pnr=b2.pnr, reason="Moderate cancellation")

    assert b2_cancelled.status == BookingStatus.CANCELLED
    # 50% of fare (500.00) + 50% of tax (25.00) = 525.00 net refund
    assert refund_2.gross_fare_refunded == Decimal('500.00')
    assert refund_2.tax_refunded == Decimal('25.00')
    assert refund_2.net_refund_amount == Decimal('525.00')
    assert refund_2.cancellation_fee_charged == Decimal('525.00')
    print(f"  [✓] Booking {b2.pnr} cancelled: 50% Refund PKR {refund_2.net_refund_amount} processed accurately")

    # -------------------------------------------------------------------------
    # [4/8] Late Cancellation (<6h): 0% Refund Enforcement
    # -------------------------------------------------------------------------
    print("\n[4/8] Testing Late Cancellation (<6h: Non-Refundable Policy)...")
    b3 = create_verified_booking(trip_1, bus, passenger_user, seat_numbers=['3C'], hours_to_departure=2)
    b3_cancelled, refund_3 = CancellationService.cancel_booking(pnr=b3.pnr, reason="Missed bus")

    assert b3_cancelled.status == BookingStatus.CANCELLED
    assert refund_3.net_refund_amount == Decimal('0.00')
    assert refund_3.cancellation_fee_charged == Decimal('1050.00')
    print(f"  [✓] Booking {b3.pnr} cancelled under 6h: 0% refund enforced. Full fare+tax retained as fee.")

    # -------------------------------------------------------------------------
    # [5/8] Partial Seat Cancellation & Instant Segment Inventory Recovery
    # -------------------------------------------------------------------------
    print("\n[5/8] Testing Partial Seat Cancellation & Segment Inventory Recovery...")
    b4 = create_verified_booking(trip_1, bus, passenger_user, seat_numbers=['4A', '4B'], hours_to_departure=36)
    seat_4a = b4.seats.get(seat__seat_number='4A')
    seat_4b = b4.seats.get(seat__seat_number='4B')

    # Before cancellation, 4A is occupied
    assert BookingSeat.objects.filter(trip=trip_1, seat=seat_4a.seat, status=BookingSeatStatus.BOOKED).exists()

    # Cancel only Seat 4A
    b4_partial, refund_4a = CancellationService.cancel_seats(
        pnr=b4.pnr,
        seat_ids=[seat_4a.id],
        reason="Passenger 4A cannot travel"
    )

    assert b4_partial.status == BookingStatus.PARTIALLY_CANCELLED
    seat_4a.refresh_from_db()
    seat_4b.refresh_from_db()
    assert seat_4a.status == BookingSeatStatus.CANCELLED
    assert seat_4b.status == BookingSeatStatus.BOOKED

    # Ticket 4A is cancelled, Ticket 4B remains issued
    tkt_4a = Ticket.objects.get(booking_seat=seat_4a)
    tkt_4b = Ticket.objects.get(booking_seat=seat_4b)
    assert tkt_4a.status == TicketStatus.CANCELLED
    assert tkt_4b.status == TicketStatus.ISSUED

    # Verify Seat 4A is IMMEDIATELY available for new passenger
    occupied_now = BookingSeat.objects.filter(
        trip=trip_1,
        seat=seat_4a.seat,
        status__in=[BookingSeatStatus.LOCKED, BookingSeatStatus.BOOKED]
    ).exists()
    assert not occupied_now, "Seat 4A must be free across segment inventory"

    # Re-book Seat 4A by another passenger
    new_cust, _ = User.objects.get_or_create(phone="+923009999099", defaults={"role": UserRole.PASSENGER, "is_verified": True})
    new_lock = SeatLockService.lock_seats(
        trip=trip_1,
        seat_ids=[seat_4a.seat.id],
        from_stop=s0,
        to_stop=s2
    )
    assert new_lock['lock_token'] is not None
    print(f"  [✓] Seat 4A partial cancellation: Booking transitioned to PARTIALLY_CANCELLED")
    print(f"  [✓] Seat 4B remains BOOKED (Ticket ISSUED). Seat 4A immediately re-locked by new customer!")

    # -------------------------------------------------------------------------
    # [6/8] Invariant Guards & Fraud Prevention
    # -------------------------------------------------------------------------
    print("\n[6/8] Testing Invariant Guards (Departed, Boarded & Duplicate Cancellations)...")
    # Guard A: Prevent cancelling departed trip
    b5 = create_verified_booking(trip_1, bus, passenger_user, seat_numbers=['5A'], hours_to_departure=48)
    trip_1.departure_datetime = timezone.now() - timedelta(minutes=10)
    trip_1.save()

    try:
        CancellationService.cancel_booking(pnr=b5.pnr)
        assert False, "Should reject cancellation of departed trip"
    except BusinessLogicError as e:
        print(f"  [✓] Guard A passed: Departed trip rejected ({e})")

    # Restore departure
    trip_1.departure_datetime = timezone.now() + timedelta(hours=48)
    trip_1.save()

    # Guard B: Prevent cancelling boarded passenger
    tkt_5 = Ticket.objects.get(booking_seat__booking=b5)
    tkt_5.status = TicketStatus.BOARDED
    tkt_5.boarded_at = timezone.now()
    tkt_5.save()

    try:
        CancellationService.cancel_booking(pnr=b5.pnr)
        assert False, "Should reject cancellation of boarded passenger"
    except BusinessLogicError as e:
        print(f"  [✓] Guard B passed: Boarded passenger rejected ({e})")

    # Guard C: Duplicate cancellation rejection
    try:
        CancellationService.cancel_booking(pnr=b1.pnr)
        assert False, "Should reject duplicate cancellation on already cancelled booking"
    except BusinessLogicError as e:
        print(f"  [✓] Guard C passed: Duplicate cancellation rejected ({e})")

    # -------------------------------------------------------------------------
    # [7/8] Operator Bulk Trip Cancellation: 100% Full Refund
    # -------------------------------------------------------------------------
    print("\n[7/8] Testing Operator Bulk Trip Cancellation (100% Full Refund)...")
    trip_2 = Trip.objects.create(
        trip_number="V9-TRIP-902",
        route=route,
        bus=bus,
        departure_datetime=timezone.now() + timedelta(hours=30),
        arrival_datetime=timezone.now() + timedelta(hours=34),
        status=TripStatus.SCHEDULED,
        base_fare=Decimal('1000.00')
    )
    b_op_1 = create_verified_booking(trip_2, bus, passenger_user, seat_numbers=['6A'], hours_to_departure=30)
    b_op_2 = create_verified_booking(trip_2, bus, passenger_user, seat_numbers=['6B'], hours_to_departure=30)

    total_expected_refund = b_op_1.net_amount + b_op_2.net_amount

    op_result = CancellationService.cancel_trip_by_operator(
        trip_id=trip_2.id,
        reason="Bus breakdown before departure",
        operator_user=operator_user
    )

    assert op_result['status'] == TripStatus.CANCELLED
    assert op_result['bookings_cancelled_count'] == 2
    assert op_result['total_refunded'] == total_expected_refund

    # Verify both bookings received 100% full refund with 0 cancellation fee
    b_op_1.refresh_from_db()
    b_op_2.refresh_from_db()
    assert b_op_1.status == BookingStatus.CANCELLED
    assert b_op_2.status == BookingStatus.CANCELLED

    ref_op_1 = b_op_1.refunds.first()
    assert ref_op_1.net_refund_amount == b_op_1.net_amount
    assert ref_op_1.cancellation_fee_charged == Decimal('0.00')
    assert ref_op_1.is_operator_cancellation is True
    print(f"  [✓] Operator cancelled Trip {trip_2.trip_number}: 2 bookings cancelled with 100% refund (PKR {op_result['total_refunded']}) & ZERO fee")

    # -------------------------------------------------------------------------
    # [8/8] REST API Endpoints Verification
    # -------------------------------------------------------------------------
    print("\n[8/8] Verifying REST API Endpoint Suite...")
    client = Client()

    # API 1: Policies list
    res_policies = client.get('/api/cancellations/policies/')
    assert res_policies.status_code == 200
    assert len(res_policies.json()['data']['policies']) >= 3
    print("  [✓] GET /api/cancellations/policies/ -> 200 OK")

    # API 2: Refund estimate
    b_api = create_verified_booking(trip_1, bus, passenger_user, seat_numbers=['7B'], hours_to_departure=30)
    res_estimate = client.post(
        '/api/cancellations/estimate/',
        data={"pnr": b_api.pnr},
        content_type='application/json'
    )
    assert res_estimate.status_code == 200
    est_data = res_estimate.json()['data']
    assert Decimal(str(est_data['refund_percentage'])) == Decimal('90.00')
    assert Decimal(str(est_data['net_refund_amount'])) > Decimal('0.00')
    print("  [✓] POST /api/cancellations/estimate/ -> 200 OK (Estimated Net: PKR " + str(est_data['net_refund_amount']) + ")")

    # API 3: Cancel booking
    res_cancel = client.post(
        '/api/cancellations/cancel-booking/',
        data={"pnr": b_api.pnr, "reason": "API cancel test"},
        content_type='application/json'
    )
    assert res_cancel.status_code == 200
    assert res_cancel.json()['data']['status'] == BookingStatus.CANCELLED
    print("  [✓] POST /api/cancellations/cancel-booking/ -> 200 OK")

    # API 4: Audit refunds history
    res_refunds = client.get(f'/api/cancellations/refunds/{b_api.pnr}/')
    assert res_refunds.status_code == 200
    assert len(res_refunds.json()['data']['refunds']) >= 1
    print(f"  [✓] GET /api/cancellations/refunds/{b_api.pnr}/ -> 200 OK ({len(res_refunds.json()['data']['refunds'])} record)")

    print_header("ALL 8 VERIFICATION CHECKS PASSED FOR MODULE 9!")


if __name__ == "__main__":
    run_verification()
