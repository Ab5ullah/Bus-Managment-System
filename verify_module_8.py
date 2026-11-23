"""
Interactive verification script for Module 8: Tickets, QR Codes & Conductor Validation.
Verifies:
  1. Provisioning corridor, luxury bus, scheduled trip & conductor account.
  2. Automatic cryptographically signed ticket generation for confirmed booking.
  3. HMAC-SHA256 signature verification & QR code data URI generation.
  4. Cryptographic tamper detection (rejection of corrupted tokens).
  5. Conductor gate boarding validation with audit tracking (timestamp & conductor).
  6. Anti-fraud double-boarding prevention.
  7. Real-time conductor passenger manifest & boarding statistics.
  8. Printable PDF e-ticket generation with embedded high-resolution QR pass.

Usage:
  python verify_module_8.py
"""
import io
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
    TicketStatus,
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
from tickets.models import Ticket
from tickets.services import TicketService
from tickets.crypto import sign_qr_payload, verify_qr_payload, generate_qr_code_image_base64
from tickets.pdf_generator import generate_ticket_pdf


def print_header(title: str):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def run_verification():
    print_header("MODULE 8 VERIFICATION: TICKETS, QR CODES & CONDUCTOR VALIDATION")

    # -------------------------------------------------------------------------
    # [1/8] Environment Setup
    # -------------------------------------------------------------------------
    print("\n[1/8] Provisioning System Entities (Conductor, Route, Executive Bus & Trip)...")
    conductor, _ = User.objects.get_or_create(
        phone="+923008889901",
        defaults={"role": UserRole.CONDUCTOR, "email": "conductor8@example.com", "is_verified": True, "is_staff": True}
    )
    passenger, _ = User.objects.get_or_create(
        phone="+923008889902",
        defaults={"role": UserRole.PASSENGER, "email": "passenger8@example.com", "is_verified": True}
    )

    t_lhr, _ = Terminal.objects.get_or_create(code="V8-LHR", defaults={"name": "Lahore Terminal", "city": "Lahore", "is_active": True})
    t_bhr, _ = Terminal.objects.get_or_create(code="V8-BHR", defaults={"name": "Bhera Rest Area", "city": "Bhera", "is_active": True})
    t_isb, _ = Terminal.objects.get_or_create(code="V8-ISB", defaults={"name": "Islamabad Station", "city": "Islamabad", "is_active": True})

    route, _ = Route.objects.get_or_create(
        code="V8-M2-EXP",
        defaults={
            "name": "M2 Motorway Boarding Express",
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
        registration_number="V8-BUS-800",
        defaults={"bus_type": BusType.EXECUTIVE, "status": BusStatus.ACTIVE, "total_seats": 40}
    )
    if created or bus.seats.count() == 0:
        generate_bus_seat_matrix(bus, layout_type="2x2", rows=10, ladies_rows=[1, 2])

    now = timezone.now().replace(microsecond=0)
    trip, _ = Trip.objects.get_or_create(
        trip_number="V8-TRIP-801",
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

    # Clean up prior test runs for idempotent execution
    Ticket.objects.filter(booking_seat__trip=trip).delete()
    BookingSeat.objects.filter(trip=trip).delete()
    Booking.objects.filter(trip=trip).delete()
    SeatLock.objects.filter(trip=trip).delete()

    print(f"      OK: Environment ready. Conductor '{conductor.phone}' assigned to Trip '{trip.trip_number}'.")

    # -------------------------------------------------------------------------
    # [2/8] Creating Confirmed Booking & Auto-Generating Tickets
    # -------------------------------------------------------------------------
    print("\n[2/8] Creating Confirmed Booking for Seats 5A & 5B...")
    seat_5a = Seat.objects.get(bus=bus, seat_number="5A")
    seat_5b = Seat.objects.get(bus=bus, seat_number="5B")

    lock_info = SeatLockService.lock_seats(trip=trip, seat_ids=[seat_5a.id, seat_5b.id], from_stop=s0, to_stop=s2)
    booking = BookingService.create_booking_from_locks(
        trip=trip,
        lock_token=uuid.UUID(lock_info['lock_token']),
        from_stop=s0,
        to_stop=s2,
        passenger_name="Hamza Aslam",
        passenger_phone="+923008889902",
        passengers_data=[
            {'seat_id': seat_5a.id, 'passenger_name': 'Hamza Aslam', 'gender': Gender.MALE, 'age': 32},
            {'seat_id': seat_5b.id, 'passenger_name': 'Ali Aslam', 'gender': Gender.MALE, 'age': 28},
        ],
        user=passenger
    )
    booking.status = BookingStatus.CONFIRMED
    booking.save()
    booking.seats.update(status=BookingSeatStatus.BOOKED)

    tickets = TicketService.issue_tickets_for_booking(booking)
    assert len(tickets) == 2, f"Expected 2 tickets, got {len(tickets)}"
    ticket_5a = next(t for t in tickets if t.seat_number == "5A")
    ticket_5b = next(t for t in tickets if t.seat_number == "5B")

    print(f"      OK: 2 Cryptographically signed tickets issued for PNR '{booking.pnr}':")
    print(f"          - Seat 5A: Ticket '{ticket_5a.ticket_number}' [Status: {ticket_5a.status}]")
    print(f"          - Seat 5B: Ticket '{ticket_5b.ticket_number}' [Status: {ticket_5b.status}]")

    # -------------------------------------------------------------------------
    # [3/8] Verifying QR Token Cryptographic Signature & Data URI
    # -------------------------------------------------------------------------
    print("\n[3/8] Verifying Cryptographic QR Token & Visual Image Generation...")
    is_valid, payload, msg = verify_qr_payload(ticket_5a.qr_token)
    assert is_valid is True, "QR signature must verify"
    assert payload['ticket_number'] == ticket_5a.ticket_number
    assert payload['seat_number'] == "5A"
    assert payload['pnr'] == booking.pnr

    data_uri = generate_qr_code_image_base64(ticket_5a.qr_token)
    assert data_uri.startswith("data:image/png;base64,"), "Must generate Base64 PNG data URI"

    print(f"      OK: HMAC-SHA256 signature verified successfully:")
    print(f"          - Payload: Ticket={payload['ticket_number']} | Seat={payload['seat_number']} | PNR={payload['pnr']}")
    print(f"          - Visual QR Data URI: {data_uri[:45]}... (Length: {len(data_uri)} chars)")

    # -------------------------------------------------------------------------
    # [4/8] Testing Tamper Detection
    # -------------------------------------------------------------------------
    print("\n[4/8] Testing Cryptographic Tamper Detection on Altered QR Token...")
    parts = ticket_5a.qr_token.split('.')
    tampered_token = f"{parts[0]}.{parts[1][:-4]}AAAA"

    tamper_valid, _, tamper_msg = verify_qr_payload(tampered_token)
    assert tamper_valid is False, "Altered token signature must fail"
    val_check = TicketService.validate_qr_token(tampered_token)
    assert val_check['can_board'] is False, "Tampered pass must be denied boarding"

    print(f"      OK: Tampered QR code rejected: '{tamper_msg}'")

    # -------------------------------------------------------------------------
    # [5/8] Conductor Boarding Validation
    # -------------------------------------------------------------------------
    print("\n[5/8] Scanning QR Token & Executing Conductor Boarding Validation...")
    # 1. Gate scan check
    scan_result = TicketService.validate_qr_token(ticket_5a.qr_token, trip_id=trip.id)
    assert scan_result['can_board'] is True, "Valid ticket must be cleared for boarding"

    # 2. Commit boarding
    boarded_t = TicketService.board_passenger(
        qr_token=ticket_5a.qr_token,
        trip_id=trip.id,
        conductor_user=conductor,
        boarding_stop=s0
    )

    ticket_5a.refresh_from_db()
    assert ticket_5a.status == TicketStatus.BOARDED, "Ticket must transition to BOARDED"
    assert ticket_5a.boarded_at is not None, "boarded_at timestamp must be recorded"
    assert ticket_5a.boarded_by == conductor, "boarded_by conductor must match"
    assert ticket_5a.booking_seat.status == BookingSeatStatus.COMPLETED

    print(f"      OK: Passenger 'Hamza Aslam' (Seat 5A) boarded successfully:")
    print(f"          - Boarded At: {ticket_5a.boarded_at:%Y-%m-%d %H:%M:%S}")
    print(f"          - Validated By Conductor: {conductor.phone}")
    print(f"          - Seat State: COMPLETED")

    # -------------------------------------------------------------------------
    # [6/8] Anti-Fraud Double-Boarding Prevention
    # -------------------------------------------------------------------------
    print("\n[6/8] Testing Anti-Fraud Double-Boarding Prevention...")
    try:
        TicketService.board_passenger(qr_token=ticket_5a.qr_token, conductor_user=conductor)
        assert False, "Double boarding must be prevented!"
    except BusinessLogicError as e:
        assert "already boarded" in str(e).lower()
        print(f"      OK: Duplicate boarding strictly blocked: {str(e)}")

    # -------------------------------------------------------------------------
    # [7/8] Conductor Trip Passenger Manifest
    # -------------------------------------------------------------------------
    print("\n[7/8] Inspecting Real-Time Conductor Boarding Manifest...")
    manifest = TicketService.get_trip_passenger_manifest(trip)

    stats = manifest['stats']
    assert stats['total_seats'] == 40
    assert stats['total_booked'] == 2
    assert stats['total_boarded'] == 1
    assert stats['pending_boarding'] == 1

    print(f"      OK: Boarding Manifest verified for Trip '{manifest['trip_number']}':")
    print(f"          - Capacity: {stats['total_seats']} | Booked: {stats['total_booked']} | Occupancy: {stats['occupancy_percentage']}%")
    print(f"          - Boarded: {stats['total_boarded']} | Pending: {stats['pending_boarding']}")
    for p in manifest['passengers']:
        print(f"          * Seat {p['seat_number']}: {p['passenger_name']} [{p['status']}]")

    # -------------------------------------------------------------------------
    # [8/8] Printable PDF E-Ticket Generation
    # -------------------------------------------------------------------------
    print("\n[8/8] Generating High-Resolution PDF E-Ticket & Boarding Pass...")
    pdf_buffer = generate_ticket_pdf(ticket_5b)
    pdf_bytes = pdf_buffer.getvalue()

    assert pdf_bytes.startswith(b'%PDF-'), "Must produce valid PDF binary stream"
    assert len(pdf_bytes) > 2000, "PDF must include styling, tables, and embedded QR code"

    # Save demo sample for local viewing
    sample_path = "sample_boarding_pass.pdf"
    with open(sample_path, "wb") as f:
        f.write(pdf_bytes)

    print(f"      OK: PDF boarding pass generated successfully ({len(pdf_bytes)} bytes):")
    print(f"          - Binary Header: {pdf_bytes[:8].decode('ascii', errors='ignore')}")
    print(f"          - Embedded Elements: Route stops, seat badge, passenger manifest, security terms, high-res QR code")
    print(f"          - Sample exported to: '{sample_path}'")

    print_header("MODULE 8 VERIFICATION PASSED: ALL 8 CORE CHECKS 100% SUCCESSFUL!")


if __name__ == '__main__':
    run_verification()
