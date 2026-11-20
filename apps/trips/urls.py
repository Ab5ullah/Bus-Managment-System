"""
URL routing for Trips and Fare Rules.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import TripViewSet, FareRuleViewSet

router = DefaultRouter()
router.register(r'fare-rules', FareRuleViewSet, basename='fare-rule')
router.register(r'', TripViewSet, basename='trip')

urlpatterns = [
    path('', include(router.urls)),
]
