"""
API Views for Trip Search and Segment Seat Map Availability.
"""
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework import status
from drf_spectacular.utils import extend_schema, OpenApiResponse

from common.responses import api_success
from routes.models import Route, RouteStop, Terminal
from trips.models import Trip
from .serializers import TripSearchQuerySerializer, SeatMapQuerySerializer
from .services import TripSearchService, SegmentAvailabilityService


class TripSearchView(APIView):
    """
    High-Performance Segment-Aware Trip Search Engine.
    Discovers all active journeys traversing between origin and destination stations,
    computing real-time available seat counts and segment fares.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Search Scheduled Trips by Corridor & Date",
        description=(
            "Finds scheduled commercial journeys between an origin and destination. "
            "Origin and destination can be specified by city name (e.g., 'Lahore', 'Islamabad') "
            "or exact Terminal UUID. Automatically computes segment-specific departure/arrival "
            "timings, duration, distance, ticket price, and real-time available seats count."
        ),
        parameters=[TripSearchQuerySerializer],
        responses={200: OpenApiResponse(description="Trips found successfully.")},
        tags=["Search: Trip Discovery"]
    )
    def get(self, request):
        serializer = TripSearchQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        params = serializer.validated_data

        trips = TripSearchService.search_trips(
            origin_query=params['origin'],
            destination_query=params['destination'],
            travel_date=params.get('date'),
            bus_type=params.get('bus_type'),
            min_price=params.get('min_price'),
            max_price=params.get('max_price'),
            departure_window=params.get('departure_window'),
            sort_by=params.get('sort_by', 'departure_asc')
        )

        return api_success(
            data={
                "total_results": len(trips),
                "origin_query": params['origin'],
                "destination_query": params['destination'],
                "travel_date": params.get('date').isoformat() if params.get('date') else None,
                "trips": trips
            },
            message=f"Found {len(trips)} available trip(s) matching your criteria."
        )


class TripSeatMapView(APIView):
    """
    Visual Seat Map Availability Endpoint.
    Returns physical floor layout for a trip and evaluates seat status
    (AVAILABLE, BOOKED, LOCKED, BLOCKED) for the specific travel segment.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Get Trip Seat Map for Segment",
        description=(
            "Returns complete physical bus seat grid (deck, row, column, type, ladies-only attribute) "
            "annotated with availability status for the requested travel segment [from_stop, to_stop). "
            "Enforces segment overlap: seats booked or locked on an overlapping leg are marked unavailable, "
            "while seats free on this segment are marked AVAILABLE."
        ),
        parameters=[SeatMapQuerySerializer],
        responses={200: OpenApiResponse(description="Seat map returned.")},
        tags=["Search: Seat Map"]
    )
    def get(self, request, trip_id):
        trip = get_object_or_404(
            Trip.objects.select_related('route', 'bus').prefetch_related('bus__seats', 'seat_blocks'),
            id=trip_id
        )

        serializer = SeatMapQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        from_stop = get_object_or_404(RouteStop.objects.select_related('terminal'), id=data['from_stop'])
        to_stop = get_object_or_404(RouteStop.objects.select_related('terminal'), id=data['to_stop'])

        seat_map = SegmentAvailabilityService.get_trip_seat_status_map(
            trip=trip,
            from_stop=from_stop,
            to_stop=to_stop
        )

        return api_success(
            data=seat_map,
            message=f"Seat map for trip {trip.trip_number} ({from_stop.terminal.city} ➔ {to_stop.terminal.city}) generated."
        )


class PopularRoutesView(APIView):
    """
    Returns popular transit corridors and available destination cities for quick booking search widgets.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        summary="List Popular Routes & Active Cities",
        description="Returns list of popular bus corridors and cities for quick search auto-completion.",
        tags=["Search: Trip Discovery"]
    )
    def get(self, request):
        routes = Route.objects.filter(is_active=True).select_related('origin', 'destination')[:10]
        cities = list(Terminal.objects.filter(is_active=True).values_list('city', flat=True).distinct())

        popular = [
            {
                "route_id": str(r.id),
                "code": r.code,
                "name": r.name,
                "origin_city": r.origin.city,
                "destination_city": r.destination.city,
                "distance_km": str(r.total_distance_km),
                "duration_minutes": r.estimated_duration_minutes,
            }
            for r in routes
        ]

        return api_success(
            data={
                "cities": cities,
                "popular_routes": popular
            },
            message="Popular routes and cities retrieved."
        )
