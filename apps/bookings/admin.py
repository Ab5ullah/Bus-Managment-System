"""
Django Admin registration for Bookings, BookingSeats, and SeatLocks.
"""
from django.contrib import admin
from .models import Booking, BookingSeat, SeatLock


class BookingSeatInline(admin.TabularInline):
    model = BookingSeat
    extra = 0
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ('pnr', 'passenger_name', 'passenger_phone', 'trip', 'status', 'net_amount', 'created_at')
    list_filter = ('status', 'trip__route', 'created_at')
    search_fields = ('pnr', 'passenger_name', 'passenger_phone', 'passenger_email')
    readonly_fields = ('pnr', 'created_at', 'updated_at')
    inlines = [BookingSeatInline]


@admin.register(BookingSeat)
class BookingSeatAdmin(admin.ModelAdmin):
    list_display = ('booking', 'seat', 'passenger_name', 'gender', 'price', 'status')
    list_filter = ('status', 'gender')
    search_fields = ('booking__pnr', 'passenger_name', 'seat__seat_number')


@admin.register(SeatLock)
class SeatLockAdmin(admin.ModelAdmin):
    list_display = ('trip', 'seat', 'lock_token', 'locked_until', 'is_expired')
    list_filter = ('trip__route', 'locked_until')
    search_fields = ('lock_token', 'seat__seat_number')
