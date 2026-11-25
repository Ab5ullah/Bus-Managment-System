"""
Module 13: System Hardening, Audit Logging, Throttling & End-to-End Full Passenger Journey Test.
"""
from datetime import timedelta
from decimal import Decimal
import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditAction, AuditLog
from bookings.models import Booking
from cancellations.models import CancellationPolicy
from common.constants import (
    BookingSeatStatus,
    BookingStatus,
    BusType,
    Gender,
    PaymentMethod,
    TicketStatus,
    TripStatus,
    UserRole,
)
from fleet.models import Bus, Driver
from fleet.services import generate_bus_seat_matrix
from routes.models import Route, RouteStop, Terminal
from trips.models import Trip


@pytest.fixture
def e2e_infrastructure(db):
    cache.clear()
    admin = User.objects.create_superuser(
        phone="+923007770001", email="admin_e2e@bus.com", password="Password123!"
    )
    conductor = User.objects.create_user(
        phone="+923007770002",
        email="conductor_e2e@bus.com",
        password="Password123!",
        role=UserRole.CONDUCTOR,
        is_verified=True,
    )

    t_a = Terminal.objects.create(name="Lahore Terminal", city="Lahore", code="LHR-E2E")
    t_b = Terminal.objects.create(name="Jhelum Stop", city="Jhelum", code="JLM-E2E")
    t_c = Terminal.objects.create(name="Islamabad Terminal", city="Islamabad", code="ISB-E2E")

    route = Route.objects.create(
        name="Lahore - Islamabad E2E",
        code="LHR-ISB-E2E",
        origin=t_a,
        destination=t_c,
        total_distance_km=380,
    )
    stop_a = RouteStop.objects.create(route=route, terminal=t_a, stop_order=1, distance_from_origin_km=0)
    stop_b = RouteStop.objects.create(route=route, terminal=t_b, stop_order=2, distance_from_origin_km=180)
    stop_c = RouteStop.objects.create(route=route, terminal=t_c, stop_order=3, distance_from_origin_km=380)

    bus = Bus.objects.create(
        registration_number="LHR-E2E-100",
        bus_type=BusType.EXECUTIVE,
        total_seats=20,
    )
    seats = generate_bus_seat_matrix(bus, rows=5)
    driver = Driver.objects.create(full_name="Driver E2E", phone="+923007778881", license_number="LIC-E2E")

    # Departure 48 hours from now (>24h tier -> 90% refund policy)
    dep_time = timezone.now() + timedelta(hours=48)
    trip = Trip.objects.create(
        trip_number="TRP-E2E-001",
        route=route,
        bus=bus,
        driver=driver,
        departure_datetime=dep_time,
        arrival_datetime=dep_time + timedelta(hours=5),
        base_fare=Decimal("2000.00"),
        status=TripStatus.SCHEDULED,
    )

    # Seed >24h 90% refund cancellation policy
    CancellationPolicy.objects.create(
        name="Early Cancel (>24h)",
        min_hours_before_departure=24,
        refund_percentage=Decimal("90.00"),
        cancellation_fee_fixed=Decimal("0.00"),
        is_active=True,
    )

    return {
        "admin": admin,
        "conductor": conductor,
        "t_a": t_a,
        "t_b": t_b,
        "t_c": t_c,
        "stop_a": stop_a,
        "stop_b": stop_b,
        "stop_c": stop_c,
        "trip": trip,
        "seats": seats,
        "dep_time": dep_time,
    }


@pytest.mark.django_db
class TestModule13HardeningAndFullJourney:
    def test_complete_passenger_journey_e2e(self, e2e_infrastructure):
        data = e2e_infrastructure
        client = APIClient()

        # 1. Register passenger & verify OTP
        reg_res = client.post("/api/auth/register/", {
            "phone": "+923005551234",
            "email": "e2e_pax@example.com",
            "password": "StrongPassword123!",
            "full_name": "Sara Ahmed",
            "gender": Gender.FEMALE,
        }, format="json")
        assert reg_res.status_code == 201
        otp_code = reg_res.data["data"]["otp_preview"]

        verify_res = client.post("/api/auth/verify-otp/", {
            "identifier": "+923005551234",
            "otp_code": otp_code,
            "purpose": "REGISTRATION",
        }, format="json")
        assert verify_res.status_code == 200

        login_res = client.post("/api/auth/login/", {
            "identifier": "+923005551234",
            "password": "StrongPassword123!",
        }, format="json")
        assert login_res.status_code == 200
        pax_token = login_res.data["data"]["tokens"]["access"]

        # Verify AuditLog masked the password on login/register
        reg_audit = AuditLog.objects.filter(resource="/api/auth/register/").first()
        assert reg_audit is not None
        assert reg_audit.action == AuditAction.CREATE
        assert reg_audit.payload_diff.get("password") == "***MASKED***"

        # 2. Search trip for A -> C
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {pax_token}")
        search_res = client.get(
            f"/api/search/trips/?origin=Lahore&destination=Islamabad"
            f"&date={data['dep_time'].strftime('%Y-%m-%d')}"
        )
        assert search_res.status_code == 200
        trips_found = search_res.data["data"]["trips"]
        assert len(trips_found) >= 1

        seat_map_res = client.get(
            f"/api/search/trips/{data['trip'].id}/seat-map/"
            f"?from_stop={data['stop_a'].id}&to_stop={data['stop_c'].id}"
        )
        assert seat_map_res.status_code == 200
        assert len(seat_map_res.data["data"]["seats"]) == 20

        # 3. Select and lock 2 seats (1A and 1B)
        seat_1 = data["seats"][0]
        seat_2 = data["seats"][1]
        lock_res = client.post("/api/bookings/lock-seats/", {
            "trip_id": str(data["trip"].id),
            "from_stop": str(data["stop_a"].id),
            "to_stop": str(data["stop_c"].id),
            "seat_ids": [str(seat_1.id), str(seat_2.id)],
            "seat_genders": {
                str(seat_1.id): Gender.FEMALE,
                str(seat_2.id): Gender.FEMALE,
            },
        }, format="json")
        assert lock_res.status_code in (200, 201)
        lock_token = lock_res.data["data"]["lock_token"]

        booking_res = client.post("/api/bookings/create/", {
            "trip_id": str(data["trip"].id),
            "from_stop": str(data["stop_a"].id),
            "to_stop": str(data["stop_c"].id),
            "lock_token": lock_token,
            "passenger_name": "Sara Ahmed",
            "passenger_phone": "+923005551234",
            "passenger_email": "e2e_pax@example.com",
            "payment_method": PaymentMethod.CARD_ONLINE,
            "passengers": [
                {
                    "seat_id": str(seat_1.id),
                    "passenger_name": "Sara Ahmed",
                    "gender": Gender.FEMALE,
                    "age": 27,
                },
                {
                    "seat_id": str(seat_2.id),
                    "passenger_name": "Hina Ahmed",
                    "gender": Gender.FEMALE,
                    "age": 24,
                },
            ],
        }, format="json")
        assert booking_res.status_code == 201
        pnr = booking_res.data["data"]["pnr"]

        # 4. Simulate payment success
        init_pay_res = client.post("/api/payments/initiate/", {
            "booking_id": booking_res.data["data"]["id"],
            "gateway_name": "mock",
            "idempotency_key": f"e2e-pay-{pnr}",
        }, format="json")
        assert init_pay_res.status_code == 201
        payment_id = init_pay_res.data["data"]["payment"]["id"]

        confirm_pay_res = client.post("/api/payments/confirm-mock/", {
            "payment_id": payment_id,
            "outcome": "success",
        }, format="json")
        assert confirm_pay_res.status_code == 200

        booking = Booking.objects.get(pnr=pnr)
        assert booking.status == BookingStatus.CONFIRMED

        # 5. Generate & download PDF ticket with QR code
        tickets_res = client.get(f"/api/tickets/booking/{pnr}/")
        assert tickets_res.status_code == 200
        tickets_list = tickets_res.data["data"]
        assert len(tickets_list) == 2
        ticket_1 = next(t for t in tickets_list if t["seat_number"] == seat_1.seat_number)
        ticket_2 = next(t for t in tickets_list if t["seat_number"] == seat_2.seat_number)

        pdf_res = client.get(f"/api/tickets/{ticket_1['ticket_number']}/pdf/")
        assert pdf_res.status_code == 200
        assert pdf_res["Content-Type"] == "application/pdf"
        pdf_bytes = b"".join(pdf_res.streaming_content)
        assert pdf_bytes[:4] == b"%PDF"

        # 6. Conductor scans QR and boards passenger 1
        conductor_client = APIClient()
        conductor_client.force_authenticate(user=data["conductor"])
        board_res = conductor_client.post("/api/tickets/board/", {
            "qr_token": ticket_1["qr_token"],
            "trip_id": str(data["trip"].id),
        }, format="json")
        assert board_res.status_code == 200
        assert board_res.data["data"]["status"] == TicketStatus.BOARDED

        # 7. Partial cancellation of seat 2 (>24h -> 90% refund policy)
        booking_seat_2 = booking.seats.get(seat=seat_2)
        cancel_res = client.post("/api/cancellations/cancel-seats/", {
            "pnr": pnr,
            "seat_ids": [str(booking_seat_2.id)],
            "reason": "Change of plans for second passenger",
        }, format="json")
        assert cancel_res.status_code == 200
        booking.refresh_from_db()
        assert booking.status == BookingStatus.PARTIALLY_CANCELLED
        assert booking.seats.filter(seat=seat_2, status=BookingSeatStatus.CANCELLED).exists()
        assert booking.seats.filter(seat=seat_1, status__in=[BookingSeatStatus.BOOKED, BookingSeatStatus.COMPLETED]).exists()
        assert Decimal(cancel_res.data["data"]["refund"]["net_refund_amount"]) > Decimal("0.00")

        # 8. Verify AuditLog API lists CANCEL & CREATE entries for admin
        admin_client = APIClient()
        admin_client.force_authenticate(user=data["admin"])
        audit_res = admin_client.get("/api/admin/audit/logs/?action=CANCEL")
        assert audit_res.status_code == 200
        assert audit_res.data["data"]["count"] >= 1
        assert audit_res.data["data"]["results"][0]["resource"] == "/api/cancellations/cancel-seats/"

    def test_rate_limiting_throttling_enforced(self, e2e_infrastructure, monkeypatch):
        from rest_framework.throttling import AnonRateThrottle
        cache.clear()
        monkeypatch.setitem(AnonRateThrottle.THROTTLE_RATES, "anon", "2/min")

        client = APIClient()
        # First 2 anonymous requests pass
        r1 = client.get("/api/health/")
        r2 = client.get("/api/health/")
        assert r1.status_code == 200
        assert r2.status_code == 200

        # 3rd request within 1 minute is throttled (429 Too Many Requests)
        r3 = client.get("/api/health/")
        assert r3.status_code == 429
