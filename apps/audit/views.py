"""
Admin API views for Audit Logs.
"""
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import generics, filters
from rest_framework.permissions import IsAuthenticated

from accounts.permissions import IsOperator
from .models import AuditLog
from .serializers import AuditLogSerializer


class AuditLogListView(generics.ListAPIView):
    """
    GET /api/admin/audit/logs/
    Paginated, filterable list of system audit logs.
    """
    queryset = AuditLog.objects.select_related('user').all()
    serializer_class = AuditLogSerializer
    permission_classes = [IsAuthenticated, IsOperator]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['action', 'http_method', 'status_code']
    search_fields = ['resource', 'ip_address', 'user__phone']
    ordering_fields = ['created_at', 'status_code']
    ordering = ['-created_at']
