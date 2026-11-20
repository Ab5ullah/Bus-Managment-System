"""
URL routing for Routes and Terminals.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import TerminalViewSet, RouteViewSet

router = DefaultRouter()
router.register(r'terminals', TerminalViewSet, basename='terminal')
router.register(r'', RouteViewSet, basename='route')

urlpatterns = [
    path('', include(router.urls)),
]
