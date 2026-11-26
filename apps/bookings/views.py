"""
API Views for Seat Locking, Bookings, Counter Ticketing, and Cancellations.
"""
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiResponse, OpenApiParameter

from common.constants import UserRole, BookingStatus
from common.pagination import StandardResultsSetPagination
from common.responses import api_success
from accounts.permissions import IsOperatorOrAgent
from routes.models import RouteStop
from trips.models import Trip
from .models import Booking, BookingSeat
from .serializers import (
    SeatLockRequestSerializer,
    UnlockSeatsRequestSerializer,
    CreateBookingRequestSerializer,
    CounterBookingRequestSerializer,
    BookingDetailSerializer,
)
from .services import SeatLockService, BookingService


class SeatLockView(APIView):
    """
    Real-Time Distributed Seat Locking Endpoint.
    Holds up to 6 seats for 10 minutes on a specific trip segment [from_stop, to_stop).
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Lock Seats on a Trip Segment (10-minute hold)",
        description=(
            "Acquires temporary exclusive locks on up to 6 physical seats on a trip segment. "
            "Returns a secret lock_token required to confirm the booking or release the hold. "
            "Guarantees that no other customer can book or hold overlapping segments of these seats."
        ),
        request=SeatLockRequestSerializer,
        responses={200: OpenApiResponse(description="Seats locked successfully.")},
        tags=["Bookings: Seat Locking"]
    )
    def post(self, request):
        serializer = SeatLockRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        trip = get_object_or_404(Trip.objects.select_related('route', 'bus'), id=data['trip_id'])
        from_stop = get_object_or_404(RouteStop.objects.select_related('terminal'), id=data['from_stop'])
        to_stop = get_object_or_404(RouteStop.objects.select_related('terminal'), id=data['to_stop'])

        user = request.user if request.user.is_authenticated else None
        session_key = request.session.session_key if hasattr(request, 'session') and request.session.session_key else ""

        result = SeatLockService.lock_seats(
            trip=trip,
            seat_ids=data['seat_ids'],
            from_stop=from_stop,
            to_stop=to_stop,
            user=user,
            session_key=session_key or "",
            lock_duration_minutes=data.get('lock_duration_minutes', 10),
            seat_genders=data.get('seat_genders')
        )

        return api_success(
            data=result,
            message=f"Successfully locked {len(data['seat_ids'])} seat(s) for 10 minutes.",
            status_code=status.HTTP_200_OK
        )


class SeatUnlockView(APIView):
    """
    Releases temporary seat locks held by a lock_token.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Release Held Seat Locks",
        description="Releases all seats locked under the specified lock_token back into the available pool.",
        request=UnlockSeatsRequestSerializer,
        responses={200: OpenApiResponse(description="Locks released.")},
        tags=["Bookings: Seat Locking"]
    )
    def post(self, request):
        serializer = UnlockSeatsRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        deleted = SeatLockService.release_locks(lock_token=data['lock_token'])
        return api_success(
            data={"released_seats_count": deleted},
            message=f"Released {deleted} held seat(s)."
        )


class BookingCreateView(APIView):
    """
    Transforms held SeatLocks into a formal PENDING booking.
    Enforces gender seating invariants and computes authoritative financial totals.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Create Booking from Locked Seats",
        description=(
            "Converts previously held seat locks into a PENDING booking with a unique PNR. "
            "Requires passenger names and gender for every locked seat to enforce ladies-only seating rules. "
            "Computes authoritative gross fare, 5% sales tax, and 50 PKR service fee."
        ),
        request=CreateBookingRequestSerializer,
        responses={201: BookingDetailSerializer},
        tags=["Bookings: Reservation"]
    )
    def post(self, request):
        serializer = CreateBookingRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        trip = get_object_or_404(Trip.objects.select_related('route', 'bus'), id=data['trip_id'])
        from_stop = get_object_or_404(RouteStop.objects.select_related('terminal'), id=data['from_stop'])
        to_stop = get_object_or_404(RouteStop.objects.select_related('terminal'), id=data['to_stop'])

        user = request.user if request.user.is_authenticated else None

        booking = BookingService.create_booking_from_locks(
            trip=trip,
            lock_token=data['lock_token'],
            from_stop=from_stop,
            to_stop=to_stop,
            passenger_name=data['passenger_name'],
            passenger_phone=data['passenger_phone'],
            passenger_email=data.get('passenger_email', ''),
            passengers_data=data['passengers'],
            user=user,
            payment_method=data.get('payment_method'),
            coupon_code=data.get('coupon_code', '')
        )

        resp_serializer = BookingDetailSerializer(booking)
        return api_success(
            data=resp_serializer.data,
            message=f"Booking created with PNR {booking.pnr} in PENDING state.",
            status_code=status.HTTP_201_CREATED
        )


class CounterBookingView(APIView):
    """
    Direct in-person walk-in ticketing for Counter Agents and Operators.
    Creates an immediately CONFIRMED booking with cash or card payment.
    """
    permission_classes = [IsAuthenticated, IsOperatorOrAgent]

    @extend_schema(
        summary="Counter Walk-In Ticket Booking (Agent / Operator)",
        description=(
            "Allows ticket counter agents and operators to issue immediate CONFIRMED tickets "
            "for walk-in passengers. Bypasses web lock tokens by executing atomic lock and confirmation."
        ),
        request=CounterBookingRequestSerializer,
        responses={201: BookingDetailSerializer},
        tags=["Bookings: Reservation"]
    )
    def post(self, request):
        serializer = CounterBookingRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        trip = get_object_or_404(Trip.objects.select_related('route', 'bus'), id=data['trip_id'])
        from_stop = get_object_or_404(RouteStop.objects.select_related('terminal'), id=data['from_stop'])
        to_stop = get_object_or_404(RouteStop.objects.select_related('terminal'), id=data['to_stop'])

        booking = BookingService.confirm_counter_booking(
            trip=trip,
            from_stop=from_stop,
            to_stop=to_stop,
            agent_user=request.user,
            passenger_name=data['passenger_name'],
            passenger_phone=data['passenger_phone'],
            passenger_email=data.get('passenger_email', ''),
            passengers_data=data['passengers'],
            payment_method=data.get('payment_method')
        )

        resp_serializer = BookingDetailSerializer(booking)
        return api_success(
            data=resp_serializer.data,
            message=f"Walk-in counter booking {booking.pnr} issued and confirmed.",
            status_code=status.HTTP_201_CREATED
        )


class ActiveSeatLockView(APIView):
    """
    GET /api/bookings/active-lock/
    Recovers the authenticated user's active un-expired SeatLock for mobile resume flow.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Recover Active Seat Lock",
        description="Returns active held seats and remaining countdown seconds for the logged-in user.",
        tags=["Bookings: Seat Locking"]
    )
    def get(self, request):
        active_lock = SeatLockService.get_active_lock_for_user(request.user)
        return api_success(
            data=active_lock,
            message="Active seat lock retrieved." if active_lock else "No active seat lock found."
        )


class BookingListView(APIView):
    """
    List bookings: passengers see their own bookings; operators/counter agents see all.
    Supports ?filter=upcoming|past|cancelled|pending and optional ?page=&page_size= pagination.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List Bookings",
        description="Returns list of bookings. Supports filter (upcoming, past, cancelled, pending) and optional pagination.",
        parameters=[
            OpenApiParameter(name='filter', type=str, required=False, description="upcoming, past, cancelled, pending"),
            OpenApiParameter(name='page', type=int, required=False),
            OpenApiParameter(name='page_size', type=int, required=False),
        ],
        responses={200: BookingDetailSerializer(many=True)},
        tags=["Bookings: Management"]
    )
    def get(self, request):
        user = request.user
        if user.role in [UserRole.OPERATOR, UserRole.COUNTER_AGENT, UserRole.SUPER_ADMIN] or user.is_staff:
            bookings_qs = Booking.objects.all()
        else:
            bookings_qs = Booking.objects.filter(user=user)

        filter_param = (request.query_params.get('filter') or '').strip().lower()
        now = timezone.now()
        if filter_param == 'upcoming':
            bookings_qs = bookings_qs.filter(
                status__in=[BookingStatus.CONFIRMED, BookingStatus.PARTIALLY_CANCELLED],
                trip__departure_datetime__gte=now
            )
        elif filter_param == 'past':
            bookings_qs = bookings_qs.filter(
                status__in=[BookingStatus.CONFIRMED, BookingStatus.COMPLETED, BookingStatus.PARTIALLY_CANCELLED],
                trip__departure_datetime__lt=now
            )
        elif filter_param == 'cancelled':
            bookings_qs = bookings_qs.filter(
                status__in=[BookingStatus.CANCELLED, BookingStatus.REFUNDED, BookingStatus.EXPIRED]
            )
        elif filter_param == 'pending':
            bookings_qs = bookings_qs.filter(status=BookingStatus.PENDING)

        bookings_qs = bookings_qs.select_related(
            'trip', 'trip__route', 'trip__bus', 'from_stop__terminal', 'to_stop__terminal'
        ).prefetch_related('seats', 'seats__seat')

        if 'page' in request.query_params or 'page_size' in request.query_params:
            paginator = StandardResultsSetPagination()
            page = paginator.paginate_queryset(bookings_qs, request, view=self)
            serializer = BookingDetailSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        bookings_slice = bookings_qs[:50]
        serializer = BookingDetailSerializer(bookings_slice, many=True)
        return api_success(
            data={"total": bookings_qs.count(), "bookings": serializer.data},
            message="Bookings retrieved successfully."
        )


class BookingDetailView(APIView):
    """
    Retrieve booking details by unique PNR.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Lookup Booking by PNR",
        description="Lookup complete e-ticket details, passenger manifest, and payment summary by unique PNR reference.",
        responses={200: BookingDetailSerializer},
        tags=["Bookings: Management"]
    )
    def get(self, request, pnr):
        booking = get_object_or_404(
            Booking.objects.select_related(
                'trip', 'trip__route', 'trip__bus', 'from_stop__terminal', 'to_stop__terminal'
            ).prefetch_related('seats', 'seats__seat'),
            pnr=pnr.strip().upper()
        )
        serializer = BookingDetailSerializer(booking)
        return api_success(
            data=serializer.data,
            message=f"Booking {booking.pnr} retrieved."
        )


class BookingCancelView(APIView):
    """
    Cancels a booking, releasing all seats back into the available inventory.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Cancel Booking",
        description="Cancels an entire booking reservation. Normal passengers can only cancel their own bookings.",
        responses={200: BookingDetailSerializer},
        tags=["Bookings: Management"]
    )
    def post(self, request, pnr):
        booking = get_object_or_404(
            Booking.objects.select_related('trip', 'user').prefetch_related('seats'),
            pnr=pnr.strip().upper()
        )

        user = request.user
        is_staff_or_agent = (
            user.role in [UserRole.OPERATOR, UserRole.COUNTER_AGENT, UserRole.SUPER_ADMIN] or user.is_staff
        )
        if not is_staff_or_agent and booking.user_id != user.id:
            return api_success(
                data=None,
                message="You do not have permission to cancel this booking.",
                status_code=status.HTTP_403_FORBIDDEN
            )

        cancelled_booking = BookingService.cancel_booking(booking=booking, cancelled_by=user)
        serializer = BookingDetailSerializer(cancelled_booking)
        return api_success(
            data=serializer.data,
            message=f"Booking {cancelled_booking.pnr} cancelled successfully. Seats released."
        )
