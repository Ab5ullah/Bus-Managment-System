"""
Views for Fleet Management: Buses, Dynamic Seat Layouts, and Crew.
"""
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiResponse

from common.responses import api_success
from accounts.permissions import IsOperator
from .models import Bus, Seat, Driver, Conductor
from .services import generate_bus_seat_matrix
from .serializers import (
    BusListSerializer,
    BusDetailSerializer,
    BusCreateSerializer,
    GenerateSeatsSerializer,
    SeatSerializer,
    SeatUpdateSerializer,
    DriverSerializer,
    ConductorSerializer,
)


class BusViewSet(viewsets.ModelViewSet):
    """
    CRUD viewset for physical buses in the fleet.
    Read actions are open for catalog display; creation and management require Operator privileges.
    """
    queryset = Bus.objects.prefetch_related('seats').all()
    filterset_fields = ['bus_type', 'status', 'is_active']
    search_fields = ['registration_number', 'name']
    ordering_fields = ['registration_number', 'total_seats', 'created_at']

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [AllowAny()]
        return [IsOperator()]

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return BusDetailSerializer
        if self.action == 'create':
            return BusCreateSerializer
        if self.action == 'generate_seats':
            return GenerateSeatsSerializer
        return BusListSerializer

    @extend_schema(tags=["Fleet: Buses"])
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return api_success(data=serializer.data)

    @extend_schema(tags=["Fleet: Buses"])
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return api_success(data=serializer.data)

    @extend_schema(
        tags=["Fleet: Buses"],
        responses={201: BusDetailSerializer}
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        bus = serializer.save()
        return api_success(
            data=BusDetailSerializer(bus).data,
            message=f"Bus {bus.registration_number} created with {bus.total_seats} seats configured.",
            status_code=status.HTTP_201_CREATED
        )

    @extend_schema(tags=["Fleet: Buses"])
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return api_success(data=serializer.data, message="Bus details updated.")

    @extend_schema(
        tags=["Fleet: Buses"],
        summary="Generate Seat Layout Matrix",
        description="Generates or regenerates physical seat grid layout (e.g. 2x2, 2x1 luxury, sleeper) for this bus.",
        request=GenerateSeatsSerializer,
        responses={200: BusDetailSerializer}
    )
    @action(detail=True, methods=['post'], url_path='generate-seats')
    def generate_seats(self, request, pk=None):
        bus = self.get_object()
        serializer = GenerateSeatsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        layout_type = serializer.validated_data.get('layout_type', '2x2')
        rows = serializer.validated_data.get('rows', 10)
        deck_count = serializer.validated_data.get('deck_count', 1)
        ladies_rows = serializer.validated_data.get('ladies_rows', 0)

        generate_bus_seat_matrix(
            bus=bus,
            layout_type=layout_type,
            rows=rows,
            deck_count=deck_count,
            ladies_rows=ladies_rows
        )

        fresh_bus = Bus.objects.prefetch_related('seats').get(id=bus.id)
        return api_success(
            data=BusDetailSerializer(fresh_bus).data,
            message=f"Generated {fresh_bus.total_seats} seats ({layout_type} layout) for bus {fresh_bus.registration_number}."
        )

    @extend_schema(
        tags=["Fleet: Buses"],
        summary="Update Individual Seat Attributes",
        description="Configure demographic rules (is_ladies_only) or hardware status (is_active) for a specific seat.",
        request=SeatUpdateSerializer,
        responses={200: SeatSerializer}
    )
    @action(detail=True, methods=['patch'], url_path='seats/(?P<seat_id>[^/.]+)')
    def update_seat(self, request, pk=None, seat_id=None):
        bus = self.get_object()
        seat = get_object_or_404(Seat, bus=bus, id=seat_id)

        serializer = SeatUpdateSerializer(seat, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return api_success(
            data=SeatSerializer(seat).data,
            message=f"Seat {seat.seat_number} attributes updated successfully."
        )


class DriverViewSet(viewsets.ModelViewSet):
    """
    CRUD viewset for licensed bus drivers. Restricted to operators and admins.
    """
    permission_classes = [IsOperator]
    queryset = Driver.objects.all()
    serializer_class = DriverSerializer
    filterset_fields = ['status', 'is_active']
    search_fields = ['full_name', 'license_number', 'phone']
    ordering_fields = ['full_name', 'experience_years', 'created_at']

    @extend_schema(tags=["Fleet: Crew"])
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return api_success(data=serializer.data)

    @extend_schema(tags=["Fleet: Crew"])
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return api_success(data=serializer.data)

    @extend_schema(tags=["Fleet: Crew"])
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return api_success(
            data=serializer.data,
            message="Driver profile created successfully.",
            status_code=status.HTTP_201_CREATED
        )

    @extend_schema(tags=["Fleet: Crew"])
    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=kwargs.get('partial', False))
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return api_success(data=serializer.data, message="Driver profile updated.")

    @extend_schema(tags=["Fleet: Crew"])
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return api_success(message="Driver record deleted successfully.")


class ConductorViewSet(viewsets.ModelViewSet):
    """
    CRUD viewset for bus conductors and validators. Restricted to operators and admins.
    """
    permission_classes = [IsOperator]
    queryset = Conductor.objects.all()
    serializer_class = ConductorSerializer
    filterset_fields = ['status', 'is_active']
    search_fields = ['full_name', 'employee_id', 'phone']
    ordering_fields = ['full_name', 'created_at']

    @extend_schema(tags=["Fleet: Crew"])
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return api_success(data=serializer.data)

    @extend_schema(tags=["Fleet: Crew"])
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return api_success(data=serializer.data)

    @extend_schema(tags=["Fleet: Crew"])
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return api_success(
            data=serializer.data,
            message="Conductor profile created successfully.",
            status_code=status.HTTP_201_CREATED
        )

    @extend_schema(tags=["Fleet: Crew"])
    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=kwargs.get('partial', False))
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return api_success(data=serializer.data, message="Conductor profile updated.")

    @extend_schema(tags=["Fleet: Crew"])
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return api_success(message="Conductor record deleted successfully.")
