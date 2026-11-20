"""
Database models for Terminals, Sequenced Routes, and Intermediate Stops.
"""
from django.db import models
from common.models import BaseModel


class Terminal(BaseModel):
    """
    Physical bus station or passenger transit terminal.
    """
    name = models.CharField(
        max_length=150,
        help_text="Terminal station name (e.g., Faizabad Terminal, Kalma Chowk Station)"
    )
    code = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        help_text="Unique terminal identifier (e.g., ISB-01, LHR-01)"
    )
    city = models.CharField(
        max_length=100,
        db_index=True,
        help_text="City where the terminal is located (e.g., Islamabad, Lahore)"
    )
    address = models.CharField(
        max_length=255,
        blank=True,
        help_text="Street address / landmark of the terminal"
    )
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="GPS Latitude (-90 to +90)"
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="GPS Longitude (-180 to +180)"
    )
    contact_number = models.CharField(
        max_length=20,
        blank=True,
        help_text="Inquiry / helpline phone number for this terminal"
    )

    class Meta:
        verbose_name = 'Terminal'
        verbose_name_plural = 'Terminals'
        ordering = ['city', 'name']

    def __str__(self):
        return f"{self.name} ({self.code}) — {self.city}"

    def save(self, *args, **kwargs):
        # Normalize code to uppercase
        if self.code:
            self.code = self.code.strip().upper()
        super().save(*args, **kwargs)


class Route(BaseModel):
    """
    Defined journey corridor from an origin terminal to a destination terminal.
    Intermediate stops are configured sequentially via RouteStop.
    """
    code = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text="Unique route code (e.g. LHR-ISB-M2, KHI-LHR-EXP)"
    )
    name = models.CharField(
        max_length=200,
        help_text="Human-readable route title (e.g. Lahore to Islamabad via M2)"
    )
    origin = models.ForeignKey(
        Terminal,
        on_delete=models.PROTECT,
        related_name='routes_originating',
        help_text="Starting transit terminal for this route"
    )
    destination = models.ForeignKey(
        Terminal,
        on_delete=models.PROTECT,
        related_name='routes_terminating',
        help_text="Final destination terminal for this route"
    )
    total_distance_km = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        default=0.00,
        help_text="Total driving distance from origin to destination in kilometers"
    )
    estimated_duration_minutes = models.PositiveIntegerField(
        default=0,
        help_text="Estimated total journey duration in minutes"
    )
    description = models.TextField(
        blank=True,
        help_text="Operational notes or route highway details"
    )

    class Meta:
        verbose_name = 'Route'
        verbose_name_plural = 'Routes'
        ordering = ['code']
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(origin=models.F('destination')),
                name='check_route_origin_not_destination'
            )
        ]

    def __str__(self):
        return f"{self.code}: {self.origin.city} → {self.destination.city} ({self.total_distance_km} km)"

    def save(self, *args, **kwargs):
        if self.code:
            self.code = self.code.strip().upper()
        super().save(*args, **kwargs)


class RouteStop(BaseModel):
    """
    Sequenced stop along a route.
    Origin terminal has stop_order = 0, distance = 0 km, offset_minutes = 0.
    Intermediate stops have incrementing stop_order, distance, and offset_minutes.
    Destination terminal has the maximum stop_order.
    """
    route = models.ForeignKey(
        Route,
        on_delete=models.CASCADE,
        related_name='stops'
    )
    terminal = models.ForeignKey(
        Terminal,
        on_delete=models.PROTECT,
        related_name='route_stops'
    )
    stop_order = models.PositiveSmallIntegerField(
        db_index=True,
        help_text="Zero-indexed sequence position: 0 for origin, 1..N for intermediate, max for destination"
    )
    distance_from_origin_km = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        default=0.00,
        help_text="Cumulative road distance from route origin in km"
    )
    offset_minutes = models.PositiveIntegerField(
        default=0,
        help_text="Estimated travel time elapsed in minutes from departure at origin"
    )

    class Meta:
        verbose_name = 'Route Stop'
        verbose_name_plural = 'Route Stops'
        ordering = ['route', 'stop_order']
        constraints = [
            models.UniqueConstraint(
                fields=['route', 'stop_order'],
                name='unique_stop_order_per_route'
            ),
            models.UniqueConstraint(
                fields=['route', 'terminal'],
                name='unique_terminal_per_route'
            ),
        ]

    def __str__(self):
        return f"{self.route.code} [Stop #{self.stop_order}]: {self.terminal.name} (+{self.offset_minutes}m, {self.distance_from_origin_km}km)"
