"""
Views for Trips, Seat Blocking, and Fare Rules.
"""
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiResponse

from common.responses import api_success
from accounts.permissions import IsOperator
from routes.models import Route, RouteStop
from fleet.models import Bus, Seat, Driver, Conductor
from .models import Trip, TripSeatBlock, FareRule
from .services import TripScheduleService, TripSeatBlockService, FareService
from .serializers import (
    TripListSerializer,
    TripDetailSerializer,
    TripCreateSerializer,
    TripUpdateSerializer,
    TripSeatBlockSerializer,
    BlockSeatInputSerializer,
    FareRuleSerializer,
    FareCalculationQuerySerializer,
)


class TripViewSet(viewsets.ModelViewSet):
    """
    CRUD viewset for Scheduled Trips.
    Publicly listable and searchable; trip scheduling and seat blocking restricted to operators.
    """
    queryset = Trip.objects.select_related(
        'route__origin', 'route__destination', 'bus', 'driver', 'conductor'
    ).prefetch_related('seat_blocks__seat').all()
    filterset_fields = ['status', 'bus', 'driver', 'route']
    search_fields = ['trip_number', 'route__code', 'route__name', 'bus__registration_number']
    ordering_fields = ['departure_datetime', 'arrival_datetime', 'base_fare', 'created_at']

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'calculate_fare']:
            return [AllowAny()]
        return [IsOperator()]

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return TripDetailSerializer
        if self.action == 'create':
            return TripCreateSerializer
        if self.action in ['update', 'partial_update']:
            return TripUpdateSerializer
        if self.action == 'block_seat':
            return BlockSeatInputSerializer
        return TripListSerializer

    @extend_schema(tags=["Trips: Scheduled Journeys"])
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return api_success(data=serializer.data, message="Trips retrieved successfully.")

    @extend_schema(tags=["Trips: Scheduled Journeys"])
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return api_success(data=serializer.data, message="Trip details retrieved successfully.")

    @extend_schema(tags=["Trips: Scheduled Journeys"])
    def create(self, request, *args, **kwargs):
        serializer = TripCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        route = get_object_or_404(Route, id=data['route'])
        bus = get_object_or_404(Bus, id=data['bus'])
        driver = get_object_or_404(Driver, id=data['driver']) if data.get('driver') else None
        conductor = get_object_or_404(Conductor, id=data['conductor']) if data.get('conductor') else None

        trip = TripScheduleService.create_trip(
            route=route,
            bus=bus,
            driver=driver,
            conductor=conductor,
            departure_datetime=data['departure_datetime'],
            arrival_datetime=data['arrival_datetime'],
            base_fare=data['base_fare'],
            trip_number=data.get('trip_number')
        )

        return api_success(
            data=TripDetailSerializer(trip).data,
            message=f"Trip '{trip.trip_number}' scheduled successfully on route '{route.code}'.",
            status_code=status.HTTP_201_CREATED
        )

    @extend_schema(tags=["Trips: Scheduled Journeys"])
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = TripUpdateSerializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        updated_trip = TripScheduleService.update_trip(instance, **serializer.validated_data)
        return api_success(
            data=TripDetailSerializer(updated_trip).data,
            message=f"Trip '{updated_trip.trip_number}' updated successfully."
        )

    @extend_schema(tags=["Trips: Scheduled Journeys"])
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        trip_num = instance.trip_number
        TripScheduleService.cancel_trip(instance)
        return api_success(message=f"Trip '{trip_num}' marked as CANCELLED.")

    @extend_schema(
        summary="Block seat on a specific trip",
        description="Reserves/blocks a seat for operational purposes (VIP, staff rest) for this trip only.",
        request=BlockSeatInputSerializer,
        responses={201: OpenApiResponse(description="Seat blocked successfully.")},
        tags=["Trips: Seat Blocks"]
    )
    @action(detail=True, methods=['post'], url_path='block-seat')
    def block_seat(self, request, pk=None):
        trip = self.get_object()
        serializer = BlockSeatInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        seat = get_object_or_404(Seat, id=serializer.validated_data['seat_id'])
        reason = serializer.validated_data.get('reason', 'Operational Block')

        block = TripSeatBlockService.block_seat(
            trip=trip,
            seat=seat,
            reason=reason,
            user=request.user if request.user.is_authenticated else None
        )

        return api_success(
            data=TripSeatBlockSerializer(block).data,
            message=f"Seat {seat.seat_number} blocked on trip {trip.trip_number} ({reason}).",
            status_code=status.HTTP_201_CREATED
        )

    @extend_schema(
        summary="Unblock seat on a trip",
        description="Releases an operational block on a seat for this trip.",
        responses={200: OpenApiResponse(description="Seat unblocked successfully.")},
        tags=["Trips: Seat Blocks"]
    )
    @action(detail=True, methods=['delete'], url_path=r'unblock-seat/(?P<seat_id>[^/.]+)')
    def unblock_seat(self, request, pk=None, seat_id=None):
        trip = self.get_object()
        seat = get_object_or_404(Seat, id=seat_id)

        TripSeatBlockService.unblock_seat(trip, seat)
        return api_success(message=f"Seat {seat.seat_number} unblocked on trip {trip.trip_number}.")

    @extend_schema(
        summary="Calculate Segment Fare",
        description="Returns calculated ticket price between two RouteStops on this trip.",
        parameters=[FareCalculationQuerySerializer],
        responses={200: OpenApiResponse(description="Fare calculated successfully.")},
        tags=["Trips: Fare Rules"]
    )
    @action(detail=True, methods=['get'], url_path='calculate-fare')
    def calculate_fare(self, request, pk=None):
        trip = self.get_object()
        serializer = FareCalculationQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        from_stop = get_object_or_404(RouteStop, id=data['from_stop'])
        to_stop = get_object_or_404(RouteStop, id=data['to_stop'])
        seat_type = data.get('seat_type')

        fare = FareService.calculate_fare(
            trip=trip,
            from_stop=from_stop,
            to_stop=to_stop,
            seat_type=seat_type
        )

        return api_success(
            data={
                "trip_id": trip.id,
                "trip_number": trip.trip_number,
                "from_stop": {
                    "id": from_stop.id,
                    "city": from_stop.terminal.city,
                    "stop_order": from_stop.stop_order
                },
                "to_stop": {
                    "id": to_stop.id,
                    "city": to_stop.terminal.city,
                    "stop_order": to_stop.stop_order
                },
                "seat_type": seat_type or "STANDARD",
                "calculated_fare": str(fare),
                "currency": "PKR"
            },
            message=f"Fare calculated: PKR {fare}"
        )


class FareRuleViewSet(viewsets.ModelViewSet):
    """
    CRUD viewset for explicit Segment Fare Rules.
    """
    queryset = FareRule.objects.select_related(
        'route', 'from_stop__terminal', 'to_stop__terminal'
    ).all()
    serializer_class = FareRuleSerializer
    filterset_fields = ['route', 'seat_type']

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [AllowAny()]
        return [IsOperator()]

    @extend_schema(tags=["Trips: Fare Rules"])
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return api_success(data=serializer.data, message="Fare rules retrieved successfully.")

    @extend_schema(tags=["Trips: Fare Rules"])
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        rule = FareService.set_fare_rule(
            route=data['route'],
            from_stop=data['from_stop'],
            to_stop=data['to_stop'],
            fare_amount=data['fare_amount'],
            seat_type=data.get('seat_type')
        )

        return api_success(
            data=FareRuleSerializer(rule).data,
            message="Fare rule configured successfully.",
            status_code=status.HTTP_201_CREATED
        )
