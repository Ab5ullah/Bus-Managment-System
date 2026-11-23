from django.contrib import admin
from .models import Ticket


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = [
        'ticket_number', 'booking_pnr', 'seat_number',
        'passenger_name', 'status', 'boarded_at', 'boarded_by'
    ]
    list_filter = ['status', 'created_at', 'boarded_at']
    search_fields = [
        'ticket_number', 'booking_seat__booking__pnr',
        'booking_seat__passenger_name', 'booking_seat__seat__seat_number'
    ]
    readonly_fields = ['id', 'ticket_number', 'qr_token', 'created_at', 'updated_at']

    def booking_pnr(self, obj):
        return obj.booking.pnr
    booking_pnr.short_description = "Booking PNR"

    def seat_number(self, obj):
        return obj.seat_number
    seat_number.short_description = "Seat"

    def passenger_name(self, obj):
        return obj.passenger_name
    passenger_name.short_description = "Passenger"
