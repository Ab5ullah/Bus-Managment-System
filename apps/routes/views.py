"""
Views for Routes, Terminals, and Sequenced Route Stops.
"""
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiResponse

from common.responses import api_success
from accounts.permissions import IsOperator
from .models import Terminal, Route, RouteStop
from .services import TerminalService, RouteService, StopSequenceService
from .serializers import (
    TerminalSerializer,
    RouteListSerializer,
    RouteDetailSerializer,
    RouteCreateSerializer,
    RouteStopSerializer,
    RouteStopsUpdateSerializer,
)


class TerminalViewSet(viewsets.ModelViewSet):
    """
    CRUD viewset for physical transit stations and terminals.
    Publicly browsable for route planning; mutations restricted to operators/admins.
    """
    queryset = Terminal.objects.all()
    serializer_class = TerminalSerializer
    filterset_fields = ['city', 'is_active']
    search_fields = ['name', 'code', 'city', 'address']
    ordering_fields = ['name', 'code', 'city', 'created_at']

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [AllowAny()]
        return [IsOperator()]

    @extend_schema(tags=["Routes: Terminals"])
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return api_success(data=serializer.data, message="Terminals retrieved successfully.")

    @extend_schema(tags=["Routes: Terminals"])
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return api_success(data=serializer.data, message="Terminal retrieved successfully.")

    @extend_schema(tags=["Routes: Terminals"])
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data

        terminal = TerminalService.create_terminal(
            name=validated_data['name'],
            code=validated_data['code'],
            city=validated_data['city'],
            address=validated_data.get('address', ''),
            latitude=validated_data.get('latitude'),
            longitude=validated_data.get('longitude'),
            contact_number=validated_data.get('contact_number', '')
        )
        return api_success(
            data=TerminalSerializer(terminal).data,
            message=f"Terminal '{terminal.name}' ({terminal.code}) created successfully.",
            status_code=status.HTTP_201_CREATED
        )

    @extend_schema(tags=["Routes: Terminals"])
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        terminal = TerminalService.update_terminal(instance, **serializer.validated_data)
        return api_success(
            data=TerminalSerializer(terminal).data,
            message="Terminal updated successfully."
        )

    @extend_schema(tags=["Routes: Terminals"])
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        code = instance.code
        instance.delete()
        return api_success(message=f"Terminal {code} deleted successfully.")


class RouteViewSet(viewsets.ModelViewSet):
    """
    CRUD viewset for Sequenced Transit Routes.
    Publicly listable and readable; route setup restricted to operators.
    """
    queryset = Route.objects.select_related('origin', 'destination').prefetch_related('stops__terminal').all()
    filterset_fields = ['is_active']
    search_fields = ['code', 'name', 'origin__city', 'destination__city']
    ordering_fields = ['code', 'total_distance_km', 'estimated_duration_minutes', 'created_at']

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'segments']:
            return [AllowAny()]
        return [IsOperator()]

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return RouteDetailSerializer
        if self.action == 'create':
            return RouteCreateSerializer
        if self.action == 'update_stops':
            return RouteStopsUpdateSerializer
        return RouteListSerializer

    @extend_schema(tags=["Routes: Sequenced Routes"])
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return api_success(data=serializer.data, message="Routes retrieved successfully.")

    @extend_schema(tags=["Routes: Sequenced Routes"])
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return api_success(data=serializer.data, message="Route retrieved successfully.")

    @extend_schema(tags=["Routes: Sequenced Routes"])
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        origin = get_object_or_404(Terminal, id=data['origin'])
        destination = get_object_or_404(Terminal, id=data['destination'])

        route = RouteService.create_route(
            code=data['code'],
            name=data['name'],
            origin=origin,
            destination=destination,
            total_distance_km=data.get('total_distance_km', 0),
            estimated_duration_minutes=data.get('estimated_duration_minutes', 0),
            description=data.get('description', ''),
            stops_data=data.get('stops')
        )

        return api_success(
            data=RouteDetailSerializer(route).data,
            message=f"Route '{route.code}' ({route.name}) created with {route.stops.count()} stops.",
            status_code=status.HTTP_201_CREATED
        )

    @extend_schema(tags=["Routes: Sequenced Routes"])
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        code = instance.code
        instance.delete()
        return api_success(message=f"Route {code} deleted successfully.")

    @extend_schema(
        summary="Update Sequenced Stops for Route",
        description="Replaces all stops on the route with a new ordered, validated progression.",
        request=RouteStopsUpdateSerializer,
        responses={200: OpenApiResponse(description="Stops replaced successfully.")},
        tags=["Routes: Sequenced Routes"]
    )
    @action(detail=True, methods=['post'], url_path='update-stops')
    def update_stops(self, request, pk=None):
        route = self.get_object()
        serializer = RouteStopsUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        new_stops = RouteService.replace_route_stops(
            route=route,
            stops_data=serializer.validated_data['stops']
        )
        fresh_route = Route.objects.select_related('origin', 'destination').prefetch_related('stops__terminal').get(id=route.id)

        return api_success(
            data=RouteDetailSerializer(fresh_route).data,
            message=f"Route {route.code} stops updated. Configured {len(new_stops)} stops ({fresh_route.total_distance_km} km)."
        )

    @extend_schema(
        summary="List all valid directional segments on Route",
        description="Resolves all N*(N-1)/2 forward travel legs with distances and durations for fare modeling and trip booking.",
        responses={200: OpenApiResponse(description="Valid segments retrieved.")},
        tags=["Routes: Sequenced Routes"]
    )
    @action(detail=True, methods=['get'])
    def segments(self, request, pk=None):
        route = self.get_object()
        segments = StopSequenceService.get_all_segments(route)
        return api_success(
            data={
                "route_id": route.id,
                "route_code": route.code,
                "total_stops": route.stops.count(),
                "total_segments": len(segments),
                "segments": segments
            },
            message=f"Found {len(segments)} valid directional segments for route {route.code}."
        )
