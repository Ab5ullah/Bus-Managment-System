"""
DRF Serializers for Terminals, Sequenced Routes, and Route Stops.
"""
from rest_framework import serializers
from .models import Terminal, Route, RouteStop


class TerminalSerializer(serializers.ModelSerializer):
    """
    Serializer for transit terminals.
    """
    class Meta:
        model = Terminal
        fields = [
            'id', 'name', 'code', 'city', 'address',
            'latitude', 'longitude', 'contact_number',
            'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_code(self, value):
        return value.strip().upper()


class RouteStopSerializer(serializers.ModelSerializer):
    """
    Nested serializer for displaying an ordered stop on a route.
    """
    terminal_name = serializers.CharField(source='terminal.name', read_only=True)
    terminal_code = serializers.CharField(source='terminal.code', read_only=True)
    terminal_city = serializers.CharField(source='terminal.city', read_only=True)

    class Meta:
        model = RouteStop
        fields = [
            'id', 'stop_order', 'terminal', 'terminal_name',
            'terminal_code', 'terminal_city',
            'distance_from_origin_km', 'offset_minutes'
        ]
        read_only_fields = ['id', 'terminal_name', 'terminal_code', 'terminal_city']


class RouteStopInputSerializer(serializers.Serializer):
    """
    Input serializer for configuring a stop within a route creation/update.
    """
    terminal = serializers.UUIDField(help_text="UUID of the Terminal station")
    stop_order = serializers.IntegerField(min_value=0, help_text="Zero-based sequence order (0, 1, 2...)")
    distance_from_origin_km = serializers.DecimalField(
        max_digits=7,
        decimal_places=2,
        min_value=0,
        default=0.0,
        help_text="Cumulative distance from route origin in km"
    )
    offset_minutes = serializers.IntegerField(
        min_value=0,
        default=0,
        help_text="Travel time offset from origin departure in minutes"
    )


class RouteListSerializer(serializers.ModelSerializer):
    """
    Lightweight summary serializer for route listing.
    """
    origin_city = serializers.CharField(source='origin.city', read_only=True)
    origin_name = serializers.CharField(source='origin.name', read_only=True)
    origin_code = serializers.CharField(source='origin.code', read_only=True)
    destination_city = serializers.CharField(source='destination.city', read_only=True)
    destination_name = serializers.CharField(source='destination.name', read_only=True)
    destination_code = serializers.CharField(source='destination.code', read_only=True)
    stops_count = serializers.IntegerField(source='stops.count', read_only=True)

    class Meta:
        model = Route
        fields = [
            'id', 'code', 'name',
            'origin', 'origin_city', 'origin_name', 'origin_code',
            'destination', 'destination_city', 'destination_name', 'destination_code',
            'total_distance_km', 'estimated_duration_minutes',
            'stops_count', 'is_active', 'created_at'
        ]


class RouteDetailSerializer(serializers.ModelSerializer):
    """
    Comprehensive serializer for single route retrieval including sequenced stops.
    """
    origin = TerminalSerializer(read_only=True)
    destination = TerminalSerializer(read_only=True)
    stops = RouteStopSerializer(many=True, read_only=True)
    segments_count = serializers.SerializerMethodField()

    class Meta:
        model = Route
        fields = [
            'id', 'code', 'name',
            'origin', 'destination',
            'total_distance_km', 'estimated_duration_minutes',
            'description', 'is_active', 'stops',
            'segments_count', 'created_at', 'updated_at'
        ]

    def get_segments_count(self, obj) -> int:
        n = obj.stops.count()
        return (n * (n - 1)) // 2 if n >= 2 else 0


class RouteCreateSerializer(serializers.Serializer):
    """
    Payload serializer for creating a route with optional custom stops.
    """
    code = serializers.CharField(max_length=50)
    name = serializers.CharField(max_length=200)
    origin = serializers.UUIDField()
    destination = serializers.UUIDField()
    total_distance_km = serializers.DecimalField(max_digits=7, decimal_places=2, default=0.0)
    estimated_duration_minutes = serializers.IntegerField(default=0)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    stops = serializers.ListField(
        child=RouteStopInputSerializer(),
        required=False,
        default=None,
        help_text="Optional list of ordered stops. If omitted, a direct origin-destination stop pair is generated."
    )


class RouteStopsUpdateSerializer(serializers.Serializer):
    """
    Payload serializer for replacing all stops on a route.
    """
    stops = serializers.ListField(
        child=RouteStopInputSerializer(),
        min_length=2,
        help_text="Complete ordered list of stops from origin (0) to destination (N-1)."
    )
