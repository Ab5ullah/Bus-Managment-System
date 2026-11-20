"""
URL routing for Search and Segment-Aware Availability Engine.
"""
from django.urls import path
from .views import TripSearchView, TripSeatMapView, PopularRoutesView

app_name = 'search'

urlpatterns = [
    path('trips/', TripSearchView.as_view(), name='search-trips'),
    path('trips/<uuid:trip_id>/seat-map/', TripSeatMapView.as_view(), name='trip-seat-map'),
    path('popular/', PopularRoutesView.as_view(), name='popular-routes'),
]
