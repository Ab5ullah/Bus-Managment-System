"""
Serializers for Notification Logs.
"""
from rest_framework import serializers
from .models import NotificationLog


class NotificationLogSerializer(serializers.ModelSerializer):
    """Read-only serializer for notification audit logs."""
    user_phone = serializers.CharField(source='user.phone', read_only=True, default=None)

    class Meta:
        model = NotificationLog
        fields = [
            'id', 'user', 'user_phone', 'recipient', 'channel',
            'subject', 'body', 'template_name', 'status',
            'error_message', 'metadata', 'sent_at', 'created_at',
        ]
        read_only_fields = fields
