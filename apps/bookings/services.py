"""
Domain Service Layer for Real-Time Seat Locking, Atomic PNR Booking,
Counter Walk-In Sales, and Cancellation Life Cycles.
"""
from datetime import timedelta
from decimal import Decimal
from typing import List, Dict, Any, Optional, Set, Tuple
import uuid

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from common.constants import (
    BookingStatus,
    BookingSeatStatus,
    PaymentMethod,
    Gender,
    UserRole,
    TripStatus,
)
from common.exceptions import BusinessLogicError
from common.money import round_currency
from fleet.models import Seat
from routes.models import RouteStop
from trips.models import Trip, TripSeatBlock
from trips.services import FareService
from .models import SeatLock, Booking, BookingSeat, generate_unique_pnr


class SeatLockService:
    """
    Real-Time Distributed Seat Locking Engine.
    Guarantees concurrency safety, segment-aware hold isolation, and auto-expiration.
    """
    MAX_SEATS_PER_LOCK = 6
    DEFAULT_LOCK_MINUTES = 10

    @classmethod
    @transaction.atomic
    def lock_seats(
        cls,
        trip: Trip,
        seat_ids: List[Any],
        from_stop: RouteStop,
        to_stop: RouteStop,
        user=None,
        session_key: str = "",
        lock_duration_minutes: int = DEFAULT_LOCK_MINUTES
    ) -> Dict[str, Any]:
        """
        Atomically locks up to 6 seats on a specific journey segment [i, j) for 10 minutes.
        Employs select_for_update() row-locking and segment overlap prevention.
        """
        # 1. Domain validations
        if from_stop.route_id != trip.route_id or to_stop.route_id != trip.route_id:
            raise BusinessLogicError("Boarding and alighting stops must belong to the trip's route.")

        if from_stop.stop_order >= to_stop.stop_order:
            raise BusinessLogicError(
                f"Boarding stop ({from_stop.terminal.city} #{from_stop.stop_order}) "
                f"must precede alighting stop ({to_stop.terminal.city} #{to_stop.stop_order})."
            )

        if not seat_ids:
            raise BusinessLogicError("At least one seat must be selected to acquire a lock.")

        # De-duplicate requested seat IDs
        unique_seat_ids = list(dict.fromkeys(seat_ids))

        if len(unique_seat_ids) > cls.MAX_SEATS_PER_LOCK:
            raise BusinessLogicError(f"Maximum of {cls.MAX_SEATS_PER_LOCK} seats can be locked in a single reservation.")

        if trip.status not in [TripStatus.SCHEDULED, TripStatus.BOARDING]:
            raise BusinessLogicError(f"Cannot book seats on trip {trip.trip_number} with status '{trip.get_status_display()}'.")

        from_order = from_stop.stop_order
        to_order = to_stop.stop_order
        now = timezone.now()

        # 2. Acquire database row-locks on the requested physical seats
        seats = list(
            Seat.objects.select_for_update().filter(
                id__in=unique_seat_ids,
                bus_id=trip.bus_id,
                is_active=True
            )
        )

        if len(seats) != len(unique_seat_ids):
            raise BusinessLogicError("One or more selected seats are invalid, inactive, or not on this bus vehicle.")

        # 3. Check for operational per-trip administrative seat blocks
        blocked_seats = TripSeatBlock.objects.filter(trip=trip, seat_id__in=unique_seat_ids)
        if blocked_seats.exists():
            blocked_num = blocked_seats.first().seat.seat_number
            raise BusinessLogicError(f"Seat '{blocked_num}' is administratively blocked for this trip.")

        # 4. Check for segment collisions with existing Bookings (LOCKED or BOOKED)
        # Collision rule: u < j AND v > i
        colliding_bookings = BookingSeat.objects.filter(
            trip=trip,
            seat_id__in=unique_seat_ids,
            status__in=[BookingSeatStatus.LOCKED, BookingSeatStatus.BOOKED],
            from_stop_order__lt=to_order,
            to_stop_order__gt=from_order
        ).select_related('seat')

        if colliding_bookings.exists():
            c_seat = colliding_bookings.first().seat.seat_number
            raise BusinessLogicError(
                f"Seat '{c_seat}' is already reserved or booked by another customer on an overlapping segment."
            )

        # 5. Check for active temporary locks (locked_until > now)
        colliding_locks = SeatLock.objects.filter(
            trip=trip,
            seat_id__in=unique_seat_ids,
            locked_until__gt=now,
            from_stop_order__lt=to_order,
            to_stop_order__gt=from_order
        ).select_related('seat')

        if colliding_locks.exists():
            held_seat = colliding_locks.first().seat.seat_number
            raise BusinessLogicError(
                f"Seat '{held_seat}' is currently held by another passenger. Please wait or choose another seat."
            )

        # 6. Clean up any expired locks on these seats to prevent table bloat
        SeatLock.objects.filter(
            trip=trip,
            seat_id__in=unique_seat_ids,
            locked_until__lte=now
        ).delete()

        # 7. Generate atomic lock token and persist SeatLock records
        lock_token = uuid.uuid4()
        locked_until = now + timedelta(minutes=lock_duration_minutes)

        created_locks = []
        locked_seats_summary = []

        for seat in seats:
            lock_obj = SeatLock.objects.create(
                trip=trip,
                seat=seat,
                from_stop=from_stop,
                to_stop=to_stop,
                from_stop_order=from_order,
                to_stop_order=to_order,
                lock_token=lock_token,
                user=user,
                session_key=session_key,
                locked_until=locked_until
            )
            created_locks.append(lock_obj)

            # Compute segment fare
            seat_fare = FareService.calculate_fare(
                trip=trip,
                from_stop=from_stop,
                to_stop=to_stop,
                seat_type=seat.seat_type
            )

            locked_seats_summary.append({
                "seat_id": str(seat.id),
                "seat_number": seat.seat_number,
                "deck": seat.deck,
                "row": seat.row,
                "column": seat.column,
                "seat_type": seat.seat_type,
                "is_ladies_only": seat.is_ladies_only,
                "fare": str(seat_fare)
            })

            # Optional cache layer fast-check
            cache.set(f"seatlock:{trip.id}:{seat.id}", str(lock_token), timeout=lock_duration_minutes * 60)

        return {
            "lock_token": str(lock_token),
            "locked_until": locked_until.isoformat(),
            "expires_in_seconds": lock_duration_minutes * 60,
            "trip_id": str(trip.id),
            "trip_number": trip.trip_number,
            "segment": {
                "from_stop": from_stop.terminal.city,
                "to_stop": to_stop.terminal.city,
                "from_stop_order": from_order,
                "to_stop_order": to_order,
            },
            "locked_seats": locked_seats_summary
        }

    @classmethod
    def release_locks(cls, lock_token: uuid.UUID) -> int:
        """Explicitly releases temporary seat locks held under a token."""
        deleted_count, _ = SeatLock.objects.filter(lock_token=lock_token).delete()
        return deleted_count

    @classmethod
    def cleanup_expired_locks(cls) -> int:
        """Housekeeping: deletes expired seat locks."""
        deleted_count, _ = SeatLock.objects.filter(locked_until__lte=timezone.now()).delete()
        return deleted_count


class BookingService:
    """
    Authoritative Domain Engine for creating, confirming, and managing bookings.
    Enforces gender seating rules, financial totals (tax, fee, gross, net),
    atomic transitions, and counter walk-in sales.
    """

    @classmethod
    @transaction.atomic
    def create_booking_from_locks(
        cls,
        trip: Trip,
        lock_token: uuid.UUID,
        from_stop: RouteStop,
        to_stop: RouteStop,
        passenger_name: str,
        passenger_phone: str,
        passenger_email: str = "",
        passengers_data: Optional[List[Dict[str, Any]]] = None,
        user=None,
        payment_method: str = PaymentMethod.CARD_ONLINE
    ) -> Booking:
        """
        Transforms temporary SeatLocks into a formal PENDING booking.
        Transfers seats from SeatLock to BookingSeat(status='LOCKED').
        """
        now = timezone.now()

        # 1. Verify that lock token is active and not expired
        active_locks = list(
            SeatLock.objects.select_for_update().filter(
                trip=trip,
                lock_token=lock_token,
                locked_until__gt=now
            ).select_related('seat')
        )

        if not active_locks:
            raise BusinessLogicError("Your seat reservation has expired. Please select your seats again.")

        locked_seat_map = {str(l.seat_id): l.seat for l in active_locks}
        locked_seat_ids = set(locked_seat_map.keys())

        # 2. Validate passenger allocations
        if not passengers_data or len(passengers_data) != len(active_locks):
            raise BusinessLogicError(
                f"Passenger details must be provided for all {len(active_locks)} locked seat(s)."
            )

        provided_seat_ids = {str(p['seat_id']) for p in passengers_data}
        if provided_seat_ids != locked_seat_ids:
            raise BusinessLogicError("Mismatch between passenger assignments and locked seats.")

        # 3. Enforce Gender Restrictions
        for p in passengers_data:
            seat_id_str = str(p['seat_id'])
            seat = locked_seat_map[seat_id_str]
            gender = p.get('gender', Gender.MALE).upper()

            if seat.is_ladies_only and gender != Gender.FEMALE:
                raise BusinessLogicError(
                    f"Seat '{seat.seat_number}' is strictly reserved for female passengers. "
                    f"Passenger '{p.get('passenger_name')}' ({gender}) cannot be seated here."
                )

        # 4. Compute Financials
        seat_prices: List[Tuple[Seat, Decimal]] = []
        total_gross = Decimal('0.00')

        for p in passengers_data:
            seat = locked_seat_map[str(p['seat_id'])]
            fare = FareService.calculate_fare(
                trip=trip,
                from_stop=from_stop,
                to_stop=to_stop,
                seat_type=seat.seat_type
            )
            seat_prices.append((seat, fare))
            total_gross += fare

        tax_rate = getattr(settings, 'TAX_RATE_PERCENTAGE', Decimal('5.00'))
        service_fee_fixed = getattr(settings, 'SERVICE_FEE_FIXED', Decimal('50.00'))

        tax_amount = round_currency(total_gross * (tax_rate / Decimal('100.00')))
        service_fee = round_currency(service_fee_fixed)
        net_amount = round_currency(total_gross + tax_amount + service_fee)

        # 5. Create Booking Record
        booking = Booking.objects.create(
            user=user,
            passenger_name=passenger_name.strip(),
            passenger_phone=passenger_phone.strip(),
            passenger_email=passenger_email.strip(),
            trip=trip,
            from_stop=from_stop,
            to_stop=to_stop,
            from_stop_order=from_stop.stop_order,
            to_stop_order=to_stop.stop_order,
            status=BookingStatus.PENDING,
            total_gross_fare=round_currency(total_gross),
            discount_amount=Decimal('0.00'),
            tax_amount=tax_amount,
            service_fee=service_fee,
            net_amount=net_amount,
            payment_method=payment_method,
            expires_at=now + timedelta(minutes=15)
        )

        # 6. Create BookingSeats with status LOCKED
        p_by_seat = {str(p['seat_id']): p for p in passengers_data}
        booking_seats_to_create = []

        for seat, fare in seat_prices:
            p_info = p_by_seat[str(seat.id)]
            booking_seats_to_create.append(
                BookingSeat(
                    booking=booking,
                    trip=trip,
                    seat=seat,
                    passenger_name=p_info['passenger_name'].strip(),
                    gender=p_info.get('gender', Gender.MALE).upper(),
                    age=p_info.get('age'),
                    cnic_or_id=p_info.get('cnic_or_id', '').strip(),
                    price=fare,
                    from_stop=from_stop,
                    to_stop=to_stop,
                    from_stop_order=from_stop.stop_order,
                    to_stop_order=to_stop.stop_order,
                    status=BookingSeatStatus.LOCKED
                )
            )

        BookingSeat.objects.bulk_create(booking_seats_to_create)

        # 7. Release temporary SeatLocks (now secured by BookingSeat.status=LOCKED)
        SeatLock.objects.filter(id__in=[l.id for l in active_locks]).delete()

        return booking

    @classmethod
    @transaction.atomic
    def confirm_counter_booking(
        cls,
        trip: Trip,
        from_stop: RouteStop,
        to_stop: RouteStop,
        agent_user,
        passenger_name: str,
        passenger_phone: str,
        passengers_data: List[Dict[str, Any]],
        passenger_email: str = "",
        payment_method: str = PaymentMethod.CASH_COUNTER
    ) -> Booking:
        """
        Direct Walk-In counter booking by counter agents or operators with immediate cash payment.
        Bypasses web lock tokens by executing an immediate atomic lock + confirm transaction.
        """
        # Role check
        if agent_user.role not in [UserRole.COUNTER_AGENT, UserRole.OPERATOR, UserRole.SUPER_ADMIN]:
            raise BusinessLogicError("Only counter agents or operators can execute counter walk-in bookings.")

        if from_stop.stop_order >= to_stop.stop_order:
            raise BusinessLogicError("Boarding stop must precede alighting stop.")

        if not passengers_data:
            raise BusinessLogicError("At least one passenger/seat assignment required.")

        seat_ids = [p['seat_id'] for p in passengers_data]
        unique_seat_ids = list(dict.fromkeys(seat_ids))
        if len(unique_seat_ids) != len(seat_ids):
            raise BusinessLogicError("Duplicate seats in passenger assignments.")

        from_order = from_stop.stop_order
        to_order = to_stop.stop_order
        now = timezone.now()

        # Row lock seats
        seats = list(
            Seat.objects.select_for_update().filter(
                id__in=unique_seat_ids,
                bus_id=trip.bus_id,
                is_active=True
            )
        )
        if len(seats) != len(unique_seat_ids):
            raise BusinessLogicError("One or more selected seats are invalid or not on this vehicle.")

        seat_map = {str(s.id): s for s in seats}

        # Check blocks
        if TripSeatBlock.objects.filter(trip=trip, seat_id__in=unique_seat_ids).exists():
            raise BusinessLogicError("One of the selected seats is administratively blocked for this trip.")

        # Check segment overlap against BookingSeat
        colliding_bookings = BookingSeat.objects.filter(
            trip=trip,
            seat_id__in=unique_seat_ids,
            status__in=[BookingSeatStatus.LOCKED, BookingSeatStatus.BOOKED],
            from_stop_order__lt=to_order,
            to_stop_order__gt=from_order
        )
        if colliding_bookings.exists():
            c_seat = colliding_bookings.first().seat.seat_number
            raise BusinessLogicError(f"Seat '{c_seat}' is already booked on an overlapping segment.")

        # Check active seat locks
        colliding_locks = SeatLock.objects.filter(
            trip=trip,
            seat_id__in=unique_seat_ids,
            locked_until__gt=now,
            from_stop_order__lt=to_order,
            to_stop_order__gt=from_order
        )
        if colliding_locks.exists():
            l_seat = colliding_locks.first().seat.seat_number
            raise BusinessLogicError(f"Seat '{l_seat}' is currently held by an online checkout.")

        # Gender validation
        for p in passengers_data:
            seat = seat_map[str(p['seat_id'])]
            gender = p.get('gender', Gender.MALE).upper()
            if seat.is_ladies_only and gender != Gender.FEMALE:
                raise BusinessLogicError(
                    f"Seat '{seat.seat_number}' is reserved for female passengers only."
                )

        # Compute fares
        total_gross = Decimal('0.00')
        seat_prices = []
        for p in passengers_data:
            seat = seat_map[str(p['seat_id'])]
            fare = FareService.calculate_fare(trip, from_stop, to_stop, seat_type=seat.seat_type)
            seat_prices.append((seat, fare))
            total_gross += fare

        tax_rate = getattr(settings, 'TAX_RATE_PERCENTAGE', Decimal('5.00'))
        service_fee_fixed = getattr(settings, 'SERVICE_FEE_FIXED', Decimal('50.00'))

        tax_amount = round_currency(total_gross * (tax_rate / Decimal('100.00')))
        service_fee = round_currency(service_fee_fixed)
        net_amount = round_currency(total_gross + tax_amount + service_fee)

        # Create CONFIRMED booking
        booking = Booking.objects.create(
            user=None,  # Walk-in passenger
            passenger_name=passenger_name.strip(),
            passenger_phone=passenger_phone.strip(),
            passenger_email=passenger_email.strip(),
            booked_by_agent=agent_user,
            trip=trip,
            from_stop=from_stop,
            to_stop=to_stop,
            from_stop_order=from_order,
            to_stop_order=to_order,
            status=BookingStatus.CONFIRMED,
            total_gross_fare=round_currency(total_gross),
            discount_amount=Decimal('0.00'),
            tax_amount=tax_amount,
            service_fee=service_fee,
            net_amount=net_amount,
            payment_method=payment_method,
            confirmed_at=now
        )

        p_by_seat = {str(p['seat_id']): p for p in passengers_data}
        booking_seats = []
        for seat, fare in seat_prices:
            p_info = p_by_seat[str(seat.id)]
            booking_seats.append(
                BookingSeat(
                    booking=booking,
                    trip=trip,
                    seat=seat,
                    passenger_name=p_info['passenger_name'].strip(),
                    gender=p_info.get('gender', Gender.MALE).upper(),
                    age=p_info.get('age'),
                    cnic_or_id=p_info.get('cnic_or_id', '').strip(),
                    price=fare,
                    from_stop=from_stop,
                    to_stop=to_stop,
                    from_stop_order=from_order,
                    to_stop_order=to_order,
                    status=BookingSeatStatus.BOOKED
                )
            )

        BookingSeat.objects.bulk_create(booking_seats)

        # Automatically issue cryptographically signed tickets for walk-in booking
        try:
            from tickets.services import TicketService
            TicketService.issue_tickets_for_booking(booking)
        except Exception:
            pass

        return booking

    @classmethod
    @transaction.atomic
    def confirm_payment_for_booking(cls, booking: Booking, payment_method: str = "") -> Booking:
        """
        Transitions PENDING booking to CONFIRMED upon successful payment.
        Updates all associated seats from LOCKED -> BOOKED.
        """
        if booking.status != BookingStatus.PENDING:
            raise BusinessLogicError(f"Booking {booking.pnr} cannot be confirmed because status is '{booking.status}'.")

        booking.status = BookingStatus.CONFIRMED
        booking.confirmed_at = timezone.now()
        if payment_method:
            booking.payment_method = payment_method
        booking.save(update_fields=['status', 'confirmed_at', 'payment_method', 'updated_at'])

        booking.seats.filter(status=BookingSeatStatus.LOCKED).update(status=BookingSeatStatus.BOOKED)
        return booking

    @classmethod
    @transaction.atomic
    def cancel_booking(cls, booking: Booking, cancelled_by=None, reason: str = "") -> Booking:
        """
        Cancels an entire booking, releasing all seats back into the available pool.
        """
        if booking.status in [BookingStatus.CANCELLED, BookingStatus.COMPLETED]:
            raise BusinessLogicError(f"Booking {booking.pnr} is already {booking.get_status_display().lower()}.")

        booking.status = BookingStatus.CANCELLED
        booking.save(update_fields=['status', 'updated_at'])

        booking.seats.update(status=BookingSeatStatus.CANCELLED)
        return booking

    @classmethod
    @transaction.atomic
    def cancel_seat(cls, booking: Booking, booking_seat: BookingSeat) -> Tuple[Booking, Decimal]:
        """
        Cancels an individual seat from a multi-seat booking.
        Calculates prorated refund and updates booking status to PARTIALLY_CANCELLED or CANCELLED.
        """
        if booking_seat.booking_id != booking.id:
            raise BusinessLogicError("Seat does not belong to this booking.")

        if booking_seat.status == BookingSeatStatus.CANCELLED:
            raise BusinessLogicError(f"Seat '{booking_seat.seat.seat_number}' is already cancelled.")

        # Prorated calculation from Module 0 banking engine
        # Seat price proportion of total gross
        seat_price = booking_seat.price
        gross_total = booking.total_gross_fare

        if gross_total > Decimal('0.00'):
            seat_discount = round_currency(booking.discount_amount * (seat_price / gross_total))
        else:
            seat_discount = Decimal('0.00')

        seat_effective_fare = seat_price - seat_discount
        tax_rate = getattr(settings, 'TAX_RATE_PERCENTAGE', Decimal('5.00'))
        seat_tax = round_currency(seat_effective_fare * (tax_rate / Decimal('100.00')))

        # Total refund for this seat (service fees are non-refundable)
        refund_amount = round_currency(seat_effective_fare + seat_tax)

        # Mark seat cancelled
        booking_seat.status = BookingSeatStatus.CANCELLED
        booking_seat.save(update_fields=['status', 'updated_at'])

        # Check remaining active seats
        active_remaining = booking.seats.exclude(status=BookingSeatStatus.CANCELLED).exists()
        if active_remaining:
            booking.status = BookingStatus.PARTIALLY_CANCELLED
        else:
            booking.status = BookingStatus.CANCELLED

        booking.save(update_fields=['status', 'updated_at'])
        return booking, refund_amount
