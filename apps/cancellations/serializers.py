"""
Serializers for Module 9: Cancellation & Refund Engine.
"""
from rest_framework import serializers
from .models import CancellationPolicy, Refund


class CancellationPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = CancellationPolicy
        fields = [
            'id',
            'name',
            'min_hours_before_departure',
            'refund_percentage',
            'cancellation_fee_fixed',
            'is_active',
            'description',
            'created_at',
        ]


class RefundSerializer(serializers.ModelSerializer):
    booking_pnr = serializers.CharField(source='booking.pnr', read_only=True)
    passenger_name = serializers.CharField(source='booking.passenger_name', read_only=True)
    policy_name = serializers.CharField(source='policy_applied.name', read_only=True, default=None)
    seat_numbers = serializers.SerializerMethodField()

    class Meta:
        model = Refund
        fields = [
            'id',
            'refund_number',
            'booking_pnr',
            'passenger_name',
            'policy_name',
            'seat_numbers',
            'gross_fare_refunded',
            'tax_refunded',
            'cancellation_fee_charged',
            'net_refund_amount',
            'status',
            'is_operator_cancellation',
            'reason',
            'processed_at',
        ]

    def get_seat_numbers(self, obj):
        return list(obj.seats.values_list('seat__seat_number', flat=True))


class EstimateRefundRequestSerializer(serializers.Serializer):
    pnr = serializers.CharField(max_length=16, required=True)
    seat_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
        help_text="Optional list of specific seat UUIDs for partial cancellation estimation"
    )


class CancelBookingRequestSerializer(serializers.Serializer):
    pnr = serializers.CharField(max_length=16, required=True)
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')


class CancelSeatsRequestSerializer(serializers.Serializer):
    pnr = serializers.CharField(max_length=16, required=True)
    seat_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=True,
        min_length=1,
        help_text="UUIDs of specific seats to cancel"
    )
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')


class OperatorCancelTripRequestSerializer(serializers.Serializer):
    trip_id = serializers.UUIDField(required=True)
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')
