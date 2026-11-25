"""
Automated tests for Module 12: Notifications & Background Tasks (Celery / Redis).
"""
from datetime import timedelta
from decimal import Decimal
import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from bookings.models import Booking, BookingSeat, SeatLock
from common.constants import (
    BookingStatus,
    BookingSeatStatus,
    BusType,
    Gender,
    PaymentMethod,
    PaymentStatus,
    TripStatus,
    UserRole,
)
from fleet.models import Bus, Driver
from fleet.services import generate_bus_seat_matrix
from notifications.models import NotificationChannel, NotificationLog, NotificationStatus
from notifications.services import NotificationService
from notifications.tasks import (
    cleanup_expired_seat_locks,
    send_booking_confirmation_notification,
    send_cancellation_notification,
    send_departure_reminder_task,
    send_payment_receipt_notification,
)
from payments.models import Payment
from routes.models import Route, RouteStop, Terminal
from trips.models import Trip


@pytest.fixture
def notification_setup(db):
    admin = User.objects.create_superuser(
        phone="+923009990001", email="admin_notif@bus.com", password="Password123!"
    )
    passenger = User.objects.create_user(
        phone="+923009990002",
        email="pax_notif@bus.com",
        password="Password123!",
        role=UserRole.PASSENGER,
        is_verified=True,
    )

    t1 = Terminal.objects.create(name="Lahore Terminal", city="Lahore", code="LHR")
    t2 = Terminal.objects.create(name="Islamabad Terminal", city="Islamabad", code="ISB")
    route = Route.objects.create(
        name="Lahore - Islamabad Express",
        code="LHR-ISB-N12",
        origin=t1,
        destination=t2,
        total_distance_km=380,
    )
    s1 = RouteStop.objects.create(route=route, terminal=t1, stop_order=1, distance_from_origin_km=0)
    s2 = RouteStop.objects.create(route=route, terminal=t2, stop_order=2, distance_from_origin_km=380)

    bus = Bus.objects.create(
        registration_number="LHR-NOTIF-12",
        bus_type=BusType.EXECUTIVE,
        total_seats=20,
    )
    seats = generate_bus_seat_matrix(bus, rows=5)
    driver = Driver.objects.create(full_name="Driver Notif", phone="+923008887771", license_number="LIC-N12")

    now = timezone.now()
    trip = Trip.objects.create(
        trip_number="TRP-N12-1",
        route=route,
        bus=bus,
        driver=driver,
        departure_datetime=now + timedelta(hours=1),
        arrival_datetime=now + timedelta(hours=5),
        base_fare=Decimal("1500.00"),
        status=TripStatus.SCHEDULED,
    )

    booking = Booking.objects.create(
        user=passenger,
        trip=trip,
        passenger_name="Ali Raza",
        passenger_email="ali@example.com",
        passenger_phone="+923001112233",
        from_stop=s1,
        to_stop=s2,
        from_stop_order=1,
        to_stop_order=2,
        status=BookingStatus.CONFIRMED,
        total_gross_fare=Decimal("1500.00"),
        discount_amount=Decimal("0.00"),
        tax_amount=Decimal("75.00"),
        service_fee=Decimal("50.00"),
        net_amount=Decimal("1625.00"),
        payment_method=PaymentMethod.CARD_ONLINE,
        confirmed_at=now,
    )
    BookingSeat.objects.create(
        booking=booking,
        trip=trip,
        seat=seats[0],
        passenger_name="Ali Raza",
        gender=Gender.MALE,
        price=Decimal("1500.00"),
        from_stop=s1,
        to_stop=s2,
        from_stop_order=1,
        to_stop_order=2,
        status=BookingSeatStatus.BOOKED,
    )

    payment = Payment.objects.create(
        booking=booking,
        gateway_name="stripe",
        gateway_reference="tx_notif_123",
        idempotency_key="idem_notif_123",
        method=PaymentMethod.CARD_ONLINE,
        amount=Decimal("1625.00"),
        currency="PKR",
        status=PaymentStatus.SUCCESS,
        paid_at=now,
    )

    return {
        "admin": admin,
        "passenger": passenger,
        "trip": trip,
        "route": route,
        "bus": bus,
        "driver": driver,
        "seats": seats,
        "s1": s1,
        "s2": s2,
        "booking": booking,
        "payment": payment,
    }


@pytest.mark.django_db
class TestModule12Notifications:
    def test_send_email_and_sms_creates_notification_logs(self, notification_setup):
        mail.outbox.clear()
        user = notification_setup["passenger"]

        email_log = NotificationService.send_email(
            recipient="test@example.com",
            subject="Hello BusMS",
            body="Welcome aboard!",
            template_name="welcome",
            user=user,
        )
        sms_log = NotificationService.send_sms(
            recipient="+923001112233",
            message="Welcome aboard SMS!",
            template_name="welcome_sms",
            user=user,
        )

        assert email_log.status == NotificationStatus.SENT
        assert email_log.channel == NotificationChannel.EMAIL
        assert email_log.sent_at is not None
        assert len(mail.outbox) == 1
        assert mail.outbox[0].subject == "Hello BusMS"

        assert sms_log.status == NotificationStatus.SENT
        assert sms_log.channel == NotificationChannel.SMS
        assert sms_log.sent_at is not None

    def test_booking_confirmation_task_dispatches_email_and_sms(self, notification_setup, settings):
        settings.CELERY_TASK_ALWAYS_EAGER = True
        mail.outbox.clear()
        booking = notification_setup["booking"]

        result = send_booking_confirmation_notification.delay(str(booking.id))
        assert result.get() == 2

        logs = NotificationLog.objects.filter(template_name="booking_confirmation")
        assert logs.count() == 2
        assert logs.filter(channel=NotificationChannel.EMAIL, status=NotificationStatus.SENT).exists()
        assert logs.filter(channel=NotificationChannel.SMS, status=NotificationStatus.SENT).exists()
        assert len(mail.outbox) == 1
        assert booking.pnr in mail.outbox[0].subject

    def test_payment_receipt_and_cancellation_tasks(self, notification_setup, settings):
        settings.CELERY_TASK_ALWAYS_EAGER = True
        booking = notification_setup["booking"]
        payment = notification_setup["payment"]

        res_pay = send_payment_receipt_notification.delay(str(payment.id))
        assert res_pay.get() == 2
        assert NotificationLog.objects.filter(template_name="payment_receipt").count() == 2

        res_cancel = send_cancellation_notification.delay(str(booking.id), "1462.50")
        assert res_cancel.get() == 2
        assert NotificationLog.objects.filter(template_name="booking_cancellation").count() == 2

    def test_departure_reminder_task_t_minus_2h_and_idempotency(self, notification_setup, settings):
        settings.CELERY_TASK_ALWAYS_EAGER = True
        data = notification_setup
        now = timezone.now()

        # Create a second trip departing in 6 hours (outside T-2h window)
        far_trip = Trip.objects.create(
            trip_number="TRP-N12-2",
            route=data["route"],
            bus=data["bus"],
            driver=data["driver"],
            departure_datetime=now + timedelta(hours=6),
            arrival_datetime=now + timedelta(hours=10),
            base_fare=Decimal("1500.00"),
            status=TripStatus.SCHEDULED,
        )
        Booking.objects.create(
            user=data["passenger"],
            trip=far_trip,
            passenger_name="Far Passenger",
            passenger_email="far@example.com",
            passenger_phone="+923009998877",
            from_stop=data["s1"],
            to_stop=data["s2"],
            from_stop_order=1,
            to_stop_order=2,
            status=BookingStatus.CONFIRMED,
            total_gross_fare=Decimal("1500.00"),
            net_amount=Decimal("1625.00"),
            confirmed_at=now,
        )

        # First run: only the T+1h booking should receive reminders (email + sms = 2)
        first_run = send_departure_reminder_task.delay().get()
        assert first_run == 2
        assert NotificationLog.objects.filter(template_name="departure_reminder").count() == 2

        # Second run: idempotent — no duplicate reminders sent
        second_run = send_departure_reminder_task.delay().get()
        assert second_run == 0

    def test_cleanup_expired_seat_locks_housekeeping(self, notification_setup, settings):
        settings.CELERY_TASK_ALWAYS_EAGER = True
        data = notification_setup
        now = timezone.now()

        # Expired lock
        SeatLock.objects.create(
            trip=data["trip"],
            seat=data["seats"][1],
            from_stop=data["s1"],
            to_stop=data["s2"],
            from_stop_order=1,
            to_stop_order=2,
            locked_until=now - timedelta(minutes=5),
        )
        # Active lock
        active_lock = SeatLock.objects.create(
            trip=data["trip"],
            seat=data["seats"][2],
            from_stop=data["s1"],
            to_stop=data["s2"],
            from_stop_order=1,
            to_stop_order=2,
            locked_until=now + timedelta(minutes=10),
        )

        deleted = cleanup_expired_seat_locks.delay().get()
        assert deleted == 1
        assert SeatLock.objects.count() == 1
        assert SeatLock.objects.filter(id=active_lock.id).exists()

    def test_admin_notification_logs_api_and_permissions(self, notification_setup, settings):
        settings.CELERY_TASK_ALWAYS_EAGER = True
        data = notification_setup
        send_booking_confirmation_notification.delay(str(data["booking"].id))

        client = APIClient()

        # Unauthenticated rejected
        resp_unauth = client.get("/api/admin/notifications/logs/")
        assert resp_unauth.status_code == 401

        # Passenger forbidden
        client.force_authenticate(user=data["passenger"])
        resp_pax = client.get("/api/admin/notifications/logs/")
        assert resp_pax.status_code == 403

        # Admin allowed + filterable
        client.force_authenticate(user=data["admin"])
        resp_admin = client.get("/api/admin/notifications/logs/?channel=EMAIL")
        assert resp_admin.status_code == 200
        assert resp_admin.data["success"] is True
        assert resp_admin.data["data"]["count"] == 1
        assert resp_admin.data["data"]["results"][0]["channel"] == "EMAIL"

        # Manual trigger endpoints
        resp_cleanup = client.post("/api/admin/notifications/cleanup-locks/")
        assert resp_cleanup.status_code == 200
        assert resp_cleanup.data["success"] is True

        resp_remind = client.post("/api/admin/notifications/send-reminders/")
        assert resp_remind.status_code == 200
        assert resp_remind.data["success"] is True
