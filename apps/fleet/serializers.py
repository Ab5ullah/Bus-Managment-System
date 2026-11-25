"""
Serializers for Fleet Management: Buses, Dynamic Seat Layouts, and Crew.
"""
from rest_framework import serializers
from common.constants import BusType, BusStatus, SeatType
from .models import Bus, Seat, Driver, Conductor, CrewStatus
from .services import generate_bus_seat_matrix


class SeatSerializer(serializers.ModelSerializer):
    """Serializer for individual bus seats."""
    seat_type_display = serializers.CharField(source='get_seat_type_display', read_only=True)

    class Meta:
        model = Seat
        fields = [
            'id', 'seat_number', 'row', 'column', 'deck',
            'seat_type', 'seat_type_display', 'is_ladies_only', 'is_active'
        ]
        read_only_fields = ['id', 'seat_number', 'row', 'column', 'deck']


class SeatUpdateSerializer(serializers.ModelSerializer):
    """Allows operators to update seat demographic attributes or hardware status."""

    class Meta:
        model = Seat
        fields = ['seat_type', 'is_ladies_only', 'is_active']


class BusListSerializer(serializers.ModelSerializer):
    """Summary serializer for bus listings."""
    bus_type_display = serializers.CharField(source='get_bus_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Bus
        fields = [
            'id', 'registration_number', 'name', 'bus_type',
            'bus_type_display', 'total_seats', 'amenities', 'status',
            'status_display', 'is_active', 'created_at'
        ]


class BusDetailSerializer(serializers.ModelSerializer):
    """Detailed bus serializer including full nested seat map."""
    bus_type_display = serializers.CharField(source='get_bus_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    seats = SeatSerializer(many=True, read_only=True)

    class Meta:
        model = Bus
        fields = [
            'id', 'registration_number', 'name', 'bus_type',
            'bus_type_display', 'total_seats', 'amenities', 'status',
            'status_display', 'is_active', 'seats', 'created_at'
        ]


class LayoutSpecSerializer(serializers.Serializer):
    """Optional nested spec for instant seat layout generation during bus creation."""
    layout_type = serializers.ChoiceField(
        choices=["2x2", "2x1", "1x1", "sleeper_2x1"],
        default="2x2"
    )
    rows = serializers.IntegerField(min_value=1, max_value=30, default=10)
    deck_count = serializers.IntegerField(min_value=1, max_value=2, default=1)
    ladies_rows = serializers.IntegerField(min_value=0, max_value=10, default=0)


class BusCreateSerializer(serializers.ModelSerializer):
    """Bus creation serializer with optional automatic seat layout generator."""
    layout = LayoutSpecSerializer(required=False, write_only=True)

    class Meta:
        model = Bus
        fields = [
            'id', 'registration_number', 'name', 'bus_type',
            'amenities', 'status', 'is_active', 'layout'
        ]
        read_only_fields = ['id']

    def validate_registration_number(self, value):
        cleaned = value.strip().upper()
        if Bus.objects.filter(registration_number=cleaned).exists():
            raise serializers.ValidationError("A bus with this registration number already exists.")
        return cleaned

    def create(self, validated_data):
        layout_spec = validated_data.pop('layout', None)
        bus = Bus.objects.create(**validated_data)

        if layout_spec:
            generate_bus_seat_matrix(
                bus=bus,
                layout_type=layout_spec.get('layout_type', '2x2'),
                rows=layout_spec.get('rows', 10),
                deck_count=layout_spec.get('deck_count', 1),
                ladies_rows=layout_spec.get('ladies_rows', 0)
            )
        return bus


class GenerateSeatsSerializer(LayoutSpecSerializer):
    """Request serializer for the generate-seats endpoint."""
    pass


class DriverSerializer(serializers.ModelSerializer):
    """CRUD serializer for bus drivers."""
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Driver
        fields = [
            'id', 'user', 'full_name', 'license_number', 'phone',
            'experience_years', 'status', 'status_display', 'is_active', 'created_at'
        ]
        read_only_fields = ['id', 'status_display', 'created_at']

    def validate_license_number(self, value):
        cleaned = value.strip().upper()
        query = Driver.objects.filter(license_number=cleaned)
        if self.instance:
            query = query.exclude(id=self.instance.id)
        if query.exists():
            raise serializers.ValidationError("A driver with this license number already exists.")
        return cleaned

    def validate_phone(self, value):
        cleaned = value.strip().replace(" ", "").replace("-", "")
        query = Driver.objects.filter(phone=cleaned)
        if self.instance:
            query = query.exclude(id=self.instance.id)
        if query.exists():
            raise serializers.ValidationError("A driver with this phone number already exists.")
        return cleaned


class ConductorSerializer(serializers.ModelSerializer):
    """CRUD serializer for conductors."""
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Conductor
        fields = [
            'id', 'user', 'full_name', 'employee_id', 'phone',
            'status', 'status_display', 'is_active', 'created_at'
        ]
        read_only_fields = ['id', 'status_display', 'created_at']

    def validate_employee_id(self, value):
        cleaned = value.strip().upper()
        query = Conductor.objects.filter(employee_id=cleaned)
        if self.instance:
            query = query.exclude(id=self.instance.id)
        if query.exists():
            raise serializers.ValidationError("A conductor with this employee ID already exists.")
        return cleaned
