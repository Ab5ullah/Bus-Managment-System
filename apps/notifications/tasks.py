"""
Celery asynchronous tasks and periodic beat jobs for Notifications & Housekeeping.
"""
from datetime import timedelta
from celery import shared_task
from django.utils import timezone

from .models import NotificationLog, NotificationStatus
from .services import NotificationService


@shared_task(name='notifications.tasks.send_booking_confirmation_notification')
def send_booking_confirmation_notification(booking_id: str) -> int:
    """Dispatch booking confirmation email/SMS asynchronously."""
    from bookings.models import Booking
    booking = (
        Booking.objects
        .select_related('trip__route', 'user')
        .prefetch_related('seats__seat')
        .filter(id=booking_id)
        .first()
    )
    if not booking:
        return 0
    logs = NotificationService.notify_booking_confirmed(booking)
    return len(logs)


@shared_task(name='notifications.tasks.send_payment_receipt_notification')
def send_payment_receipt_notification(payment_id: str) -> int:
    """Dispatch payment receipt email/SMS asynchronously."""
    from payments.models import Payment
    payment = (
        Payment.objects
        .select_related('booking__user')
        .filter(id=payment_id)
        .first()
    )
    if not payment:
        return 0
    logs = NotificationService.notify_payment_receipt(payment)
    return len(logs)


@shared_task(name='notifications.tasks.send_cancellation_notification')
def send_cancellation_notification(booking_id: str, refund_amount: str | None = None) -> int:
    """Dispatch booking cancellation & refund email/SMS asynchronously."""
    from bookings.models import Booking
    booking = Booking.objects.select_related('user').filter(id=booking_id).first()
    if not booking:
        return 0
    logs = NotificationService.notify_booking_cancelled(booking, refund_amount=refund_amount)
    return len(logs)


@shared_task(name='notifications.tasks.send_departure_reminder_task')
def send_departure_reminder_task() -> int:
    """
    Periodic Beat Task: Send T-2h departure reminders for CONFIRMED bookings
    departing within the next 2 hours that haven't already been reminded.
    """
    from bookings.models import Booking
    from common.constants import BookingStatus

    now = timezone.now()
    window_end = now + timedelta(hours=2)

    confirmed_bookings = (
        Booking.objects
        .select_related('trip__route', 'user')
        .filter(
            status=BookingStatus.CONFIRMED,
            trip__departure_datetime__gt=now,
            trip__departure_datetime__lte=window_end,
        )
    )

    sent_count = 0
    for booking in confirmed_bookings:
        already_sent = NotificationLog.objects.filter(
            template_name='departure_reminder',
            status=NotificationStatus.SENT,
            metadata__booking_id=str(booking.id),
        ).exists()
        if already_sent:
            continue
        logs = NotificationService.notify_departure_reminder(booking)
        sent_count += len(logs)
    return sent_count


@shared_task(name='notifications.tasks.cleanup_expired_seat_locks')
def cleanup_expired_seat_locks() -> int:
    """
    Periodic Housekeeping Beat Task: Delete all expired SeatLock rows
    to keep the lock table lean.
    """
    from bookings.models import SeatLock
    from bookings.services import BookingService
    now = timezone.now()
    deleted_count, _ = SeatLock.objects.filter(locked_until__lt=now).delete()
    BookingService.expire_overdue_pending_bookings()
    return deleted_count
