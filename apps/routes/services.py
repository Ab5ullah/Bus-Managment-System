"""
Service Layer for Routes, Terminals, and Stop Sequence Progression.
Enforces domain rules, distance/time invariants, and segment resolution.
"""
from decimal import Decimal
from typing import List, Dict, Any, Tuple, Optional
from django.db import transaction
from django.core.exceptions import ValidationError

from common.exceptions import BusinessLogicError
from .models import Terminal, Route, RouteStop


class TerminalService:
    """
    Domain service for Terminal operations.
    """

    @classmethod
    def create_terminal(
        cls,
        name: str,
        code: str,
        city: str,
        address: str = "",
        latitude: Optional[Decimal] = None,
        longitude: Optional[Decimal] = None,
        contact_number: str = ""
    ) -> Terminal:
        code = code.strip().upper()
        if Terminal.objects.filter(code=code).exists():
            raise BusinessLogicError(f"Terminal with code '{code}' already exists.")

        if latitude is not None and not (-90 <= latitude <= 90):
            raise BusinessLogicError("Latitude must be between -90 and +90 degrees.")

        if longitude is not None and not (-180 <= longitude <= 180):
            raise BusinessLogicError("Longitude must be between -180 and +180 degrees.")

        return Terminal.objects.create(
            name=name.strip(),
            code=code,
            city=city.strip(),
            address=address.strip(),
            latitude=latitude,
            longitude=longitude,
            contact_number=contact_number.strip()
        )

    @classmethod
    def update_terminal(cls, terminal: Terminal, **kwargs) -> Terminal:
        if 'code' in kwargs:
            code = kwargs['code'].strip().upper()
            if Terminal.objects.filter(code=code).exclude(id=terminal.id).exists():
                raise BusinessLogicError(f"Terminal with code '{code}' already exists.")
            terminal.code = code

        for field in ['name', 'city', 'address', 'latitude', 'longitude', 'contact_number', 'is_active']:
            if field in kwargs:
                setattr(terminal, field, kwargs[field])

        terminal.save()
        return terminal


class RouteService:
    """
    Domain service for Sequenced Route lifecycle and stop validation.
    """

    @classmethod
    @transaction.atomic
    def create_route(
        cls,
        code: str,
        name: str,
        origin: Terminal,
        destination: Terminal,
        total_distance_km: Decimal = Decimal('0.00'),
        estimated_duration_minutes: int = 0,
        description: str = "",
        stops_data: Optional[List[Dict[str, Any]]] = None
    ) -> Route:
        """
        Creates a route with strict domain validations and sequenced stops.
        """
        code = code.strip().upper()
        if Route.objects.filter(code=code).exists():
            raise BusinessLogicError(f"Route with code '{code}' already exists.")

        if origin.id == destination.id:
            raise BusinessLogicError("Origin and destination terminals cannot be the same terminal.")

        route = Route.objects.create(
            code=code,
            name=name.strip(),
            origin=origin,
            destination=destination,
            total_distance_km=Decimal(str(total_distance_km)),
            estimated_duration_minutes=estimated_duration_minutes,
            description=description.strip()
        )

        if stops_data is None or len(stops_data) == 0:
            # Default direct 2-stop route (Origin -> Destination)
            RouteStop.objects.create(
                route=route,
                terminal=origin,
                stop_order=0,
                distance_from_origin_km=Decimal('0.00'),
                offset_minutes=0
            )
            RouteStop.objects.create(
                route=route,
                terminal=destination,
                stop_order=1,
                distance_from_origin_km=Decimal(str(total_distance_km)),
                offset_minutes=estimated_duration_minutes
            )
        else:
            cls._validate_and_save_stops(route, stops_data)

        return route

    @classmethod
    @transaction.atomic
    def replace_route_stops(cls, route: Route, stops_data: List[Dict[str, Any]]) -> List[RouteStop]:
        """
        Atomically replaces all stops on a route with a validated sequence.
        """
        # Delete existing stops
        route.stops.all().delete()
        new_stops = cls._validate_and_save_stops(route, stops_data)
        return new_stops

    @classmethod
    def _validate_and_save_stops(cls, route: Route, stops_data: List[Dict[str, Any]]) -> List[RouteStop]:
        """
        Validates stop sequence invariants:
        1. Minimum 2 stops.
        2. Stop 0 MUST be origin terminal with distance=0, offset=0.
        3. Last stop MUST be destination terminal.
        4. stop_order must strictly increment: 0, 1, 2, ..., N-1.
        5. distance_from_origin_km must be strictly increasing.
        6. offset_minutes must be strictly increasing.
        7. No duplicate terminals on the route.
        """
        if len(stops_data) < 2:
            raise BusinessLogicError("A route must have at least 2 stops (origin and destination).")

        # Sort stops by stop_order to inspect sequence
        try:
            sorted_stops = sorted(stops_data, key=lambda s: int(s['stop_order']))
        except (KeyError, ValueError, TypeError):
            raise BusinessLogicError("Each stop must include a valid integer 'stop_order'.")

        terminal_ids = set()
        prev_distance = Decimal('-1')
        prev_offset = -1
        expected_order = 0

        created_stops = []

        for idx, stop_info in enumerate(sorted_stops):
            order = int(stop_info['stop_order'])
            terminal_val = stop_info['terminal']
            terminal = terminal_val if isinstance(terminal_val, Terminal) else Terminal.objects.get(id=terminal_val)
            distance = Decimal(str(stop_info.get('distance_from_origin_km', 0)))
            offset = int(stop_info.get('offset_minutes', 0))

            # Invariant 4: Continuous sequential order
            if order != expected_order:
                raise BusinessLogicError(
                    f"Stop sequence must be continuous and 0-indexed. Expected stop_order {expected_order}, got {order}."
                )

            # Invariant 7: No duplicate terminals
            if terminal.id in terminal_ids:
                raise BusinessLogicError(
                    f"Terminal '{terminal.name}' ({terminal.code}) cannot appear multiple times on the same route."
                )
            terminal_ids.add(terminal.id)

            # Invariant 2: Origin stop rules
            if idx == 0:
                if terminal.id != route.origin_id:
                    raise BusinessLogicError(
                        f"First stop (order 0) must be the route origin terminal '{route.origin.name}'."
                    )
                if distance != Decimal('0.00'):
                    raise BusinessLogicError("Origin stop distance must be 0.00 km.")
                if offset != 0:
                    raise BusinessLogicError("Origin stop offset must be 0 minutes.")

            # Invariant 5: Strictly increasing distance
            if idx > 0 and distance <= prev_distance:
                raise BusinessLogicError(
                    f"Distance at stop #{order} ({distance} km) must be strictly greater than preceding stop ({prev_distance} km)."
                )

            # Invariant 6: Strictly increasing offset minutes
            if idx > 0 and offset <= prev_offset:
                raise BusinessLogicError(
                    f"Travel time offset at stop #{order} ({offset} min) must be strictly greater than preceding stop ({prev_offset} min)."
                )

            prev_distance = distance
            prev_offset = offset
            expected_order += 1

            created_stops.append(
                RouteStop(
                    route=route,
                    terminal=terminal,
                    stop_order=order,
                    distance_from_origin_km=distance,
                    offset_minutes=offset
                )
            )

        # Invariant 3: Last stop rules
        last_stop = created_stops[-1]
        if last_stop.terminal_id != route.destination_id:
            raise BusinessLogicError(
                f"Last stop (order {last_stop.stop_order}) must be the route destination terminal '{route.destination.name}'."
            )

        # Bulk create all stops atomically
        RouteStop.objects.bulk_create(created_stops)

        # Synchronize route total distance and estimated duration from final stop
        route.total_distance_km = last_stop.distance_from_origin_km
        route.estimated_duration_minutes = last_stop.offset_minutes
        route.save(update_fields=['total_distance_km', 'estimated_duration_minutes', 'updated_at'])

        return created_stops


class StopSequenceService:
    """
    Domain service for resolving valid travel segments and stop order relationships.
    Essential foundation for Module 4 (Fare Rules) and Module 5 (Segment Search).
    """

    @classmethod
    def get_ordered_stops(cls, route: Route) -> List[RouteStop]:
        """Returns ordered list of RouteStops for a route."""
        return list(route.stops.select_related('terminal').order_by('stop_order'))

    @classmethod
    def get_all_segments(cls, route: Route) -> List[Dict[str, Any]]:
        """
        Generates all N*(N-1)/2 directional travel segments possible on a sequenced route.
        Each segment represents a valid travel leg from an earlier stop to a later stop.
        """
        stops = cls.get_ordered_stops(route)
        segments = []
        n = len(stops)

        for i in range(n):
            for j in range(i + 1, n):
                from_s = stops[i]
                to_s = stops[j]
                dist = to_s.distance_from_origin_km - from_s.distance_from_origin_km
                duration = to_s.offset_minutes - from_s.offset_minutes

                segments.append({
                    "from_stop_id": from_s.id,
                    "to_stop_id": to_s.id,
                    "from_terminal": {
                        "id": from_s.terminal.id,
                        "name": from_s.terminal.name,
                        "code": from_s.terminal.code,
                        "city": from_s.terminal.city,
                    },
                    "to_terminal": {
                        "id": to_s.terminal.id,
                        "name": to_s.terminal.name,
                        "code": to_s.terminal.code,
                        "city": to_s.terminal.city,
                    },
                    "from_stop_order": from_s.stop_order,
                    "to_stop_order": to_s.stop_order,
                    "distance_km": dist,
                    "duration_minutes": duration,
                    "stops_count": j - i
                })

        return segments

    @classmethod
    def validate_segment(
        cls,
        route: Route,
        from_terminal_id: Any,
        to_terminal_id: Any
    ) -> Tuple[RouteStop, RouteStop, Decimal, int]:
        """
        Validates whether traveling from 'from_terminal' to 'to_terminal' is a valid forward segment.
        Returns (from_stop, to_stop, segment_distance_km, segment_duration_minutes).
        """
        stops = {str(s.terminal_id): s for s in cls.get_ordered_stops(route)}

        from_id_str = str(from_terminal_id)
        to_id_str = str(to_terminal_id)

        if from_id_str not in stops:
            raise BusinessLogicError(f"Boarding terminal is not on route {route.code}.")
        if to_id_str not in stops:
            raise BusinessLogicError(f"Alighting terminal is not on route {route.code}.")

        from_stop = stops[from_id_str]
        to_stop = stops[to_id_str]

        if from_stop.stop_order >= to_stop.stop_order:
            raise BusinessLogicError(
                f"Invalid travel direction: {from_stop.terminal.city} (Stop #{from_stop.stop_order}) "
                f"is after or equal to {to_stop.terminal.city} (Stop #{to_stop.stop_order}) on route {route.code}."
            )

        segment_distance = to_stop.distance_from_origin_km - from_stop.distance_from_origin_km
        segment_duration = to_stop.offset_minutes - from_stop.offset_minutes

        return from_stop, to_stop, segment_distance, segment_duration
