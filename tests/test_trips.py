"""
Comprehensive test suite for Module 4: Scheduled Trips, Fleet/Crew Overlap Prevention, Seat Blocking & Fares.
Tests:
  1. Trip Scheduling & Timing Constraints
  2. Fleet (Bus) Overlap Rejection
  3. Crew (Driver/Conductor) Overlap Rejection
  4. Per-Trip Seat Blocking & Invariant Validation
  5. Segment Fare Calculation Engine (Exact, General, Proportional Distance)
  6. API Endpoints & RBAC Enforcement
"""
import pytest
from datetime import timedelta
from decimal import Decimal
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from common.constants import UserRole, BusType, BusStatus, SeatType, TripStatus
from common.exceptions import BusinessLogicError
from accounts.models import User
from fleet.models import Bus, Driver, Conductor, CrewStatus
from fleet.services import generate_bus_seat_matrix
from routes.models import Terminal, Route, RouteStop
from routes.services import RouteService
from trips.models import Trip, TripSeatBlock, FareRule
from trips.services import TripScheduleService, TripSeatBlockService, FareService


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def operator_user(db):
    return User.objects.create_user(
        phone="+923009999004",
        role=UserRole.OPERATOR,
        password="SecureOperatorPass123!",
        is_verified=True,
        is_staff=True
    )


@pytest.fixture
def passenger_user(db):
    return User.objects.create_user(
        phone="+923008888004",
        role=UserRole.PASSENGER,
        password="SecurePassengerPass123!",
        is_verified=True
    )


@pytest.fixture
def operator_token(api_client, operator_user):
    login_url = reverse('auth:login')
    res = api_client.post(login_url, {
        'identifier': operator_user.phone,
        'password': 'SecureOperatorPass123!'
    })
    return res.data['data']['tokens']['access']


@pytest.fixture
def passenger_token(api_client, passenger_user):
    login_url = reverse('auth:login')
    res = api_client.post(login_url, {
        'identifier': passenger_user.phone,
        'password': 'SecurePassengerPass123!'
    })
    return res.data['data']['tokens']['access']


@pytest.fixture
def sample_fleet_and_route(db):
    # Terminals & Route
    t_lhr = Terminal.objects.create(name="Lahore Thokar", code="LHR-TRIP-T", city="Lahore")
    t_bhr = Terminal.objects.create(name="Bhera Rest Area", code="BHR-TRIP-T", city="Bhera")
    t_isb = Terminal.objects.create(name="Islamabad Faizabad", code="ISB-TRIP-T", city="Islamabad")

    route = RouteService.create_route(
        code="LHR-ISB-M4",
        name="Lahore to Islamabad M4",
        origin=t_lhr,
        destination=t_isb,
        stops_data=[
            {"terminal": t_lhr.id, "stop_order": 0, "distance_from_origin_km": 0.0, "offset_minutes": 0},
            {"terminal": t_bhr.id, "stop_order": 1, "distance_from_origin_km": 200.0, "offset_minutes": 130},
            {"terminal": t_isb.id, "stop_order": 2, "distance_from_origin_km": 380.0, "offset_minutes": 260},
        ]
    )

    # Bus & Seats
    bus = Bus.objects.create(
        registration_number="BUS-TRIP-01",
        bus_type=BusType.STANDARD_AC,
        status=BusStatus.ACTIVE
    )
    generate_bus_seat_matrix(bus=bus, layout_type="2x2", rows=10, ladies_rows=2)

    # Driver & Conductor
    driver = Driver.objects.create(
        full_name="Akram Khan",
        license_number="LIC-TRIP-01",
        phone="+923019999011",
        status=CrewStatus.AVAILABLE
    )
    conductor = Conductor.objects.create(
        full_name="Naveed Iqbal",
        employee_id="CND-TRIP-01",
        phone="+923019999012",
        status=CrewStatus.AVAILABLE
    )

    return {
        'route': route,
        'stops': list(route.stops.order_by('stop_order')),
        'bus': bus,
        'driver': driver,
        'conductor': conductor,
    }


# =====================================================================
# 1. Trip Scheduling & Overlap Prevention Tests
# =====================================================================

@pytest.mark.django_db
class TestTripSchedulingAndOverlap:
    def test_schedule_trip_success(self, sample_fleet_and_route):
        data = sample_fleet_and_route
        now = timezone.now() + timedelta(days=1)
        dep = now.replace(hour=10, minute=0, second=0, microsecond=0)
        arr = dep + timedelta(hours=4, minutes=20)

        trip = TripScheduleService.create_trip(
            route=data['route'],
            bus=data['bus'],
            driver=data['driver'],
            conductor=data['conductor'],
            departure_datetime=dep,
            arrival_datetime=arr,
            base_fare=Decimal('2200.00'),
            trip_number="TRIP-TEST-001"
        )
        assert trip.trip_number == "TRIP-TEST-001"
        assert trip.status == TripStatus.SCHEDULED
        assert trip.base_fare == Decimal('2200.00')

    def test_arrival_before_departure_rejected(self, sample_fleet_and_route):
        data = sample_fleet_and_route
        now = timezone.now() + timedelta(days=1)
        dep = now.replace(hour=14, minute=0, second=0, microsecond=0)
        arr = now.replace(hour=10, minute=0, second=0, microsecond=0)

        with pytest.raises(BusinessLogicError) as exc:
            TripScheduleService.create_trip(
                route=data['route'],
                bus=data['bus'],
                departure_datetime=dep,
                arrival_datetime=arr,
                base_fare=Decimal('2000.00')
            )
        assert "after departure" in str(exc.value)

    def test_bus_double_booking_overlap_rejected(self, sample_fleet_and_route):
        """Scheduling the same bus on an overlapping window must be strictly rejected."""
        data = sample_fleet_and_route
        now = timezone.now() + timedelta(days=2)
        dep1 = now.replace(hour=8, minute=0)
        arr1 = now.replace(hour=13, minute=0)

        TripScheduleService.create_trip(
            route=data['route'],
            bus=data['bus'],
            departure_datetime=dep1,
            arrival_datetime=arr1,
            base_fare=Decimal('2000.00'),
            trip_number="TRIP-FIRST-01"
        )

        # Overlapping Trip: Departs at 11:00 (during trip 1) and arrives at 16:00
        dep2 = now.replace(hour=11, minute=0)
        arr2 = now.replace(hour=16, minute=0)

        with pytest.raises(BusinessLogicError) as exc:
            TripScheduleService.create_trip(
                route=data['route'],
                bus=data['bus'],
                departure_datetime=dep2,
                arrival_datetime=arr2,
                base_fare=Decimal('2000.00'),
                trip_number="TRIP-OVERLAP-02"
            )
        assert "already assigned to active trip" in str(exc.value)

    def test_driver_double_booking_overlap_rejected(self, sample_fleet_and_route):
        """A driver cannot be scheduled on overlapping journeys even on different buses."""
        data = sample_fleet_and_route
        now = timezone.now() + timedelta(days=3)
        dep1 = now.replace(hour=9, minute=0)
        arr1 = now.replace(hour=14, minute=0)

        TripScheduleService.create_trip(
            route=data['route'],
            bus=data['bus'],
            driver=data['driver'],
            departure_datetime=dep1,
            arrival_datetime=arr1,
            base_fare=Decimal('2000.00'),
            trip_number="TRIP-DRV-01"
        )

        # Second bus
        bus2 = Bus.objects.create(registration_number="BUS-ALT-02", bus_type=BusType.STANDARD_AC)

        # Overlapping time for same driver on bus2
        dep2 = now.replace(hour=12, minute=0)
        arr2 = now.replace(hour=17, minute=0)

        with pytest.raises(BusinessLogicError) as exc:
            TripScheduleService.create_trip(
                route=data['route'],
                bus=bus2,
                driver=data['driver'],
                departure_datetime=dep2,
                arrival_datetime=arr2,
                base_fare=Decimal('2000.00'),
                trip_number="TRIP-DRV-02"
            )
        assert "Driver 'Akram Khan' is already scheduled" in str(exc.value)

    def test_non_overlapping_trips_allowed(self, sample_fleet_and_route):
        """Same bus can be scheduled sequentially once the previous trip finishes."""
        data = sample_fleet_and_route
        now = timezone.now() + timedelta(days=4)
        dep1 = now.replace(hour=8, minute=0)
        arr1 = now.replace(hour=12, minute=0)
        t1 = TripScheduleService.create_trip(
            route=data['route'],
            bus=data['bus'],
            departure_datetime=dep1,
            arrival_datetime=arr1,
            base_fare=Decimal('2000.00')
        )

        dep2 = now.replace(hour=13, minute=0)
        arr2 = now.replace(hour=17, minute=0)
        t2 = TripScheduleService.create_trip(
            route=data['route'],
            bus=data['bus'],
            departure_datetime=dep2,
            arrival_datetime=arr2,
            base_fare=Decimal('2000.00')
        )
        assert t1.id != t2.id

    def test_cancelled_trip_releases_resources(self, sample_fleet_and_route):
        """A cancelled trip must not block the vehicle from being reassigned."""
        data = sample_fleet_and_route
        now = timezone.now() + timedelta(days=5)
        dep = now.replace(hour=10, minute=0)
        arr = now.replace(hour=15, minute=0)

        t1 = TripScheduleService.create_trip(
            route=data['route'],
            bus=data['bus'],
            departure_datetime=dep,
            arrival_datetime=arr,
            base_fare=Decimal('2000.00'),
            trip_number="TRIP-CANCEL-01"
        )
        TripScheduleService.cancel_trip(t1)
        assert t1.status == TripStatus.CANCELLED

        # Now re-schedule on the exact same time window
        t2 = TripScheduleService.create_trip(
            route=data['route'],
            bus=data['bus'],
            departure_datetime=dep,
            arrival_datetime=arr,
            base_fare=Decimal('2000.00'),
            trip_number="TRIP-REUSE-02"
        )
        assert t2.status == TripStatus.SCHEDULED


# =====================================================================
# 2. Per-Trip Seat Blocking Tests
# =====================================================================

@pytest.mark.django_db
class TestTripSeatBlocking:
    def test_block_seat_for_trip(self, sample_fleet_and_route, operator_user):
        data = sample_fleet_and_route
        now = timezone.now() + timedelta(days=6)
        trip = TripScheduleService.create_trip(
            route=data['route'],
            bus=data['bus'],
            departure_datetime=now,
            arrival_datetime=now + timedelta(hours=4),
            base_fare=Decimal('2000.00')
        )

        seat_1a = data['bus'].seats.get(seat_number="1A")
        block = TripSeatBlockService.block_seat(
            trip=trip,
            seat=seat_1a,
            reason="VIP Protocol Hold",
            user=operator_user
        )
        assert block.seat == seat_1a
        assert block.reason == "VIP Protocol Hold"

        blocked_ids = TripSeatBlockService.get_blocked_seat_ids(trip)
        assert str(seat_1a.id) in blocked_ids

    def test_double_blocking_same_seat_rejected(self, sample_fleet_and_route):
        data = sample_fleet_and_route
        now = timezone.now() + timedelta(days=7)
        trip = TripScheduleService.create_trip(
            route=data['route'],
            bus=data['bus'],
            departure_datetime=now,
            arrival_datetime=now + timedelta(hours=4),
            base_fare=Decimal('2000.00')
        )
        seat_1a = data['bus'].seats.get(seat_number="1A")
        TripSeatBlockService.block_seat(trip=trip, seat=seat_1a)

        with pytest.raises(BusinessLogicError) as exc:
            TripSeatBlockService.block_seat(trip=trip, seat=seat_1a)
        assert "already blocked" in str(exc.value)

    def test_seat_from_wrong_bus_rejected(self, sample_fleet_and_route):
        data = sample_fleet_and_route
        now = timezone.now() + timedelta(days=8)
        trip = TripScheduleService.create_trip(
            route=data['route'],
            bus=data['bus'],
            departure_datetime=now,
            arrival_datetime=now + timedelta(hours=4),
            base_fare=Decimal('2000.00')
        )

        other_bus = Bus.objects.create(registration_number="OTHER-BUS-99", bus_type=BusType.STANDARD_AC)
        generate_bus_seat_matrix(bus=other_bus, layout_type="2x2", rows=5)
        wrong_seat = other_bus.seats.first()

        with pytest.raises(BusinessLogicError) as exc:
            TripSeatBlockService.block_seat(trip=trip, seat=wrong_seat)
        assert "belongs to bus" in str(exc.value)

    def test_unblock_seat_success(self, sample_fleet_and_route):
        data = sample_fleet_and_route
        now = timezone.now() + timedelta(days=9)
        trip = TripScheduleService.create_trip(
            route=data['route'],
            bus=data['bus'],
            departure_datetime=now,
            arrival_datetime=now + timedelta(hours=4),
            base_fare=Decimal('2000.00')
        )
        seat_1b = data['bus'].seats.get(seat_number="1B")
        TripSeatBlockService.block_seat(trip=trip, seat=seat_1b)
        assert str(seat_1b.id) in TripSeatBlockService.get_blocked_seat_ids(trip)

        TripSeatBlockService.unblock_seat(trip, seat_1b)
        assert str(seat_1b.id) not in TripSeatBlockService.get_blocked_seat_ids(trip)


# =====================================================================
# 3. Segment Fare Pricing Engine Tests
# =====================================================================

@pytest.mark.django_db
class TestFareCalculationEngine:
    def test_exact_fare_rule_match(self, sample_fleet_and_route):
        data = sample_fleet_and_route
        route = data['route']
        stops = data['stops']
        s0, s1, s2 = stops[0], stops[1], stops[2]  # LHR, BHR, ISB

        # Configure exact rule for LHR -> BHR (Window seat)
        FareService.set_fare_rule(
            route=route,
            from_stop=s0,
            to_stop=s1,
            seat_type=SeatType.WINDOW,
            fare_amount=Decimal('1250.00')
        )

        now = timezone.now() + timedelta(days=10)
        trip = TripScheduleService.create_trip(
            route=route,
            bus=data['bus'],
            departure_datetime=now,
            arrival_datetime=now + timedelta(hours=4),
            base_fare=Decimal('2200.00')
        )

        fare = FareService.calculate_fare(trip, s0, s1, seat_type=SeatType.WINDOW)
        assert fare == Decimal('1250.00')

    def test_default_segment_rule_fallback(self, sample_fleet_and_route):
        data = sample_fleet_and_route
        route = data['route']
        s0, s1 = data['stops'][0], data['stops'][1]

        # General rule without seat_type (all classes)
        FareService.set_fare_rule(
            route=route,
            from_stop=s0,
            to_stop=s1,
            seat_type=None,
            fare_amount=Decimal('1100.00')
        )

        now = timezone.now() + timedelta(days=11)
        trip = TripScheduleService.create_trip(
            route=route,
            bus=data['bus'],
            departure_datetime=now,
            arrival_datetime=now + timedelta(hours=4),
            base_fare=Decimal('2200.00')
        )

        # Querying an AISLE seat should hit the default rule of 1100.00
        fare = FareService.calculate_fare(trip, s0, s1, seat_type=SeatType.AISLE)
        assert fare == Decimal('1100.00')

    def test_proportional_distance_fallback(self, sample_fleet_and_route):
        """When no explicit FareRule exists, fare is calculated by distance ratio."""
        data = sample_fleet_and_route
        route = data['route']
        s0, s1, s2 = data['stops'][0], data['stops'][1], data['stops'][2]

        # Route: 0km -> 200km -> 380km. Total = 380km. Base fare = 3800.00 PKR
        now = timezone.now() + timedelta(days=12)
        trip = TripScheduleService.create_trip(
            route=route,
            bus=data['bus'],
            departure_datetime=now,
            arrival_datetime=now + timedelta(hours=4),
            base_fare=Decimal('3800.00')
        )

        # Segment BHR -> ISB: dist = 380 - 200 = 180 km.
        # Ratio = 180 / 380 = 0.473684...
        # Proportional fare = 3800 * (180 / 380) = 1800.00 PKR
        fare = FareService.calculate_fare(trip, s1, s2)
        assert fare == Decimal('1800.00')

    def test_reverse_stops_rejected(self, sample_fleet_and_route):
        data = sample_fleet_and_route
        route = data['route']
        s0, s2 = data['stops'][0], data['stops'][2]

        now = timezone.now() + timedelta(days=13)
        trip = TripScheduleService.create_trip(
            route=route,
            bus=data['bus'],
            departure_datetime=now,
            arrival_datetime=now + timedelta(hours=4),
            base_fare=Decimal('2000.00')
        )

        with pytest.raises(BusinessLogicError) as exc:
            FareService.calculate_fare(trip, s2, s0)
        assert "before destination stop" in str(exc.value)


# =====================================================================
# 4. Trip REST API & RBAC Tests
# =====================================================================

@pytest.mark.django_db
class TestTripAPI:
    def test_operator_create_trip_api(self, api_client, operator_token, sample_fleet_and_route):
        data = sample_fleet_and_route
        url = reverse('trip-list')
        now = timezone.now() + timedelta(days=14)
        dep = (now + timedelta(hours=1)).isoformat()
        arr = (now + timedelta(hours=5)).isoformat()

        payload = {
            "route": str(data['route'].id),
            "bus": str(data['bus'].id),
            "driver": str(data['driver'].id),
            "conductor": str(data['conductor'].id),
            "departure_datetime": dep,
            "arrival_datetime": arr,
            "base_fare": "2400.00",
            "trip_number": "TRIP-API-01"
        }
        res = api_client.post(url, payload, format='json', HTTP_AUTHORIZATION=f"Bearer {operator_token}")
        assert res.status_code == status.HTTP_201_CREATED
        assert res.data['data']['trip_number'] == "TRIP-API-01"

    def test_passenger_cannot_create_trip(self, api_client, passenger_token, sample_fleet_and_route):
        data = sample_fleet_and_route
        url = reverse('trip-list')
        now = timezone.now() + timedelta(days=15)
        payload = {
            "route": str(data['route'].id),
            "bus": str(data['bus'].id),
            "departure_datetime": now.isoformat(),
            "arrival_datetime": (now + timedelta(hours=4)).isoformat(),
            "base_fare": "2000.00"
        }
        res = api_client.post(url, payload, format='json', HTTP_AUTHORIZATION=f"Bearer {passenger_token}")
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_operator_block_and_unblock_seat_api(self, api_client, operator_token, sample_fleet_and_route):
        data = sample_fleet_and_route
        now = timezone.now() + timedelta(days=16)
        trip = TripScheduleService.create_trip(
            route=data['route'],
            bus=data['bus'],
            departure_datetime=now,
            arrival_datetime=now + timedelta(hours=4),
            base_fare=Decimal('2000.00')
        )
        seat = data['bus'].seats.get(seat_number="2A")

        # Block Seat API
        block_url = reverse('trip-block-seat', kwargs={'pk': str(trip.id)})
        block_res = api_client.post(
            block_url,
            {"seat_id": str(seat.id), "reason": "VIP Booking Hold"},
            format='json',
            HTTP_AUTHORIZATION=f"Bearer {operator_token}"
        )
        assert block_res.status_code == status.HTTP_201_CREATED
        assert block_res.data['data']['seat_number'] == "2A"

        # Unblock Seat API
        unblock_url = reverse('trip-unblock-seat', kwargs={'pk': str(trip.id), 'seat_id': str(seat.id)})
        unblock_res = api_client.delete(unblock_url, HTTP_AUTHORIZATION=f"Bearer {operator_token}")
        assert unblock_res.status_code == status.HTTP_200_OK

    def test_public_calculate_fare_api(self, api_client, sample_fleet_and_route):
        data = sample_fleet_and_route
        now = timezone.now() + timedelta(days=17)
        trip = TripScheduleService.create_trip(
            route=data['route'],
            bus=data['bus'],
            departure_datetime=now,
            arrival_datetime=now + timedelta(hours=4),
            base_fare=Decimal('3800.00')
        )
        s0 = data['stops'][0]
        s1 = data['stops'][1]

        url = reverse('trip-calculate-fare', kwargs={'pk': str(trip.id)})
        res = api_client.get(url, {'from_stop': str(s0.id), 'to_stop': str(s1.id)})
        assert res.status_code == status.HTTP_200_OK
        assert 'calculated_fare' in res.data['data']
        assert res.data['data']['currency'] == "PKR"

    def test_auto_calculate_arrival_from_route_duration(self, api_client, sample_fleet_and_route, operator_token):
        data = sample_fleet_and_route
        route = data['route']
        route.estimated_duration_minutes = 260
        route.save(update_fields=['estimated_duration_minutes'])

        dep = timezone.now() + timedelta(days=18)
        url = reverse('trip-list')
        res = api_client.post(
            url,
            {
                "route": str(route.id),
                "bus": str(data['bus'].id),
                "departure_datetime": dep.isoformat(),
                "base_fare": "2500.00",
            },
            format='json',
            HTTP_AUTHORIZATION=f"Bearer {operator_token}",
        )
        assert res.status_code == status.HTTP_201_CREATED
        trip = Trip.objects.get(id=res.data['data']['id'])
        assert trip.arrival_datetime == trip.departure_datetime + timedelta(minutes=260)
