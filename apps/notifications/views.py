"""
Admin & Operator API views for Notification Logs & Housekeeping triggers.
"""
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import generics, filters
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.permissions import IsOperator
from common.responses import api_success
from .models import NotificationLog
from .serializers import NotificationLogSerializer
from .tasks import cleanup_expired_seat_locks, send_departure_reminder_task


class NotificationLogListView(generics.ListAPIView):
    """
    GET /api/admin/notifications/logs/
    Paginated list of email/SMS notification logs with filtering by channel, status, template_name.
    """
    queryset = NotificationLog.objects.select_related('user').all()
    serializer_class = NotificationLogSerializer
    permission_classes = [IsAuthenticated, IsOperator]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['channel', 'status', 'template_name']
    search_fields = ['recipient', 'subject', 'body']
    ordering_fields = ['created_at', 'sent_at']
    ordering = ['-created_at']


class TriggerCleanupSeatLocksView(APIView):
    """
    POST /api/admin/notifications/cleanup-locks/
    Manually dispatch the expired seat lock cleanup Celery task.
    """
    permission_classes = [IsAuthenticated, IsOperator]

    def post(self, request):
        result = cleanup_expired_seat_locks.delay()
        deleted = result.get() if hasattr(result, 'get') else None
        return api_success(
            data={'deleted_locks': deleted, 'task_id': str(getattr(result, 'id', ''))},
            message="Seat lock cleanup task executed."
        )


class TriggerDepartureRemindersView(APIView):
    """
    POST /api/admin/notifications/send-reminders/
    Manually dispatch the T-2h departure reminder Celery task.
    """
    permission_classes = [IsAuthenticated, IsOperator]

    def post(self, request):
        result = send_departure_reminder_task.delay()
        sent = result.get() if hasattr(result, 'get') else None
        return api_success(
            data={'reminders_sent': sent, 'task_id': str(getattr(result, 'id', ''))},
            message="Departure reminder task executed."
        )
