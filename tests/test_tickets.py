"""
Comprehensive test suite for Module 8: Tickets, QR Codes & Conductor Validation.
Tests:
  1. Ticket issuance for confirmed bookings (amounts, QR payload, seat linkage)
  2. Idempotent ticket issuance (safe repeated execution)
  3. Rejection of ticket issuance for unconfirmed/pending bookings
  4. Cryptographic HMAC-SHA256 signature validation & tamper detection
  5. Conductor QR scanning & boarding authorization checks
  6. Atomic passenger boarding & conductor audit logging
  7. Anti-fraud double-boarding prevention
  8. Trip mismatch boarding rejection
  9. Cancelled ticket boarding rejection
  10. Trip passenger manifest generation & occupancy calculations
  11. High-resolution PDF e-ticket generation (%PDF- binary verification)
  12. DRF API endpoints (validate-qr, board, manifest, booking-tickets, pdf download)
"""
import io
import uuid
import pytest
from datetime import timedelta
from decimal import Decimal
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

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


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def test_network(db):
    """
    Corridor: Lahore -> Bhera -> Islamabad
    """
    term_lhr = Terminal.objects.create(name="Lahore Terminal", code="LHR-TK-01", city="Lahore", is_active=True)
    term_bhr = Terminal.objects.create(name="Bhera Rest Stop", code="BHR-TK-01", city="Bhera", is_active=True)
    term_isb = Terminal.objects.create(name="Islamabad Terminal", code="ISB-TK-01", city="Islamabad", is_active=True)

    route = RouteService.create_route(
        code="LHR-ISB-TK",
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
        registration_number="LES-TK-999",
        bus_type=BusType.EXECUTIVE,
        status=BusStatus.ACTIVE,
        total_seats=40
    )
    generate_bus_seat_matrix(bus, layout_type="2x2", rows=10, ladies_rows=[1, 2])

    now = timezone.now().replace(microsecond=0)
    dep = now + timedelta(days=1)
    arr = dep + timedelta(minutes=260)
    trip = Trip.objects.create(
        trip_number="TRIP-TK-001",
        route=route,
        bus=bus,
        departure_datetime=dep,
        arrival_datetime=arr,
        status=TripStatus.SCHEDULED,
        base_fare=Decimal("2000.00")
    )

    stops = list(route.stops.order_by('stop_order'))
    s0, s1, s2 = stops[0], stops[1], stops[2]
    FareService.set_fare_rule(route, s0, s2, Decimal("2000.00"))

    conductor = User.objects.create_user(
        phone="+923008880001",
        role=UserRole.CONDUCTOR,
        password="ConductorPass123!",
        is_verified=True,
        is_staff=True
    )

    return {
        'route': route,
        'bus': bus,
        'trip': trip,
        'stops': stops,
        'conductor': conductor,
    }


@pytest.fixture
def confirmed_booking(test_network):
    """
    Creates a CONFIRMED booking with 2 booked seats (Seat 3A and 3B).
    """
    trip = test_network['trip']
    s0 = test_network['stops'][0]
    s2 = test_network['stops'][2]
    seat_3a = Seat.objects.get(bus=test_network['bus'], seat_number="3A")
    seat_3b = Seat.objects.get(bus=test_network['bus'], seat_number="3B")

    lock_info = SeatLockService.lock_seats(
        trip=trip,
        seat_ids=[seat_3a.id, seat_3b.id],
        from_stop=s0,
        to_stop=s2
    )

    booking = BookingService.create_booking_from_locks(
        trip=trip,
        lock_token=uuid.UUID(lock_info['lock_token']),
        from_stop=s0,
        to_stop=s2,
        passenger_name="Hamza Khan",
        passenger_phone="+923001239999",
        passengers_data=[
            {'seat_id': seat_3a.id, 'passenger_name': 'Hamza Khan', 'gender': Gender.MALE, 'age': 30},
            {'seat_id': seat_3b.id, 'passenger_name': 'Zain Khan', 'gender': Gender.MALE, 'age': 25},
        ]
    )

    # Transition booking to CONFIRMED
    booking.status = BookingStatus.CONFIRMED
    booking.save()
    booking.seats.update(status=BookingSeatStatus.BOOKED)

    return booking


@pytest.mark.django_db
class TestTicketService:

    def test_ticket_issuance_for_confirmed_booking(self, confirmed_booking):
        tickets = TicketService.issue_tickets_for_booking(confirmed_booking)

        assert len(tickets) == 2
        for t in tickets:
            assert t.ticket_number.startswith("TKT-")
            assert t.status == TicketStatus.ISSUED
            assert t.qr_token is not None
            assert '.' in t.qr_token
            assert t.booking == confirmed_booking
            assert t.boarded_at is None

    def test_ticket_issuance_idempotency(self, confirmed_booking):
        # First issuance
        tickets1 = TicketService.issue_tickets_for_booking(confirmed_booking)
        # Repeated call
        tickets2 = TicketService.issue_tickets_for_booking(confirmed_booking)

        assert [t.id for t in tickets1] == [t.id for t in tickets2]
        assert Ticket.objects.filter(booking_seat__booking=confirmed_booking).count() == 2

    def test_ticket_issuance_rejected_for_pending_booking(self, confirmed_booking):
        confirmed_booking.status = BookingStatus.PENDING
        confirmed_booking.save()

        with pytest.raises(BusinessLogicError, match="CONFIRMED bookings"):
            TicketService.issue_tickets_for_booking(confirmed_booking)

    def test_qr_cryptographic_verification_valid(self, confirmed_booking):
        tickets = TicketService.issue_tickets_for_booking(confirmed_booking)
        ticket = tickets[0]

        is_valid, payload, msg = verify_qr_payload(ticket.qr_token)
        assert is_valid is True
        assert payload is not None
        assert payload['ticket_number'] == ticket.ticket_number
        assert payload['pnr'] == confirmed_booking.pnr
        assert payload['seat_number'] == ticket.seat_number

        # Service-level validation check
        val_result = TicketService.validate_qr_token(ticket.qr_token)
        assert val_result['is_valid'] is True
        assert val_result['can_board'] is True
        assert val_result['ticket'].id == ticket.id

    def test_qr_cryptographic_verification_tampered(self, confirmed_booking):
        tickets = TicketService.issue_tickets_for_booking(confirmed_booking)
        ticket = tickets[0]

        # Tamper token by altering the signature
        parts = ticket.qr_token.split('.')
        tampered_token = f"{parts[0]}.{parts[1][:-4]}0000"

        is_valid, payload, msg = verify_qr_payload(tampered_token)
        assert is_valid is False
        assert "tampered" in msg.lower()

        val_result = TicketService.validate_qr_token(tampered_token)
        assert val_result['is_valid'] is False
        assert val_result['can_board'] is False

    def test_board_passenger_success(self, confirmed_booking, test_network):
        tickets = TicketService.issue_tickets_for_booking(confirmed_booking)
        ticket = tickets[0]
        conductor = test_network['conductor']

        boarded_ticket = TicketService.board_passenger(
            qr_token=ticket.qr_token,
            conductor_user=conductor
        )

        assert boarded_ticket.status == TicketStatus.BOARDED
        assert boarded_ticket.boarded_at is not None
        assert boarded_ticket.boarded_by == conductor
        assert boarded_ticket.booking_seat.status == BookingSeatStatus.COMPLETED

    def test_anti_fraud_double_boarding_prevention(self, confirmed_booking, test_network):
        tickets = TicketService.issue_tickets_for_booking(confirmed_booking)
        ticket = tickets[0]
        conductor = test_network['conductor']

        # Board first time
        TicketService.board_passenger(qr_token=ticket.qr_token, conductor_user=conductor)

        # Attempt to board second time with exact same token/ticket
        with pytest.raises(BusinessLogicError, match="Anti-Fraud Alert: Ticket .* was already boarded"):
            TicketService.board_passenger(qr_token=ticket.qr_token, conductor_user=conductor)

    def test_board_passenger_trip_mismatch(self, confirmed_booking, test_network):
        tickets = TicketService.issue_tickets_for_booking(confirmed_booking)
        ticket = tickets[0]
        random_trip_id = uuid.uuid4()

        with pytest.raises(BusinessLogicError, match="Trip mismatch"):
            TicketService.board_passenger(qr_token=ticket.qr_token, trip_id=random_trip_id)

    def test_board_passenger_cancelled_ticket_rejected(self, confirmed_booking):
        tickets = TicketService.issue_tickets_for_booking(confirmed_booking)
        ticket = tickets[0]
        ticket.status = TicketStatus.CANCELLED
        ticket.save()

        with pytest.raises(BusinessLogicError, match="Boarding rejected: Ticket status is 'Cancelled'"):
            TicketService.board_passenger(ticket_number=ticket.ticket_number)

    def test_trip_passenger_manifest(self, confirmed_booking, test_network):
        trip = test_network['trip']
        tickets = TicketService.issue_tickets_for_booking(confirmed_booking)

        # Board 1 passenger
        TicketService.board_passenger(ticket_number=tickets[0].ticket_number)

        manifest = TicketService.get_trip_passenger_manifest(trip)

        assert manifest['trip_number'] == trip.trip_number
        assert manifest['stats']['total_seats'] == 40
        assert manifest['stats']['total_booked'] == 2
        assert manifest['stats']['total_boarded'] == 1
        assert manifest['stats']['pending_boarding'] == 1
        assert len(manifest['passengers']) == 2
        assert manifest['passengers'][0]['qr_data_uri'].startswith("data:image/png;base64,")

    def test_pdf_ticket_generation(self, confirmed_booking):
        tickets = TicketService.issue_tickets_for_booking(confirmed_booking)
        ticket = tickets[0]

        pdf_buffer = generate_ticket_pdf(ticket)
        assert isinstance(pdf_buffer, io.BytesIO)
        content = pdf_buffer.getvalue()
        assert content.startswith(b'%PDF-')
        assert len(content) > 1000


@pytest.mark.django_db
class TestTicketAPI:

    def test_api_validate_qr(self, api_client, confirmed_booking):
        tickets = TicketService.issue_tickets_for_booking(confirmed_booking)
        ticket = tickets[0]

        url = reverse('ticket-validate-qr')
        response = api_client.post(url, {'qr_token': ticket.qr_token}, format='json')

        assert response.status_code == status.HTTP_200_OK
        data = response.data['data']
        assert data['is_valid'] is True
        assert data['can_board'] is True
        assert data['ticket']['ticket_number'] == ticket.ticket_number

    def test_api_board_passenger(self, api_client, confirmed_booking):
        tickets = TicketService.issue_tickets_for_booking(confirmed_booking)
        ticket = tickets[0]

        url = reverse('ticket-board')
        response = api_client.post(url, {'ticket_number': ticket.ticket_number}, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['data']['status'] == TicketStatus.BOARDED

    def test_api_ticket_detail_and_pdf(self, api_client, confirmed_booking):
        tickets = TicketService.issue_tickets_for_booking(confirmed_booking)
        ticket = tickets[0]

        # Detail
        det_url = reverse('ticket-detail', kwargs={'ticket_number': ticket.ticket_number})
        det_res = api_client.get(det_url)
        assert det_res.status_code == status.HTTP_200_OK
        assert det_res.data['data']['ticket_number'] == ticket.ticket_number
        assert det_res.data['data']['qr_data_uri'].startswith('data:image/png;base64,')

        # PDF Download
        pdf_url = reverse('ticket-pdf', kwargs={'ticket_number': ticket.ticket_number})
        pdf_res = api_client.get(pdf_url)
        assert pdf_res.status_code == status.HTTP_200_OK
        assert pdf_res['Content-Type'] == 'application/pdf'
        pdf_bytes = b"".join(pdf_res.streaming_content)
        assert pdf_bytes.startswith(b'%PDF-')

    def test_api_trip_manifest(self, api_client, confirmed_booking, test_network):
        trip = test_network['trip']
        TicketService.issue_tickets_for_booking(confirmed_booking)

        url = reverse('ticket-trip-manifest', kwargs={'trip_id': trip.id})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        data = response.data['data']
        assert data['stats']['total_booked'] == 2
        assert len(data['passengers']) == 2
