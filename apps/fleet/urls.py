"""
URL Configuration for Fleet Management (Buses, Drivers, Conductors).
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import BusViewSet, DriverViewSet, ConductorViewSet

app_name = 'fleet'

router = DefaultRouter()
router.register('buses', BusViewSet, basename='buses')
router.register('drivers', DriverViewSet, basename='drivers')
router.register('conductors', ConductorViewSet, basename='conductors')

urlpatterns = [
    path('', include(router.urls)),
]
