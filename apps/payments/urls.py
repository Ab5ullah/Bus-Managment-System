"""
URL configuration for Module 7: Payments, Webhooks & Financial Reconciliation.
"""
from django.urls import path
from .views import (
    PaymentInitiateView,
    ConfirmMockPaymentView,
    PaymentWebhookView,
    PaymentDetailView,
    PaymentReceiptDetailView,
    BookingPaymentsListView,
    PaymentRefundView,
)

urlpatterns = [
    # Payment Intent Initiation
    path('initiate/', PaymentInitiateView.as_view(), name='payment-initiate'),

    # Sandbox / Mock Confirmation
    path('confirm-mock/', ConfirmMockPaymentView.as_view(), name='payment-confirm-mock'),

    # Asynchronous Webhook Ingestion
    path('webhook/<str:gateway_name>/', PaymentWebhookView.as_view(), name='payment-webhook'),

    # Payment Status & Details
    path('<uuid:id>/', PaymentDetailView.as_view(), name='payment-detail'),
    path('<uuid:id>/refund/', PaymentRefundView.as_view(), name='payment-refund'),

    # Financial Receipts
    path('receipt/<str:receipt_number>/', PaymentReceiptDetailView.as_view(), name='payment-receipt-detail'),

    # Booking Transactions
    path('booking/<str:pnr>/', BookingPaymentsListView.as_view(), name='payment-booking-list'),
]
