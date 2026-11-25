"""
Database Models for Bookings, Real-Time Seat Locking, and Passenger Seat Allocations.
Supports segment-aware reservations, dual-database PostgreSQL ExclusionConstraint / SQLite compatibility,
and full monetary tracking.
"""
from datetime import timedelta
from decimal import Decimal
import secrets
import string
import uuid

from django.conf import settings
from django.contrib.postgres.fields import IntegerRangeField
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from psycopg.types.range import Range as NumericRange

from common.constants import BookingStatus, BookingSeatStatus, PaymentMethod, Gender
from common.models import BaseModel
from fleet.models import Seat
from routes.models import RouteStop
from trips.models import Trip


class SegmentRangeField(IntegerRangeField):
    """
    Dual-database IntegerRangeField.
    Operates as native int4range with GiST on PostgreSQL,
    and provides graceful string serialization on SQLite for testing.
    """
    def db_type(self, connection):
        if connection and connection.vendor != 'postgresql':
            return 'varchar(64)'
        return super().db_type(connection)

    def get_placeholder_sql(self, value, compiler, connection):
        if connection and connection.vendor != 'postgresql':
            if hasattr(value, 'as_sql'):
                return compiler.compile(value)
            return '%s', (value,)
        return super().get_placeholder_sql(value, compiler, connection)

    def get_db_prep_value(self, value, connection=None, prepared=False):
        if value is None:
            return None
        if connection and connection.vendor != 'postgresql':
            return str(value)
        return super().get_db_prep_value(value, connection=connection, prepared=prepared)

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        if isinstance(value, str):
            try:
                clean = value.strip("[]()")
                parts = [int(p.strip()) for p in clean.split(',')]
                return NumericRange(parts[0], parts[1], '[)')
            except Exception:
                return value
        return value


def generate_unique_pnr() -> str:
    """
    Generates a secure, human-readable 8-character PNR (Passenger Name Record).
    Format: 'PK' prefix + 6 unambiguous alphanumeric characters (e.g. PK-8F3W9X).
    """
    chars = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # Excludes 0, 1, I, O
    for _ in range(20):
        code = "PK" + "".join(secrets.choice(chars) for _ in range(6))
        # Lazy check avoiding circular imports
        from .models import Booking
        if not Booking.objects.filter(pnr=code).exists():
            return code
    return "PK" + uuid.uuid4().hex[:6].upper()


class SeatLock(BaseModel):
    """
    Temporary holding mechanism for seats during the booking/checkout flow.
    Enforces a segment window [from_stop_order, to_stop_order) for 10 minutes.
    """
    trip = models.ForeignKey(
        Trip,
        on_delete=models.CASCADE,
        related_name='seat_locks',
        help_text="Trip for which seat is locked"
    )
    seat = models.ForeignKey(
        Seat,
        on_delete=models.CASCADE,
        related_name='trip_locks',
        help_text="Physical seat held"
    )
    from_stop = models.ForeignKey(
        RouteStop,
        on_delete=models.CASCADE,
        related_name='+'
    )
    to_stop = models.ForeignKey(
        RouteStop,
        on_delete=models.CASCADE,
        related_name='+'
    )
    from_stop_order = models.PositiveSmallIntegerField(db_index=True)
    to_stop_order = models.PositiveSmallIntegerField(db_index=True)
    segment_range = SegmentRangeField(
        null=True,
        blank=True,
        help_text="Half-open stop order interval [from_stop_order, to_stop_order)"
    )
    lock_token = models.UUIDField(
        default=uuid.uuid4,
        db_index=True,
        help_text="Secret token issued to client to claim or release this lock"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='held_seat_locks'
    )
    session_key = models.CharField(
        max_length=64,
        blank=True,
        default='',
        db_index=True,
        help_text="Session identifier for guest/unauthenticated users"
    )
    locked_until = models.DateTimeField(
        db_index=True,
        help_text="Timestamp when this lock expires and frees the seat"
    )
    gender = models.CharField(
        max_length=10,
        choices=Gender.choices,
        null=True,
        blank=True,
        help_text="Optional passenger gender specified when locking the seat"
    )

    class Meta:
        verbose_name = "Seat Lock"
        verbose_name_plural = "Seat Locks"
        indexes = [
            models.Index(fields=['trip', 'seat', 'locked_until']),
            models.Index(fields=['lock_token', 'locked_until']),
            models.Index(fields=['from_stop_order', 'to_stop_order']),
        ]

    def __str__(self):
        return f"Lock {self.seat.seat_number} on Trip {self.trip.trip_number} until {self.locked_until:%H:%M:%S}"

    def clean(self):
        super().clean()
        if self.from_stop_order >= self.to_stop_order:
            raise ValidationError("from_stop_order must be strictly less than to_stop_order.")

    def save(self, *args, **kwargs):
        if self.from_stop_order is not None and self.to_stop_order is not None:
            try:
                self.segment_range = NumericRange(self.from_stop_order, self.to_stop_order, '[)')
            except Exception:
                pass
        super().save(*args, **kwargs)

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.locked_until


class Booking(BaseModel):
    """
    Passenger booking record representing a journey reservation.
    Supports guest web checkouts, registered users, and walk-in counter sales.
    """
    pnr = models.CharField(
        max_length=16,
        unique=True,
        db_index=True,
        default=generate_unique_pnr,
        help_text="Unique 8-character booking identifier"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='bookings',
        help_text="Customer account (null for counter walk-ins or guest checkouts)"
    )
    passenger_name = models.CharField(
        max_length=120,
        help_text="Primary passenger / contact name"
    )
    passenger_phone = models.CharField(
        max_length=30,
        help_text="Primary contact phone number"
    )
    passenger_email = models.EmailField(
        blank=True,
        default='',
        help_text="Primary contact email for e-ticket delivery"
    )
    booked_by_agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='agent_bookings',
        help_text="Counter agent or operator who booked this ticket in-person"
    )
    trip = models.ForeignKey(
        Trip,
        on_delete=models.PROTECT,
        related_name='bookings'
    )
    from_stop = models.ForeignKey(
        RouteStop,
        on_delete=models.PROTECT,
        related_name='+'
    )
    to_stop = models.ForeignKey(
        RouteStop,
        on_delete=models.PROTECT,
        related_name='+'
    )
    from_stop_order = models.PositiveSmallIntegerField(db_index=True)
    to_stop_order = models.PositiveSmallIntegerField(db_index=True)
    segment_range = SegmentRangeField(null=True, blank=True)
    status = models.CharField(
        max_length=25,
        choices=BookingStatus.choices,
        default=BookingStatus.PENDING,
        db_index=True
    )

    # Monetary and Financial Breakdown (Decimal 12, 2)
    total_gross_fare = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Sum of all seat fares"
    )
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Coupon or promotional discount applied"
    )
    tax_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Government / municipal sales tax"
    )
    service_fee = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Platform or terminal service fee"
    )
    net_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Total final payable amount (Gross - Discount + Tax + Fee)"
    )

    payment_method = models.CharField(
        max_length=25,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CARD_ONLINE,
        blank=True
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Expiration time for PENDING checkout payment"
    )
    confirmed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when booking was successfully paid and confirmed"
    )

    class Meta:
        verbose_name = "Booking"
        verbose_name_plural = "Bookings"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['trip', 'status']),
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['pnr', 'status']),
        ]

    def __str__(self):
        return f"PNR {self.pnr} ({self.status}) - {self.passenger_name} ({self.from_stop.terminal.city} -> {self.to_stop.terminal.city})"

    def clean(self):
        super().clean()
        if self.from_stop_order >= self.to_stop_order:
            raise ValidationError("from_stop_order must be strictly less than to_stop_order.")

    def save(self, *args, **kwargs):
        if self.from_stop_order is not None and self.to_stop_order is not None:
            try:
                self.segment_range = NumericRange(self.from_stop_order, self.to_stop_order, '[)')
            except Exception:
                pass
        super().save(*args, **kwargs)


class BookingSeat(BaseModel):
    """
    Individual passenger seat assignment inside a booking.
    Directly carries status and segment interval to ensure high-performance availability queries.
    """
    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name='seats'
    )
    trip = models.ForeignKey(
        Trip,
        on_delete=models.PROTECT,
        related_name='booking_seats'
    )
    seat = models.ForeignKey(
        Seat,
        on_delete=models.PROTECT,
        related_name='booking_reservations'
    )
    passenger_name = models.CharField(max_length=120)
    gender = models.CharField(max_length=10, choices=Gender.choices, default=Gender.MALE)
    age = models.PositiveSmallIntegerField(null=True, blank=True)
    cnic_or_id = models.CharField(max_length=50, blank=True, default='')
    price = models.DecimalField(max_digits=12, decimal_places=2)
    from_stop = models.ForeignKey(RouteStop, on_delete=models.PROTECT, related_name='+')
    to_stop = models.ForeignKey(RouteStop, on_delete=models.PROTECT, related_name='+')
    from_stop_order = models.PositiveSmallIntegerField(db_index=True)
    to_stop_order = models.PositiveSmallIntegerField(db_index=True)
    segment_range = SegmentRangeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=BookingSeatStatus.choices,
        default=BookingSeatStatus.LOCKED,
        db_index=True
    )

    class Meta:
        verbose_name = "Booking Seat"
        verbose_name_plural = "Booking Seats"
        ordering = ['seat__seat_number']
        indexes = [
            models.Index(fields=['trip', 'seat', 'status']),
            models.Index(fields=['booking', 'status']),
            models.Index(fields=['from_stop_order', 'to_stop_order']),
        ]

    def __str__(self):
        return f"{self.booking.pnr} - Seat {self.seat.seat_number} ({self.status}) [{self.gender}]"

    def clean(self):
        super().clean()
        if self.from_stop_order >= self.to_stop_order:
            raise ValidationError("from_stop_order must be strictly less than to_stop_order.")

    def save(self, *args, **kwargs):
        if self.from_stop_order is not None and self.to_stop_order is not None:
            try:
                self.segment_range = NumericRange(self.from_stop_order, self.to_stop_order, '[)')
            except Exception:
                pass
        super().save(*args, **kwargs)


# Dynamically attach PostgreSQL ExclusionConstraint when configured for PostgreSQL
if 'postgresql' in settings.DATABASES.get('default', {}).get('ENGINE', ''):
    from django.contrib.postgres.constraints import ExclusionConstraint
    from django.contrib.postgres.fields import RangeOperators

    BookingSeat._meta.constraints.append(
        ExclusionConstraint(
            name='exclude_overlapping_seats_on_trip',
            expressions=[
                ('trip', RangeOperators.EQUAL),
                ('seat', RangeOperators.EQUAL),
                ('segment_range', RangeOperators.OVERLAPS),
            ],
            condition=models.Q(status__in=[BookingSeatStatus.LOCKED, BookingSeatStatus.BOOKED]),
        )
    )
