"""
Django Admin Configuration for Terminals, Sequenced Routes, and Stops.
"""
from django.contrib import admin
from .models import Terminal, Route, RouteStop


class RouteStopInline(admin.TabularInline):
    model = RouteStop
    extra = 0
    fields = ('stop_order', 'terminal', 'distance_from_origin_km', 'offset_minutes')
    ordering = ('stop_order',)
    show_change_link = True


@admin.register(Terminal)
class TerminalAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'city', 'address', 'contact_number', 'is_active', 'created_at')
    list_filter = ('city', 'is_active')
    search_fields = ('name', 'code', 'city', 'address')
    ordering = ('city', 'name')


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = (
        'code', 'name', 'get_origin', 'get_destination',
        'total_distance_km', 'estimated_duration_minutes',
        'stops_count', 'is_active', 'created_at'
    )
    list_filter = ('origin__city', 'destination__city', 'is_active')
    search_fields = ('code', 'name', 'origin__city', 'destination__city')
    inlines = [RouteStopInline]

    def get_origin(self, obj):
        return f"{obj.origin.city} ({obj.origin.code})"
    get_origin.short_description = "Origin"

    def get_destination(self, obj):
        return f"{obj.destination.city} ({obj.destination.code})"
    get_destination.short_description = "Destination"

    def stops_count(self, obj):
        return obj.stops.count()
    stops_count.short_description = "Stops"


@admin.register(RouteStop)
class RouteStopAdmin(admin.ModelAdmin):
    list_display = ('route', 'stop_order', 'terminal', 'distance_from_origin_km', 'offset_minutes')
    list_filter = ('route', 'terminal__city')
    search_fields = ('route__code', 'terminal__name', 'terminal__code')
    ordering = ('route', 'stop_order')
