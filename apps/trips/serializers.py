"""
DRF Serializers for Trips, Fare Rules, and Per-Trip Seat Blocks.
"""
from rest_framework import serializers
from fleet.serializers import BusListSerializer, DriverSerializer, ConductorSerializer
from routes.serializers import RouteListSerializer, RouteStopSerializer
from common.constants import TripStatus, SeatType
from .models import Trip, TripSeatBlock, FareRule


class TripListSerializer(serializers.ModelSerializer):
    """
    Summary serializer for trip listings and search results.
    """
    route_code = serializers.CharField(source='route.code', read_only=True)
    route_name = serializers.CharField(source='route.name', read_only=True)
    origin_city = serializers.CharField(source='route.origin.city', read_only=True)
    destination_city = serializers.CharField(source='route.destination.city', read_only=True)
    bus_registration = serializers.CharField(source='bus.registration_number', read_only=True)
    bus_type = serializers.CharField(source='bus.bus_type', read_only=True)
    bus_type_display = serializers.CharField(source='bus.get_bus_type_display', read_only=True)
    total_seats = serializers.IntegerField(source='bus.total_seats', read_only=True)
    driver_name = serializers.CharField(source='driver.full_name', read_only=True, default=None)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Trip
        fields = [
            'id', 'trip_number', 'status', 'status_display',
            'route', 'route_code', 'route_name',
            'origin_city', 'destination_city',
            'bus', 'bus_registration', 'bus_type', 'bus_type_display', 'total_seats',
            'driver', 'driver_name',
            'departure_datetime', 'arrival_datetime', 'base_fare',
            'created_at'
        ]


class TripSeatBlockSerializer(serializers.ModelSerializer):
    """
    Serializer for seats blocked for a specific trip.
    """
    seat_number = serializers.CharField(source='seat.seat_number', read_only=True)
    seat_type = serializers.CharField(source='seat.seat_type', read_only=True)
    blocked_by_phone = serializers.CharField(source='blocked_by.phone', read_only=True, default=None)

    class Meta:
        model = TripSeatBlock
        fields = [
            'id', 'trip', 'seat', 'seat_number', 'seat_type',
            'reason', 'blocked_by', 'blocked_by_phone', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class TripDetailSerializer(serializers.ModelSerializer):
    """
    Comprehensive serializer for detailed trip view including route stops and seat blocks.
    """
    route = RouteListSerializer(read_only=True)
    bus = BusListSerializer(read_only=True)
    driver = DriverSerializer(read_only=True)
    conductor = ConductorSerializer(read_only=True)
    seat_blocks = TripSeatBlockSerializer(many=True, read_only=True)
    blocked_seats_count = serializers.IntegerField(source='seat_blocks.count', read_only=True)

    class Meta:
        model = Trip
        fields = [
            'id', 'trip_number', 'status',
            'route', 'bus', 'driver', 'conductor',
            'departure_datetime', 'arrival_datetime',
            'base_fare', 'blocked_seats_count', 'seat_blocks',
            'created_at', 'updated_at'
        ]


class TripCreateSerializer(serializers.Serializer):
    """
    Payload serializer for scheduling a new trip.
    """
    route = serializers.UUIDField()
    bus = serializers.UUIDField()
    driver = serializers.UUIDField(required=False, allow_null=True, default=None)
    conductor = serializers.UUIDField(required=False, allow_null=True, default=None)
    departure_datetime = serializers.DateTimeField()
    arrival_datetime = serializers.DateTimeField()
    base_fare = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    trip_number = serializers.CharField(max_length=40, required=False, allow_blank=True, default=None)


class TripUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer for modifying trip schedule or operational status.
    """
    class Meta:
        model = Trip
        fields = [
            'route', 'bus', 'driver', 'conductor',
            'departure_datetime', 'arrival_datetime',
            'base_fare', 'status'
        ]
        extra_kwargs = {
            'route': {'required': False},
            'bus': {'required': False},
            'departure_datetime': {'required': False},
            'arrival_datetime': {'required': False},
            'base_fare': {'required': False},
            'status': {'required': False},
        }


class BlockSeatInputSerializer(serializers.Serializer):
    """
    Payload serializer for blocking a seat on a trip.
    """
    seat_id = serializers.UUIDField()
    reason = serializers.CharField(max_length=150, default="Operational Block", required=False)


class FareRuleSerializer(serializers.ModelSerializer):
    """
    Serializer for explicit segment fare rules.
    """
    route_code = serializers.CharField(source='route.code', read_only=True)
    from_city = serializers.CharField(source='from_stop.terminal.city', read_only=True)
    to_city = serializers.CharField(source='to_stop.terminal.city', read_only=True)

    class Meta:
        model = FareRule
        fields = [
            'id', 'route', 'route_code',
            'from_stop', 'from_city', 'from_stop_order',
            'to_stop', 'to_city', 'to_stop_order',
            'seat_type', 'fare_amount', 'created_at'
        ]
        read_only_fields = ['id', 'from_stop_order', 'to_stop_order', 'created_at']


class FareCalculationQuerySerializer(serializers.Serializer):
    """
    Query parameters for segment fare calculation endpoint.
    """
    from_stop = serializers.UUIDField(help_text="UUID of departure RouteStop")
    to_stop = serializers.UUIDField(help_text="UUID of arrival RouteStop")
    seat_type = serializers.ChoiceField(choices=SeatType.choices, required=False, allow_null=True, default=None)
