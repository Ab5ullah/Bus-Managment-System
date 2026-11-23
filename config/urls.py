"""
URL configuration for Bus Ticket Management System (busms).
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from common.views import PortalHomeView

urlpatterns = [
    # Interactive Web Control Portal
    path('', PortalHomeView.as_view(), name='portal-home'),

    path('admin/', admin.site.urls),

    # Common & Health Check endpoints
    path('api/', include('common.urls')),

    # Module 1: Authentication & User Accounts
    path('api/auth/', include('accounts.urls_auth')),
    path('api/accounts/', include('accounts.urls_accounts')),

    # Module 2: Fleet Management
    path('api/fleet/', include('fleet.urls')),

    # Module 3: Terminals & Sequenced Routes
    path('api/routes/', include('routes.urls')),

    # Module 4: Trips, Fares & Overlap Prevention
    path('api/trips/', include('trips.urls')),

    # Module 5: Search & Segment-Aware Availability Engine
    path('api/search/', include('search.urls')),

    # Module 6: Concurrency, Seat Locking & Booking Engine
    path('api/bookings/', include('bookings.urls')),

    # Module 7: Payments, Webhooks & Financial Reconciliation
    path('api/payments/', include('payments.urls')),

    # Module 8: Tickets, QR Codes & Conductor Validation
    path('api/tickets/', include('tickets.urls')),

    # Module 9: Cancellation & Refund Engine
    path('api/cancellations/', include('cancellations.urls')),

    # OpenAPI Schema & Interactive Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
