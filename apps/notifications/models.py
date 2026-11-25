"""
Database models for Notification Logs (Email & SMS dispatch audit).
"""
from django.db import models
from django.conf import settings
from common.models import UUIDModel, TimeStampedModel


class NotificationChannel(models.TextChoices):
    EMAIL = 'EMAIL', 'Email'
    SMS = 'SMS', 'SMS'


class NotificationStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    SENT = 'SENT', 'Sent'
    FAILED = 'FAILED', 'Failed'


class NotificationLog(UUIDModel, TimeStampedModel):
    """
    Audit trail for every asynchronous email and SMS notification dispatched.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notification_logs'
    )
    recipient = models.CharField(max_length=255, db_index=True)
    channel = models.CharField(
        max_length=20,
        choices=NotificationChannel.choices,
        db_index=True
    )
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField(blank=True)
    template_name = models.CharField(max_length=120, blank=True, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=NotificationStatus.choices,
        default=NotificationStatus.PENDING,
        db_index=True
    )
    error_message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['channel', 'status']),
            models.Index(fields=['template_name', 'created_at']),
        ]

    def __str__(self):
        return f"[{self.channel}] {self.recipient} - {self.status} ({self.template_name or 'custom'})"
