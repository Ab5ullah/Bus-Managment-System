"""
Authoritative Domain Service Layer for Tickets, QR Codes & Conductor Boarding Validation.
Guarantees:
- Cryptographic HMAC-SHA256 verification of QR tokens
- Atomic double-boarding protection using select_for_update()
- Real-time conductor passenger manifest generation with boarding statistics
- Automatic ticket issuance for confirmed online & walk-in reservations
"""
import uuid
from typing import List, Dict, Any, Optional
from django.db import transaction
from django.utils import timezone
from common.exceptions import BusinessLogicError
from common.constants import (
    BookingStatus,
    BookingSeatStatus,
    TicketStatus,
)
from bookings.models import Booking, BookingSeat
from trips.models import Trip
from .models import Ticket, generate_ticket_number
from .crypto import sign_qr_payload, verify_qr_payload, generate_qr_code_image_base64


class TicketService:
    """
    Core Domain Service managing ticket lifecycle, conductor validation, and manifests.
    """

    @classmethod
    @transaction.atomic
    def issue_tickets_for_booking(cls, booking: Booking) -> List[Ticket]:
        """
        Generates individual cryptographically signed travel tickets
        for all active booked seats in a CONFIRMED booking.
        Idempotent: returns existing tickets if already issued.
        """
        if booking.status != BookingStatus.CONFIRMED:
            raise BusinessLogicError(
                f"Tickets can only be issued for CONFIRMED bookings. Current status: '{booking.status}'."
            )

        issued_tickets = []
        confirmed_seats = booking.seats.filter(status=BookingSeatStatus.BOOKED).select_related(
            'seat', 'from_stop__terminal', 'to_stop__terminal', 'trip__bus'
        )

        for seat_record in confirmed_seats:
            # Check if ticket already exists for this seat
            existing_ticket = Ticket.objects.filter(booking_seat=seat_record).first()
            if existing_ticket:
                issued_tickets.append(existing_ticket)
                continue

            ticket_num = generate_ticket_number()
            payload = {
                "ticket_number": ticket_num,
                "pnr": booking.pnr,
                "trip_id": str(booking.trip_id),
                "seat_number": seat_record.seat.seat_number,
                "passenger_name": seat_record.passenger_name,
                "gender": seat_record.gender,
                "cnic_or_id": seat_record.cnic_or_id,
                "from_stop": seat_record.from_stop.terminal.city,
                "to_stop": seat_record.to_stop.terminal.city,
                "issued_at": timezone.now().isoformat()
            }
            qr_token = sign_qr_payload(payload)

            ticket = Ticket.objects.create(
                booking_seat=seat_record,
                ticket_number=ticket_num,
                qr_token=qr_token,
                status=TicketStatus.ISSUED
            )
            issued_tickets.append(ticket)

        return issued_tickets

    @classmethod
    def validate_qr_token(
        cls,
        qr_token: str,
        trip_id: Optional[uuid.UUID] = None
    ) -> Dict[str, Any]:
        """
        Cryptographically validates a scanned QR code token and returns
        authorization status and passenger manifest details.
        """
        is_valid_sig, payload, sig_msg = verify_qr_payload(qr_token)
        if not is_valid_sig or not payload:
            return {
                "is_valid": False,
                "can_board": False,
                "message": sig_msg,
                "ticket": None,
                "payload": None
            }

        ticket_number = payload.get('ticket_number')
        ticket = Ticket.objects.filter(ticket_number=ticket_number).select_related(
            'booking_seat__booking',
            'booking_seat__seat',
            'booking_seat__from_stop__terminal',
            'booking_seat__to_stop__terminal',
            'booking_seat__trip__bus',
            'boarded_by'
        ).first()

        if not ticket:
            return {
                "is_valid": True,
                "can_board": False,
                "message": f"Ticket '{ticket_number}' not found in active database.",
                "ticket": None,
                "payload": payload
            }

        # Check trip mismatch
        if trip_id and ticket.trip.id != trip_id:
            return {
                "is_valid": True,
                "can_board": False,
                "message": f"Wrong Trip: Ticket is for Trip '{ticket.trip.trip_number}', not current trip.",
                "ticket": ticket,
                "payload": payload
            }

        # Check ticket status
        if ticket.status == TicketStatus.BOARDED:
            conductor_label = ticket.boarded_by.phone if ticket.boarded_by else "Staff"
            time_str = ticket.boarded_at.strftime("%H:%M:%S") if ticket.boarded_at else ""
            return {
                "is_valid": True,
                "can_board": False,
                "message": f"Already Boarded: Passenger boarded at {time_str} by {conductor_label}.",
                "ticket": ticket,
                "payload": payload
            }

        if ticket.status == TicketStatus.CANCELLED:
            return {
                "is_valid": True,
                "can_board": False,
                "message": "Boarding Denied: Ticket has been cancelled.",
                "ticket": ticket,
                "payload": payload
            }

        if ticket.status == TicketStatus.EXPIRED:
            return {
                "is_valid": True,
                "can_board": False,
                "message": "Boarding Denied: Ticket has expired.",
                "ticket": ticket,
                "payload": payload
            }

        return {
            "is_valid": True,
            "can_board": True,
            "message": "Valid ticket. Cleared for boarding.",
            "ticket": ticket,
            "payload": payload
        }

    @classmethod
    @transaction.atomic
    def board_passenger(
        cls,
        qr_token: Optional[str] = None,
        ticket_number: Optional[str] = None,
        trip_id: Optional[uuid.UUID] = None,
        conductor_user=None,
        boarding_stop=None
    ) -> Ticket:
        """
        Executes boarding validation with select_for_update() row lock.
        Prevents double-boarding, updates ticket state to BOARDED,
        and logs conductor audit data.
        """
        ticket_qs = Ticket.objects.select_for_update().select_related(
            'booking_seat__booking', 'booking_seat__seat', 'booking_seat__trip', 'boarded_by'
        )

        ticket = None
        if qr_token:
            is_valid_sig, payload, sig_msg = verify_qr_payload(qr_token)
            if not is_valid_sig or not payload:
                raise BusinessLogicError(sig_msg)
            t_num = payload.get('ticket_number')
            ticket = ticket_qs.filter(ticket_number=t_num).first()
        elif ticket_number:
            ticket = ticket_qs.filter(ticket_number=ticket_number.strip().upper()).first()
        else:
            raise BusinessLogicError("Either qr_token or ticket_number must be provided.")

        if not ticket:
            raise BusinessLogicError("Ticket record not found.")

        # Invariant 1: Trip match
        if trip_id and ticket.trip.id != trip_id:
            raise BusinessLogicError(
                f"Trip mismatch: Ticket is for Trip '{ticket.trip.trip_number}', not current trip."
            )

        # Invariant 2: Double-boarding anti-fraud guard
        if ticket.status == TicketStatus.BOARDED:
            c_name = ticket.boarded_by.phone if ticket.boarded_by else "Staff"
            t_str = ticket.boarded_at.strftime("%H:%M:%S") if ticket.boarded_at else "earlier"
            raise BusinessLogicError(
                f"Anti-Fraud Alert: Ticket '{ticket.ticket_number}' was already boarded at {t_str} by {c_name}."
            )

        # Invariant 3: Must be in ISSUED state
        if ticket.status != TicketStatus.ISSUED:
            raise BusinessLogicError(
                f"Boarding rejected: Ticket status is '{ticket.get_status_display()}'."
            )

        # Mark ticket as BOARDED
        now = timezone.now()
        ticket.status = TicketStatus.BOARDED
        ticket.boarded_at = now
        ticket.boarded_by = conductor_user
        if boarding_stop:
            ticket.boarding_station = boarding_stop
        ticket.save()

        # Update seat status
        ticket.booking_seat.status = BookingSeatStatus.COMPLETED
        ticket.booking_seat.save(update_fields=['status', 'updated_at'])

        return ticket

    @classmethod
    def get_trip_passenger_manifest(cls, trip: Trip) -> Dict[str, Any]:
        """
        Generates the authoritative passenger manifest and boarding statistics
        for a trip. Used by bus conductors, station masters, and dispatchers.
        """
        tickets = Ticket.objects.filter(
            booking_seat__trip=trip
        ).select_related(
            'booking_seat__booking',
            'booking_seat__seat',
            'booking_seat__from_stop__terminal',
            'booking_seat__to_stop__terminal',
            'boarded_by'
        ).order_by('booking_seat__seat__seat_number')

        total_seats = trip.bus.total_seats
        total_booked = tickets.count()
        total_boarded = tickets.filter(status=TicketStatus.BOARDED).count()
        pending_boarding = tickets.filter(status=TicketStatus.ISSUED).count()
        cancelled_count = tickets.filter(status=TicketStatus.CANCELLED).count()

        manifest_entries = []
        for t in tickets:
            bs = t.booking_seat
            manifest_entries.append({
                "ticket_id": str(t.id),
                "ticket_number": t.ticket_number,
                "pnr": bs.booking.pnr,
                "seat_number": bs.seat.seat_number,
                "is_ladies_only": bs.seat.is_ladies_only,
                "passenger_name": bs.passenger_name,
                "gender": bs.gender,
                "age": bs.age,
                "cnic_or_id": bs.cnic_or_id,
                "phone": bs.booking.passenger_phone,
                "from_terminal": bs.from_stop.terminal.city,
                "to_terminal": bs.to_stop.terminal.city,
                "price": str(bs.price),
                "status": t.status,
                "boarded_at": t.boarded_at.isoformat() if t.boarded_at else None,
                "boarded_by": t.boarded_by.phone if t.boarded_by else None,
                "qr_token": t.qr_token,
                "qr_data_uri": generate_qr_code_image_base64(t.qr_token)
            })

        return {
            "trip_id": str(trip.id),
            "trip_number": trip.trip_number,
            "route_name": trip.route.name,
            "bus_registration": trip.bus.registration_number,
            "departure_datetime": trip.departure_datetime.isoformat(),
            "stats": {
                "total_seats": total_seats,
                "total_booked": total_booked,
                "total_boarded": total_boarded,
                "pending_boarding": pending_boarding,
                "cancelled_count": cancelled_count,
                "occupancy_percentage": round((total_booked / total_seats * 100), 1) if total_seats else 0
            },
            "passengers": manifest_entries
        }
