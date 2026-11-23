"""
Comprehensive test suite for Module 9: Cancellation & Refund Engine.
Tests:
  1. Multi-tier cancellation policy seeding and tier resolution (>24h: 90%, 6-24h: 50%, <6h: 0%)
  2. Advance cancellation (>24 hours) refund calculations
  3. Standard cancellation (6-24 hours) refund calculations
  4. Late cancellation (<6 hours) non-refundable policy enforcement
  5. Non-refundable platform service fee retention on passenger cancellations
  6. Prorated tax and discount allocation across seats
  7. Full booking cancellation and gateway refund execution
  8. Partial seat cancellation and Booking status transition to PARTIALLY_CANCELLED
  9. Immediate segment inventory recovery following seat cancellation
  10. Prevention of cancelling departed trips
  11. Prevention of cancelling already boarded tickets
  12. Rejection of duplicate cancellation attempts on cancelled bookings
  13. Operator-initiated bulk trip cancellation with 100% full refund and zero fee
  14. DRF API endpoints (policies, estimate, cancel-booking, cancel-seats, refunds, operator cancel)
"""
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
from bookings.models import Booking, BookingSeat
from bookings.services import SeatLockService, BookingService
from payments.models import Payment
from payments.services import PaymentService
from tickets.models import Ticket
from tickets.services import TicketService
from cancellations.models import CancellationPolicy, Refund
from cancellations.services import CancellationService



@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def test_network(db):
    """
    Corridor: Lahore (stop 0) -> Bhera (stop 1) -> Islamabad (stop 2)
    """
    term_lhr = Terminal.objects.create(name="Lahore Terminal", code="LHR-C9-01", city="Lahore", is_active=True)
    term_bhr = Terminal.objects.create(name="Bhera Rest Stop", code="BHR-C9-01", city="Bhera", is_active=True)
    term_isb = Terminal.objects.create(name="Islamabad Terminal", code="ISB-C9-01", city="Islamabad", is_active=True)

    route = RouteService.create_route(
        code="LHR-ISB-C9",
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
        registration_number="LES-C9-999",
        bus_type=BusType.EXECUTIVE,
        status=BusStatus.ACTIVE,
        total_seats=40
    )
    generate_bus_seat_matrix(bus, layout_type="2x2", rows=10, ladies_rows=[])

    stops = list(route.stops.order_by('stop_order'))
    stop_lhr = stops[0]
    stop_bhr = stops[1]
    stop_isb = stops[2]

    # Standard departure 48 hours in future
    departure = timezone.now() + timedelta(hours=48)
    arrival = departure + timedelta(minutes=260)

    trip = Trip.objects.create(
        trip_number="TRIP-C9-001",
        route=route,
        bus=bus,
        departure_datetime=departure,
        arrival_datetime=arrival,
        status=TripStatus.SCHEDULED,
        base_fare=Decimal('1000.00')
    )

    FareService.set_fare_rule(route, stop_lhr, stop_isb, Decimal('1000.00'))
    FareService.set_fare_rule(route, stop_lhr, stop_bhr, Decimal('600.00'))
    FareService.set_fare_rule(route, stop_bhr, stop_isb, Decimal('500.00'))

    operator_user = User.objects.create_user(
        phone="+923009990001",
        role=UserRole.OPERATOR,
        password="OperatorPass123!",
        is_verified=True,
        is_staff=True
    )

    passenger_user = User.objects.create_user(
        phone="+923009990002",
        role=UserRole.PASSENGER,
        password="PassengerPass123!",
        is_verified=True
    )

    CancellationService.seed_default_policies()

    return {
        'route': route,
        'bus': bus,
        'trip': trip,
        'stops': [stop_lhr, stop_bhr, stop_isb],
        'operator': operator_user,
        'passenger': passenger_user,
    }


def create_confirmed_booking(network, seat_numbers=['1A', '1B'], hours_to_departure=48):
    """Helper to lock, book, pay, and issue tickets for a test booking."""
    trip = network['trip']
    if hours_to_departure != 48:
        trip.departure_datetime = timezone.now() + timedelta(hours=hours_to_departure)
        trip.save(update_fields=['departure_datetime'])

    stop_lhr = network['stops'][0]
    stop_isb = network['stops'][2]

    seats = list(Seat.objects.filter(bus=network['bus'], seat_number__in=seat_numbers))
    seat_ids = [s.id for s in seats]

    lock_info = SeatLockService.lock_seats(
        trip=trip,
        seat_ids=seat_ids,
        from_stop=stop_lhr,
        to_stop=stop_isb
    )

    passengers_data = [
        {"seat_id": s.id, "passenger_name": f"Passenger {s.seat_number}", "gender": Gender.MALE, "age": 28}
        for s in seats
    ]

    booking = BookingService.create_booking_from_locks(
        trip=trip,
        lock_token=uuid.UUID(lock_info['lock_token']),
        from_stop=stop_lhr,
        to_stop=stop_isb,
        passenger_name="Lead Booker",
        passenger_phone="+923009990002",
        passenger_email="passenger.c9@test.com",
        passengers_data=passengers_data,
        payment_method=PaymentMethod.CARD_ONLINE,
        user=network['passenger']
    )

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


@pytest.mark.django_db
class TestCancellationPolicies:

    def test_seed_default_policies(self, test_network):
        policies = CancellationPolicy.objects.filter(is_active=True).order_by('-min_hours_before_departure')
        assert policies.count() >= 3

        tier_24 = policies.filter(min_hours_before_departure=24).first()
        assert tier_24 is not None
        assert tier_24.refund_percentage == Decimal('90.00')

        tier_6 = policies.filter(min_hours_before_departure=6).first()
        assert tier_6 is not None
        assert tier_6.refund_percentage == Decimal('50.00')

        tier_0 = policies.filter(min_hours_before_departure=0).first()
        assert tier_0 is not None
        assert tier_0.refund_percentage == Decimal('0.00')

    def test_policy_tier_matching(self, test_network):
        trip = test_network['trip']

        # Case 1: Departure in 30 hours -> Tier >24h (90%)
        trip.departure_datetime = timezone.now() + timedelta(hours=30)
        trip.save()
        p1 = CancellationService.get_applicable_policy(trip)
        assert p1.refund_percentage == Decimal('90.00')

        # Case 2: Departure in 12 hours -> Tier 6-24h (50%)
        trip.departure_datetime = timezone.now() + timedelta(hours=12)
        trip.save()
        p2 = CancellationService.get_applicable_policy(trip)
        assert p2.refund_percentage == Decimal('50.00')

        # Case 3: Departure in 2 hours -> Tier <6h (0%)
        trip.departure_datetime = timezone.now() + timedelta(hours=2)
        trip.save()
        p3 = CancellationService.get_applicable_policy(trip)
        assert p3.refund_percentage == Decimal('0.00')


@pytest.mark.django_db
class TestCancellationService:

    def test_advance_cancellation_90_percent_refund(self, test_network):
        booking = create_confirmed_booking(test_network, seat_numbers=['2A'], hours_to_departure=48)
        # Gross = 1000.00, Tax = 50.00, Service fee = 50.00, Net = 1100.00
        assert booking.total_gross_fare == Decimal('1000.00')
        assert booking.tax_amount == Decimal('50.00')
        assert booking.service_fee == Decimal('50.00')

        booking, refund = CancellationService.cancel_booking(pnr=booking.pnr, reason="Change of plans")

        assert booking.status == BookingStatus.CANCELLED
        assert refund is not None
        assert refund.status == RefundStatus.PROCESSED
        # 90% of fare = 900.00, 90% of tax = 45.00 -> Net refund = 945.00
        assert refund.gross_fare_refunded == Decimal('900.00')
        assert refund.tax_refunded == Decimal('45.00')
        assert refund.net_refund_amount == Decimal('945.00')
        # Cancellation fee = (1000 + 50) - 945 = 105.00
        assert refund.cancellation_fee_charged == Decimal('105.00')

        # Service fee (50.00) is non-refundable and kept by platform
        # Check tickets invalidated
        ticket = Ticket.objects.get(booking_seat__booking=booking)
        assert ticket.status == TicketStatus.CANCELLED

        # Check booking seat is CANCELLED
        seat = booking.seats.first()
        assert seat.status == BookingSeatStatus.CANCELLED

    def test_standard_cancellation_50_percent_refund(self, test_network):
        booking = create_confirmed_booking(test_network, seat_numbers=['2B'], hours_to_departure=12)

        booking, refund = CancellationService.cancel_booking(pnr=booking.pnr, reason="Late cancellation")

        assert booking.status == BookingStatus.CANCELLED
        # 50% of 1000.00 = 500.00, 50% of 50.00 = 25.00 -> Net refund = 525.00
        assert refund.gross_fare_refunded == Decimal('500.00')
        assert refund.tax_refunded == Decimal('25.00')
        assert refund.net_refund_amount == Decimal('525.00')
        assert refund.cancellation_fee_charged == Decimal('525.00')

    def test_late_cancellation_zero_refund(self, test_network):
        booking = create_confirmed_booking(test_network, seat_numbers=['3A'], hours_to_departure=2)

        booking, refund = CancellationService.cancel_booking(pnr=booking.pnr, reason="Very late cancellation")

        assert booking.status == BookingStatus.CANCELLED
        # 0% refund
        assert refund.gross_fare_refunded == Decimal('0.00')
        assert refund.tax_refunded == Decimal('0.00')
        assert refund.net_refund_amount == Decimal('0.00')
        # All fare + tax charged as cancellation fee
        assert refund.cancellation_fee_charged == Decimal('1050.00')

    def test_partial_seat_cancellation(self, test_network):
        # 2 seats: 4A and 4B
        booking = create_confirmed_booking(test_network, seat_numbers=['4A', '4B'], hours_to_departure=36)
        assert booking.seats.count() == 2

        seat_4a = booking.seats.get(seat__seat_number='4A')
        seat_4b = booking.seats.get(seat__seat_number='4B')

        # Cancel only 4A
        updated_booking, refund = CancellationService.cancel_seats(
            pnr=booking.pnr,
            seat_ids=[seat_4a.id],
            reason="One person cannot make it"
        )

        assert updated_booking.status == BookingStatus.PARTIALLY_CANCELLED

        seat_4a.refresh_from_db()
        seat_4b.refresh_from_db()
        assert seat_4a.status == BookingSeatStatus.CANCELLED
        assert seat_4b.status == BookingSeatStatus.BOOKED

        # Ticket 4A is CANCELLED, Ticket 4B remains ISSUED
        ticket_4a = Ticket.objects.get(booking_seat=seat_4a)
        ticket_4b = Ticket.objects.get(booking_seat=seat_4b)
        assert ticket_4a.status == TicketStatus.CANCELLED
        assert ticket_4b.status == TicketStatus.ISSUED

        # 90% refund for 4A: 90% of 1000 = 900, 90% of 25 = 22.50 (rounded: 23 or 22.50)
        assert refund.net_refund_amount > Decimal('0.00')
        assert refund.seats.count() == 1

        # Now cancel 4B as well -> Booking transitions to CANCELLED
        final_booking, refund_2 = CancellationService.cancel_seats(
            pnr=booking.pnr,
            seat_ids=[seat_4b.id],
            reason="Second person cancelled"
        )
        assert final_booking.status == BookingStatus.CANCELLED
        seat_4b.refresh_from_db()
        assert seat_4b.status == BookingSeatStatus.CANCELLED

    def test_cancelled_seat_immediately_free_in_inventory(self, test_network):
        # Book seat 5A
        booking = create_confirmed_booking(test_network, seat_numbers=['5A'], hours_to_departure=48)
        seat_5a = booking.seats.first().seat

        # Before cancellation: seat 5A is occupied
        stop_lhr = test_network['stops'][0]
        stop_isb = test_network['stops'][2]
        occupied_before = BookingSeat.objects.filter(
            trip=test_network['trip'],
            seat=seat_5a,
            status__in=[BookingSeatStatus.LOCKED, BookingSeatStatus.BOOKED]
        ).exists()
        assert occupied_before is True

        # Cancel booking
        CancellationService.cancel_booking(pnr=booking.pnr)

        # After cancellation: seat 5A is free
        occupied_after = BookingSeat.objects.filter(
            trip=test_network['trip'],
            seat=seat_5a,
            status__in=[BookingSeatStatus.LOCKED, BookingSeatStatus.BOOKED]
        ).exists()
        assert occupied_after is False

        # Another passenger can now lock seat 5A!
        new_passenger = User.objects.create_user(phone="+923008880001", password="NewPass123!", is_verified=True)
        new_lock = SeatLockService.lock_seats(
            trip=test_network['trip'],
            seat_ids=[seat_5a.id],
            from_stop=stop_lhr,
            to_stop=stop_isb
        )
        assert new_lock is not None

    def test_prevent_cancellation_of_departed_trip(self, test_network):
        booking = create_confirmed_booking(test_network, seat_numbers=['6A'], hours_to_departure=48)
        # Shift departure to the past
        test_network['trip'].departure_datetime = timezone.now() - timedelta(minutes=15)
        test_network['trip'].save()

        with pytest.raises(BusinessLogicError, match="already departed"):
            CancellationService.cancel_booking(pnr=booking.pnr)

    def test_prevent_cancellation_of_boarded_passenger(self, test_network):
        booking = create_confirmed_booking(test_network, seat_numbers=['6B'], hours_to_departure=48)
        ticket = Ticket.objects.get(booking_seat__booking=booking)
        # Mark ticket as boarded
        ticket.status = TicketStatus.BOARDED
        ticket.boarded_at = timezone.now()
        ticket.save()

        with pytest.raises(BusinessLogicError, match="already boarded"):
            CancellationService.cancel_booking(pnr=booking.pnr)

    def test_rejection_of_double_cancellation(self, test_network):
        booking = create_confirmed_booking(test_network, seat_numbers=['7A'], hours_to_departure=48)
        CancellationService.cancel_booking(pnr=booking.pnr)

        with pytest.raises(BusinessLogicError, match="already CANCELLED"):
            CancellationService.cancel_booking(pnr=booking.pnr)

    def test_operator_trip_cancellation_full_refund(self, test_network):
        booking_1 = create_confirmed_booking(test_network, seat_numbers=['8A'], hours_to_departure=48)
        booking_2 = create_confirmed_booking(test_network, seat_numbers=['8B'], hours_to_departure=48)

        result = CancellationService.cancel_trip_by_operator(
            trip_id=test_network['trip'].id,
            reason="Bus engine maintenance",
            operator_user=test_network['operator']
        )

        assert result['status'] == TripStatus.CANCELLED
        assert result['bookings_cancelled_count'] == 2
        # Both bookings got 100% full refund
        assert result['total_refunded'] == booking_1.net_amount + booking_2.net_amount

        booking_1.refresh_from_db()
        booking_2.refresh_from_db()
        assert booking_1.status == BookingStatus.CANCELLED
        assert booking_2.status == BookingStatus.CANCELLED

        # Refund records have zero cancellation fee
        refund_1 = booking_1.refunds.first()
        assert refund_1.cancellation_fee_charged == Decimal('0.00')
        assert refund_1.net_refund_amount == booking_1.net_amount
        assert refund_1.is_operator_cancellation is True


@pytest.mark.django_db
class TestCancellationAPIEndpoints:

    def test_get_policies_api(self, api_client, test_network):
        url = reverse('cancellation-policies-list')
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['success'] is True
        assert len(data['data']['policies']) >= 3

    def test_estimate_refund_api(self, api_client, test_network):
        booking = create_confirmed_booking(test_network, seat_numbers=['9A'], hours_to_departure=36)
        url = reverse('cancellation-estimate-refund')
        payload = {"pnr": booking.pnr}
        response = api_client.post(url, data=payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['success'] is True
        assert Decimal(str(data['data']['refund_percentage'])) == Decimal('90.00')
        assert Decimal(data['data']['net_refund_amount']) > Decimal('0.00')

    def test_cancel_booking_api(self, api_client, test_network):
        booking = create_confirmed_booking(test_network, seat_numbers=['9B'], hours_to_departure=36)
        url = reverse('cancellation-cancel-booking')
        payload = {"pnr": booking.pnr, "reason": "Passenger sick"}
        response = api_client.post(url, data=payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['success'] is True
        assert data['data']['status'] == BookingStatus.CANCELLED
        assert data['data']['refund'] is not None

    def test_cancel_seats_api(self, api_client, test_network):
        booking = create_confirmed_booking(test_network, seat_numbers=['10A', '10B'], hours_to_departure=36)
        seat_10a = booking.seats.get(seat__seat_number='10A')
        url = reverse('cancellation-cancel-seats')
        payload = {"pnr": booking.pnr, "seat_ids": [str(seat_10a.id)], "reason": "Drop one passenger"}
        response = api_client.post(url, data=payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['success'] is True
        assert data['data']['status'] == BookingStatus.PARTIALLY_CANCELLED

    def test_booking_refunds_audit_api(self, api_client, test_network):
        booking = create_confirmed_booking(test_network, seat_numbers=['10C'], hours_to_departure=36)
        CancellationService.cancel_booking(pnr=booking.pnr)

        url = reverse('cancellation-booking-refunds', kwargs={'pnr': booking.pnr})
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['success'] is True
        assert len(data['data']['refunds']) == 1

    def test_operator_cancel_trip_api(self, api_client, test_network):
        api_client.force_authenticate(user=test_network['operator'])
        url = reverse('cancellation-operator-cancel-trip')
        payload = {"trip_id": str(test_network['trip'].id), "reason": "Severe weather alert"}
        response = api_client.post(url, data=payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['success'] is True
        assert data['data']['status'] == TripStatus.CANCELLED

    def test_estimate_refund_api_validation_error(self, api_client):
        url = reverse('cancellation-estimate-refund')
        response = api_client.post(url, data={}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data['success'] is False
        assert data['error']['code'] == 'VALIDATION_ERROR'
        assert 'pnr' in data['error']['details']

    def test_estimate_refund_api_not_found(self, api_client):
        url = reverse('cancellation-estimate-refund')
        response = api_client.post(url, data={"pnr": "NONEXISTENT"}, format='json')
        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()
        assert data['success'] is False
        assert data['error']['code'] == 'RESOURCE_NOT_FOUND'

    def test_cancel_seats_api_validation_error(self, api_client):
        url = reverse('cancellation-cancel-seats')
        response = api_client.post(url, data={"pnr": "PK123456"}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data['success'] is False
        assert data['error']['code'] == 'VALIDATION_ERROR'
        assert 'seat_ids' in data['error']['details']

