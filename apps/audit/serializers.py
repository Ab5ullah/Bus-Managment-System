"""
Serializers for Audit Logs.
"""
from rest_framework import serializers
from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    """Read-only serializer for AuditLog entries."""
    user_phone = serializers.CharField(source='user.phone', read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = [
            'id', 'user', 'user_phone', 'action', 'resource',
            'http_method', 'status_code', 'ip_address',
            'user_agent', 'payload_diff', 'created_at',
        ]
        read_only_fields = fields
