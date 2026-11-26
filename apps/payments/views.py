"""
DRF Views for Module 7: Payments, Webhooks & Financial Reconciliation.
"""
from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiResponse
from common.responses import api_success, api_error
from common.exceptions import BusinessLogicError
from common.constants import PaymentStatus
from bookings.models import Booking
from .models import Payment, PaymentReceipt
from .services import PaymentService
from .serializers import (
    PaymentInitiateSerializer,
    PaymentDetailSerializer,
    PaymentReceiptSerializer,
    ConfirmMockPaymentSerializer,
    RefundPaymentSerializer,
    VerifyStripeSessionSerializer,
)


class PaymentInitiateView(APIView):
    """
    POST /api/payments/initiate/
    Creates or idempotently retrieves a payment session for a PENDING booking.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Initiate Booking Payment",
        description="Creates an idempotent payment transaction for a pending booking and returns gateway session details.",
        request=PaymentInitiateSerializer,
        responses={200: PaymentDetailSerializer, 201: PaymentDetailSerializer},
        tags=["Payments & Reconciliation"]
    )
    def post(self, request):
        serializer = PaymentInitiateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = request.user if request.user.is_authenticated else None

        meta = dict(data.get('metadata') or {})
        if data.get('checkout_mode'):
            meta['checkout_mode'] = data['checkout_mode']

        payment, gateway_info = PaymentService.initiate_payment(
            booking_id=data.get('booking_id'),
            pnr=data.get('pnr'),
            method=data.get('method'),
            gateway_name=data.get('gateway_name') or None,
            idempotency_key=data.get('idempotency_key'),
            return_url=data.get('return_url', ''),
            metadata=meta,
            user=user
        )

        response_payload = {
            "payment": PaymentDetailSerializer(payment).data,
            "gateway": gateway_info
        }

        status_code = status.HTTP_200_OK if gateway_info.get('already_confirmed') else status.HTTP_201_CREATED
        return api_success(
            data=response_payload,
            message="Payment initiated successfully.",
            status_code=status_code
        )


class ConfirmMockPaymentView(APIView):
    """
    POST /api/payments/confirm-mock/
    Simulates gateway completion (success or failure) for test sandboxes and local UI.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Confirm Sandbox Mock Payment",
        description="Simulates immediate payment confirmation or rejection in sandbox environments.",
        request=ConfirmMockPaymentSerializer,
        tags=["Payments & Reconciliation"]
    )
    def post(self, request):
        serializer = ConfirmMockPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        target_status = data['status']
        payment_id = data['payment_id']

        if target_status == 'SUCCESS':
            receipt = PaymentService.process_payment_success(
                payment_id=payment_id,
                gateway_reference=f"MOCK-CONFIRMED-{payment_id.hex[:8].upper()}",
                raw_response={"simulated": True, "action": "CONFIRM_SUCCESS"}
            )
            return api_success(
                data={
                    "payment_id": str(payment_id),
                    "status": PaymentStatus.SUCCESS,
                    "receipt": PaymentReceiptSerializer(receipt).data
                },
                message="Payment successfully captured. Booking confirmed."
            )
        else:
            payment = PaymentService.process_payment_failure(
                payment_id=payment_id,
                failure_reason=data.get('failure_reason') or "Card declined / Insufficient funds",
                raw_response={"simulated": True, "action": "CONFIRM_FAILED"}
            )
            return api_error(
                message="Payment failed. Held seats released back into inventory.",
                code="PAYMENT_FAILED",
                details={
                    "payment_id": str(payment_id),
                    "status": PaymentStatus.FAILED,
                    "failure_reason": payment.failure_reason
                },
                status_code=status.HTTP_400_BAD_REQUEST
            )


class PaymentWebhookView(APIView):
    """
    POST /api/payments/webhook/{gateway_name}/
    Idempotent cryptographic webhook endpoint receiving external gateway callbacks.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Ingest Gateway Webhook",
        description="Receives and processes asynchronous webhook callbacks idempotently from payment gateways.",
        tags=["Payments & Reconciliation"]
    )
    def post(self, request, gateway_name: str):
        # Read raw_body first so Django caches request.body before DRF accesses the stream
        raw_body = request.body
        payload = request.data if isinstance(request.data, dict) else {}
        signature = (
            request.headers.get('Stripe-Signature') or
            request.META.get('HTTP_STRIPE_SIGNATURE') or
            request.headers.get('X-Signature') or
            request.headers.get('HTTP_X_SIGNATURE') or
            payload.get('signature', '')
        )

        payment, is_processed, message = PaymentService.handle_webhook(
            gateway_name=gateway_name,
            payload=payload,
            signature=signature,
            headers=dict(request.headers),
            raw_body=raw_body
        )

        return api_success(
            data={
                "processed": is_processed,
                "payment_id": str(payment.id) if payment else None,
                "status": payment.status if payment else None,
            },
            message=message,
            status_code=status.HTTP_200_OK
        )


class PaymentDetailView(APIView):
    """
    GET /api/payments/{id}/
    Retrieves full transaction status and receipt information.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(summary="Get Payment Details", responses={200: PaymentDetailSerializer}, tags=["Payments & Reconciliation"])
    def get(self, request, id):
        try:
            payment = Payment.objects.select_related('booking', 'receipt').get(id=id)
        except Payment.DoesNotExist:
            raise BusinessLogicError("Payment record not found.")

        return api_success(
            data=PaymentDetailSerializer(payment).data,
            message="Payment record retrieved."
        )


class PaymentReceiptDetailView(APIView):
    """
    GET /api/payments/receipt/{receipt_number}/
    Retrieves full financial breakdown of an issued receipt.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(summary="Get Payment Receipt", responses={200: PaymentReceiptSerializer}, tags=["Payments & Reconciliation"])
    def get(self, request, receipt_number: str):
        try:
            receipt = PaymentReceipt.objects.select_related('payment', 'payment__booking').get(
                receipt_number=receipt_number.strip().upper()
            )
        except PaymentReceipt.DoesNotExist:
            raise BusinessLogicError("Receipt not found.")

        return api_success(
            data=PaymentReceiptSerializer(receipt).data,
            message="Payment receipt retrieved."
        )


class BookingPaymentsListView(APIView):
    """
    GET /api/payments/booking/{pnr}/
    Lists all payment attempts and receipts associated with a booking PNR.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(summary="List Payments for Booking PNR", tags=["Payments & Reconciliation"])
    def get(self, request, pnr: str):
        booking = Booking.objects.filter(pnr=pnr.strip().upper()).first()
        if not booking:
            raise BusinessLogicError(f"Booking with PNR '{pnr}' not found.")

        payments = Payment.objects.filter(booking=booking).select_related('receipt').order_by('-created_at')
        return api_success(
            data=PaymentDetailSerializer(payments, many=True).data,
            message=f"Payments for booking '{booking.pnr}' retrieved."
        )


class PaymentRefundView(APIView):
    """
    POST /api/payments/{id}/refund/
    Processes a full or partial refund on a successful payment.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(summary="Refund Payment", request=RefundPaymentSerializer, tags=["Payments & Reconciliation"])
    def post(self, request, id):
        serializer = RefundPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        payment = PaymentService.process_refund(
            payment_id=id,
            refund_amount=data.get('amount'),
            reason=data.get('reason', '')
        )

        return api_success(
            data=PaymentDetailSerializer(payment).data,
            message="Payment successfully refunded."
        )


class VerifyStripeSessionView(APIView):
    """
    POST /api/payments/verify-stripe-session/
    Verifies a completed Stripe Checkout Session upon redirect return and confirms booking idempotently.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        summary="Verify Stripe Checkout Session",
        request=VerifyStripeSessionSerializer,
        tags=["Payments & Reconciliation"]
    )
    def post(self, request):
        serializer = VerifyStripeSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session_id = serializer.validated_data['session_id']

        payment, receipt, msg = PaymentService.verify_stripe_session(session_id)
        return api_success(
            data={
                "payment": PaymentDetailSerializer(payment).data,
                "receipt": PaymentReceiptSerializer(receipt).data if receipt else None,
            },
            message=msg
        )
