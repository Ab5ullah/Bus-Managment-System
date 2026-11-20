"""
Django Admin Configuration for Trips, Seat Blocks, and Fare Rules.
"""
from django.contrib import admin
from .models import Trip, TripSeatBlock, FareRule


class TripSeatBlockInline(admin.TabularInline):
    model = TripSeatBlock
    extra = 0
    fields = ('seat', 'reason', 'blocked_by', 'created_at')
    readonly_fields = ('created_at',)


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = (
        'trip_number', 'route', 'bus', 'driver',
        'departure_datetime', 'arrival_datetime',
        'base_fare', 'status', 'blocked_seats_count'
    )
    list_filter = ('status', 'route', 'bus__bus_type', 'departure_datetime')
    search_fields = ('trip_number', 'route__code', 'bus__registration_number', 'driver__full_name')
    ordering = ('-departure_datetime',)
    inlines = [TripSeatBlockInline]

    def blocked_seats_count(self, obj):
        return obj.seat_blocks.count()
    blocked_seats_count.short_description = "Blocked Seats"


@admin.register(TripSeatBlock)
class TripSeatBlockAdmin(admin.ModelAdmin):
    list_display = ('trip', 'seat', 'reason', 'blocked_by', 'created_at')
    list_filter = ('trip__status', 'created_at')
    search_fields = ('trip__trip_number', 'seat__seat_number', 'reason')
    ordering = ('-created_at',)


@admin.register(FareRule)
class FareRuleAdmin(admin.ModelAdmin):
    list_display = (
        'route', 'from_stop_desc', 'to_stop_desc',
        'seat_type', 'fare_amount', 'created_at'
    )
    list_filter = ('route', 'seat_type')
    search_fields = ('route__code', 'from_stop__terminal__city', 'to_stop__terminal__city')

    def from_stop_desc(self, obj):
        return f"{obj.from_stop.terminal.city} (#{obj.from_stop_order})"
    from_stop_desc.short_description = "From Stop"

    def to_stop_desc(self, obj):
        return f"{obj.to_stop.terminal.city} (#{obj.to_stop_order})"
    to_stop_desc.short_description = "To Stop"
