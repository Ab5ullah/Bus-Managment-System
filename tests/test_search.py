"""
Comprehensive test suite for Module 5: Search & Segment-Aware Availability Engine.
Tests:
  1. Corridor discovery (full route & intermediate stop traversal)
  2. Station departure / arrival time offsets calculation
  3. Reverse corridor rejection (forward directional flow enforcement)
  4. Query filtering: travel date, time-of-day window, bus type, price range
  5. Sorting options: price, departure, duration
  6. Segment-aware Seat Map availability (AVAILABLE, BLOCKED, ladies-only, fares)
  7. Popular routes & cities discovery
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
from fleet.models import Bus
from fleet.services import generate_bus_seat_matrix
from routes.models import Terminal, Route, RouteStop
from routes.services import RouteService
from trips.models import Trip, TripSeatBlock, FareRule
from trips.services import TripScheduleService, TripSeatBlockService, FareService
from search.services import TripSearchService, SegmentAvailabilityService


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def search_network(db):
    """
    Sets up a 4-stop corridor:
      Stop 1: Lahore (km 0, offset 0 min)
      Stop 2: Bhera (km 190, offset 120 min)
      Stop 3: Rawalpindi (km 350, offset 230 min)
      Stop 4: Islamabad (km 375, offset 260 min)
    """
    term_lhr = Terminal.objects.create(name="Lahore Thokar", code="LHR-01", city="Lahore", is_active=True)
    term_bhr = Terminal.objects.create(name="Bhera Service Area", code="BHR-01", city="Bhera", is_active=True)
    term_rwp = Terminal.objects.create(name="Rawalpindi Faizabad", code="RWP-01", city="Rawalpindi", is_active=True)
    term_isb = Terminal.objects.create(name="Islamabad Motorway", code="ISB-01", city="Islamabad", is_active=True)
    term_khi = Terminal.objects.create(name="Karachi Cantt", code="KHI-01", city="Karachi", is_active=True)

    route_lhr_isb = RouteService.create_route(
        code="LHR-ISB-M2",
        name="Lahore to Islamabad Express",
        origin=term_lhr,
        destination=term_isb,
        total_distance_km=Decimal('375.00'),
        estimated_duration_minutes=260,
        stops_data=[
            {'terminal': term_lhr, 'stop_order': 0, 'distance_from_origin_km': Decimal('0.00'), 'offset_minutes': 0},
            {'terminal': term_bhr, 'stop_order': 1, 'distance_from_origin_km': Decimal('190.00'), 'offset_minutes': 120},
            {'terminal': term_rwp, 'stop_order': 2, 'distance_from_origin_km': Decimal('350.00'), 'offset_minutes': 230},
            {'terminal': term_isb, 'stop_order': 3, 'distance_from_origin_km': Decimal('375.00'), 'offset_minutes': 260},
        ]
    )

    # Bus 1: Executive 40 seats (departure 08:00 AM tomorrow)
    bus_exec = Bus.objects.create(
        registration_number="LES-501",
        bus_type=BusType.EXECUTIVE,
        status=BusStatus.ACTIVE,
        total_seats=40
    )
    generate_bus_seat_matrix(bus_exec, layout_type='2x2', rows=10, ladies_rows=[1, 2])

    # Bus 2: Luxury 30 seats (departure 14:00 PM tomorrow)
    bus_lux = Bus.objects.create(
        registration_number="LES-502",
        bus_type=BusType.LUXURY,
        status=BusStatus.ACTIVE,
        total_seats=30
    )
    generate_bus_seat_matrix(bus_lux, layout_type='2x1', rows=10)

    now = timezone.now().replace(microsecond=0)
    # Trip 1: Morning departure (08:00 UTC)
    dep1 = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0)
    arr1 = dep1 + timedelta(minutes=260)
    trip_exec = Trip.objects.create(
        trip_number="TRIP-SRCH-001",
        route=route_lhr_isb,
        bus=bus_exec,
        departure_datetime=dep1,
        arrival_datetime=arr1,
        status=TripStatus.SCHEDULED,
        base_fare=Decimal("2800.00")
    )

    # Trip 2: Afternoon departure (14:00 UTC)
    dep2 = (now + timedelta(days=1)).replace(hour=14, minute=0, second=0)
    arr2 = dep2 + timedelta(minutes=260)
    trip_lux = Trip.objects.create(
        trip_number="TRIP-SRCH-002",
        route=route_lhr_isb,
        bus=bus_lux,
        departure_datetime=dep2,
        arrival_datetime=arr2,
        status=TripStatus.SCHEDULED,
        base_fare=Decimal("3500.00")
    )

    # Custom FareRule for intermediate stop: Bhera -> Islamabad
    stop_bhr = route_lhr_isb.stops.get(stop_order=1)
    stop_isb = route_lhr_isb.stops.get(stop_order=3)
    FareService.set_fare_rule(
        route=route_lhr_isb,
        from_stop=stop_bhr,
        to_stop=stop_isb,
        fare_amount=Decimal("1600.00")
    )

    return {
        'terminals': {
            'lhr': term_lhr,
            'bhr': term_bhr,
            'rwp': term_rwp,
            'isb': term_isb,
            'khi': term_khi,
        },
        'route': route_lhr_isb,
        'buses': {'exec': bus_exec, 'lux': bus_lux},
        'trips': {'exec': trip_exec, 'lux': trip_lux},
        'stops': {
            'lhr': route_lhr_isb.stops.get(stop_order=0),
            'bhr': stop_bhr,
            'rwp': route_lhr_isb.stops.get(stop_order=2),
            'isb': stop_isb,
        }
    }


@pytest.mark.django_db
class TestTripSearchService:

    def test_search_full_route_by_city(self, search_network):
        """Searching 'Lahore' to 'Islamabad' finds both scheduled trips."""
        results = TripSearchService.search_trips(
            origin_query="Lahore",
            destination_query="Islamabad"
        )
        assert len(results) == 2
        trip_numbers = [r['trip_number'] for r in results]
        assert "TRIP-SRCH-001" in trip_numbers
        assert "TRIP-SRCH-002" in trip_numbers

    def test_search_intermediate_hop(self, search_network):
        """
        Passenger searches from intermediate station Bhera to Islamabad.
        Trip 1 departs Bhera at departure_datetime + 120 minutes offset.
        Fare should match custom FareRule (1600.00).
        """
        results = TripSearchService.search_trips(
            origin_query="Bhera",
            destination_query="Islamabad"
        )
        assert len(results) == 2

        # Check segment calculation
        res = next(r for r in results if r['trip_number'] == "TRIP-SRCH-001")
        assert res['origin']['city'] == "Bhera"
        assert res['origin']['stop_order'] == 1
        assert res['destination']['city'] == "Islamabad"
        assert res['destination']['stop_order'] == 3

        # Segment distance = 375 - 190 = 185 km
        assert res['segment_details']['distance_km'] == "185.00"
        # Segment duration = 260 - 120 = 140 min
        assert res['segment_details']['duration_minutes'] == 140

        # Fare matches Bhera->Islamabad FareRule
        assert Decimal(res['fare']['amount']) == Decimal("1600.00")

    def test_search_reverse_direction_returns_empty(self, search_network):
        """
        Route goes Lahore -> Islamabad.
        Searching Islamabad to Lahore must return 0 trips because of forward directional flow.
        """
        results = TripSearchService.search_trips(
            origin_query="Islamabad",
            destination_query="Lahore"
        )
        assert len(results) == 0

    def test_search_unconnected_city_returns_empty(self, search_network):
        """Karachi is not on this route, searching Lahore to Karachi yields 0 trips."""
        results = TripSearchService.search_trips(
            origin_query="Lahore",
            destination_query="Karachi"
        )
        assert len(results) == 0

    def test_search_invalid_station_raises_error(self, search_network):
        """Searching an unknown city name raises BusinessLogicError."""
        with pytest.raises(BusinessLogicError) as exc_info:
            TripSearchService.search_trips(
                origin_query="NonExistentCity",
                destination_query="Islamabad"
            )
        assert "No active stations found" in str(exc_info.value)

    def test_filter_by_travel_date(self, search_network):
        """Searching with exact departure date returns trips; different date returns 0."""
        trip1 = search_network['trips']['exec']
        travel_date = trip1.departure_datetime.date()

        results = TripSearchService.search_trips(
            origin_query="Lahore",
            destination_query="Islamabad",
            travel_date=travel_date
        )
        assert len(results) == 2

        # Another date
        empty_results = TripSearchService.search_trips(
            origin_query="Lahore",
            destination_query="Islamabad",
            travel_date=travel_date + timedelta(days=5)
        )
        assert len(empty_results) == 0

    def test_filter_by_bus_type(self, search_network):
        """Filtering by LUXURY returns only the Luxury trip."""
        results = TripSearchService.search_trips(
            origin_query="Lahore",
            destination_query="Islamabad",
            bus_type=BusType.LUXURY
        )
        assert len(results) == 1
        assert results[0]['trip_number'] == "TRIP-SRCH-002"
        assert results[0]['bus']['bus_type'] == BusType.LUXURY

    def test_filter_by_departure_window(self, search_network):
        """
        Trip 1 is 08:00 (morning).
        Trip 2 is 14:00 (afternoon).
        """
        morning_res = TripSearchService.search_trips(
            origin_query="Lahore",
            destination_query="Islamabad",
            departure_window="morning"
        )
        assert len(morning_res) == 1
        assert morning_res[0]['trip_number'] == "TRIP-SRCH-001"

        afternoon_res = TripSearchService.search_trips(
            origin_query="Lahore",
            destination_query="Islamabad",
            departure_window="afternoon"
        )
        assert len(afternoon_res) == 1
        assert afternoon_res[0]['trip_number'] == "TRIP-SRCH-002"

        night_res = TripSearchService.search_trips(
            origin_query="Lahore",
            destination_query="Islamabad",
            departure_window="night"
        )
        assert len(night_res) == 0

    def test_filter_by_price_range(self, search_network):
        """Filtering by price boundaries."""
        results = TripSearchService.search_trips(
            origin_query="Lahore",
            destination_query="Islamabad",
            min_price=Decimal("3000.00"),
            max_price=Decimal("4500.00")
        )
        assert len(results) == 1
        assert results[0]['trip_number'] == "TRIP-SRCH-002"
        assert Decimal(results[0]['fare']['amount']) == Decimal("4025.00")

    def test_sorting_results(self, search_network):
        """Tests price_desc and price_asc sorting."""
        asc_res = TripSearchService.search_trips(
            origin_query="Lahore",
            destination_query="Islamabad",
            sort_by="price_asc"
        )
        assert Decimal(asc_res[0]['fare']['amount']) <= Decimal(asc_res[1]['fare']['amount'])

        desc_res = TripSearchService.search_trips(
            origin_query="Lahore",
            destination_query="Islamabad",
            sort_by="price_desc"
        )
        assert Decimal(desc_res[0]['fare']['amount']) >= Decimal(desc_res[1]['fare']['amount'])


@pytest.mark.django_db
class TestSegmentAvailabilityService:

    def test_seat_status_map_with_blocks(self, search_network):
        """
        Tests seat status evaluation:
        - 40 total seats
        - 2 seats blocked administratively via TripSeatBlock
        - Result: 38 AVAILABLE, 2 BLOCKED
        """
        trip = search_network['trips']['exec']
        from_stop = search_network['stops']['lhr']
        to_stop = search_network['stops']['isb']

        # Block seats 1A and 1B
        seat_1a = trip.bus.seats.get(seat_number="1A")
        seat_1b = trip.bus.seats.get(seat_number="1B")
        TripSeatBlockService.block_seat(trip=trip, seat=seat_1a, reason="VIP Reservation")
        TripSeatBlockService.block_seat(trip=trip, seat=seat_1b, reason="Protocol")

        seat_map = SegmentAvailabilityService.get_trip_seat_status_map(
            trip=trip,
            from_stop=from_stop,
            to_stop=to_stop
        )

        assert seat_map['summary']['total'] == 40
        assert seat_map['summary']['available'] == 38
        assert seat_map['summary']['blocked'] == 2

        # Verify ladies-only tagging (rows 1 and 2 = 8 seats in 2x2)
        assert seat_map['summary']['ladies_only'] == 8

        # Find 1A
        s1a_info = next(s for s in seat_map['seats'] if s['seat_number'] == "1A")
        assert s1a_info['status'] == "BLOCKED"

        # Find 3A
        s3a_info = next(s for s in seat_map['seats'] if s['seat_number'] == "3A")
        assert s3a_info['status'] == "AVAILABLE"
        assert Decimal(s3a_info['fare']) == Decimal("2800.00")

    def test_reverse_stops_in_seat_map_raises_error(self, search_network):
        """Requesting seat map with reverse stops (to_stop before from_stop) is rejected."""
        trip = search_network['trips']['exec']
        from_stop = search_network['stops']['isb']
        to_stop = search_network['stops']['lhr']

        with pytest.raises(BusinessLogicError) as exc_info:
            SegmentAvailabilityService.get_trip_seat_status_map(
                trip=trip,
                from_stop=from_stop,
                to_stop=to_stop
            )
        assert "Invalid travel direction" in str(exc_info.value)


@pytest.mark.django_db
class TestSearchAPIEndpoints:

    def test_search_api_endpoint_success(self, api_client, search_network):
        url = reverse('search:search-trips')
        res = api_client.get(url, {
            'origin': 'Lahore',
            'destination': 'Islamabad'
        })
        assert res.status_code == status.HTTP_200_OK
        assert res.data['success'] is True
        assert res.data['data']['total_results'] == 2
        assert len(res.data['data']['trips']) == 2

    def test_search_api_missing_destination(self, api_client):
        url = reverse('search:search-trips')
        res = api_client.get(url, {'origin': 'Lahore'})
        assert res.status_code == status.HTTP_400_BAD_REQUEST

    def test_seat_map_api_endpoint_success(self, api_client, search_network):
        trip = search_network['trips']['exec']
        from_stop = search_network['stops']['lhr']
        to_stop = search_network['stops']['rwp']

        url = reverse('search:trip-seat-map', kwargs={'trip_id': trip.id})
        res = api_client.get(url, {
            'from_stop': str(from_stop.id),
            'to_stop': str(to_stop.id)
        })
        assert res.status_code == status.HTTP_200_OK
        assert res.data['success'] is True
        assert res.data['data']['summary']['total'] == 40
        assert res.data['data']['segment']['from_stop']['city'] == "Lahore"
        assert res.data['data']['segment']['to_stop']['city'] == "Rawalpindi"

    def test_popular_routes_api_endpoint(self, api_client, search_network):
        url = reverse('search:popular-routes')
        res = api_client.get(url)
        assert res.status_code == status.HTTP_200_OK
        assert res.data['success'] is True
        assert "Lahore" in res.data['data']['cities']
        assert "Islamabad" in res.data['data']['cities']
        assert len(res.data['data']['popular_routes']) >= 1
