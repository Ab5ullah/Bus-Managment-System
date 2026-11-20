"""
Database Models for Fleet Management: Buses, Dynamic Seat Layouts, and Crew.
"""
from django.db import models
from django.conf import settings

from common.models import BaseModel
from common.constants import BusType, BusStatus, SeatType


class Bus(BaseModel):
    """
    Physical vehicle in the fleet with hardware configuration and amenity specs.
    """
    registration_number = models.CharField(
        max_length=30,
        unique=True,
        db_index=True,
        help_text="License plate / registration number (e.g., LEA-1234)"
    )
    name = models.CharField(
        max_length=100,
        blank=True,
        help_text="Optional fleet nickname (e.g., Daewoo Gold 01)"
    )
    bus_type = models.CharField(
        max_length=20,
        choices=BusType.choices,
        default=BusType.STANDARD_AC,
        db_index=True
    )
    total_seats = models.PositiveSmallIntegerField(
        default=0,
        help_text="Total physical seats configured on the bus."
    )
    amenities = models.JSONField(
        default=list,
        blank=True,
        help_text="List of amenities: ['WiFi', 'USB', 'Refreshments', 'Blanket']"
    )
    status = models.CharField(
        max_length=20,
        choices=BusStatus.choices,
        default=BusStatus.ACTIVE,
        db_index=True
    )

    class Meta:
        verbose_name = 'Bus'
        verbose_name_plural = 'Buses'
        ordering = ['registration_number']

    def __str__(self):
        return f"{self.registration_number} ({self.get_bus_type_display()}) - {self.total_seats} seats"


class Seat(BaseModel):
    """
    Physical seat position within a bus.
    Note:
    - 'is_ladies_only' is a demographic seat attribute (e.g. front rows designated for females).
    - 'is_active' is a hardware status (False if the seat is physically damaged/removed).
    Trip-specific operational blocking is handled per trip via TripSeatBlock in Module 4.
    """
    bus = models.ForeignKey(
        Bus,
        on_delete=models.CASCADE,
        related_name='seats'
    )
    seat_number = models.CharField(
        max_length=10,
        db_index=True,
        help_text="Alphanumeric seat identifier (e.g. '1A', '1B', 'S1')"
    )
    row = models.PositiveSmallIntegerField(help_text="Row index, starting from 1 at front")
    column = models.PositiveSmallIntegerField(help_text="Column index, starting from 1 (left to right)")
    deck = models.PositiveSmallIntegerField(default=1, help_text="1 for lower/single deck, 2 for upper deck")
    seat_type = models.CharField(
        max_length=20,
        choices=SeatType.choices,
        default=SeatType.AISLE
    )
    is_ladies_only = models.BooleanField(
        default=False,
        help_text="Seat policy attribute reserved for female passengers"
    )

    class Meta:
        verbose_name = 'Seat'
        verbose_name_plural = 'Seats'
        ordering = ['deck', 'row', 'column']
        constraints = [
            models.UniqueConstraint(
                fields=['bus', 'seat_number'],
                name='unique_seat_number_per_bus'
            ),
            models.UniqueConstraint(
                fields=['bus', 'row', 'column', 'deck'],
                name='unique_seat_grid_per_bus'
            )
        ]

    def __str__(self):
        return f"{self.bus.registration_number} - Seat {self.seat_number}"


class CrewStatus(models.TextChoices):
    AVAILABLE = 'AVAILABLE', 'Available'
    ON_TRIP = 'ON_TRIP', 'On Trip'
    ON_LEAVE = 'ON_LEAVE', 'On Leave'
    SUSPENDED = 'SUSPENDED', 'Suspended'


class Driver(BaseModel):
    """
    Licensed bus driver record.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='driver_profile'
    )
    full_name = models.CharField(max_length=150)
    license_number = models.CharField(max_length=50, unique=True, db_index=True)
    phone = models.CharField(max_length=20, unique=True)
    experience_years = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(
        max_length=20,
        choices=CrewStatus.choices,
        default=CrewStatus.AVAILABLE,
        db_index=True
    )

    class Meta:
        verbose_name = 'Driver'
        verbose_name_plural = 'Drivers'
        ordering = ['full_name']

    def __str__(self):
        return f"{self.full_name} (Lic: {self.license_number})"


class Conductor(BaseModel):
    """
    Bus conductor / validator crew member.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='conductor_profile'
    )
    full_name = models.CharField(max_length=150)
    employee_id = models.CharField(max_length=50, unique=True, db_index=True)
    phone = models.CharField(max_length=20, unique=True)
    status = models.CharField(
        max_length=20,
        choices=CrewStatus.choices,
        default=CrewStatus.AVAILABLE,
        db_index=True
    )

    class Meta:
        verbose_name = 'Conductor'
        verbose_name_plural = 'Conductors'
        ordering = ['full_name']

    def __str__(self):
        return f"{self.full_name} (Emp: {self.employee_id})"
