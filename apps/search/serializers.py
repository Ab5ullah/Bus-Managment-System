"""
DRF Serializers for Trip Search and Segment-Aware Seat Map Queries.
"""
from rest_framework import serializers
from common.constants import BusType


class TripSearchQuerySerializer(serializers.Serializer):
    """
    Query parameters for searching trips along an origin-destination corridor.
    Supports 'origin' / 'destination' and aliases 'origin_terminal' / 'destination_terminal'.
    """
    origin = serializers.CharField(
        required=False,
        help_text="Origin city name (e.g. 'Lahore'), terminal code, or Terminal station UUID"
    )
    destination = serializers.CharField(
        required=False,
        help_text="Destination city name (e.g. 'Islamabad'), terminal code, or Terminal station UUID"
    )
    origin_terminal = serializers.CharField(
        required=False,
        help_text="Alias for 'origin'"
    )
    destination_terminal = serializers.CharField(
        required=False,
        help_text="Alias for 'destination'"
    )
    page = serializers.IntegerField(required=False, default=1, min_value=1)
    page_size = serializers.IntegerField(required=False, default=20, min_value=1, max_value=100)
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

    def validate(self, attrs):
        origin = attrs.get('origin') or attrs.get('origin_terminal')
        destination = attrs.get('destination') or attrs.get('destination_terminal')
        if not origin:
            raise serializers.ValidationError({"origin": "This field is required (pass 'origin' or 'origin_terminal')."})
        if not destination:
            raise serializers.ValidationError({"destination": "This field is required (pass 'destination' or 'destination_terminal')."})
        attrs['origin'] = origin.strip()
        attrs['destination'] = destination.strip()
        return attrs


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
