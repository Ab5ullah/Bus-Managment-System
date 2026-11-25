"""
Domain Service Layer for Module 9: Cancellation & Refund Engine.
Orchestrates tiered cancellation policies, prorated financial calculations,
atomic seat inventory release, ticket invalidation, and operator trip cancellation.
"""
import uuid
from decimal import Decimal
from typing import Optional, List, Dict, Any, Tuple

from django.db import transaction
from django.utils import timezone

from common.constants import (
    BookingStatus,
    BookingSeatStatus,
    TicketStatus,
    PaymentStatus,
    RefundStatus,
    TripStatus,
)
from common.exceptions import BusinessLogicError, ResourceNotFoundError
from common.money import round_currency
from bookings.models import Booking, BookingSeat, SeatLock
from trips.models import Trip
from payments.models import Payment
from payments.services import PaymentService
from .models import CancellationPolicy, Refund


class CancellationService:
    """
    Authoritative domain service managing passenger cancellations,
    refund calculations, inventory recovery, and operator bulk cancellations.
    """

    @classmethod
    def seed_default_policies(cls) -> List[CancellationPolicy]:
        """
        Idempotently creates standard multi-tier cancellation policies:
        - Tier 1: > 24 Hours before departure: 90% refund (10% fee)
        - Tier 2: 6 to 24 Hours before departure: 50% refund (50% fee)
        - Tier 3: < 6 Hours before departure: 0% refund (100% non-refundable)
        """
        defaults = [
            {
                'name': 'Advance Cancellation (>24h)',
                'min_hours_before_departure': 24,
                'refund_percentage': Decimal('90.00'),
                'cancellation_fee_fixed': Decimal('0.00'),
                'is_active': True,
                'description': 'Cancellations made more than 24 hours prior to departure receive a 90% fare and tax refund.'
            },
            {
                'name': 'Standard Cancellation (6-24h)',
                'min_hours_before_departure': 6,
                'refund_percentage': Decimal('50.00'),
                'cancellation_fee_fixed': Decimal('0.00'),
                'is_active': True,
                'description': 'Cancellations made between 6 and 24 hours prior to departure receive a 50% fare and tax refund.'
            },
            {
                'name': 'Late Cancellation (<6h)',
                'min_hours_before_departure': 0,
                'refund_percentage': Decimal('0.00'),
                'cancellation_fee_fixed': Decimal('0.00'),
                'is_active': True,
                'description': 'Cancellations made less than 6 hours prior to departure are strictly non-refundable.'
            },
        ]
        policies = []
        for d in defaults:
            policy, _ = CancellationPolicy.objects.get_or_create(
                min_hours_before_departure=d['min_hours_before_departure'],
                defaults=d
            )
            policies.append(policy)
        return policies

    @classmethod
    def get_applicable_policy(cls, trip: Trip) -> Optional[CancellationPolicy]:
        """
        Determines the active policy tier matching the time until trip departure.
        """
        # Ensure default policies exist in DB
        if not CancellationPolicy.objects.filter(is_active=True).exists():
            cls.seed_default_policies()

        now = timezone.now()
        hours_remaining = (trip.departure_datetime - now).total_seconds() / 3600.0

        if hours_remaining < 0:
            return None

        policy = CancellationPolicy.objects.filter(
            is_active=True,
            min_hours_before_departure__lte=max(0, int(hours_remaining))
        ).order_by('-min_hours_before_departure').first()

        return policy

    @classmethod
    def estimate_refund(
        cls,
        booking: Booking,
        seat_ids: Optional[List[uuid.UUID]] = None
    ) -> Dict[str, Any]:
        """
        Calculates refund projection breakdown without modifying database state.
        Handles both full booking cancellation and partial seat selection.
        """
        # 1. Resolve Target Seats
        if seat_ids:
            target_seats = booking.seats.filter(id__in=seat_ids, status=BookingSeatStatus.BOOKED)
            if not target_seats.exists():
                raise BusinessLogicError("None of the specified seats are currently active (BOOKED).")
        else:
            target_seats = booking.seats.filter(status=BookingSeatStatus.BOOKED)

        active_seat_count = target_seats.count()
        all_active_count = booking.seats.filter(status=BookingSeatStatus.BOOKED).count()

        # 2. Status Invariant Checks
        if booking.status in (BookingStatus.CANCELLED, BookingStatus.REFUNDED, BookingStatus.COMPLETED):
            raise BusinessLogicError(f"Booking {booking.pnr} is already {booking.status} and cannot be cancelled.")

        # 3. Departure Check
        now = timezone.now()
        hours_remaining = (booking.trip.departure_datetime - now).total_seconds() / 3600.0
        if hours_remaining <= 0:
            raise BusinessLogicError("Cannot cancel a booking for a trip that has already departed.")

        # 4. Boarding Check: Has any ticket already boarded?
        from tickets.models import Ticket
        boarded_tickets = Ticket.objects.filter(
            booking_seat__in=target_seats,
            status=TicketStatus.BOARDED
        )
        if boarded_tickets.exists():
            raise BusinessLogicError("Cannot cancel seat(s) because boarding has already occurred.")

        # 5. Pending Booking (Unpaid): 0 refund, immediate cancellation
        if booking.status == BookingStatus.PENDING:
            return {
                "booking_pnr": booking.pnr,
                "is_pending": True,
                "seats_count": active_seat_count,
                "hours_to_departure": round(hours_remaining, 2),
                "policy_name": "Unpaid Booking",
                "refund_percentage": Decimal('0.00'),
                "gross_fare_refunded": Decimal('0.00'),
                "tax_refunded": Decimal('0.00'),
                "service_fee_refunded": Decimal('0.00'),
                "cancellation_fee_charged": Decimal('0.00'),
                "net_refund_amount": Decimal('0.00'),
                "non_refundable_fee": Decimal('0.00'),
            }

        # 6. Resolve Policy
        policy = cls.get_applicable_policy(booking.trip)
        refund_pct = policy.refund_percentage if policy else Decimal('0.00')
        policy_name = policy.name if policy else "Late Cancellation (No Refund)"
        fixed_fee = policy.cancellation_fee_fixed if policy else Decimal('0.00')

        # 7. Financial Breakdown Calculations
        target_gross = sum(s.price for s in target_seats)
        total_gross = booking.total_gross_fare if booking.total_gross_fare > 0 else target_gross

        # Prorate discount and tax proportionally to seat price share
        if total_gross > Decimal('0.00'):
            ratio = target_gross / total_gross
            prorated_discount = round_currency(booking.discount_amount * ratio)
            prorated_tax = round_currency(booking.tax_amount * ratio)
        else:
            prorated_discount = Decimal('0.00')
            prorated_tax = Decimal('0.00')

        # Service fee is strictly non-refundable on customer cancellations
        fare_after_discount = max(Decimal('0.00'), target_gross - prorated_discount)

        multiplier = refund_pct / Decimal('100.00')
        gross_refund = round_currency(fare_after_discount * multiplier)
        tax_refund = round_currency(prorated_tax * multiplier)

        # Net refund = gross refund + tax refund - fixed administrative fee
        tentative_net = gross_refund + tax_refund - fixed_fee
        net_refund = max(Decimal('0.00'), round_currency(tentative_net))

        # Cancellation fee charged = total seat value (fare - discount + tax) - net refund
        total_seat_value = round_currency(fare_after_discount + prorated_tax)
        cancellation_fee = max(Decimal('0.00'), round_currency(total_seat_value - net_refund))

        return {
            "booking_pnr": booking.pnr,
            "is_pending": False,
            "seats_count": active_seat_count,
            "is_partial": (active_seat_count < all_active_count),
            "hours_to_departure": round(hours_remaining, 2),
            "policy_name": policy_name,
            "refund_percentage": refund_pct,
            "target_gross_fare": target_gross,
            "prorated_discount": prorated_discount,
            "prorated_tax": prorated_tax,
            "gross_fare_refunded": gross_refund,
            "tax_refunded": tax_refund,
            "service_fee_refunded": Decimal('0.00'),
            "cancellation_fee_charged": cancellation_fee,
            "net_refund_amount": net_refund,
            "non_refundable_service_fee": booking.service_fee,
        }

    @classmethod
    @transaction.atomic
    def cancel_booking(
        cls,
        pnr: str,
        reason: str = '',
        user=None
    ) -> Tuple[Booking, Optional[Refund]]:
        """
        Cancels an entire booking, voids all issued boarding tickets,
        releases all segment seats back into available inventory,
        and executes gateway refund.
        """
        # Row-lock booking and trip
        booking = Booking.objects.select_for_update().filter(pnr=pnr).first()
        if not booking:
            raise ResourceNotFoundError(f"Booking with PNR '{pnr}' not found.")

        # Invariant checks
        if booking.status in (BookingStatus.CANCELLED, BookingStatus.REFUNDED, BookingStatus.COMPLETED):
            raise BusinessLogicError(f"Booking {pnr} is already {booking.status} and cannot be cancelled.")

        # Handle PENDING booking (unpaid)
        if booking.status == BookingStatus.PENDING:
            booking.status = BookingStatus.CANCELLED
            booking.save(update_fields=['status', 'updated_at'])

            # Transition all seats to CANCELLED to free inventory
            BookingSeat.objects.filter(booking=booking).update(status=BookingSeatStatus.CANCELLED)

            # Purge any remaining temporary locks
            SeatLock.objects.filter(
                trip=booking.trip,
                from_stop_order=booking.from_stop_order,
                to_stop_order=booking.to_stop_order
            ).delete()

            return booking, None

        # Confirmed booking checks
        trip = Trip.objects.select_for_update().get(id=booking.trip_id)
        now = timezone.now()
        if trip.departure_datetime <= now:
            raise BusinessLogicError("Cannot cancel a booking for a trip that has already departed.")

        active_seats = list(booking.seats.select_for_update().filter(status=BookingSeatStatus.BOOKED))
        if not active_seats:
            raise BusinessLogicError("No active seats found in this booking to cancel.")

        # Verify no passenger on this booking has boarded
        from tickets.models import Ticket
        boarded_tickets = Ticket.objects.filter(
            booking_seat__in=active_seats,
            status=TicketStatus.BOARDED
        )
        if boarded_tickets.exists():
            ticket_nums = ", ".join(t.ticket_number for t in boarded_tickets)
            raise BusinessLogicError(f"Cannot cancel booking: Ticket(s) {ticket_nums} have already boarded.")

        # Calculate refund
        estimate = cls.estimate_refund(booking)
        policy = cls.get_applicable_policy(trip)

        # 1. Invalidate Tickets
        Ticket.objects.filter(booking_seat__in=active_seats).update(status=TicketStatus.CANCELLED)

        # 2. Release Seats back into inventory
        BookingSeat.objects.filter(id__in=[s.id for s in active_seats]).update(
            status=BookingSeatStatus.CANCELLED
        )

        # 3. Transition Booking Status
        booking.status = BookingStatus.CANCELLED
        booking.save(update_fields=['status', 'updated_at'])

        # 4. Create Audit Refund Record
        refund = Refund.objects.create(
            booking=booking,
            policy_applied=policy,
            gross_fare_refunded=estimate['gross_fare_refunded'],
            tax_refunded=estimate['tax_refunded'],
            cancellation_fee_charged=estimate['cancellation_fee_charged'],
            net_refund_amount=estimate['net_refund_amount'],
            status=RefundStatus.PROCESSED,
            is_operator_cancellation=False,
            reason=reason or "Customer requested full booking cancellation",
            processed_by=user
        )
        refund.seats.set(active_seats)

        # 5. Dispatch Payment Gateway Refund if funds are due
        if estimate['net_refund_amount'] > Decimal('0.00'):
            payment = booking.payments.filter(status=PaymentStatus.SUCCESS).first()
            if payment:
                PaymentService.process_refund(
                    payment_id=payment.id,
                    refund_amount=estimate['net_refund_amount'],
                    reason=f"Cancellation PNR {booking.pnr}: {reason}"
                )
                refund.payment = payment
                refund.save(update_fields=['payment'])

        try:
            from notifications.tasks import send_cancellation_notification
            send_cancellation_notification.delay(str(booking.id), str(refund.net_refund_amount))
        except Exception:
            pass

        return booking, refund

    @classmethod
    @transaction.atomic
    def cancel_seats(
        cls,
        pnr: str,
        seat_ids: List[uuid.UUID],
        reason: str = '',
        user=None
    ) -> Tuple[Booking, Refund]:
        """
        Cancels specific seats inside a booking, leaving non-cancelled seats intact.
        Transitions Booking to PARTIALLY_CANCELLED (or CANCELLED if all seats cancelled).
        Immediately frees segment availability for the cancelled seats.
        """
        if not seat_ids:
            raise BusinessLogicError("At least one seat ID must be specified for cancellation.")

        booking = Booking.objects.select_for_update().filter(pnr=pnr).first()
        if not booking:
            raise ResourceNotFoundError(f"Booking with PNR '{pnr}' not found.")

        if booking.status in (BookingStatus.CANCELLED, BookingStatus.REFUNDED, BookingStatus.COMPLETED):
            raise BusinessLogicError(f"Booking {pnr} is already {booking.status} and cannot be cancelled.")

        trip = Trip.objects.select_for_update().get(id=booking.trip_id)
        now = timezone.now()
        if trip.departure_datetime <= now:
            raise BusinessLogicError("Cannot cancel seat(s) for a trip that has already departed.")

        # Find target seats
        target_seats = list(booking.seats.select_for_update().filter(
            id__in=seat_ids,
            status=BookingSeatStatus.BOOKED
        ))
        if len(target_seats) != len(seat_ids):
            raise BusinessLogicError("One or more specified seats are not currently active in this booking.")

        # Verify no selected seat has already boarded
        from tickets.models import Ticket
        boarded_tickets = Ticket.objects.filter(
            booking_seat__in=target_seats,
            status=TicketStatus.BOARDED
        )
        if boarded_tickets.exists():
            ticket_nums = ", ".join(t.ticket_number for t in boarded_tickets)
            raise BusinessLogicError(f"Cannot cancel: Ticket(s) {ticket_nums} have already boarded.")

        # Calculate partial refund
        estimate = cls.estimate_refund(booking, seat_ids=[s.id for s in target_seats])
        policy = cls.get_applicable_policy(trip)

        # 1. Invalidate tickets for these specific seats
        Ticket.objects.filter(booking_seat__in=target_seats).update(status=TicketStatus.CANCELLED)

        # 2. Transition target seats to CANCELLED (instantly frees inventory)
        BookingSeat.objects.filter(id__in=[s.id for s in target_seats]).update(
            status=BookingSeatStatus.CANCELLED
        )

        # 3. Update Booking Status
        remaining_active_seats = booking.seats.filter(
            status__in=[BookingSeatStatus.BOOKED, BookingSeatStatus.COMPLETED]
        ).count()
        if remaining_active_seats == 0:
            booking.status = BookingStatus.CANCELLED
        else:
            booking.status = BookingStatus.PARTIALLY_CANCELLED
        booking.save(update_fields=['status', 'updated_at'])

        # 4. Create Refund Record
        ticket_ref = None
        if len(target_seats) == 1:
            ticket_ref = Ticket.objects.filter(booking_seat=target_seats[0]).first()

        refund = Refund.objects.create(
            booking=booking,
            ticket=ticket_ref,
            policy_applied=policy,
            gross_fare_refunded=estimate['gross_fare_refunded'],
            tax_refunded=estimate['tax_refunded'],
            cancellation_fee_charged=estimate['cancellation_fee_charged'],
            net_refund_amount=estimate['net_refund_amount'],
            status=RefundStatus.PROCESSED,
            is_operator_cancellation=False,
            reason=reason or f"Partial cancellation of {len(target_seats)} seat(s)",
            processed_by=user
        )
        refund.seats.set(target_seats)

        # 5. Dispatch Payment Refund
        if estimate['net_refund_amount'] > Decimal('0.00'):
            payment = booking.payments.filter(status=PaymentStatus.SUCCESS).first()
            if payment:
                PaymentService.process_refund(
                    payment_id=payment.id,
                    refund_amount=estimate['net_refund_amount'],
                    reason=f"Partial seat cancellation PNR {booking.pnr}: {reason}"
                )
                refund.payment = payment
                refund.save(update_fields=['payment'])

        try:
            from notifications.tasks import send_cancellation_notification
            send_cancellation_notification.delay(str(booking.id), str(refund.net_refund_amount))
        except Exception:
            pass

        return booking, refund

    @classmethod
    @transaction.atomic
    def cancel_trip_by_operator(
        cls,
        trip_id: uuid.UUID,
        reason: str = '',
        operator_user=None
    ) -> Dict[str, Any]:
        """
        Operator-initiated trip cancellation.
        Cancels the Trip, releases all seat inventory, voids all tickets,
        and executes 100% full refunds (including taxes and service fees)
        with zero administrative cancellation fee.
        """
        trip = Trip.objects.select_for_update().filter(id=trip_id).first()
        if not trip:
            raise ResourceNotFoundError(f"Trip with ID '{trip_id}' not found.")

        if trip.status in (TripStatus.COMPLETED, TripStatus.CANCELLED):
            raise BusinessLogicError(f"Trip {trip.trip_number} is already {trip.status} and cannot be cancelled.")

        # 1. Cancel Trip
        trip.status = TripStatus.CANCELLED
        trip.save(update_fields=['status', 'updated_at'])

        # 2. Clean up any remaining temporary locks
        SeatLock.objects.filter(trip=trip).delete()

        # 3. Find all active and pending bookings for this trip
        bookings = list(Booking.objects.select_for_update().filter(
            trip=trip,
            status__in=[BookingStatus.CONFIRMED, BookingStatus.PARTIALLY_CANCELLED, BookingStatus.PENDING]
        ))

        from tickets.models import Ticket

        cancelled_count = 0
        total_refunded = Decimal('0.00')

        for booking in bookings:
            # PENDING bookings: cancel immediately with no financial transaction
            if booking.status == BookingStatus.PENDING:
                booking.status = BookingStatus.CANCELLED
                booking.save(update_fields=['status', 'updated_at'])
                BookingSeat.objects.filter(booking=booking).update(status=BookingSeatStatus.CANCELLED)
                cancelled_count += 1
                continue

            # CONFIRMED or PARTIALLY_CANCELLED bookings: 100% full refund
            active_seats = list(booking.seats.select_for_update().filter(status=BookingSeatStatus.BOOKED))

            # Invalidate tickets
            Ticket.objects.filter(booking_seat__in=active_seats).update(status=TicketStatus.CANCELLED)

            # Invalidate seats
            BookingSeat.objects.filter(id__in=[s.id for s in active_seats]).update(
                status=BookingSeatStatus.CANCELLED
            )

            # Full 100% refund of net amount paid
            refund_amount = round_currency(booking.net_amount)

            # Create Refund Record
            refund = Refund.objects.create(
                booking=booking,
                policy_applied=None,  # Operator override bypasses policies
                gross_fare_refunded=booking.total_gross_fare,
                tax_refunded=booking.tax_amount,
                cancellation_fee_charged=Decimal('0.00'),
                net_refund_amount=refund_amount,
                status=RefundStatus.PROCESSED,
                is_operator_cancellation=True,
                reason=reason or f"Trip {trip.trip_number} cancelled by operator",
                processed_by=operator_user
            )
            refund.seats.set(active_seats)

            # Execute Gateway Refund
            if refund_amount > Decimal('0.00'):
                payment = booking.payments.filter(status=PaymentStatus.SUCCESS).first()
                if payment:
                    PaymentService.process_refund(
                        payment_id=payment.id,
                        refund_amount=refund_amount,
                        reason=f"Operator Trip Cancellation {trip.trip_number}: {reason}"
                    )
                    refund.payment = payment
                    refund.save(update_fields=['payment'])

            booking.status = BookingStatus.CANCELLED
            booking.save(update_fields=['status', 'updated_at'])

            cancelled_count += 1
            total_refunded += refund_amount

        return {
            "trip_id": str(trip.id),
            "trip_number": trip.trip_number,
            "status": trip.status,
            "bookings_cancelled_count": cancelled_count,
            "total_refunded": total_refunded,
            "reason": reason,
        }
