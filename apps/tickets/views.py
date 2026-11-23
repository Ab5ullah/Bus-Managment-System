"""
DRF Views for Module 8: Tickets, QR Codes & Conductor Validation.
"""
from django.http import FileResponse
from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiResponse
from common.responses import api_success, api_error
from common.exceptions import BusinessLogicError
from common.constants import UserRole
from trips.models import Trip
from bookings.models import Booking
from .models import Ticket
from .services import TicketService
from .pdf_generator import generate_ticket_pdf
from .serializers import (
    TicketSerializer,
    ValidateQrRequestSerializer,
    BoardPassengerRequestSerializer,
)


class TicketDetailView(APIView):
    """
    GET /api/tickets/{ticket_number}/
    Retrieves full ticket manifest, journey details, and Base64 QR code image.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(summary="Get Ticket Details", responses={200: TicketSerializer}, tags=["Tickets & Boarding"])
    def get(self, request, ticket_number: str):
        ticket = Ticket.objects.select_related(
            'booking_seat__booking',
            'booking_seat__seat',
            'booking_seat__from_stop__terminal',
            'booking_seat__to_stop__terminal',
            'booking_seat__trip__bus',
            'boarded_by'
        ).filter(ticket_number=ticket_number.strip().upper()).first()

        if not ticket:
            raise BusinessLogicError(f"Ticket '{ticket_number}' not found.")

        return api_success(
            data=TicketSerializer(ticket).data,
            message="Ticket details retrieved."
        )


class TicketPdfDownloadView(APIView):
    """
    GET /api/tickets/{ticket_number}/pdf/
    Streams official printable PDF boarding pass with embedded high-resolution QR code.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Download Printable PDF E-Ticket",
        description="Generates an on-the-fly, high-resolution PDF boarding pass with travel manifest and signed QR code.",
        tags=["Tickets & Boarding"]
    )
    def get(self, request, ticket_number: str):
        ticket = Ticket.objects.select_related(
            'booking_seat__booking',
            'booking_seat__seat',
            'booking_seat__from_stop__terminal',
            'booking_seat__to_stop__terminal',
            'booking_seat__trip__bus',
        ).filter(ticket_number=ticket_number.strip().upper()).first()

        if not ticket:
            raise BusinessLogicError(f"Ticket '{ticket_number}' not found.")

        pdf_buffer = generate_ticket_pdf(ticket)
        filename = f"{ticket.ticket_number}.pdf"

        response = FileResponse(pdf_buffer, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        return response


class ValidateQrView(APIView):
    """
    POST /api/tickets/validate-qr/
    Conductor Gate Scanner: Decodes & cryptographically verifies QR token.
    Detects tampering, trip mismatch, or double-boarding prior to committing board action.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Validate Scanned QR Code",
        description="Validates cryptographic authenticity of a scanned QR token and checks boarding eligibility.",
        request=ValidateQrRequestSerializer,
        tags=["Tickets & Boarding"]
    )
    def post(self, request):
        serializer = ValidateQrRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result = TicketService.validate_qr_token(
            qr_token=data['qr_token'],
            trip_id=data.get('trip_id')
        )

        ticket_data = TicketSerializer(result['ticket']).data if result.get('ticket') else None

        return api_success(
            data={
                "is_valid": result['is_valid'],
                "can_board": result['can_board'],
                "ticket": ticket_data,
                "token_payload": result.get('payload')
            },
            message=result['message']
        )


class BoardPassengerView(APIView):
    """
    POST /api/tickets/board/
    Records passenger boarding at the gate.
    Atomically updates ticket state to BOARDED with conductor ID and timestamp.
    Strictly prevents double boarding.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Board Passenger at Gate",
        description="Commits boarding for a passenger using either QR token or ticket number. Guarantees anti-fraud double boarding prevention.",
        request=BoardPassengerRequestSerializer,
        tags=["Tickets & Boarding"]
    )
    def post(self, request):
        serializer = BoardPassengerRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        conductor_user = request.user if request.user.is_authenticated else None

        ticket = TicketService.board_passenger(
            qr_token=data.get('qr_token'),
            ticket_number=data.get('ticket_number'),
            trip_id=data.get('trip_id'),
            conductor_user=conductor_user
        )

        return api_success(
            data=TicketSerializer(ticket).data,
            message=f"Passenger '{ticket.passenger_name}' (Seat {ticket.seat_number}) boarded successfully!"
        )


class BookingTicketsListView(APIView):
    """
    GET /api/tickets/booking/{pnr}/
    Returns all issued e-tickets for a confirmed booking PNR.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(summary="List Tickets for Booking PNR", tags=["Tickets & Boarding"])
    def get(self, request, pnr: str):
        booking = Booking.objects.filter(pnr=pnr.strip().upper()).first()
        if not booking:
            raise BusinessLogicError(f"Booking with PNR '{pnr}' not found.")

        # Ensure tickets exist for confirmed booking
        if booking.status == 'CONFIRMED' and not Ticket.objects.filter(booking_seat__booking=booking).exists():
            TicketService.issue_tickets_for_booking(booking)

        tickets = Ticket.objects.filter(booking_seat__booking=booking).select_related(
            'booking_seat__seat',
            'booking_seat__from_stop__terminal',
            'booking_seat__to_stop__terminal',
            'booking_seat__trip__bus',
            'boarded_by'
        )

        return api_success(
            data=TicketSerializer(tickets, many=True).data,
            message=f"Tickets for booking '{booking.pnr}' retrieved."
        )


class TripManifestView(APIView):
    """
    GET /api/tickets/manifest/{trip_id}/
    Provides authoritative passenger manifest and boarding statistics for a trip.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(summary="Get Conductor Passenger Manifest", tags=["Tickets & Boarding"])
    def get(self, request, trip_id):
        try:
            trip = Trip.objects.select_related('bus', 'route').get(id=trip_id)
        except Trip.DoesNotExist:
            raise BusinessLogicError("Trip not found.")

        manifest_data = TicketService.get_trip_passenger_manifest(trip)
        return api_success(
            data=manifest_data,
            message=f"Passenger manifest for Trip '{trip.trip_number}' retrieved."
        )
