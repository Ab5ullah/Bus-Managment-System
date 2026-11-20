"""
DRF Serializers for Trip Search and Segment-Aware Seat Map Queries.
"""
from rest_framework import serializers
from common.constants import BusType


class TripSearchQuerySerializer(serializers.Serializer):
    """
    Query parameters for searching trips along an origin-destination corridor.
    """
    origin = serializers.CharField(
        required=True,
        help_text="Origin city name (e.g. 'Lahore') or Terminal station UUID"
    )
    destination = serializers.CharField(
        required=True,
        help_text="Destination city name (e.g. 'Islamabad') or Terminal station UUID"
    )
    date = serializers.DateField(
        required=False,
        default=None,
        help_text="Travel departure date (YYYY-MM-DD). If omitted, returns all upcoming scheduled trips."
    )
    bus_type = serializers.ChoiceField(
        choices=BusType.choices,
        required=False,
        default=None,
        help_text="Filter by vehicle class (STANDARD_AC, EXECUTIVE, LUXURY, SLEEPER)"
    )
    min_price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        min_value=0,
        default=None,
        help_text="Minimum ticket fare in PKR"
    )
    max_price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        min_value=0,
        default=None,
        help_text="Maximum ticket fare in PKR"
    )
    departure_window = serializers.ChoiceField(
        choices=['morning', 'afternoon', 'night'],
        required=False,
        default=None,
        help_text="Time of day window: 'morning' (05:00-11:59), 'afternoon' (12:00-17:59), 'night' (18:00-04:59)"
    )
    sort_by = serializers.ChoiceField(
        choices=['departure_asc', 'departure_desc', 'price_asc', 'price_desc', 'duration_asc'],
        default='departure_asc',
        help_text="Sort order: departure_asc, departure_desc, price_asc, price_desc, duration_asc"
    )


class SeatMapQuerySerializer(serializers.Serializer):
    """
    Query parameters for querying a trip's seat availability map for a specific segment.
    """
    from_stop = serializers.UUIDField(
        required=True,
        help_text="UUID of boarding RouteStop"
    )
    to_stop = serializers.UUIDField(
        required=True,
        help_text="UUID of alighting RouteStop"
    )
