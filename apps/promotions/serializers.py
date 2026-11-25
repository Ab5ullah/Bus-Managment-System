"""
Serializers for Module 10: Promotions, Coupons & Dynamic Pricing.
"""
from decimal import Decimal
from rest_framework import serializers
from .models import Coupon, CouponUsage, DynamicPricingRule


class CouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = [
            'id',
            'code',
            'description',
            'discount_type',
            'discount_value',
            'min_booking_amount',
            'max_discount_amount',
            'valid_from',
            'valid_to',
            'max_total_uses',
            'max_uses_per_user',
            'current_usage_count',
            'is_active',
            'created_at',
        ]
        read_only_fields = ['id', 'current_usage_count', 'created_at']

    def validate(self, attrs):
        discount_type = attrs.get('discount_type', getattr(self.instance, 'discount_type', None))
        discount_value = attrs.get('discount_value', getattr(self.instance, 'discount_value', None))
        valid_from = attrs.get('valid_from', getattr(self.instance, 'valid_from', None))
        valid_to = attrs.get('valid_to', getattr(self.instance, 'valid_to', None))

        if discount_value is not None and discount_value <= Decimal('0.00'):
            raise serializers.ValidationError({"discount_value": "Discount value must be greater than 0.00."})
        if discount_type == 'PERCENTAGE' and discount_value is not None and discount_value > Decimal('100.00'):
            raise serializers.ValidationError({"discount_value": "Percentage discount cannot exceed 100.00%."})
        if valid_from and valid_to and valid_to <= valid_from:
            raise serializers.ValidationError({"valid_to": "valid_to must be strictly after valid_from."})
        return attrs


class CouponUsageSerializer(serializers.ModelSerializer):
    coupon_code = serializers.CharField(source='coupon.code', read_only=True)
    booking_pnr = serializers.CharField(source='booking.pnr', read_only=True)

    class Meta:
        model = CouponUsage
        fields = [
            'id',
            'coupon_code',
            'booking_pnr',
            'passenger_phone',
            'discount_applied',
            'used_at',
        ]


class DynamicPricingRuleSerializer(serializers.ModelSerializer):
    route_code = serializers.CharField(source='route.code', read_only=True, default='GLOBAL')

    class Meta:
        model = DynamicPricingRule
        fields = [
            'id',
            'name',
            'rule_type',
            'route',
            'route_code',
            'min_occupancy_percentage',
            'hours_before_departure_min',
            'hours_before_departure_max',
            'multiplier',
            'priority',
            'is_active',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class ValidateCouponRequestSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=50, required=True)
    gross_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=True, min_value=Decimal('0.01'))
    passenger_phone = serializers.CharField(max_length=30, required=False, allow_blank=True, default='')


class ApplyCouponRequestSerializer(serializers.Serializer):
    pnr = serializers.CharField(max_length=16, required=True)
    code = serializers.CharField(max_length=50, required=True)


class RemoveCouponRequestSerializer(serializers.Serializer):
    pnr = serializers.CharField(max_length=16, required=True)
