"""
API Views for Module 9: Cancellation & Refund Engine.
"""
from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.generics import ListAPIView
from drf_spectacular.utils import extend_schema, OpenApiResponse

from common.responses import api_success, api_error
from common.exceptions import BusinessLogicError, ResourceNotFoundError
from bookings.models import Booking
from .models import CancellationPolicy, Refund
from .serializers import (
    CancellationPolicySerializer,
    RefundSerializer,
    EstimateRefundRequestSerializer,
    CancelBookingRequestSerializer,
    CancelSeatsRequestSerializer,
    OperatorCancelTripRequestSerializer,
)
from .services import CancellationService


class CancellationPolicyListView(ListAPIView):
    """
    List all active cancellation policy tiers.
    """
    queryset = CancellationPolicy.objects.filter(is_active=True).order_by('-min_hours_before_departure')
    serializer_class = CancellationPolicySerializer
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="List Active Cancellation Policies",
        description="Returns all active cancellation tiers with threshold hours, refund percentages, and fixed fees.",
        responses={200: CancellationPolicySerializer(many=True)},
        tags=["Cancellations & Refunds"]
    )
    def list(self, request, *args, **kwargs):
        if not CancellationPolicy.objects.filter(is_active=True).exists():
            CancellationService.seed_default_policies()
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return api_success(
            data={"policies": serializer.data},
            message="Active cancellation policies retrieved successfully."
        )


class EstimateRefundView(APIView):
    """
    Simulate and preview refund calculation breakdown before confirming cancellation.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Estimate Cancellation Refund",
        description="Calculates gross fare, prorated tax, non-refundable platform fee, and net refund without modifying database state.",
        request=EstimateRefundRequestSerializer,
        responses={200: OpenApiResponse(description="Refund projection estimate")},
        tags=["Cancellations & Refunds"]
    )
    def post(self, request):
        serializer = EstimateRefundRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return api_error(
                message="Validation failed for one or more fields.",
                code="VALIDATION_ERROR",
                details=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST
            )

        pnr = serializer.validated_data['pnr'].strip().upper()
        seat_ids = serializer.validated_data.get('seat_ids', [])

        booking = Booking.objects.filter(pnr=pnr).first()
        if not booking:
            return api_error(
                message=f"Booking with PNR '{pnr}' not found.",
                code="RESOURCE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND
            )

        try:
            estimate = CancellationService.estimate_refund(booking, seat_ids=seat_ids if seat_ids else None)
            return api_success(data=estimate, message="Refund estimate calculated successfully.")
        except BusinessLogicError as exc:
            return api_error(message=str(exc), code="BUSINESS_RULE_VIOLATION", status_code=status.HTTP_400_BAD_REQUEST)


class CancelBookingView(APIView):
    """
    Cancel an entire booking, invalidate all issued tickets,
    release segment seats back into inventory, and execute gateway refund.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Cancel Entire Booking",
        description="Voids all tickets, frees all segment seats, updates booking status to CANCELLED, and triggers payment refund.",
        request=CancelBookingRequestSerializer,
        responses={200: OpenApiResponse(description="Booking cancelled and refund processed")},
        tags=["Cancellations & Refunds"]
    )
    def post(self, request):
        serializer = CancelBookingRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return api_error(
                message="Validation failed for one or more fields.",
                code="VALIDATION_ERROR",
                details=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST
            )

        pnr = serializer.validated_data['pnr'].strip().upper()
        reason = serializer.validated_data.get('reason', '')
        user = request.user if request.user.is_authenticated else None

        try:
            booking, refund = CancellationService.cancel_booking(pnr=pnr, reason=reason, user=user)
            refund_data = RefundSerializer(refund).data if refund else None
            return api_success(
                data={
                    "pnr": booking.pnr,
                    "status": booking.status,
                    "refund": refund_data,
                },
                message=f"Booking {booking.pnr} successfully cancelled."
            )
        except (BusinessLogicError, ResourceNotFoundError) as exc:
            status_code = status.HTTP_404_NOT_FOUND if isinstance(exc, ResourceNotFoundError) else status.HTTP_400_BAD_REQUEST
            code = "RESOURCE_NOT_FOUND" if isinstance(exc, ResourceNotFoundError) else "BUSINESS_RULE_VIOLATION"
            return api_error(message=str(exc), code=code, status_code=status_code)


class CancelSeatsView(APIView):
    """
    Cancel specific seats inside a booking, leaving non-cancelled seats intact.
    Transitions booking to PARTIALLY_CANCELLED and releases inventory for cancelled seats.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Cancel Specific Seats (Partial Cancellation)",
        description="Cancels targeted seats, transitions booking to PARTIALLY_CANCELLED, and immediately releases segment availability.",
        request=CancelSeatsRequestSerializer,
        responses={200: OpenApiResponse(description="Partial seats cancelled and refund processed")},
        tags=["Cancellations & Refunds"]
    )
    def post(self, request):
        serializer = CancelSeatsRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return api_error(
                message="Validation failed for one or more fields.",
                code="VALIDATION_ERROR",
                details=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST
            )

        pnr = serializer.validated_data['pnr'].strip().upper()
        seat_ids = serializer.validated_data['seat_ids']
        reason = serializer.validated_data.get('reason', '')
        user = request.user if request.user.is_authenticated else None

        try:
            booking, refund = CancellationService.cancel_seats(
                pnr=pnr,
                seat_ids=seat_ids,
                reason=reason,
                user=user
            )
            return api_success(
                data={
                    "pnr": booking.pnr,
                    "status": booking.status,
                    "refund": RefundSerializer(refund).data if refund else None,
                },
                message=f"Successfully cancelled {len(seat_ids)} seat(s) for booking {booking.pnr}."
            )
        except (BusinessLogicError, ResourceNotFoundError) as exc:
            status_code = status.HTTP_404_NOT_FOUND if isinstance(exc, ResourceNotFoundError) else status.HTTP_400_BAD_REQUEST
            code = "RESOURCE_NOT_FOUND" if isinstance(exc, ResourceNotFoundError) else "BUSINESS_RULE_VIOLATION"
            return api_error(message=str(exc), code=code, status_code=status_code)


class BookingRefundsView(APIView):
    """
    Retrieve all refund audit records for a given booking PNR.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="List Refunds for Booking",
        description="Returns audit trail of all refunds issued against a booking PNR.",
        responses={200: RefundSerializer(many=True)},
        tags=["Cancellations & Refunds"]
    )
    def get(self, request, pnr):
        pnr_clean = pnr.strip().upper()
        booking = Booking.objects.filter(pnr=pnr_clean).first()
        if not booking:
            return api_error(
                message=f"Booking with PNR '{pnr_clean}' not found.",
                code="RESOURCE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND
            )

        refunds = booking.refunds.all()
        return api_success(
            data={"refunds": RefundSerializer(refunds, many=True).data},
            message=f"Refund records for PNR {pnr_clean} retrieved successfully."
        )


class OperatorCancelTripView(APIView):
    """
    Operator endpoint to cancel an entire trip, issuing 100% full refunds
    to all passenger bookings with zero cancellation fees.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Operator Bulk Trip Cancellation",
        description="Operator action: Cancels scheduled trip, voids all passenger tickets, and executes 100% full refunds with zero cancellation fee.",
        request=OperatorCancelTripRequestSerializer,
        responses={200: OpenApiResponse(description="Trip cancelled and bulk refunds executed")},
        tags=["Cancellations & Refunds"]
    )
    def post(self, request):
        serializer = OperatorCancelTripRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return api_error(
                message="Validation failed for one or more fields.",
                code="VALIDATION_ERROR",
                details=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST
            )

        trip_id = serializer.validated_data['trip_id']
        reason = serializer.validated_data.get('reason', '')

        try:
            result = CancellationService.cancel_trip_by_operator(
                trip_id=trip_id,
                reason=reason,
                operator_user=request.user
            )
            return api_success(
                data=result,
                message=f"Trip cancelled by operator. {result['bookings_cancelled_count']} booking(s) refunded."
            )
        except (BusinessLogicError, ResourceNotFoundError) as exc:
            status_code = status.HTTP_404_NOT_FOUND if isinstance(exc, ResourceNotFoundError) else status.HTTP_400_BAD_REQUEST
            code = "RESOURCE_NOT_FOUND" if isinstance(exc, ResourceNotFoundError) else "BUSINESS_RULE_VIOLATION"
            return api_error(message=str(exc), code=code, status_code=status_code)
