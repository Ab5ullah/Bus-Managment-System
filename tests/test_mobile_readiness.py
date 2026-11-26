"""
Comprehensive test suite for Mobile-Readiness Enhancements:
  1. CNIC / Government ID normalization, format validation, and mandatory enforcement
  2. End-to-end CNIC propagation: BookingSeat -> TicketSerializer -> QR Payload -> Manifest -> PDF
  3. Mobile Bootstrap Endpoint: GET /api/app-config/
  4. Active SeatLock recovery: GET /api/bookings/active-lock/
  5. Bookings filtering (?filter=upcoming|past|cancelled|pending) and pagination
  6. Native Stripe PaymentIntent mode (checkout_mode='payment_intent') returning client_secret
"""
import uuid
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import override_settings
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
    Gender,
)
from common.exceptions import BusinessLogicError
from common.validators import normalize_and_validate_cnic_or_id
from accounts.models import User
from fleet.models import Bus, Seat
from fleet.services import generate_bus_seat_matrix
from routes.models import Terminal
from routes.services import RouteService
from trips.models import Trip
from trips.services import FareService
from bookings.services import SeatLockService, BookingService
from tickets.crypto import verify_qr_payload
from tickets.pdf_generator import generate_ticket_pdf
from tickets.services import TicketService


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def mobile_network(db):
    t_lhr = Terminal.objects.create(name="Lahore Mob", code="LHR-MOB", city="Lahore", is_active=True)
    t_isb = Terminal.objects.create(name="Islamabad Mob", code="ISB-MOB", city="Islamabad", is_active=True)

    route = RouteService.create_route(
        code="LHR-ISB-MOB",
        name="Lahore to Islamabad Mobile Express",
        origin=t_lhr,
        destination=t_isb,
        total_distance_km=Decimal('375.00'),
        estimated_duration_minutes=260,
        stops_data=[
            {'terminal': t_lhr, 'stop_order': 0, 'distance_from_origin_km': Decimal('0.00'), 'offset_minutes': 0},
            {'terminal': t_isb, 'stop_order': 1, 'distance_from_origin_km': Decimal('375.00'), 'offset_minutes': 260},
        ]
    )

    bus = Bus.objects.create(
        registration_number="MOB-BUS-01",
        bus_type=BusType.EXECUTIVE,
        status=BusStatus.ACTIVE,
        total_seats=20
    )
    generate_bus_seat_matrix(bus, layout_type="2x2", rows=5, ladies_rows=[])

    stops = list(route.stops.order_by('stop_order'))
    s0, s1 = stops[0], stops[1]
    FareService.set_fare_rule(route, s0, s1, Decimal('2000.00'))

    dep = timezone.now() + timedelta(hours=36)
    trip = Trip.objects.create(
        trip_number="TRIP-MOB-01",
        route=route,
        bus=bus,
        departure_datetime=dep,
        arrival_datetime=dep + timedelta(minutes=260),
        status=TripStatus.SCHEDULED,
        base_fare=Decimal('2000.00')
    )

    passenger = User.objects.create_user(
        phone="+923009090001", role=UserRole.PASSENGER, password="Pass123!", is_verified=True
    )
    agent = User.objects.create_user(
        phone="+923009090002", role=UserRole.COUNTER_AGENT, password="Agent123!", is_verified=True
    )

    return {
        'route': route,
        'bus': bus,
        'trip': trip,
        'stops': [s0, s1],
        'passenger': passenger,
        'agent': agent,
    }


class TestCnicValidation:

    def test_cnic_normalization_and_passport_acceptance(self):
        assert normalize_and_validate_cnic_or_id("3520212345671", required=True) == "35202-1234567-1"
        assert normalize_and_validate_cnic_or_id(" 35202-1234567-1 ", required=True) == "35202-1234567-1"
        assert normalize_and_validate_cnic_or_id("ab1234567", required=True) == "AB1234567"
        assert normalize_and_validate_cnic_or_id("", required=False) == ""

        with pytest.raises(BusinessLogicError, match="CNIC or Government ID is required"):
            normalize_and_validate_cnic_or_id("", required=True)

        with pytest.raises(BusinessLogicError, match="Invalid CNIC"):
            normalize_and_validate_cnic_or_id("123", required=True)


@pytest.mark.django_db
class TestMobileReadinessFeatures:

    @override_settings(REQUIRE_CNIC_ON_BOOKING=True)
    def test_cnic_enforced_and_propagated_to_ticket_qr_manifest_pdf(self, api_client, mobile_network):
        trip = mobile_network['trip']
        s0, s1 = mobile_network['stops']
        seat_1a = Seat.objects.get(bus=mobile_network['bus'], seat_number="1A")

        lock = SeatLockService.lock_seats(
            trip=trip, seat_ids=[seat_1a.id], from_stop=s0, to_stop=s1, user=mobile_network['passenger']
        )

        # Missing CNIC must be rejected when REQUIRE_CNIC_ON_BOOKING=True
        with pytest.raises(BusinessLogicError, match="CNIC or Government ID is required"):
            BookingService.create_booking_from_locks(
                trip=trip,
                lock_token=uuid.UUID(lock['lock_token']),
                from_stop=s0,
                to_stop=s1,
                passenger_name="Mobile Pax",
                passenger_phone=mobile_network['passenger'].phone,
                passengers_data=[
                    {"seat_id": seat_1a.id, "passenger_name": "Mobile Pax", "gender": Gender.MALE}
                ],
                user=mobile_network['passenger']
            )

        # Providing raw 13-digit CNIC normalizes & creates booking
        booking = BookingService.create_booking_from_locks(
            trip=trip,
            lock_token=uuid.UUID(lock['lock_token']),
            from_stop=s0,
            to_stop=s1,
            passenger_name="Mobile Pax",
            passenger_phone=mobile_network['passenger'].phone,
            passengers_data=[
                {
                    "seat_id": seat_1a.id,
                    "passenger_name": "Mobile Pax",
                    "cnic_or_id": "3520212345671",
                    "gender": Gender.MALE
                }
            ],
            user=mobile_network['passenger']
        )
        assert booking.seats.first().cnic_or_id == "35202-1234567-1"

        # Confirm & issue ticket
        booking.status = BookingStatus.CONFIRMED
        booking.save()
        booking.seats.update(status=BookingSeatStatus.BOOKED)
        tickets = TicketService.issue_tickets_for_booking(booking)
        ticket = tickets[0]

        # QR payload contains CNIC
        ok, payload, _ = verify_qr_payload(ticket.qr_token)
        assert ok is True
        assert payload is not None
        assert payload['cnic_or_id'] == "35202-1234567-1"

        # Ticket API returns CNIC
        t_res = api_client.get(reverse('ticket-booking-list', kwargs={'pnr': booking.pnr}))
        assert t_res.status_code == status.HTTP_200_OK
        t_json = t_res.json()
        assert t_json['data'][0]['cnic_or_id'] == "35202-1234567-1"

        # Manifest includes CNIC for conductor gate check
        manifest = TicketService.get_trip_passenger_manifest(trip)
        assert manifest['passengers'][0]['cnic_or_id'] == "35202-1234567-1"

        # PDF generation succeeds with CNIC embedded
        pdf_bytes = generate_ticket_pdf(ticket).getvalue()
        assert pdf_bytes.startswith(b"%PDF-")

    def test_app_config_endpoint(self, api_client, settings):
        settings.STRIPE_PUBLISHABLE_KEY = "pk_test_mobile_123"
        res = api_client.get(reverse('common:app-config'))
        assert res.status_code == status.HTTP_200_OK
        data = res.json()['data']
        assert data['currency'] == 'PKR'
        assert data['stripe_publishable_key'] == 'pk_test_mobile_123'
        assert 'stripe' in data['supported_payment_gateways']

    def test_active_lock_recovery_and_booking_filters(self, api_client, mobile_network):
        trip = mobile_network['trip']
        s0, s1 = mobile_network['stops']
        seat_1b = Seat.objects.get(bus=mobile_network['bus'], seat_number="1B")
        seat_1c = Seat.objects.get(bus=mobile_network['bus'], seat_number="1C")

        api_client.force_authenticate(user=mobile_network['passenger'])

        # 1. Active lock recovery
        lock = SeatLockService.lock_seats(
            trip=trip, seat_ids=[seat_1b.id], from_stop=s0, to_stop=s1, user=mobile_network['passenger']
        )
        res_lock = api_client.get(reverse('bookings:active-lock'))
        assert res_lock.status_code == status.HTTP_200_OK
        lock_json = res_lock.json()
        assert lock_json['data']['lock_token'] == lock['lock_token']
        assert lock_json['data']['seconds_remaining'] > 0

        # 2. Create upcoming confirmed booking & test filter + pagination
        lk2 = SeatLockService.lock_seats(
            trip=trip, seat_ids=[seat_1c.id], from_stop=s0, to_stop=s1, user=mobile_network['passenger']
        )
        b = BookingService.create_booking_from_locks(
            trip=trip,
            lock_token=uuid.UUID(lk2['lock_token']),
            from_stop=s0,
            to_stop=s1,
            passenger_name="Filter Pax",
            passenger_phone=mobile_network['passenger'].phone,
            passengers_data=[
                {"seat_id": seat_1c.id, "passenger_name": "Filter Pax", "cnic_or_id": "35202-1111111-1", "gender": Gender.MALE}
            ],
            user=mobile_network['passenger']
        )
        b.status = BookingStatus.CONFIRMED
        b.save()
        b.seats.update(status=BookingSeatStatus.BOOKED)

        res_up = api_client.get('/api/bookings/?filter=upcoming&page=1')
        assert res_up.status_code == status.HTTP_200_OK
        up_json = res_up.json()
        assert up_json['data']['count'] == 1
        assert up_json['data']['results'][0]['pnr'] == b.pnr

        res_cancel = api_client.get('/api/bookings/?filter=cancelled&page=1')
        assert res_cancel.status_code == status.HTTP_200_OK
        assert res_cancel.json()['data']['count'] == 0

    def test_stripe_payment_intent_mode_for_mobile_paymentsheet(self, api_client, mobile_network, settings):
        settings.STRIPE_SECRET_KEY = "sk_test_mobile_key"
        settings.STRIPE_PUBLISHABLE_KEY = "pk_test_mobile_pub"

        trip = mobile_network['trip']
        s0, s1 = mobile_network['stops']
        seat_2a = Seat.objects.get(bus=mobile_network['bus'], seat_number="2A")
        lk = SeatLockService.lock_seats(
            trip=trip, seat_ids=[seat_2a.id], from_stop=s0, to_stop=s1, user=mobile_network['passenger']
        )
        b = BookingService.create_booking_from_locks(
            trip=trip,
            lock_token=uuid.UUID(lk['lock_token']),
            from_stop=s0,
            to_stop=s1,
            passenger_name="Sheet Pax",
            passenger_phone=mobile_network['passenger'].phone,
            passengers_data=[{"seat_id": seat_2a.id, "passenger_name": "Sheet Pax", "gender": Gender.MALE}],
            user=mobile_network['passenger']
        )

        fake_intent = SimpleNamespace(
            id="pi_mobile_sheet_123",
            client_secret="pi_mobile_sheet_123_secret_abc",
            to_dict=lambda: {"id": "pi_mobile_sheet_123", "status": "requires_payment_method"}
        )

        api_client.force_authenticate(user=mobile_network['passenger'])
        with patch('stripe.PaymentIntent.create', return_value=fake_intent) as mock_pi:
            res = api_client.post(
                reverse('payment-initiate'),
                data={
                    "pnr": b.pnr,
                    "method": PaymentMethod.STRIPE,
                    "gateway_name": "stripe",
                    "checkout_mode": "payment_intent",
                },
                format='json'
            )

        assert res.status_code == status.HTTP_201_CREATED
        assert mock_pi.called
        gw = res.json()['data']['gateway']
        assert gw['checkout_mode'] == 'payment_intent'
        assert gw['client_secret'] == "pi_mobile_sheet_123_secret_abc"
        assert gw['publishable_key'] == "pk_test_mobile_pub"
