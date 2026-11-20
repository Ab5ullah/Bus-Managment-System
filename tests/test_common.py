"""
Unit and integration tests for Module 0: Core Foundation, Health Check, Exceptions, and Docs.
"""
import uuid
import pytest
from django.urls import reverse
from django.test import RequestFactory
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError, PermissionDenied, NotFound
from rest_framework.views import APIView
from rest_framework.response import Response

from common.models import BaseModel
from common.exceptions import (
    BusinessLogicError,
    ResourceConflictError,
    SeatUnavailableError,
    SeatLockExpiredError,
    custom_exception_handler,
)
from common.responses import api_success, api_error


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
class TestHealthCheck:
    """Test system health check endpoint."""

    def test_health_check_success(self, api_client):
        url = reverse('common:health-check')
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['success'] is True
        assert data['message'] == "Service is operational"
        assert data['data']['status'] == "healthy"
        assert data['data']['database'] == "healthy"
        assert "version" in data['data']
        assert "timestamp" in data['data']


class TestDocumentationEndpoints:
    """Test OpenAPI schema and Swagger / Redoc UI endpoints."""

    def test_openapi_schema_endpoint(self, api_client):
        url = reverse('schema')
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert "openapi" in response.content.decode()

    def test_swagger_ui_endpoint(self, api_client):
        url = reverse('swagger-ui')
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert b"swagger-ui" in response.content.lower()

    def test_redoc_ui_endpoint(self, api_client):
        url = reverse('redoc')
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert b"redoc" in response.content.lower()


class TestCustomExceptionHandler:
    """Test standard JSON error formatting across various exception types."""

    def test_validation_error_format(self):
        factory = RequestFactory()
        request = factory.get('/')
        exc = ValidationError({"email": ["Enter a valid email address."]})
        context = {"request": request, "view": APIView()}

        response = custom_exception_handler(exc, context)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['success'] is False
        assert response.data['error']['code'] == "VALIDATION_ERROR"
        assert "email" in response.data['error']['details']

    def test_permission_denied_format(self):
        factory = RequestFactory()
        request = factory.get('/')
        exc = PermissionDenied()
        context = {"request": request, "view": APIView()}

        response = custom_exception_handler(exc, context)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data['success'] is False
        assert response.data['error']['code'] == "PERMISSION_DENIED"

    def test_not_found_format(self):
        factory = RequestFactory()
        request = factory.get('/')
        exc = NotFound("Trip schedule not found.")
        context = {"request": request, "view": APIView()}

        response = custom_exception_handler(exc, context)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data['success'] is False
        assert response.data['error']['code'] == "NOT_FOUND"

    def test_custom_domain_exceptions(self):
        factory = RequestFactory()
        request = factory.get('/')
        context = {"request": request, "view": APIView()}

        # BusinessLogicError
        ble = BusinessLogicError("Booking cutoff time reached.")
        res_ble = custom_exception_handler(ble, context)
        assert res_ble.status_code == status.HTTP_400_BAD_REQUEST
        assert res_ble.data['error']['code'] == "BUSINESS_RULE_VIOLATION"

        # ResourceConflictError / SeatUnavailableError
        sue = SeatUnavailableError("Seat 12B is already booked.")
        res_sue = custom_exception_handler(sue, context)
        assert res_sue.status_code == status.HTTP_409_CONFLICT
        assert res_sue.data['error']['code'] == "SEAT_UNAVAILABLE"

        # SeatLockExpiredError
        sle = SeatLockExpiredError("Seat lock expired.")
        res_sle = custom_exception_handler(sle, context)
        assert res_sle.status_code == status.HTTP_410_GONE
        assert res_sle.data['error']['code'] == "SEAT_LOCK_EXPIRED"


class TestResponseHelpers:
    """Test standard response constructors."""

    def test_api_success_helper(self):
        resp = api_success(data={"id": 123}, message="Created successfully", status_code=status.HTTP_201_CREATED)
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['success'] is True
        assert resp.data['message'] == "Created successfully"
        assert resp.data['data'] == {"id": 123}

    def test_api_error_helper(self):
        resp = api_error(message="Invalid PNR", code="INVALID_PNR", status_code=status.HTTP_400_BAD_REQUEST)
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.data['success'] is False
        assert resp.data['error']['code'] == "INVALID_PNR"
        assert resp.data['error']['message'] == "Invalid PNR"


class TestMoneyAndFinancialEngine:
    """Test monetary precision, banking rounding, tax/fee math, and prorated refunds."""

    def test_currency_rounding(self):
        from decimal import Decimal
        from common.money import round_currency

        assert round_currency(Decimal('10.555')) == Decimal('10.56')
        assert round_currency(Decimal('10.554')) == Decimal('10.55')
        assert round_currency('49.999') == Decimal('50.00')

    def test_booking_financial_calculation(self):
        from decimal import Decimal
        from common.money import calculate_booking_financials

        result = calculate_booking_financials(
            gross_fare=Decimal('2000.00'),
            discount_amount=Decimal('200.00'),
            tax_rate_percentage=Decimal('5.00'),
            service_fee_fixed=Decimal('50.00')
        )

        assert result['gross_fare'] == Decimal('2000.00')
        assert result['discount_amount'] == Decimal('200.00')
        assert result['taxable_amount'] == Decimal('1800.00')
        assert result['tax_amount'] == Decimal('90.00')
        assert result['service_fee'] == Decimal('50.00')
        assert result['net_payable'] == Decimal('1940.00')

    def test_seat_refund_with_prorated_discount_and_tax(self):
        from decimal import Decimal
        from common.money import calculate_seat_refund

        # 2 seats totaling 2000 gross, with a 200 coupon discount (100 off each seat)
        refund_90 = calculate_seat_refund(
            seat_price=Decimal('1000.00'),
            total_gross_fare=Decimal('2000.00'),
            total_discount=Decimal('200.00'),
            refund_percentage=Decimal('90.00'),
            tax_rate_percentage=Decimal('5.00')
        )

        assert refund_90['seat_effective_fare'] == Decimal('900.00')
        assert refund_90['seat_tax'] == Decimal('45.00')
        assert refund_90['refundable_base'] == Decimal('945.00')
        assert refund_90['net_refund_amount'] == Decimal('850.50')
        assert refund_90['cancellation_fee'] == Decimal('94.50')

        # Test 50% tier
        refund_50 = calculate_seat_refund(
            seat_price=Decimal('1000.00'),
            total_gross_fare=Decimal('2000.00'),
            total_discount=Decimal('200.00'),
            refund_percentage=Decimal('50.00'),
            tax_rate_percentage=Decimal('5.00')
        )
        assert refund_50['net_refund_amount'] == Decimal('472.50')
        assert refund_50['cancellation_fee'] == Decimal('472.50')

