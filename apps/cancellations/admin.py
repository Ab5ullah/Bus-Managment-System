"""
Admin registration for Module 9: Cancellation & Refund Engine.
"""
from django.contrib import admin
from .models import CancellationPolicy, Refund


@admin.register(CancellationPolicy)
class CancellationPolicyAdmin(admin.ModelAdmin):
    list_display = [
        'name',
        'min_hours_before_departure',
        'refund_percentage',
        'cancellation_fee_fixed',
        'is_active',
    ]
    list_filter = ['is_active']
    search_fields = ['name', 'description']


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = [
        'refund_number',
        'booking',
        'net_refund_amount',
        'status',
        'is_operator_cancellation',
        'processed_at',
    ]
    list_filter = ['status', 'is_operator_cancellation', 'processed_at']
    search_fields = ['refund_number', 'booking__pnr', 'reason']
    readonly_fields = ['refund_number', 'processed_at']
