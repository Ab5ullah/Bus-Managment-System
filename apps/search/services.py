"""
Domain Services for Trip Search, Route Traversal Matching, and Segment-Aware Seat Availability.
"""
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import List, Dict, Any, Optional, Set, Tuple
import uuid
from django.apps import apps
from django.db.models import Q
from django.utils import timezone

from common.constants import TripStatus, SeatType, BusType
from common.exceptions import BusinessLogicError
from common.money import round_currency
from routes.models import Terminal, Route, RouteStop
from fleet.models import Bus, Seat
from trips.models import Trip, TripSeatBlock
from trips.services import FareService


class SegmentAvailabilityService:
    """
    Engine for determining seat availability for a specific directional travel segment [from_order, to_order).
    """

    @classmethod
    def get_trip_seat_status_map(
        cls,
        trip: Trip,
        from_stop: RouteStop,
        to_stop: RouteStop
    ) -> Dict[str, Any]:
        """
        Calculates the real-time status of every seat on the bus for travel from from_stop to to_stop.
        Enforces segment overlap: seat is occupied if a reservation overlaps [i, j).
        """
        if from_stop.route_id != trip.route_id or to_stop.route_id != trip.route_id:
            raise BusinessLogicError("Requested stops do not belong to the trip's route.")

        if from_stop.stop_order >= to_stop.stop_order:
            raise BusinessLogicError(
                f"Invalid travel direction: {from_stop.terminal.city} (Stop #{from_stop.stop_order}) "
                f"must precede {to_stop.terminal.city} (Stop #{to_stop.stop_order})."
            )

        from_order = from_stop.stop_order
        to_order = to_stop.stop_order

        # 1. Physical seats on this bus
        seats = list(trip.bus.seats.all().order_by('deck', 'row', 'column'))

        # 2. Administrative per-trip seat blocks
        blocked_seat_ids: Set[uuid.UUID] = set(
            TripSeatBlock.objects.filter(trip=trip).values_list('seat_id', flat=True)
        )

        # 3. Dynamic check for Module 6 BookingSeat and SeatLock models if present
        booked_seat_ids: Set[uuid.UUID] = set()
        locked_seat_ids: Set[uuid.UUID] = set()

        if apps.is_installed('bookings'):
            try:
                BookingSeat = apps.get_model('bookings', 'BookingSeat')
                # Overlap condition: u < j and v > i
                overlapping_bookings = BookingSeat.objects.filter(
                    booking__trip=trip,
                    status='BOOKED',
                    from_stop_order__lt=to_order,
                    to_stop_order__gt=from_order
                ).values_list('seat_id', flat=True)
                booked_seat_ids.update(overlapping_bookings)

                overlapping_booking_locks = BookingSeat.objects.filter(
                    booking__trip=trip,
                    status='LOCKED',
                    from_stop_order__lt=to_order,
                    to_stop_order__gt=from_order
                ).values_list('seat_id', flat=True)
                locked_seat_ids.update(overlapping_booking_locks)

                # Real-time temporary seat locks
                SeatLock = apps.get_model('bookings', 'SeatLock')
                now = timezone.now()
                overlapping_active_locks = SeatLock.objects.filter(
                    trip=trip,
                    locked_until__gt=now,
                    from_stop_order__lt=to_order,
                    to_stop_order__gt=from_order
                ).values_list('seat_id', flat=True)
                locked_seat_ids.update(overlapping_active_locks)
            except LookupError:
                pass

        # 4. Classify each physical seat
        seat_results = []
        counts = {
            "total": len(seats),
            "available": 0,
            "booked": 0,
            "locked": 0,
            "blocked": 0,
            "ladies_only": 0,
        }

        # Cache fare calculation per seat_type
        fare_cache: Dict[str, Decimal] = {}

        for seat in seats:
            if seat.seat_type not in fare_cache:
                fare_cache[seat.seat_type] = FareService.calculate_fare(
                    trip=trip,
                    from_stop=from_stop,
                    to_stop=to_stop,
                    seat_type=seat.seat_type
                )
            seat_fare = fare_cache[seat.seat_type]

            # Classification hierarchy
            if not seat.is_active or seat.id in blocked_seat_ids:
                status = "BLOCKED"
                counts["blocked"] += 1
            elif seat.id in booked_seat_ids:
                status = "BOOKED"
                counts["booked"] += 1
            elif seat.id in locked_seat_ids:
                status = "LOCKED"
                counts["locked"] += 1
            else:
                status = "AVAILABLE"
                counts["available"] += 1

            if seat.is_ladies_only:
                counts["ladies_only"] += 1

            seat_results.append({
                "id": str(seat.id),
                "seat_number": seat.seat_number,
                "row": seat.row,
                "column": seat.column,
                "deck": seat.deck,
                "seat_type": seat.seat_type,
                "is_ladies_only": seat.is_ladies_only,
                "status": status,
                "fare": str(seat_fare),
                "currency": "PKR"
            })

        return {
            "trip": {
                "id": str(trip.id),
                "trip_number": trip.trip_number,
                "departure_datetime": trip.departure_datetime.isoformat(),
                "arrival_datetime": trip.arrival_datetime.isoformat(),
                "bus_registration": trip.bus.registration_number,
                "bus_type": trip.bus.bus_type,
                "bus_type_display": trip.bus.get_bus_type_display(),
            },
            "segment": {
                "from_stop": {
                    "id": str(from_stop.id),
                    "city": from_stop.terminal.city,
                    "terminal_name": from_stop.terminal.name,
                    "stop_order": from_stop.stop_order
                },
                "to_stop": {
                    "id": str(to_stop.id),
                    "city": to_stop.terminal.city,
                    "terminal_name": to_stop.terminal.name,
                    "stop_order": to_stop.stop_order
                },
                "distance_km": str(to_stop.distance_from_origin_km - from_stop.distance_from_origin_km),
                "duration_minutes": to_stop.offset_minutes - from_stop.offset_minutes,
            },
            "summary": counts,
            "seats": seat_results
        }


class TripSearchService:
    """
    Search pipeline for discovering trips matching passenger travel criteria.
    Supports segment hops, date filtering, bus specifications, and price ranges.
    """

    @classmethod
    def resolve_terminals(cls, query: str) -> List[Terminal]:
        """Resolves a terminal by UUID string or city/name query."""
        query = query.strip()
        try:
            val_uuid = uuid.UUID(query)
            return list(Terminal.objects.filter(id=val_uuid, is_active=True))
        except ValueError:
            return list(
                Terminal.objects.filter(
                    Q(city__iexact=query) | Q(name__icontains=query) | Q(code__iexact=query),
                    is_active=True
                )
            )

    @classmethod
    def search_trips(
        cls,
        origin_query: str,
        destination_query: str,
        travel_date: Optional[date] = None,
        bus_type: Optional[str] = None,
        min_price: Optional[Decimal] = None,
        max_price: Optional[Decimal] = None,
        departure_window: Optional[str] = None,
        sort_by: str = 'departure_asc'
    ) -> List[Dict[str, Any]]:
        """
        Searches all active trips that traverse from origin to destination in forward sequence.
        """
        origin_terminals = cls.resolve_terminals(origin_query)
        if not origin_terminals:
            raise BusinessLogicError(f"No active stations found matching origin '{origin_query}'.")

        dest_terminals = cls.resolve_terminals(destination_query)
        if not dest_terminals:
            raise BusinessLogicError(f"No active stations found matching destination '{destination_query}'.")

        origin_ids = {t.id for t in origin_terminals}
        dest_ids = {t.id for t in dest_terminals}

        # 1. Discover all candidate routes that visit an origin terminal then a destination terminal
        origin_stops = RouteStop.objects.filter(terminal_id__in=origin_ids).select_related('route', 'terminal')
        dest_stops = RouteStop.objects.filter(terminal_id__in=dest_ids).select_related('route', 'terminal')

        # Pair up by route where origin_stop.stop_order < dest_stop.stop_order
        dest_stops_by_route: Dict[uuid.UUID, List[RouteStop]] = {}
        for ds in dest_stops:
            dest_stops_by_route.setdefault(ds.route_id, []).append(ds)

        valid_route_segments: List[Tuple[Route, RouteStop, RouteStop]] = []
        for os in origin_stops:
            if os.route_id in dest_stops_by_route:
                for ds in dest_stops_by_route[os.route_id]:
                    if os.stop_order < ds.stop_order:
                        valid_route_segments.append((os.route, os, ds))

        if not valid_route_segments:
            return []

        route_map = {r.id: (r, os, ds) for r, os, ds in valid_route_segments}
        candidate_route_ids = list(route_map.keys())

        # 2. Query active trips on these routes
        trips_qs = Trip.objects.filter(
            route_id__in=candidate_route_ids,
            status__in=[TripStatus.SCHEDULED, TripStatus.BOARDING],
            bus__is_active=True,
            bus__status='ACTIVE'
        ).select_related(
            'route', 'route__origin', 'route__destination',
            'bus', 'driver'
        ).prefetch_related('bus__seats', 'seat_blocks')

        if bus_type:
            trips_qs = trips_qs.filter(bus__bus_type=bus_type)

        results = []

        for trip in trips_qs:
            _, from_stop, to_stop = route_map[trip.route_id]

            # Calculate station departure time
            station_dep_dt = trip.departure_datetime + timedelta(minutes=from_stop.offset_minutes)
            station_arr_dt = trip.departure_datetime + timedelta(minutes=to_stop.offset_minutes)

            # Date Filter: matches local/UTC date at origin stop
            if travel_date and station_dep_dt.date() != travel_date:
                continue

            # Time of day window filter
            dep_hour = station_dep_dt.hour
            if departure_window == 'morning' and not (5 <= dep_hour < 12):
                continue
            elif departure_window == 'afternoon' and not (12 <= dep_hour < 18):
                continue
            elif departure_window == 'night' and not (dep_hour >= 18 or dep_hour < 5):
                continue

            # Calculate Segment Price
            fare = FareService.calculate_fare(trip, from_stop, to_stop)

            if min_price is not None and fare < min_price:
                continue
            if max_price is not None and fare > max_price:
                continue

            # Real-time segment seat counts
            status_map = SegmentAvailabilityService.get_trip_seat_status_map(trip, from_stop, to_stop)
            avail_count = status_map['summary']['available']
            total_count = status_map['summary']['total']

            seg_dist = to_stop.distance_from_origin_km - from_stop.distance_from_origin_km
            seg_duration = to_stop.offset_minutes - from_stop.offset_minutes

            results.append({
                "trip_id": str(trip.id),
                "trip_number": trip.trip_number,
                "status": trip.status,
                "route": {
                    "id": str(trip.route.id),
                    "code": trip.route.code,
                    "name": trip.route.name,
                },
                "origin": {
                    "terminal_id": str(from_stop.terminal.id),
                    "terminal_name": from_stop.terminal.name,
                    "city": from_stop.terminal.city,
                    "stop_order": from_stop.stop_order,
                    "departure_time": station_dep_dt.isoformat()
                },
                "destination": {
                    "terminal_id": str(to_stop.terminal.id),
                    "terminal_name": to_stop.terminal.name,
                    "city": to_stop.terminal.city,
                    "stop_order": to_stop.stop_order,
                    "arrival_time": station_arr_dt.isoformat()
                },
                "segment_details": {
                    "from_stop_id": str(from_stop.id),
                    "to_stop_id": str(to_stop.id),
                    "distance_km": str(seg_dist),
                    "duration_minutes": seg_duration,
                },
                "bus": {
                    "id": str(trip.bus.id),
                    "registration_number": trip.bus.registration_number,
                    "bus_type": trip.bus.bus_type,
                    "bus_type_display": trip.bus.get_bus_type_display(),
                    "amenities": trip.bus.amenities,
                    "total_seats": total_count,
                },
                "driver": {
                    "name": trip.driver.full_name if trip.driver else None,
                },
                "fare": {
                    "amount": str(fare),
                    "currency": "PKR"
                },
                "availability": {
                    "available_seats": avail_count,
                    "total_seats": total_count,
                    "is_sold_out": avail_count == 0
                }
            })

        # 3. Sorting
        if sort_by == 'price_asc':
            results.sort(key=lambda x: Decimal(x['fare']['amount']))
        elif sort_by == 'price_desc':
            results.sort(key=lambda x: Decimal(x['fare']['amount']), reverse=True)
        elif sort_by == 'duration_asc':
            results.sort(key=lambda x: x['segment_details']['duration_minutes'])
        elif sort_by == 'departure_desc':
            results.sort(key=lambda x: x['origin']['departure_time'], reverse=True)
        else:  # default departure_asc
            results.sort(key=lambda x: x['origin']['departure_time'])

        return results
