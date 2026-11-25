"""
DRF Serializers for Module 7: Payments, Webhooks & Financial Reconciliation.
"""
from decimal import Decimal
from rest_framework import serializers
from common.constants import PaymentMethod, PaymentStatus
from .models import Payment, PaymentReceipt


class PaymentReceiptSerializer(serializers.ModelSerializer):
    currency = serializers.CharField(source='payment.currency', read_only=True)
    booking_pnr = serializers.CharField(source='payment.booking.pnr', read_only=True)

    class Meta:
        model = PaymentReceipt
        fields = [
            'id',
            'receipt_number',
            'booking_pnr',
            'gross_amount',
            'discount_amount',
            'tax_amount',
            'service_fee',
            'net_amount',
            'currency',
            'issued_at',
        ]
        read_only_fields = fields


class PaymentDetailSerializer(serializers.ModelSerializer):
    booking_pnr = serializers.CharField(source='booking.pnr', read_only=True)
    receipt = PaymentReceiptSerializer(read_only=True)

    class Meta:
        model = Payment
        fields = [
            'id',
            'booking_pnr',
            'amount',
            'currency',
            'method',
            'status',
            'gateway_name',
            'gateway_reference',
            'idempotency_key',
            'failure_reason',
            'paid_at',
            'receipt',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class PaymentInitiateSerializer(serializers.Serializer):
    booking_id = serializers.UUIDField(required=False, allow_null=True)
    pnr = serializers.CharField(required=False, max_length=16, allow_blank=True)
    method = serializers.ChoiceField(
        choices=PaymentMethod.choices,
        default=PaymentMethod.CARD_ONLINE
    )
    gateway_name = serializers.CharField(required=False, allow_blank=True, default='', max_length=50)
    checkout_mode = serializers.ChoiceField(
        choices=['checkout_session', 'payment_intent'],
        required=False,
        default='checkout_session'
    )
    idempotency_key = serializers.CharField(required=False, max_length=120, allow_blank=True)
    return_url = serializers.CharField(required=False, max_length=500, allow_blank=True)
    metadata = serializers.DictField(required=False, default=dict)

    def validate(self, attrs):
        if not attrs.get('booking_id') and not attrs.get('pnr'):
            raise serializers.ValidationError("Either 'booking_id' or 'pnr' must be supplied.")
        return attrs


class ConfirmMockPaymentSerializer(serializers.Serializer):
    payment_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=['SUCCESS', 'FAILED'], default='SUCCESS')
    failure_reason = serializers.CharField(required=False, allow_blank=True, default='')


class RefundPaymentSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        allow_null=True
    )
    reason = serializers.CharField(required=False, allow_blank=True, default='')


class VerifyStripeSessionSerializer(serializers.Serializer):
    session_id = serializers.CharField(max_length=255, required=True)
