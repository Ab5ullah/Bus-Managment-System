"""
URL configuration for Module 9: Cancellation & Refund Engine.
"""
from django.urls import path
from .views import (
    CancellationPolicyListView,
    EstimateRefundView,
    CancelBookingView,
    CancelSeatsView,
    BookingRefundsView,
    OperatorCancelTripView,
)

urlpatterns = [
    # Policy catalog
    path('policies/', CancellationPolicyListView.as_view(), name='cancellation-policies-list'),

    # Refund estimation preview
    path('estimate/', EstimateRefundView.as_view(), name='cancellation-estimate-refund'),

    # Cancellation actions
    path('cancel-booking/', CancelBookingView.as_view(), name='cancellation-cancel-booking'),
    path('cancel-seats/', CancelSeatsView.as_view(), name='cancellation-cancel-seats'),

    # Audit records
    path('refunds/<str:pnr>/', BookingRefundsView.as_view(), name='cancellation-booking-refunds'),

    # Operator trip cancellation override
    path('operator/cancel-trip/', OperatorCancelTripView.as_view(), name='cancellation-operator-cancel-trip'),
]
