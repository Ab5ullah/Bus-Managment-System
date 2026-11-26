"""
Comprehensive test suite for Module 6: Concurrency, Seat Locking & Booking Engine.
Tests:
  1. Seat Locking & Invariant Validations (max 6 seats, valid segment, bus check)
  2. Segment-Aware Collision Prevention (u < j and v > i)
  3. Non-overlapping Disjoint Segments Allowed (A->B booked, B->C completely free!)
  4. Automatic Expiration (expired locks ignored at query time)
  5. Early Lock Release (unlock-seats)
  6. PNR Creation & Financial Calculations (Gross, Tax, Service Fee, Net)
  7. Gender Seating Rules (Male passenger on ladies-only seat rejected)
  8. Counter Agent Walk-In Cash Ticketing (Immediate CONFIRMED state)
  9. Full & Partial Seat Cancellation with Prorated Financial Refunds
  10. DRF Endpoints & RBAC Permissions
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
    SeatType,
    TripStatus,
    BookingStatus,
    BookingSeatStatus,
    PaymentMethod,
    Gender,
)
from common.exceptions import BusinessLogicError
from accounts.models import User
from fleet.models import Bus, Seat
from fleet.services import generate_bus_seat_matrix
from routes.models import Terminal, Route, RouteStop
from routes.services import RouteService
from trips.models import Trip, TripSeatBlock, FareRule
from trips.services import TripSeatBlockService, FareService
from bookings.models import Booking, BookingSeat, SeatLock
from bookings.services import SeatLockService, BookingService


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def test_users(db):
    passenger = User.objects.create_user(
        phone="+923001112233",
        role=UserRole.PASSENGER,
        password="PassengerPassword123!",
        is_verified=True
    )
    counter_agent = User.objects.create_user(
        phone="+923004445566",
        role=UserRole.COUNTER_AGENT,
        password="AgentPassword123!",
        is_verified=True,
        is_staff=True
    )
    operator = User.objects.create_user(
        phone="+923007778899",
        role=UserRole.OPERATOR,
        password="OperatorPassword123!",
        is_verified=True,
        is_staff=True
    )
    return {
        'passenger': passenger,
        'counter_agent': counter_agent,
        'operator': operator
    }


@pytest.fixture
def booking_network(db):
    """
    3-stop corridor:
      Stop 0: Lahore (km 0, offset 0 min)
      Stop 1: Bhera (km 190, offset 120 min)
      Stop 2: Islamabad (km 375, offset 260 min)
    """
    term_lhr = Terminal.objects.create(name="Lahore Terminal", code="LHR-BK-01", city="Lahore", is_active=True)
    term_bhr = Terminal.objects.create(name="Bhera Station", code="BHR-BK-01", city="Bhera", is_active=True)
    term_isb = Terminal.objects.create(name="Islamabad Terminal", code="ISB-BK-01", city="Islamabad", is_active=True)

    route = RouteService.create_route(
        code="LHR-ISB-BK",
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
        registration_number="LES-BK-777",
        bus_type=BusType.EXECUTIVE,
        status=BusStatus.ACTIVE,
        total_seats=40
    )
    # Row 1 and 2 are ladies only (Seats 1A, 1B, 1C, 1D, 2A, 2B, 2C, 2D)
    generate_bus_seat_matrix(bus, layout_type="2x2", rows=10, ladies_rows=[1, 2])

    now = timezone.now().replace(microsecond=0)
    dep = now + timedelta(days=1)
    arr = dep + timedelta(minutes=260)
    trip = Trip.objects.create(
        trip_number="TRIP-BK-001",
        route=route,
        bus=bus,
        departure_datetime=dep,
        arrival_datetime=arr,
        status=TripStatus.SCHEDULED,
        base_fare=Decimal("2000.00")
    )

    stop_lhr = route.stops.get(stop_order=0)
    stop_bhr = route.stops.get(stop_order=1)
    stop_isb = route.stops.get(stop_order=2)

    # Set segment fares
    FareService.set_fare_rule(route, stop_lhr, stop_bhr, Decimal("1000.00"))
    FareService.set_fare_rule(route, stop_bhr, stop_isb, Decimal("1200.00"))
    FareService.set_fare_rule(route, stop_lhr, stop_isb, Decimal("2000.00"))

    return {
        'route': route,
        'bus': bus,
        'trip': trip,
        'stops': {
            'lhr': stop_lhr,
            'bhr': stop_bhr,
            'isb': stop_isb,
        }
    }


@pytest.mark.django_db
class TestSeatLockService:

    def test_lock_seats_success(self, booking_network):
        trip = booking_network['trip']
        stop_lhr = booking_network['stops']['lhr']
        stop_isb = booking_network['stops']['isb']
        seat_3a = trip.bus.seats.get(seat_number="3A")
        seat_3b = trip.bus.seats.get(seat_number="3B")

        result = SeatLockService.lock_seats(
            trip=trip,
            seat_ids=[seat_3a.id, seat_3b.id],
            from_stop=stop_lhr,
            to_stop=stop_isb,
            lock_duration_minutes=10
        )

        assert 'lock_token' in result
        assert len(result['locked_seats']) == 2
        assert SeatLock.objects.filter(lock_token=result['lock_token']).count() == 2

    def test_max_seats_constraint_rejected(self, booking_network):
        """Locking more than 6 seats in a single reservation is rejected."""
        trip = booking_network['trip']
        stop_lhr = booking_network['stops']['lhr']
        stop_isb = booking_network['stops']['isb']
        seats = list(trip.bus.seats.all()[:7])

        with pytest.raises(BusinessLogicError) as exc_info:
            SeatLockService.lock_seats(
                trip=trip,
                seat_ids=[s.id for s in seats],
                from_stop=stop_lhr,
                to_stop=stop_isb
            )
        assert "Maximum of 6 seats" in str(exc_info.value)

    def test_lock_overlapping_segment_rejected(self, booking_network):
        """
        Customer A holds Seat 3A from Lahore (0) to Islamabad (2).
        Customer B attempts to hold Seat 3A from Bhera (1) to Islamabad (2).
        Must be strictly rejected because intervals [0, 2) and [1, 2) overlap!
        """
        trip = booking_network['trip']
        stop_lhr = booking_network['stops']['lhr']
        stop_bhr = booking_network['stops']['bhr']
        stop_isb = booking_network['stops']['isb']
        seat_3a = trip.bus.seats.get(seat_number="3A")

        # Customer A locks LHR -> ISB
        SeatLockService.lock_seats(
            trip=trip,
            seat_ids=[seat_3a.id],
            from_stop=stop_lhr,
            to_stop=stop_isb
        )

        # Customer B attempts BHR -> ISB on same seat
        with pytest.raises(BusinessLogicError) as exc_info:
            SeatLockService.lock_seats(
                trip=trip,
                seat_ids=[seat_3a.id],
                from_stop=stop_bhr,
                to_stop=stop_isb
            )
        assert "currently held" in str(exc_info.value)

    def test_disjoint_segment_locking_allowed(self, booking_network):
        """
        Customer A holds Seat 3A from Lahore (0) to Bhera (1) -> [0, 1).
        Customer B holds Seat 3A from Bhera (1) to Islamabad (2) -> [1, 2).
        These segments are mathematically disjoint ([0, 1) and [1, 2) do NOT overlap).
        Customer B MUST succeed! This proves segment reusability.
        """
        trip = booking_network['trip']
        stop_lhr = booking_network['stops']['lhr']
        stop_bhr = booking_network['stops']['bhr']
        stop_isb = booking_network['stops']['isb']
        seat_3a = trip.bus.seats.get(seat_number="3A")

        # Customer A locks [0, 1)
        res_a = SeatLockService.lock_seats(
            trip=trip,
            seat_ids=[seat_3a.id],
            from_stop=stop_lhr,
            to_stop=stop_bhr
        )
        assert res_a['lock_token'] is not None

        # Customer B locks [1, 2) on the EXACT same seat!
        res_b = SeatLockService.lock_seats(
            trip=trip,
            seat_ids=[seat_3a.id],
            from_stop=stop_bhr,
            to_stop=stop_isb
        )
        assert res_b['lock_token'] is not None
        assert SeatLock.objects.filter(seat=seat_3a).count() == 2

    def test_lock_administratively_blocked_seat_rejected(self, booking_network):
        trip = booking_network['trip']
        stop_lhr = booking_network['stops']['lhr']
        stop_isb = booking_network['stops']['isb']
        seat_4a = trip.bus.seats.get(seat_number="4A")

        # Admin block
        TripSeatBlockService.block_seat(trip, seat_4a, reason="VIP Hold")

        with pytest.raises(BusinessLogicError) as exc_info:
            SeatLockService.lock_seats(
                trip=trip,
                seat_ids=[seat_4a.id],
                from_stop=stop_lhr,
                to_stop=stop_isb
            )
        assert "administratively blocked" in str(exc_info.value)

    def test_expired_lock_ignored_and_replaced(self, booking_network):
        """
        If a lock expired (locked_until in the past), another user can immediately lock it.
        """
        trip = booking_network['trip']
        stop_lhr = booking_network['stops']['lhr']
        stop_isb = booking_network['stops']['isb']
        seat_5a = trip.bus.seats.get(seat_number="5A")

        # Create expired lock in the past
        past = timezone.now() - timedelta(minutes=5)
        SeatLock.objects.create(
            trip=trip,
            seat=seat_5a,
            from_stop=stop_lhr,
            to_stop=stop_isb,
            from_stop_order=0,
            to_stop_order=2,
            locked_until=past
        )

        # Now Customer B requests seat_5a
        res = SeatLockService.lock_seats(
            trip=trip,
            seat_ids=[seat_5a.id],
            from_stop=stop_lhr,
            to_stop=stop_isb
        )
        assert res['lock_token'] is not None
        # Old expired lock was pruned
        assert SeatLock.objects.filter(seat=seat_5a, locked_until__gt=timezone.now()).count() == 1

    def test_early_unlock_seats(self, booking_network):
        trip = booking_network['trip']
        stop_lhr = booking_network['stops']['lhr']
        stop_isb = booking_network['stops']['isb']
        seat_6a = trip.bus.seats.get(seat_number="6A")

        res = SeatLockService.lock_seats(
            trip=trip,
            seat_ids=[seat_6a.id],
            from_stop=stop_lhr,
            to_stop=stop_isb
        )
        token = res['lock_token']
        assert SeatLock.objects.filter(lock_token=token).count() == 1

        deleted = SeatLockService.release_locks(token)
        assert deleted == 1
        assert SeatLock.objects.filter(lock_token=token).count() == 0


@pytest.mark.django_db
class TestBookingService:

    def test_create_booking_from_locks_success(self, booking_network, test_users):
        trip = booking_network['trip']
        stop_lhr = booking_network['stops']['lhr']
        stop_isb = booking_network['stops']['isb']
        seat_3a = trip.bus.seats.get(seat_number="3A")
        seat_3b = trip.bus.seats.get(seat_number="3B")

        # 1. Lock seats
        lock_res = SeatLockService.lock_seats(
            trip=trip,
            seat_ids=[seat_3a.id, seat_3b.id],
            from_stop=stop_lhr,
            to_stop=stop_isb
        )
        token = lock_res['lock_token']

        # 2. Confirm booking
        booking = BookingService.create_booking_from_locks(
            trip=trip,
            lock_token=token,
            from_stop=stop_lhr,
            to_stop=stop_isb,
            passenger_name="Ali Khan",
            passenger_phone="+923001234567",
            passenger_email="ali@example.com",
            user=test_users['passenger'],
            passengers_data=[
                {'seat_id': seat_3a.id, 'passenger_name': 'Ali Khan', 'gender': Gender.MALE, 'age': 30},
                {'seat_id': seat_3b.id, 'passenger_name': 'Hamza Khan', 'gender': Gender.MALE, 'age': 25},
            ]
        )

        assert booking.pnr.startswith("PK")
        assert booking.status == BookingStatus.PENDING
        assert booking.seats.count() == 2
        assert SeatLock.objects.filter(lock_token=token).count() == 0

        # Financial breakdown:
        # Gross = 2000.00 * 2 = 4000.00
        # Tax = 5% of 4000 = 200.00
        # Service Fee = 50.00
        # Net Amount = 4250.00
        assert booking.total_gross_fare == Decimal("4000.00")
        assert booking.tax_amount == Decimal("200.00")
        assert booking.service_fee == Decimal("50.00")
        assert booking.net_amount == Decimal("4250.00")

    def test_male_passenger_in_ladies_only_seat_rejected(self, booking_network):
        """Male passenger attempting to reserve Row 1 or 2 (ladies only) must be rejected."""
        trip = booking_network['trip']
        stop_lhr = booking_network['stops']['lhr']
        stop_isb = booking_network['stops']['isb']
        seat_1a = trip.bus.seats.get(seat_number="1A")
        assert seat_1a.is_ladies_only is True

        lock_res = SeatLockService.lock_seats(
            trip=trip,
            seat_ids=[seat_1a.id],
            from_stop=stop_lhr,
            to_stop=stop_isb
        )

        with pytest.raises(BusinessLogicError) as exc_info:
            BookingService.create_booking_from_locks(
                trip=trip,
                lock_token=lock_res['lock_token'],
                from_stop=stop_lhr,
                to_stop=stop_isb,
                passenger_name="Tariq Mahmood",
                passenger_phone="+923005551212",
                passengers_data=[
                    {'seat_id': seat_1a.id, 'passenger_name': 'Tariq Mahmood', 'gender': Gender.MALE, 'age': 40}
                ]
            )
        assert "strictly reserved for female passengers" in str(exc_info.value)

    def test_female_passenger_in_ladies_only_seat_success(self, booking_network):
        """Female passenger in ladies seat succeeds."""
        trip = booking_network['trip']
        stop_lhr = booking_network['stops']['lhr']
        stop_isb = booking_network['stops']['isb']
        seat_1a = trip.bus.seats.get(seat_number="1A")

        lock_res = SeatLockService.lock_seats(
            trip=trip,
            seat_ids=[seat_1a.id],
            from_stop=stop_lhr,
            to_stop=stop_isb
        )

        booking = BookingService.create_booking_from_locks(
            trip=trip,
            lock_token=lock_res['lock_token'],
            from_stop=stop_lhr,
            to_stop=stop_isb,
            passenger_name="Fatima Noor",
            passenger_phone="+923005551212",
            passengers_data=[
                {'seat_id': seat_1a.id, 'passenger_name': 'Fatima Noor', 'gender': Gender.FEMALE, 'age': 28}
            ]
        )
        assert booking.status == BookingStatus.PENDING
        assert booking.seats.first().gender == Gender.FEMALE

    def test_counter_agent_walkin_booking_confirmed_immediately(self, booking_network, test_users):
        """Counter Agent issues immediate walk-in cash ticket (CONFIRMED state, BOOKED seats)."""
        trip = booking_network['trip']
        stop_lhr = booking_network['stops']['lhr']
        stop_isb = booking_network['stops']['isb']
        seat_7a = trip.bus.seats.get(seat_number="7A")

        booking = BookingService.confirm_counter_booking(
            trip=trip,
            from_stop=stop_lhr,
            to_stop=stop_isb,
            agent_user=test_users['counter_agent'],
            passenger_name="Walk-in Passenger",
            passenger_phone="+923000000001",
            passengers_data=[
                {'seat_id': seat_7a.id, 'passenger_name': 'Walk-in Passenger', 'gender': Gender.MALE, 'age': 35}
            ],
            payment_method=PaymentMethod.CASH_COUNTER
        )

        assert booking.status == BookingStatus.CONFIRMED
        assert booking.booked_by_agent == test_users['counter_agent']
        assert booking.confirmed_at is not None
        assert booking.seats.first().status == BookingSeatStatus.BOOKED

    def test_cancel_entire_booking(self, booking_network, test_users):
        """Cancelling a booking transitions booking and seats to CANCELLED."""
        trip = booking_network['trip']
        stop_lhr = booking_network['stops']['lhr']
        stop_isb = booking_network['stops']['isb']
        seat_8a = trip.bus.seats.get(seat_number="8A")

        booking = BookingService.confirm_counter_booking(
            trip=trip,
            from_stop=stop_lhr,
            to_stop=stop_isb,
            agent_user=test_users['counter_agent'],
            passenger_name="Test Cancel",
            passenger_phone="+923000000002",
            passengers_data=[
                {'seat_id': seat_8a.id, 'passenger_name': 'Test Cancel', 'gender': Gender.MALE}
            ]
        )

        cancelled = BookingService.cancel_booking(booking=booking)
        assert cancelled.status == BookingStatus.CANCELLED
        assert cancelled.seats.first().status == BookingSeatStatus.CANCELLED

        # Seat 8A is now available again!
        res_lock = SeatLockService.lock_seats(
            trip=trip,
            seat_ids=[seat_8a.id],
            from_stop=stop_lhr,
            to_stop=stop_isb
        )
        assert res_lock['lock_token'] is not None

    def test_cancel_individual_seat_partial(self, booking_network, test_users):
        """Cancelling 1 seat of a 2-seat booking marks booking PARTIALLY_CANCELLED with prorated refund."""
        trip = booking_network['trip']
        stop_lhr = booking_network['stops']['lhr']
        stop_isb = booking_network['stops']['isb']
        seat_9a = trip.bus.seats.get(seat_number="9A")
        seat_9b = trip.bus.seats.get(seat_number="9B")

        booking = BookingService.confirm_counter_booking(
            trip=trip,
            from_stop=stop_lhr,
            to_stop=stop_isb,
            agent_user=test_users['counter_agent'],
            passenger_name="Family Trip",
            passenger_phone="+923000000003",
            passengers_data=[
                {'seat_id': seat_9a.id, 'passenger_name': 'Person 1', 'gender': Gender.MALE},
                {'seat_id': seat_9b.id, 'passenger_name': 'Person 2', 'gender': Gender.MALE},
            ]
        )

        seat_to_cancel = booking.seats.get(seat=seat_9a)
        updated_booking, refund_amount = BookingService.cancel_seat(booking, seat_to_cancel)

        assert updated_booking.status == BookingStatus.PARTIALLY_CANCELLED
        seat_to_cancel.refresh_from_db()
        assert seat_to_cancel.status == BookingSeatStatus.CANCELLED

        # Refund = 2000.00 fare + 100.00 tax (5%) = 2100.00
        assert refund_amount == Decimal("2100.00")


@pytest.mark.django_db
class TestBookingAPIEndpoints:

    def test_api_lock_and_unlock_flow(self, api_client, booking_network, test_users):
        trip = booking_network['trip']
        stop_lhr = booking_network['stops']['lhr']
        stop_isb = booking_network['stops']['isb']
        seat = trip.bus.seats.get(seat_number="10A")

        url_lock = reverse('bookings:lock-seats')
        # 0. Unauthenticated user must log in first
        res_unauth = api_client.post(url_lock, {
            'trip_id': str(trip.id),
            'seat_ids': [str(seat.id)],
            'from_stop': str(stop_lhr.id),
            'to_stop': str(stop_isb.id)
        }, format='json')
        assert res_unauth.status_code == status.HTTP_401_UNAUTHORIZED

        api_client.force_authenticate(user=test_users['passenger'])

        # 1. Lock seats
        res_lock = api_client.post(url_lock, {
            'trip_id': str(trip.id),
            'seat_ids': [str(seat.id)],
            'from_stop': str(stop_lhr.id),
            'to_stop': str(stop_isb.id)
        }, format='json')

        assert res_lock.status_code == status.HTTP_200_OK
        assert res_lock.data['success'] is True
        token = res_lock.data['data']['lock_token']

        # 2. Release lock early
        url_unlock = reverse('bookings:unlock-seats')
        res_unlock = api_client.post(url_unlock, {'lock_token': token}, format='json')
        assert res_unlock.status_code == status.HTTP_200_OK
        assert res_unlock.data['data']['released_seats_count'] == 1

    def test_api_counter_booking_rbacs(self, api_client, booking_network, test_users):
        """Counter booking requires Counter Agent role; normal passenger receives 403."""
        trip = booking_network['trip']
        stop_lhr = booking_network['stops']['lhr']
        stop_isb = booking_network['stops']['isb']
        seat = trip.bus.seats.get(seat_number="10B")
        url_counter = reverse('bookings:booking-counter')

        payload = {
            'trip_id': str(trip.id),
            'from_stop': str(stop_lhr.id),
            'to_stop': str(stop_isb.id),
            'passenger_name': "Counter Passenger",
            'passenger_phone': "+923001239999",
            'passengers': [
                {'seat_id': str(seat.id), 'passenger_name': 'Counter Passenger', 'gender': Gender.MALE}
            ],
            'payment_method': PaymentMethod.CASH_COUNTER
        }

        # Passenger fails
        api_client.force_authenticate(user=test_users['passenger'])
        res_fail = api_client.post(url_counter, payload, format='json')
        assert res_fail.status_code == status.HTTP_403_FORBIDDEN

        # Counter Agent succeeds
        api_client.force_authenticate(user=test_users['counter_agent'])
        res_success = api_client.post(url_counter, payload, format='json')
        assert res_success.status_code == status.HTTP_201_CREATED
        assert res_success.data['data']['status'] == BookingStatus.CONFIRMED

    def test_api_booking_lookup_by_pnr(self, api_client, booking_network, test_users):
        trip = booking_network['trip']
        stop_lhr = booking_network['stops']['lhr']
        stop_isb = booking_network['stops']['isb']
        seat = trip.bus.seats.get(seat_number="10C")

        booking = BookingService.confirm_counter_booking(
            trip=trip,
            from_stop=stop_lhr,
            to_stop=stop_isb,
            agent_user=test_users['counter_agent'],
            passenger_name="Lookup Test",
            passenger_phone="+923008889999",
            passengers_data=[
                {'seat_id': seat.id, 'passenger_name': 'Lookup Test', 'gender': Gender.MALE}
            ]
        )

        url_lookup = reverse('bookings:booking-detail', kwargs={'pnr': booking.pnr})
        res = api_client.get(url_lookup)
        assert res.status_code == status.HTTP_200_OK
        assert res.data['data']['pnr'] == booking.pnr
        assert res.data['data']['passenger_name'] == "Lookup Test"


@pytest.mark.django_db
class TestDynamicGenderAdjacentSeating:
    """
    Tests dynamic open seating where any seat can be chosen for Male or Female,
    and once a seat is selected/booked by a Female, a Male cannot select next to it.
    """

    def test_default_matrix_has_open_seating(self):
        from fleet.models import Bus
        from fleet.services import generate_bus_seat_matrix

        bus = Bus.objects.create(registration_number="OPEN-SEAT-1", bus_type=BusType.EXECUTIVE)
        seats = generate_bus_seat_matrix(bus=bus, layout_type="2x2", rows=5)
        assert len(seats) == 20
        assert all(not s.is_ladies_only for s in seats)

    def test_adjacent_seat_to_booked_female_blocks_male_allows_female_and_aisle(self, api_client, booking_network, test_users):
        from search.services import SegmentAvailabilityService

        trip = booking_network['trip']
        stop_lhr = booking_network['stops']['lhr']
        stop_isb = booking_network['stops']['isb']

        seat_3a = trip.bus.seats.get(seat_number="3A")  # col 1 (pair with 3B)
        seat_3b = trip.bus.seats.get(seat_number="3B")  # col 2 (adjacent to 3A)
        seat_3c = trip.bus.seats.get(seat_number="3C")  # col 3 (across the aisle)

        # Female books 3A
        BookingService.confirm_counter_booking(
            trip=trip,
            from_stop=stop_lhr,
            to_stop=stop_isb,
            agent_user=test_users['counter_agent'],
            passenger_name="Ayesha",
            passenger_phone="+923007771111",
            passengers_data=[
                {'seat_id': seat_3a.id, 'passenger_name': 'Ayesha', 'gender': Gender.FEMALE}
            ]
        )

        # Check Seat Map metadata
        seat_map = SegmentAvailabilityService.get_trip_seat_status_map(trip, stop_lhr, stop_isb)
        info_3a = next(s for s in seat_map['seats'] if s['seat_number'] == "3A")
        info_3b = next(s for s in seat_map['seats'] if s['seat_number'] == "3B")
        info_3c = next(s for s in seat_map['seats'] if s['seat_number'] == "3C")

        assert info_3a['occupied_gender'] == Gender.FEMALE
        assert info_3b['female_adjacent_restricted'] is True
        assert info_3b['allowed_genders'] == [Gender.FEMALE]
        assert info_3c['female_adjacent_restricted'] is False
        assert Gender.MALE in info_3c['allowed_genders']

        # Male trying to lock 3B next to Female 3A must be rejected
        with pytest.raises(BusinessLogicError, match="adjacent seat '3A'"):
            SeatLockService.lock_seats(
                trip=trip,
                seat_ids=[seat_3b.id],
                from_stop=stop_lhr,
                to_stop=stop_isb,
                seat_genders={str(seat_3b.id): Gender.MALE}
            )

        # Male locking without seat_genders then submitting MALE on 3B at booking creation must also fail
        lock_raw = SeatLockService.lock_seats(
            trip=trip,
            seat_ids=[seat_3b.id],
            from_stop=stop_lhr,
            to_stop=stop_isb
        )
        with pytest.raises(BusinessLogicError, match="adjacent seat '3A'"):
            BookingService.create_booking_from_locks(
                trip=trip,
                lock_token=lock_raw['lock_token'],
                from_stop=stop_lhr,
                to_stop=stop_isb,
                passenger_name="Bilal",
                passenger_phone="+923007772222",
                passengers_data=[
                    {'seat_id': seat_3b.id, 'passenger_name': 'Bilal', 'gender': Gender.MALE}
                ]
            )
        SeatLockService.release_locks(uuid.UUID(lock_raw['lock_token']))

        # Male booking 3C across the aisle succeeds!
        lock_3c = SeatLockService.lock_seats(
            trip=trip,
            seat_ids=[seat_3c.id],
            from_stop=stop_lhr,
            to_stop=stop_isb,
            seat_genders={str(seat_3c.id): Gender.MALE}
        )
        b_male = BookingService.create_booking_from_locks(
            trip=trip,
            lock_token=lock_3c['lock_token'],
            from_stop=stop_lhr,
            to_stop=stop_isb,
            passenger_name="Bilal",
            passenger_phone="+923007772222",
            passengers_data=[
                {'seat_id': seat_3c.id, 'passenger_name': 'Bilal', 'gender': Gender.MALE}
            ]
        )
        assert b_male.seats.first().seat_id == seat_3c.id

        # Another Female booking 3B next to 3A succeeds!
        lock_3b_f = SeatLockService.lock_seats(
            trip=trip,
            seat_ids=[seat_3b.id],
            from_stop=stop_lhr,
            to_stop=stop_isb,
            seat_genders={str(seat_3b.id): Gender.FEMALE}
        )
        b_fem = BookingService.create_booking_from_locks(
            trip=trip,
            lock_token=lock_3b_f['lock_token'],
            from_stop=stop_lhr,
            to_stop=stop_isb,
            passenger_name="Sana",
            passenger_phone="+923007773333",
            passengers_data=[
                {'seat_id': seat_3b.id, 'passenger_name': 'Sana', 'gender': Gender.FEMALE}
            ]
        )
        assert b_fem.seats.first().gender == Gender.FEMALE

    def test_adjacent_seat_to_locked_female_blocks_male(self, booking_network):
        trip = booking_network['trip']
        stop_lhr = booking_network['stops']['lhr']
        stop_isb = booking_network['stops']['isb']

        seat_4c = trip.bus.seats.get(seat_number="4C")
        seat_4d = trip.bus.seats.get(seat_number="4D")

        # Female acquires temporary lock on 4C
        SeatLockService.lock_seats(
            trip=trip,
            seat_ids=[seat_4c.id],
            from_stop=stop_lhr,
            to_stop=stop_isb,
            seat_genders={str(seat_4c.id): Gender.FEMALE}
        )

        # Male trying to lock 4D immediately rejected
        with pytest.raises(BusinessLogicError, match="reserved for a female passenger"):
            SeatLockService.lock_seats(
                trip=trip,
                seat_ids=[seat_4d.id],
                from_stop=stop_lhr,
                to_stop=stop_isb,
                seat_genders={str(seat_4d.id): Gender.MALE}
            )

    def test_staff_can_override_or_relock_active_seat_lock(self, booking_network, test_users):
        trip = booking_network['trip']
        stop_lhr = booking_network['stops']['lhr']
        stop_isb = booking_network['stops']['isb']
        seat_2a = trip.bus.seats.get(seat_number="2A")

        # 1. Customer locks seat 2A
        SeatLockService.lock_seats(
            trip=trip,
            seat_ids=[seat_2a.id],
            from_stop=stop_lhr,
            to_stop=stop_isb,
            user=test_users['passenger']
        )

        # 2. Staff agent can override lock when locking or issuing walk-in ticket
        staff_lock = SeatLockService.lock_seats(
            trip=trip,
            seat_ids=[seat_2a.id],
            from_stop=stop_lhr,
            to_stop=stop_isb,
            user=test_users['counter_agent']
        )
        assert staff_lock['lock_token'] is not None

        # 3. Staff can confirm counter booking directly even if seat was locked
        counter_b = BookingService.confirm_counter_booking(
            trip=trip,
            from_stop=stop_lhr,
            to_stop=stop_isb,
            agent_user=test_users['counter_agent'],
            passenger_name="Walk-in Passenger",
            passenger_phone="+923008889999",
            passengers_data=[{'seat_id': seat_2a.id, 'passenger_name': 'Walk-in', 'gender': Gender.FEMALE}]
        )
        assert counter_b.status == BookingStatus.CONFIRMED

    def test_same_booking_male_next_to_female_allowed(self, booking_network):
        trip = booking_network['trip']
        stop_lhr = booking_network['stops']['lhr']
        stop_isb = booking_network['stops']['isb']

        seat_5a = trip.bus.seats.get(seat_number="5A")
        seat_5b = trip.bus.seats.get(seat_number="5B")

        lock_res = SeatLockService.lock_seats(
            trip=trip,
            seat_ids=[seat_5a.id, seat_5b.id],
            from_stop=stop_lhr,
            to_stop=stop_isb,
            seat_genders={str(seat_5a.id): Gender.FEMALE, str(seat_5b.id): Gender.MALE}
        )
        booking = BookingService.create_booking_from_locks(
            trip=trip,
            lock_token=lock_res['lock_token'],
            from_stop=stop_lhr,
            to_stop=stop_isb,
            passenger_name="Family Booking",
            passenger_phone="+923007775555",
            passengers_data=[
                {'seat_id': seat_5a.id, 'passenger_name': 'Fatima', 'gender': Gender.FEMALE},
                {'seat_id': seat_5b.id, 'passenger_name': 'Omar', 'gender': Gender.MALE},
            ]
        )
        assert booking.seats.count() == 2

    def test_non_overlapping_segment_allows_male_next_to_female(self, booking_network, test_users):
        trip = booking_network['trip']
        stop_lhr = booking_network['stops']['lhr']
        stop_bhr = booking_network['stops']['bhr']
        stop_isb = booking_network['stops']['isb']

        seat_6a = trip.bus.seats.get(seat_number="6A")
        seat_6b = trip.bus.seats.get(seat_number="6B")

        # Female rides LHR -> BHR on 6A
        BookingService.confirm_counter_booking(
            trip=trip,
            from_stop=stop_lhr,
            to_stop=stop_bhr,
            agent_user=test_users['counter_agent'],
            passenger_name="Hina",
            passenger_phone="+923007774444",
            passengers_data=[
                {'seat_id': seat_6a.id, 'passenger_name': 'Hina', 'gender': Gender.FEMALE}
            ]
        )

        # Male rides BHR -> ISB on 6B (non-overlapping segment!) -> allowed!
        lock_6b = SeatLockService.lock_seats(
            trip=trip,
            seat_ids=[seat_6b.id],
            from_stop=stop_bhr,
            to_stop=stop_isb,
            seat_genders={str(seat_6b.id): Gender.MALE}
        )
        assert lock_6b['lock_token'] is not None

