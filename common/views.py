"""
Common API views including system health check.
"""
from datetime import datetime, timezone
from django.db import connection
from django.views.generic import TemplateView
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework import status
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer
from rest_framework import serializers


@method_decorator(ensure_csrf_cookie, name='dispatch')
class PortalHomeView(TemplateView):
    """
    Renders the unified interactive control portal for managing fleet, seats, users, and testing endpoints.
    """
    template_name = 'portal/index.html'


class HealthCheckView(APIView):
    """
    System Health Check Endpoint.
    Checks API server responsiveness and database connectivity.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        summary="System Health Check",
        description="Returns API service health, uptime status, and database connectivity state.",
        responses={
            200: OpenApiResponse(
                description="System is operational.",
                response=inline_serializer(
                    name="HealthCheckResponse",
                    fields={
                        "success": serializers.BooleanField(default=True),
                        "message": serializers.CharField(default="Service is healthy"),
                        "data": serializers.DictField()
                    }
                )
            ),
            503: OpenApiResponse(
                description="Database or dependency is down."
            )
        },
        tags=["System"]
    )
    def get(self, request):
        db_status = "healthy"
        status_code = status.HTTP_200_OK

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
                cursor.fetchone()
        except Exception as e:
            db_status = f"unhealthy: {str(e)}"
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE

        payload = {
            "success": status_code == status.HTTP_200_OK,
            "message": "Service is operational" if status_code == status.HTTP_200_OK else "Service degraded",
            "data": {
                "service": "Bus Ticket Management System API",
                "version": "1.0.0",
                "status": "healthy" if status_code == status.HTTP_200_OK else "degraded",
                "database": db_status,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        }
        return Response(payload, status=status_code)
