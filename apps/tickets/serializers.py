"""
DRF Serializers for Module 8: Tickets, QR Codes & Conductor Validation.
"""
from rest_framework import serializers
from .models import Ticket
from .crypto import generate_qr_code_image_base64


class TicketSerializer(serializers.ModelSerializer):
    booking_pnr = serializers.CharField(source='booking.pnr', read_only=True)
    seat_number = serializers.CharField(source='seat.seat_number', read_only=True)
    is_ladies_only = serializers.BooleanField(source='seat.is_ladies_only', read_only=True)
    passenger_name = serializers.CharField(read_only=True)
    gender = serializers.CharField(source='booking_seat.gender', read_only=True)
    age = serializers.IntegerField(source='booking_seat.age', read_only=True)
    cnic_or_id = serializers.CharField(source='booking_seat.cnic_or_id', read_only=True)
    passenger_phone = serializers.CharField(source='booking.passenger_phone', read_only=True)
    from_terminal = serializers.CharField(source='booking_seat.from_stop.terminal.city', read_only=True)
    to_terminal = serializers.CharField(source='booking_seat.to_stop.terminal.city', read_only=True)
    trip_id = serializers.UUIDField(source='trip.id', read_only=True)
    trip_number = serializers.CharField(source='trip.trip_number', read_only=True)
    bus_registration = serializers.CharField(source='trip.bus.registration_number', read_only=True)
    departure_datetime = serializers.DateTimeField(source='trip.departure_datetime', read_only=True)
    boarded_by_phone = serializers.CharField(source='boarded_by.phone', read_only=True, allow_null=True)
    qr_data_uri = serializers.SerializerMethodField()

    class Meta:
        model = Ticket
        fields = [
            'id',
            'ticket_number',
            'booking_pnr',
            'seat_number',
            'is_ladies_only',
            'passenger_name',
            'gender',
            'age',
            'cnic_or_id',
            'passenger_phone',
            'from_terminal',
            'to_terminal',
            'trip_id',
            'trip_number',
            'bus_registration',
            'departure_datetime',
            'status',
            'boarded_at',
            'boarded_by_phone',
            'qr_token',
            'qr_data_uri',
            'created_at',
        ]
        read_only_fields = fields

    def get_qr_data_uri(self, obj) -> str:
        return generate_qr_code_image_base64(obj.qr_token)


class ValidateQrRequestSerializer(serializers.Serializer):
    qr_token = serializers.CharField(required=True, help_text="Cryptographically signed QR token string")
    trip_id = serializers.UUIDField(required=False, allow_null=True, help_text="Optional current trip ID for mismatch checking")


class BoardPassengerRequestSerializer(serializers.Serializer):
    qr_token = serializers.CharField(required=False, allow_blank=True, default='')
    ticket_number = serializers.CharField(required=False, allow_blank=True, default='')
    trip_id = serializers.UUIDField(required=False, allow_null=True)
    boarding_stop_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        if not attrs.get('qr_token') and not attrs.get('ticket_number'):
            raise serializers.ValidationError("Either 'qr_token' or 'ticket_number' must be provided.")
        return attrs
