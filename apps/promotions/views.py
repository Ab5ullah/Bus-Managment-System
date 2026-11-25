"""
API Views for Module 10: Promotions, Coupons & Dynamic Pricing.
"""
from django.shortcuts import get_object_or_404
from rest_framework import status, permissions, viewsets
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, OpenApiResponse, OpenApiParameter

from accounts.permissions import IsOperator
from common.responses import api_success, api_error
from common.exceptions import BusinessLogicError, ResourceNotFoundError
from common.money import calculate_booking_financials
from trips.models import Trip
from routes.models import RouteStop
from .models import Coupon, CouponUsage, DynamicPricingRule
from .serializers import (
    CouponSerializer,
    CouponUsageSerializer,
    DynamicPricingRuleSerializer,
    ValidateCouponRequestSerializer,
    ApplyCouponRequestSerializer,
    RemoveCouponRequestSerializer,
)
from .services import CouponService, DynamicPricingService


class ValidateCouponView(APIView):
    """
    Preview and validate a coupon code against a proposed gross booking amount
    without recording usage.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Validate Promotional Coupon",
        description="Checks coupon eligibility, expiry, minimum spend, and quotas; returns discount preview.",
        request=ValidateCouponRequestSerializer,
        responses={200: OpenApiResponse(description="Coupon is valid and discount calculated")},
        tags=["Promotions & Pricing"]
    )
    def post(self, request):
        serializer = ValidateCouponRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return api_error(
                message="Validation failed for one or more fields.",
                code="VALIDATION_ERROR",
                details=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST
            )

        data = serializer.validated_data
        user = request.user if request.user.is_authenticated else None

        try:
            coupon, discount = CouponService.validate_coupon(
                code=data['code'],
                gross_amount=data['gross_amount'],
                user=user,
                passenger_phone=data.get('passenger_phone', '')
            )
            financials = calculate_booking_financials(
                gross_fare=data['gross_amount'],
                discount_amount=discount
            )
            return api_success(
                data={
                    "code": coupon.code,
                    "discount_type": coupon.discount_type,
                    "discount_value": coupon.discount_value,
                    "discount_amount": discount,
                    "projected_tax_amount": financials['tax_amount'],
                    "projected_service_fee": financials['service_fee'],
                    "projected_net_payable": financials['net_payable'],
                },
                message=f"Coupon '{coupon.code}' is valid! Discount of PKR {discount} applies."
            )
        except (BusinessLogicError, ResourceNotFoundError) as exc:
            status_code = status.HTTP_404_NOT_FOUND if isinstance(exc, ResourceNotFoundError) else status.HTTP_400_BAD_REQUEST
            code = "RESOURCE_NOT_FOUND" if isinstance(exc, ResourceNotFoundError) else "BUSINESS_RULE_VIOLATION"
            return api_error(message=str(exc), code=code, status_code=status_code)


class ApplyCouponView(APIView):
    """
    Atomically applies a promotional coupon to an existing PENDING booking
    and recalculates tax and net payable server-side.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Apply Coupon to Booking",
        description="Applies a coupon code to a PENDING booking, recalculating tax and net amount atomically.",
        request=ApplyCouponRequestSerializer,
        responses={200: OpenApiResponse(description="Coupon applied and booking totals updated")},
        tags=["Promotions & Pricing"]
    )
    def post(self, request):
        serializer = ApplyCouponRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return api_error(
                message="Validation failed for one or more fields.",
                code="VALIDATION_ERROR",
                details=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST
            )

        pnr = serializer.validated_data['pnr']
        code = serializer.validated_data['code']
        user = request.user if request.user.is_authenticated else None

        try:
            booking, usage = CouponService.apply_coupon_to_booking(pnr=pnr, code=code, user=user)
            return api_success(
                data={
                    "pnr": booking.pnr,
                    "coupon_code": usage.coupon.code,
                    "total_gross_fare": booking.total_gross_fare,
                    "discount_amount": booking.discount_amount,
                    "tax_amount": booking.tax_amount,
                    "service_fee": booking.service_fee,
                    "net_amount": booking.net_amount,
                },
                message=f"Coupon '{usage.coupon.code}' applied successfully (-PKR {usage.discount_applied})."
            )
        except (BusinessLogicError, ResourceNotFoundError) as exc:
            status_code = status.HTTP_404_NOT_FOUND if isinstance(exc, ResourceNotFoundError) else status.HTTP_400_BAD_REQUEST
            err_code = "RESOURCE_NOT_FOUND" if isinstance(exc, ResourceNotFoundError) else "BUSINESS_RULE_VIOLATION"
            return api_error(message=str(exc), code=err_code, status_code=status_code)


class RemoveCouponView(APIView):
    """
    Removes an applied coupon from a PENDING booking and restores standard fare totals.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Remove Coupon from Booking",
        request=RemoveCouponRequestSerializer,
        tags=["Promotions & Pricing"]
    )
    def post(self, request):
        serializer = RemoveCouponRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return api_error(
                message="Validation failed for one or more fields.",
                code="VALIDATION_ERROR",
                details=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST
            )

        try:
            booking = CouponService.remove_coupon_from_booking(pnr=serializer.validated_data['pnr'])
            return api_success(
                data={
                    "pnr": booking.pnr,
                    "total_gross_fare": booking.total_gross_fare,
                    "discount_amount": booking.discount_amount,
                    "tax_amount": booking.tax_amount,
                    "net_amount": booking.net_amount,
                },
                message=f"Coupon removed from booking {booking.pnr}."
            )
        except (BusinessLogicError, ResourceNotFoundError) as exc:
            status_code = status.HTTP_404_NOT_FOUND if isinstance(exc, ResourceNotFoundError) else status.HTTP_400_BAD_REQUEST
            err_code = "RESOURCE_NOT_FOUND" if isinstance(exc, ResourceNotFoundError) else "BUSINESS_RULE_VIOLATION"
            return api_error(message=str(exc), code=err_code, status_code=status_code)


class DynamicPriceCheckView(APIView):
    """
    Evaluates live dynamic pricing rules (occupancy surge, early bird, weekend)
    for a specific trip segment.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Evaluate Live Dynamic Pricing",
        parameters=[
            OpenApiParameter(name='trip_id', type=str, required=True),
            OpenApiParameter(name='from_stop_id', type=str, required=True),
            OpenApiParameter(name='to_stop_id', type=str, required=True),
            OpenApiParameter(name='seat_type', type=str, required=False),
        ],
        tags=["Promotions & Pricing"]
    )
    def get(self, request):
        trip_id = request.query_params.get('trip_id')
        from_stop_id = request.query_params.get('from_stop_id')
        to_stop_id = request.query_params.get('to_stop_id')
        seat_type = request.query_params.get('seat_type')

        if not (trip_id and from_stop_id and to_stop_id):
            return api_error(
                message="trip_id, from_stop_id, and to_stop_id query parameters are required.",
                code="VALIDATION_ERROR",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        trip = get_object_or_404(Trip.objects.select_related('route', 'bus'), id=trip_id)
        from_stop = get_object_or_404(RouteStop, id=from_stop_id)
        to_stop = get_object_or_404(RouteStop, id=to_stop_id)

        try:
            pricing = DynamicPricingService.calculate_dynamic_fare(
                trip=trip,
                from_stop=from_stop,
                to_stop=to_stop,
                seat_type=seat_type
            )
            return api_success(data=pricing, message="Dynamic fare evaluated successfully.")
        except BusinessLogicError as exc:
            return api_error(message=str(exc), code="BUSINESS_RULE_VIOLATION", status_code=status.HTTP_400_BAD_REQUEST)


class CouponAdminViewSet(viewsets.ModelViewSet):
    """
    Admin / Operator CRUD API for managing promotional coupon campaigns.
    """
    queryset = Coupon.objects.all().order_by('-created_at')
    serializer_class = CouponSerializer
    permission_classes = [IsOperator]

    def list(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return api_success(data={"coupons": serializer.data}, message="Coupons retrieved successfully.")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        coupon = serializer.save()
        return api_success(
            data=self.get_serializer(coupon).data,
            message=f"Coupon '{coupon.code}' created successfully.",
            status_code=status.HTTP_201_CREATED
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        return api_success(data=self.get_serializer(instance).data, message="Coupon details retrieved.")

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        coupon = serializer.save()
        return api_success(data=self.get_serializer(coupon).data, message=f"Coupon '{coupon.code}' updated.")

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.usages.exists():
            # Soft-deactivate if already redeemed to preserve audit trail
            instance.is_active = False
            instance.save(update_fields=['is_active'])
            return api_success(message=f"Coupon '{instance.code}' deactivated (audit trail preserved).")
        instance.delete()
        return api_success(message="Coupon deleted.")


class DynamicPricingRuleViewSet(viewsets.ModelViewSet):
    """
    Admin / Operator CRUD API for managing dynamic pricing / yield rules.
    """
    queryset = DynamicPricingRule.objects.all().order_by('-priority', '-created_at')
    serializer_class = DynamicPricingRuleSerializer
    permission_classes = [IsOperator]

    def list(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return api_success(data={"rules": serializer.data}, message="Dynamic pricing rules retrieved successfully.")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rule = serializer.save()
        return api_success(
            data=self.get_serializer(rule).data,
            message=f"Dynamic pricing rule '{rule.name}' created successfully.",
            status_code=status.HTTP_201_CREATED
        )

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        rule = serializer.save()
        return api_success(data=self.get_serializer(rule).data, message=f"Rule '{rule.name}' updated.")

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.delete()
        return api_success(message="Dynamic pricing rule deleted.")
