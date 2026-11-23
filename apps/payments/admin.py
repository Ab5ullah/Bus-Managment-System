from django.contrib import admin
from .models import Payment, PaymentReceipt


class PaymentReceiptInline(admin.StackedInline):
    model = PaymentReceipt
    extra = 0
    readonly_fields = [
        'receipt_number', 'gross_amount', 'discount_amount',
        'tax_amount', 'service_fee', 'net_amount', 'issued_at'
    ]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'booking_pnr', 'amount', 'currency', 'method',
        'gateway_name', 'status', 'paid_at', 'created_at'
    ]
    list_filter = ['status', 'method', 'gateway_name', 'currency']
    search_fields = ['id', 'booking__pnr', 'gateway_reference', 'idempotency_key']
    readonly_fields = ['id', 'created_at', 'updated_at']
    inlines = [PaymentReceiptInline]

    def booking_pnr(self, obj):
        return obj.booking.pnr
    booking_pnr.short_description = "Booking PNR"


@admin.register(PaymentReceipt)
class PaymentReceiptAdmin(admin.ModelAdmin):
    list_display = [
        'receipt_number', 'payment_id', 'gross_amount', 'tax_amount',
        'service_fee', 'net_amount', 'issued_at'
    ]
    search_fields = ['receipt_number', 'payment__id', 'payment__booking__pnr']
    readonly_fields = ['issued_at', 'created_at', 'updated_at']
