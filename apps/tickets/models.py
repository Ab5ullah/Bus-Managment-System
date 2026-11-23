"""
Database models for Module 8: Tickets, QR Codes & Conductor Validation.
"""
import uuid
import secrets
from django.db import models
from django.conf import settings
from django.utils import timezone
from common.models import BaseModel
from common.constants import TicketStatus


def generate_ticket_number() -> str:
    """
    Generates a unique audit-compliant e-ticket identifier.
    Format: TKT-YYYYMMDD-XXXXXX (e.g. TKT-20261123-9F8C1D)
    """
    date_str = timezone.now().strftime("%Y%m%d")
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    random_suffix = "".join(secrets.choice(chars) for _ in range(6))
    return f"TKT-{date_str}-{random_suffix}"


class Ticket(BaseModel):
    """
    Formal travel document issued for an individual reserved passenger seat.
    Contains cryptographically signed QR code token and conductor boarding records.
    """
    booking_seat = models.OneToOneField(
        'bookings.BookingSeat',
        on_delete=models.CASCADE,
        related_name='ticket',
        help_text="Reserved seat associated with this ticket"
    )
    ticket_number = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        default=generate_ticket_number,
        help_text="Unique audit e-ticket number"
    )
    qr_token = models.TextField(
        help_text="Cryptographically signed HMAC token payload for conductor verification"
    )
    status = models.CharField(
        max_length=25,
        choices=TicketStatus.choices,
        default=TicketStatus.ISSUED,
        db_index=True,
        help_text="Current state of this ticket"
    )
    boarded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when conductor scanned and validated this ticket at boarding"
    )
    boarded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='boarded_tickets',
        help_text="Conductor or driver who validated boarding"
    )
    boarding_station = models.ForeignKey(
        'routes.RouteStop',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
        help_text="Station stop where boarding was recorded"
    )

    class Meta:
        verbose_name = "Ticket"
        verbose_name_plural = "Tickets"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['ticket_number']),
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self):
        return f"Ticket {self.ticket_number} | Seat {self.seat_number} [{self.status}]"

    @property
    def booking(self):
        return self.booking_seat.booking

    @property
    def trip(self):
        return self.booking_seat.trip

    @property
    def seat(self):
        return self.booking_seat.seat

    @property
    def seat_number(self) -> str:
        return self.booking_seat.seat.seat_number

    @property
    def passenger_name(self) -> str:
        return self.booking_seat.passenger_name

    @property
    def is_boarded(self) -> bool:
        return self.status == TicketStatus.BOARDED
