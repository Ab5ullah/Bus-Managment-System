"""
Comprehensive test suite for Module 3: Terminals, Sequenced Routes & Intermediate Stops.
Tests:
  1. Terminal CRUD, duplicate code rejections, and coordinate validations.
  2. Route creation with default stops and custom sequenced stops.
  3. Domain Invariants:
     - Origin != Destination
     - Stop 0 == Origin (dist 0, offset 0)
     - Last stop == Destination
     - Strictly increasing distance progression
     - Strictly increasing time progression
     - No circular / duplicate terminals in a single route
  4. Stop Sequence Service:
     - Segment resolution: N*(N-1)/2 directional pairs
     - Directional validity (forward allowed, backward rejected)
  5. API RBAC:
     - Public can browse catalog
     - Only Operator/Admin can mutate
"""
import pytest
from decimal import Decimal
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from common.constants import UserRole
from common.exceptions import BusinessLogicError
from accounts.models import User
from routes.models import Terminal, Route, RouteStop
from routes.services import TerminalService, RouteService, StopSequenceService


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def operator_user(db):
    return User.objects.create_user(
        phone="+923009999003",
        role=UserRole.OPERATOR,
        password="SecureOperatorPass123!",
        is_verified=True,
        is_staff=True
    )


@pytest.fixture
def passenger_user(db):
    return User.objects.create_user(
        phone="+923008888003",
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
def sample_terminals(db):
    t_lhr = Terminal.objects.create(name="Lahore Thokar Terminal", code="LHR-01", city="Lahore")
    t_bhr = Terminal.objects.create(name="Bhera Interchange Rest Area", code="BHR-01", city="Bhera")
    t_isb = Terminal.objects.create(name="Islamabad Faizabad Terminal", code="ISB-01", city="Islamabad")
    t_fsd = Terminal.objects.create(name="Faisalabad Kohistan Terminal", code="FSD-01", city="Faisalabad")
    return {
        'lhr': t_lhr,
        'bhr': t_bhr,
        'isb': t_isb,
        'fsd': t_fsd,
    }


# =====================================================================
# 1. Terminal Domain & API Tests
# =====================================================================

@pytest.mark.django_db
class TestTerminalManagement:
    def test_create_terminal_service(self):
        terminal = TerminalService.create_terminal(
            name="Rawalpindi Pirwadhai",
            code="rwp-01",
            city="Rawalpindi",
            latitude=Decimal('33.600000'),
            longitude=Decimal('73.066667')
        )
        assert terminal.code == "RWP-01"
        assert terminal.city == "Rawalpindi"
        assert terminal.latitude == Decimal('33.600000')

    def test_duplicate_terminal_code_rejected(self, sample_terminals):
        with pytest.raises(BusinessLogicError) as exc_info:
            TerminalService.create_terminal(
                name="Duplicate Lahore",
                code="LHR-01",
                city="Lahore"
            )
        assert "already exists" in str(exc_info.value)

    def test_invalid_coordinates_rejected(self):
        with pytest.raises(BusinessLogicError):
            TerminalService.create_terminal(
                name="Bad Lat",
                code="BAD-LAT",
                city="Test",
                latitude=Decimal('105.00')
            )

        with pytest.raises(BusinessLogicError):
            TerminalService.create_terminal(
                name="Bad Lng",
                code="BAD-LNG",
                city="Test",
                longitude=Decimal('-195.00')
            )

    def test_public_can_list_terminals(self, api_client, sample_terminals):
        url = reverse('terminal-list')
        res = api_client.get(url)
        assert res.status_code == status.HTTP_200_OK
        data = res.data['data'] if 'data' in res.data else res.data['results']
        assert len(data) >= 4

    def test_operator_can_create_terminal_via_api(self, api_client, operator_token):
        url = reverse('terminal-list')
        payload = {
            "name": "Peshawar Haji Camp Terminal",
            "code": "PEW-01",
            "city": "Peshawar",
            "address": "GT Road, Haji Camp",
            "contact_number": "+92911234567"
        }
        res = api_client.post(url, payload, HTTP_AUTHORIZATION=f"Bearer {operator_token}")
        assert res.status_code == status.HTTP_201_CREATED
        assert res.data['data']['code'] == "PEW-01"

    def test_passenger_cannot_create_terminal(self, api_client, passenger_token):
        url = reverse('terminal-list')
        payload = {
            "name": "Unauthorized Terminal",
            "code": "UNAUTH-01",
            "city": "Nowhere"
        }
        res = api_client.post(url, payload, HTTP_AUTHORIZATION=f"Bearer {passenger_token}")
        assert res.status_code == status.HTTP_403_FORBIDDEN


# =====================================================================
# 2. Route Creation & Invariant Validation Tests
# =====================================================================

@pytest.mark.django_db
class TestRouteCreationAndInvariants:
    def test_create_direct_route_default_stops(self, sample_terminals):
        """Creating a route without explicit stops should auto-create origin (0) and destination (1) stops."""
        route = RouteService.create_route(
            code="LHR-ISB-DIR",
            name="Lahore to Islamabad Direct",
            origin=sample_terminals['lhr'],
            destination=sample_terminals['isb'],
            total_distance_km=Decimal('375.00'),
            estimated_duration_minutes=270
        )
        assert route.stops.count() == 2
        stops = list(route.stops.order_by('stop_order'))
        assert stops[0].stop_order == 0
        assert stops[0].terminal == sample_terminals['lhr']
        assert stops[0].distance_from_origin_km == Decimal('0.00')
        assert stops[0].offset_minutes == 0

        assert stops[1].stop_order == 1
        assert stops[1].terminal == sample_terminals['isb']
        assert stops[1].distance_from_origin_km == Decimal('375.00')
        assert stops[1].offset_minutes == 270

    def test_create_route_with_intermediate_stops(self, sample_terminals):
        """Create route with Lahore (0km, 0m) -> Bhera (210km, 140m) -> Islamabad (380km, 260m)."""
        stops_data = [
            {"terminal": sample_terminals['lhr'].id, "stop_order": 0, "distance_from_origin_km": 0.0, "offset_minutes": 0},
            {"terminal": sample_terminals['bhr'].id, "stop_order": 1, "distance_from_origin_km": 210.0, "offset_minutes": 140},
            {"terminal": sample_terminals['isb'].id, "stop_order": 2, "distance_from_origin_km": 380.0, "offset_minutes": 260},
        ]
        route = RouteService.create_route(
            code="LHR-ISB-M2",
            name="Lahore to Islamabad via M2 Motorway",
            origin=sample_terminals['lhr'],
            destination=sample_terminals['isb'],
            stops_data=stops_data
        )
        assert route.stops.count() == 3
        assert route.total_distance_km == Decimal('380.00')
        assert route.estimated_duration_minutes == 260

    def test_origin_cannot_equal_destination(self, sample_terminals):
        with pytest.raises(BusinessLogicError) as exc:
            RouteService.create_route(
                code="CIRCULAR-01",
                name="Circular Route",
                origin=sample_terminals['lhr'],
                destination=sample_terminals['lhr']
            )
        assert "cannot be the same terminal" in str(exc.value)

    def test_stop_zero_must_be_origin(self, sample_terminals):
        stops_data = [
            {"terminal": sample_terminals['bhr'].id, "stop_order": 0, "distance_from_origin_km": 0.0, "offset_minutes": 0},
            {"terminal": sample_terminals['isb'].id, "stop_order": 1, "distance_from_origin_km": 380.0, "offset_minutes": 260},
        ]
        with pytest.raises(BusinessLogicError) as exc:
            RouteService.create_route(
                code="BAD-ORIGIN",
                name="Bad Origin Stop",
                origin=sample_terminals['lhr'],
                destination=sample_terminals['isb'],
                stops_data=stops_data
            )
        assert "must be the route origin terminal" in str(exc.value)

    def test_last_stop_must_be_destination(self, sample_terminals):
        stops_data = [
            {"terminal": sample_terminals['lhr'].id, "stop_order": 0, "distance_from_origin_km": 0.0, "offset_minutes": 0},
            {"terminal": sample_terminals['bhr'].id, "stop_order": 1, "distance_from_origin_km": 210.0, "offset_minutes": 140},
        ]
        with pytest.raises(BusinessLogicError) as exc:
            RouteService.create_route(
                code="BAD-DEST",
                name="Bad Destination Stop",
                origin=sample_terminals['lhr'],
                destination=sample_terminals['isb'],
                stops_data=stops_data
            )
        assert "must be the route destination terminal" in str(exc.value)

    def test_non_continuous_stop_order_rejected(self, sample_terminals):
        stops_data = [
            {"terminal": sample_terminals['lhr'].id, "stop_order": 0, "distance_from_origin_km": 0.0, "offset_minutes": 0},
            {"terminal": sample_terminals['bhr'].id, "stop_order": 2, "distance_from_origin_km": 210.0, "offset_minutes": 140},
            {"terminal": sample_terminals['isb'].id, "stop_order": 3, "distance_from_origin_km": 380.0, "offset_minutes": 260},
        ]
        with pytest.raises(BusinessLogicError) as exc:
            RouteService.create_route(
                code="GAP-STOPS",
                name="Gap in Stops",
                origin=sample_terminals['lhr'],
                destination=sample_terminals['isb'],
                stops_data=stops_data
            )
        assert "continuous and 0-indexed" in str(exc.value)

    def test_non_increasing_distance_rejected(self, sample_terminals):
        stops_data = [
            {"terminal": sample_terminals['lhr'].id, "stop_order": 0, "distance_from_origin_km": 0.0, "offset_minutes": 0},
            {"terminal": sample_terminals['bhr'].id, "stop_order": 1, "distance_from_origin_km": 210.0, "offset_minutes": 140},
            {"terminal": sample_terminals['isb'].id, "stop_order": 2, "distance_from_origin_km": 190.0, "offset_minutes": 260},
        ]
        with pytest.raises(BusinessLogicError) as exc:
            RouteService.create_route(
                code="RETRO-DIST",
                name="Retrograde Distance",
                origin=sample_terminals['lhr'],
                destination=sample_terminals['isb'],
                stops_data=stops_data
            )
        assert "strictly greater" in str(exc.value)

    def test_non_increasing_offset_minutes_rejected(self, sample_terminals):
        stops_data = [
            {"terminal": sample_terminals['lhr'].id, "stop_order": 0, "distance_from_origin_km": 0.0, "offset_minutes": 0},
            {"terminal": sample_terminals['bhr'].id, "stop_order": 1, "distance_from_origin_km": 210.0, "offset_minutes": 140},
            {"terminal": sample_terminals['isb'].id, "stop_order": 2, "distance_from_origin_km": 380.0, "offset_minutes": 130},
        ]
        with pytest.raises(BusinessLogicError) as exc:
            RouteService.create_route(
                code="TIME-WARP",
                name="Time Warp Route",
                origin=sample_terminals['lhr'],
                destination=sample_terminals['isb'],
                stops_data=stops_data
            )
        assert "strictly greater than preceding stop" in str(exc.value)

    def test_duplicate_terminal_in_route_rejected(self, sample_terminals):
        stops_data = [
            {"terminal": sample_terminals['lhr'].id, "stop_order": 0, "distance_from_origin_km": 0.0, "offset_minutes": 0},
            {"terminal": sample_terminals['bhr'].id, "stop_order": 1, "distance_from_origin_km": 210.0, "offset_minutes": 140},
            {"terminal": sample_terminals['bhr'].id, "stop_order": 2, "distance_from_origin_km": 260.0, "offset_minutes": 180},
            {"terminal": sample_terminals['isb'].id, "stop_order": 3, "distance_from_origin_km": 380.0, "offset_minutes": 260},
        ]
        with pytest.raises(BusinessLogicError) as exc:
            RouteService.create_route(
                code="DUP-TERMINAL",
                name="Duplicate Terminal Route",
                origin=sample_terminals['lhr'],
                destination=sample_terminals['isb'],
                stops_data=stops_data
            )
        assert "cannot appear multiple times" in str(exc.value)


# =====================================================================
# 3. Stop Sequence & Travel Segments Engine Tests
# =====================================================================

@pytest.mark.django_db
class TestStopSequenceEngine:
    def test_get_all_segments_combinatorics(self, sample_terminals):
        """For a 3-stop route (LHR -> BHR -> ISB), there must be exactly 3*(3-1)/2 = 3 segments."""
        stops_data = [
            {"terminal": sample_terminals['lhr'].id, "stop_order": 0, "distance_from_origin_km": 0.0, "offset_minutes": 0},
            {"terminal": sample_terminals['bhr'].id, "stop_order": 1, "distance_from_origin_km": 210.0, "offset_minutes": 140},
            {"terminal": sample_terminals['isb'].id, "stop_order": 2, "distance_from_origin_km": 380.0, "offset_minutes": 260},
        ]
        route = RouteService.create_route(
            code="LHR-ISB-3STOPS",
            name="Lahore to Islamabad 3 Stops",
            origin=sample_terminals['lhr'],
            destination=sample_terminals['isb'],
            stops_data=stops_data
        )

        segments = StopSequenceService.get_all_segments(route)
        assert len(segments) == 3

        # Segment 1: LHR (0) -> BHR (1)
        seg1 = segments[0]
        assert seg1['from_stop_order'] == 0
        assert seg1['to_stop_order'] == 1
        assert seg1['distance_km'] == Decimal('210.00')
        assert seg1['duration_minutes'] == 140

        # Segment 2: LHR (0) -> ISB (2)
        seg2 = segments[1]
        assert seg2['from_stop_order'] == 0
        assert seg2['to_stop_order'] == 2
        assert seg2['distance_km'] == Decimal('380.00')
        assert seg2['duration_minutes'] == 260

        # Segment 3: BHR (1) -> ISB (2)
        seg3 = segments[2]
        assert seg3['from_stop_order'] == 1
        assert seg3['to_stop_order'] == 2
        assert seg3['distance_km'] == Decimal('170.00')  # 380 - 210
        assert seg3['duration_minutes'] == 120  # 260 - 140

    def test_validate_segment_directionality(self, sample_terminals):
        stops_data = [
            {"terminal": sample_terminals['lhr'].id, "stop_order": 0, "distance_from_origin_km": 0.0, "offset_minutes": 0},
            {"terminal": sample_terminals['bhr'].id, "stop_order": 1, "distance_from_origin_km": 210.0, "offset_minutes": 140},
            {"terminal": sample_terminals['isb'].id, "stop_order": 2, "distance_from_origin_km": 380.0, "offset_minutes": 260},
        ]
        route = RouteService.create_route(
            code="LHR-ISB-DIR-TEST",
            name="Direction Test",
            origin=sample_terminals['lhr'],
            destination=sample_terminals['isb'],
            stops_data=stops_data
        )

        # Forward segment: BHR (1) -> ISB (2) should succeed
        from_s, to_s, dist, dur = StopSequenceService.validate_segment(
            route, sample_terminals['bhr'].id, sample_terminals['isb'].id
        )
        assert from_s.stop_order == 1
        assert to_s.stop_order == 2
        assert dist == Decimal('170.00')
        assert dur == 120

        # Backward segment: ISB (2) -> BHR (1) must raise BusinessLogicError
        with pytest.raises(BusinessLogicError) as exc:
            StopSequenceService.validate_segment(
                route, sample_terminals['isb'].id, sample_terminals['bhr'].id
            )
        assert "Invalid travel direction" in str(exc.value)

        # Same stop segment: LHR (0) -> LHR (0) must raise BusinessLogicError
        with pytest.raises(BusinessLogicError):
            StopSequenceService.validate_segment(
                route, sample_terminals['lhr'].id, sample_terminals['lhr'].id
            )


# =====================================================================
# 4. Route API Endpoints & RBAC Tests
# =====================================================================

@pytest.mark.django_db
class TestRouteAPI:
    def test_operator_create_route_api(self, api_client, operator_token, sample_terminals):
        url = reverse('route-list')
        payload = {
            "code": "LHR-FSD-EXP",
            "name": "Lahore to Faisalabad Express",
            "origin": str(sample_terminals['lhr'].id),
            "destination": str(sample_terminals['fsd'].id),
            "total_distance_km": "180.00",
            "estimated_duration_minutes": 130
        }
        res = api_client.post(url, payload, format='json', HTTP_AUTHORIZATION=f"Bearer {operator_token}")
        assert res.status_code == status.HTTP_201_CREATED
        assert res.data['data']['code'] == "LHR-FSD-EXP"
        assert res.data['data']['segments_count'] == 1

    def test_passenger_cannot_create_route(self, api_client, passenger_token, sample_terminals):
        url = reverse('route-list')
        payload = {
            "code": "ILLEGAL-ROUTE",
            "name": "Illegal Route",
            "origin": str(sample_terminals['lhr'].id),
            "destination": str(sample_terminals['isb'].id),
        }
        res = api_client.post(url, payload, format='json', HTTP_AUTHORIZATION=f"Bearer {passenger_token}")
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_route_segments_action_api(self, api_client, sample_terminals):
        stops_data = [
            {"terminal": sample_terminals['lhr'].id, "stop_order": 0, "distance_from_origin_km": 0.0, "offset_minutes": 0},
            {"terminal": sample_terminals['bhr'].id, "stop_order": 1, "distance_from_origin_km": 210.0, "offset_minutes": 140},
            {"terminal": sample_terminals['isb'].id, "stop_order": 2, "distance_from_origin_km": 380.0, "offset_minutes": 260},
        ]
        route = RouteService.create_route(
            code="API-SEG-TEST",
            name="API Segments Test",
            origin=sample_terminals['lhr'],
            destination=sample_terminals['isb'],
            stops_data=stops_data
        )

        url = reverse('route-segments', kwargs={'pk': str(route.id)})
        res = api_client.get(url)
        assert res.status_code == status.HTTP_200_OK
        assert res.data['data']['total_segments'] == 3
        assert len(res.data['data']['segments']) == 3

    def test_update_stops_action_api(self, api_client, operator_token, sample_terminals):
        route = RouteService.create_route(
            code="API-UPDATE-STOPS",
            name="Update Stops Test",
            origin=sample_terminals['lhr'],
            destination=sample_terminals['isb'],
            total_distance_km=Decimal('380.00'),
            estimated_duration_minutes=260
        )
        assert route.stops.count() == 2

        url = reverse('route-update-stops', kwargs={'pk': str(route.id)})
        new_stops_payload = {
            "stops": [
                {"terminal": str(sample_terminals['lhr'].id), "stop_order": 0, "distance_from_origin_km": "0.00", "offset_minutes": 0},
                {"terminal": str(sample_terminals['bhr'].id), "stop_order": 1, "distance_from_origin_km": "210.00", "offset_minutes": 140},
                {"terminal": str(sample_terminals['isb'].id), "stop_order": 2, "distance_from_origin_km": "385.00", "offset_minutes": 265},
            ]
        }
        res = api_client.post(url, new_stops_payload, format='json', HTTP_AUTHORIZATION=f"Bearer {operator_token}")
        assert res.status_code == status.HTTP_200_OK
        assert res.data['data']['total_distance_km'] == '385.00'
        assert res.data['data']['estimated_duration_minutes'] == 265
        assert len(res.data['data']['stops']) == 3
