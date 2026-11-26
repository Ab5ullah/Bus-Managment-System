"""
Service Layer for Trips, Fleet & Crew Scheduling, Seat Blocking, and Segment Fare Resolution.
"""
from decimal import Decimal
import secrets
from typing import Optional, Tuple, Set, List
from django.db import transaction
from django.utils import timezone

from common.constants import TripStatus, BusStatus, SeatType, BusType
from common.exceptions import BusinessLogicError
from common.money import round_currency
from fleet.models import Bus, Seat, Driver, Conductor, CrewStatus
from routes.models import Route, RouteStop
from .models import Trip, TripSeatBlock, FareRule


class TripScheduleService:
    """
    Domain service for scheduling journeys and strictly preventing bus and crew double-booking.
    """

    @classmethod
    def check_bus_overlap(
        cls,
        bus: Bus,
        departure_dt,
        arrival_dt,
        exclude_trip_id=None
    ) -> Tuple[bool, Optional[Trip]]:
        """
        Detects time overlap for a bus across active trips.
        Two intervals [D1, A1] and [D2, A2] overlap if D1 < A2 and A1 > D2.
        """
        active_statuses = [TripStatus.SCHEDULED, TripStatus.BOARDING, TripStatus.DEPARTED]
        qs = Trip.objects.filter(
            bus=bus,
            status__in=active_statuses,
            departure_datetime__lt=arrival_dt,
            arrival_datetime__gt=departure_dt
        )
        if exclude_trip_id:
            qs = qs.exclude(id=exclude_trip_id)

        conflicting = qs.first()
        return (conflicting is not None), conflicting

    @classmethod
    def check_driver_overlap(
        cls,
        driver: Optional[Driver],
        departure_dt,
        arrival_dt,
        exclude_trip_id=None
    ) -> Tuple[bool, Optional[Trip]]:
        """
        Detects time overlap for a driver across active trips.
        """
        if not driver:
            return False, None

        active_statuses = [TripStatus.SCHEDULED, TripStatus.BOARDING, TripStatus.DEPARTED]
        qs = Trip.objects.filter(
            driver=driver,
            status__in=active_statuses,
            departure_datetime__lt=arrival_dt,
            arrival_datetime__gt=departure_dt
        )
        if exclude_trip_id:
            qs = qs.exclude(id=exclude_trip_id)

        conflicting = qs.first()
        return (conflicting is not None), conflicting

    @classmethod
    def check_conductor_overlap(
        cls,
        conductor: Optional[Conductor],
        departure_dt,
        arrival_dt,
        exclude_trip_id=None
    ) -> Tuple[bool, Optional[Trip]]:
        """
        Detects time overlap for a conductor across active trips.
        """
        if not conductor:
            return False, None

        active_statuses = [TripStatus.SCHEDULED, TripStatus.BOARDING, TripStatus.DEPARTED]
        qs = Trip.objects.filter(
            conductor=conductor,
            status__in=active_statuses,
            departure_datetime__lt=arrival_dt,
            arrival_datetime__gt=departure_dt
        )
        if exclude_trip_id:
            qs = qs.exclude(id=exclude_trip_id)

        conflicting = qs.first()
        return (conflicting is not None), conflicting

    @classmethod
    @transaction.atomic
    def create_trip(
        cls,
        route: Route,
        bus: Bus,
        departure_datetime,
        arrival_datetime=None,
        base_fare: Decimal = Decimal('0.00'),
        driver: Optional[Driver] = None,
        conductor: Optional[Conductor] = None,
        trip_number: Optional[str] = None
    ) -> Trip:
        """
        Schedules a trip after strictly verifying time invariants and resource availability.
        Auto-computes arrival_datetime from route duration or last stop offset_minutes if omitted.
        """
        if arrival_datetime is None:
            from datetime import timedelta
            duration_mins = getattr(route, 'estimated_duration_minutes', 0) or 0
            if duration_mins <= 0:
                last_stop = route.stops.order_by('-stop_order').first()
                duration_mins = last_stop.offset_minutes if last_stop and last_stop.offset_minutes > 0 else 0
            if duration_mins <= 0:
                raise BusinessLogicError(
                    "Arrival datetime is required when the route has no estimated duration or stop offset_minutes configured."
                )
            arrival_datetime = departure_datetime + timedelta(minutes=duration_mins)

        if arrival_datetime <= departure_datetime:
            raise BusinessLogicError("Arrival datetime must be strictly after departure datetime.")

        if bus.status != BusStatus.ACTIVE or not bus.is_active:
            raise BusinessLogicError(f"Bus '{bus.registration_number}' is not active or is under maintenance.")

        if driver and (driver.status in [CrewStatus.SUSPENDED, CrewStatus.ON_LEAVE] or not driver.is_active):
            raise BusinessLogicError(f"Driver '{driver.full_name}' is currently {driver.status} or inactive.")

        if conductor and (conductor.status in [CrewStatus.SUSPENDED, CrewStatus.ON_LEAVE] or not conductor.is_active):
            raise BusinessLogicError(f"Conductor '{conductor.full_name}' is currently {conductor.status} or inactive.")

        # 1. Bus Overlap Check
        bus_overlaps, conflict_bus = cls.check_bus_overlap(bus, departure_datetime, arrival_datetime)
        if bus_overlaps:
            raise BusinessLogicError(
                f"Bus '{bus.registration_number}' is already assigned to active trip "
                f"'{conflict_bus.trip_number}' ({conflict_bus.departure_datetime:%Y-%m-%d %H:%M} to "
                f"{conflict_bus.arrival_datetime:%Y-%m-%d %H:%M})."
            )

        # 2. Driver Overlap Check
        driver_overlaps, conflict_driver = cls.check_driver_overlap(driver, departure_datetime, arrival_datetime)
        if driver_overlaps:
            raise BusinessLogicError(
                f"Driver '{driver.full_name}' is already scheduled on active trip "
                f"'{conflict_driver.trip_number}' ({conflict_driver.departure_datetime:%Y-%m-%d %H:%M} to "
                f"{conflict_driver.arrival_datetime:%Y-%m-%d %H:%M})."
            )

        # 3. Conductor Overlap Check
        cond_overlaps, conflict_cond = cls.check_conductor_overlap(conductor, departure_datetime, arrival_datetime)
        if cond_overlaps:
            raise BusinessLogicError(
                f"Conductor '{conductor.full_name}' is already scheduled on active trip "
                f"'{conflict_cond.trip_number}' ({conflict_cond.departure_datetime:%Y-%m-%d %H:%M} to "
                f"{conflict_cond.arrival_datetime:%Y-%m-%d %H:%M})."
            )

        # Generate unique trip_number if not provided
        if not trip_number:
            date_str = departure_datetime.strftime("%Y%m%d")
            rand_code = secrets.token_hex(2).upper()
            trip_number = f"TRIP-{date_str}-{rand_code}"
            while Trip.objects.filter(trip_number=trip_number).exists():
                rand_code = secrets.token_hex(2).upper()
                trip_number = f"TRIP-{date_str}-{rand_code}"
        else:
            trip_number = trip_number.strip().upper()
            if Trip.objects.filter(trip_number=trip_number).exists():
                raise BusinessLogicError(f"Trip with trip_number '{trip_number}' already exists.")

        trip = Trip.objects.create(
            trip_number=trip_number,
            route=route,
            bus=bus,
            driver=driver,
            conductor=conductor,
            departure_datetime=departure_datetime,
            arrival_datetime=arrival_datetime,
            base_fare=round_currency(Decimal(str(base_fare))),
            status=TripStatus.SCHEDULED
        )
        return trip

    @classmethod
    @transaction.atomic
    def update_trip(cls, trip: Trip, **kwargs) -> Trip:
        """
        Updates trip attributes, re-validating overlap invariants if timings, bus, or crew change.
        """
        departure = kwargs.get('departure_datetime', trip.departure_datetime)
        arrival = kwargs.get('arrival_datetime', trip.arrival_datetime)
        bus = kwargs.get('bus', trip.bus)
        driver = kwargs.get('driver', trip.driver)
        conductor = kwargs.get('conductor', trip.conductor)

        if arrival <= departure:
            raise BusinessLogicError("Arrival datetime must be strictly after departure datetime.")

        # Re-check bus overlap
        if bus != trip.bus or departure != trip.departure_datetime or arrival != trip.arrival_datetime:
            overlaps, conflict = cls.check_bus_overlap(bus, departure, arrival, exclude_trip_id=trip.id)
            if overlaps:
                raise BusinessLogicError(
                    f"Bus '{bus.registration_number}' is already assigned to conflicting trip '{conflict.trip_number}'."
                )

        # Re-check driver overlap
        if driver != trip.driver or departure != trip.departure_datetime or arrival != trip.arrival_datetime:
            overlaps, conflict = cls.check_driver_overlap(driver, departure, arrival, exclude_trip_id=trip.id)
            if overlaps:
                raise BusinessLogicError(
                    f"Driver '{driver.full_name}' is already scheduled on conflicting trip '{conflict.trip_number}'."
                )

        for key, value in kwargs.items():
            if key == 'base_fare':
                value = round_currency(Decimal(str(value)))
            setattr(trip, key, value)

        trip.save()
        return trip

    @classmethod
    def cancel_trip(cls, trip: Trip, reason: str = "") -> Trip:
        """Cancels a scheduled trip."""
        trip.status = TripStatus.CANCELLED
        trip.save(update_fields=['status', 'updated_at'])
        return trip


class TripSeatBlockService:
    """
    Domain service for per-trip seat blocking (VIP, crew rest, mechanical hold).
    """

    @classmethod
    def block_seat(
        cls,
        trip: Trip,
        seat: Seat,
        reason: str = "Operational Block",
        user=None
    ) -> TripSeatBlock:
        """
        Blocks an individual seat on a specific trip.
        """
        if seat.bus_id != trip.bus_id:
            raise BusinessLogicError(
                f"Seat '{seat.seat_number}' belongs to bus '{seat.bus.registration_number}', "
                f"not the vehicle assigned to this trip ('{trip.bus.registration_number}')."
            )

        if TripSeatBlock.objects.filter(trip=trip, seat=seat).exists():
            raise BusinessLogicError(f"Seat '{seat.seat_number}' is already blocked on trip '{trip.trip_number}'.")

        return TripSeatBlock.objects.create(
            trip=trip,
            seat=seat,
            reason=reason.strip(),
            blocked_by=user
        )

    @classmethod
    def unblock_seat(cls, trip: Trip, seat: Seat) -> bool:
        """Removes a seat block from a trip."""
        deleted_count, _ = TripSeatBlock.objects.filter(trip=trip, seat=seat).delete()
        if deleted_count == 0:
            raise BusinessLogicError(f"Seat '{seat.seat_number}' is not currently blocked on trip '{trip.trip_number}'.")
        return True

    @classmethod
    def get_blocked_seat_ids(cls, trip: Trip) -> Set[str]:
        """Returns set of seat UUIDs currently blocked on a trip."""
        return set(
            str(s_id) for s_id in TripSeatBlock.objects.filter(trip=trip).values_list('seat_id', flat=True)
        )


class FareService:
    """
    Authoritative domain pricing engine for segment fares and seat classes.
    """

    @classmethod
    def set_fare_rule(
        cls,
        route: Route,
        from_stop: RouteStop,
        to_stop: RouteStop,
        fare_amount: Decimal,
        seat_type: Optional[str] = None
    ) -> FareRule:
        """
        Configures an explicit fare price for a travel segment.
        """
        if from_stop.route_id != route.id or to_stop.route_id != route.id:
            raise BusinessLogicError("Both stops must belong to the specified route.")

        if from_stop.stop_order >= to_stop.stop_order:
            raise BusinessLogicError(
                f"Origin stop #{from_stop.stop_order} must precede destination stop #{to_stop.stop_order}."
            )

        amount = round_currency(Decimal(str(fare_amount)))
        rule, _ = FareRule.objects.update_or_create(
            route=route,
            from_stop=from_stop,
            to_stop=to_stop,
            seat_type=seat_type,
            defaults={
                'from_stop_order': from_stop.stop_order,
                'to_stop_order': to_stop.stop_order,
                'fare_amount': amount,
            }
        )
        return rule

    @classmethod
    def calculate_base_fare(
        cls,
        trip: Trip,
        from_stop: RouteStop,
        to_stop: RouteStop,
        seat_type: Optional[str] = None
    ) -> Decimal:
        """
        Calculates base ticket price for a travel segment before dynamic pricing multipliers:
        1. Exact match on (route, from_stop, to_stop, seat_type).
        2. Fallback to default segment rule (seat_type is NULL).
        3. Fallback to proportional base fare:
           fare = trip.base_fare * (segment_distance / route_total_distance)
           with seat class multiplier (e.g. 1.25x for sleeper).
        """
        if from_stop.stop_order >= to_stop.stop_order:
            raise BusinessLogicError("Origin stop must be before destination stop.")

        # 1. Exact match
        if seat_type:
            exact_rule = FareRule.objects.filter(
                route=trip.route,
                from_stop=from_stop,
                to_stop=to_stop,
                seat_type=seat_type
            ).first()
            if exact_rule:
                return exact_rule.fare_amount

        # 2. General segment rule
        general_rule = FareRule.objects.filter(
            route=trip.route,
            from_stop=from_stop,
            to_stop=to_stop,
            seat_type__isnull=True
        ).first()
        if general_rule:
            return general_rule.fare_amount

        # 3. Proportional distance calculation
        segment_dist = to_stop.distance_from_origin_km - from_stop.distance_from_origin_km
        route_dist = trip.route.total_distance_km

        if route_dist > Decimal('0.00'):
            proportion = segment_dist / route_dist
            calculated_fare = trip.base_fare * proportion
        else:
            calculated_fare = trip.base_fare

        # Multiplier by seat type or bus luxury
        multiplier = Decimal('1.00')
        if seat_type in [SeatType.SLEEPER_LOWER, SeatType.SLEEPER_UPPER]:
            multiplier = Decimal('1.25')
        elif trip.bus.bus_type == BusType.LUXURY:
            multiplier = Decimal('1.15')

        final_fare = round_currency(calculated_fare * multiplier)
        # Ensure minimum fare of at least 100 PKR
        return max(final_fare, Decimal('100.00'))

    @classmethod
    def calculate_fare(
        cls,
        trip: Trip,
        from_stop: RouteStop,
        to_stop: RouteStop,
        seat_type: Optional[str] = None
    ) -> Decimal:
        """
        Calculates authoritative ticket price including active dynamic pricing multipliers.
        """
        base_fare = cls.calculate_base_fare(
            trip=trip,
            from_stop=from_stop,
            to_stop=to_stop,
            seat_type=seat_type
        )
        try:
            from promotions.services import DynamicPricingService
            eval_res = DynamicPricingService.evaluate_trip_multiplier(trip)
            multiplier = eval_res.get('multiplier', Decimal('1.00'))
            if multiplier != Decimal('1.00'):
                return max(round_currency(base_fare * multiplier), Decimal('100.00'))
        except Exception:
            pass
        return base_fare
