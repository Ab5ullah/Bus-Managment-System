"""
Database Models for Scheduled Trips, Segment Fare Rules, and Per-Trip Seat Blocks.
"""
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError

from common.models import BaseModel
from common.constants import TripStatus, SeatType
from routes.models import Route, RouteStop
from fleet.models import Bus, Seat, Driver, Conductor


class Trip(BaseModel):
    """
    Scheduled commercial journey assigned to a route, a bus vehicle, and operating crew.
    """
    trip_number = models.CharField(
        max_length=40,
        unique=True,
        db_index=True,
        help_text="Unique trip identifier (e.g., TRIP-20261120-001)"
    )
    route = models.ForeignKey(
        Route,
        on_delete=models.PROTECT,
        related_name='trips',
        help_text="Route path followed by this journey"
    )
    bus = models.ForeignKey(
        Bus,
        on_delete=models.PROTECT,
        related_name='trips',
        help_text="Physical vehicle assigned to this trip"
    )
    driver = models.ForeignKey(
        Driver,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='trips',
        help_text="Primary driver assigned to this trip"
    )
    conductor = models.ForeignKey(
        Conductor,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='trips',
        help_text="Ticket conductor / onboard crew validator"
    )
    departure_datetime = models.DateTimeField(
        db_index=True,
        help_text="Scheduled departure date & time from origin station"
    )
    arrival_datetime = models.DateTimeField(
        db_index=True,
        help_text="Scheduled arrival date & time at destination terminal"
    )
    status = models.CharField(
        max_length=20,
        choices=TripStatus.choices,
        default=TripStatus.SCHEDULED,
        db_index=True
    )
    base_fare = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Full-corridor standard ticket base fare (excluding taxes)"
    )

    class Meta:
        verbose_name = 'Trip'
        verbose_name_plural = 'Trips'
        ordering = ['departure_datetime', 'trip_number']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(arrival_datetime__gt=models.F('departure_datetime')),
                name='check_trip_arrival_after_departure'
            )
        ]
        indexes = [
            models.Index(fields=['route', 'departure_datetime']),
            models.Index(fields=['bus', 'departure_datetime']),
            models.Index(fields=['driver', 'departure_datetime']),
            models.Index(fields=['status', 'departure_datetime']),
        ]

    def __str__(self):
        return f"{self.trip_number}: {self.route.code} ({self.departure_datetime:%Y-%m-%d %H:%M})"

    def clean(self):
        super().clean()
        if self.departure_datetime and self.arrival_datetime:
            if self.arrival_datetime <= self.departure_datetime:
                raise ValidationError("Trip arrival datetime must be strictly after departure datetime.")


class TripSeatBlock(BaseModel):
    """
    Per-trip seat reservation / administrative blocking.
    Allows blocking a seat for a single trip (e.g. VIP, crew rest, mechanical fault)
    without altering fleet-wide bus configuration.
    """
    trip = models.ForeignKey(
        Trip,
        on_delete=models.CASCADE,
        related_name='seat_blocks'
    )
    seat = models.ForeignKey(
        Seat,
        on_delete=models.CASCADE,
        related_name='trip_blocks'
    )
    reason = models.CharField(
        max_length=150,
        default="Operational Block",
        help_text="Reason for blocking (e.g., VIP, Crew Rest, Maintenance)"
    )
    blocked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='blocked_trip_seats'
    )

    class Meta:
        verbose_name = 'Trip Seat Block'
        verbose_name_plural = 'Trip Seat Blocks'
        ordering = ['trip', 'seat__seat_number']
        constraints = [
            models.UniqueConstraint(
                fields=['trip', 'seat'],
                name='unique_seat_block_per_trip'
            )
        ]

    def __str__(self):
        return f"Block on Trip {self.trip.trip_number} - Seat {self.seat.seat_number} ({self.reason})"


class FareRule(BaseModel):
    """
    Segment-specific fare pricing rules for a route corridor.
    Allows exact price definitions between any two stops on a route,
    with optional seat class override.
    """
    route = models.ForeignKey(
        Route,
        on_delete=models.CASCADE,
        related_name='fare_rules'
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
    seat_type = models.CharField(
        max_length=20,
        choices=SeatType.choices,
        null=True,
        blank=True,
        help_text="Optional seat class override (e.g. SLEEPER_LOWER, SLEEPER_UPPER). Null applies as default."
    )
    fare_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Base ticket fare price in PKR for this specific segment"
    )

    class Meta:
        verbose_name = 'Fare Rule'
        verbose_name_plural = 'Fare Rules'
        ordering = ['route', 'from_stop_order', 'to_stop_order']
        constraints = [
            models.UniqueConstraint(
                fields=['route', 'from_stop', 'to_stop', 'seat_type'],
                name='unique_fare_rule_per_segment_and_seat_type'
            )
        ]

    def __str__(self):
        seat_spec = f" [{self.seat_type}]" if self.seat_type else " [All Classes]"
        return (
            f"{self.route.code}: {self.from_stop.terminal.city} (#{self.from_stop_order}) ➔ "
            f"{self.to_stop.terminal.city} (#{self.to_stop_order}){seat_spec} = PKR {self.fare_amount}"
        )

    def clean(self):
        super().clean()
        if self.from_stop.route_id != self.route_id:
            raise ValidationError("Origin stop does not belong to this route.")
        if self.to_stop.route_id != self.route_id:
            raise ValidationError("Destination stop does not belong to this route.")
        if self.from_stop.stop_order >= self.to_stop.stop_order:
            raise ValidationError("Origin stop must precede destination stop in sequence.")
