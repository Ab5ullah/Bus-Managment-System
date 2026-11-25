"""
URL routing for Bookings, Seat Locking, and Counter Ticketing.
"""
from django.urls import path
from .views import (
    SeatLockView,
    SeatUnlockView,
    ActiveSeatLockView,
    BookingCreateView,
    CounterBookingView,
    BookingListView,
    BookingDetailView,
    BookingCancelView,
)

app_name = 'bookings'

urlpatterns = [
    path('lock-seats/', SeatLockView.as_view(), name='lock-seats'),
    path('unlock-seats/', SeatUnlockView.as_view(), name='unlock-seats'),
    path('active-lock/', ActiveSeatLockView.as_view(), name='active-lock'),
    path('create/', BookingCreateView.as_view(), name='booking-create'),
    path('counter/', CounterBookingView.as_view(), name='booking-counter'),
    path('', BookingListView.as_view(), name='booking-list'),
    path('<str:pnr>/', BookingDetailView.as_view(), name='booking-detail'),
    path('<str:pnr>/cancel/', BookingCancelView.as_view(), name='booking-cancel'),
]
