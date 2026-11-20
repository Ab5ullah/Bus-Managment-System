"""
DRF Serializers for Seat Locking, Bookings, Passenger Allocations, and Counter Sales.
"""
from rest_framework import serializers
from common.constants import BookingStatus, BookingSeatStatus, PaymentMethod, Gender
from .models import Booking, BookingSeat, SeatLock


class SeatLockRequestSerializer(serializers.Serializer):
    """Payload to acquire temporary locks on up to 6 seats for 10 minutes."""
    trip_id = serializers.UUIDField(required=True)
    seat_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=6,
        required=True
    )
    from_stop = serializers.UUIDField(required=True)
    to_stop = serializers.UUIDField(required=True)
    lock_duration_minutes = serializers.IntegerField(
        required=False,
        default=10,
        min_value=1,
        max_value=30
    )


class UnlockSeatsRequestSerializer(serializers.Serializer):
    """Payload to release temporary seat locks."""
    lock_token = serializers.UUIDField(required=True)


class PassengerAssignmentSerializer(serializers.Serializer):
    """Individual passenger allocation mapped to a specific seat."""
    seat_id = serializers.UUIDField(required=True)
    passenger_name = serializers.CharField(max_length=120, required=True)
    gender = serializers.ChoiceField(choices=Gender.choices, default=Gender.MALE)
    age = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=120)
    cnic_or_id = serializers.CharField(required=False, allow_blank=True, default='')


class CreateBookingRequestSerializer(serializers.Serializer):
    """Payload to confirm a PENDING booking using held SeatLocks."""
    trip_id = serializers.UUIDField(required=True)
    lock_token = serializers.UUIDField(required=True)
    from_stop = serializers.UUIDField(required=True)
    to_stop = serializers.UUIDField(required=True)
    passenger_name = serializers.CharField(max_length=120, required=True)
    passenger_phone = serializers.CharField(max_length=30, required=True)
    passenger_email = serializers.EmailField(required=False, allow_blank=True, default='')
    passengers = PassengerAssignmentSerializer(many=True, required=True)
    payment_method = serializers.ChoiceField(choices=PaymentMethod.choices, default=PaymentMethod.CARD_ONLINE)


class CounterBookingRequestSerializer(serializers.Serializer):
    """Payload for Walk-In counter ticketing by Counter Agents or Operators."""
    trip_id = serializers.UUIDField(required=True)
    from_stop = serializers.UUIDField(required=True)
    to_stop = serializers.UUIDField(required=True)
    passenger_name = serializers.CharField(max_length=120, required=True)
    passenger_phone = serializers.CharField(max_length=30, required=True)
    passenger_email = serializers.EmailField(required=False, allow_blank=True, default='')
    passengers = PassengerAssignmentSerializer(many=True, required=True)
    payment_method = serializers.ChoiceField(choices=PaymentMethod.choices, default=PaymentMethod.CASH_COUNTER)


class BookingSeatDetailSerializer(serializers.ModelSerializer):
    seat_number = serializers.CharField(source='seat.seat_number', read_only=True)
    deck = serializers.IntegerField(source='seat.deck', read_only=True)
    row = serializers.IntegerField(source='seat.row', read_only=True)
    column = serializers.IntegerField(source='seat.column', read_only=True)
    seat_type = serializers.CharField(source='seat.seat_type', read_only=True)
    is_ladies_only = serializers.BooleanField(source='seat.is_ladies_only', read_only=True)

    class Meta:
        model = BookingSeat
        fields = [
            'id', 'seat_id', 'seat_number', 'deck', 'row', 'column',
            'seat_type', 'is_ladies_only', 'passenger_name', 'gender',
            'age', 'cnic_or_id', 'price', 'status'
        ]


class BookingDetailSerializer(serializers.ModelSerializer):
    seats = BookingSeatDetailSerializer(many=True, read_only=True)
    trip_number = serializers.CharField(source='trip.trip_number', read_only=True)
    route_name = serializers.CharField(source='trip.route.name', read_only=True)
    origin_city = serializers.CharField(source='from_stop.terminal.city', read_only=True)
    origin_terminal = serializers.CharField(source='from_stop.terminal.name', read_only=True)
    destination_city = serializers.CharField(source='to_stop.terminal.city', read_only=True)
    destination_terminal = serializers.CharField(source='to_stop.terminal.name', read_only=True)
    bus_registration = serializers.CharField(source='trip.bus.registration_number', read_only=True)
    bus_type = serializers.CharField(source='trip.bus.bus_type', read_only=True)

    class Meta:
        model = Booking
        fields = [
            'id', 'pnr', 'status', 'passenger_name', 'passenger_phone', 'passenger_email',
            'trip_id', 'trip_number', 'route_name', 'bus_registration', 'bus_type',
            'origin_city', 'origin_terminal', 'destination_city', 'destination_terminal',
            'from_stop_order', 'to_stop_order',
            'total_gross_fare', 'discount_amount', 'tax_amount', 'service_fee', 'net_amount',
            'payment_method', 'expires_at', 'confirmed_at', 'created_at',
            'seats'
        ]
