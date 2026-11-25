"""
Notification Dispatch Services (Email & SMS) with persistent NotificationLog tracking.
"""
import logging
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import NotificationLog, NotificationChannel, NotificationStatus

logger = logging.getLogger(__name__)


class NotificationService:
    """
    Central service for rendering and sending Email and SMS notifications,
    recording every attempt in NotificationLog.
    """

    @staticmethod
    def send_email(
        recipient: str,
        subject: str,
        body: str,
        template_name: str = '',
        user=None,
        metadata: dict | None = None,
    ) -> NotificationLog:
        """Send an email via Django's email backend and log the outcome."""
        log = NotificationLog.objects.create(
            user=user,
            recipient=recipient,
            channel=NotificationChannel.EMAIL,
            subject=subject[:255],
            body=body,
            template_name=template_name,
            status=NotificationStatus.PENDING,
            metadata=metadata or {},
        )
        try:
            send_mail(
                subject=subject,
                message=body,
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@busms.local'),
                recipient_list=[recipient],
                fail_silently=False,
            )
            log.status = NotificationStatus.SENT
            log.sent_at = timezone.now()
            log.save(update_fields=['status', 'sent_at', 'updated_at'])
        except Exception as exc:
            logger.exception("Email notification failed for %s: %s", recipient, exc)
            log.status = NotificationStatus.FAILED
            log.error_message = str(exc)
            log.save(update_fields=['status', 'error_message', 'updated_at'])
        return log

    @staticmethod
    def send_sms(
        recipient: str,
        message: str,
        template_name: str = '',
        user=None,
        metadata: dict | None = None,
    ) -> NotificationLog:
        """
        Dispatch an SMS notification and record it in NotificationLog.
        Uses structured logging as the default gateway when no external SMS provider is configured.
        """
        log = NotificationLog.objects.create(
            user=user,
            recipient=recipient,
            channel=NotificationChannel.SMS,
            subject=template_name or 'SMS',
            body=message,
            template_name=template_name,
            status=NotificationStatus.PENDING,
            metadata=metadata or {},
        )
        try:
            logger.info("[SMS GATEWAY] To: %s | Template: %s | Msg: %s", recipient, template_name, message)
            log.status = NotificationStatus.SENT
            log.sent_at = timezone.now()
            log.save(update_fields=['status', 'sent_at', 'updated_at'])
        except Exception as exc:
            logger.exception("SMS notification failed for %s: %s", recipient, exc)
            log.status = NotificationStatus.FAILED
            log.error_message = str(exc)
            log.save(update_fields=['status', 'error_message', 'updated_at'])
        return log

    @classmethod
    def notify_booking_confirmed(cls, booking) -> list[NotificationLog]:
        """Send booking confirmation via Email and/or SMS."""
        logs = []
        seats = ", ".join(
            bs.seat.seat_number for bs in booking.seats.select_related('seat').all()
        )
        trip = booking.trip
        route_name = trip.route.name if trip and trip.route else "Bus Trip"
        dep = trip.departure_datetime.strftime('%Y-%m-%d %H:%M') if trip else ""
        meta = {'booking_id': str(booking.id), 'pnr': booking.pnr}

        subject = f"Booking Confirmed - PNR {booking.pnr}"
        body = (
            f"Dear {booking.passenger_name or 'Passenger'},\n\n"
            f"Your booking {booking.pnr} on {route_name} ({dep}) is CONFIRMED.\n"
            f"Seats: {seats}\n"
            f"Total Fare: {getattr(settings, 'CURRENCY_CODE', 'PKR')} {booking.net_amount}\n\n"
            f"Thank you for travelling with us!"
        )

        if booking.passenger_email:
            logs.append(cls.send_email(
                recipient=booking.passenger_email,
                subject=subject,
                body=body,
                template_name='booking_confirmation',
                user=booking.user,
                metadata=meta,
            ))

        if booking.passenger_phone:
            sms_text = (
                f"Booking {booking.pnr} CONFIRMED for {route_name} ({dep}). "
                f"Seats: {seats}. Total: {booking.net_amount}."
            )
            logs.append(cls.send_sms(
                recipient=booking.passenger_phone,
                message=sms_text,
                template_name='booking_confirmation',
                user=booking.user,
                metadata=meta,
            ))
        return logs

    @classmethod
    def notify_payment_receipt(cls, payment) -> list[NotificationLog]:
        """Send payment receipt notification."""
        logs = []
        booking = payment.booking
        meta = {'payment_id': str(payment.id), 'booking_id': str(booking.id), 'pnr': booking.pnr}
        subject = f"Payment Receipt - PNR {booking.pnr}"
        body = (
            f"Dear {booking.passenger_name or 'Passenger'},\n\n"
            f"We have received your payment of {payment.currency} {payment.amount} "
            f"via {payment.gateway_name} for booking {booking.pnr}.\n"
            f"Transaction Ref: {payment.gateway_reference or payment.id}\n"
        )

        if booking.passenger_email:
            logs.append(cls.send_email(
                recipient=booking.passenger_email,
                subject=subject,
                body=body,
                template_name='payment_receipt',
                user=booking.user,
                metadata=meta,
            ))

        if booking.passenger_phone:
            sms_text = f"Payment of {payment.currency} {payment.amount} received for PNR {booking.pnr}."
            logs.append(cls.send_sms(
                recipient=booking.passenger_phone,
                message=sms_text,
                template_name='payment_receipt',
                user=booking.user,
                metadata=meta,
            ))
        return logs

    @classmethod
    def notify_booking_cancelled(cls, booking, refund_amount=None) -> list[NotificationLog]:
        """Send booking cancellation & refund notification."""
        logs = []
        meta = {'booking_id': str(booking.id), 'pnr': booking.pnr}
        refund_note = f" Refund amount: {refund_amount}." if refund_amount is not None else ""
        subject = f"Booking Cancellation - PNR {booking.pnr}"
        body = (
            f"Dear {booking.passenger_name or 'Passenger'},\n\n"
            f"Your booking {booking.pnr} has been updated to {booking.status}.{refund_note}\n"
        )

        if booking.passenger_email:
            logs.append(cls.send_email(
                recipient=booking.passenger_email,
                subject=subject,
                body=body,
                template_name='booking_cancellation',
                user=booking.user,
                metadata=meta,
            ))

        if booking.passenger_phone:
            sms_text = f"Booking {booking.pnr} status: {booking.status}.{refund_note}"
            logs.append(cls.send_sms(
                recipient=booking.passenger_phone,
                message=sms_text,
                template_name='booking_cancellation',
                user=booking.user,
                metadata=meta,
            ))
        return logs

    @classmethod
    def notify_departure_reminder(cls, booking) -> list[NotificationLog]:
        """Send T-2h departure reminder notification."""
        logs = []
        trip = booking.trip
        dep = trip.departure_datetime.strftime('%Y-%m-%d %H:%M') if trip else ""
        route_name = trip.route.name if trip and trip.route else "Bus Trip"
        meta = {'booking_id': str(booking.id), 'pnr': booking.pnr, 'trip_id': str(trip.id)}
        subject = f"Departure Reminder - PNR {booking.pnr}"
        body = (
            f"Reminder: Your trip {route_name} (PNR {booking.pnr}) departs at {dep}.\n"
            f"Please arrive at the terminal at least 20 minutes before departure."
        )

        if booking.passenger_email:
            logs.append(cls.send_email(
                recipient=booking.passenger_email,
                subject=subject,
                body=body,
                template_name='departure_reminder',
                user=booking.user,
                metadata=meta,
            ))

        if booking.passenger_phone:
            sms_text = f"Reminder: Trip {route_name} (PNR {booking.pnr}) departs at {dep}."
            logs.append(cls.send_sms(
                recipient=booking.passenger_phone,
                message=sms_text,
                template_name='departure_reminder',
                user=booking.user,
                metadata=meta,
            ))
        return logs
