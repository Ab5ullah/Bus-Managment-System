"""
Comprehensive test suite for Module 2: Fleet Management (Buses, Seat Layout Matrix, Crew).
"""
from fleet.models import Seat
from fleet.services import generate_bus_seat_matrix
from fleet.models import Bus
from accounts.models import User
import pytest
from django.urls import reverse
from django.db import IntegrityError
from rest_framework import status
from rest_framework.test import APIClient

from common.constants import UserRole, BusType, BusStatus, SeatType


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def operator_user(db):
    return User.objects.create_user(
        phone="+923009999001",
        email="operator.fleet@example.com",
        password="OperatorPass123!",
        role=UserRole.OPERATOR,
        is_verified=True
    )


@pytest.fixture
def passenger_user(db):
    return User.objects.create_user(
        phone="+923009999002",
        email="passenger.fleet@example.com",
        password="PassengerPass123!",
        role=UserRole.PASSENGER,
        is_verified=True
    )


@pytest.fixture
def sample_bus(db):
    bus = Bus.objects.create(
        registration_number="LEA-1000",
        name="Liner Alpha",
        bus_type=BusType.STANDARD_AC,
        amenities=["WiFi", "USB Charging"],
        status=BusStatus.ACTIVE
    )
    generate_bus_seat_matrix(bus=bus, layout_type="2x2", rows=10, ladies_rows=2)
    return bus


@pytest.mark.django_db
class TestSeatMatrixGenerator:
    """Test automated seat layout matrix generation algorithms."""

    def test_generate_2x2_standard_matrix(self):
        bus = Bus.objects.create(
            registration_number="KHI-2000",
            bus_type=BusType.STANDARD_AC
        )
        seats = generate_bus_seat_matrix(bus=bus, layout_type="2x2", rows=10, ladies_rows=2)

        assert len(seats) == 40
        assert bus.total_seats == 40

        # Check numbering
        seat_numbers = [s.seat_number for s in seats]
        assert "1A" in seat_numbers
        assert "1D" in seat_numbers
        assert "10D" in seat_numbers

        # Check types
        s_1a = Seat.objects.get(bus=bus, seat_number="1A")
        s_1b = Seat.objects.get(bus=bus, seat_number="1B")
        assert s_1a.seat_type == SeatType.WINDOW
        assert s_1b.seat_type == SeatType.AISLE

        # Check ladies-only attributes
        assert s_1a.is_ladies_only is True
        s_3a = Seat.objects.get(bus=bus, seat_number="3A")
        assert s_3a.is_ladies_only is False

    def test_generate_2x1_luxury_matrix(self):
        bus = Bus.objects.create(
            registration_number="ISB-3000",
            bus_type=BusType.LUXURY
        )
        seats = generate_bus_seat_matrix(bus=bus, layout_type="2x1", rows=10, ladies_rows=1)

        assert len(seats) == 30
        assert bus.total_seats == 30

        seat_numbers = [s.seat_number for s in seats]
        assert "1A" in seat_numbers
        assert "1B" in seat_numbers
        assert "1C" in seat_numbers
        assert "1D" not in seat_numbers

    def test_generate_sleeper_double_deck(self):
        bus = Bus.objects.create(
            registration_number="LHR-4000",
            bus_type=BusType.SLEEPER
        )
        seats = generate_bus_seat_matrix(bus=bus, layout_type="sleeper_2x1", rows=5, deck_count=2, ladies_rows=1)

        assert len(seats) == 30  # 5 rows * 3 cols * 2 decks
        assert bus.total_seats == 30

        lower_berth = Seat.objects.get(bus=bus, seat_number="1A", deck=1)
        upper_berth = Seat.objects.get(bus=bus, seat_number="U1A", deck=2)

        assert lower_berth.seat_type == SeatType.SLEEPER_LOWER
        assert upper_berth.seat_type == SeatType.SLEEPER_UPPER


@pytest.mark.django_db
class TestSeatIntegrityConstraints:
    """Test database-level unique constraints on seats."""

    def test_duplicate_seat_number_rejected(self, sample_bus):
        with pytest.raises(IntegrityError):
            Seat.objects.create(
                bus=sample_bus,
                seat_number="1A",  # Already exists
                row=11,
                column=1,
                deck=1
            )

    def test_duplicate_grid_position_rejected(self, sample_bus):
        with pytest.raises(IntegrityError):
            Seat.objects.create(
                bus=sample_bus,
                seat_number="99Z",
                row=1,
                column=1,  # (bus, row=1, col=1, deck=1) already exists for 1A
                deck=1
            )


@pytest.mark.django_db
class TestBusAPIEndpoints:
    """Test Bus CRUD API endpoints and RBAC."""

    def test_public_can_list_and_retrieve_buses(self, api_client, sample_bus):
        list_url = reverse('fleet:buses-list')
        res = api_client.get(list_url)
        assert res.status_code == status.HTTP_200_OK
        assert len(res.data['data']['results']) >= 1

        detail_url = reverse('fleet:buses-detail', kwargs={'pk': sample_bus.id})
        detail_res = api_client.get(detail_url)
        assert detail_res.status_code == status.HTTP_200_OK
        assert detail_res.data['data']['registration_number'] == "LEA-1000"
        assert len(detail_res.data['data']['seats']) == 40

    def test_operator_can_create_bus_with_auto_layout(self, api_client, operator_user):
        api_client.force_authenticate(user=operator_user)
        url = reverse('fleet:buses-list')
        payload = {
            "registration_number": "MN-5000",
            "name": "Multan Express",
            "bus_type": "EXECUTIVE",
            "amenities": ["WiFi", "Refreshments"],
            "status": "ACTIVE",
            "layout": {
                "layout_type": "2x2",
                "rows": 8,
                "ladies_rows": 2
            }
        }
        res = api_client.post(url, payload, format='json')
        assert res.status_code == status.HTTP_201_CREATED
        assert res.data['success'] is True
        assert res.data['data']['registration_number'] == "MN-5000"
        assert res.data['data']['total_seats'] == 32
        assert len(res.data['data']['seats']) == 32

    def test_passenger_forbidden_from_creating_bus(self, api_client, passenger_user):
        api_client.force_authenticate(user=passenger_user)
        url = reverse('fleet:buses-list')
        res = api_client.post(url, {"registration_number": "FORBIDDEN-01"}, format='json')
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_operator_can_regenerate_seats(self, api_client, operator_user, sample_bus):
        api_client.force_authenticate(user=operator_user)
        url = reverse('fleet:buses-generate-seats', kwargs={'pk': sample_bus.id})
        res = api_client.post(url, {
            "layout_type": "2x1",
            "rows": 5,
            "ladies_rows": 1
        }, format='json')
        assert res.status_code == status.HTTP_200_OK
        sample_bus.refresh_from_db()
        assert sample_bus.total_seats == 15

    def test_operator_can_update_seat_attributes(self, api_client, operator_user, sample_bus):
        api_client.force_authenticate(user=operator_user)
        seat = sample_bus.seats.first()
        url = reverse('fleet:buses-update-seat', kwargs={'pk': sample_bus.id, 'seat_id': seat.id})

        res = api_client.patch(url, {
            "is_ladies_only": True,
            "is_active": False  # Hardware damage
        }, format='json')

        assert res.status_code == status.HTTP_200_OK
        seat.refresh_from_db()
        assert seat.is_ladies_only is True
        assert seat.is_active is False


@pytest.mark.django_db
class TestCrewManagement:
    """Test Driver and Conductor CRUD endpoints."""

    def test_driver_crud(self, api_client, operator_user):
        api_client.force_authenticate(user=operator_user)
        list_url = reverse('fleet:drivers-list')

        # Create
        create_res = api_client.post(list_url, {
            "full_name": "Muhammad Aslam",
            "license_number": "PK-DRV-98765",
            "phone": "+923001239876",
            "experience_years": 8,
            "status": "AVAILABLE"
        }, format='json')
        assert create_res.status_code == status.HTTP_201_CREATED
        driver_id = create_res.data['data']['id']

        # Read
        get_res = api_client.get(reverse('fleet:drivers-detail', kwargs={'pk': driver_id}))
        assert get_res.status_code == status.HTTP_200_OK
        assert get_res.data['data']['full_name'] == "Muhammad Aslam"

        # Duplicate license rejected
        dup_res = api_client.post(list_url, {
            "full_name": "Another Driver",
            "license_number": "PK-DRV-98765",
            "phone": "+923000001111",
        }, format='json')
        assert dup_res.status_code == status.HTTP_400_BAD_REQUEST

    def test_conductor_crud(self, api_client, operator_user):
        api_client.force_authenticate(user=operator_user)
        list_url = reverse('fleet:conductors-list')

        # Create
        create_res = api_client.post(list_url, {
            "full_name": "Tariq Mehmood",
            "employee_id": "EMP-CND-001",
            "phone": "+923005551234",
            "status": "AVAILABLE"
        }, format='json')
        assert create_res.status_code == status.HTTP_201_CREATED
        conductor_id = create_res.data['data']['id']

        # Duplicate employee_id rejected
        dup_res = api_client.post(list_url, {
            "full_name": "Another Conductor",
            "employee_id": "EMP-CND-001",
            "phone": "+923005559999",
        }, format='json')
        assert dup_res.status_code == status.HTTP_400_BAD_REQUEST
