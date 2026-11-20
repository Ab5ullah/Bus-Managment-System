"""
Django Admin Configuration for Fleet Management (Buses, Seats, Drivers, Conductors).
"""
from django.contrib import admin
from .models import Bus, Seat, Driver, Conductor
from .services import generate_bus_seat_matrix


class SeatInline(admin.TabularInline):
    model = Seat
    extra = 0
    fields = ('seat_number', 'row', 'column', 'deck', 'seat_type', 'is_ladies_only', 'is_active')
    readonly_fields = ('seat_number', 'row', 'column', 'deck')
    show_change_link = True
    can_delete = False


@admin.register(Bus)
class BusAdmin(admin.ModelAdmin):
    list_display = (
        'registration_number', 'name', 'bus_type', 'total_seats',
        'status', 'is_active', 'created_at'
    )
    list_filter = ('bus_type', 'status', 'is_active')
    search_fields = ('registration_number', 'name')
    inlines = [SeatInline]

    actions = ['generate_standard_40_seats', 'generate_luxury_30_seats']

    @admin.action(description="Generate Standard 2x2 Layout (40 seats, 2 ladies rows)")
    def generate_standard_40_seats(self, request, queryset):
        for bus in queryset:
            generate_bus_seat_matrix(bus=bus, layout_type="2x2", rows=10, ladies_rows=2)
        self.message_user(request, f"Generated 40-seat standard layout for {queryset.count()} bus(es).")

    @admin.action(description="Generate Luxury 2x1 Layout (30 seats, 2 ladies rows)")
    def generate_luxury_30_seats(self, request, queryset):
        for bus in queryset:
            generate_bus_seat_matrix(bus=bus, layout_type="2x1", rows=10, ladies_rows=2)
        self.message_user(request, f"Generated 30-seat luxury layout for {queryset.count()} bus(es).")


@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):
    list_display = (
        'bus', 'seat_number', 'row', 'column', 'deck',
        'seat_type', 'is_ladies_only', 'is_active'
    )
    list_filter = ('bus', 'seat_type', 'is_ladies_only', 'is_active', 'deck')
    search_fields = ('seat_number', 'bus__registration_number')
    ordering = ('bus', 'deck', 'row', 'column')

    actions = ['mark_ladies_only', 'unmark_ladies_only', 'mark_damaged', 'mark_operational']

    @admin.action(description="Mark selected seats as Ladies-Only")
    def mark_ladies_only(self, request, queryset):
        queryset.update(is_ladies_only=True)

    @admin.action(description="Remove Ladies-Only restriction")
    def unmark_ladies_only(self, request, queryset):
        queryset.update(is_ladies_only=False)

    @admin.action(description="Mark selected seats as Damaged/Inactive")
    def mark_damaged(self, request, queryset):
        queryset.update(is_active=False)

    @admin.action(description="Mark selected seats as Operational/Active")
    def mark_operational(self, request, queryset):
        queryset.update(is_active=True)


@admin.register(Driver)
class DriverAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'license_number', 'phone', 'experience_years', 'status', 'is_active')
    list_filter = ('status', 'is_active')
    search_fields = ('full_name', 'license_number', 'phone')


@admin.register(Conductor)
class ConductorAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'employee_id', 'phone', 'status', 'is_active')
    list_filter = ('status', 'is_active')
    search_fields = ('full_name', 'employee_id', 'phone')
