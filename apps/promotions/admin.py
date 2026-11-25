"""
Admin configuration for Module 10: Promotions, Coupons & Dynamic Pricing.
"""
from django.contrib import admin
from .models import Coupon, CouponUsage, DynamicPricingRule


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = [
        'code', 'discount_type', 'discount_value',
        'min_booking_amount', 'max_discount_amount',
        'current_usage_count', 'max_total_uses', 'valid_to', 'is_active'
    ]
    list_filter = ['discount_type', 'is_active', 'valid_from', 'valid_to']
    search_fields = ['code', 'description']


@admin.register(CouponUsage)
class CouponUsageAdmin(admin.ModelAdmin):
    list_display = ['coupon', 'booking', 'passenger_phone', 'discount_applied', 'used_at']
    list_filter = ['used_at']
    search_fields = ['coupon__code', 'booking__pnr', 'passenger_phone']


@admin.register(DynamicPricingRule)
class DynamicPricingRuleAdmin(admin.ModelAdmin):
    list_display = ['name', 'rule_type', 'route', 'multiplier', 'priority', 'is_active']
    list_filter = ['rule_type', 'is_active']
    search_fields = ['name']
