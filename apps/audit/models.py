"""
Database model for production Audit Logging.
"""
from django.conf import settings
from django.db import models
from common.models import UUIDModel, TimeStampedModel


class AuditAction(models.TextChoices):
    CREATE = 'CREATE', 'Create'
    UPDATE = 'UPDATE', 'Update'
    CANCEL = 'CANCEL', 'Cancel'
    DELETE = 'DELETE', 'Delete'


class AuditLog(UUIDModel, TimeStampedModel):
    """
    Immutable audit trail recording state-changing API operations,
    acting user, resource path, client IP, and sanitized payload diff.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs'
    )
    action = models.CharField(
        max_length=20,
        choices=AuditAction.choices,
        db_index=True
    )
    resource = models.CharField(max_length=255, db_index=True)
    http_method = models.CharField(max_length=10)
    status_code = models.PositiveSmallIntegerField(default=200)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    payload_diff = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['action', 'created_at']),
            models.Index(fields=['resource', 'created_at']),
        ]

    def __str__(self):
        actor = self.user.phone if self.user else 'Anonymous'
        return f"[{self.action}] {self.resource} by {actor} ({self.status_code})"
