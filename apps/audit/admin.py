"""
Django Admin registration for AuditLog.
"""
from django.contrib import admin
from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('action', 'resource', 'http_method', 'status_code', 'user', 'ip_address', 'created_at')
    list_filter = ('action', 'http_method', 'status_code')
    search_fields = ('resource', 'ip_address', 'user__phone')
    readonly_fields = (
        'id', 'user', 'action', 'resource', 'http_method',
        'status_code', 'ip_address', 'user_agent', 'payload_diff', 'created_at', 'updated_at'
    )
    ordering = ('-created_at',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
