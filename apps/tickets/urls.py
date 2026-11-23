"""
URL configuration for Module 8: Tickets, QR Codes & Conductor Validation.
"""
from django.urls import path
from .views import (
    TicketDetailView,
    TicketPdfDownloadView,
    ValidateQrView,
    BoardPassengerView,
    BookingTicketsListView,
    TripManifestView,
)

urlpatterns = [
    # Scanned QR validation (read-only verification before boarding)
    path('validate-qr/', ValidateQrView.as_view(), name='ticket-validate-qr'),

    # Atomic Passenger Boarding (commit boarding at gate)
    path('board/', BoardPassengerView.as_view(), name='ticket-board'),

    # Conductor Trip Passenger Manifest
    path('manifest/<uuid:trip_id>/', TripManifestView.as_view(), name='ticket-trip-manifest'),

    # Booking Tickets List
    path('booking/<str:pnr>/', BookingTicketsListView.as_view(), name='ticket-booking-list'),

    # Individual Ticket Details & Printable PDF E-Ticket
    path('<str:ticket_number>/', TicketDetailView.as_view(), name='ticket-detail'),
    path('<str:ticket_number>/pdf/', TicketPdfDownloadView.as_view(), name='ticket-pdf'),
]
